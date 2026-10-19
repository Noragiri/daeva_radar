"""Position debug (a Settings page): your raw and converted position, where it came from, and how it's moving."""
import collections
import math
import time

from .game import MapData, project
from .ui import COMPASS
from . import page as P


class PositionDebug:
    """Follows your position every tick (App.tick); its page is drawn from that (webui: /api/page?feature=Debug)."""
    title, TABS = "Debug", ("Position",)
    HISTORY = 14  # recent position changes listed at the bottom

    def __init__(self, app):
        self.app = app
        self.history = collections.deque(maxlen=self.HISTORY)
        self.samples = collections.deque()  # (time, position, update count) over the last few seconds
        self.last = None
        self.heading = None
        self.snap = None

    def update(self, snap):
        now = time.time()
        self.snap = snap
        pos, updates = snap["position"], snap.get("position_updates", 0)
        self.samples.append((now, pos, updates))
        while self.samples and now - self.samples[0][0] > 5:
            self.samples.popleft()
        if pos is None:
            self.last = None
            return
        if self.last is not None and self.last != pos:
            dx, dy, dz = ((a - b) / 100 for a, b in zip(pos, self.last))
            if math.hypot(dx, dy) > 0.05:
                angle = math.degrees(math.atan2(dx, -dy)) % 360  # 0 = map top (N), as on the radar
                self.heading = "%3.0f° %s" % (angle, COMPASS[int((angle + 22.5) // 45) % 8])
            self.history.appendleft("%s  %9.2f %9.2f %8.2f   Δ %6.2f  z%+6.2f" % (
                time.strftime("%H:%M:%S"), pos[0] / 100, pos[1] / 100, pos[2] / 100, math.hypot(dx, dy), dz))
        self.last = pos

    def page(self, tab, snap):
        snap = self.snap or snap
        now, pos, updates = time.time(), snap["position"], snap.get("position_updates", 0)
        who = ("%s · entity #%s" % (snap["own_name"], snap["own"]) if snap["own_name"]
               else ("#%d" % snap["own"]) if snap["own"] else "not identified yet")
        rows = [("Character", who, None)]
        if pos is None:
            rows.append(("Last packet", "no position yet (teleport or change zone)", "#ff9ad9"))
            rows.append(("Packets", "%d total" % updates, None))
        else:
            x, y, z = pos
            rx, ry = project(pos)
            mx, my = MapData.to_px(pos)
            age = snap["position_age"] or 0
            t0, p0, u0 = self.samples[0] if self.samples else (now, None, updates)
            span = now - t0
            rate = (updates - u0) / span if span > 0.5 else 0
            speed = ("%.1f m/s (last %.0f s)" % (math.hypot(x - p0[0], y - p0[1]) / 100 / span, span)
                     if p0 is not None and span > 0.5 else "–")
            rows += [("World (units)", "%10.1f %10.1f %9.1f" % (x, y, z), None),
                     ("World (m)", "%10.2f %10.2f %9.2f" % (x / 100, y / 100, z / 100), None),
                     ("Radar map px", "%10.1f %10.1f" % (rx, ry), None),
                     ("4096 map px", "%10.1f %10.1f" % (mx, my), None),
                     ("Last packet", "%.1f s ago%s" % (age, "  · no aura? (walking alone sends nothing)" if age >= 5 else ""),
                      "#7cf08a" if age < 2 else "#ffd24a" if age < 10 else "#ff5c5c"),
                     ("Packet message", snap.get("position_source") or "?", None),
                     ("Packets", "%d total · %.1f / s" % (updates, rate), None),
                     ("Speed", speed, None),
                     ("Heading", self.heading or "–", None)]
        width = max(len(r[0]) for r in rows)
        return [P.heading("Position debug", "Where the radar thinks you are, and how it's updating. The game only sends "
                          "your position on teleports, dashes and aura ticks (about once a second)."),
                P.lines([("%-*s  %s" % (width, label, value), color) for label, value, color in rows]),
                P.row(P.button("copy", "Copy position", disabled=pos is None)),
                P.section("Recent changes (Δ m from the previous one)"),
                P.lines([(line, None) for line in self.history] or [("No movement yet.", "#8a95a5")])]

    def act(self, id, value):
        if id == "copy" and self.snap and self.snap["position"]:
            x, y, z = self.snap["position"]
            rx, ry = project(self.snap["position"])
            return dict(copy="world %.1f, %.1f, %.1f · map px %.1f, %.1f" % (x, y, z, rx, ry))
        return None
