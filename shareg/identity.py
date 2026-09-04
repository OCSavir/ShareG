"""Local device identity for ShareG (persisted device_id + display name).

The cache is keyed by the identity file path so multiple instances within one
process (tests, multi-instance setups) resolve their own separate identities
when SHAREG_DATA_DIR differs.
"""

from __future__ import annotations

import json
import os
import threading
import uuid

from . import constants as c

_lock = threading.Lock()
_caches: dict = {}  # identity file path -> {"device_id", "name"}


def _path() -> str:
    from .paths import identity_store_path

    return identity_store_path()


def _load_or_create(path: str, name: str) -> dict:
    data = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    device_id = data.get("device_id") or uuid.uuid4().hex
    if data.get("device_id") != device_id or data.get("device_name") != name:
        data = {"device_id": device_id, "device_name": name}
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except OSError:
            pass
    return {"device_id": device_id, "name": data.get("device_name") or name}


def get_identity(name: str = None) -> dict:
    """Return {'device_id', 'name'} for this (data-dir, name); persists on disk."""
    path = _path()
    with _lock:
        ident = _caches.get(path)
        if ident is None:
            ident = _load_or_create(path, name or c.APP_NAME)
            _caches[path] = ident
        elif name is not None and ident["name"] != name:
            ident = _load_or_create(path, name)
            _caches[path] = ident
        return dict(ident)


def set_device_name(name: str) -> None:
    get_identity(name)
