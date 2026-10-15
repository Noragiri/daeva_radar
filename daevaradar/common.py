"""Platform flags, the project's folders, and the settings file."""
from pathlib import Path
import os
import subprocess
import sys


ROOT = Path(__file__).resolve().parent.parent  # the project folder (assets/, data/, sounds/, windivert/)
ENTRY = ROOT / "daeva_radar.py"  # the launcher: relaunched elevated / as the Linux capture helper
ASSETS = ROOT / "assets"
SOUNDS = ROOT / "sounds"
HERE = ROOT
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
WINDOWS = sys.platform == "win32"
LINUX = sys.platform.startswith("linux")
CONFIG = Path(os.environ.get("APPDATA") or os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
SETTINGS = CONFIG / "DaevaRadar" / "settings.json"
OLD_SETTINGS = CONFIG / "CubeFinder" / "settings.json"  # from before the rename to Daeva Radar
def load_settings():
    import json
    path = SETTINGS if SETTINGS.exists() or not OLD_SETTINGS.exists() else OLD_SETTINGS  # until the first save
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
def save_settings(settings):
    import json
    try:
        SETTINGS.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS.write_text(json.dumps(settings), encoding="utf-8")
    except OSError:
        pass
