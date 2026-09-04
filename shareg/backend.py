"""ShareG backend facade: wires discovery, TCP server, pairing, heartbeat.

The UI layer (Flet) talks only to this class; all socket work lives below it.
Long-running work happens on daemon threads; UI callbacks are invoked from
those threads and marshalled onto the UI thread by the UI layer.

Connection lifecycle per remote device is tracked by
:class:`shareg.connstate.ConnectionStateMachine` so duplicate connection
attempts are refused and pairing/transfer sessions are serialized per peer.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Dict, Optional

from . import constants as c
from . import connstate
from .discovery import DiscoveryService, get_local_ip
from .heartbeat import HeartbeatService
from .identity import get_identity, set_device_name
from .pairing import PairingStore
from .protocol import (
    ProtocolError,
    TransferRejected,
    TransferProgress,
    TransferServer,
    collect_files,
    ping,
    send_files,
    send_text,
)

log = logging.getLogger(__name__)


class ShareGUserError(Exception):
    """User-facing error (bad selection, unreachable device, rejection…)."""


def discovery_peer_view(device_id: str, info: dict) -> dict:
    return {
        "device_id": device_id,
        "name": info["name"],
        "ip": info["ip"],
        "port": info["port"],
        "last_seen": info["last_seen"],
    }


class ShareGBackend:
    """One instance per running app; owns all networking state."""

    UI_CALLBACK_KEYS = (
        "on_peers_changed", "on_pair_prompt", "on_text_received",
        "on_files_received", "on_status_changed", "on_progress",
    )

    def __init__(
        self,
        device_name: str,
        *,
        on_log: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.on_log = on_log or (lambda msg: None)

        self.pairing = PairingStore()
        self.device_id = get_identity(device_name)["device_id"]
        self.conn_state = connstate.ConnectionStateMachine()

        self.discovery = DiscoveryService(
            device_name=device_name,
            transfer_port=c.TRANSFER_PORT,
            on_peer_seen=self._on_peer_seen,
            on_peer_expired=self._on_peer_expired,
        )
        self.transfer_server = TransferServer(
            pairing_store=self.pairing,
            on_pair_request=self._handle_pair_request,
            on_text=self._on_text_received,
            on_files=self._on_files_received,
            on_transfer_progress=self._on_progress,
            own_device_id=self.device_id,
            own_name=device_name,
        )
        self.heartbeat = HeartbeatService(
            pairing_store=self.pairing,
            resolve_addr=self._resolve_addr,
            on_status=self._on_status_changed,
        )
        self.heartbeat.own_id = self.device_id
        self.heartbeat.own_name = device_name

        self.peers: Dict[str, dict] = {}
        self.statuses: Dict[str, str] = {}
        self._lock = threading.Lock()
        self._ui_callbacks: Dict[str, Optional[Callable]] = {k: None for k in self.UI_CALLBACK_KEYS}
        self._stopped = threading.Event()

    # ------------------------------------------------------------------ UI wiring

    def set_ui_callbacks(self, **callbacks: Callable) -> None:
        """Register UI callbacks; unknown keys are ignored."""
        for key, cb in callbacks.items():
            if key in self._ui_callbacks and cb is not None:
                self._ui_callbacks[key] = cb
        # Seed with any peers discovered before the UI attached.
        with self._lock:
            for did, info in self.discovery.peers().items():
                self.peers[did] = discovery_peer_view(did, info)
        self._notify("on_peers_changed")

    def _notify(self, key: str, *args) -> None:
        cb = self._ui_callbacks.get(key)
        if not cb:
            return
        try:
            cb(*args)
        except Exception:  # noqa: BLE001 — a dead UI must not kill networking
            log.exception("UI callback %s failed", key)

    # ------------------------------------------------------------------ lifecycle

    def start(self) -> None:
        if not self._stopped.is_set() and self.discovery._threads:
            return  # already started
        self._stopped.clear()
        self.transfer_server.start()
        # Announce the port actually bound (may be ephemeral if the default
        # port was taken by another ShareG instance on this machine).
        self.discovery.transfer_port = self.transfer_server.port
        self.discovery.start()
        self.heartbeat.start()
        self.log(f"ShareG started — device id {self.device_id[:8]}…, "
                 f"transfer port {self.transfer_server.port}")

    def stop(self) -> None:
        """Centralized shutdown: stop every service, join its threads.

        Safe to call more than once and from any thread. After it returns,
        no ShareG thread is doing socket work and all listeners are closed.
        """
        if self._stopped.is_set():
            return
        self._stopped.set()
        log.info("ShareG backend stopping…")
        self.heartbeat.stop()
        self.discovery.stop()
        self.transfer_server.stop()
        self.conn_state.clear()
        self.log("ShareG stopped")

    def threads(self) -> list:
        """All live thread objects this backend owns (for lifecycle tests)."""
        out = []
        out.extend(self.discovery.threads())
        out.extend(self.transfer_server.threads())
        out.extend(self.heartbeat.threads())
        return out

    # ------------------------------------------------------------------ discovery events

    def _on_peer_seen(self, peer: dict) -> None:
        with self._lock:
            self.peers[peer["device_id"]] = discovery_peer_view(peer["device_id"], peer)
        log.debug("peer seen: %s (%s:%s)", peer["name"], peer["ip"], peer["port"])
        self._notify("on_peers_changed")

    def _on_peer_expired(self, device_id: str) -> None:
        with self._lock:
            self.peers.pop(device_id, None)
        log.info("peer expired: %s…", device_id[:8])
        self._notify("on_peers_changed")

    def _resolve_addr(self, device_id: str) -> Optional[tuple]:
        """Heartbeat address lookup: live discovery first, then stored IP."""
        peer = self.discovery.get_peer(device_id)
        if peer:
            return (peer["ip"], peer["port"])
        entry = self.pairing.get(device_id)
        if entry and entry.get("ip"):
            return (entry["ip"], int(entry.get("port") or c.TRANSFER_PORT))
        return None

    # ------------------------------------------------------------------ pairing (receiver side)

    def _handle_pair_request(self, device_id: str, name: str, ip: str, port: int = 0) -> bool:
        """Runs on the receiver's connection thread for unknown devices.

        Persists the decision on THIS device (the requester persists its
        mirror entry when it receives PAIR_ACCEPT, via _on_pair_accepted),
        updates the connection state machine, and notifies the UI.
        """
        self.log(f"Pairing request from {name} ({ip})")
        self.conn_state.transition(device_id, connstate.PAIRING_PENDING)
        cb = self._ui_callbacks.get("on_pair_prompt")
        if cb is None:
            self.log(f"Pairing request from {name} ({ip}) — no UI attached, rejected")
            self.conn_state.transition(device_id, connstate.DISCONNECTED, force=True)
            return False
        box = {"event": threading.Event(), "answer": None}
        try:
            cb(device_id, name, ip, box)
        except Exception:  # noqa: BLE001
            log.exception("pair prompt UI callback failed")
            self.conn_state.transition(device_id, connstate.DISCONNECTED, force=True)
            return False
        box["event"].wait(timeout=c.PAIR_PROMPT_TIMEOUT)
        accepted = bool(box["answer"])
        # Persist the decision (receiver side) so future connections skip the
        # prompt; the requester stores the mirror entry on PAIR_ACCEPT.
        if accepted:
            self.pairing.pair(device_id, name, ip, port=port)
        else:
            self.pairing.block(device_id, name, ip)
        self.conn_state.transition(
            device_id,
            connstate.PAIRED if accepted else connstate.DISCONNECTED,
            force=True,
        )
        self._notify("on_status_changed", device_id, name,
                     "connected" if accepted else "disconnected",
                     "pairing " + ("accepted" if accepted else "rejected"))
        self.log(f"Pairing {'accepted' if accepted else 'rejected'}: {name}")
        return accepted

    # ------------------------------------------------------------------ pairing (sender side)

    def _on_pair_accepted(self, peer: dict) -> None:
        """Called on the SENDER when the receiver answers PAIR_ACCEPT.

        Persists the mirror trusted entry so pairing is mutual: both devices
        now hold each other's device_id, name, ip and transfer port.
        """
        device_id = peer.get("device_id") or ""
        if not device_id:
            log.warning("pair_accept frame without device_id; cannot persist")
            return
        self.pairing.pair(device_id, peer.get("name", ""), peer.get("ip", ""),
                          port=int(peer.get("port") or 0))
        self.conn_state.transition(device_id, connstate.PAIRED, force=True)
        self.log(f"Paired with {peer.get('name', device_id[:8])}")
        self._notify("on_status_changed", device_id, peer.get("name", ""),
                     "connected", "pairing established")

    # ------------------------------------------------------------------ receive callbacks

    def _on_text_received(self, sender_name: str, text: str) -> None:
        self.log(f"Text received from {sender_name} ({len(text)} chars)")
        self._notify("on_text_received", sender_name, text)

    def _on_files_received(self, sender_name: str, folder: str, files: list) -> None:
        self.log(f"Received {len(files)} file(s) from {sender_name} → {folder}")
        self._notify("on_files_received", sender_name, folder, files)

    def _on_progress(self, progress: TransferProgress) -> None:
        self._notify("on_progress", progress)

    def _on_status_changed(self, device_id: str, name: str, status: str, detail: str) -> None:
        with self._lock:
            self.statuses[device_id] = status
        self._notify("on_status_changed", device_id, name, status, detail)

    # ------------------------------------------------------------------ UI actions (send)

    def send_text_to(self, device_id: str, text: str) -> None:
        self._send(device_id, lambda peer: self._do_send_text(peer, text))

    def send_files_to(self, device_id: str, selection: list) -> int:
        paths = [p for p in (selection or []) if p]
        if not paths:
            raise ShareGUserError("No files or folders selected")
        entries, total = collect_files(paths)
        if not entries:
            raise ShareGUserError("Selected folders contain no files")
        return self._send(device_id, lambda peer: self._do_send_files(peer, paths, total))

    def _send(self, device_id: str, action: Callable[[dict], object]) -> object:
        """Shared send path: claim the peer, run the transfer, update state.

        begin_session() refuses a second concurrent session with the same
        device, preventing duplicate connection/pairing attempts.
        """
        session = self.conn_state.begin_session(device_id)
        if session is None:
            raise ShareGUserError("A connection with this device is already in progress")
        try:
            peer = self._require_peer(device_id)
            self.log(f"Connecting to {peer['name']}…")
            result = action(peer)
            # The transfer succeeded: the link is demonstrably up.
            self.conn_state.end_session(device_id, session, connstate.CONNECTED)
            with self._lock:
                self.statuses[device_id] = "connected"
            self._notify("on_status_changed", device_id, peer["name"],
                         "connected", "transfer ok")
            return result
        except TransferRejected as e:
            self.conn_state.end_session(device_id, session, connstate.DISCONNECTED)
            self.log(f"✗ {e}")
            raise ShareGUserError(str(e)) from e
        except Exception as e:
            self.conn_state.end_session(device_id, session, connstate.DISCONNECTED)
            if isinstance(e, ShareGUserError):
                raise
            self.log(f"✗ Send failed: {e}")
            raise ShareGUserError(f"Send failed: {e}") from e

    def _do_send_text(self, peer: dict, text: str) -> None:
        send_text(peer["ip"], peer["port"], text,
                  sender_id=self.device_id,
                  sender_name=self.discovery.device_name,
                  sender_port=self.transfer_server.own_port,
                  on_pair_accepted=self._on_pair_accepted)
        self.log(f"Text sent to {peer['name']}")

    def _do_send_files(self, peer: dict, paths: list, total: int) -> int:
        self.log(f"Sending {len(paths)} item(s) ({total:,} bytes) to {peer['name']}…")
        n = send_files(peer["ip"], peer["port"], paths, on_progress=self._on_progress,
                       sender_id=self.device_id,
                       sender_name=self.discovery.device_name,
                       sender_port=self.transfer_server.own_port,
                       on_pair_accepted=self._on_pair_accepted)
        self.log(f"Sent {n} file(s) to {peer['name']}")
        return n

    def _require_peer(self, device_id: str) -> dict:
        peer = self.discovery.get_peer(device_id)
        if not peer:
            raise ShareGUserError("Device is not currently visible on the network")
        return peer

    # ------------------------------------------------------------------ pairing management

    def paired_devices(self) -> list:
        out = []
        for did, entry in self.pairing.all().items():
            if entry.get("status") == "paired":
                out.append({
                    "device_id": did,
                    "name": entry.get("name", ""),
                    "ip": entry.get("ip", ""),
                    "status": self.heartbeat.status_of(did),
                })
        return out

    def forget_device(self, device_id: str) -> None:
        self.pairing.unpair(device_id)
        with self._lock:
            self.statuses.pop(device_id, None)
        self.log("Removed paired device")
        self._notify("on_status_changed", device_id, "", "removed", "")

    # ------------------------------------------------------------------ misc

    def update_device_name(self, name: str) -> None:
        set_device_name(name)
        self.discovery.device_name = name
        # Announce immediately so peers learn the new name quickly.
        try:
            self.discovery._send_multicast(self.discovery._announce_payload())
        except OSError:
            pass

    def log(self, msg: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        try:
            self.on_log(f"[{stamp}] {msg}")
        except Exception:  # noqa: BLE001
            pass
