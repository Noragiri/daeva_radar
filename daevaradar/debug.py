"""Position debug window: your raw and converted position, where it came from, and how it's moving."""
import collections
import math
import time

from .common import load_settings, save_settings
from .game import MapData, project
from .ui import ACCENT, BG, COMPASS, FONT, MONO, MUTED, PANEL, TEXT, button


class PositionWindow:
    """A small always-on-top window, refreshed from App.tick() with each snapshot."""
    HISTORY = 12  # recent position changes listed at the bottom

    def __init__(self, app):
        tk = app.tk
        self.app = app
        self.win = win = tk.Toplevel(app.root)
        win.title("Daeva Radar · Position debug")
        win.configure(bg=BG)
        win.attributes("-topmost", True)
        win.resizable(False, False)
        geometry = load_settings().get("debug_geometry")
        if geometry:
            win.geometry(geometry)
        win.protocol("WM_DELETE_WINDOW", app.toggle_debug)
        self.fields = {}
        grid = tk.Frame(win, bg=BG)
        grid.pack(fill="x", padx=12, pady=(10, 4))
        for row, (key, label) in enumerate((
                ("who", "Character"), ("world", "World (units)"), ("metres", "World (m)"),
                ("radar", "Radar map px"), ("map", "4096 map px"), ("follow", "Position from"),
                ("age", "Last packet"), ("source", "Packet message"), ("rate", "Packets"), ("pet", "Pet"),
                ("fixerr", "Off at last fix"), ("speed", "Speed"),
                ("heading", "Heading"))):
            tk.Label(grid, text=label, bg=BG, fg=MUTED, font=(FONT, 9), anchor="w").grid(
                row=row, column=0, sticky="w", padx=(0, 12), pady=1)
            self.fields[key] = tk.Label(grid, text="–", bg=BG, fg=TEXT, font=(MONO, 10), anchor="w")
            self.fields[key].grid(row=row, column=1, sticky="w", pady=1)
        row = tk.Frame(win, bg=BG)
        row.pack(fill="x", padx=12, pady=(6, 4))
        button(tk, row, "Copy position", self.copy).pack(side="left")
        self.copied = tk.Label(row, text="", bg=BG, fg=ACCENT, font=(FONT, 9))
        self.copied.pack(side="left", padx=8)
        tk.Label(win, text="RECENT CHANGES (P = packet fix, m = pet estimate; Δ m from the previous one)", bg=BG, fg="#6b7590",
                 font=(FONT, 8, "bold"), anchor="w").pack(fill="x", padx=12, pady=(6, 2))
        self.history_label = tk.Label(win, text="", bg=PANEL, fg=TEXT, font=(MONO, 9), anchor="nw",
                                      justify="left", width=62, height=self.HISTORY, padx=8, pady=6)
        self.history_label.pack(fill="x", padx=12, pady=(0, 12))
        self.history = collections.deque(maxlen=self.HISTORY)
        self.samples = collections.deque()  # (time, position, update count) over the last few seconds
        self.last = None
        self.last_updates = None
        self.position = None

    def update(self, snap):
        now = time.time()
        f = self.fields
        if snap["own_name"]:
            f["who"].config(text="%s (ID %s) · entity #%s" % (snap["own_name"], snap["own_char_no"], snap["own"]))
        else:
            f["who"].config(text=("#%d" % snap["own"]) if snap["own"] else "not identified yet")
        pos = self.position = snap["position"]
        updates = snap.get("position_updates", 0)
        self.samples.append((now, pos, updates))
        while self.samples and now - self.samples[0][0] > 5:
            self.samples.popleft()
        if pos is None:
            for key in ("world", "metres", "radar", "map", "follow", "age", "source", "pet", "fixerr",
                        "speed", "heading"):
                f[key].config(text="–")
            f["age"].config(text="no position yet (teleport or change zone)", fg="#ff9ad9")
            f["rate"].config(text="%d total" % updates)
            self.last = None
            return
        x, y, z = pos
        f["world"].config(text="%10.1f %10.1f %9.1f" % (x, y, z))
        f["metres"].config(text="%10.2f %10.2f %9.2f" % (x / 100, y / 100, z / 100))
        rx, ry = project(pos)
        mx, my = MapData.to_px(pos)
        f["radar"].config(text="%10.1f %10.1f" % (rx, ry))
        f["map"].config(text="%10.1f %10.1f" % (mx, my))
        follow = snap.get("follow") or {}
        by_pet = follow.get("source") == "pet"
        f["follow"].config(text="pet + packets" if by_pet else "packets only", fg="#7cf08a" if by_pet else "#ffd24a")
        pet = snap.get("pet")
        if pet:
            moving = math.hypot(*pet["velocity"][:2]) / 100
            f["pet"].config(text="#%d · %.1f m from you · %s · %.1f s ago" % (
                pet["id"], math.dist(pet["xyz"][:2], pos[:2]) / 100,
                "moving %.0f m/s" % moving if moving else "resting", now - pet["time"]), fg=TEXT)
        else:
            f["pet"].config(text="none (summon it: it follows you)", fg=MUTED)
        err = follow.get("fix_error")
        f["fixerr"].config(text="–" if err is None else "%.1f m" % err)
        age = snap["position_age"] or 0
        stale = "  · no aura or pet? (walking alone sends nothing)" if age >= 5 and not by_pet else ""
        f["age"].config(text="%.1f s ago%s" % (age, stale),
                        fg="#7cf08a" if age < 2 else "#ffd24a" if age < 10 else "#ff5c5c")
        f["source"].config(text=snap.get("position_source") or "?")
        t0, p0, u0 = self.samples[0]
        span = now - t0
        rate = (updates - u0) / span if span > 0.5 else 0
        f["rate"].config(text="%d total · %.1f / s" % (updates, rate))
        if p0 is not None and span > 0.5:
            moved = math.hypot(x - p0[0], y - p0[1]) / 100
            f["speed"].config(text="%.1f m/s (last %.0f s)" % (moved / span, span))
        else:
            f["speed"].config(text="–")
        if self.last is not None and self.last != pos:
            dx, dy, dz = ((a - b) / 100 for a, b in zip(pos, self.last))
            if math.hypot(dx, dy) > 0.05:
                angle = math.degrees(math.atan2(dx, -dy)) % 360  # 0 = map top (N), as on the radar
                f["heading"].config(text="%3.0f° %s" % (angle, COMPASS[int((angle + 22.5) // 45) % 8]))
            fix = updates != self.last_updates
            self.history.appendleft("%s %s %9.2f %9.2f %8.2f   Δ %6.2f  z%+6.2f" % (
                time.strftime("%H:%M:%S"), "P" if fix else "m", x / 100, y / 100, z / 100, math.hypot(dx, dy), dz))
            self.history_label.config(text="\n".join(self.history))
        self.last, self.last_updates = pos, updates

    def copy(self):
        if self.position is None:
            return
        x, y, z = self.position
        rx, ry = project(self.position)
        text = "world %.1f, %.1f, %.1f · map px %.1f, %.1f" % (x, y, z, rx, ry)
        self.win.clipboard_clear()
        self.win.clipboard_append(text)
        self.copied.config(text="copied")
        self.win.after(1500, lambda: self.copied.config(text=""))

    def close(self):
        try:
            save_settings(dict(load_settings(), debug_geometry="+%d+%d" % (self.win.winfo_x(), self.win.winfo_y())))
            self.win.destroy()
        except Exception:
            pass
