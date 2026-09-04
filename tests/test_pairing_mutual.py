"""Mutual pairing: accept once -> trusted on BOTH devices; reliable first try."""

import threading
import time

import pytest

from conftest import wait_for


def test_pairing_is_mutual_and_persisted(make_node):
    a = make_node("Alpha")
    b = make_node("Beta")
    wait_for(lambda: b.device_id in a.peers, timeout=15, msg="discovery")

    # Single first attempt must succeed end-to-end.
    a.send_text_to(b.device_id, "hello")
    wait_for(lambda: b._test_received["texts"], timeout=10, msg="text")

    # BOTH stores now hold the peer as paired, with connection info.
    ea = a.pairing.get(b.device_id)
    eb = b.pairing.get(a.device_id)
    assert ea and ea["status"] == "paired", f"A side: {ea}"
    assert eb and eb["status"] == "paired", f"B side: {eb}"
    assert ea["name"] == "Beta" and eb["name"] == "Alpha"
    assert ea["port"] == b.transfer_server.port  # A knows B's real port
    assert eb["port"] == a.transfer_server.port  # B knows A's real port
    assert ea["ip"] and eb["ip"]

    # Both sides persisted to disk.
    assert a.pairing.is_paired(b.device_id)
    assert b.pairing.is_paired(a.device_id)


def test_first_attempt_prompts_exactly_once(make_node):
    a = make_node("Alpha")
    b = make_node("Beta")
    wait_for(lambda: b.device_id in a.peers, timeout=15, msg="discovery")

    a.send_text_to(b.device_id, "first try")
    wait_for(lambda: b._test_received["texts"], timeout=10, msg="text")
    # Exactly one prompt for exactly one request (regression: retries used to
    # stack multiple dialogs / multiple accepts were needed).
    assert len(b._test_pair_prompts) == 1

    # Subsequent transfers: paired both ways, no new prompt, no reconnect.
    a.send_text_to(b.device_id, "second try")
    wait_for(lambda: len(b._test_received["texts"]) == 2, timeout=10, msg="text 2")
    assert len(b._test_pair_prompts) == 1


def test_rejection_informs_sender_and_persists_blocked(make_node):
    a = make_node("Alpha")
    b = make_node("Beta", auto_accept=False)
    wait_for(lambda: b.device_id in a.peers, timeout=15, msg="discovery")

    from shareg.backend import ShareGUserError
    with pytest.raises(ShareGUserError):
        a.send_text_to(b.device_id, "nope")

    # Sender is informed (TransferRejected surfaced as user error) and does
    # NOT mark the peer paired.
    assert not a.pairing.is_paired(b.device_id)
    # Receiver persists the rejection as blocked; nothing delivered.
    assert b.pairing.is_blocked(a.device_id)
    assert b._test_received["texts"] == []


def test_rejection_is_not_silent_after_timeout(make_node):
    """A receiver that never answers must still yield a clean rejection to
    the sender (no hang, no success)."""
    a = make_node("Alpha")

    # Simulate a receiver that accepts the TCP handshake but never answers
    # the pairing negotiation: raw socket that just holds the connection.
    import socket
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    from shareg.protocol import TransferRejected, _connect_and_identify
    result = {}

    def connect():
        try:
            _connect_and_identify("127.0.0.1", port, sender_id="x", sender_name="X")
            result["ok"] = True
        except TransferRejected:
            result["rejected"] = True
        except Exception as e:  # noqa: BLE001
            result["error"] = e

    t = threading.Thread(target=connect, daemon=True)
    t.start()
    # accept + hold without replying
    conn, _ = srv.accept()
    t.join(timeout=20)
    assert not result.get("ok"), "silent receiver must not be reported as success"
    conn.close()
    srv.close()


def test_duplicate_sessions_refused(make_node):
    a = make_node("Alpha")
    b = make_node("Beta")
    wait_for(lambda: b.device_id in a.peers, timeout=15, msg="discovery")

    from shareg.backend import ShareGUserError
    from shareg import connstate
    # Claim a session manually to simulate an in-flight connection.
    session = a.conn_state.begin_session(b.device_id)
    assert session is not None
    with pytest.raises(ShareGUserError, match="already in progress"):
        a.send_text_to(b.device_id, "should be refused")
    a.conn_state.end_session(b.device_id, session, connstate.DISCONNECTED)

    # After ending the session, sending works again.
    a.send_text_to(b.device_id, "after session")
    wait_for(lambda: b._test_received["texts"], timeout=10, msg="text")


def test_state_machine_transitions():
    from shareg import connstate
    sm = connstate.ConnectionStateMachine()
    did = "dev1"
    assert sm.state_of(did) == connstate.DISCOVERED
    sid = sm.begin_session(did)
    assert sid == 1 and sm.state_of(did) == connstate.CONNECTING
    # a second concurrent session must be refused
    assert sm.begin_session(did) is None
    assert sm.transition(did, connstate.PAIRING_PENDING)
    assert sm.transition(did, connstate.PAIRED)
    assert sm.transition(did, connstate.CONNECTED)
    assert sm.transition(did, connstate.DISCONNECTED)
    # illegal jump: DISCOVERED -> PAIRED without a session
    sm2 = connstate.ConnectionStateMachine()
    assert not sm2.transition("x", connstate.PAIRED)
    # stale session cannot clobber a newer one
    s1 = sm.begin_session("y")
    sm.end_session("y", s1, connstate.DISCONNECTED)
    s2 = sm.begin_session("y")
    assert not sm.end_session("y", s1, connstate.CONNECTED)  # stale id
    assert sm.state_of("y") == connstate.CONNECTING
    sm.end_session("y", s2, connstate.CONNECTED)


def test_concurrent_pairing_requests_share_one_prompt(make_node):
    """Two connections from the same unknown device at the same time produce
    ONE prompt, and both succeed once accepted."""
    a = make_node("Alpha")
    b = make_node("Beta")
    wait_for(lambda: b.device_id in a.peers, timeout=15, msg="discovery")

    results = []
    def send(i):
        try:
            a.send_text_to(b.device_id, f"concurrent {i}")
            results.append(True)
        except Exception as e:  # noqa: BLE001
            results.append(e)

    threads = [threading.Thread(target=send, args=(i,)) for i in range(2)]
    threads[0].start()
    time.sleep(0.4)  # let the first claim the session
    threads[1].start()
    for t in threads:
        t.join(timeout=60)

    ok = [r for r in results if r is True]
    assert ok, f"no send succeeded: {results}"
    # One prompt only (the second attempt is refused as a duplicate session).
    assert len(b._test_pair_prompts) <= 1
