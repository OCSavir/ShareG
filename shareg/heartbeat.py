"""Connection-health monitoring for ShareG.

Periodically pings every paired device (device-level, not just in-discovery)
and reports each as "connected" or "disconnected" so the UI can update
statuses without blocking. Uses the TCP ping from protocol.py, spread across
a small worker pool so many paired devices don't serialize.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Dict, Optional

from . import constants as c
from .protocol import ping

log = logging.getLogger(__name__)


class HeartbeatService:
    """Background ping loop over paired devices.

    Callbacks:
        on_status(device_id, name, status, detail)
            status: "connected" | "disconnected"
    """

    def __init__(
        self,
        pairing_store,
        resolve_addr: Optional[Callable[[str], Optional[tuple]]] = None,
        on_status: Optional[Callable[[str, str, str, str], None]] = None,
        interval: float = c.HEARTBEAT_INTERVAL,
        timeout: float = c.HEARTBEAT_TIMEOUT,
    ) -> None:
        self.pairing_store = pairing_store
        # resolve_addr(device_id) -> (ip, port) | None. Default: look up the
        # last-seen IP from the pairing store + discovery cache is injected by
        # the app layer.
        self.resolve_addr = resolve_addr
        self.own_id = ""
        self.own_name = ""
        self.on_status = on_status
        self.interval = interval
        self.timeout = timeout

        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._statuses: Dict[str, str] = {}
        self._lock = threading.Lock()

    def start(self) -> None:
        if self._thread:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="shareg-heartbeat", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def threads(self) -> list:
        """Live thread objects owned by this service (for lifecycle tests)."""
        return [t for t in (self._thread,) if t and t.is_alive()]

    def status_of(self, device_id: str) -> str:
        with self._lock:
            return self._statuses.get(device_id, "unknown")

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self._round()
            except Exception:  # noqa: BLE001
                log.exception("heartbeat round failed")

    def _round(self) -> None:
        devices = self.pairing_store.all()
        paired = {did: d for did, d in devices.items() if d.get("status") == "paired"}
        for did, info in paired.items():
            if self._stop.is_set():
                return
            addr = self.resolve_addr(did) if self.resolve_addr else None
            if not addr:
                self._report(did, info.get("name", ""), "disconnected", "not on network")
                continue
            ip, port = addr
            ok = ping(ip, port, timeout=self.timeout,
                      sender_id=self.own_id, sender_name=self.own_name)
            self._report(
                did, info.get("name", ""), "connected" if ok else "disconnected",
                f"{ip}:{port}",
            )

    def _report(self, device_id: str, name: str, status: str, detail: str) -> None:
        changed = False
        with self._lock:
            if self._statuses.get(device_id) != status:
                self._statuses[device_id] = status
                changed = True
        if changed and self.on_status:
            try:
                self.on_status(device_id, name, status, detail)
            except Exception:  # noqa: BLE001
                log.exception("on_status callback failed")
