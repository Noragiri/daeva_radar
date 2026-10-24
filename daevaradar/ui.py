"""Shared drawing and widgets: colours, pins, labels, radar rings, the overlay window, popups."""
import ctypes
import math
import sys

from .common import ASSETS, WINDOWS, load_settings, save_settings


COMPASS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")
def bearing(position, point):
    """Map bearing from you to point in degrees, 0 = map top (N), clockwise."""
    return math.degrees(math.atan2(point[0] - position[0], -(point[1] - position[1]))) % 360
def relative(position, point):
    """-> (horizontal metres, height metres, compass) from you to point. Map top = N."""
    dist = math.hypot(point[0] - position[0], point[1] - position[1]) / 100
    dz = (point[2] - position[2]) / 100
    return dist, dz, COMPASS[int((bearing(position, point) + 22.5) // 45) % 8]
FONT = "Segoe UI" if WINDOWS else "DejaVu Sans"
MONO = "Consolas" if WINDOWS else "DejaVu Sans Mono"
BG, PANEL = "#15171c", "#1d2027"
LEVEL_M = 3
HEIGHT = {"above": ("#4fd8ff", "▲"), "below": ("#ff6ad5", "▼"), "level": ("#ffd24a", "■")}
def height_class(dz):
    return "above" if dz >= LEVEL_M else "below" if dz <= -LEVEL_M else "level"
def dim(color, amount=0.55):
    """Blend a colour toward the panel background (for 'last seen')."""
    a = [int(color[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(PANEL[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(round(x + (y - x) * amount) for x, y in zip(a, b))
OUTLINE_OFFSETS = ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (1, 1), (-1, 1), (1, -1))
def draw_text(canvas, x, y, text, color, outline=PANEL, anchor="center", size=10, bold=True):
    """Canvas text with a dark outline so it stays readable over rings, pins and the game map."""
    font = (FONT, size, "bold" if bold else "normal")
    for ox, oy in OUTLINE_OFFSETS:
        canvas.create_text(x + ox, y + oy, text=text, fill=outline, anchor=anchor, font=font)
    canvas.create_text(x, y, text=text, fill=color, anchor=anchor, font=font)
def pin_points(x, y, kind, r):
    if kind == "above":
        return (x, y - r, x + r, y + r * 0.8, x - r, y + r * 0.8)
    if kind == "below":
        return (x, y + r, x + r, y - r * 0.8, x - r, y - r * 0.8)
    r -= 1
    return (x - r, y - r, x + r, y - r, x + r, y + r, x - r, y + r)
def draw_pin(canvas, x, y, kind, color, solid, r=8, halo=None):
    """▲ above, ▼ below, ■ level. Solid = in range; outline = last seen / off the edge.
    halo: a dark rim behind the pin, for busy backgrounds like the game's minimap."""
    if halo:
        canvas.create_polygon(pin_points(x, y, kind, r + 2), fill=halo if solid else "", outline=halo,
                              width=1 if solid else 4)
    canvas.create_polygon(pin_points(x, y, kind, r), fill=color if solid else "", outline=color,
                          width=1 if solid else 2)
def label_side(x, mid, width, size, gap=12):
    """Put a pin label on the side away from the centre, unless that runs off the canvas."""
    left = x < mid - 1
    if left and x - gap - width < 0:
        left = False
    elif not left and x + gap + width > size:
        left = True
    return (x - gap, "e") if left else (x + gap, "w")
def click_through(window, on):
    """Windows: let mouse clicks pass through the window to the game (or stop doing so).
    Also keeps it off the taskbar / Alt+Tab and from taking focus from the game."""
    if not WINDOWS:
        return
    user32 = ctypes.windll.user32
    user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
    user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
    # until Tk has laid the window out, wm_frame() is the inner window: made layered, its content never shows
    window.update_idletasks()
    hwnd = int(window.wm_frame(), 16)
    style = user32.GetWindowLongPtrW(hwnd, -20)  # GWL_EXSTYLE
    style |= 0x80000 | 0x80 | 0x08000000  # LAYERED | TOOLWINDOW | NOACTIVATE
    style = style | 0x20 if on else style & ~0x20  # TRANSPARENT = click-through
    user32.SetWindowLongPtrW(hwnd, -20, style)
_exe_of = {}  # pid -> lower-case exe name (a pid keeps its program)


def foreground_exe():
    """Windows: (pid, exe name) of the program whose window has the focus, or (None, None)."""
    from ctypes import wintypes
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None, None
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    pid = pid.value
    if pid not in _exe_of:
        name = ""
        kernel32.OpenProcess.restype = ctypes.c_void_p
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if handle:
            buf, size = ctypes.create_unicode_buffer(1024), wintypes.DWORD(1024)
            if kernel32.QueryFullProcessImageNameW(ctypes.c_void_p(handle), 0, buf, ctypes.byref(size)):
                name = buf.value.replace("/", "\\").rsplit("\\", 1)[-1].lower()
            kernel32.CloseHandle(ctypes.c_void_p(handle))
        if len(_exe_of) > 200:
            _exe_of.clear()
        _exe_of[pid] = name
    return pid, _exe_of[pid]


def hotkey_thread(flag):
    """Windows: global Ctrl+Shift+O (works while the game has focus) sets flag."""
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    if not user32.RegisterHotKey(None, 1, 0x2 | 0x4 | 0x4000, ord("O")):  # CTRL | SHIFT | NOREPEAT
        return
    msg = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        if msg.message == 0x312:  # WM_HOTKEY
            flag.set()
def set_icon(window):
    """Window/taskbar icon from assets/icon*.png; returns the images so they stay alive. (icon.ico is
    for the shortcut only: Tk's own .ico loading replaced the icon with a blank one.)"""
    import tkinter as tk
    try:
        # big + simplified small sizes, so the title bar / taskbar don't shrink the detailed one
        images = [tk.PhotoImage(file=str(ASSETS / name)) for name in ("icon.png", "icon-32.png", "icon-16.png")
                  if (ASSETS / name).exists()]
        window.iconphoto(True, *images)
        return images
    except (tk.TclError, OSError):
        return None
def show_error(text):
    """Errors must stay visible when there is no console (pythonw)."""
    if sys.stdout is not None and sys.stdin is not None and sys.stdin.isatty():
        print("Error:", text)
        input("Press Enter to close.")
        return
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        set_icon(root)
        messagebox.showerror("Daeva Radar", str(text), parent=root)
        root.destroy()
    except Exception:
        pass
OVERLAY_CONE = "#4fd8ff"  # the overlays' cone outline: not white, so the cone reader never mistakes it for the game's
OVERLAY_KEY = "#0b0c0e"  # colour made transparent on Windows; never used for drawing
OVERLAY_DEFAULT = dict(x=200, y=200, w=320, h=240, ppm=1.0, locked=False, shown=False, cone=True)
class Overlay:
    """Borderless, see-through layer to place over the game's minimap (north-up, like it). Each feature
    has its own, saved under its own settings key, and draws its content via owner.paint_overlay().
    Unlocked: drag to move, drag the bottom-right corner to resize, mouse wheel to change the scale,
    right-click to lock. Locked: only the feature's marks; clicks go through to the game (Windows)."""
    GRIP = 18

    def __init__(self, owner):
        """owner: the feature (needs .tk, .root, .overlay_key, .paint_overlay() and .overlay_changed())."""
        tk = owner.tk
        self.owner = owner
        self.cfg = dict(OVERLAY_DEFAULT, **load_settings().get(owner.overlay_key, {}))
        self.drag = None
        self.win = win = tk.Toplevel(owner.root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg=OVERLAY_KEY)
        if WINDOWS:
            win.attributes("-transparentcolor", OVERLAY_KEY)
        else:
            win.attributes("-alpha", 0.8)  # Tk has no colour-key transparency on Linux
        self.canvas = c = tk.Canvas(win, bg=OVERLAY_KEY, highlightthickness=0)
        c.pack(fill="both", expand=True)
        self.place()
        c.bind("<ButtonPress-1>", self.press)
        c.bind("<B1-Motion>", self.motion)
        c.bind("<ButtonRelease-1>", lambda e: self.save())
        c.bind("<Button-3>", lambda e: self.set_locked(True))
        c.bind("<MouseWheel>", lambda e: self.wheel(e.delta > 0))
        c.bind("<Button-4>", lambda e: self.wheel(True))
        c.bind("<Button-5>", lambda e: self.wheel(False))
        win.update_idletasks()
        # visible to screenshots: nothing it draws is near-white over the minimap (cone outline in OVERLAY_CONE,
        # your white dot sits inside the arrow area the cone reader skips), so it can't fool the cone reader
        self.set_locked(self.cfg["locked"])

    def place(self):
        c = self.cfg
        self.win.geometry("%dx%d+%d+%d" % (c["w"], c["h"], c["x"], c["y"]))

    def save(self):
        save_settings(dict(load_settings(), **{self.owner.overlay_key: self.cfg}))

    def press(self, e):
        resize = e.x > self.cfg["w"] - self.GRIP and e.y > self.cfg["h"] - self.GRIP
        self.drag = (e.x_root, e.y_root, dict(self.cfg), resize)

    def motion(self, e):
        if self.cfg["locked"] or not self.drag:
            return
        x0, y0, start, resize = self.drag
        dx, dy = e.x_root - x0, e.y_root - y0
        layout = getattr(self.owner.app, "layout", None)  # layout mode: snap to its grid
        if resize:
            w, h = max(80, start["w"] + dx), max(80, start["h"] + dy)
            self.cfg["w"], self.cfg["h"] = (layout.snap_size(w), layout.snap_size(h)) if layout else (w, h)
        else:
            x, y = start["x"] + dx, start["y"] + dy
            self.cfg["x"], self.cfg["y"] = layout.snap(x, y) if layout else (x, y)
        self.place()

    def wheel(self, up):
        if not self.cfg["locked"]:
            self.cfg["ppm"] = max(0.05, min(20.0, self.cfg["ppm"] * (1.1 if up else 1 / 1.1)))
            self.save()

    def set_visible(self, on):
        """Shown only while the game has the focus (App.tick)."""
        if on != (self.win.state() != "withdrawn"):
            self.win.deiconify() if on else self.win.withdraw()

    def set_locked(self, locked):
        self.cfg["locked"] = locked
        click_through(self.win, locked)
        self.save()
        self.owner.overlay_changed()

    def close(self):
        self.win.destroy()

    def to_screen(self, position, point, margin=10):
        """World point -> (x, y, on_edge): offset from you at the overlay's scale, pinned to its border
        (pointing the right way) when outside it."""
        w, h, ppm = self.cfg["w"], self.cfg["h"], self.cfg["ppm"]
        cx, cy = w / 2, h / 2
        dx = (point[0] - position[0]) / 100 * ppm
        dy = (point[1] - position[1]) / 100 * ppm
        edge = abs(dx) > cx - margin or abs(dy) > cy - margin
        if edge:
            f = min((cx - margin) / abs(dx) if dx else math.inf, (cy - margin) / abs(dy) if dy else math.inf)
            dx, dy = dx * f, dy * f
        return cx + dx, cy + dy, edge

    def draw(self, snap):
        c = self.canvas
        w, h, ppm = self.cfg["w"], self.cfg["h"], self.cfg["ppm"]
        cx, cy = w / 2, h / 2
        c.delete("all")
        if not self.cfg["locked"]:  # alignment aids
            c.create_rectangle(1, 1, w - 2, h - 2, outline=self.owner.overlay_color, width=2, dash=(6, 4))
            for metres in (25, 50, 100, 200, 500, 1000, 2000):
                r = metres * ppm
                if 18 <= r <= max(w, h):
                    c.create_oval(cx - r, cy - r, cx + r, cy + r, outline="#9fb0d0", dash=(3, 4))
                    draw_text(c, cx + 3, cy - r + 2, "%d m" % metres, "#c8d2e6", outline="#000000",
                              anchor="nw", size=8, bold=False)
            c.create_line(cx - 12, cy, cx + 12, cy, fill="#ffffff", width=2)
            c.create_line(cx, cy - 12, cx, cy + 12, fill="#ffffff", width=2)
            c.create_polygon(w - 2, h - self.GRIP, w - 2, h - 2, w - self.GRIP, h - 2, fill=self.owner.overlay_color)
            draw_text(c, 6, 6, "%s overlay · drag: move · corner: size\nwheel: scale · right-click or Ctrl+Shift+O: lock"
                      % self.owner.title, "#ffffff", outline="#000000", anchor="nw", size=8, bold=False)
        if not snap["position"]:
            if not self.cfg["locked"]:
                draw_text(c, cx, cy + 24, "waiting for your position", "#ffffff", outline="#000000", size=9)
            return
        if self.cfg["cone"]:
            draw_cone(c, cx, cy, min(w, h) / 2 - 6, snap.get("cone"), outline=OVERLAY_CONE)
        self.owner.paint_overlay(self, c, snap)
SIDEBAR, ACCENT, TEXT, MUTED, BUTTON, BUTTON_HOVER = "#101216", "#4fd8ff", "#e6e6e6", "#9aa0aa", "#2a2e37", "#3a3f4b"
def button(tk, parent, text, command, width=None, **kw):
    return tk.Button(parent, text=text, command=command, bg=BUTTON, fg=TEXT, relief="flat", bd=0,
                     activebackground=BUTTON_HOVER, activeforeground=TEXT, padx=10, pady=4,
                     font=(FONT, 10), width=width, cursor="hand2", **kw)
def heading(tk, parent, text, sub=None):
    tk.Label(parent, text=text, bg=BG, fg=TEXT, font=(FONT, 13, "bold"), anchor="w").pack(fill="x")
    if sub:
        tk.Label(parent, text=sub, bg=BG, fg=MUTED, font=(FONT, 9), anchor="w", justify="left",
                 wraplength=390).pack(fill="x", pady=(0, 8))
def section(tk, parent, text):
    tk.Label(parent, text=text.upper(), bg=BG, fg="#6b7590", font=(FONT, 8, "bold"), anchor="w").pack(
        fill="x", pady=(12, 4))
def checkbox(tk, parent, text, variable, command):
    return tk.Checkbutton(parent, text=text, variable=variable, command=command, bg=BG, fg=TEXT,
                          selectcolor=BUTTON, activebackground=BG, activeforeground=TEXT, font=(FONT, 10), anchor="w")
def draw_rings(canvas, size, range_m):
    """The radar background shared by features: distance rings, N, and you in the middle.
    -> pixels per metre."""
    mid = size / 2
    scale = (mid - 18) / range_m
    for metres in (25, 50, 100, 200, 500, 1000):
        if metres <= range_m and metres * scale > 14:
            r = metres * scale
            canvas.create_oval(mid - r, mid - r, mid + r, mid + r, outline="#3a3f4b" if metres != 100 else "#5a6580")
            canvas.create_text(mid + 4, mid - r + 3, text="%d m" % metres, fill="#6b7590", anchor="nw", font=(FONT, 8))
    draw_text(canvas, mid, 11, "N", "#9fb0d0", size=11)
    canvas.create_oval(mid - 6, mid - 6, mid + 6, mid + 6, fill="#ffffff", outline="#0b0c0f", width=2)
    return scale
def draw_me(canvas, x, y, r):
    """Your position: a white dot with a dark rim, readable on top of anything."""
    canvas.create_oval(x - r - 2, y - r - 2, x + r + 2, y + r + 2, fill="#000000", outline="")
    canvas.create_oval(x - r, y - r, x + r, y + r, fill="#ffffff", outline="")
def radar_point(position, point, mid, scale, range_m):
    """World point -> radar (x, y, beyond_range); beyond range it sits on the edge."""
    dist = math.hypot(point[0] - position[0], point[1] - position[1]) / 100
    a = math.radians(bearing(position, point))
    shown = min(dist, range_m)
    return mid + math.sin(a) * shown * scale, mid - math.cos(a) * shown * scale, dist > range_m
CUBE_SPAWN = "#ff5c5c"  # hollow cube spawn points; no other dot uses red
WATCH_COLORS = ("#7cf08a", "#ffb347", "#c58cff", "#4fd8ff", "#ffd24a", "#ff6ad5", "#9fe3ff")
CARD, CARD_EDGE, CARD_MUTED = "#141a22", "#2a3442", "#8a95a5"  # the cards over the game (panels.py, pop-ups)


class Card:
    """The pop-ups' look: a dark card with a thin edge, a coloured bar on the left, a small-caps label, a bold
    title and muted lines. Shown on top of everything without taking focus from the game; click to dismiss (or
    on_click). Fades out."""
    FADE_MS, FADE_STEPS = 600, 12

    def __init__(self, root, color=ACCENT, on_click=None):
        tk = __import__("tkinter")
        self.root, self.color, self.on_click = root, color, on_click
        self.win = win = tk.Toplevel(root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.attributes("-alpha", 0.96)
        win.configure(bg=CARD_EDGE)
        self.card = tk.Frame(win, bg=CARD)
        self.card.pack(padx=1, pady=1, fill="both")
        tk.Frame(self.card, bg=color, width=3).pack(side="left", fill="y")
        self.inner = tk.Frame(self.card, bg=CARD)
        self.inner.pack(side="left", fill="both", padx=(13, 18), pady=(10, 12))
        self.timer = None
        click_through(win, False)  # tool window, never activated: the game keeps focus

    def fill(self, label, title, lines, more=None):
        """(Re)draw the card's text: lines are [(text, colour or None)]."""
        tk = __import__("tkinter")
        for child in self.inner.winfo_children():
            child.destroy()
        head = tk.Frame(self.inner, bg=CARD)
        head.pack(fill="x")
        tk.Label(head, text=label.upper(), bg=CARD, fg=self.color, font=(FONT, 8, "bold"), anchor="w").pack(side="left")
        if more:
            tk.Label(head, text=more, bg=CARD, fg=CARD_MUTED, font=(FONT, 8), anchor="e").pack(side="right", padx=(16, 0))
        if title:
            tk.Label(self.inner, text=title, bg=CARD, fg=TEXT, font=(FONT, 12, "bold"), anchor="w",
                     justify="left").pack(fill="x", pady=(2, 0))
        for text, color in lines:
            tk.Label(self.inner, text=text, bg=CARD, fg=color or CARD_MUTED, font=(FONT, 10), anchor="w",
                     justify="left").pack(fill="x", pady=(2, 0))
        for widget in [self.win, self.card, self.inner, head] + list(self.inner.winfo_children()) + list(head.winfo_children()):
            widget.bind("<Button-1>", lambda e: self.clicked())
            widget.configure(cursor="hand2")
        self.win.update_idletasks()

    def clicked(self):
        if self.on_click:
            self.on_click()
        self.close()

    def show_for(self, seconds):
        """Fully visible, then fade out after seconds (restarts the countdown); None: until it's clicked."""
        if self.win is None:
            return
        self.win.attributes("-alpha", 0.96)
        if self.timer:
            self.win.after_cancel(self.timer)
            self.timer = None
        if seconds is not None:
            self.timer = self.win.after(int(seconds * 1000), lambda: self.fade(self.FADE_STEPS))

    def fade(self, step):
        if self.win is None:
            return
        if step <= 0:
            return self.close()
        self.win.attributes("-alpha", 0.96 * step / self.FADE_STEPS)
        self.timer = self.win.after(self.FADE_MS // self.FADE_STEPS, lambda: self.fade(step - 1))

    def close(self):
        try:
            self.win.destroy()
        except Exception:
            pass
        self.win, self.timer = None, None


class Toast:
    """A notification card on top of everything (the game too) for a few seconds, at the top centre.
    text: its title, then detail lines; label: the small-caps line above (e.g. "Update"). Click to dismiss, or
    on_click (e.g. open Settings). seconds=None: it stays until clicked."""

    def __init__(self, root, text, color=ACCENT, seconds=8, label="Daeva Radar", on_click=None):
        title, *lines = text.split("\n")
        self.card = Card(root, color, on_click)
        self.card.fill(label, title, [(line, None) for line in lines], None if seconds and seconds < 60 else "click to close")
        win = self.card.win
        win.geometry("+%d+%d" % ((root.winfo_screenwidth() - win.winfo_reqwidth()) // 2, 60))
        self.card.show_for(seconds)

    def close(self):
        self.card.close()


def draw_cone(canvas, cx, cy, length, cone, fill=None, outline=None, below=False):
    """The vision cone as a wedge from (cx, cy): filled (the app's radars) or outlined (overlays, so the
    game's minimap stays visible underneath)."""
    if not cone:
        return
    bearing_deg, width = cone
    # Tk arcs: start angle counter-clockwise from 3 o'clock; our bearing is clockwise from 12
    start = 90 - bearing_deg - width / 2
    box = (cx - length, cy - length, cx + length, cy + length)
    if fill:
        canvas.create_arc(*box, start=start, extent=width, style="pieslice", fill=fill, outline="", tags="cone")
    if outline:
        canvas.create_arc(*box, start=start, extent=width, style="pieslice", outline="#000000", width=4, tags="cone")
        canvas.create_arc(*box, start=start, extent=width, style="pieslice", outline=outline, width=2, tags="cone")
    if below:
        canvas.tag_lower("cone")  # under the rings and pins
