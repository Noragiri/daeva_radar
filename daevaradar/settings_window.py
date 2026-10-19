"""The app's web windows: Settings, and the World map (App.open_map). Each is its own small process
(DaevaRadar.exe --settings-window URL) showing a page from the radar's local server (webui.py) with pywebview (Edge
WebView2 on Windows). Separate, because pywebview and Tk each need a main thread; and if one fails, the radar keeps
running. Exit code 3: no pywebview / WebView2 (the radar then opens the page in the default browser instead).
The URL says which window it is: "solo=map" is the World map ("ontop=1": above the game)."""
import ctypes
import sys
from urllib.parse import parse_qs, urlsplit

from .common import ASSETS, CONFIG, WINDOWS

NO_WEBVIEW = 3


def set_window_icon(hwnd):
    """The app's icon on a window (title bar, Alt+Tab, taskbar), from assets/icon.ico."""
    user32 = ctypes.windll.user32
    user32.LoadImageW.restype = ctypes.c_void_p
    user32.LoadImageW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    user32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
    path = str(ASSETS / "icon.ico")
    for kind, size in ((1, 32), (0, 16)):  # ICON_BIG (taskbar, Alt+Tab), ICON_SMALL (title bar)
        icon = user32.LoadImageW(None, path, 1, size, size, 0x10)  # IMAGE_ICON, LR_LOADFROMFILE
        if icon:
            user32.SendMessageW(hwnd, 0x80, kind, icon)  # WM_SETICON


def run(url):
    try:
        import webview
    except ImportError:
        sys.exit(NO_WEBVIEW)
    query = parse_qs(urlsplit(url).query)
    solo = query.get("solo", [""])[0]
    if WINDOWS:  # the app's taskbar button (Settings and the World map stack on it), not Python's
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("DaevaRadar.Radar")  # = main.py's

    MIN_W, MIN_H = (700, 480) if solo == "map" else (860, 560)

    class Api:
        """Called by the page: its own top bar (the window is frameless) and its resize edges."""
        _maximized = False

        def focus(self):
            """Bring the window to the front (the tray's Settings while it's already open)."""
            window.restore()
            window.show()
            window.on_top = True
            window.on_top = query.get("ontop") == ["1"]

        def on_top(self, on):
            window.on_top = bool(on)

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

    title = "Daeva Radar · World map" if solo == "map" else "Daeva Radar"
    width, height = (1180, 780) if solo == "map" else (1120, 740)
    window = webview.create_window(title, url, width=width, height=height, min_size=(MIN_W, MIN_H),
                                   background_color="#0f1318", js_api=Api(), text_select=False,
                                   frameless=True, easy_drag=False, shadow=True, on_top=query.get("ontop") == ["1"])

    def shown():
        if WINDOWS:
            try:
                set_window_icon(window.native.Handle.ToInt64())  # pythonnet: the WinForms form's handle
            except Exception:
                pass
    window.events.shown += shown
    try:
        webview.start(private_mode=False, storage_path=str(CONFIG / "DaevaRadar" / "webview"))
    except Exception:
        sys.exit(NO_WEBVIEW)
