"""The Feature base: one sidebar entry with its own tabs, On/Off switch, overlay and settings."""

from .common import WINDOWS, load_settings, save_settings
from .ui import ACCENT, BG, BUTTON, FONT, MUTED, OVERLAY_DEFAULT, Overlay, TEXT, button, checkbox, heading, section


class Feature:
    """One sidebar entry: a self-contained feature with its own tabs, overlay and settings.
    To add a feature: subclass, set title/icon/TABS, implement build_<tab>(frame) for each tab, list the
    class in App.FEATURES. For an overlay: set overlay_key, implement paint_overlay(), and call
    build_overlay_tab() from build_overlay().
      background(snap): every tick, whatever is shown (alerts, the overlay)
      update(snap, tab): every tick, only while this feature and that tab are shown"""
    title, icon, TABS = "", "", ()
    overlay_key, overlay_color, overlay_text = None, ACCENT, ""
    tracks = None  # what the tracker parses for this feature ("cubes", "monsters"); skipped while every user is off
    off_text = "Nothing is tracked, alerted or drawn while it's off."

    def __init__(self, app, parent):
        self.app, self.tk, self.root = app, app.tk, app.root
        self.overlay = None
        self.enabled = self.load_enabled()
        tk = self.tk
        self.frame = tk.Frame(parent, bg=BG)
        bar = tk.Frame(self.frame, bg=BG)
        bar.pack(fill="x", pady=(0, 10))
        self.body = tk.Frame(self.frame, bg=BG)
        self.body.pack(fill="both", expand=True)
        self.tabs, self.tab_buttons, self.tab = {}, {}, None
        for name in self.TABS:
            self.tabs[name] = tk.Frame(self.body, bg=BG)
            getattr(self, "build_" + name.lower().replace(" ", "_"))(self.tabs[name])
            b = tk.Label(bar, text=name, bg=BG, fg=MUTED, font=(FONT, 10, "bold"), padx=12, pady=5, cursor="hand2")
            b.pack(side="left")
            b.bind("<Button-1>", lambda e, n=name: self.show_tab(n))
            self.tab_buttons[name] = b
        self.switch = tk.Label(bar, text="", font=(FONT, 10, "bold"), padx=12, pady=5, cursor="hand2")
        self.switch.pack(side="right")
        self.switch.bind("<Button-1>", lambda e: self.set_enabled(not self.enabled))
        self.banner = tk.Label(self.frame, text="%s is off. %s Settings can still be changed." % (self.title, self.off_text),
                               bg="#2a2230", fg="#ff9ad9", font=(FONT, 9), anchor="w", justify="left", wraplength=400,
                               padx=10, pady=6)
        saved = load_settings().get("tabs", {}).get(self.title)
        self.show_tab(saved if saved in self.tabs else self.TABS[0])
        if self.enabled and self.overlay_key and load_settings().get(self.overlay_key, {}).get("shown"):
            self.toggle_overlay()
        self.enabled_changed()
        self.overlay_changed()

    # ---- on / off
    def load_enabled(self):
        return load_settings().get("features", {}).get(self.title, True)

    def store_enabled(self, on):
        settings = load_settings()
        save_settings(dict(settings, features=dict(settings.get("features", {}), **{self.title: on})))

    def set_enabled(self, on):
        if on == self.enabled:
            return
        self.enabled = on
        self.store_enabled(on)
        if self.overlay_key:
            if not on and self.overlay:  # hide it, but remember it was shown for when it's back on
                self.overlay.close()
                self.overlay = None
            elif on and load_settings().get(self.overlay_key, {}).get("shown"):
                self.toggle_overlay()
        self.enabled_changed()
        self.overlay_changed()

    def enabled_changed(self):
        """Switch, banner, sidebar entry, and what the tracker parses."""
        self.switch.config(text="● On" if self.enabled else "○ Off", bg="#1f3a2a" if self.enabled else BUTTON,
                           fg="#7cf08a" if self.enabled else MUTED)
        if self.enabled:
            self.banner.pack_forget()
        else:
            self.banner.pack(fill="x", pady=(0, 8), before=self.body)
        if self.tracks:
            users = [f for f in getattr(self.app, "features", {}).values() if f.tracks == self.tracks and f is not self]
            on = self.enabled or any(f.enabled for f in users)
            setattr(self.app.tracker, "track_" + self.tracks, on)
            if not on:
                with self.app.tracker.lock:
                    getattr(self.app.tracker, self.tracks).clear()
        self.app.feature_state_changed(self)

    def show_tab(self, name):
        if self.tab:
            self.tabs[self.tab].pack_forget()
            self.tab_buttons[self.tab].config(fg=MUTED, bg=BG)
        self.tab = name
        self.tabs[name].pack(fill="both", expand=True)
        self.tab_buttons[name].config(fg=ACCENT, bg=BUTTON)
        settings = load_settings()
        save_settings(dict(settings, tabs=dict(settings.get("tabs", {}), **{self.title: name})))
        self.update(self.app.snapshot(), name)

    # ---- overlay (shared by every feature that has one)
    def build_overlay_tab(self, f):
        tk = self.tk
        heading(tk, f, "%s overlay" % self.title, self.overlay_text +
                " It keeps running whichever page is open. The game must be in borderless or windowed mode.")
        row = tk.Frame(f, bg=BG)
        row.pack(fill="x")
        self.show_button = button(tk, row, "Show overlay", self.toggle_overlay, width=14)
        self.show_button.pack(side="left", padx=(0, 6))
        self.lock_button = button(tk, row, "Lock", self.toggle_lock, width=10)
        self.lock_button.pack(side="left")
        self.overlay_state = tk.Label(f, text="", bg=BG, fg=MUTED, font=(FONT, 9), anchor="w", justify="left")
        self.overlay_state.pack(fill="x", pady=(8, 0))
        self.cone_var = tk.BooleanVar(value=self.overlay_cfg()["cone"])
        checkbox(tk, f, "Show view cone on this overlay (needs View cone on)", self.cone_var,
                 self.set_overlay_cone).pack(fill="x", pady=(6, 0))
        section(tk, f, "Scale")
        row = tk.Frame(f, bg=BG)
        row.pack(fill="x")
        button(tk, row, "−", lambda: self.overlay_scale(1 / 1.1), width=3).pack(side="left", padx=(0, 4))
        button(tk, row, "+", lambda: self.overlay_scale(1.1), width=3).pack(side="left", padx=(0, 8))
        self.scale_label = tk.Label(row, text="", bg=BG, fg=TEXT, font=(FONT, 10), anchor="w")
        self.scale_label.pack(side="left")
        section(tk, f, "Position")
        row = tk.Frame(f, bg=BG)
        row.pack(fill="x")
        button(tk, row, "Bring it here", self.overlay_recenter).pack(side="left")
        tk.Label(row, text="if it got lost off-screen", bg=BG, fg=MUTED, font=(FONT, 9)).pack(side="left", padx=8)
        section(tk, f, "How to line it up")
        tk.Label(f, bg=BG, fg=MUTED, font=(FONT, 9), anchor="w", justify="left", wraplength=390, text=(
            "1. Unlocked, drag the overlay so its crosshair sits on your arrow.\n"
            "2. Drag its bottom-right corner to the minimap's size.\n"
            "3. Mouse wheel (or − / + here) until its rings match the game's distances.\n"
            "4. Lock: right-click it, the Lock button, or Ctrl+Shift+O (works in game). Locked, "
            "clicks go through to the game. Ctrl+Shift+O locks / unlocks every shown overlay.")).pack(fill="x")

    def overlay_cfg(self):
        return self.overlay.cfg if self.overlay else dict(OVERLAY_DEFAULT, **load_settings().get(self.overlay_key, {}))

    def toggle_overlay(self):
        if not self.enabled and not self.overlay:
            return
        if self.overlay:
            self.overlay.close()
            self.overlay = None
            save_settings(dict(load_settings(), **{self.overlay_key: dict(self.overlay_cfg(), shown=False)}))
        else:
            self.overlay = Overlay(self)
            self.overlay.cfg["shown"] = True
            self.overlay.save()
        self.overlay_changed()

    def toggle_lock(self):
        if self.overlay:
            self.overlay.set_locked(not self.overlay.cfg["locked"])

    def set_overlay_cone(self):
        cfg = self.overlay_cfg()
        cfg["cone"] = self.cone_var.get()
        save_settings(dict(load_settings(), **{self.overlay_key: cfg}))
        if self.overlay and not cfg["cone"]:
            self.overlay.canvas.delete("cone")
        self.app.cone_serial = -1  # redraw (or drop) the cone right away

    def overlay_scale(self, factor):
        cfg = self.overlay_cfg()
        cfg["ppm"] = max(0.05, min(20.0, cfg["ppm"] * factor))
        save_settings(dict(load_settings(), **{self.overlay_key: cfg}))
        self.overlay_changed()

    def overlay_recenter(self):
        if not self.overlay:
            self.toggle_overlay()
        cfg = self.overlay.cfg
        cfg["x"] = self.root.winfo_rootx() + self.root.winfo_width() // 2 - cfg["w"] // 2
        cfg["y"] = self.root.winfo_rooty() + self.root.winfo_height() // 2 - cfg["h"] // 2
        self.overlay.place()
        if cfg["locked"]:
            self.overlay.set_locked(False)  # so it can be dragged into place
        self.overlay.save()
        self.overlay_changed()

    def overlay_changed(self):
        if not self.overlay_key or not hasattr(self, "show_button"):
            return  # no overlay, or still building
        overlay, cfg = self.overlay, self.overlay_cfg()
        self.show_button.config(text="Hide overlay" if overlay else "Show overlay",
                                state="normal" if self.enabled else "disabled")
        self.lock_button.config(state="normal" if overlay else "disabled", text="Unlock" if cfg["locked"] else "Lock")
        if overlay:
            state = "Shown · %s · %d×%d at %d, %d" % (
                "locked (clicks go to the game)" if cfg["locked"] else "unlocked (drag / resize / wheel)",
                cfg["w"], cfg["h"], cfg["x"], cfg["y"])
        else:
            state = "Hidden"
        self.overlay_state.config(text=state + ("\nHotkey: Ctrl+Shift+O locks / unlocks shown overlays." if WINDOWS else ""))
        self.scale_label.config(text="%d m across (1 px = %.2f m)" % (cfg["w"] / cfg["ppm"], 1 / cfg["ppm"]))
        self.app.set_badge(self, None if not overlay else "overlay " + ("locked" if cfg["locked"] else "unlocked"))

    def paint_overlay(self, overlay, canvas, snap):
        pass

    # ---- loop hooks
    def background(self, snap):
        if self.overlay:
            self.overlay.draw(snap)

    def update(self, snap, tab):
        if tab == "Overlay":
            self.overlay_changed()  # it can be dragged or scaled from the overlay itself

    def status(self, snap):
        return None

    def close(self):
        pass
