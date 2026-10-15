"""Startup: command line, elevation, capture or demo, the window or console mode; the Windows shortcut."""
from pathlib import Path
import argparse
import ctypes
import math
import os
import struct
import subprocess
import sys
import threading
import time

from .common import ASSETS, ENTRY, HERE, LINUX, NO_WINDOW, WINDOWS, load_settings
from . import game
from .game import Recorder, TEMPLATES, Tracker
from .capture import Engine, capture_helper, find_windivert
from .sound import play, voices
from .ui import relative, show_error
from .app import Radar


def make_shortcut():
    """Windows: 'Daeva Radar.lnk' beside the script and on the Desktop: pythonw (no console) + icon."""
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    target = pythonw if pythonw.exists() else Path(sys.executable)
    script = (
        "$s = New-Object -ComObject WScript.Shell; "
        "foreach ($dir in @('{here}', [Environment]::GetFolderPath('Desktop'))) {{ "
        "$l = $s.CreateShortcut((Join-Path $dir 'Daeva Radar.lnk')); "
        "$l.TargetPath = '{target}'; $l.Arguments = '\"{main}\"'; $l.WorkingDirectory = '{here}'; "
        "$l.IconLocation = '{icon},0'; $l.Description = 'Daeva Radar: AION2 radar'; $l.Save() }}"
    ).format(here=HERE, target=target, main=HERE / "daeva_radar.py", icon=ASSETS / "icon.ico")
    subprocess.run(["powershell", "-NoProfile", "-Command", script], check=True, creationflags=NO_WINDOW)
    print("Created 'Daeva Radar.lnk' in %s and on your Desktop." % HERE)
def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except OSError:
        return False
def main():
    if sys.argv[1:] == ["--capture-helper"]:  # Linux root side, started by HelperCapture
        return capture_helper()
    parser = argparse.ArgumentParser(description="Standalone Hidden Cube radar for AION2 (passive capture: "
                                                 "WinDivert on Windows, a raw socket on Linux).")
    parser.add_argument("--windivert", help="Windows: folder with WinDivert.dll and WinDivert64.sys")
    parser.add_argument("--port", type=int, help="extra game server port to capture")
    parser.add_argument("--range", type=float, default=150, help="radar radius in metres (default 150)")
    parser.add_argument("--voice", help="cube callout: %s (default: last picked, else calm)" % ", ".join(voices()))
    parser.add_argument("--sound", help="your own WAV file to play when a cube appears")
    parser.add_argument("--volume", type=int, help="alert volume 0-200 %% (default: last set, else 100)")
    parser.add_argument("--no-sound", action="store_true", help="no alert sound")
    parser.add_argument("--no-topmost", action="store_true", help="don't keep the window on top")
    parser.add_argument("--console", action="store_true", help="no window; print cubes to the console")
    parser.add_argument("--record", help="save every decoded game message to this file (JSON lines), "
                        "to work out new message layouts")
    parser.add_argument("--log", help="also append messages and a 10 s status line to this file, "
                        "including the type id of any object you open within 10 m")
    parser.add_argument("--template", type=int, action="append", default=[],
                        help="extra cube type id to track (repeatable)")
    parser.add_argument("--shortcut", action="store_true",
                        help="Windows: create a 'Daeva Radar' shortcut (icon, no console) here and on the Desktop")
    parser.add_argument("--position", choices=("any", "old"), default="any",
                        help="how your position is read: any = every movement message about you (default); "
                             "old = only the old version's exact layouts")
    parser.add_argument("--debug", action="store_true",
                        help="open the position debug window (also from the sidebar)")
    parser.add_argument("--no-update-check", action="store_true",
                        help="don't check GitHub for a new version (at launch and every full hour)")
    parser.add_argument("--demo", action="store_true",
                        help="no capture or admin rights: simulated player and cubes, to try the window")
    args = parser.parse_args()
    TEMPLATES.update(args.template)
    game.POSITION_MODE = args.position
    settings = load_settings()
    if args.no_sound:
        args.voice = "off"
    elif args.sound:
        args.voice = "custom"
    elif args.voice is None:
        args.voice = settings.get("voice", "calm")
    if args.sound is None and settings.get("custom") and os.path.exists(settings["custom"]):
        args.sound = settings["custom"]
    if args.voice == "custom" and not args.sound:
        args.voice = "calm"
    if args.volume is None:
        args.volume = settings.get("volume", 100)
    args.volume = max(0, min(200, args.volume))
    if args.voice not in voices() + ["custom"]:
        parser.error("--voice must be one of: " + ", ".join(voices()))
    if not (WINDOWS or LINUX):
        sys.exit("Windows or Linux only.")
    if args.shortcut:
        return make_shortcut() if WINDOWS else sys.exit("--shortcut is Windows-only.")
    if WINDOWS:  # real pixels everywhere: screen capture and window positions must agree
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except (OSError, AttributeError):
            ctypes.windll.user32.SetProcessDPIAware()
    if WINDOWS:  # our own taskbar entry and icon, not Python's
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("DaevaRadar.Radar")
    if WINDOWS and not is_admin() and not args.demo:  # WinDivert needs elevation: relaunch through UAC (it starts in System32)
        argv = [str(ENTRY)]
        for prev, a in zip([None] + sys.argv[1:], sys.argv[1:]):
            argv.append(os.path.abspath(a) if os.path.exists(a) or prev in ("--log", "--record") else a)
        params = subprocess.list2cmdline(argv)
        # The elevated copy runs without a console window unless one was asked for.
        exe = Path(sys.executable)
        if not args.console and exe.with_name("pythonw.exe").exists():
            exe = exe.with_name("pythonw.exe")
        if ctypes.windll.shell32.ShellExecuteW(None, "runas", str(exe), params, None, 1) <= 32:
            show_error("Administrator rights are required (WinDivert needs them).")
        return
    try:
        folder = find_windivert(args.windivert) if WINDOWS and not args.demo else None
        tracker = Tracker()
        log = print
        if args.log:
            def log(text):
                print(text)
                with open(args.log, "a", encoding="utf-8") as f:
                    f.write(text + "\n")
        engine = Engine(tracker, folder, args.port, log)
        if args.log:
            tracker.log = engine.say
        if args.record:
            tracker.recorder = Recorder(args.record)
            engine.say("Recording game messages to " + args.record)
            threading.Thread(target=engine.heartbeat, daemon=True).start()
        if folder:
            engine.say("WinDivert from " + str(folder))
        run = (lambda: demo(tracker, engine)) if args.demo else engine.run
        if not args.demo:
            engine.say("Start this before logging in or changing zone; cubes appear as the server sends them.")
        if args.console:
            threading.Thread(target=console_loop, args=(tracker, engine, args), daemon=True).start()
            run()
        else:
            threading.Thread(target=run, daemon=True).start()
            Radar(tracker, engine, args).root.mainloop()
    except Exception as e:
        show_error(e)
def demo(tracker, engine):
    """--demo: no capture. Feeds made-up game messages through the real decoder: you walk a slow
    circle while cubes appear around you at various heights, then get opened or drift out of range,
    and monsters spawn, wander and die."""
    import random

    def varint_bytes(n):
        out = bytearray()
        while True:
            b, n = n & 127, n >> 7
            out.append(b | (128 if n else 0))
            if not n:
                return bytes(out)

    def point(x, y, z):
        return struct.pack("<fff", x, y, z)

    own, centre, step, next_id, live = 6190, (10000.0, 20000.0, 500.0), 0, 50000, {}
    mobs = {}
    # Krall Soldier (two variants), Krall Healer, Krall Karnif, Drana Test Subject, Failed Drana Mutant
    MOB_TYPES = (2700977, 2700972, 2700978, 2700980, 2110450, 2110451)
    engine.status = "Demo (simulated, no capture)"
    engine.say("Demo mode: simulated player, cubes and monsters; nothing is captured.")
    teleport = b"#6" + bytes(6) + point(*centre) + bytes(6)
    tracker.feed([b"36" + varint_bytes(own) + bytes(5), teleport], time.time())
    # your pet collection ('00 90'): a few pets at each level, then a soul drop now and then ('0d 90')
    pets = {1002: (1, 12), 1004: (2, 30), 1015: (1, 22), 1130: (1, 21), 1033: (2, 0), 1005: (3, 0)}
    tracker.feed([b"\x00\x90" + bytes(4) + varint_bytes(len(pets))
                  + b"".join(struct.pack("<III", p, p, lv) for p, (lv, s) in pets.items())
                  + varint_bytes(len(pets)) + b"".join(struct.pack("<II", p, s) for p, (lv, s) in pets.items())],
                 time.time())
    while not engine.stop:
        step += 1
        angle = step * 0.01  # one lap of a 40 m circle every ~2.5 minutes
        me = (centre[0] + 4000 * math.sin(angle), centre[1] - 4000 * math.cos(angle), centre[2])
        batch = [b"*8" + varint_bytes(own) + bytes(28) + point(*me)]
        if step % 16 == 1 and len(live) < 7:  # a cube every ~4 s
            dist, a = random.uniform(15, 190), random.uniform(0, 2 * math.pi)
            dz = random.choice((random.uniform(-25, -4), random.uniform(-2, 2), random.uniform(4, 30)))
            xyz = (me[0] + dist * 100 * math.sin(a), me[1] - dist * 100 * math.cos(a), me[2] + dz * 100)
            batch.append(b"46" + varint_bytes(next_id) + b"\x04\x00" + struct.pack("<I", 1200039) + point(*xyz))
            live[next_id] = time.time()
            next_id += 1
        for entity, born in list(live.items()):
            if time.time() - born > 20 and random.random() < 0.02:
                gone = random.random() < 0.5
                batch.append((b"\x05\x8d" if gone else b"56") + varint_bytes(entity) + (b"" if gone else b"\x04\x03\x00"))
                del live[entity]
        # monsters (real types from data/monsters.json): spawn ('41 36'), wander ('*8'), sometimes die ('04 8d')
        if step % 6 == 1 and len(mobs) < 14:
            type_id = random.choice(MOB_TYPES)
            dist, a = random.uniform(10, 120), random.uniform(0, 2 * math.pi)
            xyz = [me[0] + dist * 100 * math.sin(a), me[1] - dist * 100 * math.cos(a), me[2]]
            batch.append(b"A6" + varint_bytes(next_id) + bytes(3) + struct.pack("<I", type_id) + b"\x40\x02"
                         + point(*xyz) + bytes(40))
            mobs[next_id] = xyz
            next_id += 1
        for entity, xyz in list(mobs.items()):
            r = random.random()
            if r < 0.004:
                batch.append(b"\x04\x8d" + varint_bytes(entity) + bytes(4))
                del mobs[entity]
            elif r < 0.08:
                xyz[0] += random.uniform(-600, 600)
                xyz[1] += random.uniform(-600, 600)
                batch.append(b"*8" + varint_bytes(entity) + bytes(31) + point(*xyz))
        if step % 80 == 40:  # a soul drop every ~20 s
            batch.append(b"\r\x90" + varint_bytes(1) + struct.pack("<II", random.choice((1002, 1015, 1130)), random.randint(1, 2)))
        engine.packets += 1
        tracker.feed(batch, time.time())
        time.sleep(0.25)
def console_loop(tracker, engine, args):
    seen = 0
    while not engine.stop:
        snap = tracker.snapshot()
        if snap["detections"] > seen:
            seen = snap["detections"]
            for cube in snap["cubes"]:
                if cube["id"] == snap["last_detection"] and snap["position"]:
                    dist, dz, compass = relative(snap["position"], cube["xyz"])
                    engine.say("Cube! %d m %s (height %+d m)" % (dist, compass, dz))
                    break
            else:
                engine.say("Cube! (your position not known yet)")
            play(args.voice, args.sound, args.volume)
        time.sleep(0.25)
