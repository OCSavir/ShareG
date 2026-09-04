"""Pairing / trust store for ShareG.

Persisted per device on disk. Once a remote device has been accepted once,
future incoming connections from it are accepted automatically (no prompt);
rejected devices are remembered as blocked.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Dict, Optional

log = logging.getLogger(__name__)


class PairingStore:
    """Thread-safe persistent store of trusted / blocked devices."""

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = path or self._default_path()
        self._lock = threading.Lock()
        self._devices: Dict[str, dict] = {}
        self._load()

    @staticmethod
    def _default_path() -> str:
        from .paths import pairing_store_path

        return pairing_store_path()

    # ------------------------------------------------------------------ io

    def _load(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                self._devices = raw
        except (OSError, ValueError):
            self._devices = {}

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._devices, f, indent=2)
            os.replace(tmp, self.path)
        except OSError:
            log.exception("failed to save pairing store")

    # ------------------------------------------------------------------ api

    def get(self, device_id: str) -> Optional[dict]:
        with self._lock:
            return dict(self._devices[device_id]) if device_id in self._devices else None

    def all(self) -> Dict[str, dict]:
        with self._lock:
            return {did: dict(d) for did, d in self._devices.items()}

    def is_paired(self, device_id: str) -> bool:
        entry = self.get(device_id)
        return bool(entry and entry.get("status") == "paired")

    def is_blocked(self, device_id: str) -> bool:
        entry = self.get(device_id)
        return bool(entry and entry.get("status") == "blocked")

    def known(self, device_id: str) -> bool:
        """True if we have a stored decision (paired OR blocked) for this device."""
        return self.get(device_id) is not None

    def pair(self, device_id: str, name: str, ip: str = "", port: int = 0) -> None:
        with self._lock:
            self._devices[device_id] = {
                "name": name,
                "status": "paired",
                "ip": ip,
                "port": int(port),
                "paired_at": time.time(),
            }
            self._save()

    def block(self, device_id: str, name: str, ip: str = "") -> None:
        with self._lock:
            self._devices[device_id] = {
                "name": name,
                "status": "blocked",
                "ip": ip,
                "blocked_at": time.time(),
            }
            self._save()

    def unpair(self, device_id: str) -> None:
        with self._lock:
            self._devices.pop(device_id, None)
            self._save()
