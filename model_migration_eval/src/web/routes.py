"""
Flask Routes for Model Migration Evaluation Web Interface
"""

import json
import sys
import time
import logging
import threading
import contextvars
from datetime import datetime
from pathlib import Path

from flask import Flask, render_template, request, jsonify, session, redirect, g
from flask_cors import CORS
from flask_compress import Compress

from ..auth.user_store import UserStore
from ..auth.code_manager import CodeManager
from ..auth.email_sender import create_email_sender
from ..auth.user_context import UserContext
from ..auth.session import is_public_route, get_easyauth_email
from ..clients.azure_openai import create_client_from_config
from ..utils.prompt_loader import PromptLoader
from ..utils.prompt_manager import PromptManager
from ..utils.data_loader import DataLoader, ensure_flat_schema
from ..evaluation.metrics import MetricsCalculator
from ..evaluation.evaluator import ModelEvaluator, MissingPromptsError
from ..evaluation.comparator import ModelComparator
from ..evaluation.realtime_evaluator import RealtimeEvaluator
from ..evaluation.foundry_evaluator import (
    is_foundry_available,
    create_foundry_evaluator_from_config,
)
from ..clients.tts_client import load_tts_config_from_settings
from ..clients.realtime_client import RealtimeConfig
from ..utils import blob_sync


def create_app(config_path: str = None) -> Flask:
    """
    Create and configure the Flask application.
    
    Args:
        config_path: Path to settings.yaml configuration
        
    Returns:
        Configured Flask application
    """
    app = Flask(__name__, 
                template_folder='templates',
                static_folder='static')
    CORS(app)
    Compress(app)
    
    # Reload templates from disk only in debug mode (production uses cached bytecode)
    app.config['TEMPLATES_AUTO_RELOAD'] = app.debug
    
    # Store configuration
    app.config['CONFIG_PATH'] = config_path or 'config/settings.yaml'
    # Legacy global paths (kept for seeding new users from shared data)
    app.config['DATA_DIR'] = 'data/synthetic'
    app.config['RESULTS_DIR'] = 'data/results'

    # Load full settings.yaml for model lookups
    try:
        import yaml as _yaml_init
        with open(app.config['CONFIG_PATH'], 'r') as _f_init:
            app.config['SETTINGS'] = _yaml_init.safe_load(_f_init) or {}
    except Exception:
        app.config['SETTINGS'] = {}

    # ── Auth infrastructure ──────────────────────────────────────────
    import os as _os
    import secrets as _secrets
    app.secret_key = _os.environ.get('FLASK_SECRET_KEY') or _secrets.token_hex(32)

    _auth_cfg = app.config['SETTINGS'].get('auth', {})
    # Environment variable override: AUTH_CODE_VERIFICATION ("true"/"false")
    # takes precedence over settings.yaml → auth.code_verification.
    # This allows changing the setting from the Azure Portal (Container App env vars)
    # without rebuilding the image.
    _cv_env = _os.environ.get('AUTH_CODE_VERIFICATION', '').strip().lower()
    if _cv_env in ('true', '1', 'yes'):
        _code_verification = True
    elif _cv_env in ('false', '0', 'no'):
        _code_verification = False
    else:
        _cv_yaml = _auth_cfg.get('code_verification', True)
        # If the YAML value is an unresolved placeholder like "${...}", default to True
        if isinstance(_cv_yaml, str):
            _cv_yaml_lower = _cv_yaml.strip().lower()
            _code_verification = _cv_yaml_lower not in ('false', '0', 'no') and not _cv_yaml_lower.startswith('${')
        else:
            _code_verification = bool(_cv_yaml)

    # EasyAuth auto-login: when True the app trusts the
    # X-MS-CLIENT-PRINCIPAL-NAME header injected by the Container Apps
    # authentication sidecar and creates a session automatically.
    _ea_env = _os.environ.get('AUTH_EASYAUTH_AUTO_LOGIN', '').strip().lower()
    if _ea_env in ('false', '0', 'no'):
        _easyauth_auto_login = False
    elif _ea_env in ('true', '1', 'yes'):
        _easyauth_auto_login = True
    else:
        _ea_yaml = _auth_cfg.get('easyauth_auto_login', True)
        if isinstance(_ea_yaml, str):
            _ea_lower = _ea_yaml.strip().lower()
            # Unresolved placeholder ${...} → default to True (auto-detect)
            # Only explicit 'false'/'0'/'no' disables the feature.
            _easyauth_auto_login = _ea_lower not in ('false', '0', 'no')
        else:
            _easyauth_auto_login = bool(_ea_yaml)

    _user_store = UserStore(db_path='data/auth.db')
    _code_manager = CodeManager(
        db_path='data/auth.db',
        code_length=int(_auth_cfg.get('code_length', 6)),
        ttl_seconds=int(_auth_cfg.get('code_ttl_seconds', 300)),
        max_attempts=int(_auth_cfg.get('max_attempts', 3)),
    )
    _email_sender = create_email_sender(app.config['SETTINGS'])

    # Per-user instance cache with LRU eviction  (user_id → instances)
    _MAX_CACHED_USERS = 50
    _user_cache: dict = {}          # uid → {"data_loader": ..., "last_access": float, ...}
    _user_cache_lock = threading.Lock()

    # Lazy-loaded client (shared across all users — same Azure endpoint)
    _client = None
    _metrics_calc = None
    _foundry_evaluator = None
    _foundry_checked = False
    _singleton_lock = threading.Lock()  # protects lazy init of the above

    # In-memory backend log streaming buffers (Option B)
    # Each run_id maps to a dict with a thread-safe deque for entries
    # and metadata. The global lock is only needed to create/delete
    # run slots — appends to deque are lock-free.
    from collections import deque as _deque
    _run_logs = {}
    _run_logs_lock = threading.Lock()
    _run_logs_max_lines = 2000
    _run_logs_ttl_sec = 3600

    # Shared thread pool for /api/evaluate/single parallel model dispatch
    from concurrent.futures import ThreadPoolExecutor as _TPE
    _shared_pool = _TPE(max_workers=10)

    # ── Verbose-log masking (hide sensitive endpoints from UI) ───────
    import re as _re
    _mask_patterns: list = []   # [(compiled_regex, replacement), ...]
    for _env_key in ('AZURE_OPENAI_ENDPOINT', 'FOUNDRY_PROJECT_ENDPOINT'):
        _val = _os.environ.get(_env_key, '').strip().rstrip('/')
        if _val:
            _mask_patterns.append(
                (_re.compile(_re.escape(_val), _re.IGNORECASE), f'<{_env_key}>'))
    # Sort longest patterns first so FOUNDRY_PROJECT_ENDPOINT (which is a
    # longer URL that starts with the AZURE_OPENAI_ENDPOINT prefix) is
    # replaced before the shorter one can partially match.
    _mask_patterns.sort(key=lambda pair: len(pair[1]), reverse=True)

    def _mask_verbose_message(msg: str) -> str:
        """Replace sensitive endpoint URLs with placeholders for verbose UI."""
        for pattern, replacement in _mask_patterns:
            msg = pattern.sub(replacement, msg)
        return msg
    _current_run_id = contextvars.ContextVar("current_run_id", default=None)

    # Async comparison jobs storage
    _compare_jobs = {}
    _compare_jobs_lock = threading.Lock()

    # Async batch evaluation jobs storage
    _batch_jobs = {}
    _batch_jobs_lock = threading.Lock()

    # Async generate jobs storage
    _generate_jobs = {}
    _generate_jobs_lock = threading.Lock()

    # Async regenerate jobs storage
    _regenerate_jobs = {}
    _regenerate_jobs_lock = threading.Lock()

    # ── Topic-safety lock ────────────────────────────────────────────
    # Tracks the number of running jobs that depend on the active topic
    # (evaluations, comparisons, generations, regenerations).  While the
    # count is > 0 the activate_topic endpoint will be rejected.  A
    # topic switch in progress (_topic_switching flag) blocks new jobs.
    _active_jobs_lock = threading.Lock()
    _active_jobs_count = 0
    _topic_switching = False  # True while activate_topic is executing

    def _register_job():
        """Mark that a topic-dependent job is starting."""
        with _active_jobs_lock:
            if _topic_switching:
                raise RuntimeError(
                    'A topic switch is in progress. '
                    'Please wait until the switch completes before starting a new job.'
                )
            nonlocal _active_jobs_count
            _active_jobs_count += 1

    def _unregister_job():
        """Mark that a topic-dependent job has finished."""
        nonlocal _active_jobs_count
        with _active_jobs_lock:
            _active_jobs_count = max(0, _active_jobs_count - 1)

    def _check_topic_switchable():
        """Raise if the topic cannot be switched right now."""
        with _active_jobs_lock:
            if _active_jobs_count > 0:
                raise RuntimeError(
                    f'Cannot switch topic while {_active_jobs_count} job(s) are running '
                    f'(evaluations, comparisons, or generators). '
                    f'Wait for them to finish or cancel them first.'
                )

    class _RunLogCaptureHandler(logging.Handler):
        """Capture log records for the active run_id context into memory.

        Uses a per-run ``collections.deque`` with a fixed maxlen so that
        appends are thread-safe without acquiring the global lock on
        every log line — only slot creation needs the lock.
        """

        def emit(self, record):
            run_id = _current_run_id.get()
            if not run_id:
                return
            try:
                message = record.getMessage()
            except Exception:
                message = str(record.msg)

            entry = {
                'timestamp': datetime.now().strftime('%H:%M:%S'),
                'level': record.levelname,
                'logger': record.name,
                'message': message,
            }
            now = time.time()
            # Fast path: slot already exists (no lock needed for deque append)
            buff = _run_logs.get(run_id)
            if buff is None:
                with _run_logs_lock:
                    buff = _run_logs.setdefault(run_id, {
                        'entries': _deque(maxlen=_run_logs_max_lines),
                        'created_at': now,
                        'last_access': now,
                    })
            buff['entries'].append(entry)
            buff['last_access'] = now

    def _install_run_log_handler_once():
        root = logging.getLogger()
        for h in root.handlers:
            if getattr(h, '_run_log_capture_handler', False):
                return
        h = _RunLogCaptureHandler(level=logging.INFO)
        h._run_log_capture_handler = True
        root.addHandler(h)

    def _cleanup_run_logs():
        cutoff = time.time() - _run_logs_ttl_sec
        with _run_logs_lock:
            expired = [rid for rid, data in _run_logs.items() if data.get('last_access', 0) < cutoff]
            for rid in expired:
                _run_logs.pop(rid, None)
        # Also clean up old async jobs (compare, batch, generate, regenerate)
        for lock, store in [
            (_compare_jobs_lock, _compare_jobs),
            (_batch_jobs_lock, _batch_jobs),
            (_generate_jobs_lock, _generate_jobs),
            (_regenerate_jobs_lock, _regenerate_jobs),
        ]:
            with lock:
                expired = [rid for rid, j in store.items() if j.get('created', 0) < cutoff]
                for rid in expired:
                    store.pop(rid, None)

    def _normalize_run_id(candidate):
        if not candidate:
            return f"run_{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
        return str(candidate)

    _install_run_log_handler_once()
    
    # ── Helper: get current user context from request ────────────────

    def _get_user_context() -> UserContext:
        """Return the UserContext for the currently authenticated user.

        Built once per request and cached in ``flask.g``.
        """
        ctx = getattr(g, '_user_context', None)
        if ctx is not None:
            return ctx
        user_id = session.get('user_id')
        if not user_id:
            raise RuntimeError('Not authenticated')
        ctx = UserContext(user_id=user_id, base_dir='data/users')
        g._user_context = ctx
        return ctx

    # ── Session establishment (shared logic) ──────────────────────────

    def _establish_session(email: str) -> str:
        """Create / fetch user, bootstrap dirs, set Flask session.

        Returns the ``user_id``.  This is the single place that turns
        an email into a fully-initialised session — used by:
        * ``auth_login`` (code_verification off)
        * ``auth_verify`` (OTP flow)
        * EasyAuth auto-login (Container Apps sidecar)
        """
        user = _user_store.get_or_create(email)
        _user_store.update_last_login(user.id)

        uctx = UserContext(user_id=user.id, base_dir='data/users')
        model_keys = list(
            app.config.get('SETTINGS', {}).get('azure', {}).get('models', {}).keys()
        )
        already_exists = uctx.is_initialised

        # If user data is not on local disk, try restoring from blob
        if not already_exists and blob_sync.is_enabled():
            if blob_sync.download_user(user.id):
                already_exists = uctx.is_initialised
                app.logger.info('Restored user %s from blob storage', user.id)

        uctx.ensure_dirs(model_keys=model_keys)
        # seed_from_shared is idempotent — only copies prompts for model
        # dirs that have no .md files.  Always calling it ensures that
        # models added after the user was created get default prompts.
        seeded = uctx.seed_from_shared(
            shared_prompts='prompts',
            shared_data='data/synthetic',
        )
        if seeded:
            # If new model dirs were seeded AND the user has an active
            # topic, overwrite those defaults with the topic's prompts
            # so the user doesn't end up with mismatched prompts.
            uctx.seed_new_models_from_active_topic(seeded)
        if not already_exists:
            # Persist newly-seeded user to blob so it survives restarts
            blob_sync.upload_user_tree(user.id)
            blob_sync.upload_auth_db()
            app.logger.info('Seeded new user %s from shared data', user.id)

        session.permanent = True
        session['user_id'] = user.id
        session['user_email'] = user.email
        return user.id

    app.logger.info('EasyAuth auto-login enabled: %s', _easyauth_auto_login)

    # ── Auth middleware ───────────────────────────────────────────────

    @app.before_request
    def _require_authentication():
        """Reject unauthenticated requests (except public routes).

        When running behind Azure Container Apps EasyAuth the sidecar
        injects ``X-MS-CLIENT-PRINCIPAL-NAME`` with the user's email.
        If ``easyauth_auto_login`` is enabled and that header is
        present, a Flask session is created transparently so the user
        never sees the login page.
        """
        if is_public_route(request.path):
            return None
        user_id = session.get('user_id')
        if not user_id:
            # ── Try EasyAuth auto-login ──────────────────────────────
            if _easyauth_auto_login:
                ea_email = get_easyauth_email()
                if ea_email:
                    user_id = _establish_session(ea_email)
                    app.logger.info('EasyAuth auto-login for %s', ea_email)
                else:
                    # Log headers for debugging (first request only)
                    principal = request.headers.get('X-MS-CLIENT-PRINCIPAL-NAME', '')
                    principal_id = request.headers.get('X-MS-CLIENT-PRINCIPAL-ID', '')
                    app.logger.debug(
                        'EasyAuth headers: PRINCIPAL-NAME=%r, PRINCIPAL-ID=%r, path=%s',
                        principal, principal_id, request.path,
                    )
        # ── Re-seed user data if dirs were lost (e.g. container restart) ─
        if user_id:
            uctx = UserContext(user_id=user_id, base_dir='data/users')
            if not uctx.is_initialised:
                # Try restoring from blob first (user data persisted there)
                restored = False
                if blob_sync.is_enabled():
                    if blob_sync.download_user(user_id):
                        restored = uctx.is_initialised
                        app.logger.info('Restored user %s from blob after restart', user_id)

                if not restored:
                    app.logger.info('User dirs missing after restart — re-seeding %s', user_id)

                # Always ensure dirs + seed after restore (or from scratch).
                # Blob data may predate newly-added models, so ensure_dirs
                # creates their directories and seed_from_shared fills them
                # with default prompts.  Both are idempotent.
                model_keys = list(
                    app.config.get('SETTINGS', {}).get('azure', {}).get('models', {}).keys()
                )
                uctx.ensure_dirs(model_keys=model_keys)
                seeded = uctx.seed_from_shared(
                    shared_prompts='prompts',
                    shared_data='data/synthetic',
                )
                if seeded:
                    uctx.seed_new_models_from_active_topic(seeded)
                if not restored:
                    blob_sync.upload_user_tree(user_id)

                _invalidate_user(user_id)   # clear stale cached managers
        if not user_id:
            if request.path.startswith('/api/'):
                return jsonify({'error': 'Authentication required'}), 401
            return redirect('/login')
        # Refresh session TTL on every authenticated request (sliding window)
        session.modified = True
        # Make user info available to templates
        g.user_id = user_id
        g.user_email = session.get('user_email', '')

    @app.context_processor
    def _inject_user():
        """Inject user info into all templates."""
        return {
            'user_id': getattr(g, 'user_id', None),
            'user_email': getattr(g, 'user_email', ''),
        }

    # ── Blob write-through: sync after mutating API calls ────────────

    @app.after_request
    def _blob_write_through(response):
        """After any successful POST/PUT/DELETE API call, sync the current
        user's data tree to Blob Storage so changes survive restarts."""
        if (
            blob_sync.is_enabled()
            and request.method in ('POST', 'PUT', 'DELETE')
            and request.path.startswith('/api/')
            and response.status_code < 400
        ):
            uid = session.get('user_id')
            if uid:
                blob_sync.upload_user_tree(uid)
                # Also sync auth.db on login-related endpoints
                if 'auth' in request.path or 'login' in request.path:
                    blob_sync.upload_auth_db()
        return response

    # ── Auth routes ──────────────────────────────────────────────────

    @app.route('/login')
    def login_page():
        if session.get('user_id'):
            return redirect('/')
        # If EasyAuth headers are present, auto-login and skip the form
        if _easyauth_auto_login:
            ea_email = get_easyauth_email()
            if ea_email:
                _establish_session(ea_email)
                app.logger.info('EasyAuth auto-login (login page) for %s', ea_email)
                return redirect('/')
        return render_template('login.html')

    @app.route('/api/auth/login', methods=['POST'])
    def auth_login():
        """Step 1: send OTP code to email (or directly authenticate if code_verification is off)."""
        data = request.get_json() or {}
        email = (data.get('email') or '').strip().lower()
        if not email or '@' not in email:
            return jsonify({'error': 'A valid email address is required'}), 400

        # Skip OTP — authenticate immediately with just the email
        if not _code_verification:
            uid = _establish_session(email)
            return jsonify({'status': 'authenticated', 'user_id': uid, 'redirect': '/'})

        code = _code_manager.generate(email)
        sent = _email_sender.send_code(email, code)
        if not sent:
            return jsonify({'error': 'Failed to send email. Please try again.'}), 500
        return jsonify({'status': 'code_sent', 'email': email})

    @app.route('/api/auth/verify', methods=['POST'])
    def auth_verify():
        """Step 2: verify OTP code, create session."""
        data = request.get_json() or {}
        email = (data.get('email') or '').strip().lower()
        code = (data.get('code') or '').strip()
        if not email or not code:
            return jsonify({'error': 'Email and code are required'}), 400

        ok, msg = _code_manager.verify(email, code)
        if not ok:
            return jsonify({'error': msg}), 401

        uid = _establish_session(email)
        return jsonify({'status': 'authenticated', 'user_id': uid, 'redirect': '/'})

    @app.route('/api/auth/logout', methods=['POST'])
    def auth_logout():
        session.clear()
        return jsonify({'status': 'logged_out'})

    @app.route('/api/auth/me')
    def auth_me():
        uid = session.get('user_id')
        if not uid:
            return jsonify({'authenticated': False}), 401
        return jsonify({'authenticated': True, 'user_id': uid, 'email': session.get('user_email', '')})

    # ── Per-user instance factories ──────────────────────────────────

    def _get_user_slot(uid: str) -> dict:
        """Return the cache slot for *uid*, creating it if needed.

        Thread-safe.  If the cache exceeds ``_MAX_CACHED_USERS`` the
        least-recently-accessed entry is evicted.
        """
        now = time.time()
        with _user_cache_lock:
            slot = _user_cache.get(uid)
            if slot is not None:
                slot["last_access"] = now
                return slot
            # Evict oldest entries if over limit
            while len(_user_cache) >= _MAX_CACHED_USERS:
                oldest_uid = min(_user_cache, key=lambda k: _user_cache[k]["last_access"])
                del _user_cache[oldest_uid]
                app.logger.debug(f"Evicted cached instances for user {oldest_uid}")
            slot = {"last_access": now}
            _user_cache[uid] = slot
            return slot

    def _invalidate_user(uid: str):
        """Remove all cached instances for a user (e.g. after topic switch)."""
        with _user_cache_lock:
            _user_cache.pop(uid, None)

    def get_client():
        nonlocal _client
        if _client is None:
            with _singleton_lock:
                if _client is None:
                    try:
                        _client = create_client_from_config(app.config['CONFIG_PATH'])
                    except Exception as e:
                        app.logger.error(f"Failed to create client: {e}")
                        return None
        return _client

    def get_data_loader() -> DataLoader:
        uctx = _get_user_context()
        uid = uctx.user_id
        slot = _get_user_slot(uid)
        if "data_loader" not in slot:
            slot["data_loader"] = DataLoader(str(uctx.data_dir))
        return slot["data_loader"]

    def get_prompt_loader() -> PromptLoader:
        uctx = _get_user_context()
        uid = uctx.user_id
        slot = _get_user_slot(uid)
        if "prompt_loader" not in slot:
            slot["prompt_loader"] = PromptLoader(str(uctx.prompts_dir))
        return slot["prompt_loader"]

    def get_prompt_manager() -> PromptManager:
        uctx = _get_user_context()
        uid = uctx.user_id
        slot = _get_user_slot(uid)
        if "prompt_manager" not in slot:
            slot["prompt_manager"] = PromptManager(
                prompts_dir=str(uctx.prompts_dir),
                data_dir=str(uctx.data_dir),
                config=app.config.get('SETTINGS', {}),
            )
        return slot["prompt_manager"]

    def get_metrics_calc():
        nonlocal _metrics_calc
        if _metrics_calc is None:
            with _singleton_lock:
                if _metrics_calc is None:
                    cost_rates = app.config.get('SETTINGS', {}).get('cost_rates', {})
                    _metrics_calc = MetricsCalculator(cost_rates=cost_rates if cost_rates else None)
        return _metrics_calc

    def _load_perf_settings() -> dict:
        """Return evaluation performance settings from the cached config.

        Uses ``app.config['SETTINGS']`` (loaded once at startup) instead
        of re-reading settings.yaml from disk on every call.
        """
        return app.config.get('SETTINGS', {}).get('evaluation', {})

    def get_evaluator():
        uctx = _get_user_context()
        uid = uctx.user_id
        client = get_client()
        slot = _get_user_slot(uid)
        if client and "evaluator" not in slot:
            perf = _load_perf_settings()
            slot["evaluator"] = ModelEvaluator(
                client,
                prompt_loader=get_prompt_loader(),
                data_loader=get_data_loader(),
                max_concurrent=perf.get('max_concurrent_requests', 5),
            )
        return slot.get("evaluator")

    def get_realtime_evaluator():
        """Lazy-load the RealtimeEvaluator for voice/realtime models.

        Mirrors the comparator's ``_realtime_settings`` initialisation so
        that standalone Evaluate pages route realtime models through
        TTS → WebSocket instead of chat/completions.
        """
        uctx = _get_user_context()
        uid = uctx.user_id
        client = get_client()
        slot = _get_user_slot(uid)
        if client and "realtime_evaluator" not in slot:
            settings = app.config.get('SETTINGS', {})
            rt_cfg = settings.get('realtime', {})
            raw_ep = rt_cfg.get('endpoint', '')
            realtime_endpoint = client._resolve_env_var(raw_ep) if raw_ep else None
            raw_apiv = rt_cfg.get('api_version', '')
            realtime_api_version = client._resolve_env_var(raw_apiv) if raw_apiv else None
            tts_config = load_tts_config_from_settings({'realtime': rt_cfg})
            perf = _load_perf_settings()
            slot["realtime_evaluator"] = RealtimeEvaluator(
                azure_client=client,
                prompt_loader=get_prompt_loader(),
                data_loader=get_data_loader(),
                tts_config=tts_config,
                max_concurrent=min(perf.get('max_concurrent_requests', 5), 2),
                realtime_endpoint=realtime_endpoint,
                realtime_api_version=realtime_api_version,
            )
        return slot.get("realtime_evaluator")

    def get_comparator():
        uctx = _get_user_context()
        uid = uctx.user_id
        client = get_client()
        slot = _get_user_slot(uid)
        if client and "comparator" not in slot:
            perf = _load_perf_settings()
            settings = app.config.get('SETTINGS', {})
            comp = ModelComparator(
                client,
                evaluator=get_evaluator(),
                foundry_evaluator=get_foundry_evaluator(),
                parallel_models=perf.get('parallel_models', True),
                acceptance_thresholds=settings.get('evaluation', {}).get('acceptance_thresholds', {}),
                prompt_loader=get_prompt_loader(),
                data_loader=get_data_loader(),
            )
            # Pass realtime/voice endpoint config so the RealtimeEvaluator
            # can use a dedicated endpoint + TTS deployment from settings.
            comp._realtime_settings = settings.get('realtime', {})
            slot["comparator"] = comp
        else:
            comp = slot.get("comparator")
            if comp is not None:
                comp.foundry_evaluator = get_foundry_evaluator()
        return slot.get("comparator")

    def get_foundry_evaluator():
        """Lazy-load the Foundry evaluator from settings.yaml config."""
        nonlocal _foundry_evaluator, _foundry_checked
        if _foundry_checked:
            return _foundry_evaluator
        _foundry_checked = True
        if not is_foundry_available():
            return None
        try:
            cfg = app.config.get('SETTINGS', {})
            _foundry_evaluator = create_foundry_evaluator_from_config(cfg)
        except Exception as e:
            app.logger.warning(f'Foundry evaluator init failed: {e}')
            _foundry_evaluator = None
        return _foundry_evaluator

    # =========================================================================
    # Web Routes
    # =========================================================================

    @app.after_request
    def add_no_cache_headers(response):
        """Prevent browser caching of HTML pages so template changes are picked up immediately."""
        if response.content_type and 'text/html' in response.content_type:
            response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
            response.headers['Pragma'] = 'no-cache'
            response.headers['Expires'] = '0'
        return response

    @app.route('/')
    def index():
        """Main dashboard page"""
        return render_template('index.html')
        
    @app.route('/evaluate')
    def evaluate_page():
        """Evaluation runner page"""
        return render_template('evaluate.html')
        
    @app.route('/compare')
    def compare_page():
        """Model comparison page"""
        return render_template('compare.html')
        
    @app.route('/results')
    def results_page():
        """Results browser page"""
        return render_template('results.html')
        
    @app.route('/prompts')
    def prompts_page():
        """Prompt editor/viewer page"""
        return render_template('prompts.html')

    @app.route('/import-samples')
    def import_samples_page():
        """JSON & CSV import samples reference page"""
        return render_template('import_samples.html')

    # =========================================================================
    # API Routes
    # =========================================================================
    
    @app.route('/api/health')
    def health_check():
        """Health check endpoint"""
        client = get_client()
        return jsonify({
            'status': 'healthy',
            'client_ready': client is not None,
            'timestamp': datetime.now().isoformat()
        })

    @app.route('/api/logs/<run_id>')
    def get_run_logs(run_id: str):
        """Fetch backend logs for a specific run_id with offset pagination."""
        _cleanup_run_logs()
        try:
            offset = int(request.args.get('offset', 0))
        except ValueError:
            offset = 0
        if offset < 0:
            offset = 0

        rid = _normalize_run_id(run_id)
        buff = _run_logs.get(rid)
        if buff is None:
            entries_list: list = []
        else:
            entries_list = list(buff.get('entries', []))
            buff['last_access'] = time.time()
        next_offset = len(entries_list)
        slice_entries = entries_list[offset:]

        # Mask sensitive endpoints before sending to the UI
        if _mask_patterns:
            masked = []
            for entry in slice_entries:
                e = dict(entry)
                e['message'] = _mask_verbose_message(e.get('message', ''))
                masked.append(e)
            slice_entries = masked

        return jsonify({
            'run_id': rid,
            'entries': slice_entries,
            'next_offset': next_offset,
        })
        
    @app.route('/api/models')
    def list_models():
        """List available model configurations"""
        client = get_client()
        if not client:
            return jsonify({'error': 'Client not configured'}), 500
            
        models = []
        for name, config in client.models.items():
            # Build a human-friendly display name:
            # reasoning models get a "(reasoning)" suffix so the UI
            # distinguishes them from the regular deployment.
            display = config.deployment_name
            if name.endswith('_reasoning'):
                display = f"{config.deployment_name} (reasoning)"
            models.append({
                'name': name,
                'deployment': config.deployment_name,
                'display_name': display,
                'version': config.model_version,
                'max_tokens': config.max_tokens,
                'model_family': config.model_family or 'gpt4',
                'modality': config.modality,
            })
        return jsonify({'models': models})

    @app.route('/api/settings/test-data-counts')
    def get_test_data_counts():
        """Return the default test-data generation counts from settings.yaml."""
        cfg_dc = app.config.get('SETTINGS', {}).get('evaluation', {}).get('test_data_counts', {})
        defaults = {
            'classification': cfg_dc.get('classification', 20),
            'dialog':         cfg_dc.get('dialog', 15),
            'general':        cfg_dc.get('general', 15),
            'rag':            cfg_dc.get('rag', 10),
            'tool_calling':   cfg_dc.get('tool_calling', 10),
        }
        return jsonify(defaults)

    @app.route('/api/data/summary')
    def data_summary():
        """Get summary of available test data"""
        try:
            loader = get_data_loader()
            summary = loader.get_summary()
            return jsonify(summary)
        except Exception as e:
            return jsonify({'error': str(e)}), 500
            
    @app.route('/api/data/classification')
    def get_classification_data():
        """Get classification test scenarios"""
        try:
            loader = get_data_loader()
            scenarios = loader.load_classification_scenarios()
            return jsonify({
                'count': len(scenarios),
                'scenarios': [
                    {
                        'id': s.id,
                        'customer_input': s.customer_input[:80] if s.customer_input else '-',
                        'expected_category': s.expected_category,
                        'expected_subcategory': s.expected_subcategory,
                        'expected_priority': s.expected_priority,
                        'expected_sentiment': s.expected_sentiment,
                    }
                    for s in scenarios
                ]
            })
        except Exception as e:
            return jsonify({'error': str(e)}), 500
            
    @app.route('/api/data/dialog')
    def get_dialog_data():
        """Get dialog test scenarios"""
        try:
            loader = get_data_loader()
            scenarios = loader.load_dialog_scenarios()
            return jsonify({
                'count': len(scenarios),
                'scenarios': [
                    {
                        'id': s.id,
                        'optimal_follow_up': (s.optimal_follow_up[:80] + '…') if len(s.optimal_follow_up) > 80 else s.optimal_follow_up,
                        'context_gaps': s.context_gaps,
                        'expected_resolution_turns': s.expected_resolution_turns,
                    }
                    for s in scenarios
                ]
            })
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @app.route('/api/data/general')
    def get_general_data():
        """Get general test scenarios"""
        try:
            loader = get_data_loader()
            tests = loader.load_general_tests()
            return jsonify({
                'count': len(tests),
                'scenarios': [
                    {
                        'id': t.id,
                        'prompt': (t.prompt[:80] + '…') if t.prompt and len(t.prompt) > 80 else (t.prompt or '-'),
                        'test_type': t.test_type,
                        'complexity': t.complexity,
                    }
                    for t in tests
                ]
            })
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @app.route('/api/data/rag')
    def get_rag_data():
        """Get RAG test scenarios"""
        try:
            loader = get_data_loader()
            scenarios = loader.load_rag_scenarios()
            return jsonify({
                'count': len(scenarios),
                'scenarios': [
                    {
                        'id': s.id,
                        'query': (s.query[:80] + '…') if s.query and len(s.query) > 80 else (s.query or '-'),
                        'ground_truth': (s.ground_truth[:80] + '…') if s.ground_truth and len(s.ground_truth) > 80 else (s.ground_truth or '-'),
                    }
                    for s in scenarios
                ]
            })
        except FileNotFoundError:
            return jsonify({'count': 0, 'scenarios': []})
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @app.route('/api/data/tool_calling')
    def get_tool_calling_data():
        """Get tool-calling test scenarios"""
        try:
            loader = get_data_loader()
            scenarios = loader.load_tool_calling_scenarios()
            return jsonify({
                'count': len(scenarios),
                'scenarios': [
                    {
                        'id': s.id,
                        'query': (s.query[:80] + '…') if s.query and len(s.query) > 80 else (s.query or '-'),
                        'expected_tool_calls': s.expected_tool_calls,
                    }
                    for s in scenarios
                ]
            })
        except FileNotFoundError:
            return jsonify({'count': 0, 'scenarios': []})
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @app.route('/api/evaluate/single', methods=['POST'])
    def evaluate_single():
        """Evaluate a single prompt against one or both models"""
        data = request.get_json() or {}
        run_id = _normalize_run_id(data.get('run_id') if data else None)
        _cleanup_run_logs()
        token = _current_run_id.set(run_id)
        customer_input = data.get('customer_input', '')
        models_to_test = data.get('models', ['gpt4'])
        evaluation_type = data.get('type', 'classification')
        
        valid_types = ('classification', 'dialog', 'general', 'rag', 'tool_calling')
        if evaluation_type not in valid_types:
            _current_run_id.reset(token)
            return jsonify({'error': f"Invalid evaluation type '{evaluation_type}'. Must be one of {valid_types}."}), 400

        if not customer_input:
            _current_run_id.reset(token)
            return jsonify({'error': 'customer_input is required'}), 400
            
        client = get_client()
        if not client:
            _current_run_id.reset(token)
            return jsonify({'error': 'Client not configured'}), 500
            
        prompt_loader = get_prompt_loader()
        metrics_calc = get_metrics_calc()

        # Eagerly resolve the realtime evaluator *in the request thread*
        # (where Flask app-context is active) so the thread-pool closures
        # below never need to call get_realtime_evaluator() themselves.
        _has_realtime = any(
            client.models.get(m) and client.models[m].backend == 'realtime'
            for m in models_to_test
        )
        rt_eval = get_realtime_evaluator() if _has_realtime else None

        # --- Helper: route a realtime model through TTS → WebSocket ----
        def _eval_one_realtime(model_name: str, config):
            """Send *customer_input* through the Realtime API pipeline.

            1. Load the agent system prompt for the evaluation type.
            2. Synthesise the user text to PCM-16 audio via TTS.
            3. Open a Realtime WebSocket session and stream the audio.
            4. Return the transcript + latency metrics.
            """
            try:
                import asyncio
                if not rt_eval:
                    return model_name, {'error': 'Realtime evaluator not available — check realtime settings in settings.yaml'}

                # Prompt type mapping (same keys as _EVAL_PROMPT_TYPES)
                _prompt_map = {
                    'classification': 'classification_agent_system',
                    'dialog': 'dialog_agent_system',
                    'rag': 'rag_agent_system',
                    'tool_calling': 'tool_calling_agent_system',
                }
                prompt_type = _prompt_map.get(evaluation_type)
                instructions = ""
                if prompt_type:
                    instructions = prompt_loader.load_prompt(model_name, prompt_type)

                # Enrich instructions for specific types
                if evaluation_type == 'classification':
                    instructions += "\n\nRespond ONLY with a valid JSON object containing your classification."
                elif evaluation_type == 'rag' and data.get('context'):
                    instructions += f"\n\nContext:\n{data['context']}"

                # TTS synthesis
                tts = rt_eval._ensure_tts()
                voice = config.voice or "alloy"
                tts_r = tts.synthesize(customer_input, voice)

                # WebSocket send
                rt_config = RealtimeConfig(
                    deployment_name=config.deployment_name,
                    voice=voice,
                    modalities=["text", "audio"],
                    instructions=instructions,
                    temperature=config.temperature,
                    max_response_output_tokens=config.max_tokens,
                )

                realtime = rt_eval._ensure_realtime()
                rt_result = asyncio.run(realtime.send_audio(tts_r.audio.data, rt_config))

                result = {
                    'response': rt_result.transcript,
                    'latency': rt_result.session_time_ms / 1000.0,
                    'tokens': {
                        'prompt': rt_result.input_tokens,
                        'completion': rt_result.output_tokens,
                        'total': rt_result.input_tokens + rt_result.output_tokens,
                        'cached': 0,
                        'reasoning': 0,
                    },
                    'cost': 0,  # realtime pricing differs — not estimated here
                    'realtime': {
                        'ttfa_ms': rt_result.time_to_first_audio_ms,
                        'session_ms': rt_result.session_time_ms,
                        'ws_connect_ms': rt_result.ws_connect_time_ms,
                        'tts_latency_ms': tts_r.tts_latency_ms,
                        'tts_cached': tts_r.cached,
                    },
                }

                if evaluation_type == 'classification':
                    result['parsed'] = metrics_calc.extract_classification_from_response(
                        rt_result.transcript
                    )

                return model_name, result

            except Exception as e:
                return model_name, {'error': str(e)}

        # --- Helper: evaluate one model (runs inside a thread) ----------
        def _eval_one_model(model_name: str):
            if model_name not in client.models:
                return model_name, {'error': f'Model {model_name} not configured'}

            # ── Realtime models → TTS + WebSocket (not chat/completions) ──
            config = client.models[model_name]
            if config.backend == "realtime":
                return _eval_one_realtime(model_name, config)

            try:
                if evaluation_type == 'classification':
                    messages = prompt_loader.load_classification_prompt(
                        model=model_name,
                        customer_message=customer_input
                    )
                    response_format = {"type": "json_object"}
                elif evaluation_type == 'dialog':
                    messages = prompt_loader.load_dialog_prompt(
                        model=model_name,
                        conversation=[{'role': 'customer', 'message': customer_input}]
                    )
                    response_format = None
                elif evaluation_type == 'rag':
                    messages = prompt_loader.load_rag_prompt(
                        model=model_name,
                        query=customer_input,
                        context=data.get('context', '')
                    )
                    response_format = None
                elif evaluation_type == 'tool_calling':
                    messages = prompt_loader.load_tool_calling_prompt(
                        model=model_name,
                        query=customer_input,
                        available_tools=data.get('available_tools', [])
                    )
                    response_format = None
                else:
                    messages = prompt_loader.load_general_prompt(
                        model=model_name,
                        prompt=customer_input
                    )
                    response_format = None

                completion = client.complete(
                    messages=messages,
                    model_name=model_name,
                    response_format=response_format
                )

                result = {
                    'response': completion.content,
                    'latency': completion.metrics.total_time,
                    'tokens': {
                        'prompt': completion.metrics.prompt_tokens,
                        'completion': completion.metrics.completion_tokens,
                        'total': completion.metrics.total_tokens,
                        'cached': completion.metrics.cached_tokens,
                        'reasoning': completion.metrics.reasoning_tokens,
                    },
                    'cost': _estimate_cost(model_name, completion.metrics),
                }

                if evaluation_type == 'classification':
                    result['parsed'] = metrics_calc.extract_classification_from_response(
                        completion.content
                    )

                return model_name, result

            except Exception as e:
                return model_name, {'error': str(e)}

        # --- Dispatch all models in parallel ----------------------------
        from concurrent.futures import as_completed
        results = {}
        futures = {_shared_pool.submit(_eval_one_model, m): m for m in models_to_test}
        for future in as_completed(futures):
            model_name, result = future.result()
            results[model_name] = result

        try:
            return jsonify(results)
        finally:
            _current_run_id.reset(token)
        
    @app.route('/api/evaluate/batch', methods=['POST'])
    def evaluate_batch():
        """Run batch evaluation — runs in background thread, returns 202."""
        data = request.get_json() or {}
        run_id = _normalize_run_id(data.get('run_id') if data else None)
        _cleanup_run_logs()
        model_name = data.get('model', 'gpt4')
        evaluation_type = data.get('type', 'classification')
        limit = min(data.get('limit', 10), 100)  # Cap at 100 to prevent abuse

        try:
            _register_job()
        except RuntimeError as e:
            return jsonify({'error': str(e), 'run_id': run_id}), 409

        # Detect realtime models — they route through RealtimeEvaluator
        # (TTS → WebSocket) instead of the text chat/completions path.
        _client = get_client()
        _model_cfg = _client.models.get(model_name) if _client else None
        is_realtime = _model_cfg is not None and _model_cfg.backend == "realtime"

        if is_realtime:
            rt_evaluator = get_realtime_evaluator()
            if not rt_evaluator:
                _unregister_job()
                return jsonify({
                    'error': 'Realtime evaluator not available — check realtime settings in settings.yaml',
                    'run_id': run_id,
                }), 500
        else:
            evaluator = get_evaluator()
            if not evaluator:
                _unregister_job()
                return jsonify({'error': 'Evaluator not available', 'run_id': run_id}), 500

        # Pre-load data in the request thread (fast, needs user context).
        # Realtime models load their own data inside RealtimeEvaluator,
        # so skip pre-loading for them.
        scenarios = None
        if not is_realtime:
            loader = get_data_loader()
            try:
                if evaluation_type == 'classification':
                    scenarios = loader.load_classification_scenarios()[:limit]
                elif evaluation_type == 'dialog':
                    scenarios = loader.load_dialog_scenarios()[:limit]
                elif evaluation_type == 'rag':
                    scenarios = loader.load_rag_scenarios()[:limit]
                elif evaluation_type == 'tool_calling':
                    scenarios = loader.load_tool_calling_scenarios()[:limit]
                else:
                    scenarios = loader.load_general_tests()[:limit]
            except Exception as e:
                _unregister_job()
                return jsonify({'error': str(e), 'run_id': run_id}), 400

        results_dir = str(_get_user_context().results_dir)

        # Register job as running
        with _batch_jobs_lock:
            _batch_jobs[run_id] = {
                'status': 'running',
                'result': None,
                'error': None,
                'error_type': None,
                'created': time.time(),
            }

        def _bg_batch():
            """Background worker — runs evaluation and stores result."""
            token = _current_run_id.set(run_id)
            try:
                if is_realtime:
                    # Realtime path: TTS → WebSocket (uses RealtimeEvaluator)
                    import asyncio
                    result = asyncio.run(
                        rt_evaluator.evaluate_async(model_name, evaluation_type)
                    )
                elif evaluation_type == 'classification':
                    result = evaluator.evaluate_classification(model_name, scenarios)
                elif evaluation_type == 'dialog':
                    result = evaluator.evaluate_dialog(model_name, scenarios)
                elif evaluation_type == 'rag':
                    result = evaluator.evaluate_rag(model_name, scenarios)
                elif evaluation_type == 'tool_calling':
                    result = evaluator.evaluate_tool_calling(model_name, scenarios)
                else:
                    result = evaluator.evaluate_general(model_name, scenarios)

                # Auto-save results to disk
                try:
                    result.save(results_dir)
                    logging.getLogger(__name__).info(
                        f"Auto-saved {evaluation_type} result for {model_name}"
                    )
                except Exception as save_err:
                    logging.getLogger(__name__).warning(f"Failed to auto-save result: {save_err}")

                result_dict = result.to_dict()
                ts = result.timestamp.replace(':', '-')
                result_dict['saved_filename'] = f"{model_name}_{evaluation_type}_{ts}.json"
                result_dict['run_id'] = run_id
                with _batch_jobs_lock:
                    _batch_jobs[run_id]['status'] = 'completed'
                    _batch_jobs[run_id]['result'] = result_dict
            except MissingPromptsError as exc:
                logging.getLogger(__name__).warning(
                    f"Missing prompts for {model_name}/{evaluation_type}: {exc}"
                )
                with _batch_jobs_lock:
                    _batch_jobs[run_id]['status'] = 'failed'
                    _batch_jobs[run_id]['error'] = str(exc)
                    _batch_jobs[run_id]['error_type'] = 'missing_prompts'
            except Exception as exc:
                logging.getLogger(__name__).error(f"Batch evaluation failed: {exc}")
                with _batch_jobs_lock:
                    _batch_jobs[run_id]['status'] = 'failed'
                    _batch_jobs[run_id]['error'] = str(exc)
            finally:
                _unregister_job()
                _current_run_id.reset(token)

        thread = threading.Thread(target=_bg_batch, daemon=True)
        thread.start()

        return jsonify({'status': 'running', 'run_id': run_id}), 202

    @app.route('/api/evaluate/batch/<run_id>/status')
    def batch_status(run_id: str):
        """Poll batch evaluation job status; returns result payload when complete."""
        rid = _normalize_run_id(run_id)
        with _batch_jobs_lock:
            job = _batch_jobs.get(rid)
        if not job:
            return jsonify({'status': 'not_found', 'run_id': rid}), 404

        resp = {'status': job['status'], 'run_id': rid}
        if job['status'] == 'completed':
            resp['result'] = job['result']
        elif job['status'] == 'failed':
            resp['error'] = job['error']
            if job.get('error_type'):
                resp['error_type'] = job['error_type']
        return jsonify(resp)
            
    @app.route('/api/compare', methods=['POST'])
    def compare_models():
        """Compare two models — runs in background thread, returns 202."""
        data = request.get_json() or {}
        run_id = _normalize_run_id(data.get('run_id') if data else None)
        _cleanup_run_logs()
        model_a = data.get('model_a', 'gpt4')
        model_b = data.get('model_b', 'gpt5')
        evaluation_type = data.get('type', 'classification')
        include_foundry = bool(data.get('include_foundry', False))

        try:
            _register_job()
        except RuntimeError as e:
            return jsonify({'error': str(e), 'run_id': run_id}), 409

        comparator = get_comparator()
        if not comparator:
            _unregister_job()
            return jsonify({'error': 'Comparator not available', 'run_id': run_id}), 500

        results_dir = str(_get_user_context().results_dir)
        # Capture user_id for background thread
        _bg_user_id = session.get('user_id')

        # Register job as running
        with _compare_jobs_lock:
            _compare_jobs[run_id] = {
                'status': 'running',
                'result': None,
                'error': None,
                'created': time.time(),
            }

        def _bg_compare():
            """Background worker — sets run_id context so logs stream correctly."""
            # Restore user context for this thread
            _uctx = UserContext(user_id=_bg_user_id, base_dir='data/users')
            token = _current_run_id.set(run_id)
            try:
                report = comparator.compare_models(
                    model_a=model_a,
                    model_b=model_b,
                    evaluation_type=evaluation_type,
                    include_foundry=include_foundry,
                )
                # Auto-save comparison results to disk
                try:
                    report.save(results_dir)
                    logging.getLogger(__name__).info(
                        f"Auto-saved comparison {model_a} vs {model_b} ({evaluation_type})"
                    )
                except Exception as save_err:
                    logging.getLogger(__name__).warning(
                        f"Failed to auto-save comparison: {save_err}"
                    )

                payload = report.to_dict()
                payload['run_id'] = run_id
                with _compare_jobs_lock:
                    _compare_jobs[run_id]['status'] = 'completed'
                    _compare_jobs[run_id]['result'] = payload
            except Exception as exc:
                logging.getLogger(__name__).error(f"Comparison failed: {exc}")
                with _compare_jobs_lock:
                    _compare_jobs[run_id]['status'] = 'failed'
                    _compare_jobs[run_id]['error'] = str(exc)
            finally:
                _unregister_job()
                _current_run_id.reset(token)

        thread = threading.Thread(target=_bg_compare, daemon=True)
        thread.start()

        return jsonify({'status': 'running', 'run_id': run_id}), 202

    @app.route('/api/compare/<run_id>/status')
    def compare_status(run_id: str):
        """Poll comparison job status; returns result payload when complete."""
        rid = _normalize_run_id(run_id)
        with _compare_jobs_lock:
            job = _compare_jobs.get(rid)
        if not job:
            return jsonify({'status': 'not_found', 'run_id': rid}), 404

        resp = {'status': job['status'], 'run_id': rid}
        if job['status'] == 'completed':
            resp['result'] = job['result']
        elif job['status'] == 'failed':
            resp['error'] = job['error']
        return jsonify(resp)

    # ── Batch comparison (Model A vs multiple Model B's) ─────────────

    @app.route('/api/compare/batch', methods=['POST'])
    def compare_models_batch():
        """Compare model_a against a list of model_b's — background thread, returns 202.

        Request body:
            model_a:        str
            model_b_list:   list[str]   (≥1 models)
            type:           str         evaluation type
            include_foundry: bool
            run_id:         str|null
        """
        data = request.get_json() or {}
        run_id = _normalize_run_id(data.get('run_id'))
        _cleanup_run_logs()
        model_a = data.get('model_a', 'gpt4')
        model_b_list = data.get('model_b_list', [])
        evaluation_type = data.get('type', 'classification')
        include_foundry = bool(data.get('include_foundry', False))

        if not model_b_list or not isinstance(model_b_list, list):
            return jsonify({'error': 'model_b_list must be a non-empty list', 'run_id': run_id}), 400

        try:
            _register_job()
        except RuntimeError as e:
            return jsonify({'error': str(e), 'run_id': run_id}), 409

        comparator = get_comparator()
        if not comparator:
            _unregister_job()
            return jsonify({'error': 'Comparator not available', 'run_id': run_id}), 500

        results_dir = str(_get_user_context().results_dir)
        _bg_user_id = session.get('user_id')

        # Register batch job
        with _compare_jobs_lock:
            _compare_jobs[run_id] = {
                'status': 'running',
                'mode': 'batch',
                'total': len(model_b_list),
                'completed': 0,
                'current_model_b': None,
                'results': {mb: {'status': 'pending'} for mb in model_b_list},
                'error': None,
                'created': time.time(),
            }

        def _bg_batch_compare():
            _uctx = UserContext(user_id=_bg_user_id, base_dir='data/users')
            token = _current_run_id.set(run_id)
            try:
                def _progress(completed_idx, total, current_mb, report):
                    with _compare_jobs_lock:
                        job = _compare_jobs.get(run_id)
                        if not job:
                            return
                        job['completed'] = completed_idx
                        job['current_model_b'] = current_mb
                        if report is not None:
                            try:
                                report.save(results_dir)
                                logging.getLogger(__name__).info(
                                    f"Auto-saved batch comparison {model_a} vs {current_mb} ({evaluation_type})"
                                )
                            except Exception as save_err:
                                logging.getLogger(__name__).warning(
                                    f"Failed to auto-save batch comparison: {save_err}"
                                )
                            job['results'][current_mb] = {
                                'status': 'completed',
                                'report': report.to_dict(),
                            }
                        else:
                            job['results'][current_mb] = {
                                'status': 'failed',
                            }

                reports = comparator.compare_models_batch(
                    model_a=model_a,
                    model_b_list=model_b_list,
                    evaluation_type=evaluation_type,
                    include_foundry=include_foundry,
                    progress_callback=_progress,
                )

                with _compare_jobs_lock:
                    _compare_jobs[run_id]['status'] = 'completed'
            except Exception as exc:
                logging.getLogger(__name__).error(f"Batch comparison failed: {exc}")
                with _compare_jobs_lock:
                    _compare_jobs[run_id]['status'] = 'failed'
                    _compare_jobs[run_id]['error'] = str(exc)
            finally:
                _unregister_job()
                _current_run_id.reset(token)

        thread = threading.Thread(target=_bg_batch_compare, daemon=True)
        thread.start()

        return jsonify({
            'status': 'running',
            'run_id': run_id,
            'mode': 'batch',
            'total': len(model_b_list),
        }), 202

    @app.route('/api/compare/batch/<run_id>/status')
    def compare_batch_status(run_id: str):
        """Poll batch comparison job status with per-model progress."""
        rid = _normalize_run_id(run_id)
        with _compare_jobs_lock:
            job = _compare_jobs.get(rid)
        if not job:
            return jsonify({'status': 'not_found', 'run_id': rid}), 404
        if job.get('mode') != 'batch':
            return jsonify({'status': 'not_found', 'run_id': rid, 'error': 'Not a batch job'}), 404

        resp = {
            'status': job['status'],
            'run_id': rid,
            'total': job.get('total', 0),
            'completed': job.get('completed', 0),
            'current_model_b': job.get('current_model_b'),
            'results': job.get('results', {}),
        }
        if job['status'] == 'failed' and job.get('error'):
            resp['error'] = job['error']
        return jsonify(resp)
            
    @app.route('/api/prompts/<model>/<prompt_type>')
    def get_prompt(model: str, prompt_type: str):
        """Get a specific prompt template"""
        try:
            loader = get_prompt_loader()
            content = loader.load_prompt(model, prompt_type)
            return jsonify({
                'model': model,
                'type': prompt_type,
                'content': content
            })
        except FileNotFoundError:
            return jsonify({'error': 'Prompt not found'}), 404
        except Exception as e:
            return jsonify({'error': str(e)}), 500
            
    @app.route('/api/prompts')
    def list_prompts():
        """List all available prompts"""
        try:
            loader = get_prompt_loader()
            available = loader.list_available_prompts()
            return jsonify(available)
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    # =========================================================================
    # Prompt Management API (edit, save, generate, version history)
    # =========================================================================

    @app.route('/api/prompts/<model>/<prompt_type>', methods=['PUT'])
    def save_prompt(model: str, prompt_type: str):
        """Save/update a prompt template (creates version snapshot)."""
        data = request.get_json() or {}
        content = data.get('content', '')
        topic = data.get('topic', '')
        if not content:
            return jsonify({'error': 'content is required'}), 400

        try:
            manager = get_prompt_manager()
            entry = manager.save_prompt(
                model=model,
                prompt_type=prompt_type,
                content=content,
                topic=topic,
                source='manual',
            )
            # Track topic change so sync-status knows data is stale
            if topic:
                manager._save_topic_metadata(topic, prompts_updated=True)
            # Invalidate prompt loader cache so next read picks up changes
            loader = get_prompt_loader()
            loader._cache.clear()
            # Check if data is now out of sync
            sync = manager.is_data_in_sync()
            return jsonify({'status': 'saved', 'version': entry, 'data_sync': sync})
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @app.route('/api/prompts/history')
    def list_prompt_versions():
        """List version history, optionally filtered by model/type/topic."""
        model = request.args.get('model')
        prompt_type = request.args.get('type')
        topic = request.args.get('topic')
        try:
            manager = get_prompt_manager()
            versions = manager.list_versions(model=model, prompt_type=prompt_type, topic=topic)
            return jsonify({'versions': versions})
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @app.route('/api/prompts/history/<version_id>')
    def get_prompt_version(version_id: str):
        """Read the content of a specific version."""
        # Sanitize: version_id should be alphanumeric + underscores only
        import re
        if not re.match(r'^[\w]+$', version_id):
            return jsonify({'error': 'Invalid version ID'}), 400
        try:
            manager = get_prompt_manager()
            content = manager.get_version_content(version_id)
            if content is None:
                return jsonify({'error': 'Version not found'}), 404
            # Find metadata
            entry = next((v for v in manager._index if v['id'] == version_id), {})
            return jsonify({'version_id': version_id, 'content': content, 'metadata': entry})
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @app.route('/api/prompts/restore', methods=['POST'])
    def restore_prompt_version():
        """Restore a historical version as the active prompt."""
        data = request.get_json()
        version_id = data.get('version_id', '')
        import re
        if not version_id or not re.match(r'^[\w]+$', version_id):
            return jsonify({'error': 'Invalid version_id'}), 400
        try:
            manager = get_prompt_manager()
            entry = manager.restore_version(version_id)
            if entry is None:
                return jsonify({'error': 'Version not found'}), 404
            loader = get_prompt_loader()
            loader._cache.clear()
            return jsonify({'status': 'restored', 'version': entry})
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @app.route('/api/prompts/history/<version_id>', methods=['DELETE'])
    def delete_prompt_version(version_id: str):
        """Delete a specific version from history."""
        import re
        if not re.match(r'^[\w]+$', version_id):
            return jsonify({'error': 'Invalid version ID'}), 400
        try:
            manager = get_prompt_manager()
            deleted = manager.delete_version(version_id)
            if not deleted:
                return jsonify({'error': 'Version not found'}), 404
            return jsonify({'status': 'deleted', 'version_id': version_id})
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @app.route('/api/prompts/history/bulk-delete', methods=['POST'])
    def delete_prompt_versions_bulk():
        """Delete multiple versions from history at once."""
        data = request.get_json()
        version_ids = data.get('version_ids', [])
        if not version_ids or not isinstance(version_ids, list):
            return jsonify({'error': 'version_ids (list) is required'}), 400
        import re
        for vid in version_ids:
            if not re.match(r'^[\w]+$', str(vid)):
                return jsonify({'error': f'Invalid version ID: {vid}'}), 400
        try:
            manager = get_prompt_manager()
            count = manager.delete_versions_bulk(version_ids)
            return jsonify({'status': 'deleted', 'deleted_count': count})
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @app.route('/api/prompts/generate', methods=['POST'])
    def generate_prompts():
        """AI-generate prompts + test data — runs in background thread, returns 202."""
        data = request.get_json() or {}
        run_id = _normalize_run_id(data.get('run_id'))
        _cleanup_run_logs()
        topic = data.get('topic', '')
        generator_model = data.get('generator_model', 'gpt5')
        target_models = data.get('target_models')  # Optional list of model keys
        data_counts = data.get('data_counts')       # Optional {type: int} overrides
        instructions = data.get('instructions', '')  # Optional custom instructions
        scope = data.get('scope', 'all')            # "all" | "prompts_only" | "data_only"
        if scope not in ('all', 'prompts_only', 'data_only'):
            scope = 'all'
        if not topic:
            return jsonify({'error': 'topic is required'}), 400
        try:
            _register_job()
        except RuntimeError as e:
            return jsonify({'error': str(e), 'run_id': run_id}), 409

        client = get_client()
        if not client:
            _unregister_job()
            return jsonify({'error': 'Client not configured'}), 500

        # Capture user references for background thread
        _bg_user_id_gen = session.get('user_id')
        _bg_user_data_dir = str(_get_user_context().data_dir)
        _bg_prompt_manager = get_prompt_manager()
        _bg_prompt_loader = get_prompt_loader()
        _bg_data_loader = get_data_loader()

        with _generate_jobs_lock:
            _generate_jobs[run_id] = {
                'status': 'running',
                'result': None,
                'error': None,
                'created': time.time(),
            }

        def _bg_generate():
            """Background worker for prompt + test data generation."""
            token = _current_run_id.set(run_id)
            try:
                manager = _bg_prompt_manager
                results = manager.generate_prompts(
                    topic=topic,
                    client=client,
                    generator_model=generator_model,
                    data_dir=_bg_user_data_dir,
                    target_models=target_models,
                    data_counts=data_counts,
                    scope=scope,
                    instructions=instructions,
                )
                # Invalidate caches so new content is picked up
                _bg_prompt_loader._cache.clear()
                _bg_data_loader.clear_cache()
                payload = {
                    'status': 'generated',
                    'topic': topic,
                    'scope': scope,
                    'prompts': results.get('prompts', {}),
                    'data': results.get('data', {}),
                    'run_id': run_id,
                }
                with _generate_jobs_lock:
                    _generate_jobs[run_id]['status'] = 'completed'
                    _generate_jobs[run_id]['result'] = payload
            except Exception as exc:
                logging.getLogger(__name__).error(f"Generation failed: {exc}")
                with _generate_jobs_lock:
                    _generate_jobs[run_id]['status'] = 'failed'
                    _generate_jobs[run_id]['error'] = str(exc)
            finally:
                _unregister_job()
                _current_run_id.reset(token)

        thread = threading.Thread(target=_bg_generate, daemon=True)
        thread.start()

        return jsonify({'status': 'running', 'run_id': run_id}), 202

    @app.route('/api/prompts/generate/<run_id>/status')
    def generate_status(run_id: str):
        """Poll generation job status; returns result payload when complete."""
        rid = _normalize_run_id(run_id)
        with _generate_jobs_lock:
            job = _generate_jobs.get(rid)
        if not job:
            return jsonify({'status': 'not_found', 'run_id': rid}), 404

        resp = {'status': job['status'], 'run_id': rid}
        if job['status'] == 'completed':
            resp['result'] = job['result']
        elif job['status'] == 'failed':
            resp['error'] = job['error']
        return jsonify(resp)
            
    # =========================================================================
    # Data Sync (auto-review synthetic data when topic changes)
    # =========================================================================

    @app.route('/api/data/sync-status')
    def data_sync_status():
        """Check whether synthetic test data is in sync with the current topic."""
        try:
            manager = get_prompt_manager()
            status = manager.is_data_in_sync()
            return jsonify(status)
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @app.route('/api/data/regenerate', methods=['POST'])
    def regenerate_test_data():
        """Regenerate synthetic test data — runs in background thread, returns 202."""
        data = request.get_json() or {}
        run_id = _normalize_run_id(data.get('run_id'))
        _cleanup_run_logs()
        topic = data.get('topic')
        generator_model = data.get('generator_model', 'gpt5')
        data_counts = data.get('data_counts')  # Optional {type: int} overrides

        try:
            _register_job()
        except RuntimeError as e:
            return jsonify({'error': str(e), 'run_id': run_id}), 409

        client = get_client()
        if not client:
            _unregister_job()
            return jsonify({'error': 'Client not configured'}), 500

        # Capture user references for background thread
        _bg_user_id_regen = session.get('user_id')
        _bg_user_data_dir_regen = str(_get_user_context().data_dir)
        _bg_prompt_manager_regen = get_prompt_manager()
        _bg_data_loader_regen = get_data_loader()

        # Register job as running
        with _regenerate_jobs_lock:
            _regenerate_jobs[run_id] = {
                'status': 'running',
                'result': None,
                'error': None,
                'created': time.time(),
            }

        def _bg_regenerate():
            """Background worker for test data regeneration."""
            token = _current_run_id.set(run_id)
            try:
                manager = _bg_prompt_manager_regen
                result = manager.regenerate_test_data(
                    client=client,
                    generator_model=generator_model,
                    data_dir=_bg_user_data_dir_regen,
                    topic=topic,
                    data_counts=data_counts,
                )
                if 'error' in result and isinstance(result.get('error'), str):
                    with _regenerate_jobs_lock:
                        _regenerate_jobs[run_id]['status'] = 'failed'
                        _regenerate_jobs[run_id]['error'] = result['error']
                    return
                # Invalidate data loader cache
                _bg_data_loader_regen.clear_cache()
                payload = {'status': 'regenerated', 'data': result, 'run_id': run_id}
                with _regenerate_jobs_lock:
                    _regenerate_jobs[run_id]['status'] = 'completed'
                    _regenerate_jobs[run_id]['result'] = payload
            except Exception as exc:
                logging.getLogger(__name__).error(f"Regeneration failed: {exc}")
                with _regenerate_jobs_lock:
                    _regenerate_jobs[run_id]['status'] = 'failed'
                    _regenerate_jobs[run_id]['error'] = str(exc)
            finally:
                _unregister_job()
                _current_run_id.reset(token)

        thread = threading.Thread(target=_bg_regenerate, daemon=True)
        thread.start()

        return jsonify({'status': 'running', 'run_id': run_id}), 202

    @app.route('/api/data/regenerate/<run_id>/status')
    def regenerate_status(run_id: str):
        """Poll regeneration job status; returns result payload when complete."""
        rid = _normalize_run_id(run_id)
        with _regenerate_jobs_lock:
            job = _regenerate_jobs.get(rid)
        if not job:
            return jsonify({'status': 'not_found', 'run_id': rid}), 404

        resp = {'status': job['status'], 'run_id': rid}
        if job['status'] == 'completed':
            resp['result'] = job['result']
        elif job['status'] == 'failed':
            resp['error'] = job['error']
        return jsonify(resp)

    @app.route('/api/results')
    def list_results():
        """List saved evaluation results.

        Optional query params for pagination:
          ``?page=1&per_page=20``
        When *page* is supplied, only that page of results is returned
        along with ``total``, ``page``, ``per_page``, and ``pages``
        metadata.  Without pagination params, **all** results are
        returned (backward-compatible).
        """
        results_dir = Path(str(_get_user_context().results_dir))
        if not results_dir.exists():
            return jsonify({'results': [], 'total': 0})

        # Collect JSON result files sorted newest-first by filesystem mtime
        # (avoids reading every JSON just to sort by timestamp).
        json_files = sorted(
            results_dir.glob('*.json'),
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )
        total = len(json_files)

        # Pagination (optional)
        page_str = request.args.get('page')
        per_page = min(int(request.args.get('per_page', 50)), 200)
        if page_str is not None:
            page = max(int(page_str), 1)
            start = (page - 1) * per_page
            json_files = json_files[start:start + per_page]
        else:
            page = None

        results = []
        for file in json_files:
            try:
                # Read only the first 3 KB — metadata fields (model_name,
                # evaluation_type, timestamp, model_a/b, foundry URLs) are
                # always at the top of the JSON, so we avoid parsing the
                # potentially large raw_results arrays.
                with open(file, 'rb') as f:
                    head = f.read(3072)
                # Complete the truncated JSON so it can be parsed
                text = head.decode('utf-8', errors='replace')
                # Try to parse. If the file is small enough, this works directly.
                # For large files, we'll get a decode error; fall back to regex.
                try:
                    data = json.loads(text)
                except json.JSONDecodeError:
                    # Extract top-level fields from the partial JSON via simple parsing
                    import re as _re_local
                    def _extract(key):
                        m = _re_local.search(rf'"{key}"\s*:\s*"([^"]*)"', text)
                        return m.group(1) if m else ''
                    data = {
                        'model_name': _extract('model_name'),
                        'model_a': _extract('model_a'),
                        'model_b': _extract('model_b'),
                        'evaluation_type': _extract('evaluation_type'),
                        'timestamp': _extract('timestamp'),
                        'foundry_report_url': _extract('foundry_report_url'),
                        'batch_id': _extract('batch_id'),
                    }

                model_display = data.get('model_name', 'unknown')
                if model_display == 'unknown' and 'model_a' in data and data.get('model_a'):
                    model_display = f"{data['model_a']} vs {data.get('model_b', '?')}"
                entry = {
                    'filename': file.name,
                    'model': model_display,
                    'type': data.get('evaluation_type', 'unknown'),
                    'timestamp': data.get('timestamp', ''),
                }
                # Include batch_id when available (batch comparisons)
                bid = data.get('batch_id')
                if bid:
                    entry['batch_id'] = bid
                # Include Foundry report URLs when available
                if data.get('foundry_report_url'):
                    entry['foundry_report_url'] = data['foundry_report_url']
                fm = data.get('foundry_meta') or {}
                urls = {}
                for key in ('model_a', 'model_b'):
                    url = (fm.get(key) or {}).get('report_url')
                    if url:
                        urls[key] = url
                if urls:
                    entry['foundry_urls'] = urls
                results.append(entry)
            except (json.JSONDecodeError, OSError) as e:
                app.logger.debug(f"Skipping unreadable result file {file.name}: {e}")
                continue

        response: dict = {'results': results, 'total': total}
        if page is not None:
            response['page'] = page
            response['per_page'] = per_page
            response['pages'] = max(1, -(-total // per_page))  # ceil division
        return jsonify(response)
        
    @app.route('/api/results/<filename>', methods=['DELETE'])
    def delete_result(filename: str):
        """Delete a saved result file"""
        safe_name = Path(filename).name
        if safe_name != filename or '..' in filename:
            return jsonify({'error': 'Invalid filename'}), 400
        results_dir = Path(str(_get_user_context().results_dir))
        file_path = results_dir / safe_name
        if not file_path.exists() or not file_path.suffix == '.json':
            return jsonify({'error': 'Result not found'}), 404
        try:
            file_path.unlink()
            return jsonify({'status': 'deleted', 'filename': safe_name})
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @app.route('/api/results/<filename>')
    def get_result(filename: str):
        """Get a specific result file"""
        # Sanitize filename to prevent path traversal
        safe_name = Path(filename).name
        if safe_name != filename or '..' in filename:
            return jsonify({'error': 'Invalid filename'}), 400
            
        results_dir = Path(str(_get_user_context().results_dir))
        file_path = results_dir / safe_name
        
        if not file_path.exists() or not file_path.suffix == '.json':
            return jsonify({'error': 'Result not found'}), 404
            
        try:
            with open(file_path, encoding='utf-8') as f:
                data = json.load(f)
            return jsonify(data)
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    # =========================================================================
    # Test Data CRUD API (view / edit raw test data for active & archived topics)
    # =========================================================================

    @app.route('/api/data/overview')
    def get_data_overview():
        """Return counts per data-type for the active set and every archived topic.

        Uses a per-user in-memory cache with a 5-second TTL to avoid
        re-scanning and parsing every data file on every page load.
        """
        uid = session.get('user_id', '')
        slot = _get_user_slot(uid) if uid else {}
        now = time.time()
        cached = slot.get('_data_overview_cache')
        if cached and (now - cached['ts']) < 5.0:
            return jsonify({'overview': cached['data']})

        manager = get_prompt_manager()
        data_files = manager._DATA_FILES
        data_dir = Path(str(_get_user_context().data_dir))
        topics_dir = data_dir / 'topics'

        def _count(base_dir: Path) -> dict:
            result = {}
            for dtype, fname in data_files.items():
                fp = base_dir / dtype / fname
                if fp.exists():
                    try:
                        with open(fp, 'r', encoding='utf-8') as f:
                            items = json.load(f)
                        result[dtype] = len(items)
                    except Exception:
                        result[dtype] = 0
                else:
                    result[dtype] = 0
            return result

        overview: list[dict] = []
        meta = manager.get_topic_metadata()
        active_topic = meta.get('topic', '') or ''
        from src.utils.prompt_manager import _slugify
        active_slug = _slugify(active_topic) if active_topic else ''
        overview.append({
            'slug': '',
            'topic': active_topic or 'Active (current)',
            'active': True,
            'counts': _count(data_dir),
        })
        if topics_dir.exists():
            for slug_dir in sorted(topics_dir.iterdir()):
                if slug_dir.is_dir() and slug_dir.name != active_slug:
                    topic_meta_path = manager.topics_dir / slug_dir.name / 'topic.json'
                    topic_name = slug_dir.name
                    if topic_meta_path.exists():
                        try:
                            with open(topic_meta_path, 'r', encoding='utf-8') as f:
                                tmeta = json.load(f)
                            topic_name = tmeta.get('topic', slug_dir.name)
                        except Exception:
                            pass
                    overview.append({
                        'slug': slug_dir.name,
                        'topic': topic_name,
                        'active': False,
                        'counts': _count(slug_dir),
                    })

        # Cache result for this user
        slot['_data_overview_cache'] = {'ts': now, 'data': overview}
        return jsonify({'overview': overview})

    @app.route('/api/data/raw/<data_type>')
    def get_raw_data(data_type: str):
        """Return the raw JSON array for a data type, optionally from an archived topic."""
        import re as _re
        valid_types = ('classification', 'dialog', 'general', 'rag', 'tool_calling')
        if data_type not in valid_types:
            return jsonify({'error': f'Invalid data_type. Must be one of {valid_types}'}), 400

        topic_slug = request.args.get('topic')  # optional: if given, read from archive

        manager = get_prompt_manager()
        data_files = manager._DATA_FILES
        filename = data_files.get(data_type)
        if not filename:
            return jsonify({'error': 'Unknown data type'}), 400

        if topic_slug:
            if not _re.match(r'^[\w]+$', topic_slug):
                return jsonify({'error': 'Invalid topic slug'}), 400
            file_path = Path(str(_get_user_context().data_dir)) / 'topics' / topic_slug / data_type / filename
        else:
            file_path = Path(str(_get_user_context().data_dir)) / data_type / filename

        # Try JSON first, fall back to CSV
        csv_path = file_path.with_suffix('.csv')
        source_format = 'json'  # track which format was loaded

        if not file_path.exists() and csv_path.exists():
            file_path = csv_path
            source_format = 'csv'
        elif not file_path.exists():
            return jsonify({'data': [], 'file': str(file_path), 'exists': False})

        try:
            if source_format == 'csv':
                import csv as _csv
                with open(file_path, 'r', encoding='utf-8-sig', newline='') as f:
                    items = list(_csv.DictReader(f))
            else:
                with open(file_path, 'r', encoding='utf-8') as f:
                    items = json.load(f)
            return jsonify({'data': items, 'count': len(items), 'exists': True, 'format': source_format})
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @app.route('/api/data/raw/<data_type>', methods=['PUT'])
    def save_raw_data(data_type: str):
        """Save the entire JSON array for a data type, optionally to an archived topic."""
        import re as _re
        valid_types = ('classification', 'dialog', 'general', 'rag', 'tool_calling')
        if data_type not in valid_types:
            return jsonify({'error': f'Invalid data_type. Must be one of {valid_types}'}), 400

        body = request.get_json()
        items = body.get('data')
        topic_slug = body.get('topic')  # optional

        if items is None or not isinstance(items, list):
            return jsonify({'error': 'data must be a JSON array'}), 400

        manager = get_prompt_manager()
        data_files = manager._DATA_FILES
        filename = data_files.get(data_type)
        if not filename:
            return jsonify({'error': 'Unknown data type'}), 400

        if topic_slug:
            if not _re.match(r'^[\w]+$', topic_slug):
                return jsonify({'error': 'Invalid topic slug'}), 400
            file_path = Path(str(_get_user_context().data_dir)) / 'topics' / topic_slug / data_type / filename
            file_path.parent.mkdir(parents=True, exist_ok=True)
        else:
            file_path = Path(str(_get_user_context().data_dir)) / data_type / filename
            file_path.parent.mkdir(parents=True, exist_ok=True)

        # Normalise to flat schema before persisting
        items = ensure_flat_schema(items, data_type)

        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(items, f, indent=2, ensure_ascii=False)
            # Invalidate data loader cache for active data
            if not topic_slug:
                dl = get_data_loader()
                dl.clear_cache()
            return jsonify({'status': 'saved', 'count': len(items)})
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    # =========================================================================
    # Import External Topic (file upload → archived topic with target model prompt generation)
    # =========================================================================

    @app.route('/api/topics/import', methods=['POST'])
    def import_topic():
        """Import an external topic from uploaded source prompt(s) + test data.

        Generates target-model prompts automatically and writes everything
        as an archived topic, ready to be activated from the UI.

        Multipart form fields:
            topic           (str, required)  — Human-readable topic name.
            source_model    (str, optional)  — Model key for the source prompts (default: gpt4).
            target_models   (str, optional)  — Comma-separated model keys to generate for.
                                               Default: all configured models except source.
            generator_model (str, optional)  — Model key for AI generation (default: gpt5).
            force           (str, optional)  — 'true' to overwrite existing.

            Source prompt files (at least one required):
            source_class_prompt  / gpt4_class_prompt   (file) — Classification system prompt.
            source_dialog_prompt / gpt4_dialog_prompt   (file) — Dialog system prompt.
            source_rag_prompt    / gpt4_rag_prompt      (file) — RAG system prompt.
            source_tool_prompt   / gpt4_tool_prompt     (file) — Tool-calling system prompt.

            Test data files (at least one required):
            class_test_data, dialog_test_data, general_test_data,
            rag_test_data, tool_calling_test_data  (file).
        """
        import re as _re
        import time as _time

        # ── Lazy-import the helper functions from tools/import_topic.py ──
        _tools_dir = Path(__file__).resolve().parent.parent.parent / 'tools'
        if str(_tools_dir) not in sys.path:
            sys.path.insert(0, str(_tools_dir))
        from import_topic import (
            _ensure_output_format,
            generate_target_prompt,
            _resolve_model_family,
            _fix_target_categories,
            _inject_missing_categories,
            _extract_json_fields_from_dialog,
            validate_and_fix_test_data,
            write_archived_topic,
            TASK_PROMPT_MAP,
            DATA_FILE_MAP,
        )
        from src.utils.prompt_manager import _slugify
        from src.utils.category_parser import extract_categories_from_prompt as _extract_categories_from_prompt

        topic_name = request.form.get('topic', '').strip()
        if not topic_name:
            return jsonify({'error': 'topic is required'}), 400

        source_model = request.form.get('source_model', 'gpt4')
        generator_model = request.form.get('generator_model', 'gpt5')
        force = request.form.get('force', 'false').lower() == 'true'
        slug = _slugify(topic_name)

        # Resolve target models
        target_models_raw = request.form.get('target_models', '').strip()
        if target_models_raw:
            target_models = [m.strip() for m in target_models_raw.split(',') if m.strip()]
        else:
            # Default: all configured models except the source
            all_models = list(app.config.get('SETTINGS', {}).get('azure', {}).get('models', {}).keys())
            target_models = [m for m in all_models if m != source_model]
            if not target_models:
                target_models = ['gpt5']  # fallback

        # Build model_family and model_deployment lookups from config
        model_families = {}
        model_deployments = {}
        for mk, mp in app.config.get('SETTINGS', {}).get('azure', {}).get('models', {}).items():
            if isinstance(mp, dict):
                model_families[mk] = mp.get('model_family', _resolve_model_family(mk))
                if mp.get('deployment'):
                    model_deployments[mk] = mp['deployment']

        # ── Collect uploaded prompt files (support both source_* and gpt4_* names) ──
        prompt_files = {}
        for task_key, field_names in (
            ('classification', ('source_class_prompt', 'gpt4_class_prompt')),
            ('dialog',         ('source_dialog_prompt', 'gpt4_dialog_prompt')),
            ('rag',            ('source_rag_prompt', 'gpt4_rag_prompt')),
            ('tool_calling',   ('source_tool_prompt', 'gpt4_tool_prompt')),
        ):
            for fn in field_names:
                f = request.files.get(fn)
                if f and f.filename:
                    prompt_files[task_key] = f.read().decode('utf-8')
                    break

        if not prompt_files:
            return jsonify({'error': 'At least one source prompt file is required.'}), 400

        # ── Collect uploaded test data files (JSON or CSV) ──
        import csv as _csv
        import io as _io

        data_files = {}
        for data_key, field_name in (('classification', 'class_test_data'),
                                      ('dialog', 'dialog_test_data'),
                                      ('general', 'general_test_data'),
                                      ('rag', 'rag_test_data'),
                                      ('tool_calling', 'tool_calling_test_data')):
            f = request.files.get(field_name)
            if f and f.filename:
                text = f.read().decode('utf-8-sig')
                if f.filename.lower().endswith('.csv'):
                    reader = _csv.DictReader(_io.StringIO(text))
                    raw = list(reader)
                else:
                    raw = json.loads(text)
                    if isinstance(raw, dict):
                        raw = raw.get('scenarios') or next((v for v in raw.values() if isinstance(v, list)), [])
                data_files[data_key] = raw

        if not data_files:
            return jsonify({'error': 'At least one test data file is required.'}), 400

        # ── Check existing ──
        topics_dir = _get_user_context().topics_dir / slug
        if topics_dir.exists() and not force:
            return jsonify({'error': f'Topic "{slug}" already exists. Enable "Overwrite" to replace it.'}), 409

        # ── Validate test data ──
        data_warnings = {}
        for data_task, items in data_files.items():
            warnings = validate_and_fix_test_data(items, data_task)
            if warnings:
                data_warnings[data_task] = warnings
                if any('obligatorios' in w for w in warnings):
                    return jsonify({'error': f'Critical schema errors in {data_task} test data: {"; ".join(warnings)}'}), 400

        # ── Get client ──
        client = get_client()
        if not client:
            return jsonify({'error': 'Azure OpenAI client not configured'}), 500

        # ── Validate all source prompts first (fast, no LLM) ──
        validated_prompts: dict[str, str] = {}
        for task, raw_content in prompt_files.items():
            app.logger.info(f'Import topic: validating {task} prompt ({len(raw_content)} chars)')
            validated_prompts[task] = _ensure_output_format(raw_content, task)

        # ── Extract cross-task domain context ──
        # 1) Categories from classification prompt (used for classification AND dialog)
        domain_categories: list[str] | None = None
        if 'classification' in validated_prompts:
            domain_categories = _extract_categories_from_prompt(validated_prompts['classification'])
            if domain_categories:
                app.logger.info(f'Import topic: extracted {len(domain_categories)} domain categories from classification prompt')

        # 2) Tool names from tool_calling test data (used for tool_calling prompt generation)
        domain_tools_summary: str | None = None
        if 'tool_calling' in data_files:
            tool_names: set[str] = set()
            for scenario in data_files['tool_calling']:
                raw_tools = scenario.get('available_tools', [])
                # available_tools may be a JSON-encoded string or a list
                if isinstance(raw_tools, str):
                    try:
                        raw_tools = json.loads(raw_tools)
                    except (json.JSONDecodeError, TypeError):
                        raw_tools = []
                if isinstance(raw_tools, list):
                    for tool in raw_tools:
                        if isinstance(tool, dict):
                            name = tool.get('function', {}).get('name') or tool.get('name', '')
                        elif isinstance(tool, str):
                            name = tool
                        else:
                            name = ''
                        if name:
                            tool_names.add(name)
            if tool_names:
                domain_tools_summary = ', '.join(sorted(tool_names))
                app.logger.info(f'Import topic: extracted {len(tool_names)} domain tools from test data: {domain_tools_summary}')

        # 3) JSON field names from dialog source prompt (used for dialog prompt generation)
        dialog_json_fields: list[str] | None = None
        if 'dialog' in validated_prompts:
            dialog_json_fields = _extract_json_fields_from_dialog(validated_prompts['dialog'])
            if dialog_json_fields:
                app.logger.info(f'Import topic: extracted {len(dialog_json_fields)} dialog JSON fields from source prompt: {dialog_json_fields}')

        # ── Generate target-model prompts in parallel ──

        def _gen_one(task: str, src_content: str, tgt_model: str):
            """Generate a single target-model prompt (runs in thread pool)."""
            family = model_families.get(tgt_model, _resolve_model_family(tgt_model))
            dep_name = model_deployments.get(tgt_model)
            app.logger.info(f'Import topic: [parallel] generating {tgt_model} {task} prompt...')
            t0 = _time.time()
            generated = generate_target_prompt(
                client, topic_name, task, src_content, generator_model,
                target_model=tgt_model, model_family=family,
                deployment_name=dep_name,
                domain_categories=domain_categories,
                domain_tools_summary=domain_tools_summary,
                dialog_json_fields=dialog_json_fields,
            )
            elapsed = round(_time.time() - t0, 1)
            app.logger.info(f'Import topic: [parallel] {tgt_model} {task} prompt generated in {elapsed}s')
            return task, tgt_model, generated, elapsed

        t_total = _time.time()
        # Run generation in parallel using threads (compatible with Flask test client on Windows)
        from concurrent.futures import ThreadPoolExecutor, as_completed
        tasks_to_run = [
            (task, content, tgt)
            for task, content in validated_prompts.items()
            for tgt in target_models
        ]
        n_workers = max(len(tasks_to_run), 1)
        results = []
        failed = []
        with ThreadPoolExecutor(max_workers=n_workers) as pool:
            future_map = {
                pool.submit(_gen_one, task, content, tgt): (task, tgt)
                for task, content, tgt in tasks_to_run
            }
            for future in as_completed(future_map):
                task_key, tgt_key = future_map[future]
                try:
                    results.append(future.result())
                except Exception as exc:
                    app.logger.error(
                        f'Import topic: generation failed for {tgt_key} {task_key}: {exc}'
                    )
                    failed.append((task_key, tgt_key, str(exc)))
        if failed:
            app.logger.warning(
                f'Import topic: {len(failed)}/{len(tasks_to_run)} generations failed — '
                f'continuing with {len(results)} successful ones'
            )
        app.logger.info(
            f'Import topic: all target prompts generated in {round(_time.time() - t_total, 1)}s (parallel)'
        )

        # Build prompts_map: {task: {model: content}}
        prompts_map: dict[str, dict[str, str]] = {}
        gen_times: dict[str, float] = {}
        for task, tgt_model, tgt_content, elapsed in results:
            prompts_map.setdefault(task, {})[tgt_model] = tgt_content
            gen_times[f'{task}:{tgt_model}'] = elapsed
        # Include source model content
        for task, content in validated_prompts.items():
            prompts_map.setdefault(task, {})[source_model] = content

        # ── Post-generation: category alignment for classification prompts ──
        if domain_categories and 'classification' in prompts_map:
            source_cls_content = validated_prompts.get('classification', '')
            for tgt_model, tgt_content in list(prompts_map['classification'].items()):
                if tgt_model == source_model:
                    continue
                # Step 1: Deterministic injection of any missing categories
                patched = _inject_missing_categories(
                    tgt_content, source_cls_content, domain_categories,
                )
                if patched != tgt_content:
                    prompts_map['classification'][tgt_model] = patched
                    app.logger.info(
                        f'Import topic: {tgt_model} classification — '
                        f'deterministic category injection applied'
                    )
                    tgt_content = patched

                # Step 2: If still <80% overlap, try LLM-based fix as fallback
                tgt_cats = _extract_categories_from_prompt(tgt_content)
                if tgt_cats:
                    source_set = set(c.lower() for c in domain_categories)
                    target_set = set(c.lower() for c in tgt_cats)
                    overlap = len(source_set & target_set) / max(len(source_set), 1)
                    if overlap < 0.80:
                        app.logger.warning(
                            f'Import topic: {tgt_model} classification has {overlap:.0%} category overlap '
                            f'({len(source_set & target_set)}/{len(source_set)}) after injection — LLM auto-fixing...'
                        )
                        fixed = _fix_target_categories(
                            client, tgt_content, domain_categories,
                            tgt_model, generator_model,
                        )
                        if fixed:
                            prompts_map['classification'][tgt_model] = fixed
                            app.logger.info(f'Import topic: {tgt_model} classification categories realigned via LLM.')
                    else:
                        app.logger.info(
                            f'Import topic: {tgt_model} classification category alignment OK ({overlap:.0%})'
                        )

        # ── Write archived topic ──
        app.logger.info(f'Import topic: writing archived topic "{slug}"…')
        uctx = _get_user_context()
        write_archived_topic(
            slug=slug,
            topic_name=topic_name,
            prompts_map=prompts_map,
            test_data_map=data_files,
            prompts_topics_dir=uctx.topics_dir,
            data_topics_dir=uctx.data_topics_dir,
        )

        # ── Invalidate caches ──
        loader = get_prompt_loader()
        loader._cache.clear()
        dl = get_data_loader()
        dl.clear_cache()

        # ── Build response summary ──
        prompt_summary = {}
        for task, models_content in prompts_map.items():
            prompt_type = TASK_PROMPT_MAP.get(task, task)
            prompt_summary[task] = {
                'prompt_type': prompt_type,
                'models': {m: len(c) for m, c in models_content.items()},
                'generation_times': {
                    m: gen_times.get(f'{task}:{m}', 0)
                    for m in models_content if m != source_model
                },
            }

        data_summary = {}
        for data_task, items in data_files.items():
            data_summary[data_task] = {
                'count': len(items),
                'warnings': data_warnings.get(data_task, []),
            }

        return jsonify({
            'status': 'imported',
            'topic': topic_name,
            'slug': slug,
            'source_model': source_model,
            'target_models': target_models,
            'prompts': prompt_summary,
            'data': data_summary,
        })

    # =========================================================================
    # Topic Management API (list, switch, delete archived topics)
    # =========================================================================

    @app.route('/api/topics')
    def list_topics():
        """List all available topics (archived + active)."""
        try:
            manager = get_prompt_manager()
            topics = manager.list_topics()
            return jsonify({'topics': topics})
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @app.route('/api/topics/lock-status')
    def topic_lock_status():
        """Return whether the topic can be switched (no running jobs)."""
        with _active_jobs_lock:
            running = _active_jobs_count
            switching = _topic_switching
        return jsonify({
            'locked': running > 0 or switching,
            'active_jobs': running,
            'topic_switching': switching,
        })

    @app.route('/api/topics/activate', methods=['POST'])
    def activate_topic():
        """Switch to a previously archived topic."""
        nonlocal _topic_switching
        data = request.get_json()
        slug = data.get('slug', '')
        if not slug:
            return jsonify({'error': 'slug is required'}), 400
        try:
            _check_topic_switchable()
            with _active_jobs_lock:
                _topic_switching = True
            manager = get_prompt_manager()
            meta = manager.activate_topic(slug)
            # Invalidate per-user caches so new content is picked up
            uid = session.get('user_id')
            if uid:
                _invalidate_user(uid)
            return jsonify({'status': 'activated', 'topic': meta})
        except RuntimeError as e:
            return jsonify({'error': str(e)}), 409
        except FileNotFoundError as e:
            return jsonify({'error': str(e)}), 404
        except Exception as e:
            return jsonify({'error': str(e)}), 500
        finally:
            with _active_jobs_lock:
                _topic_switching = False

    @app.route('/api/topics/archive', methods=['POST'])
    def archive_topic():
        """Manually archive the current active topic."""
        try:
            manager = get_prompt_manager()
            slug = manager.archive_current_topic()
            if slug is None:
                return jsonify({'error': 'No active topic to archive'}), 400
            return jsonify({'status': 'archived', 'slug': slug})
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @app.route('/api/topics/<slug>', methods=['DELETE'])
    def delete_topic(slug: str):
        """Delete an archived topic."""
        import re as _re
        if not _re.match(r'^[\w]+$', slug):
            return jsonify({'error': 'Invalid slug'}), 400
        try:
            manager = get_prompt_manager()
            removed = manager.delete_topic(slug)
            if not removed:
                return jsonify({'error': 'Topic not found'}), 404
            return jsonify({'status': 'deleted', 'slug': slug})
        except ValueError as e:
            return jsonify({'error': str(e)}), 400
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    # =========================================================================
    # Foundry Control Plane Evaluation API
    # =========================================================================

    @app.route('/api/foundry/status')
    def foundry_status():
        """Check if Foundry evaluation is available and configured."""
        sdk_available = is_foundry_available()
        evaluator = get_foundry_evaluator() if sdk_available else None
        return jsonify({
            'sdk_installed': sdk_available,
            'configured': evaluator is not None,
            'message': (
                'Ready' if evaluator
                else 'SDK not installed — pip install azure-ai-projects>=2.0.0b2'
                if not sdk_available
                else 'Set foundry.project_endpoint and foundry.judge_deployment in settings.yaml'
            ),
        })

    @app.route('/api/foundry/submit', methods=['POST'])
    def foundry_submit():
        """Submit a completed local evaluation to Foundry Control Plane.

        Expects JSON body:
          - result_filename: filename of a saved result in data/results/
          OR
          - raw_results: list of raw result dicts
          - evaluation_type: 'classification' | 'dialog' | 'general'
          - model_name: e.g. 'gpt4'
        """
        data = request.get_json()
        run_id = _normalize_run_id(data.get('run_id') if data else None)
        _cleanup_run_logs()
        token = _current_run_id.set(run_id)

        fe = get_foundry_evaluator()
        if fe is None:
            _current_run_id.reset(token)
            return jsonify({'error': 'Foundry evaluation not available. Check /api/foundry/status for details.', 'run_id': run_id}), 503

        # Option 1: reference an already-saved result file
        result_filename = data.get('result_filename')
        if result_filename:
            safe_name = Path(result_filename).name
            file_path = Path(str(_get_user_context().results_dir)) / safe_name
            if not file_path.exists():
                _current_run_id.reset(token)
                return jsonify({'error': f'Result file not found: {safe_name}', 'run_id': run_id}), 404
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    result_data = json.load(f)
                raw_results = result_data.get('raw_results', [])
                evaluation_type = result_data.get('evaluation_type', 'classification')
                model_name = result_data.get('model_name', 'unknown')
            except Exception as e:
                _current_run_id.reset(token)
                return jsonify({'error': f'Failed to load result file: {e}', 'run_id': run_id}), 500
        else:
            raw_results = data.get('raw_results', [])
            evaluation_type = data.get('evaluation_type', 'classification')
            model_name = data.get('model_name', 'unknown')

        if not raw_results:
            _current_run_id.reset(token)
            return jsonify({'error': 'No raw_results to evaluate', 'run_id': run_id}), 400

        try:
            result = fe.submit_evaluation(
                raw_results=raw_results,
                evaluation_type=evaluation_type,
                model_name=model_name,
                poll=True,
            )

            # Persist Foundry scores alongside the local result file
            if result_filename and result.get('foundry_scores'):
                try:
                    safe = Path(result_filename).name
                    fpath = Path(str(_get_user_context().results_dir)) / safe
                    if fpath.exists():
                        with open(fpath, 'r', encoding='utf-8') as f:
                            saved = json.load(f)
                        saved['foundry_scores'] = result['foundry_scores']
                        saved['foundry_eval_id'] = result.get('eval_id')
                        saved['foundry_run_id'] = result.get('run_id')
                        saved['foundry_report_url'] = result.get('report_url')
                        tmp_path = fpath.with_suffix('.json.tmp')
                        with open(tmp_path, 'w', encoding='utf-8') as f:
                            json.dump(saved, f, ensure_ascii=False, indent=2)
                        tmp_path.replace(fpath)
                        app.logger.info(f'Foundry scores persisted to {safe}')
                except Exception as e:
                    app.logger.warning(f'Failed to persist Foundry scores: {e}')

            result['run_id'] = run_id
            return jsonify(result)
        except Exception as e:
            app.logger.error(f'Foundry submission failed: {e}')
            return jsonify({'error': str(e), 'run_id': run_id}), 500
        finally:
            _current_run_id.reset(token)

    @app.route('/api/foundry/results', methods=['POST'])
    def foundry_results():
        """Retrieve Foundry scores for a previously completed run.

        Expects JSON body:
          - eval_id: Foundry evaluation ID
          - run_id: Foundry run ID
        """
        fe = get_foundry_evaluator()
        if fe is None:
            return jsonify({'error': 'Foundry evaluation not available.'}), 503

        data = request.get_json()
        eval_id = data.get('eval_id')
        run_id = data.get('run_id')
        if not eval_id or not run_id:
            return jsonify({'error': 'eval_id and run_id are required'}), 400

        try:
            scores = fe.retrieve_results(eval_id=eval_id, run_id=run_id)
            return jsonify(scores)
        except Exception as e:
            app.logger.error(f'Foundry results retrieval failed: {e}')
            return jsonify({'error': str(e)}), 500

    # =========================================================================
    # Helper: cost estimation
    # =========================================================================

    def _estimate_cost(model_name: str, metrics) -> dict:
        """Return cost estimate in USD for a single request."""
        # Use centralized cost_rates from settings.yaml (via MetricsCalculator)
        mc = get_metrics_calc()
        r = mc.get_cost_rates(model_name)
        pt = getattr(metrics, 'prompt_tokens', 0) or 0
        ct = getattr(metrics, 'cached_tokens', 0) or 0
        comp = getattr(metrics, 'completion_tokens', 0) or 0
        rt = getattr(metrics, 'reasoning_tokens', 0) or 0
        uncached = pt - ct
        cost = (
            (uncached / 1000) * r.get('input', 0.0025)
            + (ct / 1000) * r.get('cached_input', 0.00125)
            + ((comp - rt) / 1000) * r.get('output', 0.01)
            + (rt / 1000) * r.get('reasoning', r.get('output', 0.01))
        )
        return {'usd': round(cost, 6), 'breakdown': {
            'input_tokens': uncached, 'cached_tokens': ct,
            'output_tokens': comp - rt, 'reasoning_tokens': rt,
        }}

    # =========================================================================
    # Prompt Health API
    # =========================================================================

    @app.route('/api/prompts/health/<model>/<prompt_type>')
    def prompt_health(model: str, prompt_type: str):
        """Analyse a prompt's health: token count, format, cacheability."""
        try:
            loader = get_prompt_loader()
            content = loader.load_prompt(model, prompt_type)
        except FileNotFoundError:
            return jsonify({'error': 'Prompt not found'}), 404

        import tiktoken
        try:
            enc = tiktoken.encoding_for_model('gpt-4o')
        except Exception:
            enc = tiktoken.get_encoding('cl100k_base')

        tokens = enc.encode(content)
        token_count = len(tokens)
        char_count = len(content)
        line_count = content.count('\n') + 1

        # Format detection
        has_json_schema = '```json' in content.lower() or '"type"' in content
        has_yaml = '```yaml' in content.lower()
        has_xml = '</' in content
        fmt = 'json' if has_json_schema else 'yaml' if has_yaml else 'xml' if has_xml else 'markdown'

        # Cacheability (Azure OpenAI needs >= 1024 token prefix)
        cacheable = token_count >= 1024

        # Drift indicators
        warnings = []
        if token_count > 4000:
            warnings.append('Prompt exceeds 4K tokens - consider trimming for cost/latency')
        if token_count < 200:
            warnings.append('Very short prompt - may lack sufficient instructions')
        if content.count('```') % 2 != 0:
            warnings.append('Unmatched code fence detected')

        return jsonify({
            'model': model,
            'prompt_type': prompt_type,
            'token_count': token_count,
            'char_count': char_count,
            'line_count': line_count,
            'format': fmt,
            'cacheable': cacheable,
            'cache_benefit': 'Up to 50% cost reduction' if cacheable else 'Below 1024-token threshold',
            'warnings': warnings,
        })

    return app


# For running directly
if __name__ == '__main__':
    app = create_app()
    app.run(debug=True, port=5000)
