"""UDP multicast discovery for ShareG.

A device announces itself periodically on a multicast group; every device
also listens on the same group. Announcements carry name, IP, port, and a
stable unique device ID, so peers build a live map of the local network.

Pure standard library; identical behavior on Windows / Linux / Android.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import socket
import struct
import threading
import time
import uuid
from typing import Callable, Dict, Optional

from . import constants as c

log = logging.getLogger(__name__)

# Retransmit an announcement this many times per interval for UDP robustness.
ANNOUNCE_BURST = 2


def get_local_ip() -> str:
    """Best-effort local LAN IP without sending traffic (UDP 'connect' trick).

    Returns "127.0.0.1" when no route exists (e.g. airplane-mode device).
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.settimeout(0)
        s.connect(("10.255.255.255", 1))  # never actually sent (UDP connect)
        ip = s.getsockname()[0]
    except OSError:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip


class DiscoveryService:
    """Periodic UDP-multicast announcer + listener maintaining a peer map.

    Callbacks (invoked on the discovery thread):
        on_peer_seen(peer: dict)        — new peer or peer info updated
        on_peer_expired(device_id: str) — peer not heard from recently
    """

    def __init__(
        self,
        device_name: str,
        transfer_port: int = c.TRANSFER_PORT,
        on_peer_seen: Optional[Callable[[dict], None]] = None,
        on_peer_expired: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.device_name = device_name
        self.transfer_port = transfer_port
        self.device_id = self._load_or_create_device_id()
        self.on_peer_seen = on_peer_seen
        self.on_peer_expired = on_peer_expired

        self._peers: Dict[str, dict] = {}
        self._peers_lock = threading.Lock()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    # ------------------------------------------------------------------ peer map

    def peers(self) -> Dict[str, dict]:
        with self._peers_lock:
            return {did: dict(p) for did, p in self._peers.items()}

    def get_peer(self, device_id: str) -> Optional[dict]:
        with self._peers_lock:
            peer = self._peers.get(device_id)
            return dict(peer) if peer else None

    # ------------------------------------------------------------------ identity

    @staticmethod
    def _identity_path() -> str:
        from .paths import identity_store_path

        return identity_store_path()

    def _load_or_create_device_id(self) -> str:
        """Persistent per-install identity: {device_id, device_name}."""
        path = self._identity_path()
        data = {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
        device_id = data.get("device_id")
        if not device_id:
            device_id = uuid.uuid4().hex
        # Keep the stored name in sync with the user-chosen/current name.
        try:
            if data.get("device_id") != device_id or data.get("device_name") != self.device_name:
                data = {"device_id": device_id, "device_name": self.device_name}
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
        except OSError:
            pass
        return device_id

    # ------------------------------------------------------------------ lifecycle

    def start(self) -> None:
        if self._threads:
            return
        log.info("discovery starting (device %s, group %s:%d)",
                 self.device_id[:8], c.MULTICAST_GROUP, c.MULTICAST_PORT)
        self._stop.clear()
        t_ann = threading.Thread(target=self._announce_loop, name="shareg-announce", daemon=True)
        t_lis = threading.Thread(target=self._listen_loop, name="shareg-discover", daemon=True)
        t_exp = threading.Thread(target=self._expiry_loop, name="shareg-expiry", daemon=True)
        self._threads = [t_ann, t_lis, t_exp]
        for t in self._threads:
            t.start()

    def stop(self) -> None:
        log.info("discovery stopping (device %s)", self.device_id[:8])
        self._stop.set()
        self._threads = []

    def threads(self) -> list:
        """Live thread objects owned by this service (for lifecycle tests)."""
        return [t for t in self._threads if t.is_alive()]

    # ------------------------------------------------------------------ announce

    def _announce_payload(self) -> bytes:
        packet = {
            "app": c.APP_NAME,
            "device_id": self.device_id,
            "name": self.device_name,
            "ip": get_local_ip(),
            "port": self.transfer_port,
            "ts": time.time(),
        }
        return json.dumps(packet).encode("utf-8")

    def _announce_loop(self) -> None:
        payload = self._announce_payload()
        last_payload_build = time.time()
        while not self._stop.is_set():
            # Rebuild payload periodically (name/IP/port may change, e.g.
            # Wi-Fi roam or a restart picking a new ephemeral port).
            if time.time() - last_payload_build > 1.0:
                payload = self._announce_payload()
                last_payload_build = time.time()
            for _ in range(ANNOUNCE_BURST):
                try:
                    self._send_multicast(payload)
                except OSError as e:
                    log.debug("announce failed: %s", e)
                if self._stop.wait(0.05):
                    return
            self._stop.wait(c.DISCOVERY_INTERVAL)

    def _send_multicast(self, payload: bytes) -> None:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            # TTL>1 for routed LANs; loop enabled so multi-device localhost
            # setups (testing, VMs) see announcements too.
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, c.DISCOVERY_TTL)
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
            try:
                s.setsockopt(
                    socket.IPPROTO_IP, socket.IP_MULTICAST_IF,
                    socket.inet_aton(get_local_ip()),
                )
            except OSError:
                pass  # default interface is fine
            s.sendto(payload, (c.MULTICAST_GROUP, c.MULTICAST_PORT))
        finally:
            s.close()

    # ------------------------------------------------------------------ listen

    def _listen_loop(self) -> None:
        while not self._stop.is_set():
            sock = None
            try:
                sock = self._make_listener_socket()
            except OSError as e:
                log.debug("listener socket setup failed: %s", e)
            if sock is None:
                if self._stop.wait(3.0):
                    return
                continue
            try:
                self._receive_loop(sock)
            except OSError as e:
                log.debug("discovery receive loop error: %s", e)
            finally:
                try:
                    sock.close()
                except OSError:
                    pass
            if self._stop.wait(1.0):  # socket died; rebuild after a pause
                return

    def _make_listener_socket(self) -> socket.socket:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # SO_REUSEPORT where available (Linux/Android) lets several ShareG
        # instances on one machine all receive announcements (useful in tests).
        reuseport = getattr(socket, "SO_REUSEPORT", None)
        if reuseport is not None:
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except OSError:
                pass
        sock.bind(("", c.MULTICAST_PORT))

        # Join multicast group on every IPv4 interface (Windows allows only
        # one group membership per socket, Linux allows several — the loop
        # below works on both).
        joined = False
        ifindex = 1
        for iface in _local_ipv4s():
            try:
                mreq = socket.inet_aton(c.MULTICAST_GROUP) + socket.inet_aton(iface)
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
                joined = True
            except OSError:
                continue
        if not joined:
            mreq = struct.pack("4sl", socket.inet_aton(c.MULTICAST_GROUP), socket.INADDR_ANY)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        # On Linux this binds membership to a specific ifindex; harmless elsewhere.
        try:
            sock.setsockopt(socket.IPPROTO_IP, getattr(socket, "IP_MULTICAST_ALL", 71), 0)
        except OSError:
            pass
        sock.settimeout(0.5)
        return sock

    def _receive_loop(self, sock: socket.socket) -> None:
        while not self._stop.is_set():
            try:
                data, addr = sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                return
            peer = self._parse_announce(data, addr)
            if peer is None or peer["device_id"] == self.device_id:
                continue  # not ShareG, or our own looped-back announcement
            self._record_peer(peer)

    def _parse_announce(self, data: bytes, addr) -> Optional[dict]:
        try:
            msg = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            log.debug("discarded malformed announce from %s", addr)
            return None
        if not isinstance(msg, dict) or msg.get("app") != c.APP_NAME:
            return None
        device_id = msg.get("device_id")
        name = msg.get("name")
        port = msg.get("port")
        if not device_id or not name or not isinstance(port, int):
            log.debug("discarded incomplete announce from %s", addr)
            return None
        return {
            "device_id": device_id,
            "name": str(name),
            "ip": str(msg.get("ip") or addr[0]),
            "port": int(port),
            "last_seen": time.time(),
        }

    def _record_peer(self, peer: dict) -> None:
        device_id = peer["device_id"]
        callback = None
        with self._peers_lock:
            existing = self._peers.get(device_id)
            if (existing is None or existing.get("name") != peer["name"]
                    or existing.get("ip") != peer["ip"] or existing.get("port") != peer["port"]):
                self._peers[device_id] = peer
                callback = self.on_peer_seen
            else:
                existing["last_seen"] = peer["last_seen"]
                existing["port"] = peer["port"]
        if callback:
            try:
                callback(peer)
            except Exception:  # noqa: BLE001 — UI callback must not kill discovery
                log.exception("on_peer_seen callback failed")

    # ------------------------------------------------------------------ expiry

    def _expiry_loop(self) -> None:
        while not self._stop.wait(1.0):
            now = time.time()
            expired = []
            with self._peers_lock:
                for did, peer in list(self._peers.items()):
                    if now - peer.get("last_seen", 0) > c.PEER_TIMEOUT:
                        expired.append(did)
                        del self._peers[did]
            if expired and self.on_peer_expired:
                for did in expired:
                    try:
                        self.on_peer_expired(did)
                    except Exception:  # noqa: BLE001
                        log.exception("on_peer_expired callback failed")


def _local_ipv4s() -> list[str]:
    """Enumerate local IPv4 addresses (for multicast group joins)."""
    ips: list[str] = []
    try:
        host = socket.gethostname()
        for info in socket.getaddrinfo(host, None, socket.AF_INET):
            ip = info[4][0]
            try:
                if not ipaddress.ip_address(ip).is_loopback:
                    ips.append(ip)
            except ValueError:
                continue
    except OSError:
        pass
    local = get_local_ip()
    if local not in ips and local != "127.0.0.1":
        ips.append(local)
    return ips
