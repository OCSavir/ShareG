"""TCP file transfers: single, multiple, folder tree, and large files."""

import hashlib
import os

import pytest

from conftest import wait_for


def _wait_received(node, timeout=30):
    wait_for(lambda: node._test_received["files"], timeout=timeout, msg="files received")
    _, folder, files = node._test_received["files"][-1]
    return folder, files


def test_single_file(make_node, tmp_path):
    a = make_node("Alpha")
    b = make_node("Beta")
    wait_for(lambda: b.device_id in a.peers, timeout=15, msg="discovery")

    src = tmp_path / "hello.bin"
    payload = os.urandom(1024 * 512)  # 512 KB
    src.write_bytes(payload)
    a.send_files_to(b.device_id, [str(src)])

    folder, files = _wait_received(b)
    assert len(files) == 1
    assert os.path.basename(files[0]) == "hello.bin"
    assert open(files[0], "rb").read() == payload


def test_multiple_files(make_node, tmp_path):
    a = make_node("Alpha")
    b = make_node("Beta")
    wait_for(lambda: b.device_id in a.peers, timeout=15, msg="discovery")

    srcs = []
    expected = {}
    for i in range(5):
        p = tmp_path / f"multi_{i}.dat"
        data = os.urandom(100_000 * (i + 1))
        p.write_bytes(data)
        expected[p.name] = data
        srcs.append(str(p))

    a.send_files_to(b.device_id, srcs)
    folder, files = _wait_received(b, timeout=45)
    assert len(files) == 5
    for f in files:
        name = os.path.basename(f)
        assert open(f, "rb").read() == expected[name]


def test_folder_tree(make_node, tmp_path):
    a = make_node("Alpha")
    b = make_node("Beta")
    wait_for(lambda: b.device_id in a.peers, timeout=15, msg="discovery")

    root = tmp_path / "proj"
    (root / "sub" / "deep").mkdir(parents=True)
    contents = {
        "top.txt": b"top level",
        "sub/mid.bin": os.urandom(50_000),
        "sub/deep/leaf.dat": os.urandom(10_000),
        "empty_dir_marker": None,
    }
    expected = {}
    for rel, data in contents.items():
        if data is None:
            continue
        p = root / rel
        p.write_bytes(data)
        expected[rel.replace("/", os.sep)] = data
    # empty dir: transfer has no marker file (os.walk lists none) - acceptable

    a.send_files_to(b.device_id, [str(root)])
    folder, files = _wait_received(b)
    rels = {os.path.relpath(f, folder).replace(os.sep, "/"): open(f, "rb").read() for f in files}
    assert len(files) == 3
    for rel, data in expected.items():
        # Receiver preserves the selected folder's own name as the root.
        key = "proj/" + rel.replace(os.sep, "/")
        assert rels[key] == data, f"content mismatch for {key}"


def test_large_file_chunked_progress(make_node, tmp_path):
    a = make_node("Alpha")
    b = make_node("Beta")
    wait_for(lambda: b.device_id in a.peers, timeout=15, msg="discovery")

    src = tmp_path / "large.bin"
    size = 32 * 1024 * 1024  # 32 MB
    chunk = os.urandom(1024 * 1024)
    with open(src, "wb") as f:
        written = 0
        while written < size:
            f.write(chunk)
            written += len(chunk)
    src_hash = hashlib.sha256(src.read_bytes()).hexdigest()

    progresses = []
    orig = a.send_files_to
    # capture progress via backend's on_progress notification
    a.set_ui_callbacks(on_progress=lambda p: progresses.append((p.sent_bytes, p.total_bytes)))
    a.send_files_to(b.device_id, [str(src)])

    folder, files = _wait_received(b, timeout=120)
    assert len(files) == 1
    got_hash = hashlib.sha256(open(files[0], "rb").read()).hexdigest()
    assert got_hash == src_hash
    assert progresses and progresses[-1][0] == progresses[-1][1] > 0


def test_path_traversal_rejected(make_node, tmp_path):
    a = make_node("Alpha")
    b = make_node("Beta")
    wait_for(lambda: b.device_id in a.peers, timeout=15, msg="discovery")

    from shareg.protocol import TransferServer, ProtocolError
    import os
    server = b.transfer_server
    with pytest.raises(ProtocolError):
        server._safe_dest_path(os.path.join(b_dis := __import__("shareg.paths", fromlist=["downloads_dir"]).downloads_dir(), "x"), "../evil.txt")
