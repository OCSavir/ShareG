"""Low-level protocol unit tests: framing and manifest collection."""

import os

import pytest

from shareg import protocol
from shareg.protocol import (
    ProtocolError,
    collect_files,
    recv_frame,
    recv_json,
    send_frame,
    send_json,
)


def test_frame_roundtrip(socket_pair):
    a, b = socket_pair
    send_json(a, {"kind": "x", "n": 42})
    assert recv_json(b) == {"kind": "x", "n": 42}

    blob = os.urandom(300_000)
    send_frame(a, blob)
    assert recv_frame(b) == blob


def test_frame_too_large_rejected(socket_pair):
    a, b = socket_pair
    a.sendall((1 << 31).to_bytes(4, "big"))
    with pytest.raises(ProtocolError):
        recv_frame(b)


def test_collect_files_mix(make_node, tmp_path):
    f1 = tmp_path / "a.txt"
    f1.write_bytes(b"aaa")
    sub = tmp_path / "dir"
    sub.mkdir()
    f2 = sub / "b.bin"
    f2.write_bytes(os.urandom(2048))
    f3 = sub / "c.bin"
    f3.write_bytes(os.urandom(1024))

    entries, total = collect_files([str(f1), str(sub)])
    rels = {e["rel_path"] for e in entries}
    assert rels == {"a.txt", "dir/b.bin", "dir/c.bin"}
    assert total == 3 + 2048 + 1024
    for e in entries:
        assert os.path.isfile(e["abs_path"])
