#!/usr/bin/env python3
"""Build the Windows icons from assets/icon.png (stdlib only).

Outputs:
  assets/icon_windows.ico  - picked up by `flet build` for Windows packaging
  assets/icon.ico          - runtime window icon (page.window.icon), dev mode

ICO contains BMP-format images (16/32/48/64) + a PNG entry for 256.
Run after make_icon.py:  python tools/make_ico.py
"""

import os
import struct
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
PNG_PATH = os.path.join(ROOT, "assets", "icon.png")
ICO_PATH = os.path.join(ROOT, "assets", "icon_windows.ico")
ICO_RUNTIME_PATH = os.path.join(ROOT, "assets", "icon.ico")


def read_png_rgba(path):
    """Minimal PNG reader for our own generated RGBA non-interlaced file."""
    with open(path, "rb") as f:
        data = f.read()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    pos = 8
    w = h = None
    idat = bytearray()
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        tag = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        if tag == b"IHDR":
            w, h, bitd, color, _, _, interlace = struct.unpack(">IIBBBBB", body[:13])
            assert bitd == 8 and color == 6 and interlace == 0
        elif tag == b"IDAT":
            idat.extend(body)
        elif tag == b"IEND":
            break
        pos += 12 + length
    raw = zlib.decompress(bytes(idat))
    stride = w * 4
    rows = []
    prev = bytearray(stride)
    p = 0
    for _ in range(h):
        filt = raw[p]
        p += 1
        line = bytearray(raw[p:p + stride])
        p += stride
        if filt == 1:  # Sub
            for i in range(4, stride):
                line[i] = (line[i] + line[i - 4]) & 0xFF
        elif filt == 2:  # Up
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif filt == 3:  # Average
            for i in range(stride):
                left = line[i - 4] if i >= 4 else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif filt == 4:  # Paeth
            for i in range(stride):
                a = line[i - 4] if i >= 4 else 0
                b = prev[i]
                cc = prev[i - 4] if i >= 4 else 0
                pa = abs(b - cc)
                pb = abs(a - cc)
                pc = abs(a + b - 2 * cc)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else cc)
                line[i] = (line[i] + pr) & 0xFF
        rows.append(bytes(line))
        prev = line
    return w, h, rows


def resize_rows(w, h, rows, tw, th):
    """Bilinear-ish box resize of RGBA row image."""
    out = []
    for j in range(th):
        sy0 = j * h / th
        sy1 = (j + 1) * h / th
        line = bytearray(tw * 4)
        for i in range(tw):
            sx0 = int(i * w / tw)
            sx1 = max(sx0 + 1, int((i + 1) * w / tw))
            r = g = b = a = n = 0
            for y in range(int(sy0), min(h, max(int(sy0) + 1, int(sy1)))):
                row = rows[y]
                for x in range(sx0, min(w, sx1)):
                    o = x * 4
                    r += row[o]
                    g += row[o + 1]
                    b += row[o + 2]
                    a += row[o + 3]
                    n += 1
            if n:
                line[i * 4:i * 4 + 4] = bytes((r // n, g // n, b // n, a // n))
        out.append(bytes(line))
    return out


def bmp_entry(w, h, rows):
    """BMP-format ICO image: BITMAPINFOHEADER + XOR (BGRA, bottom-up) + AND mask."""
    stride = w * 4
    xor = bytearray()
    for row in reversed(rows):
        line = bytearray(stride)
        for i in range(w):
            r, g, b, a = row[i * 4:i * 4 + 4]
            line[i * 4:i * 4 + 4] = bytes((b, g, r, a))
        xor.extend(line)
    and_stride = ((w + 31) // 32) * 4
    and_mask = bytes(and_stride * h)  # alpha fully describes transparency
    header = struct.pack(
        "<IiiHHIIiiII", 40, w, h * 2, 1, 32, 0, len(xor) + len(and_mask),
        0, 0, 0, 0,
    )
    return header + bytes(xor) + and_mask


def png_entry(png_bytes):
    return png_bytes


def main():
    with open(PNG_PATH, "rb") as f:
        png_bytes = f.read()
    w, h, rows = read_png_rgba(PNG_PATH)
    assert w == 512 and h == 512

    images = []  # (width, height, data)
    for size in (16, 32, 48, 64):
        small = resize_rows(w, h, rows, size, size)
        images.append((size, size, bmp_entry(size, size, small)))
    images.append((256, 256, png_entry(png_bytes)))

    count = len(images)
    header = struct.pack("<HHH", 0, 1, count)
    offset = 6 + 16 * count
    directory = bytearray()
    body = bytearray()
    for (iw, ih, data) in images:
        b_or_png = 0 if iw >= 256 else iw
        directory += struct.pack(
            "<BBBBHHII", b_or_png, b_or_png, 0, 0, 1, 32, len(data), offset
        )
        body += data
        offset += len(data)

    data = header + bytes(directory) + bytes(body)
    with open(ICO_PATH, "wb") as f:
        f.write(data)
    with open(ICO_RUNTIME_PATH, "wb") as f:
        f.write(data)
    print("wrote", ICO_PATH, len(data), "bytes")
    print("wrote", ICO_RUNTIME_PATH, len(data), "bytes")


if __name__ == "__main__":
    main()
