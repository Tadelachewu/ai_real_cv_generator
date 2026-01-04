"""Resilient DB helpers.

Attempts to use SQLite; if the DB is unavailable (disk full, too many connections,
corruption, etc.) the module falls back to an in-memory dict persisted to
a JSON file under `temp/` so the application can continue operating.
"""

from datetime import datetime
import json
import os
import sqlite3
import threading
from typing import Dict, Optional
import logging

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMP_DIR = os.path.join(BASE_DIR, 'temp')
TEMPLATES_DIR = os.path.join(BASE_DIR, 'templates')

os.makedirs(TEMP_DIR, exist_ok=True)
os.makedirs(TEMPLATES_DIR, exist_ok=True)

DB_PATH = os.path.join(BASE_DIR, "cv_bot.db")
FALLBACK_FILE = os.path.join(TEMP_DIR, "cv_fallback.json")

logger = logging.getLogger(__name__)


class DBWrapper:
    def __init__(self):
        self._lock = threading.RLock()
        self._mode = "sqlite"
        self._conn = None
        self._fallback: Dict[str, Dict] = {}
        try:
            self._conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=5)
            c = self._conn.cursor()
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    data TEXT,
                    last_updated TEXT
                )
                """
            )
            self._conn.commit()
            logger.info("Using sqlite DB at %s", DB_PATH)
        except Exception as e:
            logger.exception("SQLite unavailable, switching to fallback: %s", e)
            self._switch_to_fallback()

    def _switch_to_fallback(self):
        with self._lock:
            try:
                if self._conn:
                    try:
                        self._conn.close()
                    except Exception:
                        pass
                    self._conn = None
            finally:
                self._mode = "fallback"
                # load existing fallback file if present
                try:
                    if os.path.exists(FALLBACK_FILE):
                        with open(FALLBACK_FILE, "r", encoding="utf-8") as f:
                            self._fallback = json.load(f)
                    else:
                        self._fallback = {}
                except Exception:
                    logger.exception("Failed to load fallback file; starting fresh")
                    self._fallback = {}

    def save_user_data(self, user_id: int, data: Dict):
        with self._lock:
            if self._mode == "sqlite":
                try:
                    c = self._conn.cursor()
                    # store as JSON to be robust
                    c.execute(
                        "INSERT OR REPLACE INTO users (user_id, data, last_updated) VALUES (?, ?, ?)",
                        (user_id, json.dumps(data), datetime.now().isoformat()),
                    )
                    self._conn.commit()
                    return True
                except Exception:
                    logger.exception("SQLite write failed; switching to fallback")
                    self._switch_to_fallback()
                    # fall through to fallback write

            # Fallback mode
            try:
                key = str(user_id)
                self._fallback[key] = {"data": data, "last_updated": datetime.now().isoformat()}
                with open(FALLBACK_FILE, "w", encoding="utf-8") as f:
                    json.dump(self._fallback, f)
                return True
            except Exception:
                logger.exception("Failed to write fallback file")
                return False

    def load_user_data(self, user_id: int) -> Optional[Dict]:
        with self._lock:
            if self._mode == "sqlite":
                try:
                    c = self._conn.cursor()
                    c.execute("SELECT data FROM users WHERE user_id=?", (user_id,))
                    row = c.fetchone()
                    if row and row[0]:
                        raw = row[0]
                        # try JSON first, then eval as a last resort for legacy rows
                        try:
                            return json.loads(raw)
                        except Exception:
                            try:
                                return eval(raw)
                            except Exception:
                                logger.exception("Failed to parse stored user data")
                                return None
                    return None
                except Exception:
                    logger.exception("SQLite read failed; switching to fallback")
                    self._switch_to_fallback()
                    # fall through to fallback read

            # Fallback mode
            try:
                key = str(user_id)
                rec = self._fallback.get(key)
                if rec:
                    return rec.get("data")
                return None
            except Exception:
                logger.exception("Failed to read fallback data")
                return None


# single module-level wrapper used by other modules
_db = DBWrapper()


def save_user_data(user_id: int, data: Dict):
    return _db.save_user_data(user_id, data)


def load_user_data(user_id: int) -> Optional[Dict]:
    return _db.load_user_data(user_id)
