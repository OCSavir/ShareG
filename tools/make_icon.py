#!/usr/bin/env python3
"""Generate the ShareG app icon (PNG) using only the standard library.

Run:  python tools/make_icon.py
Outputs: assets/icon.png (512x512)

The name "icon.png" is what `flet build` globs in assets/ for the app icon
(general, Linux, and Android adaptive-icon source). The Windows .ico is built
separately by tools/make_ico.py.
"""

import math
import os
import struct
import zlib

W = 512
TEAL_TOP = (0, 198, 178)
TEAL_BOT = (0, 122, 148)
WHITE = (255, 255, 255)

px = bytearray(W * W * 4)


def put(x, y, rgba):
    if 0 <= x < W and 0 <= y < W:
        o = (y * W + x) * 4
        px[o:o + 4] = bytes(rgba)


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def build():
    cx = cy = W / 2.0
    half = W * 0.46
    e = 4.2
    bw = W * 0.075
    nr = W * 0.075
    n1 = (W * 0.30, W * 0.32)
    n2 = (W * 0.72, W * 0.50)
    n3 = (W * 0.30, W * 0.68)

    def inside(x, y):
        dx = abs(x - cx) / half
        dy = abs(y - cy) / half
        return dx ** e + dy ** e <= 1.0

    # 1) gradient squircle background (2x2 supersampled)
    for j in range(W):
        t = 1.0 - j / (W - 1)
        col = lerp(TEAL_TOP, TEAL_BOT, t)
        for i in range(W):
            alpha = 0
            for sy in (0.25, 0.75):
                for sx in (0.25, 0.75):
                    if inside(i + sx, j + sy):
                        alpha += 1
            if alpha:
                put(i, j, (col[0], col[1], col[2], int(255 * alpha / 4)))

    def fill_disc(ccx, ccy, r, color, alpha=255):
        r2 = r * r
        j0 = max(0, int(ccy - r) - 2)
        j1 = min(W - 1, int(ccy + r) + 2)
        i0 = max(0, int(ccx - r) - 2)
        i1 = min(W - 1, int(ccx + r) + 2)
        for j in range(j0, j1 + 1):
            for i in range(i0, i1 + 1):
                d2 = (i + 0.5 - ccx) ** 2 + (j + 0.5 - ccy) ** 2
                if d2 <= r2:
                    put(i, j, (color[0], color[1], color[2], alpha))

    def fill_segment(x0, y0, x1, y1, w, color, alpha=255):
        L = math.hypot(x1 - x0, y1 - y0)
        steps = max(1, int(L * 3))
        for s in range(steps + 1):
            t = s / steps
            fill_disc(x0 + (x1 - x0) * t, y0 + (y1 - y0) * t, w / 2.0, color, alpha)

    # 2) share glyph: bars then nodes, white
    fill_segment(n1[0], n1[1], n2[0], n2[1], bw, WHITE)
    fill_segment(n3[0], n3[1], n2[0], n2[1], bw, WHITE)
    for (nx, ny) in (n1, n2, n3):
        fill_disc(nx, ny, nr, WHITE)

    # 3) punch ring holes showing the gradient
    hole = nr * 0.45
    for (nx, ny) in (n1, n2, n3):
        j0 = max(0, int(ny - hole) - 1)
        j1 = min(W - 1, int(ny + hole) + 1)
        i0 = max(0, int(nx - hole) - 1)
        i1 = min(W - 1, int(nx + hole) + 1)
        for j in range(j0, j1 + 1):
            col = lerp(TEAL_TOP, TEAL_BOT, 1 - j / (W - 1))
            for i in range(i0, i1 + 1):
                d2 = (i + 0.5 - nx) ** 2 + (j + 0.5 - ny) ** 2
                if d2 <= hole * hole:
                    put(i, j, (col[0], col[1], col[2], 255))


def write_png(path):
    raw = b"".join(b"\x00" + bytes(px[y * W * 4:(y + 1) * W * 4]) for y in range(W))

    def chunk(tag, data):
        c = tag + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", W, W, 8, 6, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 9))
    png += chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(png)


if __name__ == "__main__":
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets", "icon.png")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    build()
    write_png(out)
    print("wrote", os.path.normpath(out))
