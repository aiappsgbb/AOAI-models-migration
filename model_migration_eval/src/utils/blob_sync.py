"""
BlobSyncService — Transparent persistence of user data to Azure Blob Storage.

This module syncs the per-user filesystem tree (``data/users/``) and the
auth database (``data/auth.db``) to an Azure Blob Storage container so that
data survives container restarts, scale-to-zero events, and redeployments.

Architecture:
    - On startup: download everything from Blob → local filesystem
    - On write: write-through (local + Blob)
    - Periodic: incremental sync every N seconds (safety net)

Authentication uses ``DefaultAzureCredential`` (Managed Identity in Azure,
``az login`` locally) — no storage account keys needed.
"""

import io
import os
import logging
import threading
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Lazy import to avoid hard dependency when running locally without blob
_blob_service_client = None
_container_client = None
_init_checked = False        # True once we've attempted init (avoids repeated log spam)
_init_lock = threading.Lock()

# Default configuration
_CONTAINER_NAME = "userdata"
_USERS_PREFIX = "users/"
_AUTH_DB_BLOB = "auth.db"
_LOCAL_USERS_DIR = Path("data/users")
_LOCAL_AUTH_DB = Path("data/auth.db")

# Background sync interval (seconds)
_SYNC_INTERVAL = 120  # 2 minutes

# Startup init retries (tolerate RBAC propagation delay)
_INIT_RETRIES = 5     # max attempts
_INIT_BACKOFF = 10    # base seconds (exponential: 10, 20, 40, 80, 160)


def _get_container_client():
    """Lazily initialise the Blob container client (singleton).

    Retries up to ``_INIT_RETRIES`` times with exponential back-off so
    that newly-assigned RBAC roles have time to propagate (can take 1-5
    minutes in Azure).
    """
    global _blob_service_client, _container_client, _init_checked
    if _container_client is not None:
        return _container_client
    if _init_checked:
        return None          # already tried — don't log again

    with _init_lock:
        if _container_client is not None:
            return _container_client
        if _init_checked:
            return None

        account_name = os.environ.get("AZURE_STORAGE_ACCOUNT_NAME", "")
        if not account_name:
            logger.info("AZURE_STORAGE_ACCOUNT_NAME not set -> blob sync disabled")
            _init_checked = True
            return None

        try:
            from azure.identity import DefaultAzureCredential
            from azure.storage.blob import BlobServiceClient

            account_url = f"https://{account_name}.blob.core.windows.net"
            credential = DefaultAzureCredential(
                managed_identity_client_id=os.environ.get("AZURE_CLIENT_ID")
            )
            svc = BlobServiceClient(
                account_url=account_url,
                credential=credential,
            )
            cc = svc.get_container_client(_CONTAINER_NAME)

            # Verify access — retry to tolerate RBAC propagation delay
            last_err = None
            for attempt in range(_INIT_RETRIES):
                try:
                    cc.get_container_properties()
                    last_err = None
                    break
                except Exception as e:
                    if "AuthorizationFailure" in str(e) or "403" in str(e):
                        wait = _INIT_BACKOFF * (2 ** attempt)
                        logger.warning(
                            "Blob auth failed (attempt %d/%d) — retrying in %ds "
                            "(RBAC propagation may be pending)",
                            attempt + 1, _INIT_RETRIES, wait,
                        )
                        last_err = e
                        time.sleep(wait)
                    else:
                        # Not an auth error — maybe container doesn't exist
                        try:
                            cc.create_container()
                            logger.info("Created blob container '%s'", _CONTAINER_NAME)
                        except Exception:
                            pass  # container may already exist (race)
                        last_err = None
                        break

            if last_err is not None:
                raise last_err  # all retries exhausted → fall to outer except

            # Only assign globals after successful verification
            _blob_service_client = svc
            _container_client = cc

            logger.info(
                "Blob sync initialised: account=%s container=%s",
                account_name,
                _CONTAINER_NAME,
            )
            return _container_client
        except Exception:
            logger.exception("Failed to initialise blob sync -> running without persistence")
            _blob_service_client = None
            _container_client = None
            _init_checked = True
            return None


def is_enabled() -> bool:
    """Return True if blob sync is configured and available."""
    return _get_container_client() is not None


# ── Download (startup) ──────────────────────────────────────────────────────


def download_all():
    """Download all user data and auth.db from Blob to local filesystem.

    Called once at application startup before Flask begins serving.
    """
    client = _get_container_client()
    if client is None:
        return

    t0 = time.monotonic()
    file_count = 0

    # Download user data tree
    try:
        blobs = client.list_blobs(name_starts_with=_USERS_PREFIX)
        for blob in blobs:
            # blob.name = "users/angel_at_ms_com/prompts/gpt4/classification.md"
            local_path = _LOCAL_USERS_DIR.parent / blob.name  # data/ + users/...
            local_path.parent.mkdir(parents=True, exist_ok=True)
            blob_client = client.get_blob_client(blob.name)
            data = blob_client.download_blob().readall()
            local_path.write_bytes(data)
            file_count += 1
    except Exception:
        logger.exception("Error downloading user data from blob")

    # Download auth.db
    try:
        blob_client = client.get_blob_client(_AUTH_DB_BLOB)
        data = blob_client.download_blob().readall()
        _LOCAL_AUTH_DB.parent.mkdir(parents=True, exist_ok=True)
        _LOCAL_AUTH_DB.write_bytes(data)
        logger.info("Downloaded auth.db from blob (%d bytes)", len(data))
    except Exception as e:
        # 404 = no auth.db yet, that's fine for first deployment
        if "BlobNotFound" in str(e) or "ResourceNotFound" in str(e):
            logger.info("No auth.db in blob yet — starting fresh")
        else:
            logger.exception("Error downloading auth.db from blob")

    elapsed = time.monotonic() - t0
    logger.info(
        "Blob download complete: %d files in %.1fs", file_count, elapsed
    )


# ── Upload (write-through) ─────────────────────────────────────────────────


def upload_file(local_path: Path):
    """Upload a single file to its corresponding blob path.

    Call this immediately after any write to persist the change.
    The blob name mirrors the local relative path under ``data/``.
    """
    client = _get_container_client()
    if client is None:
        return

    try:
        # Determine blob name: data/users/uid/... → users/uid/...
        # or data/auth.db → auth.db
        rel = local_path.relative_to(Path("data"))
        blob_name = rel.as_posix()

        blob_client = client.get_blob_client(blob_name)
        with open(local_path, "rb") as f:
            blob_client.upload_blob(f, overwrite=True)

        logger.debug("Uploaded %s -> blob:%s", local_path, blob_name)
    except Exception:
        logger.exception("Failed to upload %s to blob", local_path)


def upload_bytes(blob_name: str, data: bytes):
    """Upload raw bytes to a specific blob path."""
    client = _get_container_client()
    if client is None:
        return

    try:
        blob_client = client.get_blob_client(blob_name)
        blob_client.upload_blob(io.BytesIO(data), overwrite=True)
        logger.debug("Uploaded %d bytes -> blob:%s", len(data), blob_name)
    except Exception:
        logger.exception("Failed to upload bytes to blob:%s", blob_name)


def upload_user_tree(user_id: str):
    """Upload the entire directory tree for a specific user."""
    client = _get_container_client()
    if client is None:
        return

    user_dir = _LOCAL_USERS_DIR / user_id
    if not user_dir.exists():
        return

    count = 0
    for local_file in user_dir.rglob("*"):
        if local_file.is_file():
            try:
                rel = local_file.relative_to(Path("data"))
                blob_name = rel.as_posix()
                blob_client = client.get_blob_client(blob_name)
                with open(local_file, "rb") as f:
                    blob_client.upload_blob(f, overwrite=True)
                count += 1
            except Exception:
                logger.exception("Failed to upload %s", local_file)

    logger.info("Uploaded %d files for user %s", count, user_id)


def upload_auth_db():
    """Upload the auth.db SQLite database to blob."""
    if _LOCAL_AUTH_DB.exists():
        upload_file(_LOCAL_AUTH_DB)


def delete_blob(local_path: Path):
    """Delete the blob corresponding to a local file path."""
    client = _get_container_client()
    if client is None:
        return

    try:
        rel = local_path.relative_to(Path("data"))
        blob_name = rel.as_posix()
        blob_client = client.get_blob_client(blob_name)
        blob_client.delete_blob()
        logger.debug("Deleted blob:%s", blob_name)
    except Exception:
        # Blob may not exist — that's fine
        logger.debug("Could not delete blob for %s (may not exist)", local_path)


def delete_user_tree(user_id: str):
    """Delete all blobs for a specific user."""
    client = _get_container_client()
    if client is None:
        return

    prefix = f"{_USERS_PREFIX}{user_id}/"
    try:
        blobs = list(client.list_blobs(name_starts_with=prefix))
        for blob in blobs:
            client.get_blob_client(blob.name).delete_blob()
        logger.info("Deleted %d blobs for user %s", len(blobs), user_id)
    except Exception:
        logger.exception("Failed to delete blobs for user %s", user_id)


# ── Incremental sync (background thread) ───────────────────────────────────


def _sync_all_users():
    """Upload all user data + auth.db — used by the periodic background sync."""
    client = _get_container_client()
    if client is None:
        return

    if not _LOCAL_USERS_DIR.exists():
        return

    count = 0
    for user_dir in _LOCAL_USERS_DIR.iterdir():
        if user_dir.is_dir():
            for local_file in user_dir.rglob("*"):
                if local_file.is_file():
                    try:
                        rel = local_file.relative_to(Path("data"))
                        blob_name = rel.as_posix()
                        blob_client = client.get_blob_client(blob_name)
                        with open(local_file, "rb") as f:
                            blob_client.upload_blob(f, overwrite=True)
                        count += 1
                    except Exception:
                        logger.debug("Periodic sync failed for %s", local_file)

    upload_auth_db()
    logger.info("Periodic sync: uploaded %d files", count)


_bg_thread: Optional[threading.Thread] = None
_bg_stop = threading.Event()


def start_background_sync(interval: int = _SYNC_INTERVAL):
    """Start a daemon thread that periodically syncs all data to blob."""
    global _bg_thread

    if not is_enabled():
        return

    if _bg_thread is not None and _bg_thread.is_alive():
        return

    def _worker():
        logger.info("Background blob sync started (every %ds)", interval)
        while not _bg_stop.wait(timeout=interval):
            try:
                _sync_all_users()
            except Exception:
                logger.exception("Background sync error")
        logger.info("Background blob sync stopped")

    _bg_stop.clear()
    _bg_thread = threading.Thread(target=_worker, daemon=True, name="blob-sync")
    _bg_thread.start()


def stop_background_sync():
    """Signal the background sync thread to stop."""
    _bg_stop.set()


# ── Convenience: download a single user ─────────────────────────────────────


def download_user(user_id: str) -> bool:
    """Download a specific user's data from blob.

    Returns True if any files were downloaded (user exists in blob).
    """
    client = _get_container_client()
    if client is None:
        return False

    prefix = f"{_USERS_PREFIX}{user_id}/"
    count = 0
    try:
        blobs = client.list_blobs(name_starts_with=prefix)
        for blob in blobs:
            local_path = _LOCAL_USERS_DIR.parent / blob.name
            local_path.parent.mkdir(parents=True, exist_ok=True)
            blob_client = client.get_blob_client(blob.name)
            data = blob_client.download_blob().readall()
            local_path.write_bytes(data)
            count += 1
    except Exception:
        logger.exception("Error downloading user %s from blob", user_id)
        return False

    if count > 0:
        logger.info("Downloaded %d files for user %s from blob", count, user_id)
    return count > 0
