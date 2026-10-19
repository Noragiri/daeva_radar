"""Info panels: small always-on-top cards over the game, one per feature (the minimap overlays are ui.Overlay).
Each is saved under settings["panels"][key]: x, y, w, shown, locked and its own extras. Unlocked: drag to move,
drag the right edge to change the width, right-click to lock. Locked: clicks go through to the game (Windows).
Ctrl+Shift+O, the tray and Settings → Overlay layout lock and unlock them with the minimap overlays."""
from .common import WINDOWS, load_settings, save_settings
from .ui import FONT, click_through
from .features.pets import MAX_LEVEL

CARD, EDGE, TEXT, MUTED, FAINT = "#141a22", "#2a3442", "#e6ebf2", "#8a95a5", "#2a313c"
ACCENT = "#5ab0ff"
PANEL_DEFAULT = dict(x=40, y=260, w=300, shown=False, locked=False)


class Panel:
    """Base: a borderless card the subclass paints in paint(canvas, snap, width) -> content height.
    key: settings key; title: its name in the tray and Settings; feature: the feature it belongs to (shown only
    while that's on)."""
    key, title, feature, defaults = "", "", "", {}
    MIN_W, MAX_W, GRIP = 220, 520, 8

    def __init__(self, app):
        self.app, self.tk = app, app.tk
        self.cfg = dict(PANEL_DEFAULT, **self.defaults)
        self.cfg.update(load_settings().get("panels", {}).get(self.key, {}))
        self.win = self.canvas = None
        self.drag, self.drawn, self.height = None, None, 120
        self.hot = []  # (x0, y0, x1, y1, action) clickable areas while unlocked

    # ---- state
    def save(self):
        settings = load_settings()
        save_settings(dict(settings, panels=dict(settings.get("panels", {}), **{self.key: self.cfg})))

    def allowed(self):
        owner = self.app.features.get(self.feature)
        return owner is None or owner.enabled

    def show(self, on):
        self.cfg["shown"] = bool(on)
        self.save()
        self.sync_window()

    def sync_window(self):
        """Open or close the window to match shown + the feature being on."""
        want = self.cfg["shown"] and self.allowed()
        if want and not self.win:
            self.open()
        elif not want and self.win:
            self.win.destroy()
            self.win = self.canvas = None

    def set_locked(self, locked):
        self.cfg["locked"] = bool(locked)
        self.save()
        if self.win:
            click_through(self.win, self.cfg["locked"])
            self.win.configure(cursor="" if locked else "fleur")
            self.drawn = None

    def move(self, x=None, y=None, w=None):
        if x is not None:
            self.cfg["x"], self.cfg["y"] = int(x), int(y)
        if w is not None:
            self.cfg["w"] = int(max(self.MIN_W, min(self.MAX_W, w)))
            self.drawn = None
        self.save()
        self.place()

    def place(self):
        if self.win:
            self.win.geometry("%dx%d+%d+%d" % (self.cfg["w"], self.height, self.cfg["x"], self.cfg["y"]))

    # ---- window
    def open(self):
        tk = self.tk
        self.win = win = tk.Toplevel(self.app.root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.attributes("-alpha", 0.93)
        win.configure(bg=CARD)
        self.canvas = c = tk.Canvas(win, bg=CARD, highlightthickness=0, bd=0)
        c.pack(fill="both", expand=True)
        c.bind("<ButtonPress-1>", self.press)
        c.bind("<B1-Motion>", self.motion)
        c.bind("<ButtonRelease-1>", self.release)
        c.bind("<Button-3>", lambda e: self.set_locked(True))
        self.drawn = None
        self.place()
        win.update_idletasks()
        self.set_locked(self.cfg["locked"])

    def press(self, e):
        if self.cfg["locked"]:
            return
        for x0, y0, x1, y1, action in self.hot:
            if x0 <= e.x <= x1 and y0 <= e.y <= y1:
                self.drag = None
                return action()
        edge = e.x >= self.cfg["w"] - self.GRIP
        self.drag = ("w" if edge else "move", e.x_root, e.y_root, self.cfg["x"], self.cfg["y"], self.cfg["w"])

    def motion(self, e):
        if not self.drag:
            return
        mode, x0, y0, cx, cy, cw = self.drag
        layout = self.app.layout  # layout mode: snap to its grid
        if mode == "move":
            x, y = cx + e.x_root - x0, cy + e.y_root - y0
            self.cfg["x"], self.cfg["y"] = layout.snap(x, y) if layout else (x, y)
        else:
            w = cw + e.x_root - x0
            self.cfg["w"] = int(max(self.MIN_W, min(self.MAX_W, layout.snap_size(w) if layout else w)))
            self.drawn = None
        self.place()

    def release(self, e):
        if self.drag:
            self.drag = None
            self.save()

    # ---- drawing
    def update(self, snap):
        """Every tick (App): open / close with the feature, and repaint when the content changed."""
        self.sync_window()
        if not self.win:
            return
        content = self.content(snap)
        key = (content, self.cfg["w"], self.cfg["locked"])
        if key == self.drawn:
            return
        self.drawn = key
        c = self.canvas
        c.delete("all")
        self.hot = []
        w = self.cfg["w"]
        h = self.paint(c, content, w)
        if self.height != h:
            self.height = h
            self.place()
        c.create_rectangle(0, 0, w - 1, h - 1, outline=EDGE if self.cfg["locked"] else ACCENT,
                           dash=() if self.cfg["locked"] else (4, 3))
        if not self.cfg["locked"]:
            c.create_line(w - 3, h / 2 - 8, w - 3, h / 2 + 8, fill=ACCENT, width=2)  # width grip

    def content(self, snap):
        """-> a hashable summary of what to draw (the panel repaints only when it changes)."""
        return ()

    def paint(self, c, content, w):
        return self.height

    def pin(self, c, x, y):
        """The lock pin, top right: filled when locked; click it (unlocked) to lock."""
        color = ACCENT if self.cfg["locked"] else MUTED
        c.create_oval(x - 4, y - 7, x + 4, y + 1, outline=color, fill=color if self.cfg["locked"] else "", width=1.5)
        c.create_line(x, y + 1, x, y + 7, fill=color, width=1.5)
        self.hot.append((x - 9, y - 10, x + 9, y + 10, lambda: self.set_locked(True)))

    @staticmethod
    def bar(c, x0, x1, y, part, color, height=4):
        c.create_rectangle(x0, y, x1, y + height, fill=FAINT, outline="")
        if part > 0:
            c.create_rectangle(x0, y, x0 + (x1 - x0) * min(1.0, part), y + height, fill=color, outline="")


GENUS_COLORS = {"Cogni": "#5ab0ff", "Fera": "#ff9f43", "Natura": "#7cd37c", "Varian": "#c08cff", "Special": "#ffd24a"}


class PetPanel(Panel):
    """Pet tracker: the headline pet (your first target that isn't Lv3, else the nearby one closest to its next
    level) with how many of its soul monsters are in range, its souls to the next level; then the other
    unfinished pets whose soul monsters are in range now, by genus (click the line to fold it)."""
    key, title, feature = "pet_tracker", "Pet tracker", "Monsters"
    defaults = dict(expanded=True, rows=8)

    def __init__(self, app):
        super().__init__(app)
        self.droppers = None  # monster type -> [pets whose soul it drops]

    def pets(self):
        monsters = self.app.features.get("Monsters")
        return monsters.pets if monsters else None

    def content(self, snap):
        pets = self.pets()
        if not pets:
            return ("none",)
        if self.droppers is None:
            self.droppers = {}
            for pid, p in pets.data.pets.items():
                for s in p.get("souls", ()):
                    for t in s["types"]:
                        self.droppers.setdefault(t, []).append(pid)
        if not pets.levels and not pets.synced:
            return ("unsynced",)
        in_view = {}
        for m in snap["monsters"]:
            for pid in self.droppers.get(m["type"], ()):
                in_view[pid] = in_view.get(pid, 0) + 1
        unfinished = lambda pid: pets.levels.get(pid, 0) < MAX_LEVEL
        targets = [pid for pid in pets.cfg["targets"] if unfinished(pid)]
        near = sorted((pid for pid in in_view if unfinished(pid) and pid in pets.data.pets),
                      key=lambda pid: (pets.pet(pid)[2] - pets.pet(pid)[1], pets.name(pid)))
        head = targets[0] if targets else (near[0] if near else None)
        row = lambda pid: (pid, pets.name(pid), pets.data.pets[pid]["genus"]) + pets.pet(pid)[:3]
        rest = [row(pid) for pid in near if pid != head]
        return ("ok", row(head) + (in_view.get(head, 0),) if head else None, tuple(rest), self.cfg["expanded"])

    def toggle(self):
        self.cfg["expanded"] = not self.cfg["expanded"]
        self.save()

    def paint(self, c, content, w):
        pad, right = 14, w - 14
        self.pin(c, right - 2, 18)
        if content[0] != "ok":
            text = ("Monsters is off." if content[0] == "none" else
                    "No pet data yet: enter the world with the radar running.")
            c.create_text(pad, 18, text="PETS", fill=ACCENT, anchor="w", font=(FONT, 8, "bold"))
            c.create_text(pad, 44, text=text, fill=MUTED, anchor="nw", font=(FONT, 9), width=w - 2 * pad)
            return 84
        _, head, rest, expanded = content
        if head:
            pid, name, genus, level, souls, need, seen = head
            color = GENUS_COLORS.get(genus, ACCENT)
            c.create_text(pad, 18, text=genus.upper(), fill=color, anchor="w", font=(FONT, 8, "bold"))
            c.create_text(pad, 40, text=name, fill=TEXT, anchor="w", font=(FONT, 13, "bold"))
            c.create_text(right, 40, text=("%d in view" % seen) if seen else "none in view",
                          fill=color if seen else MUTED, anchor="e", font=(FONT, 9))
            self.bar(c, pad, right, 58, souls / need if need else 1, color, 5)
            c.create_text(pad, 76, text="%d souls to Lv%d" % (need - souls, level + 1), fill=MUTED, anchor="w",
                          font=(FONT, 9))
            c.create_text(right, 76, text="%d / %d" % (souls, need), fill=TEXT, anchor="e", font=(FONT, 9, "bold"))
            y = 96
        else:
            c.create_text(pad, 18, text="PETS", fill=ACCENT, anchor="w", font=(FONT, 8, "bold"))
            c.create_text(pad, 40, text="No target pet", fill=TEXT, anchor="w", font=(FONT, 13, "bold"))
            c.create_text(pad, 62, text="Pick targets in Settings → Monsters → Pets.", fill=MUTED, anchor="w",
                          font=(FONT, 9))
            y = 84
        if not rest:
            return y + 4
        c.create_line(pad, y, right, y, fill=FAINT)
        y += 16
        label = "%d more pet%s nearby %s" % (len(rest), "" if len(rest) == 1 else "s", "▴" if expanded else "▾")
        c.create_text(pad, y, text=label, fill=TEXT, anchor="w", font=(FONT, 9))
        self.hot.append((0, y - 10, w, y + 10, self.toggle))
        y += 14
        if not expanded:
            return y + 4
        shown, genus_now = 0, None
        for pid, name, genus, level, souls, need in sorted(rest, key=lambda r: (r[2], -r[4] / (r[5] or 1), r[1])):
            if shown >= self.cfg["rows"]:
                c.create_text(pad, y + 8, text="+%d more" % (len(rest) - shown), fill=MUTED, anchor="w",
                              font=(FONT, 8))
                y += 20
                break
            color = GENUS_COLORS.get(genus, ACCENT)
            if genus != genus_now:
                genus_now = genus
                c.create_text(pad, y + 10, text=genus.upper(), fill=color, anchor="w", font=(FONT, 8, "bold"))
                y += 22
            c.create_text(pad, y + 6, text=name, fill=TEXT, anchor="w", font=(FONT, 10))
            c.create_text(right, y + 6, text="Lv%d %d/%d" % (level, souls, need), fill=MUTED, anchor="e",
                          font=(FONT, 9))
            self.bar(c, pad, right, y + 17, souls / need if need else 1, color, 2)
            y += 26
            shown += 1
        return y + 8

PANELS = (PetPanel,)
