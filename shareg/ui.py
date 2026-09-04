"""ShareG - Flet UI layer.

Backend callbacks arrive on network threads; they are marshalled onto the UI
thread with page.run_task. Blocking backend calls run off the UI thread via
loop.run_in_executor so UI and networking never block each other.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from datetime import datetime
from typing import Dict, Optional

import flet as ft

from . import constants as c
from .backend import ShareGBackend, ShareGUserError
from .protocol import TransferProgress

log = logging.getLogger(__name__)

_TEAL = "#00B8A9"
_BG = "#121417"
_CARD = "#1B1F24"
_TEXT = "#E8ECEF"
_MUTED = "#9AA6AD"
_GREEN = "#3DDC84"
_RED = "#FF5370"
_TILE_SELECTED_BG = "#123A36"

# After backend.stop() this grace period lets Flet flush the final UI updates
# before the process is torn down deterministically (see _shutdown()).
_SHUTDOWN_GRACE_SECONDS = 0.7


def _dot(color: str, size: int = 10) -> ft.Container:
    return ft.Container(width=size, height=size, border_radius=size / 2, bgcolor=color)


def _bytes_human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


# ----------------------------------------------------------------------------
# Main app
# ----------------------------------------------------------------------------

class ShareGApp:
    def __init__(self, page: ft.Page) -> None:
        self.page = page
        self.backend: Optional[ShareGBackend] = None
        self.selected_device_id: Optional[str] = None
        self.selected_paths: list = []
        self._device_tiles: Dict[str, ft.Container] = {}
        self._closed = False
        self._shutting_down = False

        page.title = c.APP_NAME
        page.window.width = 1150
        page.window.height = 720
        page.window.min_width = 820
        page.window.min_height = 560
        # Runtime window icon (title bar / taskbar). Dev mode serves the
        # assets/ dir, so the path is relative to it; packaged builds embed
        # the icon from the same file via `flet build` (assets/icon_windows.ico).
        page.window.icon = "icon.ico"
        page.bgcolor = _BG
        page.padding = 0
        page.theme_mode = ft.ThemeMode.DARK
        page.theme = ft.Theme(color_scheme_seed=_TEAL)
        # Intercept the window close so we can stop networking cleanly first.
        page.window.prevent_close = True
        page.window.on_event = self._on_window_event

        self._build_ui()

        self.backend = ShareGBackend(device_name=self._device_name())
        self.backend.set_ui_callbacks(
            on_peers_changed=self._schedule_refresh_devices,
            on_pair_prompt=self._queue_pair_prompt,
            on_text_received=self._schedule_text_received,
            on_files_received=self._schedule_files_received,
            on_status_changed=self._schedule_refresh_devices,
            on_progress=self._schedule_progress,
        )
        self.backend.start()
        self._log(f"ShareG ready — this device: {self.backend.discovery.device_name}")
        self._schedule_refresh_devices()

    @staticmethod
    def _device_name() -> str:
        import platform
        return platform.node() or "ShareG Device"

    def _to_ui(self, coro_func, *args) -> None:
        """Schedule a coroutine function on the UI thread (thread-safe)."""
        if self._closed:
            return
        try:
            self.page.run_task(coro_func, *args)
        except Exception:
            pass

    # ------------------------------------------------------------------ build

    def _build_ui(self) -> None:
        page = self.page

        # header
        self._device_chip = ft.Text("", size=12, color=_MUTED)
        header = ft.Container(
            content=ft.Row(
                [
                    ft.Icon(icon=ft.Icons.SHARE, color=_TEAL, size=26),
                    ft.Text(c.APP_NAME, size=20, weight=ft.FontWeight.BOLD, color=_TEXT),
                    ft.Container(expand=True),
                    self._device_chip,
                ],
                spacing=10,
            ),
            padding=ft.Padding(18, 12, 18, 12),
            bgcolor=_CARD,
        )

        # devices panel (left)
        self.devices_list = ft.Column(spacing=8, scroll=ft.ScrollMode.AUTO, expand=True)
        devices_panel = ft.Container(
            content=ft.Column(
                [
                    ft.Text("Nearby devices", size=12, weight=ft.FontWeight.BOLD, color=_MUTED),
                    self.devices_list,
                ],
                spacing=8,
                expand=True,
            ),
            width=250,
            padding=12,
            bgcolor=_CARD,
            border_radius=12,
        )

        # ---- text tab
        self.text_field = ft.TextField(
            hint_text="Type or paste text to share...",
            multiline=True,
            min_lines=10,
            expand=True,
            bgcolor=_BG,
            border_color="#2A3138",
            color=_TEXT,
        )
        self.send_text_btn = ft.ElevatedButton(
            content=ft.Text("Send"), icon=ft.Icons.SEND, bgcolor=_TEAL, color="#08110F",
            on_click=self._on_send_text,
        )
        text_buttons = ft.Row(
            [
                ft.OutlinedButton(content=ft.Text("Paste"), icon=ft.Icons.CONTENT_PASTE, on_click=self._on_paste),
                ft.OutlinedButton(content=ft.Text("Clear"), icon=ft.Icons.CLEAR_ALL, on_click=self._on_clear_text),
                ft.Container(expand=True),
                self.send_text_btn,
            ],
            spacing=10,
        )
        text_tab = ft.Container(
            content=ft.Column([text_buttons, self.text_field], spacing=12, expand=True),
            padding=16,
            expand=True,
        )

        # ---- files tab
        self.selection_label = ft.Text("Nothing selected", color=_MUTED, size=13)
        self.file_progress = ft.ProgressBar(visible=False, color=_TEAL, bgcolor="#24303A")
        self.file_progress_label = ft.Text("", color=_MUTED, size=12, visible=False)
        self.send_files_btn = ft.ElevatedButton(
            content=ft.Text("Send"), icon=ft.Icons.SEND, bgcolor=_TEAL, color="#08110F",
            on_click=self._on_send_files,
        )
        files_tab = ft.Container(
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.ElevatedButton(content=ft.Text("Pick files"), icon=ft.Icons.FILE_OPEN,
                                              on_click=self._on_pick_files, bgcolor=_CARD, color=_TEXT),
                            ft.ElevatedButton(content=ft.Text("Pick folder"), icon=ft.Icons.FOLDER_OPEN,
                                              on_click=self._on_pick_folder, bgcolor=_CARD, color=_TEXT),
                            ft.TextButton(content=ft.Text("Clear selection"), on_click=self._on_clear_selection),
                        ],
                        spacing=10,
                    ),
                    self.selection_label,
                    ft.Container(expand=True),
                    self.file_progress_label,
                    self.file_progress,
                    ft.Row([ft.Container(expand=True), self.send_files_btn]),
                ],
                spacing=12,
                expand=True,
            ),
            padding=16,
            expand=True,
        )

        self.tabs = ft.Tabs(
            length=2,
            selected_index=0,
            content=ft.Column(
                expand=True,
                controls=[
                    ft.TabBar(
                        tabs=[
                            ft.Tab(label="Send Text", icon=ft.Icons.CHAT),
                            ft.Tab(label="Send Files", icon=ft.Icons.FOLDER),
                        ],
                    ),
                    ft.TabBarView(
                        expand=True,
                        controls=[text_tab, files_tab],
                    ),
                ],
            ),
            expand=True,
        )

        # activity log (right)
        self.log_list = ft.Column(spacing=4, scroll=ft.ScrollMode.AUTO, expand=True)
        log_panel = ft.Container(
            content=ft.Column(
                [
                    ft.Text("Activity", size=12, weight=ft.FontWeight.BOLD, color=_MUTED),
                    self.log_list,
                ],
                spacing=8,
                expand=True,
            ),
            width=290,
            padding=12,
            bgcolor=_CARD,
            border_radius=12,
        )

        center = ft.Container(
            content=ft.Column([self.tabs], expand=True),
            expand=True,
            padding=ft.Padding(14, 6, 14, 6),
        )
        root = ft.Row([devices_panel, center, log_panel], expand=True, spacing=0)
        page.add(header, root)

    # ------------------------------------------------------------------ devices panel

    def _schedule_refresh_devices(self, *_args) -> None:
        self._to_ui(self._refresh_devices)

    async def _refresh_devices(self) -> None:
        if self._closed or not self.backend:
            return
        peers = self.backend.peers
        seen = set(peers.keys())

        for did in list(self._device_tiles.keys()):
            if did not in seen:
                tile = self._device_tiles.pop(did)
                if tile in self.devices_list.controls:
                    self.devices_list.controls.remove(tile)

        for did, peer in peers.items():
            status = self.backend.statuses.get(did, "")
            tile = self._device_tiles.get(did)
            if tile is None:
                tile = self._make_device_tile(did)
                self._device_tiles[did] = tile
                self.devices_list.controls.append(tile)
            self._update_device_tile(did, peer, status)

        if self.selected_device_id and self.selected_device_id not in seen:
            self.selected_device_id = None
        if not self.selected_device_id and self._device_tiles:
            self._select_device(next(iter(self._device_tiles)))

        self._device_chip.value = f"This device: {self.backend.discovery.device_name}"
        self.page.update()

    def _make_device_tile(self, device_id: str) -> ft.Container:
        return ft.Container(
            content=ft.Column(
                [
                    ft.Row(
                        [
                            _dot(_MUTED),
                            ft.Text("", weight=ft.FontWeight.BOLD, color=_TEXT, expand=True),
                        ],
                        spacing=8,
                    ),
                    ft.Text("", size=11, color=_MUTED),
                ],
                spacing=2,
            ),
            padding=10,
            border_radius=10,
            bgcolor=_BG,
            ink=True,
            on_click=lambda e, did=device_id: self._select_device(did),
        )

    def _update_device_tile(self, device_id: str, peer: dict, status: str) -> None:
        tile = self._device_tiles.get(device_id)
        if tile is None:
            return
        dot_color = {"connected": _GREEN, "disconnected": _RED}.get(status, _MUTED)
        selected = device_id == self.selected_device_id
        tile.bgcolor = _TILE_SELECTED_BG if selected else _BG
        col = tile.content
        col.controls[0].controls[0].bgcolor = dot_color
        col.controls[0].controls[1].value = peer["name"]
        col.controls[1].value = f"{peer['ip']} - {status or 'discovered'}"

    def _select_device(self, device_id: str) -> None:
        if self.selected_device_id == device_id:
            return
        self.selected_device_id = device_id
        self._schedule_refresh_devices()

    # ------------------------------------------------------------------ text tab

    async def _on_paste(self, e=None) -> None:
        try:
            data = await self.page.clipboard.get()
        except Exception:
            data = None
        if data:
            self.text_field.value = (self.text_field.value or "") + data
            self.page.update()

    def _on_clear_text(self, e=None) -> None:
        self.text_field.value = ""
        self.page.update()

    async def _on_send_text(self, e=None) -> None:
        if not self.selected_device_id:
            self._toast("Select a device first")
            return
        text = self.text_field.value or ""
        if not text.strip():
            self._toast("Text is empty")
            return
        did = self.selected_device_id
        self.send_text_btn.disabled = True
        self.page.update()
        try:
            await self._run_blocking(self.backend.send_text_to, did, text)
            self._toast("Text sent")
        except ShareGUserError as ex:
            self._toast(str(ex))
        except Exception as ex:  # noqa: BLE001
            self._toast(f"Send failed: {ex}")
        finally:
            self.send_text_btn.disabled = False
            self.page.update()

    # ------------------------------------------------------------------ files tab

    async def _on_pick_files(self, e=None) -> None:
        picker = ft.FilePicker()
        self.page.overlay.append(picker)
        try:
            files = await picker.pick_files(allow_multiple=True)
            if files:
                for f in files:
                    if f.path and f.path not in self.selected_paths:
                        self.selected_paths.append(f.path)
                self._refresh_selection_label()
        finally:
            if picker in self.page.overlay:
                self.page.overlay.remove(picker)
            self.page.update()

    async def _on_pick_folder(self, e=None) -> None:
        picker = ft.FilePicker()
        self.page.overlay.append(picker)
        try:
            path = await picker.get_directory_path()
            if path:
                if path not in self.selected_paths:
                    self.selected_paths.append(path)
                self._refresh_selection_label()
        finally:
            if picker in self.page.overlay:
                self.page.overlay.remove(picker)
            self.page.update()

    def _refresh_selection_label(self) -> None:
        if not self.selected_paths:
            self.selection_label.value = "Nothing selected"
            self.selection_label.color = _MUTED
        else:
            names = [p.replace("\\", "/").rstrip("/").split("/")[-1] for p in self.selected_paths]
            shown = ", ".join(names[:4]) + (f" (+{len(names) - 4})" if len(names) > 4 else "")
            self.selection_label.value = f"{len(self.selected_paths)} item(s): {shown}"
            self.selection_label.color = _TEXT
        self.page.update()

    def _on_clear_selection(self, e=None) -> None:
        self.selected_paths.clear()
        self._refresh_selection_label()

    async def _on_send_files(self, e=None) -> None:
        if not self.selected_device_id:
            self._toast("Select a device first")
            return
        if not self.selected_paths:
            self._toast("Nothing selected to send")
            return
        did = self.selected_device_id
        selection = list(self.selected_paths)
        self.send_files_btn.disabled = True
        self.page.update()
        try:
            await self._run_blocking(self.backend.send_files_to, did, selection)
            self._toast("Files sent")
        except ShareGUserError as ex:
            self._toast(str(ex))
        except Exception as ex:  # noqa: BLE001
            self._toast(f"Send failed: {ex}")
        finally:
            self.send_files_btn.disabled = False
            self.page.update()

    async def _run_blocking(self, fn, *args):
        """Run a blocking function off the UI thread."""
        loop = self.page.loop
        return await loop.run_in_executor(None, fn, *args)

    async def _schedule_progress(self, progress: TransferProgress) -> None:
        if self._closed:
            return
        if progress.done:
            self.file_progress.visible = False
            self.file_progress_label.visible = False
        else:
            frac = (progress.sent_bytes / progress.total_bytes) if progress.total_bytes else 0.0
            self.file_progress.visible = True
            self.file_progress.value = min(1.0, frac)
            self.file_progress_label.visible = True
            self.file_progress_label.value = (
                f"{progress.file_index}/{progress.file_count} - {progress.label} - "
                f"{_bytes_human(progress.sent_bytes)} / {_bytes_human(progress.total_bytes)}"
            )
        self.page.update()

    # ------------------------------------------------------------------ receiving

    def _schedule_text_received(self, sender: str, text: str) -> None:
        self._to_ui(self._show_text_received, sender, text)

    def _schedule_files_received(self, sender: str, folder: str, files: list) -> None:
        self._to_ui(self._show_files_received, sender, folder, files)

    async def _show_text_received(self, sender: str, text: str) -> None:
        self._log(f"Text from {sender}: {len(text)} chars")
        # Selectable text (Ctrl+C works natively) + a Copy button for touch
        # devices where keyboard shortcuts are impractical.
        text_area = ft.TextField(
            value=text,
            multiline=True,
            min_lines=4,
            max_lines=12,
            read_only=True,
            bgcolor=_BG,
            border_color="#2A3138",
            color=_TEXT,
            expand=True,
        )
        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text("Text received"),
            content=ft.Container(
                content=ft.Column(
                    [
                        ft.Text(f"From {sender}", color=_MUTED, size=12),
                        text_area,
                    ],
                    spacing=10,
                ),
                width=460,
            ),
            actions=[
                ft.TextButton(
                    content=ft.Text("Copy"),
                    icon=ft.Icons.CONTENT_COPY,
                    on_click=lambda e: self._copy_received_text(text),
                ),
                ft.TextButton(content=ft.Text("Close"), on_click=lambda e: self.page.pop_dialog()),
            ],
        )
        self.page.show_dialog(dlg)

    def _copy_received_text(self, text: str) -> None:
        """Copy received text to the system clipboard (works on desktop and
        Android via Flet's clipboard service)."""
        async def _copy() -> None:
            try:
                await self.page.clipboard.set(text)
                self._toast("Copied to clipboard")
            except Exception:  # noqa: BLE001
                log.exception("clipboard set failed")
                self._toast("Copy failed")
        try:
            self.page.run_task(_copy)
        except Exception:  # noqa: BLE001
            log.exception("could not schedule clipboard task")

    async def _show_files_received(self, sender: str, folder: str, files: list) -> None:
        names = [f.replace("\\", "/").split("/")[-1] for f in files]
        shown = ", ".join(names[:5]) + (f" (+{len(names) - 5} more)" if len(names) > 5 else "")
        self._log(f"Files from {sender}: {len(files)} item(s)")
        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text("Files received"),
            content=ft.Text(f"From {sender}:\n{shown}\n\nSaved to:\n{folder}"),
            actions=[ft.TextButton(content=ft.Text("OK"), on_click=lambda e: self.page.pop_dialog())],
        )
        self.page.show_dialog(dlg)

    def _queue_pair_prompt(self, device_id: str, name: str, ip: str, box: dict) -> None:
        """Runs on the receiver's connection thread. Shows the dialog on the
        UI thread and blocks this thread until the user answers (or timeout)."""
        done = threading.Event()

        async def show() -> None:
            def accept(e=None) -> None:
                box["answer"] = True
                done.set()
                self.page.pop_dialog()

            def reject(e=None) -> None:
                box["answer"] = False
                done.set()
                self.page.pop_dialog()

            dlg = ft.AlertDialog(
                modal=True,
                title=ft.Text("Incoming pairing request"),
                content=ft.Text(
                    f"Do you want to connect to this device?\n\n{name}  ({ip})"
                ),
                actions=[
                    ft.TextButton(content=ft.Text("Reject"), on_click=reject),
                    ft.ElevatedButton(content=ft.Text("Accept"), on_click=accept, bgcolor=_TEAL, color="#08110F"),
                ],
            )
            self.page.show_dialog(dlg)

        try:
            self.page.run_task(show)
        except Exception:  # noqa: BLE001
            box["answer"] = False
            return
        done.wait(timeout=c.PAIR_PROMPT_TIMEOUT)
        if not done.is_set():
            box["answer"] = False

    # ------------------------------------------------------------------ log / toast

    def _log(self, msg: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_list.controls.insert(0, ft.Text(f"[{stamp}] {msg}", size=12, color=_TEXT))
        del self.log_list.controls[80:]
        try:
            self.page.update()
        except Exception:  # noqa: BLE001
            pass

    def _toast(self, msg: str) -> None:
        if self._closed:
            return
        try:
            self.page.show_dialog(ft.SnackBar(content=ft.Text(msg), bgcolor="#2A3138"))
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------ shutdown

    async def _on_window_event(self, e) -> None:
        # Flet awaits async event handlers, so the window only actually
        # closes after the cleanup in _shutdown() has finished.
        if getattr(e, "type", None) == ft.WindowEventType.CLOSE:
            await self._shutdown()

    async def _shutdown(self) -> None:
        """Centralized, idempotent application shutdown.

        1. Stop the backend: closes all sockets, stops the UDP discovery
           threads, TCP server, heartbeat, and joins every network thread.
        2. Give the UI a moment to flush, then destroy the window.
        3. As a hard guarantee against any non-daemon straggler keeping the
           Python process alive, terminate the process explicitly.
        """
        if self._shutting_down:
            return
        self._shutting_down = True
        self._closed = True
        log.info("ShareG shutting down…")
        try:
            if self.backend:
                self.backend.stop()
        except Exception:  # noqa: BLE001
            log.exception("backend stop failed")
        try:
            await asyncio.sleep(_SHUTDOWN_GRACE_SECONDS)
        except Exception:  # noqa: BLE001
            pass
        log.info("ShareG window destroyed")
        try:
            await self.page.window.destroy()
        except Exception:  # noqa: BLE001
            log.exception("window destroy failed")
        # Nothing else may keep this process alive. If some non-daemon thread
        # or hung Flet runtime still does, exit deterministically instead of
        # leaving an orphaned process behind.
        os._exit(0)


def main(page: ft.Page) -> None:
    ShareGApp(page)
