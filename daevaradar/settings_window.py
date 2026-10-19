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
ERROR_LOG = CONFIG / "DaevaRadar" / "window_error.log"  # why the last web window couldn't open (App reads it)


def fail(reason):
    """Can't show the window: say why (the radar logs it and opens the page in the browser)."""
    import traceback
    try:
        ERROR_LOG.parent.mkdir(parents=True, exist_ok=True)
        ERROR_LOG.write_text("%s\n%s" % (reason, traceback.format_exc()), encoding="utf-8")
    except OSError:
        pass
    sys.exit(NO_WEBVIEW)


WEBVIEW2_DOWNLOAD = "https://developer.microsoft.com/microsoft-edge/webview2/"
NO_RUNTIME = "Microsoft Edge WebView2 Runtime isn't installed"


def missing_runtime():
    """Windows: what the window needs that this PC lacks (else None). pywebview needs the Edge WebView2 Runtime and
    .NET Framework 4.6.2+; without them it falls back to Internet Explorer, which the app doesn't ship (and its pages
    wouldn't run in)."""
    if not WINDOWS:
        return None
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full") as k:
            if winreg.QueryValueEx(k, "Release")[0] < 394802:
                return ".NET Framework 4.6.2 or newer isn't installed"
    except OSError:
        return ".NET Framework 4.6.2 or newer isn't installed"
    client = r"Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"  # the WebView2 Runtime
    for root, path in ((winreg.HKEY_LOCAL_MACHINE, "SOFTWARE\\WOW6432Node\\" + client),
                       (winreg.HKEY_LOCAL_MACHINE, "SOFTWARE\\" + client), (winreg.HKEY_CURRENT_USER, "SOFTWARE\\" + client)):
        try:
            with winreg.OpenKey(root, path) as k:
                if winreg.QueryValueEx(k, "pv")[0] not in ("", "0.0.0.0"):
                    return None
        except OSError:
            continue
    return NO_RUNTIME


def data_folder():
    """WebView2's data folder. Kept apart for an elevated window (the radar runs as administrator): WebView2 won't
    share one folder between elevated and normal processes, and fails to start if asked to."""
    admin = False
    if WINDOWS:
        try:
            admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
        except OSError:
            pass
    return CONFIG / "DaevaRadar" / ("webview-admin" if admin else "webview")


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
    lacking = missing_runtime()
    if lacking:
        fail(lacking)
    try:
        import webview
    except ImportError:
        fail("pywebview isn't installed for this Python (python -m pip install pywebview)")
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
        webview.start(private_mode=False, storage_path=str(data_folder()))
    except Exception as e:
        fail("the window couldn't start (Edge WebView2): %r" % e)
