"""Reading the vision cone from the game's minimap on screen (Windows)."""
import ctypes
import math
import threading
import time

from .common import WINDOWS, load_settings, save_settings


def screen_grab(x, y, w, h):
    """Windows: the screen pixels in (x, y, w, h) as top-down BGRA bytes (GDI, no extra packages)."""
    from ctypes import wintypes
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    user32.GetDC.restype = gdi32.CreateCompatibleDC.restype = gdi32.CreateCompatibleBitmap.restype = ctypes.c_void_p
    gdi32.SelectObject.restype = ctypes.c_void_p
    for f, args in ((user32.GetDC, [ctypes.c_void_p]), (user32.ReleaseDC, [ctypes.c_void_p, ctypes.c_void_p]),
                    (gdi32.CreateCompatibleDC, [ctypes.c_void_p]), (gdi32.DeleteDC, [ctypes.c_void_p]),
                    (gdi32.CreateCompatibleBitmap, [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]),
                    (gdi32.SelectObject, [ctypes.c_void_p, ctypes.c_void_p]), (gdi32.DeleteObject, [ctypes.c_void_p]),
                    (gdi32.BitBlt, [ctypes.c_void_p] + [ctypes.c_int] * 4 + [ctypes.c_void_p, ctypes.c_int,
                                                                              ctypes.c_int, wintypes.DWORD]),
                    (gdi32.GetDIBits, [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p,
                                       ctypes.c_void_p, ctypes.c_uint])):
        f.argtypes = args

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                    ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                    ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD),
                    ("biClrImportant", wintypes.DWORD)]

    screen = user32.GetDC(None)
    dc = gdi32.CreateCompatibleDC(screen)
    bitmap = gdi32.CreateCompatibleBitmap(screen, w, h)
    old = gdi32.SelectObject(dc, bitmap)
    try:
        gdi32.BitBlt(dc, 0, 0, w, h, screen, x, y, 0x00CC0020)  # SRCCOPY
        info = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)  # top-down
        buf = ctypes.create_string_buffer(w * h * 4)
        gdi32.GetDIBits(dc, bitmap, 0, h, buf, ctypes.byref(info), 0)
        return buf.raw
    finally:
        gdi32.SelectObject(dc, old)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(dc)
        user32.ReleaseDC(None, screen)
def exclude_from_capture(window):
    """Windows 10 2004+: keep our own overlays out of screen captures, so they never fool the cone reader."""
    if WINDOWS:
        try:
            ctypes.windll.user32.SetWindowDisplayAffinity(ctypes.c_void_p(int(window.wm_frame(), 16)), 0x11)
        except (OSError, ValueError):
            pass
_HOUGH_TRIG = [(math.cos(math.radians(t)), math.sin(math.radians(t))) for t in range(0, 180, 2)]
_RING_CACHE = {}
def _ring_pixels(w, h, r):
    """(byte offset, x, y relative to the centre) of every 2nd pixel outside 0.4 r, cached per size."""
    key = (w, h, r)
    if key not in _RING_CACHE:
        cx, cy = w / 2, h / 2
        _RING_CACHE[key] = [((py * w + px) * 4, px - cx, py - cy) for py in range(0, h, 2) for px in range(0, w, 2)
                            if math.hypot(px - cx, py - cy) >= 0.4 * r]
    return _RING_CACHE[key]
def detect_cone_edges(pixels, w, h, r):
    """AION 2's minimap draws the cone with two white edge lines (live screenshots, 2026-10-14). The camera
    sits behind your character, so the lines don't start at your arrow; they pass beside the white disc
    around it. So: find the two longest straight white lines in the ring around the arrow (a Hough
    transform over near-white pixels), take each one's outward direction, and the cone is between them.
    -> (bearing, width, strength 0..1) or None."""
    points = []
    for k, x, y in _ring_pixels(w, h, r):  # every 2nd pixel outside the arrow's disc (the lines are 2+ px)
        b, g, red = pixels[k], pixels[k + 1], pixels[k + 2]
        if b > 185 and g > 185 and red > 185 and max(b, g, red) - min(b, g, red) < 60:
            points.append((x, y))
    if len(points) < r * 0.08:
        return None
    trig = _HOUGH_TRIG
    votes = {}
    for x, y in points:
        for ti, (ct, st) in enumerate(trig):
            key = (ti, int(round((x * ct + y * st) / 2)))  # rho in 2 px bins
            votes[key] = votes.get(key, 0) + 1
    # candidates: the strongest Hough lines (each suppressed before the next), judged by length: the
    # longest unbroken white run along them. Cone edges run from the disc to the frame; icon outlines
    # are straight too, but short.
    candidates, chosen = [], []
    for (ti, rb), count in sorted(votes.items(), key=lambda kv: -kv[1]):  # ranked once
        if len(chosen) >= 10 or count < 3:
            break
        if any(abs((ti - cti + 45) % 90 - 45) <= 5 and abs(rb - crb) <= 4 for cti, crb in chosen):
            continue  # part of a line already taken
        chosen.append((ti, rb))
        ct, st = trig[ti]
        dx, dy = -st, ct  # along the line
        along = sorted(x * dx + y * dy for x, y in points if abs(x * ct + y * st - rb * 2) <= 2)
        best = run = 0.0
        for prev, cur in zip(along, along[1:]):
            run = run + (cur - prev) if cur - prev <= 8 else 0.0  # icons drawn over a line leave gaps
            best = max(best, run)
        if best < 0.35 * r:
            continue
        on = [(x, y) for x, y in points if abs(x * ct + y * st - rb * 2) <= 2]
        mx, my = sum(x for x, y in on) / len(on), sum(y for x, y in on) / len(on)
        if dx * mx + dy * my < 0:  # point it away from the arrow
            dx, dy = -dx, -dy
        candidates.append((best, math.degrees(math.atan2(dx, -dy)) % 360))
    candidates.sort(reverse=True)
    pair = None
    for i in range(len(candidates)):
        for j in range(i + 1, len(candidates)):
            gap = abs((candidates[i][1] - candidates[j][1] + 180) % 360 - 180)
            if 80 <= gap <= 150:  # the game's cone is ~110-120 degrees wide (fixed camera field of view)
                pair = (candidates[i], candidates[j])
                break
        if pair:
            break
    if not pair:
        if candidates and candidates[0][0] >= 0.45 * r:
            # only one edge visible (the other hidden under icons): the cone lies on its lighter side,
            # at the usual width
            edge = candidates[0][1]
            side = 1 if arc_light(pixels, w, h, r, edge + 10, edge + 100) >= arc_light(pixels, w, h, r, edge - 100, edge - 10) else -1
            return (edge + side * 57) % 360, 114, 0.5
        return None
    directions = [pair[0][1], pair[1][1]]
    lines = [(0, 0, min(pair[0][0], pair[1][0]))]  # strength from the shorter edge
    a1, a2 = directions
    span = (a2 - a1) % 360
    if span > 180:
        a1, span = a2, 360 - span
    if not 80 <= span <= 150:
        return None
    strength = min(1.0, lines[0][2] / (0.6 * r))
    return (a1 + span / 2) % 360, span, strength
def arc_light(pixels, w, h, r, start, end):
    """Mean brightness of the ring 0.5-0.9 r between two bearings (degrees, clockwise)."""
    cx, cy = w / 2, h / 2
    total = count = 0
    for deg in range(int(start), int(end), 3):
        a = math.radians(deg)
        for f in (0.5, 0.7, 0.9):
            px, py = int(cx + math.sin(a) * r * f), int(cy - math.cos(a) * r * f)
            if 0 <= px < w and 0 <= py < h:
                k = (py * w + px) * 4
                total += 0.114 * pixels[k] + 0.587 * pixels[k + 1] + 0.299 * pixels[k + 2]
                count += 1
    return total / count if count else 0
def detect_cone(pixels, w, h, r):
    edges = detect_cone_edges(pixels, w, h, r)
    if edges and edges[2] >= 0.5:  # two clear white edge lines: confidence 1..2
        return edges[0], edges[1], 1 + edges[2]
    return detect_cone_light(pixels, w, h, r)
def detect_cone_light(pixels, w, h, r):
    """The minimap's vision cone: a lighter wedge from your arrow at the image centre.
    -> (bearing degrees, 0 = up, clockwise; width degrees; confidence) or None.
    Brightness is sampled on rings around the arrow (skipping the arrow itself); the brightest
    ~60-degree arc is the cone, and its contrast against the rest of the ring is the confidence."""
    cx, cy, step = w / 2, h / 2, 4
    n = 360 // step
    profile = []
    for i in range(n):
        a = math.radians(i * step)
        total = count = 0
        for f in (0.4, 0.55, 0.7, 0.85):
            px, py = int(cx + math.sin(a) * r * f), int(cy - math.cos(a) * r * f)
            if 0 <= px < w and 0 <= py < h:
                k = (py * w + px) * 4
                total += 0.114 * pixels[k] + 0.587 * pixels[k + 1] + 0.299 * pixels[k + 2]
                count += 1
        profile.append(total / count if count else 0)
    window = 60 // step
    smooth = [sum(profile[(i + j - window // 2) % n] for j in range(window)) / window for i in range(n)]
    peak = max(range(n), key=smooth.__getitem__)
    ordered = sorted(profile)
    base = ordered[n // 2]
    spread = (ordered[int(n * 0.9)] - ordered[int(n * 0.1)]) or 1
    contrast = smooth[peak] - base
    # cone edges: where the lightly smoothed (12 degree) brightness falls below halfway; noisy map art
    # would otherwise cut the arc short
    soft = [(profile[(i - 1) % n] + profile[i] + profile[(i + 1) % n]) / 3 for i in range(n)]
    half = base + contrast * 0.5
    left = right = 0
    while left < n // 2 and soft[(peak - left - 1) % n] > half:
        left += 1
    while right < n // 2 and soft[(peak + right + 1) % n] > half:
        right += 1
    # centre: brightness-weighted middle of that arc
    weight = total = 0.0
    for k in range(-left, right + 1):
        excess = max(0.0, soft[(peak + k) % n] - base)
        weight += excess
        total += excess * k
    centre = (peak + (total / weight if weight else 0)) * step % 360
    width = max(30, min(140, (left + right + 1) * step))
    return centre, width, contrast / spread
VIEW_DEFAULT = dict(enabled=False, x=None, y=None, r=60, threshold=0.6)
class ViewCone:
    """Reads the vision cone from the minimap on its own thread, about 15 times a second (Windows), but
    only while something shows it. Each new reading bumps .serial so the window can redraw just the cone.
    The result also goes into every snapshot as snap['cone'] = (bearing, width) or None."""
    RATE = 15  # readings per second

    def __init__(self):
        self.cfg = dict(VIEW_DEFAULT, **load_settings().get("view", {}))
        self.cone = None  # (bearing, width)
        self.raw = None  # last detection incl. confidence
        self.pixels = None
        self.serial = 0
        self.needed = False
        self.running = True
        threading.Thread(target=self.run, daemon=True).start()

    def save(self):
        save_settings(dict(load_settings(), view=self.cfg))

    def ready(self):
        return WINDOWS and self.cfg["enabled"] and self.cfg["x"] is not None

    def update(self, needed=True):
        """needed: something will draw the cone. If nothing does, nothing is captured or analysed and the
        reading is dropped, so a stale cone never shows when it's needed again."""
        self.needed = needed
        if not (needed and self.ready()) and (self.cone or self.raw or self.pixels):
            self.cone = self.raw = self.pixels = None
            self.serial += 1

    def run(self):
        while self.running:
            if not (self.needed and self.ready()):
                time.sleep(0.1)  # idle: no capture, no analysis
                continue
            start = time.perf_counter()
            self.read_once()
            time.sleep(max(0.005, 1 / self.RATE - (time.perf_counter() - start)))

    def read_once(self):
        r = int(self.cfg["r"])
        size = 2 * r + 2
        try:
            pixels = (size, size, screen_grab(int(self.cfg["x"]) - r - 1, int(self.cfg["y"]) - r - 1, size, size))
        except OSError:
            self.pixels, self.cone = None, None
            self.serial += 1
            return
        raw = detect_cone(pixels[2], size, size, r)
        self.pixels, self.raw = pixels, raw
        bearing_deg, width, confidence = raw
        if confidence < self.cfg["threshold"]:
            cone = None
        elif self.cone:  # smooth: move most of the way, the short way round
            delta = (bearing_deg - self.cone[0] + 180) % 360 - 180
            cone = ((self.cone[0] + delta * 0.7) % 360, self.cone[1] * 0.5 + width * 0.5)
        else:
            cone = (bearing_deg, width)
        if cone != self.cone:
            self.cone = cone
            self.serial += 1
