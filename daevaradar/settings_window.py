"""The Settings window: its own small process (DaevaRadar.exe --settings-window URL) that shows the radar's local
Settings page (webui.py) with pywebview (Edge WebView2 on Windows). Separate, because pywebview and Tk each need a
main thread; and if it fails, the radar keeps running. Exit code 3: no pywebview / WebView2 (the radar then opens
the page in the default browser instead)."""
import sys

from .common import ASSETS, CONFIG, WINDOWS

NO_WEBVIEW = 3


def run(url):
    try:
        import webview
    except ImportError:
        sys.exit(NO_WEBVIEW)

    MIN_W, MIN_H = 860, 560

    class Api:
        """Called by the page: its own top bar (the window is frameless) and its resize edges."""
        _maximized = False

        def focus(self):
            """Bring the window to the front (the tray's Settings while it's already open)."""
            window.restore()
            window.show()
            window.on_top = True
            window.on_top = False

        def minimize(self):
            window.minimize()

        def toggle_maximize(self):
            if self._maximized:
                window.restore()
            else:
                window.maximize()
            self._maximized = not self._maximized
            return self._maximized

        def close(self):
            window.destroy()

        def size(self):
            return dict(w=window.width, h=window.height)

        def resize(self, w, h, side):
            """From the page's resize edges (logical pixels, like pywebview's): the edge opposite side stays put."""
            from webview.window import FixPoint
            fix = (FixPoint.EAST if "w" in side else FixPoint.WEST) | (FixPoint.SOUTH if "n" in side else FixPoint.NORTH)
            window.resize(max(MIN_W, int(w)), max(MIN_H, int(h)), fix)

    window = webview.create_window("Daeva Radar", url, width=1120, height=740, min_size=(MIN_W, MIN_H),
                                   background_color="#0f1318", js_api=Api(), text_select=False,
                                   frameless=True, easy_drag=False, shadow=True)

    def shown():
        if WINDOWS:  # the app's icon in the title bar and taskbar (else it's Python's / the exe's default)
            try:
                from System.Drawing import Icon  # pythonnet, which pywebview's Windows backend uses
                form = window.native
                form.Invoke(__import__("System").Action(lambda: setattr(form, "Icon", Icon(str(ASSETS / "icon.ico")))))
            except Exception:
                pass
    window.events.shown += shown
    try:
        webview.start(private_mode=False, storage_path=str(CONFIG / "DaevaRadar" / "webview"))
    except Exception:
        sys.exit(NO_WEBVIEW)
