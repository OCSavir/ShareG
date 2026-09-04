"""Render the ShareG window's small icon to a PNG for visual verification.

Run:  python tools/render_window_icon.py  (app must be running)
Output: window_icon_render.png in the project root.
"""

import ctypes
import ctypes.wintypes as wt
import os
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
OUT = os.path.join(ROOT, "window_icon_render.png")

user32 = ctypes.windll.user32
target = None


@ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
def cb(hwnd, lparam):
    global target
    n = user32.GetWindowTextLengthW(hwnd)
    if n:
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        if buf.value == "ShareG" and user32.IsWindowVisible(hwnd):
            target = hwnd
    return True


user32.EnumWindows(cb, 0)
if not target:
    raise SystemExit("no ShareG window found")
hicon = user32.SendMessageW(target, 0x7F, 0, 0)  # WM_GETICON small
print("hwnd:", target, "small icon handle:", hicon)

ps = (
    "Add-Type -AssemblyName System.Drawing\n"
    f"$ic = [System.Drawing.Icon]::FromHandle([IntPtr]{hicon})\n"
    "$bmp = $ic.ToBitmap()\n"
    "$big = New-Object System.Drawing.Bitmap(128,128)\n"
    "$g = [System.Drawing.Graphics]::FromImage($big)\n"
    "$g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::NearestNeighbor\n"
    "$g.DrawImage($bmp, 0, 0, 128, 128)\n"
    f"$big.Save(\"{OUT}\")\n"
    "Write-Output (\"rendered size: \" + $bmp.Width + \"x\" + $bmp.Height)\n"
)
r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                   capture_output=True, text=True)
print(r.stdout or r.stderr[:300])
