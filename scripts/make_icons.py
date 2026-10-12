#!/usr/bin/env python3
"""Generate the extension's PNG icons (dark rounded square with a check mark).

Pure stdlib so contributors need no imaging libraries. Run from repo root:
    python scripts/make_icons.py
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "extension" / "public" / "icons"
BG = (28, 29, 31)
FG = (255, 255, 255)


def _dist_to_segment(px: float, py: float, a: tuple[float, float], b: tuple[float, float]) -> float:
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    cx, cy = ax + t * dx, ay + t * dy
    return ((px - cx) ** 2 + (py - cy) ** 2) ** 0.5


def render(size: int) -> bytes:
    radius = size * 0.22
    stroke = size * 0.085
    p1, p2, p3 = (0.27, 0.53), (0.43, 0.69), (0.74, 0.34)
    pts = [(x * size, y * size) for x, y in (p1, p2, p3)]
    rows = []
    for y in range(size):
        row = bytearray([0])
        for x in range(size):
            px, py = x + 0.5, y + 0.5
            # Rounded-square coverage
            qx = max(radius - px, 0, px - (size - radius))
            qy = max(radius - py, 0, py - (size - radius))
            inside = (qx * qx + qy * qy) ** 0.5 <= radius
            if not inside:
                row += bytes((0, 0, 0, 0))
                continue
            d = min(
                _dist_to_segment(px, py, pts[0], pts[1]), _dist_to_segment(px, py, pts[1], pts[2])
            )
            alpha = max(0.0, min(1.0, stroke - d + 0.5))
            color = tuple(round(b * (1 - alpha) + f * alpha) for b, f in zip(BG, FG, strict=True))
            row += bytes((*color, 255))
        rows.append(bytes(row))
    raw = b"".join(rows)

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
        )

    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for size in (16, 48, 128):
        (OUT / f"icon{size}.png").write_bytes(render(size))
        print(f"wrote {OUT / f'icon{size}.png'}")


if __name__ == "__main__":
    main()
