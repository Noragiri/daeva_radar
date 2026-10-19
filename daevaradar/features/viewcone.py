"""The View cone feature: locating the minimap and showing what the reader sees."""
import base64
import io

from ..common import WINDOWS
from ..vision import exclude_from_capture
from ..ui import ACCENT, MUTED, OVERLAY_KEY, draw_text
from ..feature import Feature
from .. import page as P


class Calibrator:
    """A draggable circle to put on the minimap: centre on your arrow, size to the cone's length.
    Drag: move · mouse wheel: size · right-click: done."""

    def __init__(self, feature):
        tk, view = feature.tk, feature.view
        self.feature, self.view = feature, view
        if view.cfg["x"] is None:
            root = feature.root
            view.cfg.update(x=root.winfo_screenwidth() // 2, y=root.winfo_screenheight() // 3)
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

    def __init__(self, app):
        self.view = app.view
        self.calibrator = None
        super().__init__(app)

    def page_setup(self, snap):
        blocks = [P.heading("Vision cone", "Mirrors the camera cone from the game's minimap onto the Cubes and Monsters "
                            "radars and overlays. It's read from the screen (the game traffic doesn't contain your view "
                            "direction), so the minimap must be visible: borderless or windowed mode.")]
        if not WINDOWS:
            return blocks + [P.text("Windows only for now.", color="#ff6ad5")]
        view = self.view
        if view.raw and view.ready():
            b, w_deg, conf = view.raw
            readout = ("%s\n\ndirection %3d°\nwidth     %3d°\nclarity   %.2f\n(needs %.2f)\n\nminimap at %d, %d\n"
                       "radius %d px" % ("cone found" if view.cone else "no clear cone", b, w_deg, conf,
                                         view.cfg["threshold"], view.cfg["x"], view.cfg["y"], view.cfg["r"]))
        else:
            readout = "off"
        return blocks + [
            P.text("Turn it on with the switch at the top right.", small=True),
            P.row(P.button("calibrate", "Done" if self.calibrator else "Locate minimap", primary=bool(self.calibrator)),
                  P.text("circle on your arrow, wheel to the cone's length", small=True)),
            P.section("What it sees"),
            P.row(self.preview(), P.text(readout, mono=True, color=ACCENT if view.cone else MUTED)),
            P.section("Sensitivity"),
            P.slider("threshold", view.cfg["threshold"], 0.2, 2.0, 0.05),
            P.text("Lower = shows the cone more often; higher = fewer wrong cones on busy map art.", small=True)]

    def preview(self):
        """The minimap as the reader last captured it, with the cone it read, as an image block."""
        view = self.view
        if not view.pixels:
            return P.text("not reading\n(enable + locate minimap)", small=True)
        try:
            from PIL import Image, ImageDraw
        except ImportError:
            return P.text("(the preview needs Pillow)", small=True)
        w, h, raw = view.pixels
        image = Image.frombuffer("RGBA", (w, h), bytes(raw), "raw", "BGRA", 0, 1).convert("RGB")
        zoom = 200 / max(w, h)
        image = image.resize((round(w * zoom), round(h * zoom)))
        if view.cone:
            bearing, width = view.cone
            r = view.cfg["r"] * min(zoom, 200 / w) * 0.95
            cx, cy = image.width / 2, image.height / 2
            draw = ImageDraw.Draw(image)
            # PIL angles: degrees clockwise from 3 o'clock; the bearing is clockwise from 12
            draw.pieslice((cx - r, cy - r, cx + r, cy + r), bearing - 90 - width / 2, bearing - 90 + width / 2,
                          outline="#4fd8ff", width=2)
        out = io.BytesIO()
        image.save(out, "PNG")
        return P.image("data:image/png;base64," + base64.b64encode(out.getvalue()).decode(), image.width, image.height)

    def on_calibrate(self, _):
        self.toggle_calibrate()

    def on_threshold(self, value):
        self.view.cfg["threshold"] = round(float(value), 2)
        self.view.save()

    def toggle_calibrate(self):
        if self.calibrator:
            self.calibrator.close()
            self.calibrator = None
        else:
            self.calibrator = Calibrator(self)
            self.set_enabled(True)

    def status(self, snap):
        return ("view %d°" % snap["cone"][0]) if snap.get("cone") else None

    def close(self):
        if self.calibrator:
            self.calibrator.close()
