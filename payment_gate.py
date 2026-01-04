import json
import threading
from pathlib import Path
from typing import Union

_LOCK = threading.Lock()
_ROOT = Path(__file__).parent
_CFG_DIR = _ROOT / 'config'
_CFG_DIR.mkdir(exist_ok=True)
_SETTINGS = _CFG_DIR / 'payment_settings.json'
_PAID_USERS = _CFG_DIR / 'paid_users.json'


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


def is_payment_required() -> bool:
    """Return True when admin enabled the payment requirement."""
    cfg = _read_json(_SETTINGS, {})
    return bool(cfg.get('enabled'))


def set_payment_required(enabled: bool):
    """Set the global payment-required flag (admin only)."""
    with _LOCK:
        cfg = _read_json(_SETTINGS, {})
        cfg['enabled'] = bool(enabled)
        _write_json(_SETTINGS, cfg)


def is_user_paid(user_id: Union[int, str]) -> bool:
    """Check if a user id is marked paid. Accepts int or string."""
    users = _read_json(_PAID_USERS, [])
    uid = str(user_id)
    return uid in users


def mark_user_paid(user_id: Union[int, str]):
    """Mark a user as paid."""
    with _LOCK:
        users = _read_json(_PAID_USERS, [])
        uid = str(user_id)
        if uid not in users:
            users.append(uid)
            _write_json(_PAID_USERS, users)


def mark_user_unpaid(user_id: Union[int, str]):
    """Remove paid status for a user."""
    with _LOCK:
        users = _read_json(_PAID_USERS, [])
        uid = str(user_id)
        if uid in users:
            users.remove(uid)
            _write_json(_PAID_USERS, users)


def list_paid_users():
    return _read_json(_PAID_USERS, [])


def show_settings():
    return _read_json(_SETTINGS, {'enabled': False})
