# PyInstaller recipe for the Windows build: a DaevaRadar folder with DaevaRadar.exe and everything it needs
# (Python, Tk, Pillow, data/, assets/, sounds/, windivert/), so players don't install Python.
#
#     python -m pip install pyinstaller pillow
#     python -m PyInstaller dev/daeva_radar.spec --distpath build/dist --workpath build/work --noconfirm
#
# -> build/dist/DaevaRadar/DaevaRadar.exe. contents_directory="." keeps the files beside the exe, where the app
# looks for them (common.ROOT) and where the in-app updater replaces them. .github/workflows/release.yml zips
# that folder as DaevaRadar-<version>-win64.zip.
import os
from pathlib import Path

root = Path(SPECPATH).parent
datas = [(str(root / d), d) for d in ("data", "assets", "sounds", "windivert", "docs")]
datas += [(str(root / f), ".") for f in ("README.md", "VERSION") if (root / f).exists()]

a = Analysis(
    [str(root / "daeva_radar.py")],
    pathex=[str(root)],
    datas=datas,
    hiddenimports=["PIL.ImageTk"],
    excludes=["unittest", "pydoc", "doctest", "lib2to3", "test"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="DaevaRadar",
    icon=str(root / "assets" / "icon.ico"),
    console=os.environ.get("DAEVA_CONSOLE") == "1",  # a window app (errors in a message box); 1: for debugging
    contents_directory=".",
)
coll = COLLECT(exe, a.binaries, a.datas, name="DaevaRadar")
