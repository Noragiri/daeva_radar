"""Empyrean Traces (feathers): their spots from the map data, live ones, and the ones you've collected."""
import math
import time

from ..common import load_settings, save_settings
from ..game import MapData, map_name
from ..sound import play
from ..ui import FONT, HEIGHT, MUTED, PANEL, dim, draw_cone, draw_me, draw_pin, draw_rings, draw_text, height_class, label_side, radar_point, relative
from ..feature import Feature
from .. import page as P


FEATHER_DEFAULT = dict(spots=True, collected=False, alert=True, me_on_top=True)
FEATHER = "#e8a23a"  # bronze, like the feather; not red (that's the cube spawns')
KNOWN_TYPES = {1200865}  # Empyrean Trace: Verteron (confirmed live 2026-10-15); other maps are learned
MARK_RANGE_M = 10  # "Mark nearest collected" only within this distance


class FeathersFeature(Feature):
    """Empyrean Traces. Their spots come from the local map data (data/maps, credit Shugo.GG) and show hollow.
    The game announces a feather as an object ('46') with a type id but no name: the first object that sits
    on a feather spot teaches the type (saved for good), and from then on live feathers show solid and beep.
    Picking one up ('56' 04 03 00 on a learned feather) marks its spot collected, per map, saved; collected
    spots are hidden unless asked for. Uses the app's map (sidebar picker, else detected)."""
    title, icon, TABS = "Feathers", "✧", ("Radar", "Settings")
    overlay_key, overlay_color = "feather_overlay", FEATHER
    overlay_text = "A see-through layer with the feather spots, to place over the game's minimap."
    tracks = "nodes"

    def __init__(self, app):
        settings = load_settings()
        self.cfg = dict(FEATHER_DEFAULT, **settings.get("feathers", {}))
        self.types = KNOWN_TYPES | set(settings.get("feather_types", []))
        self.collected = {k: {tuple(p) for p in v} for k, v in settings.get("feathers_collected", {}).items()}
        self.mapdata = app.mapdata
        self.range = 150
        self.learn_checked = 0
        self.used_seen = None  # the tracker's used_count already handled
        self.alerted = set()
        self.last_alert = 0
        self.mark_text = ""
        super().__init__(app)

    # ---- state
    def save(self):
        save_settings(dict(load_settings(), feathers=self.cfg, feather_types=sorted(self.types),
                           feathers_collected={k: sorted(v) for k, v in self.collected.items()}))

    def map_key(self):
        key = self.app.map_key()
        return key if key in self.mapdata.traces else None

    def learn(self, snap):
        """A type seen sitting on a feather spot is the feather type (gathering resources excluded)."""
        key = self.map_key()
        if not key or not snap["position"] or time.time() - self.learn_checked < 2:
            return
        self.learn_checked = time.time()
        gathering = self.app.features.get("Gathering")
        known = set(gathering.learned) if gathering else set()
        for n in snap["nodes"]:
            if n["type"] in self.types or n["type"] in known:
                continue
            if self.mapdata.trace_at(key, n["xyz"], 2):
                self.types.add(n["type"])
                self.app.engine.say("Feathers: learned type %d = Empyrean Trace" % n["type"])
                self.save()

    def note_collected(self, snap):
        """Feathers you picked up since the last tick: their spots are collected. A pickup without a type
        (the tracker's ':\xe3' message) counts when it's exactly on a feather spot."""
        if self.used_seen is None or snap["used_count"] < self.used_seen:
            self.used_seen = snap["used_count"]  # first tick, or the tracker was replaced
            return
        key = self.map_key()
        for event in snap["used"]:
            if event["n"] > self.used_seen and (event["type"] in self.types or event["type"] is None) and key:
                spot = self.mapdata.trace_at(key, event["xyz"])
                if spot and spot not in self.collected.get(key, ()):
                    self.collected.setdefault(key, set()).add(spot)
                    self.app.engine.say("Feathers: collected %s at %d, %d" % (map_name(key), *spot))
                    self.save()
        self.used_seen = snap["used_count"]

    def live(self, snap):
        """Feathers the game shows, minus ones you've collected (they stay there for everyone else)."""
        key = self.map_key()
        done = self.collected.get(key, set())
        return [n for n in snap["nodes"] if n["type"] in self.types
                and not (done and self.mapdata.trace_at(key, n["xyz"]) in done)]

    def spots(self, snap, radius_m):
        """-> open [(px, world)], collected [(px, world)] within radius, leaving out spots with a live feather."""
        key = self.map_key()
        if not self.cfg["spots"] or not key or not snap["position"]:
            return [], []
        taken = [MapData.to_px(n["xyz"]) for n in self.live(snap)]
        done = self.collected.get(key, set())
        open_, collected = [], []
        for spot, xyz in self.mapdata.traces_nearby(key, snap["position"], radius_m):
            if any(math.hypot(spot[0] - x, spot[1] - y) <= 3 for x, y in taken):
                continue
            (collected if spot in done else open_).append((spot, xyz))
        return open_, collected

    # ---- Radar tab
    def page_radar(self, snap):
        c = P.Canvas(380, 380, PANEL)
        rows = self.draw_radar(c, snap)
        return [c.block(),
                P.row(P.button("zoom", "−", 1.5), P.button("zoom", "+", 1 / 1.5),
                      P.button("mark", "✓ Mark nearest collected"), P.button("unmark", "Undo")),
                P.text(self.mark_text, small=True) if self.mark_text else None,
                P.lines(rows)]

    def on_zoom(self, factor):
        self.range = max(25, min(2000, self.range * float(factor)))

    def on_mark(self, _):
        self.mark_nearest()

    def on_unmark(self, _):
        self.unmark_nearest()

    def nearest_spot(self, collected):
        """The nearest open (or collected) spot within MARK_RANGE_M of you -> (key, spot) or (key, None)."""
        snap = self.app.snapshot()
        key = self.map_key()
        if not key or not snap["position"]:
            return key, None
        done = self.collected.get(key, set())
        near = [(relative(snap["position"], xyz)[0], spot)
                for spot, xyz in self.mapdata.traces_nearby(key, snap["position"], MARK_RANGE_M)
                if (spot in done) == collected]
        return key, min(near)[1] if near else None

    def mark_nearest(self):
        key, spot = self.nearest_spot(collected=False)
        if spot:
            self.collected.setdefault(key, set()).add(spot)
            self.save()
        self.mark_text = ("Marked the nearest spot collected." if spot else
                          "No open feather spot within %d m." % MARK_RANGE_M)

    def unmark_nearest(self):
        key, spot = self.nearest_spot(collected=True)
        if spot:
            self.collected[key].discard(spot)
            self.save()
        self.mark_text = ("Unmarked the nearest collected spot." if spot else
                          "No collected spot within %d m." % MARK_RANGE_M)

    def draw_radar(self, c, snap):
        """Draws the radar on c -> the list lines [(text, colour)]."""
        size = c.w
        mid = size / 2
        draw_cone(c, mid, mid, mid - 18, snap.get("cone"), fill="#2b3346")
        scale = draw_rings(c, size, self.range)
        legend = [("▲ ■ ▼ live", FEATHER), ("○ spot", FEATHER)] + ([("○ collected", dim(FEATHER))] if self.cfg["collected"] else [])
        x = 8
        for label, color in legend:
            c.create_text(x, size - 9, text=label, fill=color, anchor="w", font=(FONT, 9, "bold"))
            x += 8 + 7 * len(label)
        position = snap["position"]
        if not position:
            draw_text(c, mid, mid + 30, "waiting for your position\n(teleport)", "#b8c0cc", size=10, bold=False)
            return []
        open_, collected = self.spots(snap, self.range)
        if self.cfg["collected"]:
            for _, xyz in collected:
                x, y, _ = radar_point(position, xyz, mid, scale, self.range)
                c.create_oval(x - 3, y - 3, x + 3, y + 3, outline=dim(FEATHER), width=1)
        for _, xyz in open_:
            x, y, _ = radar_point(position, xyz, mid, scale, self.range)
            c.create_oval(x - 5, y - 5, x + 5, y + 5, outline=FEATHER, width=2)
        live = sorted(self.live(snap), key=lambda n: -math.dist(n["xyz"][:2], position[:2]))
        for n in live:
            x, y, beyond = radar_point(position, n["xyz"], mid, scale, self.range)
            dist, dz, _ = relative(position, n["xyz"])
            draw_pin(c, x, y, height_class(dz), FEATHER, solid=not beyond, halo="#000000")
            label = "%d m" % dist + (" %s%d" % ("+" if dz > 0 else "−", abs(dz)) if height_class(dz) != "level" else "")
            lx, anchor = label_side(x, mid, 8 * len(label), size)
            draw_text(c, lx, y, label, FEATHER, anchor=anchor)
        if self.cfg["me_on_top"]:
            draw_me(c, mid, mid, 6)
        lines = []
        for n in reversed(live):
            dist, dz, direction = relative(position, n["xyz"])
            kind = height_class(dz)
            height = "%s %3d m" % (HEIGHT[kind][1], abs(dz)) if kind != "level" else "  level"
            lines.append(("  %5d m  %-2s  %s  live" % (dist, direction, height), FEATHER))
        for dist, direction in sorted(relative(position, xyz)[::2] for _, xyz in open_):
            lines.append(("○ %5d m  %-2s         spot" % (dist, direction), dim(FEATHER, 0.25)))
        lines = lines[:12] + [(self.summary(), MUTED)]
        if len(lines) == 1:
            lines.insert(0, (self.empty_text(), MUTED))
        return lines

    def empty_text(self):
        if not self.app.map_key():
            return "Map not known yet: pick it in Settings → General."
        if not self.map_key():
            return "No feather data for %s." % map_name(self.app.map_key())
        return "No feather spots within %d m." % self.range

    def summary(self):
        key = self.map_key()
        if not key:
            return ""
        learned = "type learned" if self.types else "type not learned yet: fly to a feather"
        return "%s: %d / %d collected · %s" % (map_name(key), len(self.collected.get(key, ())),
                                              len(self.mapdata.traces[key]), learned)

    # ---- Overlay
    def paint_overlay(self, overlay, c, snap):
        position = snap["position"]
        w, h = overlay.cfg["w"], overlay.cfg["h"]
        open_, collected = self.spots(snap, math.hypot(w, h) / 2 / overlay.cfg["ppm"])
        for spots, r, color in ((collected if self.cfg["collected"] else [], 3, dim(FEATHER)), (open_, 5, FEATHER)):
            for _, xyz in spots:
                x, y, edge = overlay.to_screen(position, xyz)
                if not edge:
                    c.create_oval(x - r - 2, y - r - 2, x + r + 2, y + r + 2, outline="#000000", width=4)
                    c.create_oval(x - r, y - r, x + r, y + r, outline=color, width=2)
        for n in self.live(snap):
            x, y, edge = overlay.to_screen(position, n["xyz"])
            dist, dz, _ = relative(position, n["xyz"])
            kind = height_class(dz)
            draw_pin(c, x, y, kind, FEATHER, solid=not edge, r=7, halo="#000000")
            label = "%d m" % dist + (" %s%d" % ("+" if dz > 0 else "−", abs(dz)) if kind != "level" else "")
            lx, anchor = label_side(x, w / 2, 7 * len(label), w, gap=10)
            draw_text(c, lx, y, label, FEATHER, outline="#000000", anchor=anchor, size=9)
        if self.cfg["me_on_top"]:
            draw_me(c, w / 2, h / 2, 5)

    # ---- Settings tab
    FLAGS = (("spots", "Feather spots from the map data (hollow)"),
             ("collected", "Collected spots too (dimmed)"),
             ("me_on_top", "Your position on top (radar and overlay)"),
             ("alert", "Beep when a feather appears"))

    def page_settings(self, snap):
        key = self.map_key()
        blocks = [P.heading("Feather settings", "Spots from the map data. The feather id is learned when one is near you; picking one up marks it collected."),
                  P.section("Show")]
        blocks += [P.toggle("flag_" + k, text, self.cfg[k]) for k, text in self.FLAGS]
        blocks += [P.section("Feather type ids"),
                   P.text(", ".join(str(t) for t in sorted(self.types)) or "None yet. Fly near a feather with this "
                          "feature on.", mono=True, small=True),
                   P.row(P.button("forget_types", "Forget learned ids")),
                   P.section("Collected"),
                   P.lines([("%s: %d / %d" % (map_name(k), len(self.collected.get(k, ())), len(v)), None)
                            for k, v in sorted(self.mapdata.traces.items())], mono=False),
                   P.row(P.button("reset_collected", "Reset collected on this map",
                                  disabled=not (key and self.collected.get(key)),
                                  confirm="Forget the %d feathers collected on %s?" % (
                                      len(self.collected.get(key, ())), map_name(key)) if key else None))]
        return blocks

    def on_forget_types(self, _):
        self.types = set(KNOWN_TYPES)
        self.save()

    def on_reset_collected(self, _):
        key = self.map_key()
        if key and self.collected.get(key):
            self.collected[key].clear()
            self.save()

    # ---- loop hooks
    def background(self, snap):
        self.learn(snap)
        self.note_collected(snap)
        present = {n["id"] for n in self.live(snap)}
        new = present - self.alerted
        if new and self.cfg["alert"] and time.time() - self.last_alert > 3:
            self.last_alert = time.time()
            self.app.engine.say("Feather nearby")
            play("system beep")
        self.alerted = present
        super().background(snap)

    def status(self, snap):
        n = len(self.live(snap))
        return "%d feather%s" % (n, "" if n == 1 else "s") if n else None
