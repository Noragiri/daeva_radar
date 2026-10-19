"""Overlay layout mode: a see-through grid over the game window (else the main screen) to place every overlay and
info card on the real screen. While it's on, every shown overlay is unlocked and snaps to the grid as you drag it
(ui.Overlay / panels.Panel ask App.snap); hidden ones are dashed placeholders on the grid, dragged the same way.
Done (or Esc) puts each overlay's lock back as it was and brings Settings back."""
import ctypes

from .common import WINDOWS, load_settings, save_settings
from .ui import FONT

GRIDS = (0, 10, 20, 40, 80)
TINT, LINE, MAJOR, EDGE, TEXT, MUTED, ACCENT = "#05080c", "#1c2a38", "#2f4458", "#2fd0b5", "#e6ebf2", "#8a95a5", "#2fd0b5"


def game_rect():
    """The game window's client area (screen pixels) -> (x, y, w, h), or None when it isn't running."""
    if not WINDOWS:
        return None
    from ctypes import wintypes
    from .capture import process_list
    try:
        pids = {int(pid) for pid, name in process_list().items() if name.lower() == "aion2.exe"}
    except Exception:
        return None
    if not pids:
        return None
    u = ctypes.windll.user32
    found = []

    def window(hwnd, _):
        pid = wintypes.DWORD()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids and u.IsWindowVisible(hwnd) and not u.IsIconic(hwnd):
            r, p = wintypes.RECT(), wintypes.POINT(0, 0)
            u.GetClientRect(hwnd, ctypes.byref(r))
            u.ClientToScreen(hwnd, ctypes.byref(p))
            if r.right > 200 and r.bottom > 200:
                found.append((p.x, p.y, r.right, r.bottom))
        return True
    u.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)(window), 0)
    return max(found, key=lambda r: r[2] * r[3]) if found else None


class LayoutMode:
    def __init__(self, app):
        self.app, tk = app, app.tk
        self.grid = load_settings().get("layout_grid", 20)
        game = game_rect()
        if game:
            self.rect, self.where = game, "over the game window"
        else:
            self.rect = (0, 0, app.root.winfo_screenwidth(), app.root.winfo_screenheight())
            self.where = "on the main screen (the game isn't running)"
        x, y, w, h = self.rect
        # unlock everything shown; remember how it was
        self.locks = [(cfg, set_locked, cfg["locked"]) for cfg, set_locked in app.shown_overlays()]
        app.lock_overlays(False)

        self.win = win = tk.Toplevel(app.root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.attributes("-alpha", 0.55)
        win.geometry("%dx%d+%d+%d" % (w, h, x, y))
        self.canvas = c = tk.Canvas(win, bg=TINT, highlightthickness=0, cursor="arrow")
        c.pack(fill="both", expand=True)
        c.bind("<ButtonPress-1>", self.press)
        c.bind("<B1-Motion>", self.motion)
        c.bind("<ButtonRelease-1>", self.release)
        self.drag = None

        self.bar = bar = tk.Toplevel(app.root)
        bar.overrideredirect(True)
        bar.attributes("-topmost", True)
        bar.configure(bg="#141a22", highlightthickness=1, highlightbackground="#2b3542")
        tk.Label(bar, text="Overlay layout", bg="#141a22", fg=TEXT, font=(FONT, 11, "bold")).pack(side="left", padx=(14, 6))
        tk.Label(bar, text="Drag the overlays and cards %s · grid" % self.where, bg="#141a22", fg=MUTED,
                 font=(FONT, 9)).pack(side="left")
        self.grid_buttons = {}
        for g in GRIDS:
            b = tk.Label(bar, text=("%d" % g) if g else "off", bg="#141a22", fg=MUTED, font=(FONT, 9, "bold"),
                         padx=7, pady=3, cursor="hand2")
            b.pack(side="left", padx=1)
            b.bind("<Button-1>", lambda e, g=g: self.set_grid(g))
            self.grid_buttons[g] = b
        done = tk.Label(bar, text="Done  (Esc)", bg=ACCENT, fg="#062b25", font=(FONT, 10, "bold"), padx=14, pady=5,
                        cursor="hand2")
        done.pack(side="left", padx=(12, 8), pady=6)
        done.bind("<Button-1>", lambda e: self.close())
        bar.update_idletasks()
        bar.geometry("+%d+%d" % (x + (w - bar.winfo_reqwidth()) // 2, y + 18))
        for window in (win, bar):
            window.bind("<Escape>", lambda e: self.close())
        win.focus_force()
        self.set_grid(self.grid)

    # ---- grid and snapping
    def set_grid(self, g):
        self.grid = g
        save_settings(dict(load_settings(), layout_grid=g))
        for value, b in self.grid_buttons.items():
            b.config(bg="#24303d" if value == g else "#141a22", fg=TEXT if value == g else MUTED)
        self.draw()

    def snap(self, x, y):
        """Screen position -> snapped to the grid (lines start at the game window's top-left)."""
        if not self.grid:
            return x, y
        ox, oy = self.rect[:2]
        return ox + round((x - ox) / self.grid) * self.grid, oy + round((y - oy) / self.grid) * self.grid

    def snap_size(self, v):
        return max(self.grid, round(v / self.grid) * self.grid) if self.grid else v

    def draw(self):
        c, (x0, y0, w, h) = self.canvas, self.rect
        c.delete("all")
        if self.grid:
            step = self.grid if self.grid >= 10 else 10
            for i, gx in enumerate(range(0, w, step)):
                c.create_line(gx, 0, gx, h, fill=MAJOR if i % 5 == 0 else LINE)
            for i, gy in enumerate(range(0, h, step)):
                c.create_line(0, gy, w, gy, fill=MAJOR if i % 5 == 0 else LINE)
        c.create_rectangle(1, 1, w - 2, h - 2, outline=EDGE, width=2)
        self.placeholders = []
        for o in self.app.overlay_list():
            if o["shown"]:
                continue
            tag = "ph%d" % len(self.placeholders)
            x, y = o["x"] - x0, o["y"] - y0
            shape = c.create_oval if o["kind"] == "radar" else c.create_rectangle
            shape(x, y, x + o["w"], y + o["h"], outline="#5ab0ff" if o["kind"] == "radar" else ACCENT, dash=(6, 4),
                  width=2, fill=TINT, tags=tag)
            c.create_text(x + o["w"] / 2, y + o["h"] / 2, text="%s\n(hidden%s)" % (
                o["title"], "" if o["allowed"] else ": " + o["feature"] + " is off"), fill=TEXT, justify="center",
                font=(FONT, 10, "bold"), tags=tag)
            self.placeholders.append((o, tag))
        self.lift()

    def lift(self):
        """Keep the real overlays and cards above the grid, and the bar above everything."""
        for f in self.app.features.values():
            if f.overlay:
                f.overlay.win.lift()
        for p in self.app.panels.values():
            if p.win:
                p.win.lift()
        self.bar.lift()

    # ---- dragging the hidden overlays' placeholders (the shown ones are the real windows, above the grid)
    def press(self, e):
        x0, y0 = self.rect[:2]
        for o, tag in reversed(self.placeholders):
            if o["x"] - x0 <= e.x <= o["x"] - x0 + o["w"] and o["y"] - y0 <= e.y <= o["y"] - y0 + o["h"]:
                self.canvas.tag_raise(tag)
                self.drag = (o, tag, e.x_root, e.y_root, o["x"], o["y"])
                return

    def motion(self, e):
        if not self.drag:
            return
        o, tag, sx, sy, ox, oy = self.drag
        x, y = self.snap(ox + e.x_root - sx, oy + e.y_root - sy)
        self.canvas.move(tag, x - o["x"], y - o["y"])
        o["x"], o["y"] = x, y

    def release(self, e):
        if not self.drag:
            return
        o = self.drag[0]
        self.drag = None
        self.app.web_action("overlay_move", id=o["id"], x=o["x"], y=o["y"])

    # ---- end
    def close(self):
        if not self.win:
            return
        for cfg, set_locked, locked in self.locks:
            if cfg["locked"] != locked:
                set_locked(locked)
        for f in self.app.features.values():
            f.overlay_changed()
        self.win.destroy()
        self.bar.destroy()
        self.win = None
        self.app.layout = None
        self.app.focus_seq += 1  # Settings comes back to the front
