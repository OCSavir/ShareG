"""Clean shutdown: stop() terminates all threads; no stragglers remain."""

import threading
import time

from conftest import wait_for


def _live(t):
    """A thread object may be dead but not yet joined/removed; check both."""
    return t.is_alive()


def test_stop_joins_all_network_threads(make_node):
    a = make_node("Alpha")
    b = make_node("Beta")
    wait_for(lambda: b.device_id in a.peers, timeout=15, msg="discovery")

    a_threads = a.threads()
    names = {t.name for t in a_threads}
    assert {"shareg-announce", "shareg-discover", "shareg-expiry",
            "shareg-transfer-server", "shareg-heartbeat"} <= names, names
    assert all(t.is_alive() for t in a_threads)

    a.stop()
    wait_for(lambda: not any(t.is_alive() for t in a_threads),
             timeout=8, msg="all a threads joined after stop()")

    # b is untouched: its owned threads are all still alive.
    assert b.threads() and all(t.is_alive() for t in b.threads())


def test_stop_is_idempotent_and_reversible(make_node):
    a = make_node("Alpha")
    a.stop()
    a.stop()  # must not raise
    a.start()  # restart works (fresh threads, same store)
    wait_for(lambda: a.transfer_server.port > 0, timeout=5, msg="restarted")
    a.stop()


def test_process_can_exit_after_stop(make_node):
    """Nothing ShareG owns may keep the interpreter alive: all its threads
    must be daemon threads so a plain exit() terminates the process."""
    a = make_node("Alpha")
    b = make_node("Beta")
    wait_for(lambda: b.device_id in a.peers, timeout=15, msg="discovery")

    a.stop()
    b.stop()
    deadline = time.time() + 5
    while time.time() < deadline:
        stragglers = [t for t in threading.enumerate()
                      if t.name.startswith("shareg-") and t.is_alive()]
        if not stragglers:
            break
        time.sleep(0.05)
    for t in threading.enumerate():
        if t.name.startswith("shareg-"):
            assert t.daemon, f"non-daemon ShareG thread keeps process alive: {t.name}"
