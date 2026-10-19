"""Hidden Cubes: radar, list, minimap overlay and alert sound."""
from pathlib import Path
import math
import os

from ..common import SOUNDS, load_settings, save_settings
from ..game import TEMPLATES, MapData, map_name
from ..sound import play, voices
from ..ui import CUBE_SPAWN, FONT, HEIGHT, MUTED, PANEL, dim, draw_cone, draw_pin, draw_rings, draw_text, height_class, label_side, radar_point, relative
from ..feature import Feature
from .. import page as P
from ..haptics import MAX_POSITION_AGE_S, Lovense, level_for


CUBES_DEFAULT = dict(spawns=True, haptics_on=False, haptics_host="", haptics_port=20010, haptics_max=10,
                     haptics_range=60)  # Lovense: strongest level 1-20, and the distance (m) it starts at


class CubesFeature(Feature):
    """Hidden Cubes: radar + list, the minimap overlay, and the alert settings. Spawn points from the local
    map data (data/maps, credit Shugo.GG) show as hollow circles, so you can fly around them, on the app's map
    (sidebar picker, else detected)."""
    title, icon, TABS = "Cubes", "◈", ("Radar", "Settings")
    overlay_key, overlay_color = "overlay", "#4fd8ff"
    overlay_text = "A see-through layer with only the cube pins, to place over the game's minimap."
    tracks = "cubes"

    def __init__(self, app):
        args = app.args
        self.range = args.range
        self.seen_detections = 0
        self.cfg = dict(CUBES_DEFAULT, **load_settings().get("cubes", {}))
        self.mapdata = app.mapdata
        self.voice, self.volume = args.voice, args.volume
        self.toy = Lovense(app.engine.say)
        self.toy.configure(self.cfg["haptics_host"], self.cfg["haptics_port"])
        self.haptic = None  # (level, distance m) now, for the Settings page
        super().__init__(app)

    # ---- Radar tab
    def page_radar(self, snap):
        c = P.Canvas(380, 380, PANEL)
        rows = self.draw_radar(c, snap)
        return [c.block(), P.row(P.button("zoom", "−", 1.5), P.button("zoom", "+", 1 / 1.5), P.button("clear", "Clear")),
                P.lines(rows)]

    def on_zoom(self, factor):
        self.range = max(25, min(2000, self.range * float(factor)))

    def on_clear(self, _):
        self.clear_cubes()

    # ---- spawn points from the map data
    def map_key(self):
        key = self.app.map_key()
        return key if key in self.mapdata.cubes else None

    def spawns(self, snap, radius_m):
        """Spawn points within radius_m that no tracked cube (opened ones too) is sitting on."""
        key = self.map_key()
        if not self.cfg["spawns"] or not key or not snap["position"]:
            return []
        taken = [MapData.to_px(c["xyz"]) for c in snap["cubes"]]
        out = []
        for xyz in self.mapdata.cubes_nearby(key, snap["position"], radius_m):
            px, py = MapData.to_px(xyz)
            if not any(math.hypot(px - x, py - y) <= 3 for x, y in taken):
                out.append(xyz)
        return out

    def clear_cubes(self):
        with self.app.tracker.lock:
            self.app.tracker.cubes.clear()

    def draw_radar(self, c, snap):
        """Draws the radar on c -> the list lines [(text, colour)]."""
        size = c.w
        mid = size / 2
        draw_cone(c, mid, mid, mid - 18, snap.get("cone"), fill="#2b3346")  # under the rings and pins
        scale = draw_rings(c, size, self.range)
        x = 8  # legend
        legend = [("%s %s" % (HEIGHT[kind][1], kind), HEIGHT[kind][0]) for kind in ("above", "level", "below")]
        if self.cfg["spawns"]:
            legend.append(("○ spawn point", CUBE_SPAWN))
        for label, color in legend:
            c.create_text(x, size - 9, text=label, fill=color, anchor="w", font=(FONT, 9, "bold"))
            x += 8 + 7 * len(label)
        position = snap["position"]
        rows = []
        spawns = self.spawns(snap, self.range) if position else []
        if position:
            for xyz in spawns:  # hollow, under the cube pins
                x, y, _ = radar_point(position, xyz, mid, scale, self.range)
                c.create_oval(x - 4, y - 4, x + 4, y + 4, outline=CUBE_SPAWN, width=2)
            for cube in snap["cubes"]:
                dist, dz, direction = relative(position, cube["xyz"])
                rows.append((dist, dz, direction, cube))
            rows.sort(key=lambda r: (r[3]["status"] == "opened", r[3]["status"] != "nearby", r[0]))
            for dist, dz, direction, cube in reversed(rows):
                if cube["status"] == "opened":
                    continue
                kind = height_class(dz)
                nearby = cube["status"] == "nearby"
                color = HEIGHT[kind][0] if nearby else dim(HEIGHT[kind][0])
                x, y, beyond = radar_point(position, cube["xyz"], mid, scale, self.range)
                draw_pin(c, x, y, kind, color, solid=nearby and not beyond)
                label = "%d m" % dist
                if kind != "level":
                    label += " %s%d" % ("+" if dz > 0 else "−", abs(dz))  # the pin's shape already says ▲/▼
                lx, anchor = label_side(x, mid, 8 * len(label), size)  # rough bold 10 pt width
                draw_text(c, lx, y, label, color, anchor=anchor)
        else:
            draw_text(c, mid, mid + 30, "waiting for your position\n(teleport)", "#b8c0cc", size=10, bold=False)
        out = []
        for dist, dz, direction, cube in rows:
            kind = height_class(dz)
            height = "%s %3d m" % (HEIGHT[kind][1], abs(dz)) if kind != "level" else "  level"
            if cube["status"] == "nearby":
                color = HEIGHT[kind][0]
            else:
                color = "#5f6670" if cube["status"] == "opened" else dim(HEIGHT[kind][0], 0.35)
            out.append(("%5d m  %-2s  %s  %-9s %4ds" % (dist, direction, height, cube["status"], cube["age"]), color))
        if position and not rows:
            out.append(("No cubes received yet.", MUTED))
        if position and self.cfg["spawns"]:
            out.append((self.spawn_text(position, spawns), CUBE_SPAWN))
        return out

    def spawn_text(self, position, spawns):
        if not self.app.map_key():
            return "○ spawn points: map not known yet"
        if not self.map_key():
            return "○ no spawn point data for %s" % map_name(self.app.map_key())
        if not spawns:
            return "○ no spawn points within %d m" % self.range
        dist, _, direction = min(relative(position, xyz) for xyz in spawns)
        return "○ %d spawn point%s within %d m, nearest %d m %s" % (
            len(spawns), "" if len(spawns) == 1 else "s", self.range, dist, direction)

    # ---- Overlay
    def paint_overlay(self, overlay, c, snap):
        position = snap["position"]
        w = overlay.cfg["w"]
        for xyz in self.spawns(snap, math.hypot(w, overlay.cfg["h"]) / 2 / overlay.cfg["ppm"]):
            x, y, edge = overlay.to_screen(position, xyz)
            if not edge:
                c.create_oval(x - 6, y - 6, x + 6, y + 6, outline="#000000", width=4)
                c.create_oval(x - 4, y - 4, x + 4, y + 4, outline=CUBE_SPAWN, width=2)
        cubes = sorted(snap["cubes"], key=lambda cube: -math.dist(cube["xyz"][:2], position[:2]))
        for cube in cubes:  # nearest drawn last, on top
            if cube["status"] == "opened":
                continue
            dist, dz, _ = relative(position, cube["xyz"])
            kind = height_class(dz)
            nearby = cube["status"] == "nearby"
            color = HEIGHT[kind][0] if nearby else dim(HEIGHT[kind][0], 0.35)
            x, y, edge = overlay.to_screen(position, cube["xyz"])
            draw_pin(c, x, y, kind, color, solid=nearby and not edge, r=7, halo="#000000")
            label = "%d m" % dist
            if kind != "level":
                label += " %s%d" % ("+" if dz > 0 else "−", abs(dz))
            lx, anchor = label_side(x, w / 2, 7 * len(label), w, gap=10)
            draw_text(c, lx, y, label, color, outline="#000000", anchor=anchor, size=9)

    # ---- Settings tab
    @staticmethod
    def custom_files():
        folder = SOUNDS / "custom"
        return sorted(str(p) for p in folder.glob("*") if p.suffix.lower() in (".wav", ".mp3")) if folder.exists() else []

    def page_settings(self, snap):
        voices_ = [(v, v) for v in voices()] + ([("custom", "Your own file")] if self.custom_files() else [])
        blocks = [P.heading("Cube settings"), P.section("Alert when a cube appears"),
                  P.row(P.choice("voice", voices_, self.voice, style="select"), P.button("test", "▶ Test"))]
        if self.voice == "custom":
            files = self.custom_files()
            blocks.append(P.row(P.choice("custom", [(f, Path(f).name) for f in files], self.app.args.sound,
                                         label="File", style="select"),
                                P.button("open_custom", "Open folder")))
        blocks += [P.slider("volume", self.volume, 0, 200, 5, " %", label="Volume"),
                   P.text("Your own WAV or MP3: put it in sounds/custom/ (Open folder), then pick it here.", small=True),
                   P.section("Show"),
                   P.toggle("flag_spawns", "Spawn points from the map data", self.cfg["spawns"],
                            hint="Hollow red circles; © NCSOFT, via Shugo.GG."),
                   P.section("Haptics (Lovense)"),
                   P.text("The closer you get to a cube, the stronger the vibration. Open Lovense Remote on your phone → "
                          "Discover → Game Mode, switch it on, and type the IP and port it shows (same Wi-Fi as this PC). "
                          "Nothing leaves your network. Your position only updates on dashes, teleports and aura ticks, so "
                          "keep an aura on.", small=True),
                   P.toggle("flag_haptics_on", "Vibrate as I get close to a cube", self.cfg["haptics_on"]),
                   P.row(P.field("haptics_host", self.cfg["haptics_host"], "IP, e.g. 192.168.1.20", label="IP", width=200),
                         P.number("haptics_port", self.cfg["haptics_port"], 1, 65535, label="Port")),
                   P.slider("haptics_max", self.cfg["haptics_max"], 1, 20, 1, "", label="Strongest level"),
                   P.slider("haptics_range", self.cfg["haptics_range"], 10, 150, 5, " m", label="Starts at"),
                   P.row(P.button("haptics_test", "Test"), P.button("haptics_stop", "Stop now")),
                   P.text(self.haptic_text(), small=True),
                   P.section("Cube types tracked"),
                   P.text("  ".join(str(t) for t in sorted(TEMPLATES)) + "\nAdd more with --template ID (find ids with --log).",
                          mono=True, small=True)]
        return blocks

    def test_sound(self):
        play(self.voice, self.app.args.sound, self.volume)

    def on_voice(self, voice):
        if voice == "custom" and not (self.app.args.sound and os.path.exists(self.app.args.sound)):
            files = self.custom_files()
            if not files:
                return
            self.app.args.sound = files[0]
        self.voice = voice
        save_settings(dict(load_settings(), voice=voice, custom=self.app.args.sound))
        self.test_sound()

    def on_custom(self, path):
        if path in self.custom_files():
            self.app.args.sound = os.path.normpath(path)
            save_settings(dict(load_settings(), voice="custom", custom=self.app.args.sound))
            self.test_sound()

    def on_open_custom(self, _):
        folder = SOUNDS / "custom"
        folder.mkdir(parents=True, exist_ok=True)
        if hasattr(os, "startfile"):
            os.startfile(str(folder))

    def on_test(self, _):
        self.test_sound()

    def on_volume(self, value):
        self.volume = self.app.args.volume = int(value)
        save_settings(dict(load_settings(), volume=self.volume))
        self.test_sound()

    def save(self):
        save_settings(dict(load_settings(), cubes=self.cfg))

    # ---- haptics
    def haptic_text(self):
        if not self.cfg["haptics_on"]:
            return "Off. Test works even when it's off."
        if not self.toy.ready:
            return "Enter the IP shown in Lovense Remote's Game Mode."
        now = "" if not self.haptic else " · now level %d, nearest cube %d m" % (self.haptic[0], self.haptic[1])
        return self.toy.status + now

    def apply_haptics(self, snap):
        """Every tick: the nearest cube you can still reach sets the level; any doubt means 0."""
        level, nearest = 0, None
        if (self.cfg["haptics_on"] and self.toy.ready and self.enabled and snap["position"] and
                (snap["position_age"] or 0) <= MAX_POSITION_AGE_S and self.app.overlays_visible):
            near = [math.dist(c["xyz"][:2], snap["position"][:2]) / 100 for c in snap["cubes"]
                    if c["status"] == "nearby"]
            nearest = min(near, default=None)
            level = level_for(nearest, self.cfg["haptics_range"], self.cfg["haptics_max"])
        self.haptic = (level, round(nearest)) if level else None
        self.toy.update(level)

    def on_haptics_host(self, value):
        self.cfg["haptics_host"] = (value or "").strip()
        self.toy.configure(self.cfg["haptics_host"], self.cfg["haptics_port"])
        self.save()

    def on_haptics_port(self, value):
        self.cfg["haptics_port"] = int(value)
        self.toy.configure(self.cfg["haptics_host"], self.cfg["haptics_port"])
        self.save()

    def on_haptics_max(self, value):
        self.cfg["haptics_max"] = int(value)
        self.save()

    def on_haptics_range(self, value):
        self.cfg["haptics_range"] = int(value)
        self.save()

    def on_haptics_test(self, _):
        self.toy.test(min(self.cfg["haptics_max"], 5))

    def on_haptics_stop(self, _):
        self.toy.stop()
        self.toy.test_until = 0
        self.cfg["haptics_on"] = False
        self.save()

    # ---- loop hooks
    def background(self, snap):
        if snap["detections"] > self.seen_detections:  # alerts: whatever is shown
            new = next((c for c in snap["cubes"] if c["id"] == snap["last_detection"]), None)
            where = ""
            if new and snap["position"]:
                dist, dz, compass = relative(snap["position"], new["xyz"])
                where = " %d m %s" % (dist, compass)
            self.app.engine.say("Cube!" + where)
            play(self.voice, self.app.args.sound, self.volume)
            self.seen_detections = snap["detections"]
        self.apply_haptics(snap)
        super().background(snap)

    def set_enabled(self, on):
        super().set_enabled(on)
        if not on:
            self.toy.stop()

    def close(self):
        self.toy.close()

    def status(self, snap):
        nearby = sum(1 for c in snap["cubes"] if c["status"] == "nearby")
        return "%d cube%s nearby" % (nearby, "" if nearby == 1 else "s")
