"""Shared Postgres (Neon) storage for persistent app state.

Tables created here:
  app_settings  - key/value admin settings (e.g. payment on/off)
  paid_users    - Telegram user ids marked as paid by the admin
  saved_cvs     - each user's last CV data (used by /last)

Render's disk is wiped on every deploy/restart, so anything that must survive
lives here. Callers should check `available()` and fall back to local files
when the database cannot be reached, so the bot keeps working regardless.
"""
import json
import logging
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from dotenv import load_dotenv

try:
    import psycopg2
    from psycopg2.extras import Json
except Exception:  # pragma: no cover - psycopg2 missing
    psycopg2 = None
    Json = None

_ROOT = Path(__file__).parent
load_dotenv(_ROOT / ".env")  # does not override real environment variables

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_ready = False
_last_failure = 0.0
_RETRY_SECONDS = 60

_SCHEMA = """
CREATE TABLE IF NOT EXISTS app_settings (
    key        TEXT PRIMARY KEY,
    value      JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS paid_users (
    user_id   TEXT PRIMARY KEY,
    marked_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS saved_cvs (
    user_id    BIGINT PRIMARY KEY,
    data       JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def _db_url():
    return (os.getenv("DATABASE_URL") or "").strip()


@contextmanager
def cursor():
    """Yield a cursor in its own transaction (commit on success, rollback on error)."""
    conn = psycopg2.connect(_db_url(), connect_timeout=10)
    try:
        with conn.cursor() as cur:
            yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def available() -> bool:
    """True when Postgres is configured and the schema is ready.

    The first successful call creates the tables and imports any legacy
    local data. After a failure it waits _RETRY_SECONDS before trying again,
    so an outage does not slow every request down.
    """
    global _ready, _last_failure
    if _ready:
        return True
    if psycopg2 is None or not _db_url():
        return False
    if time.time() - _last_failure < _RETRY_SECONDS:
        return False
    with _LOCK:
        if _ready:
            return True
        try:
            with cursor() as cur:
                cur.execute(_SCHEMA)
            _migrate_local_data()
            _ready = True
            logger.info("Persistent storage: Postgres ready (app_settings, paid_users, saved_cvs)")
        except Exception as e:
            _last_failure = time.time()
            logger.warning("Postgres storage unavailable, using local files for now: %s", e)
    return _ready


def mark_failed(exc: Exception):
    """Report a runtime DB error so callers fall back for a while."""
    global _ready, _last_failure
    logger.warning("Postgres operation failed, falling back to local storage: %s", exc)
    _ready = False
    _last_failure = time.time()


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default
    except Exception:
        return default


def _migrate_local_data():
    """One-way import of legacy file/SQLite data. Existing DB rows always win."""
    cfg_dir = _ROOT / "config"
    settings = _read_json(cfg_dir / "payment_settings.json", {})
    paid = _read_json(cfg_dir / "paid_users.json", [])
    rows = []
    sqlite_path = _ROOT / "cv_bot.db"
    if sqlite_path.exists():
        try:
            conn = sqlite3.connect(str(sqlite_path))
            try:
                rows = conn.execute("SELECT user_id, data FROM users").fetchall()
            finally:
                conn.close()
        except Exception:
            logger.exception("Could not read legacy SQLite CV data")

    with cursor() as cur:
        if isinstance(settings, dict) and "enabled" in settings:
            cur.execute(
                "INSERT INTO app_settings (key, value) VALUES ('payment_required', %s) "
                "ON CONFLICT (key) DO NOTHING",
                (Json(bool(settings["enabled"])),),
            )
        imported_paid = 0
        for uid in paid if isinstance(paid, list) else []:
            cur.execute(
                "INSERT INTO paid_users (user_id) VALUES (%s) ON CONFLICT DO NOTHING",
                (str(uid),),
            )
            imported_paid += cur.rowcount
        imported = 0
        for uid, raw in rows:
            try:
                data = json.loads(raw)
            except Exception:
                continue  # skip legacy rows that are not valid JSON
            if isinstance(data, dict):
                cur.execute(
                    "INSERT INTO saved_cvs (user_id, data) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    (int(uid), Json(data)),
                )
                imported += cur.rowcount
    if imported_paid or imported:
        logger.info("Imported legacy local data: %d paid users, %d saved CVs", imported_paid, imported)


# ---- settings -------------------------------------------------------------

def get_setting(key: str, default=None):
    with cursor() as cur:
        cur.execute("SELECT value FROM app_settings WHERE key = %s", (key,))
        row = cur.fetchone()
    return row[0] if row else default


def set_setting(key: str, value):
    with cursor() as cur:
        cur.execute(
            "INSERT INTO app_settings (key, value, updated_at) VALUES (%s, %s, now()) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()",
            (key, Json(value)),
        )


def all_settings() -> dict:
    with cursor() as cur:
        cur.execute("SELECT key, value FROM app_settings ORDER BY key")
        return {k: v for k, v in cur.fetchall()}


# ---- paid users -----------------------------------------------------------

def add_paid_user(user_id: str):
    with cursor() as cur:
        cur.execute("INSERT INTO paid_users (user_id) VALUES (%s) ON CONFLICT DO NOTHING", (user_id,))


def remove_paid_user(user_id: str):
    with cursor() as cur:
        cur.execute("DELETE FROM paid_users WHERE user_id = %s", (user_id,))


def is_paid_user(user_id: str) -> bool:
    with cursor() as cur:
        cur.execute("SELECT 1 FROM paid_users WHERE user_id = %s", (user_id,))
        return cur.fetchone() is not None


def paid_user_ids() -> list:
    with cursor() as cur:
        cur.execute("SELECT user_id FROM paid_users ORDER BY marked_at")
        return [r[0] for r in cur.fetchall()]


# ---- saved CVs ------------------------------------------------------------

def save_cv(user_id: int, data: dict):
    with cursor() as cur:
        cur.execute(
            "INSERT INTO saved_cvs (user_id, data, updated_at) VALUES (%s, %s, now()) "
            "ON CONFLICT (user_id) DO UPDATE SET data = EXCLUDED.data, updated_at = now()",
            (int(user_id), Json(data)),
        )


def load_cv(user_id: int):
    with cursor() as cur:
        cur.execute("SELECT data FROM saved_cvs WHERE user_id = %s", (int(user_id),))
        row = cur.fetchone()
    return row[0] if row else None
