#!/usr/bin/env python3
"""Draw the PianoJuke app icons (needs Pillow). Run from the pianojuke folder."""
from pathlib import Path

from PIL import Image, ImageDraw

BG = (12, 13, 16)
IVORY = (243, 239, 230)
EBONY = (12, 13, 16)
BRASS = (227, 174, 80)
OUT = Path(__file__).resolve().parents[1] / "pianojuke_web" / "icons"


def draw(size, safe):
    """A brass lid over five ivory keys with ebony sharps, inside `safe` of the canvas."""
    s = 4  # supersample
    img = Image.new("RGB", (size * s, size * s), BG)
    d = ImageDraw.Draw(img)
    pad = (1 - safe) / 2 * size * s
    w = size * s - 2 * pad
    left, top = pad, pad + w * 0.12
    lid_h = w * 0.16
    d.rounded_rectangle([left, top, left + w, top + lid_h], radius=lid_h * 0.35, fill=BRASS)
    keys_top = top + lid_h + w * 0.06
    keys_bottom = pad + w * 0.9
    gap = w * 0.025
    key_w = (w - gap * 4) / 5
    for i in range(5):
        x = left + i * (key_w + gap)
        d.rounded_rectangle([x, keys_top, x + key_w, keys_bottom], radius=key_w * 0.18, fill=IVORY)
    black_w, black_h = key_w * 0.62, (keys_bottom - keys_top) * 0.58
    for i in (0, 1, 3):
        cx = left + (i + 1) * (key_w + gap) - gap / 2
        d.rounded_rectangle([cx - black_w / 2, keys_top - 1, cx + black_w / 2, keys_top + black_h],
                            radius=black_w * 0.2, fill=EBONY)
    return img.resize((size, size), Image.LANCZOS)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    draw(192, 0.78).save(OUT / "icon-192.png", optimize=True)
    draw(512, 0.78).save(OUT / "icon-512.png", optimize=True)
    draw(512, 0.62).save(OUT / "icon-maskable-512.png", optimize=True)
    draw(180, 0.74).save(OUT / "apple-touch-icon.png", optimize=True)


if __name__ == "__main__":
    main()
