"""Pairing flow: first-time prompt, accept/reject, persistence, auto-reconnect."""

import os

import pytest

from conftest import wait_for


def test_first_connect_prompts_then_persists(make_node):
    a = make_node("Alpha")
    b = make_node("Beta")
    wait_for(lambda: b.device_id in a.peers, timeout=15, msg="discovery")

    # First send: Beta must show exactly one pairing prompt.
    a.send_text_to(b.device_id, "first contact")
    wait_for(lambda: b._test_received["texts"], timeout=10, msg="text")
    assert len(b._test_pair_prompts) == 1

    # Decision persisted on disk in Beta's data dir.
    entry = b.pairing.get(a.device_id)
    assert entry and entry["status"] == "paired"
    assert entry["name"] == "Alpha"

    # Second send: no new prompt (already-paired devices connect automatically).
    a.send_text_to(b.device_id, "second contact")
    wait_for(lambda: len(b._test_received["texts"]) == 2, timeout=10, msg="second text")
    assert len(b._test_pair_prompts) == 1


def test_rejection_blocks_connection(make_node):
    a = make_node("Alpha")
    b = make_node("Beta", auto_accept=False)
    wait_for(lambda: b.device_id in a.peers, timeout=15, msg="discovery")

    from shareg.backend import ShareGUserError
    with pytest.raises(ShareGUserError):
        a.send_text_to(b.device_id, "should fail")

    # Rejection recorded as blocked on the receiver; nothing delivered.
    entry = b.pairing.get(a.device_id)
    assert entry and entry["status"] == "blocked"
    assert b._test_received["texts"] == []
    assert len(b._test_pair_prompts) == 1  # exactly one prompt, then rejected

    # Blocked devices stay blocked: retry fails with NO new prompt.
    with pytest.raises(ShareGUserError):
        a.send_text_to(b.device_id, "retry")
    assert len(b._test_pair_prompts) == 1  # unchanged


def test_pairing_survives_restart(make_node):
    a = make_node("Alpha")
    b = make_node("Beta")
    wait_for(lambda: b.device_id in a.peers, timeout=15, msg="discovery")

    a.send_text_to(b.device_id, "pair me")
    wait_for(lambda: b._test_received["texts"], timeout=10, msg="text")
    assert b.pairing.is_paired(a.device_id)

    # "Restart" Beta: same data dir, fresh backend instance.
    data_dir = b._data_dir
    b.stop()
    b2 = make_node("Beta", data_dir=data_dir)
    assert b2.pairing.is_paired(a.device_id), "pairing must survive restart"

    # And a fresh send from Alpha to the restarted Beta needs no new prompt.
    # Wait for Alpha to re-learn Beta's (possibly new) transfer port first.
    wait_for(
        lambda: (a.discovery.get_peer(b2.device_id) or {}).get("port") == b2.transfer_server.port,
        timeout=15, msg="alpha re-learns restarted beta port",
    )
    a.send_text_to(b2.device_id, "after restart")
    wait_for(lambda: b2._test_received["texts"], timeout=10, msg="text after restart")
    assert b2._test_received["texts"][0][1] == "after restart"
    assert len(b2._test_pair_prompts) == 0
