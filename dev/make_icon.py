"""Draw the Daeva Radar icon (needs Pillow): python dev/make_icon.py
-> assets/icon.png, icon-16.png, icon-32.png, icon.ico and dev/icon_preview.png"""
import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

out = Path(__file__).resolve().parent.parent / "assets"
N = 1024  # master size; every icon size is downsampled from it


def lerp(a, b, t):
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def master(simple=False):
    img = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    # tile: rounded square with a radial navy gradient
    grad = Image.new("RGBA", (N, N))
    gp = grad.load()
    for y in range(0, N, 4):
        for x in range(0, N, 4):
            t = min(1, math.hypot(x - N * 0.5, y - N * 0.42) / (N * 0.72))
            c = lerp((40, 50, 70), (12, 15, 21), t) + (255,)
            for dy in range(4):
                for dx in range(4):
                    gp[x + dx, y + dy] = c
    mask = Image.new("L", (N, N), 0)
    ImageDraw.Draw(mask).rounded_rectangle((24, 24, N - 24, N - 24), radius=210, fill=255)
    img.paste(grad, (0, 0), mask)

    cx, cy = N / 2, N / 2
    layer = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    cyan = (79, 216, 255)
    # radar sweep: a soft cyan wedge fading behind its leading edge
    if not simple:
        sweep = Image.new("RGBA", (N, N), (0, 0, 0, 0))
        sd = ImageDraw.Draw(sweep)
        for i in range(60):
            a0 = -95 + i
            sd.pieslice((cx - 400, cy - 400, cx + 400, cy + 400), a0, a0 + 1.2,
                        fill=cyan + (int(70 * (i / 60) ** 2),))
        layer = Image.alpha_composite(layer, sweep)
        d = ImageDraw.Draw(layer)
    # rings + crosshair
    rings = (400,) if simple else (400, 270, 140)
    for r in rings:
        d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=cyan + (150 if r == 400 else 95,),
                  width=26 if simple else 14)
    if not simple:
        d.line((cx - 400, cy, cx + 400, cy), fill=cyan + (55,), width=8)
        d.line((cx, cy - 400, cx, cy + 400), fill=cyan + (55,), width=8)
        # a blip: the height-coloured pins from the app
        bx, by, br = cx + 250, cy - 255, 34
        d.polygon([(bx, by - br), (bx + br, by + br * 0.8), (bx - br, by + br * 0.8)], fill=cyan + (255,))
        mx, my, mr = cx - 300, cy + 170, 30
        d.polygon([(mx, my + mr), (mx + mr, my - mr * 0.8), (mx - mr, my - mr * 0.8)], fill=(255, 106, 213, 255))
    img = Image.alpha_composite(img, layer.filter(ImageFilter.GaussianBlur(1.2)))

    # isometric cube
    s = 300 if simple else 235  # half-width
    h = s * 0.58  # top-face half-height
    top = (cx, cy - s * 0.18)
    T = (top[0], top[1] - h)
    L, R, B = (top[0] - s, top[1]), (top[0] + s, top[1]), (top[0], top[1] + h)
    depth = s * 1.08
    L2, B2, R2 = (L[0], L[1] + depth), (B[0], B[1] + depth), (R[0], R[1] + depth)
    glow = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    ImageDraw.Draw(glow).polygon([T, R, R2, B2, L2, L], fill=(255, 205, 70, 150))
    img = Image.alpha_composite(img, glow.filter(ImageFilter.GaussianBlur(40)))
    cube = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    c = ImageDraw.Draw(cube)
    c.polygon([L, B, B2, L2], fill=(240, 182, 45, 255))   # left face
    c.polygon([B, R, R2, B2], fill=(196, 132, 22, 255))   # right face (shade)
    c.polygon([T, R, B, L], fill=(255, 226, 110, 255))    # top face (light)
    edge = (70, 45, 6, 255)
    w = 18 if simple else 12
    for a, b in ((T, R), (R, R2), (R2, B2), (B2, L2), (L2, L), (L, T), (L, B), (B, R), (B, B2)):
        c.line((a, b), fill=edge, width=w)
    if not simple:  # a glint on the top face
        c.polygon([(T[0], T[1] + h * 0.28), (T[0] + s * 0.42, T[1] + h * 0.52),
                   (T[0], T[1] + h * 0.76), (T[0] - s * 0.42, T[1] + h * 0.52)], fill=(255, 248, 190, 255))
    img = Image.alpha_composite(img, cube)
    return img


full, simple = master(), master(simple=True)
sizes = [16, 24, 32, 48, 64, 128, 256]
frames = [(simple if n <= 24 else full).resize((n, n), Image.LANCZOS) for n in sizes]
full.resize((256, 256), Image.LANCZOS).save(out / "icon.png")
frames[-1].save(out / "icon.ico", sizes=[(n, n) for n in sizes], append_images=frames[:-1], bitmap_format="bmp")
preview = Image.new("RGBA", (sum(sizes) + 10 * len(sizes) + 10, 276), (32, 34, 40, 255))
x = 10
for n, f in zip(sizes, frames):
    preview.alpha_composite(f, (x, 266 - n))
    x += n + 10
preview.save(Path(__file__).resolve().parent / "icon_preview.png")
print("ok")
for n, f in zip(sizes, frames):
    if n in (16, 32):
        f.save(out / ("icon-%d.png" % n))
