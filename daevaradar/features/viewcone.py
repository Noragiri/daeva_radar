"""The View cone feature: locating the minimap and showing what the reader sees."""

from ..common import WINDOWS
from ..vision import exclude_from_capture
from ..ui import ACCENT, BG, BUTTON, FONT, MONO, MUTED, OVERLAY_KEY, PANEL, TEXT, button, draw_cone, draw_text, heading, section
from ..feature import Feature


class Calibrator:
    """A draggable circle to put on the minimap: centre on your arrow, size to the cone's length.
    Drag: move · mouse wheel: size · right-click: done."""

    def __init__(self, feature):
        tk, view = feature.tk, feature.view
        self.feature, self.view = feature, view
        if view.cfg["x"] is None:
            root = feature.root
            view.cfg.update(x=root.winfo_rootx() + root.winfo_width() // 2, y=root.winfo_rooty() + 120)
        self.win = win = tk.Toplevel(feature.root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg=OVERLAY_KEY)
        if WINDOWS:
            win.attributes("-transparentcolor", OVERLAY_KEY)
        self.canvas = c = tk.Canvas(win, bg=OVERLAY_KEY, highlightthickness=0)
        c.pack(fill="both", expand=True)
        c.bind("<ButtonPress-1>", lambda e: setattr(self, "drag", (e.x_root, e.y_root, view.cfg["x"], view.cfg["y"])))
        c.bind("<B1-Motion>", self.motion)
        c.bind("<ButtonRelease-1>", lambda e: view.save())
        c.bind("<MouseWheel>", lambda e: self.resize(e.delta > 0))
        c.bind("<Button-4>", lambda e: self.resize(True))
        c.bind("<Button-5>", lambda e: self.resize(False))
        c.bind("<Button-3>", lambda e: feature.toggle_calibrate())
        self.place()
        win.update_idletasks()
        exclude_from_capture(win)

    def place(self):
        cfg, pad = self.view.cfg, 40
        size = 2 * int(cfg["r"]) + 2 * pad
        self.win.geometry("%dx%d+%d+%d" % (size, size, int(cfg["x"]) - size // 2, int(cfg["y"]) - size // 2))
        c, mid, r = self.canvas, size / 2, cfg["r"]
        c.delete("all")
        c.create_oval(mid - r, mid - r, mid + r, mid + r, outline="#000000", width=4)
        c.create_oval(mid - r, mid - r, mid + r, mid + r, outline="#ffd24a", width=2)
        c.create_line(mid - 10, mid, mid + 10, mid, fill="#ffd24a", width=2)
        c.create_line(mid, mid - 10, mid, mid + 10, fill="#ffd24a", width=2)
        draw_text(c, mid, 8, "centre on your arrow · wheel: cone length · right-click: done", "#ffffff",
                  outline="#000000", size=8, bold=False, anchor="n")

    def motion(self, e):
        x0, y0, sx, sy = self.drag
        self.view.cfg["x"], self.view.cfg["y"] = sx + e.x_root - x0, sy + e.y_root - y0
        self.place()

    def resize(self, up):
        self.view.cfg["r"] = max(20, min(300, self.view.cfg["r"] + (4 if up else -4)))
        self.view.save()
        self.place()

    def close(self):
        self.view.save()
        self.win.destroy()
class ViewFeature(Feature):
    """Reads your camera direction from the game's minimap (its vision cone) and shares it with the other
    features' radars and overlays. Windows only."""
    title, icon, TABS = "View cone", "◔", ("Setup",)
    off_text = "The screen isn't read while it's off."

    def load_enabled(self):
        return self.app.view.cfg["enabled"]

    def store_enabled(self, on):
        self.view.cfg["enabled"] = on
        if not on:
            self.view.cone = None
        self.view.save()

    def enabled_changed(self):
        super().enabled_changed()
        if self.enabled and WINDOWS and self.view.cfg["x"] is None and not self.calibrator:
            self.toggle_calibrate()  # first time on: show where the minimap is

    def __init__(self, app, parent):
        self.view = app.view
        self.calibrator = None
        self.preview_image = None
        super().__init__(app, parent)

    def build_setup(self, f):
        tk = self.tk
        heading(tk, f, "Vision cone", "Mirrors the camera cone from the game's minimap onto the Cubes and "
                "Monsters radars and overlays. It's read from the screen (the game traffic doesn't contain "
                "your view direction), so the minimap must be visible: borderless or windowed mode.")
        if not WINDOWS:
            tk.Label(f, text="Windows only for now.", bg=BG, fg="#ff6ad5", font=(FONT, 10)).pack(fill="x")
            return
        tk.Label(f, text="Turn it on with the switch at the top right.", bg=BG, fg=MUTED, font=(FONT, 9),
                 anchor="w").pack(fill="x")
        row = tk.Frame(f, bg=BG)
        row.pack(fill="x", pady=(8, 0))
        self.calibrate_button = button(tk, row, "Locate minimap", self.toggle_calibrate, width=16)
        self.calibrate_button.pack(side="left")
        tk.Label(row, text="circle on your arrow, wheel to the cone's length", bg=BG, fg=MUTED,
                 font=(FONT, 9)).pack(side="left", padx=8)
        section(tk, f, "What it sees")
        row = tk.Frame(f, bg=BG)
        row.pack(fill="x")
        self.preview = tk.Canvas(row, width=200, height=200, bg=PANEL, highlightthickness=0)
        self.preview.pack(side="left")
        self.readout = tk.Label(row, text="", bg=BG, fg=TEXT, font=(MONO, 9), justify="left", anchor="nw")
        self.readout.pack(side="left", fill="both", padx=10)
        section(tk, f, "Sensitivity")
        row = tk.Frame(f, bg=BG)
        row.pack(fill="x")
        self.threshold = tk.DoubleVar(value=self.view.cfg["threshold"])
        tk.Scale(row, from_=0.2, to=2.0, resolution=0.05, orient="horizontal", variable=self.threshold,
                 command=lambda v: self.set_threshold(), length=250, bg=BG, fg=TEXT, troughcolor=BUTTON,
                 highlightthickness=0, activebackground=ACCENT, font=(FONT, 9), showvalue=True).pack(side="left")
        tk.Label(f, text="Lower = shows the cone more often; higher = fewer wrong cones on busy map art.",
                 bg=BG, fg=MUTED, font=(FONT, 9), anchor="w", wraplength=390, justify="left").pack(fill="x")

    def set_threshold(self):
        self.view.cfg["threshold"] = round(self.threshold.get(), 2)
        self.view.save()

    def toggle_calibrate(self):
        if self.calibrator:
            self.calibrator.close()
            self.calibrator = None
        else:
            self.calibrator = Calibrator(self)
            self.set_enabled(True)
        self.calibrate_button.config(text="Done" if self.calibrator else "Locate minimap")

    def update(self, snap, tab):
        if not WINDOWS:
            return
        c = self.preview
        c.delete("all")
        view = self.view
        if view.pixels:
            w, h, raw = view.pixels
            rgb = bytearray(w * h * 3)
            rgb[0::3], rgb[1::3], rgb[2::3] = raw[2::4], raw[1::4], raw[0::4]
            ppm = b"P6\n%d %d\n255\n" % (w, h) + bytes(rgb)
            zoom = max(1, 200 // w)
            image = self.tk.PhotoImage(data=ppm)  # Tk takes binary PPM as is (not base64)
            if zoom > 1:
                image = image.zoom(zoom)
            self.preview_image = image  # keep a reference
            c.create_image(100, 100, image=image)
            draw_cone(c, 100, 100, view.cfg["r"] * min(zoom, 200 / w) * 0.95, view.cone, outline="#4fd8ff")
        else:
            c.create_text(100, 100, text="not reading\n(enable + locate minimap)", fill=MUTED, justify="center",
                          font=(FONT, 9))
        if view.raw and view.ready():
            b, w_deg, conf = view.raw
            state = "cone found" if view.cone else "no clear cone"
            self.readout.config(text="%s\n\ndirection %3d°\nwidth     %3d°\nclarity   %.2f\n(needs %.2f)\n\n"
                                "minimap at %d, %d\nradius %d px" % (state, b, w_deg, conf, view.cfg["threshold"],
                                                                     view.cfg["x"], view.cfg["y"], view.cfg["r"]),
                                fg=ACCENT if view.cone else MUTED)
        else:
            self.readout.config(text="off", fg=MUTED)

    def status(self, snap):
        return ("view %d°" % snap["cone"][0]) if snap.get("cone") else None

    def close(self):
        if self.calibrator:
            self.calibrator.close()
