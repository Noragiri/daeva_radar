"""The Settings window's backend. A local web server (127.0.0.1 only, a random port) serves assets/web/ and a small
JSON API; the Settings window (settings_window.py, its own process) shows that page. Every API call needs the
random token from the page's URL, and the Host header must be this server's, so other pages and programs can't use
it. API calls run on the Tk thread (App.call), so they can touch the windows and the features.
  GET  /api/state           everything the page shows (polled about once a second)
  GET  /api/page?feature=F&tab=T   that feature tab's blocks (Feature.page, page.py), polled while it's shown
  POST /api/action          {"action": name, "args": {...}}: one of ACTIONS"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ctypes
import json
import secrets
import threading
import time

from urllib.parse import parse_qs

from .common import ASSETS, WINDOWS, load_settings, save_settings
from . import update

WEB = ASSETS / "web"
TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon"}


class WebUI:
    def __init__(self, app):
        self.app = app
        self.token = secrets.token_urlsafe(18)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self.handler())
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def url(self):
        return "http://127.0.0.1:%d/?t=%s" % (self.port, self.token)

    def close(self):
        self.server.shutdown()

    def handler(self):
        ui = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, code, body=b"", kind="text/plain; charset=utf-8"):
                self.send_response(code)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(body)

            def json(self, data):
                self.reply(200, json.dumps(data).encode("utf-8"), "application/json")

            def trusted(self, api):
                if self.headers.get("Host") != "127.0.0.1:%d" % ui.port:
                    return False
                if self.headers.get("Origin") not in (None, "http://127.0.0.1:%d" % ui.port):
                    return False
                return not api or secrets.compare_digest(self.headers.get("X-Token", ""), ui.token)

            def do_GET(self):
                path = self.path.split("?", 1)[0]
                if not self.trusted(path.startswith("/api/")):
                    return self.reply(403)
                if path in ("/api/state", "/api/page"):
                    query = {k: v[0] for k, v in parse_qs(self.path.partition("?")[2]).items()}
                    try:
                        if path == "/api/state":
                            return self.json(ui.app.call(ui.state))
                        return self.json(ui.app.call(lambda: ui.page(query.get("feature"), query.get("tab"))))
                    except Exception as e:
                        return self.reply(500, str(e).encode("utf-8"))
                file = (WEB / ("index.html" if path == "/" else path.lstrip("/"))).resolve()
                if WEB.resolve() not in file.parents or not file.is_file():
                    return self.reply(404)
                self.reply(200, file.read_bytes(), TYPES.get(file.suffix, "application/octet-stream"))

            def do_POST(self):
                if self.path != "/api/action" or not self.trusted(True):
                    return self.reply(403)
                try:
                    body = json.loads(self.rfile.read(min(int(self.headers.get("Content-Length", 0)), 65536)))
                    name, args = body["action"], body.get("args") or {}
                    if name not in ACTIONS:
                        return self.reply(400, b"unknown action")
                    result = ui.app.call(lambda: ACTIONS[name](ui.app, **args))
                    self.json({"ok": True, "result": result})
                except Exception as e:
                    ui.app.engine.say("Settings action failed: %r" % e)
                    self.json({"ok": False, "error": str(e)})
        return Handler

    # ---- the page's data (runs on the Tk thread)
    def page(self, title, tab):
        """A feature tab's blocks (page.py), from the latest snapshot (with the vision cone)."""
        feature = self.app.features[title]
        if tab not in feature.TABS:
            raise ValueError("no tab %s" % tab)
        self.app.web_view = (title, tab, time.time())
        snap = getattr(self.app, "last_snap", None) or self.app.snapshot()
        return dict(blocks=[b for b in feature.page(tab, snap) if b])

    def state(self):
        app = self.app
        snap = app.snapshot()
        settings = load_settings()
        release = app.release or {}
        features = []
        for f in app.features.values():
            features.append(dict(title=f.title, icon=f.icon, enabled=f.enabled, tabs=list(f.TABS),
                                 status=f.status(snap) if f.enabled else None,
                                 overlays=[o["id"] for o in overlays(app) if o["feature"] == f.title]))
        return dict(
            version=app.version, git=update.is_git(),
            update=dict(state=app.update_state, release=release.get("version"), checked=app.checked_at),
            status=dict(engine=app.engine.status, who=snap["own_name"], char=snap["own_char_no"],
                        messages=snap["messages"], packets=app.engine.packets, text=app.status_text()),
            map=dict(choice=app.map_cfg, choices=app.map_choices, detected=app.detected),
            general=dict(topmost=app.topmost.get(), settings_at_start=settings.get("settings_at_start", True),
                         debug=bool(app.debug), tray=bool(app.tray)),
            features=features, overlays=overlays(app), screens=screens(app),
            layout=bool(app.layout), characters=app.characters.listing(),
            focus=app.focus_seq, goto=getattr(app, "web_goto", None))


# ---- overlays: the minimap overlays (one per feature) and the info panels, for the layout grid
def overlays(app):
    from .ui import OVERLAY_DEFAULT
    out = []
    for f in app.features.values():
        if not f.overlay_key:
            continue
        cfg = f.overlay.cfg if f.overlay else dict(OVERLAY_DEFAULT, **load_settings().get(f.overlay_key, {}))
        out.append(dict(id="radar:" + f.title, title="%s minimap" % f.title, kind="radar", feature=f.title,
                        x=cfg["x"], y=cfg["y"], w=cfg["w"], h=cfg["h"], shown=bool(f.overlay),
                        locked=cfg["locked"], allowed=f.enabled, cone=cfg["cone"], across=round(cfg["w"] / cfg["ppm"]),
                        text=f.overlay_text))
    for p in app.panels.values():
        c = p.cfg
        out.append(dict(id="panel:" + p.key, title=p.title, kind="panel", feature=p.feature, x=c["x"], y=c["y"],
                        w=c["w"], h=p.height, shown=bool(p.win), locked=c["locked"], allowed=p.allowed()))
    return out


def find_overlay(app, oid):
    kind, _, name = oid.partition(":")
    if kind == "radar" and name in app.features and app.features[name].overlay_key:
        return "radar", app.features[name]
    if kind == "panel" and name in app.panels:
        return "panel", app.panels[name]
    raise ValueError("no overlay " + oid)


def screens(app):
    """The monitors (real pixels), for drawing the layout grid."""
    monitors = []
    if WINDOWS:
        from ctypes import wintypes

        def found(handle, dc, rect, data):
            r = rect.contents
            monitors.append(dict(x=r.left, y=r.top, w=r.right - r.left, h=r.bottom - r.top,
                                 primary=r.left == 0 and r.top == 0))
            return True
        proc = ctypes.WINFUNCTYPE(ctypes.c_int, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT),
                                  wintypes.LPARAM)
        ctypes.windll.user32.EnumDisplayMonitors(None, None, proc(found), 0)
    if not monitors:
        monitors = [dict(x=0, y=0, w=app.root.winfo_screenwidth(), h=app.root.winfo_screenheight(), primary=True)]
    return monitors


# ---- actions (run on the Tk thread)
def set_feature(app, title, on):
    app.features[title].set_enabled(bool(on))


def overlay_show(app, id, on):
    kind, obj = find_overlay(app, id)
    if kind == "radar":
        if bool(obj.overlay) != bool(on):
            obj.toggle_overlay()
    else:
        obj.show(on)


def overlay_lock(app, id, on):
    kind, obj = find_overlay(app, id)
    if kind == "radar":
        if obj.overlay:
            obj.overlay.set_locked(bool(on))
        else:
            cfg = obj.overlay_cfg()
            cfg["locked"] = bool(on)
            save_settings(dict(load_settings(), **{obj.overlay_key: cfg}))
    else:
        obj.set_locked(on)


def overlay_move(app, id, x, y, w=None, h=None):
    kind, obj = find_overlay(app, id)
    if kind == "radar":
        cfg = obj.overlay_cfg()
        cfg.update(x=int(x), y=int(y))
        if w and h:
            cfg.update(w=max(80, int(w)), h=max(60, int(h)))
        if obj.overlay:
            obj.overlay.place()
        obj.save_overlay_cfg(cfg)
    else:
        obj.move(x, y, w)


def overlay_scale(app, id, factor):
    """A minimap overlay's scale (metres across)."""
    find_overlay(app, id)[1].overlay_scale(float(factor))


def overlay_cone(app, id, on):
    find_overlay(app, id)[1].set_overlay_cone(on)


def overlay_recenter(app, id):
    find_overlay(app, id)[1].overlay_recenter()


def feature_act(app, feature, id, value=None):
    """A control on a feature's page (Feature.act -> on_<id>)."""
    return app.features[feature].act(id, value)


def set_faction(app, name, faction=None):
    app.characters.set_faction(name, faction)


def forget_character(app, name):
    app.characters.forget(name)


def edit_layout(app):
    app.edit_layout()


def lock_all(app, on):
    app.lock_overlays(bool(on))


def set_map(app, key):
    app.set_map(key)


def set_topmost(app, on):
    app.topmost.set(bool(on))
    app.set_topmost()


def set_settings_at_start(app, on):
    save_settings(dict(load_settings(), settings_at_start=bool(on)))


def check_updates(app):
    app.check_updates()


def install_update(app):
    if app.update_state not in ("available", "install_failed") or update.is_git():
        return False
    app.install_update()
    return True


def open_map(app, mode=None):
    app.open_map(mode)


def toggle_debug(app):
    app.toggle_debug()


def quit_app(app):
    app.root.after(200, app.close)


ACTIONS = {f.__name__: f for f in (feature_act, set_feature, overlay_show, overlay_lock, overlay_move, overlay_scale,
                                   overlay_cone, overlay_recenter, edit_layout, lock_all, set_map, set_topmost,
                                   set_settings_at_start, check_updates, install_update, open_map, toggle_debug,
                                   quit_app, set_faction, forget_character)}
