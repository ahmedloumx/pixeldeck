"""PixelDeck's icon files from static/app-icon.svg, which tools/make_icon.py draws as one rect
per pixel run: a Windows .ico, PNGs (Linux), and the PNG set macOS's iconutil makes an .icns of.

Run: python packaging/make_ico.py static/app-icon.svg OUT.ico   (packaging/build.py does the rest)
Pixel art scales by whole numbers without blur, so every size is the 32x32 grid repeated
(16 takes every other pixel).
"""

import os
import re
import struct
import sys
import zlib

SIZES = (16, 32, 48, 64, 128, 256)


def read_grid(svg):
    size = int(re.search(r'viewBox="0 0 (\d+) (\d+)"', svg).group(1))
    grid = [[None] * size for _ in range(size)]
    for x, y, w, h, fill in re.findall(r'<rect x="(\d+)" y="(\d+)" width="(\d+)" height="(\d+)" fill="#([0-9a-fA-F]{6})"', svg):
        color = tuple(int(fill[i:i + 2], 16) for i in (0, 2, 4))
        for row in range(int(y), int(y) + int(h)):
            for col in range(int(x), int(x) + int(w)):
                grid[row][col] = color
    return grid


def png(grid, size):
    n = len(grid)
    rows = []
    for y in range(size):
        line = bytearray([0])  # filter: none
        for x in range(size):
            pixel = grid[y * n // size][x * n // size]
            line += bytes((*pixel, 255)) if pixel else bytes(4)
        rows.append(bytes(line))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)  # 8-bit RGBA
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b"")


def ico(grid):
    images = [png(grid, size) for size in SIZES]
    out = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    for size, image in zip(SIZES, images):
        out += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(image), offset)
        offset += len(image)
    return out + b"".join(images)


def load(source):
    with open(source, encoding="utf-8") as fh:
        return read_grid(fh.read())


def write_png(grid, size, target):
    with open(target, "wb") as fh:
        fh.write(png(grid, size))


def write_iconset(grid, folder):
    """The PNGs macOS's iconutil turns into an .icns: icon_<n>x<n>.png and its @2x."""
    os.makedirs(folder, exist_ok=True)
    for size in (16, 32, 128, 256, 512):
        write_png(grid, size, os.path.join(folder, f"icon_{size}x{size}.png"))
        write_png(grid, size * 2, os.path.join(folder, f"icon_{size}x{size}@2x.png"))


def main():
    source, target = sys.argv[1], sys.argv[2]
    with open(target, "wb") as fh:
        fh.write(ico(load(source)))


if __name__ == "__main__":
    main()
