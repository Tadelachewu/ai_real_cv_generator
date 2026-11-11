"""Payment gate: global "payment required" flag and the list of paid users.

Stored in Postgres (tables `app_settings` and `paid_users`, see pg_store.py)
so admin changes survive deploys. When the database is unreachable, the
JSON files under config/ are used as a local fallback.
"""
import json
import threading
from pathlib import Path
from typing import Union

import pg_store

_LOCK = threading.Lock()
_ROOT = Path(__file__).parent
_CFG_DIR = _ROOT / 'config'
_CFG_DIR.mkdir(exist_ok=True)
_SETTINGS = _CFG_DIR / 'payment_settings.json'
_PAID_USERS = _CFG_DIR / 'paid_users.json'

_PAYMENT_KEY = 'payment_required'


# ---- local fallback -------------------------------------------------------

def _read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return default


def _write_json(path: Path, data):
    tmp = path.with_suffix('.tmp')
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(data, indent=2), encoding='utf-8')
    tmp.replace(path)


def _local_set_enabled(enabled: bool):
    with _LOCK:
        cfg = _read_json(_SETTINGS, {})
        cfg['enabled'] = bool(enabled)
        _write_json(_SETTINGS, cfg)


def _local_set_paid(uid: str, paid: bool):
    with _LOCK:
        users = _read_json(_PAID_USERS, [])
        if paid and uid not in users:
            users.append(uid)
        elif not paid and uid in users:
            users.remove(uid)
        _write_json(_PAID_USERS, users)


# ---- public API -----------------------------------------------------------

def storage_backend() -> str:
    """'postgres' or 'local files' - shown in /payment_status."""
    return 'postgres' if pg_store.available() else 'local files'


def is_payment_required() -> bool:
    """Return True when admin enabled the payment requirement."""
    if pg_store.available():
        try:
            return bool(pg_store.get_setting(_PAYMENT_KEY, False))
        except Exception as e:
            pg_store.mark_failed(e)
    return bool(_read_json(_SETTINGS, {}).get('enabled'))


def set_payment_required(enabled: bool):
    """Set the global payment-required flag (admin only)."""
    if pg_store.available():
        try:
            pg_store.set_setting(_PAYMENT_KEY, bool(enabled))
            return
        except Exception as e:
            pg_store.mark_failed(e)
    _local_set_enabled(enabled)


def is_user_paid(user_id: Union[int, str]) -> bool:
    """Check if a user id is marked paid. Accepts int or string."""
    uid = str(user_id).strip()
    if pg_store.available():
        try:
            return pg_store.is_paid_user(uid)
        except Exception as e:
            pg_store.mark_failed(e)
    return uid in _read_json(_PAID_USERS, [])


def mark_user_paid(user_id: Union[int, str]):
    """Mark a user as paid."""
    uid = str(user_id).strip()
    if pg_store.available():
        try:
            pg_store.add_paid_user(uid)
            return
        except Exception as e:
            pg_store.mark_failed(e)
    _local_set_paid(uid, True)


def mark_user_unpaid(user_id: Union[int, str]):
    """Remove paid status for a user."""
    uid = str(user_id).strip()
    if pg_store.available():
        try:
            pg_store.remove_paid_user(uid)
            return
        except Exception as e:
            pg_store.mark_failed(e)
    _local_set_paid(uid, False)


def list_paid_users():
    if pg_store.available():
        try:
            return pg_store.paid_user_ids()
        except Exception as e:
            pg_store.mark_failed(e)
    return _read_json(_PAID_USERS, [])


def show_settings():
    if pg_store.available():
        try:
            return {'enabled': bool(pg_store.get_setting(_PAYMENT_KEY, False)), 'storage': 'postgres'}
        except Exception as e:
            pg_store.mark_failed(e)
    cfg = _read_json(_SETTINGS, {'enabled': False})
    cfg['storage'] = 'local files'
    return cfg
