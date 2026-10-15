"""Hidden Cubes: radar, list, minimap overlay and alert sound."""
from pathlib import Path
import math
import os

from ..common import SOUNDS, load_settings, save_settings
from ..game import TEMPLATES
from ..sound import play, voices
from ..ui import ACCENT, BG, BUTTON, BUTTON_HOVER, FONT, HEIGHT, MONO, MUTED, PANEL, TEXT, button, dim, draw_cone, draw_pin, draw_rings, draw_text, heading, height_class, label_side, radar_point, relative, section
from ..feature import Feature


class CubesFeature(Feature):
    """Hidden Cubes: radar + list, the minimap overlay, and the alert settings."""
    title, icon, TABS = "Cubes", "◈", ("Radar", "Overlay", "Settings")
    overlay_key, overlay_color = "overlay", "#4fd8ff"
    overlay_text = "A see-through layer with only the cube pins, to place over the game's minimap."
    tracks = "cubes"

    def __init__(self, app, parent):
        tk, args = app.tk, app.args
        self.range = args.range
        self.seen_detections = 0
        self.voice = tk.StringVar(value=args.voice)
        self.volume = tk.IntVar(value=args.volume)
        super().__init__(app, parent)

    # ---- Radar tab
    def build_radar(self, f):
        tk = self.tk
        self.size = size = 380
        self.canvas = tk.Canvas(f, width=size, height=size, bg=PANEL, highlightthickness=0)
        self.canvas.pack(anchor="w")
        bar = tk.Frame(f, bg=BG)
        bar.pack(fill="x", pady=(6, 0))
        button(tk, bar, "−", lambda: self.zoom(1.5), width=3).pack(side="left", padx=(0, 4))
        button(tk, bar, "+", lambda: self.zoom(1 / 1.5), width=3).pack(side="left", padx=(0, 4))
        button(tk, bar, "Clear", self.clear_cubes).pack(side="left")
        listing = tk.Frame(f, bg=BG)
        listing.pack(fill="both", expand=True, pady=(8, 0))
        self.rows = [tk.Label(listing, text="", bg=BG, fg=TEXT, anchor="w", font=(MONO, 11)) for _ in range(8)]
        for row in self.rows:
            row.pack(fill="x")

    def zoom(self, factor):
        self.range = max(25, min(2000, self.range * factor))

    def clear_cubes(self):
        with self.app.tracker.lock:
            self.app.tracker.cubes.clear()

    def draw_radar(self, snap):
        c, size = self.canvas, self.size
        mid = size / 2
        c.delete("all")
        draw_cone(c, mid, mid, mid - 18, snap.get("cone"), fill="#2b3346")  # under the rings and pins
        scale = draw_rings(c, size, self.range)
        x = 8  # legend
        for kind in ("above", "level", "below"):
            color, mark = HEIGHT[kind]
            label = "%s %s" % (mark, kind)
            c.create_text(x, size - 9, text=label, fill=color, anchor="w", font=(FONT, 9, "bold"))
            x += 8 + 7 * len(label)
        position = snap["position"]
        rows = []
        if position:
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
        for i, row in enumerate(self.rows):
            if i < len(rows):
                dist, dz, direction, cube = rows[i]
                kind = height_class(dz)
                height = "%s %3d m" % (HEIGHT[kind][1], abs(dz)) if kind != "level" else "  level"
                if cube["status"] == "nearby":
                    color = HEIGHT[kind][0]
                else:
                    color = "#5f6670" if cube["status"] == "opened" else dim(HEIGHT[kind][0], 0.35)
                row.config(text="%5d m  %-2s  %s  %-9s %4ds" % (dist, direction, height, cube["status"], cube["age"]),
                           fg=color)
            elif i == 0 and position:
                row.config(text="No cubes received yet.", fg=MUTED)
            else:
                row.config(text="")

    # ---- Overlay tab
    def build_overlay(self, f):
        self.build_overlay_tab(f)

    def paint_overlay(self, overlay, c, snap):
        position = snap["position"]
        w = overlay.cfg["w"]
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
    def build_settings(self, f):
        tk = self.tk
        heading(tk, f, "Cube settings")
        section(tk, f, "Alert when a cube appears")
        row = tk.Frame(f, bg=BG)
        row.pack(fill="x")
        menu = tk.OptionMenu(row, self.voice, *voices(), "custom", command=self.pick_voice)
        menu.config(bg=BUTTON, fg=TEXT, activebackground=BUTTON_HOVER, activeforeground=TEXT, relief="flat",
                    highlightthickness=0, width=13, font=(FONT, 10))
        menu["menu"].config(bg=BUTTON, fg=TEXT, font=(FONT, 10))
        menu.pack(side="left", padx=(0, 6))
        button(tk, row, "▶ Test", self.test_sound).pack(side="left", padx=(0, 4))
        button(tk, row, "File…", self.pick_file).pack(side="left")
        self.file_label = tk.Label(f, text="", bg=BG, fg=MUTED, font=(FONT, 9), anchor="w")
        self.file_label.pack(fill="x", pady=(4, 0))
        row = tk.Frame(f, bg=BG)
        row.pack(fill="x", pady=(6, 0))
        tk.Label(row, text="Volume", bg=BG, fg=MUTED, font=(FONT, 10)).pack(side="left")
        scale = tk.Scale(row, from_=0, to=200, orient="horizontal", variable=self.volume, showvalue=True,
                         resolution=5, length=250, bg=BG, fg=TEXT, troughcolor=BUTTON, highlightthickness=0,
                         activebackground=ACCENT, font=(FONT, 9))
        scale.pack(side="left", padx=6)
        scale.bind("<ButtonRelease-1>", lambda e: self.set_volume())
        tk.Label(row, text="%", bg=BG, fg=MUTED, font=(FONT, 10)).pack(side="left")
        section(tk, f, "Cube types tracked")
        tk.Label(f, bg=BG, fg=MUTED, font=(MONO, 9), anchor="w", justify="left", wraplength=380,
                 text="  ".join(str(t) for t in sorted(TEMPLATES)) +
                 "\nAdd more with --template ID (find ids with --log).").pack(fill="x")
        self.show_file()

    def show_file(self):
        name = Path(self.app.args.sound).name if self.app.args.sound else ""
        self.file_label.config(text="Custom file: " + name if self.voice.get() == "custom" and name else "")

    def test_sound(self):
        play(self.voice.get(), self.app.args.sound, self.volume.get())

    def pick_voice(self, voice):
        if voice == "custom" and not self.app.args.sound:
            return self.pick_file()
        save_settings(dict(load_settings(), voice=voice))
        self.show_file()
        self.test_sound()

    def pick_file(self):
        from tkinter import filedialog
        path = filedialog.askopenfilename(parent=self.root, title="Cube alert sound", initialdir=str(SOUNDS / "custom"),
                                          filetypes=[("Audio", "*.wav *.mp3"), ("WAV", "*.wav"), ("MP3", "*.mp3"),
                                                     ("All files", "*.*")])
        if not path:
            if self.voice.get() == "custom" and not self.app.args.sound:
                self.voice.set(load_settings().get("voice", "calm"))
            return
        self.app.args.sound = os.path.normpath(path)
        self.voice.set("custom")
        save_settings(dict(load_settings(), voice="custom", custom=self.app.args.sound))
        self.show_file()
        self.test_sound()

    def set_volume(self):
        save_settings(dict(load_settings(), volume=self.volume.get()))
        self.test_sound()

    # ---- loop hooks
    def background(self, snap):
        if snap["detections"] > self.seen_detections:  # alerts: whatever is shown
            new = next((c for c in snap["cubes"] if c["id"] == snap["last_detection"]), None)
            where = ""
            if new and snap["position"]:
                dist, dz, compass = relative(snap["position"], new["xyz"])
                where = " %d m %s" % (dist, compass)
            self.app.engine.say("Cube!" + where)
            play(self.voice.get(), self.app.args.sound, self.volume.get())
            self.seen_detections = snap["detections"]
        super().background(snap)

    def update(self, snap, tab):
        if tab == "Radar":
            self.draw_radar(snap)
        super().update(snap, tab)

    def status(self, snap):
        nearby = sum(1 for c in snap["cubes"] if c["status"] == "nearby")
        return "%d cube%s nearby" % (nearby, "" if nearby == 1 else "s")
