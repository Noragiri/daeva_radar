"""The main window: a sidebar of features, the status bar, and the loop that drives everything."""
import threading

from .common import ASSETS, WINDOWS, load_settings, save_settings
from .vision import ViewCone
from .debug import PositionWindow
from .follow import PositionFusion
from .ui import ACCENT, BG, BUTTON, FONT, MUTED, OVERLAY_CONE, SIDEBAR, TEXT, draw_cone, hotkey_thread, set_icon
from .features.cubes import CubesFeature
from .features.monsters import MonstersFeature
from .features.gathering import GatherFeature
from .features.alerts import AlertsFeature
from .features.viewcone import ViewFeature


class App:
    """Main window: a sidebar of features. Every feature's background() runs each tick whatever is shown;
    only the shown feature/tab is redrawn."""
    FEATURES = (CubesFeature, MonstersFeature, GatherFeature, AlertsFeature, ViewFeature)

    def __init__(self, tracker, engine, args):
        import tkinter as tk
        self.tk = tk
        self.tracker, self.engine, self.args = tracker, engine, args
        self.view = ViewCone()
        self.fusion = PositionFusion()
        self.root = root = tk.Tk()
        root.title("Daeva Radar")
        self.icon = set_icon(root)
        root.configure(bg=BG)
        settings = load_settings()
        self.topmost = tk.BooleanVar(value=settings.get("topmost", True) and not args.no_topmost)
        root.attributes("-topmost", self.topmost.get())
        self.hotkey = threading.Event()
        if WINDOWS:
            threading.Thread(target=hotkey_thread, args=(self.hotkey,), daemon=True).start()

        side = tk.Frame(root, bg=SIDEBAR, width=150)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        brand = tk.Frame(side, bg=SIDEBAR)
        brand.pack(fill="x", padx=12, pady=(14, 18))
        try:
            self.logo = tk.PhotoImage(file=str(ASSETS / "icon-32.png"))
            tk.Label(brand, image=self.logo, bg=SIDEBAR).pack(side="left")
        except tk.TclError:
            pass
        tk.Label(brand, text="Cube\nFinder", bg=SIDEBAR, fg=TEXT, font=(FONT, 11, "bold"), justify="left").pack(
            side="left", padx=8)
        tk.Checkbutton(side, text="Keep on top", variable=self.topmost, command=self.set_topmost, bg=SIDEBAR,
                       fg=MUTED, selectcolor=BUTTON, activebackground=SIDEBAR, activeforeground=TEXT,
                       font=(FONT, 9), anchor="w", padx=10).pack(side="bottom", fill="x", pady=(0, 10))
        self.debug = None
        self.debug_link = tk.Label(side, text="⌖ Position debug", bg=SIDEBAR, fg=MUTED, font=(FONT, 9), anchor="w",
                                   padx=14, cursor="hand2")
        self.debug_link.pack(side="bottom", fill="x", pady=(0, 4))
        self.debug_link.bind("<Button-1>", lambda e: self.toggle_debug())
        self.badges = tk.Label(side, text="", bg=SIDEBAR, fg=ACCENT, font=(FONT, 9), anchor="w", padx=14,
                               justify="left")
        self.badges.pack(side="bottom", fill="x", pady=(0, 6))
        self.badge_text = {}
        main = tk.Frame(root, bg=BG)
        main.pack(side="left", fill="both", expand=True)
        content = tk.Frame(main, bg=BG)
        content.pack(fill="both", expand=True, padx=14, pady=(12, 6))
        self.status = tk.Label(main, text="", bg="#0f1115", fg=MUTED, font=(FONT, 9), anchor="w", padx=10, pady=4)
        self.status.pack(fill="x", side="bottom")

        self.features, self.nav, self.current = {}, {}, None
        for cls in self.FEATURES:
            item = tk.Label(side, text="", bg=SIDEBAR, fg=MUTED, anchor="w", font=(FONT, 11), padx=10, pady=8,
                            cursor="hand2")
            item.pack(fill="x")
            item.bind("<Button-1>", lambda e, t=cls.title: self.show(t))
            item.bind("<Enter>", lambda e, w=item: w.config(bg=BUTTON) if w is not self.nav.get(self.current) else None)
            item.bind("<Leave>", lambda e, w=item: w.config(bg=SIDEBAR) if w is not self.nav.get(self.current) else None)
            self.nav[cls.title] = item
            self.features[cls.title] = cls(self, content)
            self.feature_state_changed(self.features[cls.title])
        # one fixed size for every feature and tab (the largest), so switching doesn't resize the window
        root.update_idletasks()
        banner = max(f.banner.winfo_reqheight() + 8 for f in self.features.values())  # shown while a feature is off
        sizes = [(frame.winfo_reqwidth(), frame.winfo_reqheight() + 45 + banner)  # + tab bar + off banner
                 for feature in self.features.values() for frame in feature.tabs.values()]
        content.config(width=max(w for w, h in sizes), height=max(h for w, h in sizes))
        content.pack_propagate(False)
        saved = settings.get("page")
        self.show(saved if saved in self.features else self.FEATURES[0].title)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.cone_serial = -1
        if args.debug or settings.get("debug_shown"):
            self.toggle_debug()
        self.tick()
        self.cone_tick()

    def show(self, title):
        if self.current:
            self.features[self.current].frame.pack_forget()
            self.nav[self.current].config(bg=SIDEBAR)
        self.current = title
        feature = self.features[title]
        feature.frame.pack(fill="both", expand=True)
        self.nav[title].config(bg=BUTTON)
        for f in self.features.values():
            self.feature_state_changed(f)
        save_settings(dict(load_settings(), page=title))
        feature.update(self.snapshot(), feature.tab)

    def snapshot(self):
        """The tracker's snapshot with your position followed between packets (by your pet)."""
        snap = self.fusion.apply(self.tracker.snapshot())
        self.tracker.estimate = snap["position"]
        return snap

    def feature_state_changed(self, feature):
        """Sidebar entry: '(off)' and dimmed when the feature is switched off."""
        item = getattr(self, "nav", {}).get(feature.title)
        if item is not None:
            item.config(text="  %s   %s%s" % (feature.icon, feature.title, "" if feature.enabled else "  · off"),
                        fg=(TEXT if feature.title == self.current else MUTED) if feature.enabled else "#5a606a")

    def set_badge(self, feature, text):
        """Short always-visible state in the sidebar (e.g. a feature's overlay is on)."""
        if text:
            self.badge_text[feature.title] = "%s %s:\n    %s" % (feature.icon, feature.title, text)
        else:
            self.badge_text.pop(feature.title, None)
        if hasattr(self, "badges"):
            self.badges.config(text="\n".join(self.badge_text.values()))

    def toggle_debug(self):
        """Show or hide the position debug window (remembered for the next start)."""
        if self.debug:
            self.debug.close()
            self.debug = None
        else:
            self.debug = PositionWindow(self)
            self.debug.update(self.snapshot())
        self.debug_link.config(fg=ACCENT if self.debug else MUTED)
        save_settings(dict(load_settings(), debug_shown=bool(self.debug)))

    def set_topmost(self):
        self.root.attributes("-topmost", self.topmost.get())
        save_settings(dict(load_settings(), topmost=self.topmost.get()))

    def cone_tick(self):
        """Every 50 ms: if the cone reading changed, redraw only the cone (radar tab and overlays), so it
        follows the camera without waiting for the full 250 ms refresh."""
        if self.view.serial != self.cone_serial:
            self.cone_serial = self.view.serial
            cone = self.view.cone if self.view.ready() else None
            current = self.features[self.current]
            for feature in self.features.values():
                if not feature.enabled:
                    continue
                if feature is current and feature.tab == "Radar" and hasattr(feature, "canvas"):
                    c, mid = feature.canvas, feature.size / 2
                    c.delete("cone")
                    draw_cone(c, mid, mid, mid - 18, cone, fill="#2b3346", below=True)
                if feature.overlay:
                    o = feature.overlay
                    o.canvas.delete("cone")
                    if self.tracker.position is not None and o.cfg["cone"]:
                        draw_cone(o.canvas, o.cfg["w"] / 2, o.cfg["h"] / 2, min(o.cfg["w"], o.cfg["h"]) / 2 - 6, cone,
                                  outline=OVERLAY_CONE, below=True)
        self.root.after(50, self.cone_tick)

    def cone_needed(self):
        """Is anything showing the vision cone right now? Only then is the screen read for it."""
        if any(f.overlay and f.overlay.cfg["cone"] for f in self.features.values() if f.enabled):
            return True  # overlays show it whatever the main window does
        if self.root.state() == "iconic":
            return False  # minimised: nothing in the window is visible
        current = self.features[self.current]
        return current.title == "View cone" or (current.tab == "Radar" and current.enabled)

    def on_hotkey(self):
        """Ctrl+Shift+O: lock every shown overlay if any is unlocked, else unlock them all; with none shown,
        show the current feature's overlay."""
        shown = [f for f in self.features.values() if f.overlay and f.enabled]
        if not shown:
            current = self.features[self.current]
            if current.overlay_key and current.enabled:
                current.toggle_overlay()
            return
        lock = any(not f.overlay.cfg["locked"] for f in shown)
        for f in shown:
            f.overlay.set_locked(lock)

    def tick(self):
        snap = self.snapshot()
        self.view.update(self.cone_needed())
        snap["cone"] = self.view.cone if self.view.ready() else None
        if self.hotkey.is_set():
            self.hotkey.clear()
            self.on_hotkey()
        for feature in self.features.values():
            if feature.enabled:
                feature.background(snap)
        current = self.features[self.current]
        current.update(snap, current.tab)
        if self.debug:
            self.debug.update(snap)
        if snap["own_name"]:
            who = "playing as %s" % snap["own_name"]
        else:
            who = ("character #%d" % snap["own"]) if snap["own"] else "character not identified yet: relog or change zone"
        parts = [self.engine.status, who] + [s for s in (f.status(snap) for f in self.features.values() if f.enabled) if s]
        self.status.config(text=" · ".join(parts + ["%d msgs" % snap["messages"], "%d pkts" % self.engine.packets]))
        self.root.after(250, self.tick)

    def close(self):
        self.view.running = False
        for feature in self.features.values():
            feature.close()
        if self.debug:
            self.debug.close()
        self.engine.stop = True
        if self.engine.capture:
            self.engine.capture.shutdown()
        self.root.destroy()
Radar = App  # older name
