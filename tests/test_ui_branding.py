"""Fix 2 + 3 unit-level checks: copyable received text, icon wiring."""

import os
import re

import flet as ft

ASSETS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")


def test_icon_files_exist_and_are_valid():
    import struct, zlib
    for name in ("icon.png", "icon.ico", "icon_windows.ico"):
        path = os.path.join(ASSETS, name)
        assert os.path.isfile(path), f"missing {path}"
        assert os.path.getsize(path) > 1000, f"{name} suspiciously small"
    # PNG header check
    with open(os.path.join(ASSETS, "icon.png"), "rb") as f:
        assert f.read(8) == b"\x89PNG\r\n\x1a\n"
    # ICO header check: reserved==0, type==1
    with open(os.path.join(ASSETS, "icon_windows.ico"), "rb") as f:
        reserved, ico_type, count = struct.unpack("<HHH", f.read(6))
    assert reserved == 0 and ico_type == 1 and count >= 4


def test_icon_paths_referenced_from_build_config():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    pyproject = open(os.path.join(root, "pyproject.toml"), encoding="utf-8").read()
    # product name must stay ShareG
    assert re.search(r'^product\s*=\s*"ShareG"', pyproject, re.M)
    # no stale icon keys (flet 0.86 discovers by filename)
    assert "windows_icon" not in pyproject
    assert 'icon = "assets/' not in pyproject


def test_ui_uses_runtime_window_icon():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(root, "shareg", "ui.py"), encoding="utf-8").read()
    assert 'page.window.icon = "icon.ico"' in src


def test_ui_renders_selectable_received_text_with_copy():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(root, "shareg", "ui.py"), encoding="utf-8").read()
    # received text lives in a read-only TextField (selectable, Ctrl+C works)
    m = re.search(r"async def _show_text_received.*?(?=\n    async def |\n    def )", src, re.S)
    assert m, "_show_text_received not found"
    seg = m.group(0)
    assert "ft.TextField(" in seg and "read_only=True" in seg
    # dedicated Copy button that uses the clipboard service
    assert "CONTENT_COPY" in seg
    m2 = re.search(r"def _copy_received_text.*?(?=\n    async def |\n    def )", src, re.S)
    assert m2 and "clipboard.set" in m2.group(0)


def test_text_widget_supports_selection():
    # The framework control we rely on must actually support selectable text.
    assert "selectable" in {f.name for f in __import__("dataclasses").fields(ft.Text)}
