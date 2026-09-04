"""Connection-health monitoring: offline paired devices flip to Disconnected."""

import time

from conftest import wait_for


def test_status_connected_then_disconnected(make_node):
    a = make_node("Alpha")
    b = make_node("Beta")
    wait_for(lambda: b.device_id in a.peers, timeout=15, msg="discovery")

    # Pair first so heartbeat tracks the device.
    a.send_text_to(b.device_id, "pair")
    wait_for(lambda: b._test_received["texts"], timeout=10, msg="text")

    # Alpha's heartbeat should mark Beta connected (ping succeeds).
    a.heartbeat.interval = 1.0
    wait_for(lambda: a.heartbeat.status_of(b.device_id) == "connected",
             timeout=15, msg="status connected")

    # Kill Beta: Alpha must flip it to disconnected automatically.
    b.stop()
    wait_for(lambda: a.heartbeat.status_of(b.device_id) == "disconnected",
             timeout=20, msg="status disconnected after offline")


def test_unresolvable_device_reports_disconnected(make_node):
    a = make_node("Alpha")
    # Inject a fake paired device that is not on the network.
    a.pairing.pair("ghost-id", "Ghost", "0.0.0.0")
    a.heartbeat.interval = 0.5
    wait_for(lambda: a.heartbeat.status_of("ghost-id") == "disconnected",
             timeout=10, msg="ghost device disconnected")


def test_paired_devices_view(make_node):
    a = make_node("Alpha")
    b = make_node("Beta")
    wait_for(lambda: b.device_id in a.peers, timeout=15, msg="discovery")
    a.send_text_to(b.device_id, "pair")
    wait_for(lambda: b._test_received["texts"], timeout=10, msg="text")

    paired = {d["device_id"]: d for d in a.paired_devices()}
    assert b.device_id in paired
    assert paired[b.device_id]["name"] == "Beta"
