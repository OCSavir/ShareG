"""TCP text transfer between discovered nodes."""

from conftest import wait_for


def test_text_transfer(make_node):
    a = make_node("Alpha")
    b = make_node("Beta")

    wait_for(lambda: b.device_id in a.peers, timeout=15, msg="discovery")

    a.send_text_to(b.device_id, "hello from Alpha")
    wait_for(lambda: b._test_received["texts"], timeout=10, msg="text received")
    sender, text = b._test_received["texts"][0]
    assert text == "hello from Alpha"
    assert sender == "Alpha"


def test_text_transfer_unicode_and_large(make_node):
    a = make_node("Alpha")
    b = make_node("Beta")
    wait_for(lambda: b.device_id in a.peers, timeout=15, msg="discovery")

    big = "shArG-üñíçødé-🚀 " * 20000  # ~600 KB
    a.send_text_to(b.device_id, big)
    wait_for(lambda: b._test_received["texts"], timeout=15, msg="big text received")
    assert b._test_received["texts"][0][1] == big


def test_send_to_unknown_device_raises(make_node):
    a = make_node("Alpha")
    import pytest
    from shareg.backend import ShareGUserError
    with pytest.raises(ShareGUserError):
        a.send_text_to("nonexistent-device-id", "x")
