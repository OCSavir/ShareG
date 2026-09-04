"""Shared fixtures for ShareG tests: isolated data dirs and node factory."""

import os
import socket
import sys
import time
import uuid

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shareg.backend import ShareGBackend  # noqa: E402


@pytest.fixture()
def data_dir(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    old = os.environ.get("SHAREG_DATA_DIR")
    os.environ["SHAREG_DATA_DIR"] = str(d)
    yield str(d)
    if old is not None:
        os.environ["SHAREG_DATA_DIR"] = old
    else:
        os.environ.pop("SHAREG_DATA_DIR", None)


@pytest.fixture()
def make_node(tmp_path):
    """Factory: create isolated ShareGBackend nodes with auto-answer pairing.

    _make(name, auto_accept=True, data_dir=None) — pass data_dir to reuse a
    store across "restarts".
    """
    nodes = []

    def _make(name: str, auto_accept: bool = True, data_dir=None):
        d = data_dir or (tmp_path / f"data_{name}_{uuid.uuid4().hex[:6]}")
        os.makedirs(d, exist_ok=True)
        old = os.environ.get("SHAREG_DATA_DIR")
        os.environ["SHAREG_DATA_DIR"] = str(d)
        try:
            node = ShareGBackend(device_name=name)
        finally:
            if old is not None:
                os.environ["SHAREG_DATA_DIR"] = old
            else:
                os.environ.pop("SHAREG_DATA_DIR", None)

        def on_pair_prompt(device_id, peer_name, ip, box):
            node._test_pair_prompts.append(peer_name)
            box["answer"] = auto_accept
            box["event"].set()

        node._test_received = {"texts": [], "files": []}
        node._test_pair_prompts = []
        node._data_dir = str(d)
        node.set_ui_callbacks(
            on_pair_prompt=on_pair_prompt,
            on_text_received=lambda s, t: node._test_received["texts"].append((s, t)),
            on_files_received=lambda s, f, fs: node._test_received["files"].append((s, f, fs)),
        )
        node.start()
        nodes.append(node)
        return node

    yield _make
    for n in nodes:
        try:
            n.stop()
        except Exception:
            pass


def wait_for(cond, timeout=10.0, interval=0.05, msg="condition"):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cond():
            return True
        time.sleep(interval)
    raise AssertionError(f"timeout waiting for {msg}")


import pytest as _pytest


@_pytest.fixture()
def socket_pair():
    """Connected TCP socket pair for protocol unit tests."""
    import socket

    a, b = socket.socketpair()
    a.settimeout(5)
    b.settimeout(5)
    yield a, b
    a.close()
    b.close()


@_pytest.fixture()
def make_node_v2(tmp_path):
    """Factory matching the new backend signature (port-aware pair prompt)."""
    import uuid as _uuid
    nodes = []

    def _make(name: str, auto_accept: bool = True):
        d = tmp_path / f"v2_{name}_{_uuid.uuid4().hex[:6]}"
        os.makedirs(d, exist_ok=True)
        old = os.environ.get("SHAREG_DATA_DIR")
        os.environ["SHAREG_DATA_DIR"] = str(d)
        try:
            node = ShareGBackend(device_name=name)
        finally:
            if old is not None:
                os.environ["SHAREG_DATA_DIR"] = old
            else:
                os.environ.pop("SHAREG_DATA_DIR", None)

        def on_pair_prompt(device_id, peer_name, ip, box, port=0):
            node._test_pair_prompts.append(peer_name)
            box["answer"] = auto_accept
            box["event"].set()

        node._test_received = {"texts": [], "files": []}
        node._test_pair_prompts = []
        node._data_dir = str(d)
        node.set_ui_callbacks(
            on_pair_prompt=on_pair_prompt,
            on_text_received=lambda s, t: node._test_received["texts"].append((s, t)),
            on_files_received=lambda s, f, fs: node._test_received["files"].append((s, f, fs)),
        )
        node.start()
        nodes.append(node)
        return node

    yield _make
    for n in nodes:
        try:
            n.stop()
        except Exception:
            pass
