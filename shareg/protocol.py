"""ShareG TCP wire protocol: framing + chunked transfer + protocol handler.

Wire format (identical on Windows / Linux / Android):
  * Every frame = 4-byte big-endian length + payload (JSON or binary chunk).
  * A transfer session opens with a JSON control frame, then binary frames.
  * MSG_PING / MSG_PONG single-frame exchange provides health checks.

A *file* payload is: FILE_BEGIN {name,size,rel_path} then CHUNK frames then
FILE_END. A *folder* transfer wraps files with FOLDER_BEGIN / FOLDER_END.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import struct
import sys
import threading
import time
from typing import Callable, Dict, Optional

from . import constants as c
from .pairing import PairingStore

log = logging.getLogger(__name__)

RECV_TIMEOUT = 10.0          # per-recv TCP timeout
HANDSHAKE_TIMEOUT = 8.0      # pair-handshake window on incoming connections
DEFAULT_CHUNK = c.DEFAULT_CHUNK_SIZE
MAX_CHUNK = 4 * 1024 * 1024


class ProtocolError(Exception):
    """Malformed or unexpected protocol frame."""


class TransferRejected(Exception):
    """Receiver declined the connection (pairing denied / busy)."""


# ---------------------------------------------------------------------------
# Frame helpers
# ---------------------------------------------------------------------------

def send_frame(sock: socket.socket, payload: bytes) -> None:
    header = struct.pack(">I", len(payload))
    sock.sendall(header + payload)


def recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        piece = sock.recv(min(n - len(buf), 65536))
        if not piece:
            raise ProtocolError("connection closed mid-frame")
        buf.extend(piece)
    return bytes(buf)


def recv_frame(sock: socket.socket) -> bytes:
    raw = recv_exact(sock, 4)
    (length,) = struct.unpack(">I", raw)
    if length > 1 << 30:
        raise ProtocolError(f"frame too large: {length}")
    if length == 0:
        return b""
    return recv_exact(sock, length)


def send_json(sock: socket.socket, obj: dict) -> None:
    send_frame(sock, json.dumps(obj).encode("utf-8"))


def recv_json(sock: socket.socket) -> dict:
    payload = recv_frame(sock)
    try:
        msg = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise ProtocolError(f"bad JSON frame: {e}") from e
    if not isinstance(msg, dict):
        raise ProtocolError("JSON frame is not an object")
    return msg


# ---------------------------------------------------------------------------
# Selection helpers (what the user picked in the UI)
# ---------------------------------------------------------------------------

def collect_files(selection: list[str]) -> tuple[list[dict], int]:
    """Expand UI selections (files and/or folders) into a transfer manifest.

    Returns (entries, total_bytes) where each entry is
    {abs_path, rel_path, size}.
    """
    entries: list[dict] = []
    for sel in selection:
        sel = os.path.abspath(sel)
        if os.path.isdir(sel):
            base_parent = os.path.dirname(sel) or sel
            for root, dirs, files in os.walk(sel):
                dirs.sort()
                files.sort()
                for fname in files:
                    fpath = os.path.join(root, fname)
                    if os.path.isfile(fpath) and not os.path.islink(fpath):
                        rel = os.path.relpath(fpath, base_parent)
                        entries.append({
                            "abs_path": fpath,
                            "rel_path": rel.replace(os.sep, "/"),
                            "size": os.path.getsize(fpath),
                        })
        elif os.path.isfile(sel):
            entries.append({
                "abs_path": sel,
                "rel_path": os.path.basename(sel),
                "size": os.path.getsize(sel),
            })
    total = sum(e["size"] for e in entries)
    return entries, total


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------

class TransferProgress:
    """Progress snapshot passed to UI callbacks during a transfer."""

    def __init__(self, label: str, total_bytes: int) -> None:
        self.label = label
        self.total_bytes = total_bytes
        self.sent_bytes = 0
        self.file_index = 0
        self.file_count = 0
        self.done = False
        self.error: Optional[str] = None


def _check_reply(reply: dict) -> None:
    """Raise on an error/reject reply frame from the receiver."""
    kind = reply.get("kind")
    if kind == c.MSG_OK or kind == c.MSG_PONG:
        return
    if kind == c.MSG_PAIR_REJECT:
        raise TransferRejected("Remote device rejected the connection")
    if kind == c.MSG_PAIR_REQUEST:
        raise TransferRejected("Waiting for the remote device to accept pairing")
    if kind == c.MSG_ERROR:
        raise ProtocolError(f"receiver error: {reply.get('detail', 'unknown')}")
    raise ProtocolError(f"unexpected reply: {kind}")


def send_text(
    ip: str, port: int, text: str, *,
    sender_id: str = "", sender_name: str = "", sender_port: int = 0,
    on_pair_accepted: Optional[Callable[[dict], None]] = None,
) -> None:
    """Connect, (auto-)pair, deliver a text payload, close."""
    with _connect_and_identify(ip, port, sender_id=sender_id, sender_name=sender_name,
                               sender_port=sender_port,
                               on_pair_accepted=on_pair_accepted) as (sock, peer):
        send_json(sock, {"kind": c.MSG_TEXT, "text": text})
        reply = recv_json(sock)
        _check_reply(reply)


def send_files(
    ip: str,
    port: int,
    selection: list[str],
    on_progress: Optional[Callable[[TransferProgress], None]] = None,
    *,
    sender_id: str = "",
    sender_name: str = "",
    sender_port: int = 0,
    on_pair_accepted: Optional[Callable[[dict], None]] = None,
    chunk: int = DEFAULT_CHUNK,
) -> int:
    """Send files/folders. Returns number of files delivered."""
    entries, total = collect_files(selection)
    if not entries:
        raise ProtocolError("nothing to send")
    # A "batch" (folder OR multiple files) is wrapped in FOLDER_BEGIN/END so
    # the receiver groups it into a single notification; a lone file stays
    # standalone.
    is_batch = len(entries) > 1 or any(os.path.isdir(os.path.abspath(s)) for s in selection)

    progress = TransferProgress("Preparing…", total)
    progress.file_count = len(entries)

    with _connect_and_identify(ip, port, sender_id=sender_id, sender_name=sender_name,
                               sender_port=sender_port,
                               on_pair_accepted=on_pair_accepted) as (sock, peer):
        if is_batch:
            send_json(sock, {"kind": c.MSG_FOLDER_BEGIN, "file_count": len(entries),
                             "total_bytes": total})
            _check_reply(recv_json(sock))

        for idx, entry in enumerate(entries):
            send_json(sock, {
                "kind": c.MSG_FILE_BEGIN,
                "name": os.path.basename(entry["rel_path"]),
                "rel_path": entry["rel_path"],
                "size": entry["size"],
            })
            _check_reply(recv_json(sock))

            sent = 0
            progress.label = entry["rel_path"]
            progress.file_index = idx + 1
            if on_progress:
                on_progress(progress)
            with open(entry["abs_path"], "rb") as f:
                while True:
                    data = f.read(chunk)
                    if not data:
                        break
                    send_frame(sock, data)
                    sent += len(data)
                    progress.sent_bytes += len(data)
                    if on_progress:
                        on_progress(progress)
            if sent != entry["size"]:
                raise ProtocolError(f"size mismatch sending {entry['rel_path']}")
            send_json(sock, {"kind": c.MSG_FILE_END, "rel_path": entry["rel_path"]})
            _check_reply(recv_json(sock))

        if is_batch:
            send_json(sock, {"kind": c.MSG_FOLDER_END, "file_count": len(entries)})
            _check_reply(recv_json(sock))

    progress.done = True
    progress.label = f"Sent {len(entries)} file(s)"
    if on_progress:
        on_progress(progress)
    return len(entries)


def ping(ip: str, port: int, timeout: float = c.HEARTBEAT_TIMEOUT,
         sender_id: str = "", sender_name: str = "", sender_port: int = 0) -> bool:
    """One-shot health check; True iff a pong comes back in time."""
    try:
        with _connect_and_identify(ip, port, timeout=timeout,
                                   sender_id=sender_id, sender_name=sender_name,
                                   sender_port=sender_port, probe=True) as (sock, peer):
            send_json(sock, {"kind": c.MSG_PING})
            sock.settimeout(timeout)
            reply = recv_json(sock)
            return reply.get("kind") == c.MSG_PONG
    except (OSError, ProtocolError):
        return False


# ---------------------------------------------------------------------------
# Connection setup shared by send paths
# ---------------------------------------------------------------------------

class _ConnCtx:
    """Context manager result: open socket + verified peer identity."""

    def __init__(self, sock: socket.socket, peer: dict) -> None:
        self.sock = sock
        self.peer = peer

    def __enter__(self):
        return (self.sock, self.peer)

    def __exit__(self, *exc) -> bool:
        try:
            self.sock.close()
        except OSError:
            pass
        return False


def _connect_and_identify(
    ip: str, port: int, timeout: float = RECV_TIMEOUT,
    sender_id: str = "", sender_name: str = "", sender_port: int = 0,
    probe: bool = False,
    on_pair_accepted: Optional[Callable[[dict], None]] = None,
):
    """Connect + pairing handshake.

    Flow (mutual pairing):
      * Receiver already trusts us  -> immediate PAIR_ACCEPT.
      * Receiver does not know us   -> it shows its user a prompt; we block
        here for up to PAIR_NEGOTIATION_TIMEOUT (longer than the receiver's
        own prompt timeout) until it answers.
      * On PAIR_ACCEPT the receiver's identity {device_id, name, port} is
        handed to `on_pair_accepted` so the SENDER also persists the pairing
        — pairing must end up trusted on both devices.
    """
    sock = socket.create_connection((ip, port), timeout=timeout)
    sock.settimeout(timeout)
    try:
        send_json(sock, {
            "kind": c.MSG_PAIR_REQUEST,
            "device_id": sender_id,
            "name": sender_name,
            "port": sender_port,
            "probe": probe,
        })
        if not probe:
            # The receiver may keep the connection open while its user answers
            # the pairing dialog; wait longer than the receiver's prompt
            # timeout so the FIRST attempt survives the full dialog.
            sock.settimeout(max(timeout, c.PAIR_NEGOTIATION_TIMEOUT))
        reply = recv_json(sock)
        kind = reply.get("kind")
        if kind == c.MSG_PAIR_ACCEPT:
            peer = {
                "device_id": str(reply.get("device_id") or ""),
                "name": str(reply.get("name") or ""),
                "ip": ip,
                "port": int(reply.get("port") or 0),
            }
            log.info("pairing accepted by %s (%s:%s)", peer["name"], ip, peer["port"])
            if on_pair_accepted is not None and not probe:
                try:
                    on_pair_accepted(peer)
                except Exception:  # noqa: BLE001
                    log.exception("on_pair_accepted callback failed")
            return _ConnCtx(sock, peer)
        if kind == c.MSG_PAIR_REJECT:
            detail = str(reply.get("detail") or "Remote device rejected the connection")
            log.info("pairing rejected by %s (%s): %s", sender_name, ip, detail)
            sock.close()
            raise TransferRejected(detail)
        raise ProtocolError(f"unexpected handshake reply: {kind}")
    except Exception:
        try:
            sock.close()
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Receiving
# ---------------------------------------------------------------------------

class TransferServer:
    """TCP listener that accepts ShareG connections and dispatches payloads.

    Pairing flow on an incoming connection:
      1. Sender sends PAIR_REQUEST{device_id,name}.
      2. If store says paired  -> reply PAIR_ACCEPT, proceed silently.
         If store says blocked -> reply PAIR_REJECT, close.
         Otherwise             -> invoke on_pair_request callback. It returns
                                  True (accept) / False (reject); reply
                                  accordingly and persist the decision.
    """

    def __init__(
        self,
        pairing_store: PairingStore,
        on_pair_request: Callable[..., bool],  # (device_id, name, ip, port) -> bool
        on_text: Callable[[str, str], None],                 # (sender_name, text)
        on_files: Callable[[str, str, list[str]], None],     # (sender_name, folder, files)
        on_transfer_progress: Optional[Callable[[TransferProgress], None]] = None,
        host: str = "0.0.0.0",
        port: int = c.TRANSFER_PORT,
        own_device_id: str = "",
        own_name: str = "",
    ) -> None:
        self.pairing_store = pairing_store
        self.on_pair_request = on_pair_request
        self.on_text = on_text
        self.on_files = on_files
        self.on_transfer_progress = on_transfer_progress
        self.host = host
        self.port = port
        self.own_device_id = own_device_id
        self.own_name = own_name
        self.own_port = 0  # actual bound port, set by start()

        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._server_sock: Optional[socket.socket] = None
        self._conn_threads: list[threading.Thread] = []
        self._conn_socks: list[socket.socket] = []
        self._conn_lock = threading.Lock()
        # De-duplicate simultaneous pairing prompts for the same remote device:
        # {device_id: threading.Event} - later connection threads wait on the
        # same event instead of raising a second dialog.
        self._pending_prompts: Dict[str, threading.Event] = {}
        self._pending_prompts_lock = threading.Lock()

    # ------------------------------------------------------------------ lifecycle

    def start(self) -> None:
        if self._thread:
            return
        self._stop.clear()
        self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if sys.platform == "win32" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            # Prevent a second instance from silently double-binding this port
            # (Windows SO_REUSEADDR semantics allow it, which misroutes conns).
            self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self._server_sock.bind((self.host, self.port))
        except OSError:
            # Preferred port taken (another ShareG instance): use an ephemeral
            # port and announce it via discovery instead.
            self._server_sock.bind((self.host, 0))
        self._server_sock.listen(8)
        self._server_sock.settimeout(0.5)
        self.port = self._server_sock.getsockname()[1]
        self.own_port = self.port
        self._thread = threading.Thread(
            target=self._accept_loop, name="shareg-transfer-server", daemon=True
        )
        self._thread.start()
        log.info("ShareG transfer server listening on %s:%d", self.host, self.port)

    def stop(self, join_timeout: float = 3.0) -> None:
        """Stop the listener and terminate all active connection handlers."""
        self._stop.set()
        sock, self._server_sock = self._server_sock, None
        if sock:
            try:
                sock.close()  # unblocks the accept loop immediately
            except OSError:
                pass
        with self._conn_lock:
            threads = list(self._conn_threads)
            socks = list(self._conn_socks)
        for s in socks:  # unblock handlers parked in recv()/prompt paths
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                s.close()
            except OSError:
                pass
        for t in threads:
            if t is not threading.current_thread():
                t.join(timeout=join_timeout)
        with self._conn_lock:
            self._conn_threads = [t for t in self._conn_threads if t.is_alive()]
        self._thread = None
        log.info("ShareG transfer server stopped")

    def threads(self) -> list:
        """Live thread objects owned by this server (for lifecycle tests)."""
        with self._conn_lock:
            conns = list(self._conn_threads)
        live = [t for t in ([self._thread] + conns) if t and t.is_alive()]
        return live

    # ------------------------------------------------------------------ accept loop

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                conn, addr = self._server_sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            t = threading.Thread(
                target=self._handle_connection,
                args=(conn, addr),
                name=f"shareg-conn-{addr[0]}",
                daemon=True,
            )
            with self._conn_lock:
                self._conn_threads.append(t)
            t.start()

    # ------------------------------------------------------------------ one connection

    def _handle_connection(self, conn: socket.socket, addr) -> None:
        peer_name = addr[0]
        with self._conn_lock:
            self._conn_socks.append(conn)
        try:
            conn.settimeout(HANDSHAKE_TIMEOUT)
            hello = recv_json(conn)
            if hello.get("kind") != c.MSG_PAIR_REQUEST:
                raise ProtocolError("expected pair_request handshake")
            device_id = str(hello.get("device_id") or "")
            peer_name = str(hello.get("name") or addr[0])
            if not device_id:
                raise ProtocolError("handshake missing device_id")
            if device_id == self.own_device_id:
                # Our own announcement looped back / self-connect: ignore.
                log.debug("ignoring self-connection from %s", addr[0])
                return

            probe = bool(hello.get("probe"))
            peer_port = int(hello.get("port") or 0)
            decision = self._pairing_decision(device_id, peer_name, addr[0],
                                              port=peer_port, probe=probe)
            if decision is False:
                send_json(conn, {"kind": c.MSG_PAIR_REJECT})
                return
            send_json(conn, {"kind": c.MSG_PAIR_ACCEPT, "device_id": self._own_device_id(),
                             "name": self._own_name(), "port": self.own_port})

            conn.settimeout(RECV_TIMEOUT)
            self._dispatch(conn, device_id, peer_name)
        except (ProtocolError, OSError, ValueError) as e:
            log.debug("connection from %s ended: %s", peer_name, e)
        except Exception:  # noqa: BLE001
            log.exception("unexpected error handling connection from %s", peer_name)
        finally:
            with self._conn_lock:
                if conn in self._conn_socks:
                    self._conn_socks.remove(conn)
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                conn.close()
            except OSError:
                pass

    def _pairing_decision(self, device_id: str, name: str, ip: str,
                          port: int = 0, probe: bool = False) -> bool:
        """Resolve pairing for an inbound connection (may prompt the user).

        Persistence is owned by the on_pair_request callback (the backend),
        which stores the decision on THIS device; the requesting device stores
        the mirror entry when it receives PAIR_ACCEPT - pairing therefore ends
        up trusted on both sides and is saved exactly once per side.

        Heartbeat probes from unknown devices are answered without prompting
        and without persisting anything: a probe only reveals presence, which
        discovery already announces publicly.

        Simultaneous connections from the same unknown device share one prompt
        (later threads wait on the first thread's answer event) so the user
        never sees duplicate dialogs for one request.
        """
        store = self.pairing_store
        if store.is_paired(device_id):
            return True
        if store.is_blocked(device_id):
            return False
        if probe:
            return True

        # Re-use an in-flight prompt for this device, if any.
        with self._pending_prompts_lock:
            done = self._pending_prompts.get(device_id)
            if done is None:
                done = threading.Event()
                self._pending_prompts[device_id] = done
                owner = True
            else:
                owner = False
        if not owner:
            log.info("pairing prompt for %s already in flight; joining",
                     device_id[:8])
            done.wait(timeout=c.PAIR_PROMPT_TIMEOUT + 10)
            return store.is_paired(device_id)  # owner persisted the outcome

        try:
            accepted = bool(self.on_pair_request(device_id, name, ip, port))
            log.info("pairing %s for %s (%s)",
                     "accepted" if accepted else "rejected", name, ip)
            return accepted
        finally:
            with self._pending_prompts_lock:
                self._pending_prompts.pop(device_id, None)
                done.set()

    def _own_device_id(self) -> str:
        return self.own_device_id

    def _own_name(self) -> str:
        return self.own_name

    # ------------------------------------------------------------------ dispatch

    def _dispatch(self, conn: socket.socket, device_id: str, peer_name: str) -> None:
        while True:
            msg = recv_json(conn)
            kind = msg.get("kind")
            if kind == c.MSG_PING:
                send_json(conn, {"kind": c.MSG_PONG, "device_id": self._own_device_id(),
                                 "name": self._own_name()})
            elif kind == c.MSG_TEXT:
                text = str(msg.get("text") or "")
                send_json(conn, {"kind": c.MSG_OK})
                try:
                    self.on_text(peer_name, text)
                except Exception:  # noqa: BLE001
                    log.exception("on_text callback failed")
            elif kind == c.MSG_FILE_BEGIN:
                conn.settimeout(RECV_TIMEOUT)
                self._receive_single_file(conn, msg, peer_name, standalone=True)
            elif kind == c.MSG_FOLDER_BEGIN:
                conn.settimeout(RECV_TIMEOUT)
                self._receive_folder(conn, msg, peer_name)
            else:
                raise ProtocolError(f"unexpected frame kind: {kind}")

    # ------------------------------------------------------------------ receivers

    def _reply_ok(self, conn: socket.socket) -> None:
        send_json(conn, {"kind": c.MSG_OK})

    def _safe_dest_path(self, folder: str, rel_path: str) -> str:
        """Join + normalize, refusing path traversal outside `folder`."""
        rel_norm = rel_path.replace("\\", "/")
        dest = os.path.abspath(os.path.join(folder, rel_norm))
        base = os.path.abspath(folder)
        if dest != base and not dest.startswith(base + os.sep):
            raise ProtocolError(f"unsafe destination path: {rel_path!r}")
        return dest

    def _receive_single_file(self, conn, meta: dict, peer_name: str, *, standalone: bool) -> None:
        rel_path = str(meta.get("rel_path") or meta.get("name") or "unnamed")
        size = int(meta.get("size") or 0)
        from .paths import downloads_dir

        dest = self._safe_dest_path(downloads_dir(), rel_path)
        os.makedirs(os.path.dirname(dest), exist_ok=True)

        progress = TransferProgress(rel_path, size)
        self._reply_ok(conn)

        received = 0
        with open(dest, "wb") as f:
            while received < size:
                chunk = recv_frame(conn)
                f.write(chunk)
                received += len(chunk)
                progress.sent_bytes = received
                if self.on_transfer_progress:
                    try:
                        self.on_transfer_progress(progress)
                    except Exception:  # noqa: BLE001
                        log.exception("progress callback failed")
        if received != size:
            raise ProtocolError(f"incomplete file {rel_path}: {received}/{size}")
        self._reply_ok(conn)

        end = recv_json(conn)
        if end.get("kind") != c.MSG_FILE_END:
            raise ProtocolError("expected file_end")

        files = [dest]
        if standalone:
            try:
                self.on_files(peer_name, os.path.dirname(dest), files)
            except Exception:  # noqa: BLE001
                log.exception("on_files callback failed")
            # Standalone transfer: loop back for further requests (pings etc.)
            # Actually after one standalone file, sender closes; return to keep
            # dispatch simple — sender drives connection lifetime.
            return

    def _receive_folder(self, conn, meta: dict, peer_name: str) -> None:
        count = int(meta.get("file_count") or 0)
        from .paths import downloads_dir

        base = downloads_dir()
        received_files: list[str] = []
        self._reply_ok(conn)

        for _ in range(count):
            fm = recv_json(conn)
            if fm.get("kind") != c.MSG_FILE_BEGIN:
                raise ProtocolError("expected file_begin in folder")
            rel_path = str(fm.get("rel_path") or fm.get("name") or "unnamed")
            size = int(fm.get("size") or 0)
            dest = self._safe_dest_path(base, rel_path)
            os.makedirs(os.path.dirname(dest), exist_ok=True)

            progress = TransferProgress(rel_path, size)
            self._reply_ok(conn)

            received = 0
            with open(dest, "wb") as f:
                while received < size:
                    chunk = recv_frame(conn)
                    f.write(chunk)
                    received += len(chunk)
                    progress.sent_bytes = received
                    if self.on_transfer_progress:
                        try:
                            self.on_transfer_progress(progress)
                        except Exception:  # noqa: BLE001
                            log.exception("progress callback failed")
            if received != size:
                raise ProtocolError(f"incomplete file {rel_path}: {received}/{size}")
            self._reply_ok(conn)
            end = recv_json(conn)
            if end.get("kind") != c.MSG_FILE_END:
                raise ProtocolError("expected file_end in folder")
            received_files.append(dest)

        tail = recv_json(conn)
        if tail.get("kind") != c.MSG_FOLDER_END:
            raise ProtocolError("expected folder_end")
        self._reply_ok(conn)

        try:
            self.on_files(peer_name, base, received_files)
        except Exception:  # noqa: BLE001
            log.exception("on_files callback failed")
