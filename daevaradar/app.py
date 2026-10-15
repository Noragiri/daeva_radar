"""The main window: a sidebar of features, the status bar, and the loop that drives everything."""
import threading
import time

from . import update
from .common import ASSETS, WINDOWS, load_settings, save_settings
from .vision import ViewCone
from .debug import PositionWindow
from .follow import PositionFusion
from .game import MapData, map_name
from .ui import ACCENT, BG, BUTTON, BUTTON_HOVER, FONT, MUTED, OVERLAY_CONE, PANEL, SIDEBAR, TEXT, Toast, button, draw_cone, hotkey_thread, set_icon
from .features.cubes import CubesFeature
from .features.monsters import MonstersFeature
from .features.gathering import GatherFeature
from .features.alerts import AlertsFeature
from .features.viewcone import ViewFeature


class App:
    """Main window: a sidebar of features and the map picker. Every feature's background() runs each tick
    whatever is shown; only the shown feature/tab is redrawn."""
    FEATURES = (CubesFeature, MonstersFeature, GatherFeature, AlertsFeature, ViewFeature)

    def __init__(self, tracker, engine, args):
        import tkinter as tk
        self.tk = tk
        self.tracker, self.engine, self.args = tracker, engine, args
        self.view = ViewCone()
        self.fusion = PositionFusion()
        self.mapdata = MapData()  # shared by the features that use the map data
        self.detected, self.map_checked = None, 0  # the map the live nodes / cubes line up with
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
        self.check_button = button(tk, side, "Check for updates", self.check_updates)
        self.check_button.config(font=(FONT, 9), pady=2, fg=MUTED)
        self.check_button.pack(side="bottom", fill="x", padx=14, pady=(0, 8))
        self.update_link = tk.Label(side, text="", bg=SIDEBAR, fg=MUTED, font=(FONT, 9), anchor="w", justify="left",
                                    padx=14, cursor="hand2", wraplength=136)
        self.update_link.pack(side="bottom", fill="x", pady=(0, 4))
        self.update_link.bind("<Button-1>", lambda e: self.on_update_click())
        self.version = update.current_version()
        self.release, self.update_state, self.update_shown, self.toasted = None, "idle", None, set()
        self.checked_at = None  # "HH:MM" of the last finished check
        self.badges = tk.Label(side, text="", bg=SIDEBAR, fg=ACCENT, font=(FONT, 9), anchor="w", padx=14,
                               justify="left")
        self.badges.pack(side="bottom", fill="x", pady=(0, 6))
        self.badge_text = {}
        self.build_map_picker(side, settings)
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
        self.show_update()
        if settings.get("check_updates", True) and not getattr(args, "no_update_check", False):
            threading.Thread(target=update.cleanup, daemon=True).start()
            self.check_updates()
            self.schedule_update_check()

    # ---- map: picked by hand in the sidebar, else detected from live gathering nodes or cubes
    def build_map_picker(self, side, settings):
        tk = self.tk
        box = tk.Frame(side, bg=SIDEBAR)
        box.pack(side="bottom", fill="x", padx=10, pady=(0, 10))
        tk.Label(box, text="Map", bg=SIDEBAR, fg=MUTED, font=(FONT, 9), anchor="w").pack(fill="x")
        self.map_choices = [("auto", "Auto")] + [(k, map_name(k)) for k in self.mapdata.keys()]
        self.map_cfg = settings.get("map", settings.get("gathering", {}).get("map", "auto"))  # was Gathering's
        self.map_var = tk.StringVar(value=dict(self.map_choices).get(self.map_cfg, "Auto"))
        menu = tk.OptionMenu(box, self.map_var, *[label for _, label in self.map_choices], command=self.set_map)
        menu.config(bg=BUTTON, fg=TEXT, activebackground=BUTTON_HOVER, activeforeground=TEXT, relief="flat", bd=0,
                    highlightthickness=0, font=(FONT, 9), anchor="w", cursor="hand2")
        menu["menu"].config(bg=PANEL, fg=TEXT, activebackground=BUTTON_HOVER, activeforeground=TEXT, font=(FONT, 9))
        menu.pack(fill="x")
        self.map_label = tk.Label(box, text="", bg=SIDEBAR, fg=MUTED, font=(FONT, 8), anchor="w", justify="left",
                                  wraplength=130)
        self.map_label.pack(fill="x")
        self.show_map()

    def picked(self):
        """The map picked by hand, or None for Auto (also when its data is gone)."""
        return self.map_cfg if self.map_cfg in self.mapdata.keys() else None

    def map_key(self):
        return self.picked() or self.detected

    def set_map(self, label):
        self.map_cfg = next(key for key, text in self.map_choices if text == label)
        save_settings(dict(load_settings(), map=self.map_cfg))
        self.show_map()

    def detect_map(self, snap):
        """Gathering nodes decide when there are any; cubes only when there aren't."""
        if not snap["position"] or time.time() - self.map_checked < 2:
            return
        self.map_checked = time.time()
        nodes, cubes = [n["xyz"] for n in snap["nodes"]], [c["xyz"] for c in snap["cubes"]]
        key = (nodes and self.mapdata.which_map(nodes)) or (cubes and self.mapdata.cube_map(cubes)) or None
        if key and key != self.detected:
            self.detected = key
            self.engine.say("Map is %s" % key)
            self.show_map()

    def show_map(self):
        text = ("detected: %s" % map_name(self.detected)) if self.detected else "not detected yet"
        differs = self.picked() and self.detected and self.detected != self.picked()
        self.map_label.config(text=text + (" (differs!)" if differs else ""), fg="#ffb347" if differs else MUTED)

    # ---- updates: checked at launch and every full hour; installed on a click
    def schedule_update_check(self):
        """Next check at the next x:00 (plus a few seconds), then every hour."""
        wait = 3600 - time.time() % 3600 + 5
        self.root.after(int(wait * 1000), lambda: (self.check_updates(), self.schedule_update_check()))

    def check_updates(self):
        if self.update_state in ("checking", "installing"):
            return
        self.update_state = "checking"

        def work():
            try:
                self.release = update.latest()
                self.update_state = "available" if update.is_newer(self.release, self.version) else "current"
            except Exception as e:
                self.engine.say("Update check failed: %s" % e)
                self.update_state = "failed"
            self.checked_at = time.strftime("%H:%M")
        threading.Thread(target=work, daemon=True).start()

    def show_update(self):
        """Sidebar link for the update state (the worker threads only set update_state; this runs on Tk)."""
        state = self.update_state
        if state == self.update_shown:
            return
        self.update_shown = state
        mine = "v" + self.version if self.version else ("git copy" if update.is_git() else "version unknown")
        new = self.release["version"] if self.release else ""
        text, color = {
            "idle": (mine, MUTED),
            "checking": ("Checking for updates…", MUTED),
            "current": ("✓ Up to date (%s)\n%s" % (self.checked_at, mine), MUTED),
            "failed": ("⚠ Update check failed (click to retry)", MUTED),
            "available": (("↑ %s is out (git pull)" if update.is_git() else "↑ Update to %s") % new, "#7cf08a"),
            "installing": ("Updating to %s…" % new, ACCENT),
            "install_failed": ("⚠ Update failed (click to retry)", "#ffb347"),
            "installed": ("✓ Updated, restarting…", "#7cf08a"),
        }[state]
        self.update_link.config(text=text, fg=color, font=(FONT, 9, "bold" if state == "available" else "normal"))
        busy = state in ("checking", "installing", "installed")
        self.check_button.config(state="disabled" if busy else "normal", text="Checking…" if state == "checking"
                                 else "Check for updates")
        if state == "available" and new not in self.toasted:
            self.toasted.add(new)
            Toast(self.root, "Daeva Radar %s is out.\nClick ↑ Update in the sidebar." % new, color="#7cf08a")
        if state == "install_failed":
            from tkinter import messagebox
            messagebox.showerror("Daeva Radar", "Update failed: %s" % self.update_error, parent=self.root)
        if state == "installed":
            self.root.after(800, self.restart)

    def on_update_click(self):
        from tkinter import messagebox
        if self.update_state in ("checking", "installing", "installed"):
            return
        if self.update_state not in ("available", "install_failed"):
            return self.check_updates()
        if update.is_git():
            messagebox.showinfo("Daeva Radar", "This copy is a git checkout. Update it with git pull.", parent=self.root)
            return
        if not messagebox.askyesno("Daeva Radar", "Update to %s now?\n\nThe app restarts when it's done. Your settings "
                                   "are kept." % self.release["version"], parent=self.root):
            return
        self.update_state = "installing"

        def work():
            try:
                n = update.install(self.release)
                self.engine.say("Updated to %s (%d files)" % (self.release["version"], n))
                self.update_state = "installed"
            except Exception as e:
                self.engine.say("Update failed: %s" % e)
                self.update_error = str(e)
                self.update_state = "install_failed"
        threading.Thread(target=work, daemon=True).start()

    def restart(self):
        try:
            update.restart()
        except Exception as e:
            from tkinter import messagebox
            messagebox.showerror("Daeva Radar", "Updated, but couldn't restart (%s). Start it again by hand." % e,
                                 parent=self.root)
        self.close()

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
        self.detect_map(snap)
        self.show_update()
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
