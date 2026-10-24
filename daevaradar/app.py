"""The app: the tray icon, the Settings window (webui.py + settings_window.py), the overlays and info panels, and the
loop that drives everything. Tk runs the windows that sit over the game (overlays, cards, pop-ups, the World map,
the minimap locator); its root window is never shown."""
import os
import queue
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

from . import update
from .common import FROZEN, NO_WINDOW, WINDOWS, focus_requested, launch_command, load_settings, save_settings
from .characters import Characters
from .worldmap import WorldMap
from .vision import ViewCone
from .debug import PositionDebug
from .game import BOSS_MAP_KEYS, MapData, map_name
from .ui import OVERLAY_CONE, Toast, draw_cone, foreground_exe, hotkey_thread, set_icon
from .features.cubes import CubesFeature
from .features.feathers import FeathersFeature
from .features.monsters import MonstersFeature
from .features.gathering import GatherFeature
from .features.alerts import AlertsFeature
from .features.viewcone import ViewFeature
from .features.wardrobe import WardrobeFeature
from .features.bosses import BossesFeature
from .features.shop import ShopFeature
from .panels import PANELS
from .tray import start_tray
from .webui import ACTIONS, WebUI, overlays


TERMS_VERSION = 1  # the first-run notice (assets/web/terms.js): raise it when its meaning changes, to ask again


class App:
    """Runs from the tray: Settings is a web page in its own window (a page per feature); each feature has its
    minimap overlay and info card over the game. Every feature's background() runs each tick."""
    FEATURES = (CubesFeature, FeathersFeature, MonstersFeature, GatherFeature, WardrobeFeature, BossesFeature, ShopFeature, AlertsFeature, ViewFeature)

    def __init__(self, tracker, engine, args):
        import tkinter as tk
        self.tk = tk
        self.tracker, self.engine, self.args = tracker, engine, args
        self.view = ViewCone()
        self.mapdata = MapData()  # shared by the features that use the map data
        self.detected, self.detected_zone, self.boss_seen = None, None, {}  # the map the boss list was read on, and when
        self.map_proc = None  # the World map's window (its own process, like Settings)
        self.debug = PositionDebug(self)  # the Position debug page (Settings)
        self.calls = queue.Queue()  # work handed to the Tk thread (the web server's and the tray's threads)
        self.ticks = 0
        focus_requested()  # an old request isn't for this copy
        self.focus_seq = self.map_focus = 0  # bumped to ask the open Settings / map window to come to the front
        self.settings_proc, self.settings_started, self.tray = None, 0, None
        self.panels = {}
        self.layout, self.layout_seen = None, None  # overlay layout mode (layout.py) while it's on
        self.web_view = None  # (feature, tab, when) last asked for by the Settings page
        self.overlays_visible = True  # overlays and cards show only while the game has the focus
        self.closing = False
        settings = load_settings()
        agreed = settings.get("terms") or {}
        # the demo reads nothing real, so it needs no notice; otherwise nothing is read until it's agreed to
        self.agreed = bool(args.demo) or agreed.get("version") == TERMS_VERSION
        self.agreed_at = agreed.get("when")
        tracker.paused = not self.agreed
        self.root = root = tk.Tk()
        root.withdraw()  # only the windows over the game show; Settings is the web page
        root.title("Daeva Radar")
        # errors in window callbacks go to the log (the app runs without a console: they'd be lost otherwise)
        root.report_callback_exception = lambda kind, error, tb: engine.say(
            "ERROR: " + "".join(__import__("traceback").format_exception(kind, error, tb)))
        self.icon = set_icon(root)
        settings = load_settings()
        self.topmost = tk.BooleanVar(value=settings.get("topmost", True) and not args.no_topmost)
        self.hotkey = threading.Event()
        if WINDOWS:
            threading.Thread(target=hotkey_thread, args=(self.hotkey,), daemon=True).start()
        self.map_choices = [("auto", "Auto")] + [(k, map_name(k)) for k in self.mapdata.keys()]
        self.map_cfg = settings.get("map", settings.get("gathering", {}).get("map", "auto"))  # was Gathering's
        self.version = update.current_version()
        self.release, self.update_state, self.update_shown, self.toasted = None, "idle", None, set()
        self.checked_at = self.update_error = None  # "HH:MM" of the last finished check; why an update failed

        self.characters = Characters(self)
        self.features = {}
        for cls in self.FEATURES:
            self.features[cls.title] = cls(self)
        self.panels = {cls.key: cls(self) for cls in PANELS}
        self.world_map = WorldMap(self)  # the World map page's data; the page shows in its own window
        self.web = WebUI(self)
        self.engine.say("Settings page on " + self.web.url.split("?")[0])
        self.tray = start_tray(self)
        if (self.tray is None or settings.get("settings_at_start", True) or getattr(args, "open_settings", False)
                or not self.agreed):  # the notice is in Settings
            root.after(400, self.open_settings)  # without a tray, Settings is the only way in (and out: Quit)
        self.cone_serial = -1
        if args.debug:  # Settings, at Position debug
            self.web_goto = "Debug"
            root.after(400, self.open_settings)
        if settings.get("world_map", settings.get("pet_map", {})).get("open"):  # it was open when the app closed
            root.after(300, self.open_map)
        self.tick()
        self.cone_tick()
        if self.agreed:
            self.start_update_checks()

    def start_update_checks(self):
        """At start and every full hour (not before the notice is agreed to: it's the only thing that goes online)."""
        if load_settings().get("check_updates", True) and not getattr(self.args, "no_update_check", False):
            threading.Thread(target=update.cleanup, daemon=True).start()
            self.check_updates()
            self.schedule_update_check()

    def accept_terms(self):
        """The first-run notice was agreed to: remember it (on this PC only) and start reading."""
        if self.agreed:
            return
        self.agreed, self.agreed_at = True, time.strftime("%Y-%m-%d %H:%M")
        save_settings(dict(load_settings(), terms=dict(version=TERMS_VERSION, when=self.agreed_at)))
        self.tracker.paused = False
        self.engine.say("Notice agreed: reading the game now")
        self.start_update_checks()

    # ---- map: picked in Settings, else the map whose boss list you opened since you last teleported
    def picked(self):
        """The map picked by hand, or None for Auto (also when its data is gone)."""
        return self.map_cfg if self.map_cfg in self.mapdata.keys() else None

    def map_key(self):
        return self.picked() or self.detected

    def set_map(self, key):
        self.map_cfg = key if key in dict(self.map_choices) else "auto"
        save_settings(dict(load_settings(), map=self.map_cfg))

    def detect_map(self, snap):
        """The boss list (map menu) comes only for the map you're in, so reading one says where you are. It holds until
        you teleport or zone elsewhere (then the map is unknown until the next list arrives, a few seconds later)."""
        zone = snap.get("zone_count")
        if self.detected and zone != self.detected_zone:
            self.detected = None
            self.engine.say("Zone changed: map unknown until the boss list arrives")
        for name, d in snap.get("bosses", {}).items():
            if d["time"] > self.boss_seen.get(name, 0):
                self.boss_seen[name] = d["time"]
                key = BOSS_MAP_KEYS.get(name)
                if key and key in self.mapdata.keys() and (key != self.detected or zone != self.detected_zone):
                    self.detected, self.detected_zone = key, zone
                    self.engine.say("Map is %s (from the boss list)" % key)

    # ---- updates: checked at launch and every full hour; installed from Settings
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

    def install_update(self):
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

    def show_update(self):
        """Pop-ups for the update state (the worker threads only set update_state; this runs on Tk)."""
        state = self.update_state
        if state == self.update_shown:
            return
        self.update_shown = state
        new = self.release["version"] if self.release else ""
        if state == "available" and new not in self.toasted:
            self.toasted.add(new)
            Toast(self.root, "Daeva Radar %s is out\nClick to open Settings and update." % new, color="#7cf08a",
                  label="Update", seconds=12, on_click=self.open_settings_general)
        if state == "install_failed":
            Toast(self.root, "The update failed\n%s" % self.update_error, color="#ffb347", seconds=12, label="Update")
        if state == "installed":
            self.root.after(800, self.restart)

    def restart_now(self):
        """Settings → General → Restart: a fresh copy (with Settings open again), then this one closes."""
        self.engine.say("Restarting")
        try:
            update.restart(extra=("--open-settings",))
        except Exception as e:
            Toast(self.root, "Couldn't restart\n%s" % e, color="#ffb347", label="Daeva Radar")
            return
        self.root.after(300, self.close)

    def restart(self):
        try:
            update.restart()
        except Exception as e:
            Toast(self.root, "Updated, but couldn't restart\n%s. Start it again by hand." % e, color="#ffb347",
                  label="Update")
        self.close()

    # ---- windows
    def open_map(self, mode=None):
        """The World map in its own window (assets/web/map.js): mode "Pets" or "Resources" for its side panel."""
        if mode:
            self.world_map.set_cfg(dict(mode=mode))
        if self.map_proc and self.map_proc.poll() is None:
            self.map_focus += 1  # the page sees it, switches to the mode and comes to the front
            return
        self.world_map.cfg["open"] = True
        self.world_map.save()
        url = self.web.url + "&solo=map" + ("&ontop=1" if self.topmost.get() else "")
        self.map_proc = self.launch_window(url)
        self.engine.say("World map opened")

    def launch_window(self, url):
        """A web window (settings_window.py) for url, as its own process; the browser if that can't start."""
        exe = Path(sys.executable)
        if not FROZEN and exe.with_name("pythonw.exe").exists():
            exe = exe.with_name("pythonw.exe")
        try:
            proc = subprocess.Popen([str(exe)] + launch_command()[1:] + ["--settings-window", url], creationflags=NO_WINDOW)
            proc.started = time.time()
            return proc
        except OSError as e:
            self.engine.say("Window failed (%s): opening it in the browser" % e)
            webbrowser.open(url)
            return None
    def set_topmost(self):
        """Keep the World map above the game."""
        save_settings(dict(load_settings(), topmost=self.topmost.get()))

    def snapshot(self):
        return self.tracker.snapshot()

    # ---- the Tk thread: other threads hand their work here
    def call(self, fn, timeout=5):
        """Run fn on the Tk thread and return its result (from the web server's threads)."""
        if threading.current_thread() is threading.main_thread():
            return fn()
        box, done = {}, threading.Event()
        self.calls.put((fn, box, done))
        if not done.wait(timeout):
            raise TimeoutError("the app is busy")
        if "error" in box:
            raise box["error"]
        return box.get("result")

    def post(self, fn):
        """Run fn on the Tk thread soon (from the tray's thread); no result."""
        self.calls.put((fn, None, None))

    def run_calls(self):
        while True:
            try:
                fn, box, done = self.calls.get_nowait()
            except queue.Empty:
                return
            try:
                result = fn()
                if box is not None:
                    box["result"] = result
            except Exception as e:
                if box is not None:
                    box["error"] = e
                else:
                    self.engine.say("ERROR: %r" % e)
            if done:
                done.set()

    def web_action(self, name, **args):
        return ACTIONS[name](self, **args)

    # ---- the Settings window (its own process)
    def open_settings(self):
        if self.settings_proc and self.settings_proc.poll() is None:
            self.focus_seq += 1  # the page sees it and brings its window forward
            return
        self.settings_proc = self.launch_window(self.web.url)
        self.settings_started = time.time()

    def open_settings_general(self):
        """Settings at General (the update notice's click): the page goes there on the next focus request."""
        self.web_goto = "General"
        self.open_settings()
        self.focus_seq += 1

    def explain_browser(self):
        """A pop-up: why Settings opened in the browser, and (WebView2 missing) a click to Microsoft's download."""
        from .settings_window import DOTNET_DOWNLOAD, NO_RUNTIME, OLD_DOTNET, WEBVIEW2_DOWNLOAD
        reason = self.window_error()
        if reason in (NO_RUNTIME, OLD_DOTNET):
            Toast(self.root, "Settings opened in your browser\n%s.\nClick to get it from Microsoft (free), then restart "
                  "Daeva Radar." % reason, color="#ffb347", seconds=None, label="Settings",
                  on_click=lambda: webbrowser.open(WEBVIEW2_DOWNLOAD if reason == NO_RUNTIME else DOTNET_DOWNLOAD))
        else:
            Toast(self.root, "Settings opened in your browser\nIts own window couldn't start: %s" % reason,
                  color="#ffb347", seconds=20, label="Settings")

    @staticmethod
    def window_error():
        """The reason the last web window gave for not opening (settings_window.ERROR_LOG)."""
        from .settings_window import ERROR_LOG
        try:
            return ERROR_LOG.read_text(encoding="utf-8").splitlines()[0]
        except (OSError, IndexError):
            return "unknown"

    def check_settings_window(self):
        """No pywebview / WebView2: the Settings window quits at once, so open the page in the browser instead.
        Without a tray icon, closing Settings quits the app (there'd be no way back in)."""
        from .settings_window import NO_WEBVIEW
        proc = self.settings_proc
        if proc and proc.poll() is not None:
            self.settings_proc = None
            if proc.returncode == NO_WEBVIEW and time.time() - self.settings_started < 30:
                self.engine.say("Settings window unavailable: %s. Opening it in the browser" % self.window_error())
                webbrowser.open(self.web.url)
                self.explain_browser()
            elif not self.tray:
                self.close()
        proc = self.map_proc
        if proc and proc.poll() is not None:  # the World map window was closed
            self.map_proc = None
            if proc.returncode == NO_WEBVIEW and time.time() - proc.started < 30:
                webbrowser.open(self.web.url + "&solo=map")
            else:
                self.world_map.cfg["open"] = False
                self.world_map.save()

    # ---- every overlay: the features' minimap overlays and the info panels
    def overlay_list(self):
        return overlays(self)

    def overlay_shown(self, oid):
        kind, _, name = oid.partition(":")
        if kind == "radar":
            return bool(self.features[name].overlay)
        return bool(self.panels[name].win)

    def shown_overlays(self):
        """-> [(cfg, set_locked)] of every overlay and panel on screen."""
        out = [(f.overlay.cfg, f.overlay.set_locked) for f in self.features.values() if f.overlay and f.enabled]
        return out + [(p.cfg, p.set_locked) for p in self.panels.values() if p.win]

    def overlays_locked(self):
        shown = self.shown_overlays()
        return bool(shown) and all(cfg["locked"] for cfg, _ in shown)

    def edit_layout(self):
        """Overlay layout mode: a grid over the game window to place every overlay on the real screen."""
        if self.layout:
            return self.layout.close()
        from .layout import LayoutMode
        self.layout = LayoutMode(self)

    def lock_overlays(self, lock):
        for cfg, set_locked in self.shown_overlays():
            set_locked(lock)

    # ---- the loop
    def cone_tick(self):
        """Every 50 ms: if the cone reading changed, redraw only the overlays' cone, so it follows the camera
        without waiting for the full 100 ms refresh; and run the other threads' work."""
        if self.closing:
            return
        if self.view.serial != self.cone_serial:
            self.cone_serial = self.view.serial
            cone = self.view.cone if self.view.ready() else None
            for feature in self.features.values():
                if feature.enabled and feature.overlay:
                    o = feature.overlay
                    o.canvas.delete("cone")
                    if self.tracker.position is not None and o.cfg["cone"]:
                        draw_cone(o.canvas, o.cfg["w"] / 2, o.cfg["h"] / 2, min(o.cfg["w"], o.cfg["h"]) / 2 - 6, cone,
                                  outline=OVERLAY_CONE, below=True)
        self.run_calls()
        self.root.after(50, self.cone_tick)

    def cone_needed(self):
        """Is anything showing the vision cone right now? Only then is the screen read for it."""
        if not self.agreed:
            return False
        if any(f.overlay and f.overlay.cfg["cone"] for f in self.features.values() if f.enabled):
            return True
        if self.web_view and time.time() - self.web_view[2] < 3:  # a radar or View cone open in Settings
            title, tab, _ = self.web_view
            return title == "View cone" or tab == "Radar"
        return False

    def game_focused(self):
        """Show the overlays and cards? Only while AION 2 (or one of this app's own windows, e.g. while placing
        them in Overlay layout) has the focus. Always in the demo and off Windows (no focus check there)."""
        if not WINDOWS or self.args.demo or self.layout:
            return True
        pid, exe = foreground_exe()
        return pid is None or exe == "aion2.exe" or pid == os.getpid()

    def on_hotkey(self):
        """Ctrl+Shift+O: lock every shown overlay and card if any is unlocked, else unlock them all."""
        self.lock_overlays(not self.overlays_locked())

    def tick(self):
        if self.closing:
            return
        self.ticks += 1
        if self.ticks % 5 == 0 and focus_requested():  # a second copy was started: show the Settings window
            self.open_settings()
        snap = self.snapshot()
        self.view.update(self.cone_needed())
        snap["cone"] = self.view.cone if self.view.ready() else None
        self.last_snap = snap
        self.detect_map(snap)
        self.characters.note(snap)
        self.show_update()
        if self.hotkey.is_set():
            self.hotkey.clear()
            self.on_hotkey()
        self.overlays_visible = self.game_focused()
        for feature in self.features.values():
            if feature.enabled:
                feature.background(snap)
            if feature.overlay:
                feature.overlay.set_visible(self.overlays_visible)
        for panel in self.panels.values():
            panel.update(snap)
        if self.layout:  # an overlay shown or hidden meanwhile: redraw the placeholders; keep them above the grid
            shown = tuple(o["shown"] for o in self.overlay_list())
            if shown != self.layout_seen:
                self.layout_seen = shown
                self.layout.draw()
        self.check_settings_window()
        self.debug.update(snap)
        self.root.after(100, self.tick)

    def status_text(self):
        """The Settings page's footer: capture state and what each feature sees."""
        snap = getattr(self, "last_snap", None) or self.snapshot()
        parts = [self.engine.status] + [s for s in (f.status(snap) for f in self.features.values() if f.enabled) if s]
        return " · ".join(parts)

    def close(self):
        if self.closing:
            return
        self.closing = True
        self.view.running = False
        if self.layout:
            self.layout.close()
        for feature in self.features.values():
            feature.close()
        if self.map_proc and self.map_proc.poll() is None:  # open at the next start, as now
            self.map_proc.terminate()
        if self.tray:
            self.tray.stop()
        if self.settings_proc and self.settings_proc.poll() is None:
            self.settings_proc.terminate()
        self.web.close()
        self.engine.stop = True
        if self.engine.capture:
            self.engine.capture.shutdown()
        self.root.destroy()
Radar = App  # older name
