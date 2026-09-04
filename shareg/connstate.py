"""Per-remote-device connection state machine for ShareG.

Tracks the lifecycle of the relationship with each remote device so that
pairing and transfers are serialized per peer and invalid transitions are
rejected:

    DISCOVERED -> CONNECTING -> PAIRING_PENDING -> PAIRED
                -> CONNECTED -> DISCONNECTED

All state lives in memory (process lifetime); the durable "trusted/blocked"
decision itself is persisted by the PairingStore. The state machine's job is
concurrency control (one negotiation/transfer session per peer at a time) and
deterministic transitions — not persistence.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Dict, Optional

log = logging.getLogger(__name__)

DISCOVERED = "DISCOVERED"
CONNECTING = "CONNECTING"
PAIRING_PENDING = "PAIRING_PENDING"
PAIRED = "PAIRED"
CONNECTED = "CONNECTED"
DISCONNECTED = "DISCONNECTED"

# Transitions that are always legal (others are rejected by transition()).
_ALLOWED = {
    (DISCOVERED, CONNECTING),
    (DISCOVERED, PAIRING_PENDING),   # inbound pairing request from the peer
    (DISCOVERED, DISCONNECTED),
    (CONNECTING, PAIRING_PENDING),
    (CONNECTING, PAIRED),            # pre-paired peers skip the prompt
    (CONNECTING, DISCONNECTED),      # connect failed / refused
    (PAIRING_PENDING, PAIRED),
    (PAIRING_PENDING, DISCONNECTED), # rejected / timed out / user dismissed
    (PAIRED, CONNECTED),
    (PAIRED, DISCONNECTED),
    (CONNECTED, DISCONNECTED),
    (CONNECTED, PAIRED),             # transfer session ended, link kept
    (CONNECTED, CONNECTED),          # re-entering a transfer session
    (DISCONNECTED, CONNECTING),      # retry after failure
    (DISCONNECTED, CONNECTED),       # direct success (already paired)
}


class PeerConnectionState:
    """State + session ownership for one remote device."""

    __slots__ = ("state", "updated_at", "session_id")

    def __init__(self, state: str) -> None:
        self.state = state
        self.updated_at = time.time()
        self.session_id = 0


class ConnectionStateMachine:
    """Thread-safe per-peer state registry."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._peers: Dict[str, PeerConnectionState] = {}

    # ------------------------------------------------------------------ query

    def state_of(self, device_id: str) -> str:
        with self._lock:
            entry = self._peers.get(device_id)
            return entry.state if entry else DISCOVERED

    # ------------------------------------------------------------------ transitions

    def transition(self, device_id: str, new_state: str, *, force: bool = False) -> bool:
        """Apply a state transition; returns False if it is not allowed.

        `force` is only for shutdown paths that must tear everything down.
        """
        with self._lock:
            entry = self._peers.get(device_id)
            if entry is None:
                if new_state == DISCOVERED:
                    return True  # nothing to record for the initial state
                entry = PeerConnectionState(DISCOVERED)
                self._peers[device_id] = entry
            if not force and (entry.state, new_state) not in _ALLOWED:
                log.warning(
                    "invalid state transition for %s: %s -> %s (rejected)",
                    device_id[:8], entry.state, new_state,
                )
                return False
            entry.state = new_state
            entry.updated_at = time.time()
            log.debug("peer %s… state -> %s", device_id[:8], new_state)
            return True

    # ------------------------------------------------------------------ sessions

    def begin_session(self, device_id: str) -> Optional[int]:
        """Try to claim the peer for a connect/pair/transfer session.

        Returns a session id (monotonic per peer) or None when another session
        with this peer is already in progress — the caller must not start a
        duplicate connection.
        """
        with self._lock:
            entry = self._peers.get(device_id)
            if entry is None:
                entry = PeerConnectionState(DISCOVERED)
                self._peers[device_id] = entry
            if entry.state in (CONNECTING, PAIRING_PENDING):
                log.info(
                    "peer %s… busy in %s; refusing duplicate session",
                    device_id[:8], entry.state,
                )
                return None
            entry.session_id += 1
            entry.state = CONNECTING
            entry.updated_at = time.time()
            return entry.session_id

    def end_session(self, device_id: str, session_id: int, final: str) -> bool:
        """Close a session claimed with begin_session().

        Only the session that holds the current session_id may finalize the
        state, so a stale (timed-out) session can never clobber a newer one.
        """
        with self._lock:
            entry = self._peers.get(device_id)
            if entry is None or entry.session_id != session_id:
                log.debug(
                    "stale session %s for %s… ignored", session_id, device_id[:8]
                )
                return False
            if final == CONNECTED:
                entry.state = CONNECTED
            elif final == PAIRED:
                entry.state = PAIRED
            elif final == DISCONNECTED:
                entry.state = DISCONNECTED
            else:  # pragma: no cover - guard against typos
                log.warning("unsupported session final state %s", final)
                return False
            entry.updated_at = time.time()
            return True

    def clear(self) -> None:
        with self._lock:
            self._peers.clear()
