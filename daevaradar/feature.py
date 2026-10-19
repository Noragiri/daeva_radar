"""The Feature base: one Settings page with its tabs (page.py blocks), On/Off switch, minimap overlay and settings."""
from .common import load_settings, save_settings
from .ui import ACCENT, OVERLAY_DEFAULT, Overlay


class Feature:
    """A self-contained feature: its Settings page, overlay and background work.
    To add one: subclass, set title/icon/TABS, implement page_<tab>(snap) -> [blocks] for each tab (page.py) and
    on_<id>(value) for the page's controls, list the class in App.FEATURES. For a minimap overlay: set overlay_key
    and implement paint_overlay().
      background(snap): every tick (alerts, the overlay)
      page(tab, snap): when the Settings page asks (about twice a second while the tab is open)"""
    title, icon, TABS = "", "", ()
    overlay_key, overlay_color, overlay_text = None, ACCENT, ""
    tracks = None  # what the tracker parses for this feature ("cubes", "monsters"); skipped while every user is off
    off_text = "Nothing is tracked, alerted or drawn while it's off."

    def __init__(self, app):
        self.app, self.tk, self.root = app, app.tk, app.root
        self.overlay = None
        self.enabled = self.load_enabled()
        if self.enabled and self.overlay_key and load_settings().get(self.overlay_key, {}).get("shown"):
            self.toggle_overlay()
        self.enabled_changed()

    # ---- the Settings page
    def page(self, tab, snap):
        return getattr(self, "page_" + tab.lower().replace(" ", "_"))(snap)

    def act(self, id, value):
        """A control on the page: on_<id>(value). May return {"copy": text} or {"message": text} for the page.
        A switch "flag_<key>" with no handler of its own sets cfg[key] and saves."""
        handler = getattr(self, "on_" + id, None)
        if handler is None and id.startswith("flag_") and id[5:] in getattr(self, "cfg", {}):
            self.cfg[id[5:]] = bool(value)
            return self.save()
        if handler is None:
            raise ValueError("%s has no control %s" % (self.title, id))
        return handler(value)

    def save(self):
        """Store self.cfg (features with settings override this)."""

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

    def enabled_changed(self):
        """What the tracker parses."""
        if self.tracks:
            users = [f for f in getattr(self.app, "features", {}).values() if f.tracks == self.tracks and f is not self]
            on = self.enabled or any(f.enabled for f in users)
            setattr(self.app.tracker, "track_" + self.tracks, on)
            if not on:
                with self.app.tracker.lock:
                    getattr(self.app.tracker, self.tracks).clear()

    # ---- overlay (shared by every feature that has one)
    def overlay_cfg(self):
        return self.overlay.cfg if self.overlay else dict(OVERLAY_DEFAULT, **load_settings().get(self.overlay_key, {}))

    def save_overlay_cfg(self, cfg):
        if self.overlay:
            self.overlay.save()
        else:
            save_settings(dict(load_settings(), **{self.overlay_key: cfg}))

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

    def set_overlay_cone(self, on):
        cfg = self.overlay_cfg()
        cfg["cone"] = bool(on)
        self.save_overlay_cfg(cfg)
        if self.overlay and not on:
            self.overlay.canvas.delete("cone")
        self.app.cone_serial = -1  # redraw (or drop) the cone right away

    def overlay_scale(self, factor):
        cfg = self.overlay_cfg()
        cfg["ppm"] = max(0.05, min(20.0, cfg["ppm"] * factor))
        self.save_overlay_cfg(cfg)

    def overlay_recenter(self):
        """Back to the middle of the main screen, unlocked (if it got lost off-screen)."""
        if not self.overlay:
            self.toggle_overlay()
        if not self.overlay:
            return
        cfg = self.overlay.cfg
        cfg["x"] = self.root.winfo_screenwidth() // 2 - cfg["w"] // 2
        cfg["y"] = self.root.winfo_screenheight() // 2 - cfg["h"] // 2
        self.overlay.place()
        if cfg["locked"]:
            self.overlay.set_locked(False)  # so it can be dragged into place
        self.overlay.save()

    def overlay_changed(self):
        """Called by the overlay when it's moved, scaled or (un)locked; the Settings page reads it fresh."""

    def paint_overlay(self, overlay, canvas, snap):
        pass

    # ---- loop hooks
    def background(self, snap):
        if self.overlay:
            self.overlay.draw(snap)

    def status(self, snap):
        return None

    def close(self):
        pass
