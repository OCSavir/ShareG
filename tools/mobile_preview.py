"""Launch ShareG with the MOBILE layout in a phone-sized desktop window.

Dev tool for visually verifying the Android layout on Windows:
    python tools/mobile_preview.py
    SHAREG_PREVIEW_DIALOG=1 python tools/mobile_preview.py   # + sample
                                                             # received-text dialog

The window opens at 390x844 (typical phone logical size) and the mobile
builder is forced regardless of the desktop platform.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import flet as ft
from shareg import ui as ui_mod


class MobilePreviewApp(ui_mod.ShareGApp):
    def _build_ui(self) -> None:
        # Force the Android layout path for desktop preview.
        self._is_mobile_layout = True
        self._build_ui_mobile()

    def _after_start(self) -> None:
        # Phone-size the window once the session is fully established; doing
        # it earlier gets clamped back to the desktop minimums committed in
        # __init__ by the window manager.
        async def _resize_later() -> None:
            import asyncio
            await asyncio.sleep(3)
            self.page.window.min_width = 390
            self.page.window.min_height = 600
            self.page.window.width = 390
            self.page.window.height = 844
            self.page.update()

        self.page.run_task(_resize_later)

        # Dev-only: pop a sample received-text dialog to verify fix 3.
        if os.environ.get("SHAREG_PREVIEW_DIALOG"):
            async def _show_later() -> None:
                import asyncio
                await asyncio.sleep(5)
                await self._show_text_received("PreviewSender", "Hello from the preview!\n"
                                               "This received text must be visibly readable\n"
                                               "on a phone-sized window.")
            self.page.run_task(_show_later)


def main(page: ft.Page) -> None:
    MobilePreviewApp(page)._after_start()


if __name__ == "__main__":
    ft.run(main)
