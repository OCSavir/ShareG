"""Mobile (Android) UI layout tests.

Verifies the platform dispatch and that the mobile builder produces a
layout with no fixed desktop widths, scrollable lists, and touch-sized
controls - without touching the desktop builder's output.
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import flet as ft

import shareg.ui as ui_mod
from shareg.ui import ShareGApp

UI_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "shareg", "ui.py")


def _read_ui():
    return open(UI_PATH, encoding="utf-8").read()


def test_platform_dispatch_mobile():
    src = _read_ui()
    assert "_build_ui_mobile" in src and "_build_ui_desktop" in src
    assert "is_mobile()" in src
    # the desktop builder body must be the original one (renamed, not rewritten)
    m = re.search(r"def _build_ui_desktop\(self\) -> None:\n(    def _build_ui_mobile)", src)
    assert not m  # builder bodies are separate methods
    # desktop builder still contains the original 3-pane root
    m2 = re.search(r"def _build_ui_desktop.*?root = ft.Row\(\[devices_panel, center, log_panel\]",
                   src, re.S)
    assert m2, "desktop 3-pane root must remain"


def test_desktop_builder_unchanged_marker():
    src = _read_ui()
    m = re.search(r"def _build_ui_desktop.*?(?=\n    # ------+ mobile)", src, re.S)
    assert m
    body = m.group(0)
    # original desktop layout landmarks preserved
    for landmark in ("width=250", "width=290", 'ft.Tab(label="Send Text"',
                     'ft.Tab(label="Send Files"'):
        assert landmark in body, f"desktop layout lost: {landmark}"


def test_mobile_builder_no_fixed_side_panels():
    src = _read_ui()
    m = re.search(r"def _build_ui_mobile.*?(?=\n    # ------+ mobile helpers)", src, re.S)
    assert m
    body = m.group(0)
    # the overflow culprits from the desktop layout must be absent
    assert "width=250" not in body, "mobile must not use the 250px side panel"
    assert "width=290" not in body, "mobile must not use the 290px log panel"
    # lists scroll instead of overflowing
    assert "scroll=ft.ScrollMode.AUTO" in body
    # touch-sized send buttons (48dp target)
    assert re.search(r"ElevatedButton\(\s*content=ft\.Text\(\"Send\"\).*height=48", body, re.S)
    # rows that can overflow wrap instead
    assert "wrap=True" in body
    # text ellipsizes instead of pushing content off screen
    assert "TextOverflow.ELLIPSIS" in body


def test_mobile_received_text_dialog_fits_phones():
    src = _read_ui()
    # fixed 460 width replaced by breakpoint-capped width
    assert "width=460," not in src
    assert "ResponsiveRowBreakpoint.XS" in src


def test_mobile_selection_collapses_picker_but_autoselect_does_not():
    src = _read_ui()
    # user picks collapse the device list; the auto-selection at startup must
    # pass user_pick=False
    assert "user_pick=False" in src
    m = re.search(r"def _select_device\(self, device_id: str, user_pick: bool = True\)", src)
    assert m


def test_desktop_and_mobile_build_shared_widget_surface():
    """Both builders must populate every widget attribute the shared update
    logic (refresh/log/progress) touches - this is what keeps one code path
    for device tiles, activity log, and progress on all platforms."""
    src = _read_ui()
    m_d = re.search(r"def _build_ui_desktop.*?(?=\n    # ------+ mobile \(Android)", src, re.S)
    m_m = re.search(r"def _build_ui_mobile.*?(?=\n    # ------+ mobile helpers)", src, re.S)
    assert m_d and m_m
    required = ["devices_list", "log_list", "text_field", "selection_label",
                "file_progress", "file_progress_label", "send_text_btn",
                "send_files_btn", "tabs", "_device_chip"]
    for attr in required:
        assert f"self.{attr}" in m_d.group(0), f"desktop missing {attr}"
        assert f"self.{attr}" in m_m.group(0), f"mobile missing {attr}"


def test_platform_values():
    """Sanity on the platform discriminator used by _build_ui."""
    assert ft.PagePlatform.ANDROID.is_mobile()
    assert not ft.PagePlatform.WINDOWS.is_mobile()
    assert not ft.PagePlatform.LINUX.is_mobile()
    assert not ft.PagePlatform.MACOS.is_mobile()


# ---------------------------------------------------------------------------
# Fix round 2: safe area, Send Text visibility, received-text visibility
# ---------------------------------------------------------------------------

def test_mobile_wrapped_in_safearea():
    """Fix 1: content must start below the Android status bar."""
    src = _read_ui()
    m = re.search(r"def _build_ui_mobile.*?(?=\n    # ------+ mobile helpers)", src, re.S)
    body = m.group(0)
    assert "ft.SafeArea(" in body, "mobile root must use SafeArea for status-bar insets"
    # header is INSIDE the safe area, not added separately above it
    assert "page.add(safe_root)" in body
    assert not re.search(r"page\.add\(header,", body), \
        "header must not be added outside the SafeArea"


def test_mobile_no_scrollable_root_with_expand_children():
    """Fix 2 root cause: a scrollable Column gives children unbounded height,
    which collapses flex children (tabs/TextField) to zero -> 'Send Text
    broken'. The root must not combine scroll with expand children."""
    src = _read_ui()
    m = re.search(r"def _build_ui_mobile.*?(?=\n    # ------+ mobile helpers)", src, re.S)
    body = m.group(0)
    root_m = re.search(r"content=ft\.Column\(\s*\[\s*header,.*?expand=True,\s*\),\s*\)",
                       body, re.S)
    assert root_m, "mobile root column not found"
    root = root_m.group(0)
    assert "scroll=" not in root, "root column must not scroll (unbounded height collapses flex children)"
    assert "expand=True" in root, "root column must expand to fill the safe area"


def test_mobile_fixed_height_sections():
    """Devices list and activity log get fixed heights (their internal lists
    scroll) so they can never squeeze the tabs/TextField region to zero."""
    src = _read_ui()
    m = re.search(r"def _build_ui_mobile.*?(?=\n    # ------+ mobile helpers)", src, re.S)
    body = m.group(0)
    assert "height=200" in body, "devices section needs a bounded height"
    assert "height=180, content=self.log_list" in body, "activity section needs a bounded height"


def test_received_text_dialog_no_unbounded_flex_child():
    """Fix 3 root cause: TextField had expand=True inside the height-unbounded
    dialog content -> collapsed to zero height (invisible text, Copy still
    worked). It must be fixed-size with explicit light-on-dark styling."""
    src = _read_ui()
    m = re.search(r"async def _show_text_received.*?(?=\n    async def |\n    def )", src, re.S)
    seg = m.group(0)
    tf_m = re.search(r"text_area = ft\.TextField\((.*?)\)\n", seg, re.S)
    assert tf_m, "text_area TextField not found"
    tf = tf_m.group(1)
    assert "expand=True" not in tf, "flex child collapses to zero in a dialog"
    assert "read_only=True" in tf
    assert "text_style=ft.TextStyle(color=_TEXT)" in tf, "explicit light-on-dark text style"
    assert "color=_TEXT" in tf
