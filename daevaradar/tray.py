"""The tray icon (pystray): the app lives here; Settings, the overlays and Quit are in its menu. Its menu runs on
pystray's own thread, so every action is handed to the Tk thread (App.post)."""
from .common import ASSETS


def start_tray(app):
    """-> the running pystray Icon, or None when there's no tray (pystray or Pillow missing, no tray on Linux)."""
    try:
        import pystray
        from PIL import Image
    except ImportError:
        return None
    Item, Menu = pystray.MenuItem, pystray.Menu

    def act(fn):
        return lambda icon, item: app.post(fn)

    def overlay_items():
        items = []
        for o in app.overlay_list():
            items.append(Item(o["title"], act(lambda oid=o["id"], on=not o["shown"]: app.web_action(
                "overlay_show", id=oid, on=on)), checked=lambda item, oid=o["id"]: app.overlay_shown(oid),
                enabled=o["allowed"]))
        return items

    menu = Menu(
        Item("Settings", act(app.open_settings), default=True),
        Item("Overlays", Menu(overlay_items)),
        Item("Lock overlays", act(lambda: app.lock_overlays(not app.overlays_locked())),
             checked=lambda item: app.overlays_locked()),
        Item("Edit overlay layout", act(app.edit_layout), checked=lambda item: bool(app.layout)),
        Item("World map", act(app.open_map)),
        Menu.SEPARATOR,
        Item("Quit Daeva Radar", act(app.close)),
    )
    try:
        icon = pystray.Icon("DaevaRadar", Image.open(ASSETS / "icon.png"), "Daeva Radar", menu)
        icon.run_detached()
    except Exception as e:
        app.engine.say("Tray icon unavailable: %r" % e)
        return None
    return icon
