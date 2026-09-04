"""UDP multicast discovery: devices detect each other; stale peers expire."""

import time

from conftest import wait_for


def test_two_devices_discover_each_other(make_node):
    a = make_node("Alpha")
    b = make_node("Beta")

    wait_for(lambda: "Beta" in {p["name"] for p in a.peers.values()} and
                      "Alpha" in {p["name"] for p in b.peers.values()},
             timeout=15, msg="mutual discovery")

    pa = next(p for p in a.peers.values() if p["name"] == "Beta")
    pb = next(p for p in b.peers.values() if p["name"] == "Alpha")
    assert pa["device_id"] == b.device_id
    assert pb["device_id"] == a.device_id
    assert pa["port"] == b.transfer_server.port
    assert pb["port"] == a.transfer_server.port


def test_peer_expiry_marks_device_gone(make_node):
    a = make_node("Alpha")
    b = make_node("Beta")

    wait_for(lambda: b.device_id in a.peers, timeout=15, msg="discovery")
    # Stop b's announcer; Alpha must drop it within PEER_TIMEOUT + slack.
    b.discovery.stop()
    wait_for(lambda: b.device_id not in a.peers, timeout=20, msg="peer expiry")


def test_announce_payload_shape(make_node):
    a = make_node("Alpha")
    payload = a.discovery._announce_payload()
    import json
    msg = json.loads(payload.decode())
    assert msg["app"] == "ShareG"
    assert msg["device_id"] == a.device_id
    assert msg["name"] == "Alpha"
    assert isinstance(msg["port"], int)
