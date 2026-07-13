#!/usr/bin/env python3
"""Render the PNG app icons from the same design as icon.svg (a Scrabble tile).

Requires Pillow. Outputs icon-192.png, icon-512.png, apple-touch-icon.png in the
parent directory. Re-run only when the icon design changes.
"""
import os
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..")
SERIF = "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf"
SANS = "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"

GREEN = (47, 125, 79)
TILE = (247, 233, 201)
EDGE = (216, 195, 152)
INK = (58, 51, 39)


def rounded(draw, box, r, fill, outline=None, width=1):
    draw.rounded_rectangle(box, radius=r, fill=fill, outline=outline, width=width)


def render(size):
    S = 4  # supersample for smooth edges
    W = size * S
    img = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    rounded(d, [0, 0, W, W], int(W * 0.203), GREEN)

    # tile
    m = int(W * 0.23)
    tile = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    td = ImageDraw.Draw(tile)
    rounded(td, [m, m, W - m, W - m], int(W * 0.066), TILE, outline=EDGE, width=max(2, S * 2))

    letter_font = ImageFont.truetype(SERIF, int(W * 0.42))
    d2 = ImageDraw.Draw(tile)
    d2.text((W * 0.485, W * 0.5), "B", font=letter_font, fill=INK, anchor="mm")
    pts_font = ImageFont.truetype(SANS, int(W * 0.1))
    d2.text((W * 0.7, W * 0.68), "3", font=pts_font, fill=INK, anchor="mm")

    tile = tile.rotate(6, resample=Image.BICUBIC, center=(W / 2, W / 2))
    img.alpha_composite(tile)

    img = img.resize((size, size), Image.LANCZOS)
    return img


for name, size, bg in [("icon-192.png", 192, None), ("icon-512.png", 512, None)]:
    render(size).save(os.path.join(OUT, name))

# apple-touch-icon: iOS masks its own corners, so flatten onto the green.
apple = render(180).convert("RGBA")
flat = Image.new("RGBA", apple.size, GREEN + (255,))
flat.alpha_composite(apple)
flat.convert("RGB").save(os.path.join(OUT, "apple-touch-icon.png"))
print("icons written")
