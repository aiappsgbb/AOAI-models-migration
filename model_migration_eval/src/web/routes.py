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

from flask import Flask, render_template, request, jsonify
from flask_cors import CORS

from ..clients.azure_openai import create_client_from_config
from ..utils.prompt_loader import PromptLoader
from ..utils.prompt_manager import PromptManager
from ..utils.data_loader import DataLoader
from ..evaluation.metrics import MetricsCalculator
from ..evaluation.evaluator import ModelEvaluator
from ..evaluation.comparator import ModelComparator
from ..evaluation.foundry_evaluator import (
    is_foundry_available,
    create_foundry_evaluator_from_config,
)


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
    
    # Always reload templates from disk (even without debug mode)
    app.config['TEMPLATES_AUTO_RELOAD'] = True
    
    # Store configuration
    app.config['CONFIG_PATH'] = config_path or 'config/settings.yaml'
    app.config['DATA_DIR'] = 'data/synthetic'
    app.config['RESULTS_DIR'] = 'data/results'
    
    # Lazy-loaded client
    _client = None
    _evaluator = None
    _comparator = None
    _data_loader = None
    _prompt_loader = None
    _prompt_manager = None
    _metrics_calc = None
    _foundry_evaluator = None
    _foundry_checked = False

    # In-memory backend log streaming buffers (Option B)
    _run_logs = {}
    _run_logs_lock = threading.Lock()
    _run_logs_max_lines = 2000
    _run_logs_ttl_sec = 3600
    _current_run_id = contextvars.ContextVar("current_run_id", default=None)

    # Async comparison jobs storage
    _compare_jobs = {}
    _compare_jobs_lock = threading.Lock()

    # Async generate jobs storage
    _generate_jobs = {}
    _generate_jobs_lock = threading.Lock()

    # Async regenerate jobs storage
    _regenerate_jobs = {}
    _regenerate_jobs_lock = threading.Lock()

    class _RunLogCaptureHandler(logging.Handler):
        """Capture log records for the active run_id context into memory."""

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
            with _run_logs_lock:
                buff = _run_logs.setdefault(run_id, {
                    'entries': [],
                    'created_at': now,
                    'last_access': now,
                })
                buff['entries'].append(entry)
                buff['last_access'] = now
                if len(buff['entries']) > _run_logs_max_lines:
                    buff['entries'] = buff['entries'][-_run_logs_max_lines:]

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
        # Also clean up old async jobs (compare, generate, regenerate)
        for lock, store in [
            (_compare_jobs_lock, _compare_jobs),
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
    
    def get_client():
        nonlocal _client
        if _client is None:
            try:
                _client = create_client_from_config(app.config['CONFIG_PATH'])
            except Exception as e:
                app.logger.error(f"Failed to create client: {e}")
                return None
        return _client
    
    def get_data_loader():
        nonlocal _data_loader
        if _data_loader is None:
            _data_loader = DataLoader(app.config['DATA_DIR'])
        return _data_loader
    
    def get_prompt_loader():
        nonlocal _prompt_loader
        if _prompt_loader is None:
            _prompt_loader = PromptLoader()
        return _prompt_loader
    
    def get_prompt_manager():
        nonlocal _prompt_manager
        if _prompt_manager is None:
            _prompt_manager = PromptManager(data_dir=app.config['DATA_DIR'])
        return _prompt_manager
    
    def get_metrics_calc():
        nonlocal _metrics_calc
        if _metrics_calc is None:
            _metrics_calc = MetricsCalculator()
        return _metrics_calc
    
    def _load_perf_settings() -> dict:
        """Read performance settings from config file."""
        try:
            import yaml as _yaml
            with open(app.config['CONFIG_PATH'], 'r') as f:
                cfg = _yaml.safe_load(f)
            return cfg.get('evaluation', {})
        except Exception:
            return {}

    def get_evaluator():
        nonlocal _evaluator
        client = get_client()
        if client and _evaluator is None:
            perf = _load_perf_settings()
            _evaluator = ModelEvaluator(
                client,
                max_concurrent=perf.get('max_concurrent_requests', 5),
            )
        return _evaluator
        
    def get_comparator():
        nonlocal _comparator
        client = get_client()
        if client and _comparator is None:
            perf = _load_perf_settings()
            _comparator = ModelComparator(
                client,
                evaluator=get_evaluator(),
                foundry_evaluator=get_foundry_evaluator(),
                parallel_models=perf.get('parallel_models', True),
            )
        elif _comparator is not None:
            # Keep Foundry evaluator refreshed/config-aware
            _comparator.foundry_evaluator = get_foundry_evaluator()
        return _comparator

    def get_foundry_evaluator():
        """Lazy-load the Foundry evaluator from settings.yaml config."""
        nonlocal _foundry_evaluator, _foundry_checked
        if _foundry_checked:
            return _foundry_evaluator
        _foundry_checked = True
        if not is_foundry_available():
            return None
        try:
            import yaml as _yaml
            with open(app.config['CONFIG_PATH'], 'r') as f:
                cfg = _yaml.safe_load(f)
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
        with _run_logs_lock:
            buff = _run_logs.get(rid, {'entries': [], 'last_access': time.time()})
            entries = buff.get('entries', [])
            next_offset = len(entries)
            slice_entries = entries[offset:]
            buff['last_access'] = time.time()
            _run_logs[rid] = buff

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
            models.append({
                'name': name,
                'deployment': config.deployment_name,
                'version': config.model_version,
                'max_tokens': config.max_tokens
            })
        return jsonify({'models': models})
        
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
                        'scenario': s.scenario,
                        'customer_input': s.customer_input,
                        'expected_category': s.expected_category,
                        'expected_priority': s.expected_priority
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
                        'scenario': s.scenario,
                        'category': s.category,
                        'context_gaps': s.context_gaps
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
                        'scenario': t.prompt[:80] if t.prompt else '-',
                        'category': t.test_type,
                        'complexity': t.complexity
                    }
                    for t in tests
                ]
            })
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
        
        if evaluation_type not in ('classification', 'dialog'):
            _current_run_id.reset(token)
            return jsonify({'error': f"Invalid evaluation type '{evaluation_type}'. Must be 'classification' or 'dialog'."}), 400

        if not customer_input:
            _current_run_id.reset(token)
            return jsonify({'error': 'customer_input is required'}), 400
            
        client = get_client()
        if not client:
            _current_run_id.reset(token)
            return jsonify({'error': 'Client not configured'}), 500
            
        prompt_loader = get_prompt_loader()
        metrics_calc = get_metrics_calc()
        results = {}
        
        for model_name in models_to_test:
            if model_name not in client.models:
                results[model_name] = {'error': f'Model {model_name} not configured'}
                continue
                
            try:
                if evaluation_type == 'classification':
                    messages = prompt_loader.load_classification_prompt(
                        model=model_name,
                        customer_message=customer_input
                    )
                    response_format = {"type": "json_object"}
                else:
                    messages = prompt_loader.load_dialog_prompt(
                        model=model_name,
                        conversation=[{'role': 'customer', 'message': customer_input}]
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
                    
                results[model_name] = result
                
            except Exception as e:
                results[model_name] = {'error': str(e)}
                
        try:
            return jsonify(results)
        finally:
            _current_run_id.reset(token)
        
    @app.route('/api/evaluate/batch', methods=['POST'])
    def evaluate_batch():
        """Run batch evaluation on test scenarios"""
        data = request.get_json() or {}
        run_id = _normalize_run_id(data.get('run_id') if data else None)
        _cleanup_run_logs()
        token = _current_run_id.set(run_id)
        model_name = data.get('model', 'gpt4')
        evaluation_type = data.get('type', 'classification')
        limit = min(data.get('limit', 10), 100)  # Cap at 100 to prevent abuse
        
        evaluator = get_evaluator()
        if not evaluator:
            _current_run_id.reset(token)
            return jsonify({'error': 'Evaluator not available', 'run_id': run_id}), 500
            
        try:
            loader = get_data_loader()
            if evaluation_type == 'classification':
                scenarios = loader.load_classification_scenarios()[:limit]
                result = evaluator.evaluate_classification(model_name, scenarios)
            elif evaluation_type == 'dialog':
                scenarios = loader.load_dialog_scenarios()[:limit]
                result = evaluator.evaluate_dialog(model_name, scenarios)
            else:
                scenarios = loader.load_general_tests()[:limit]
                result = evaluator.evaluate_general(model_name, scenarios)

            # Auto-save results to disk
            try:
                result.save(app.config['RESULTS_DIR'])
                app.logger.info(f"Auto-saved {evaluation_type} result for {model_name}")
            except Exception as save_err:
                app.logger.warning(f"Failed to auto-save result: {save_err}")

            result_dict = result.to_dict()
            # Include the saved filename so the UI can reference it
            ts = result.timestamp.replace(':', '-')
            result_dict['saved_filename'] = f"{model_name}_{evaluation_type}_{ts}.json"
            result_dict['run_id'] = run_id
            return jsonify(result_dict)
            
        except Exception as e:
            return jsonify({'error': str(e), 'run_id': run_id}), 500
        finally:
            _current_run_id.reset(token)
            
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

        comparator = get_comparator()
        if not comparator:
            return jsonify({'error': 'Comparator not available', 'run_id': run_id}), 500

        results_dir = app.config['RESULTS_DIR']

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
        if not topic:
            return jsonify({'error': 'topic is required'}), 400

        client = get_client()
        if not client:
            return jsonify({'error': 'Client not configured'}), 500

        # Register job as running
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
                manager = get_prompt_manager()
                results = manager.generate_prompts(
                    topic=topic,
                    client=client,
                    generator_model=generator_model,
                    data_dir=app.config['DATA_DIR'],
                )
                # Invalidate caches so new content is picked up
                loader = get_prompt_loader()
                loader._cache.clear()
                dl = get_data_loader()
                dl.clear_cache()
                payload = {
                    'status': 'generated',
                    'topic': topic,
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

        client = get_client()
        if not client:
            return jsonify({'error': 'Client not configured'}), 500

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
                manager = get_prompt_manager()
                result = manager.regenerate_test_data(
                    client=client,
                    generator_model=generator_model,
                    data_dir=app.config['DATA_DIR'],
                    topic=topic,
                )
                if 'error' in result and isinstance(result.get('error'), str):
                    with _regenerate_jobs_lock:
                        _regenerate_jobs[run_id]['status'] = 'failed'
                        _regenerate_jobs[run_id]['error'] = result['error']
                    return
                # Invalidate data loader cache
                dl = get_data_loader()
                dl.clear_cache()
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
        """List saved evaluation results"""
        results_dir = Path(app.config['RESULTS_DIR'])
        if not results_dir.exists():
            return jsonify({'results': []})
            
        results = []
        for file in results_dir.glob('*.json'):
            try:
                with open(file, encoding='utf-8') as f:
                    data = json.load(f)
                model_display = data.get('model_name', 'unknown')
                if model_display == 'unknown' and 'model_a' in data:
                    model_display = f"{data['model_a']} vs {data.get('model_b', '?')}"
                results.append({
                    'filename': file.name,
                    'model': model_display,
                    'type': data.get('evaluation_type', 'unknown'),
                    'timestamp': data.get('timestamp', '')
                })
            except (json.JSONDecodeError, OSError) as e:
                app.logger.debug(f"Skipping unreadable result file {file.name}: {e}")
                continue
                
        return jsonify({'results': sorted(results, key=lambda x: x['timestamp'], reverse=True)})
        
    @app.route('/api/results/<filename>', methods=['DELETE'])
    def delete_result(filename: str):
        """Delete a saved result file"""
        safe_name = Path(filename).name
        if safe_name != filename or '..' in filename:
            return jsonify({'error': 'Invalid filename'}), 400
        results_dir = Path(app.config['RESULTS_DIR'])
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
            
        results_dir = Path(app.config['RESULTS_DIR'])
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
        """Return counts per data-type for the active set and every archived topic."""
        manager = get_prompt_manager()
        data_files = manager._DATA_FILES          # classification, dialog, general
        data_dir = Path(app.config['DATA_DIR'])
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
        # Active
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
        # Archived (skip the currently active topic to avoid duplicates)
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

        return jsonify({'overview': overview})

    @app.route('/api/data/raw/<data_type>')
    def get_raw_data(data_type: str):
        """Return the raw JSON array for a data type, optionally from an archived topic."""
        import re as _re
        valid_types = ('classification', 'dialog', 'general')
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
            file_path = Path(app.config['DATA_DIR']) / 'topics' / topic_slug / data_type / filename
        else:
            file_path = Path(app.config['DATA_DIR']) / data_type / filename

        if not file_path.exists():
            return jsonify({'data': [], 'file': str(file_path), 'exists': False})

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                items = json.load(f)
            return jsonify({'data': items, 'count': len(items), 'exists': True})
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @app.route('/api/data/raw/<data_type>', methods=['PUT'])
    def save_raw_data(data_type: str):
        """Save the entire JSON array for a data type, optionally to an archived topic."""
        import re as _re
        valid_types = ('classification', 'dialog', 'general')
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
            file_path = Path(app.config['DATA_DIR']) / 'topics' / topic_slug / data_type / filename
            file_path.parent.mkdir(parents=True, exist_ok=True)
        else:
            file_path = Path(app.config['DATA_DIR']) / data_type / filename
            file_path.parent.mkdir(parents=True, exist_ok=True)

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
    # Import External Topic (file upload → archived topic with GPT-5 generation)
    # =========================================================================

    @app.route('/api/topics/import', methods=['POST'])
    def import_topic():
        """Import an external topic from uploaded GPT-4 prompt(s) + test data
        files.  Generates GPT-5 prompt(s) automatically and writes everything
        as an archived topic, ready to be activated from the UI.

        Multipart form fields:
            topic           (str, required) — Human-readable topic name.
            generator_model (str, optional) — Model key for GPT-5 generation (default: gpt5).
            force           (str, optional) — 'true' to overwrite existing.
            gpt4_class_prompt  (file, optional) — Classification system prompt (.txt/.md).
            gpt4_dialog_prompt (file, optional) — Dialog system prompt (.txt/.md).
            class_test_data    (file, optional) — Classification scenarios JSON.
            dialog_test_data   (file, optional) — Dialog scenarios JSON.
            general_test_data  (file, optional) — General capability tests JSON.

        At least one prompt file and one test data file are required.
        """
        import re as _re
        import time as _time

        # ── Lazy-import the helper functions from tools/import_topic.py ──
        _tools_dir = Path(__file__).resolve().parent.parent.parent / 'tools'
        if str(_tools_dir) not in sys.path:
            sys.path.insert(0, str(_tools_dir))
        from import_topic import (
            _ensure_output_format,
            generate_gpt5_prompt,
            validate_and_fix_test_data,
            write_archived_topic,
            TASK_PROMPT_MAP,
            DATA_FILE_MAP,
        )
        from src.utils.prompt_manager import _slugify

        topic_name = request.form.get('topic', '').strip()
        if not topic_name:
            return jsonify({'error': 'topic is required'}), 400

        generator_model = request.form.get('generator_model', 'gpt5')
        force = request.form.get('force', 'false').lower() == 'true'
        slug = _slugify(topic_name)

        # ── Collect uploaded prompt files ──
        prompt_files = {}
        for task_key, field_name in (('classification', 'gpt4_class_prompt'),
                                      ('dialog', 'gpt4_dialog_prompt')):
            f = request.files.get(field_name)
            if f and f.filename:
                prompt_files[task_key] = f.read().decode('utf-8')

        if not prompt_files:
            return jsonify({'error': 'At least one GPT-4 prompt file is required (gpt4_class_prompt and/or gpt4_dialog_prompt).'}), 400

        # ── Collect uploaded test data files ──
        data_files = {}
        for data_key, field_name in (('classification', 'class_test_data'),
                                      ('dialog', 'dialog_test_data'),
                                      ('general', 'general_test_data')):
            f = request.files.get(field_name)
            if f and f.filename:
                raw = json.loads(f.read().decode('utf-8-sig'))
                if isinstance(raw, dict):
                    raw = raw.get('scenarios') or next((v for v in raw.values() if isinstance(v, list)), [])
                data_files[data_key] = raw

        if not data_files:
            return jsonify({'error': 'At least one test data file is required (class_test_data, dialog_test_data, general_test_data).'}), 400

        # ── Check existing ──
        topics_dir = Path('prompts') / 'topics' / slug
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

        # ── Process each prompt: validate + generate GPT-5 ──
        prompts_map = {}
        gen_times = {}
        for task, gpt4_raw in prompt_files.items():
            app.logger.info(f'Import topic: validating {task} prompt ({len(gpt4_raw)} chars)')
            gpt4_content = _ensure_output_format(gpt4_raw, task)

            app.logger.info(f'Import topic: generating GPT-5 {task} prompt…')
            t0 = _time.time()
            gpt5_content = generate_gpt5_prompt(
                client, topic_name, task, gpt4_content, generator_model,
            )
            gen_times[task] = round(_time.time() - t0, 1)
            app.logger.info(f'Import topic: GPT-5 {task} prompt generated in {gen_times[task]}s')
            prompts_map[task] = (gpt4_content, gpt5_content)

        # ── Write archived topic ──
        app.logger.info(f'Import topic: writing archived topic "{slug}"…')
        write_archived_topic(
            slug=slug,
            topic_name=topic_name,
            prompts_map=prompts_map,
            test_data_map=data_files,
        )

        # ── Invalidate caches ──
        loader = get_prompt_loader()
        loader._cache.clear()
        dl = get_data_loader()
        dl.clear_cache()

        # ── Build response summary ──
        prompt_summary = {}
        for task, (g4, g5) in prompts_map.items():
            prompt_type = TASK_PROMPT_MAP.get(task, task)
            prompt_summary[task] = {
                'prompt_type': prompt_type,
                'gpt4_chars': len(g4),
                'gpt5_chars': len(g5),
                'generation_time': gen_times.get(task, 0),
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

    @app.route('/api/topics/activate', methods=['POST'])
    def activate_topic():
        """Switch to a previously archived topic."""
        nonlocal _evaluator, _comparator
        data = request.get_json()
        slug = data.get('slug', '')
        if not slug:
            return jsonify({'error': 'slug is required'}), 400
        try:
            manager = get_prompt_manager()
            meta = manager.activate_topic(slug)
            # Invalidate ALL caches so new content is picked up
            loader = get_prompt_loader()
            loader._cache.clear()
            dl = get_data_loader()
            dl.clear_cache()
            # Evaluator and comparator hold references to old prompt/data loaders
            _evaluator = None
            _comparator = None
            return jsonify({'status': 'activated', 'topic': meta})
        except FileNotFoundError as e:
            return jsonify({'error': str(e)}), 404
        except Exception as e:
            return jsonify({'error': str(e)}), 500

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
            file_path = Path(app.config['RESULTS_DIR']) / safe_name
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
                timeout=300,
            )

            # Persist Foundry scores alongside the local result file
            if result_filename and result.get('foundry_scores'):
                try:
                    safe = Path(result_filename).name
                    fpath = Path(app.config['RESULTS_DIR']) / safe
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
        rates = {
            'gpt4': {'input': 0.0025, 'output': 0.01, 'cached_input': 0.00125},
            'gpt5': {'input': 0.005, 'output': 0.02, 'cached_input': 0.0025, 'reasoning': 0.015},
        }
        r = rates.get(model_name, rates.get('gpt4', {}))
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
