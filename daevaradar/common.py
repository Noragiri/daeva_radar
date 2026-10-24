"""Platform flags, the project's folders, and the settings file."""
from pathlib import Path
import datetime as dt
import os
import shutil
import subprocess
import sys
import threading
import time


FROZEN = getattr(sys, "frozen", False)  # the Windows build (DaevaRadar.exe, PyInstaller): no Python needed
# the project folder (assets/, data/, sounds/, windivert/); in the build, the folder DaevaRadar.exe is in
ROOT = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent.parent
ENTRY = ROOT / "daeva_radar.py"  # the launcher: relaunched elevated / as the Linux capture helper


def launch_command():
    """How to start this program again: the exe, or Python with the launcher."""
    return [sys.executable] if FROZEN else [sys.executable, str(ENTRY)]
ASSETS = ROOT / "assets"
SOUNDS = ROOT / "sounds"
HERE = ROOT
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
WINDOWS = sys.platform == "win32"
LINUX = sys.platform.startswith("linux")
CONFIG = Path(os.environ.get("APPDATA") or os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
SETTINGS = CONFIG / "DaevaRadar" / "settings.json"
OLD_SETTINGS = CONFIG / "CubeFinder" / "settings.json"  # from before the rename to Daeva Radar
_SETTINGS_LOCK = threading.Lock()


def _read_json(path):
    import json
    return json.loads(path.read_text(encoding="utf-8"))


def load_settings():
    path = SETTINGS if SETTINGS.exists() or not OLD_SETTINGS.exists() else OLD_SETTINGS  # until the first save
    backup = path.with_suffix(".bak")
    for attempt in (path, path, backup):  # twice: another process may be writing it; then the last good copy
        try:
            return _read_json(attempt)
        except (OSError, ValueError):
            time.sleep(0.05) if attempt is path else None
    return {}


def save_settings(settings):
    """Written to a temp file and moved over the old one, so a reader (or another Daeva Radar) never sees half a file.
    The last good copy is kept as settings.bak: load_settings falls back to it."""
    import json
    try:
        SETTINGS.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(settings)
        with _SETTINGS_LOCK:
            tmp = SETTINGS.with_suffix(".tmp%d" % os.getpid())
            tmp.write_text(text, encoding="utf-8")
            try:
                _read_json(SETTINGS)
                shutil.copyfile(SETTINGS, SETTINGS.with_suffix(".bak"))
            except (OSError, ValueError):
                pass
            os.replace(tmp, SETTINGS)
    except OSError:
        pass


_INSTANCE = []  # keeps the single-instance lock alive for as long as the process runs
_MUTEX = r"Local\DaevaRadar.SingleInstance"


def instance_running():
    """Is another Daeva Radar running (in this Windows session)? Looks only; see take_instance."""
    if WINDOWS:
        import ctypes
        kernel = ctypes.windll.kernel32
        kernel.OpenMutexW.restype = ctypes.c_void_p
        handle = kernel.OpenMutexW(0x100000, False, _MUTEX)  # SYNCHRONIZE
        if handle:
            kernel.CloseHandle(ctypes.c_void_p(handle))
            return True
        return kernel.GetLastError() == 5  # access denied: an elevated copy made it
    return not _lock_file(check_only=True)


def take_instance():
    """Claim 'the' running instance -> False if another one already has it."""
    if WINDOWS:
        import ctypes
        kernel = ctypes.windll.kernel32
        kernel.CreateMutexW.restype = ctypes.c_void_p
        handle = kernel.CreateMutexW(None, False, _MUTEX)
        if not handle or kernel.GetLastError() in (5, 183):  # denied / already exists
            return False
        _INSTANCE.append(handle)
        return True
    return _lock_file()


def _lock_file(check_only=False):
    try:
        import fcntl
        CONFIG.mkdir(parents=True, exist_ok=True)
        f = open(CONFIG / "DaevaRadar.lock", "w")
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if check_only:
            f.close()
        else:
            _INSTANCE.append(f)
        return True
    except (OSError, ImportError):
        return False


FOCUS_FILE = CONFIG / "DaevaRadar" / "focus.request"  # a second copy leaves this for the running one (works across elevation)


def request_focus():
    """A second copy was started: ask the running one to bring its Settings window forward, then let it take the focus."""
    try:
        FOCUS_FILE.parent.mkdir(parents=True, exist_ok=True)
        FOCUS_FILE.write_text(str(time.time()))
    except OSError:
        pass
    if WINDOWS:
        import ctypes
        ctypes.windll.user32.AllowSetForegroundWindow(-1)  # ASFW_ANY: this copy has the focus right, the other may use it


def focus_requested():
    """-> True once if a second copy asked (the request is consumed)."""
    try:
        FOCUS_FILE.unlink()
        return True
    except OSError:
        return False


def berlin_offset(ts):
    """Seconds Europe/Berlin is ahead of UTC at unix time ts (CEST from the last Sunday of March 01:00 UTC to the last
    Sunday of October 01:00 UTC; written out because Windows has no tz database in Python)."""
    year = dt.datetime.fromtimestamp(ts, dt.timezone.utc).year

    def last_sunday(month):
        day = dt.datetime(year, month + 1, 1, 1, tzinfo=dt.timezone.utc) - dt.timedelta(days=1) if month < 12 else             dt.datetime(year, 12, 31, 1, tzinfo=dt.timezone.utc)
        return day - dt.timedelta(days=(day.weekday() + 1) % 7)

    return 7200 if last_sunday(3).timestamp() <= ts < last_sunday(10).timestamp() else 3600
