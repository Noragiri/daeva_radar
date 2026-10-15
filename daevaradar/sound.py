"""Alert sounds: bundled voice clips, the system beep or your own WAV/MP3, with volume."""
from pathlib import Path
import ctypes
import os
import subprocess

from .common import SOUNDS, WINDOWS


def voices():
    """Bundled callouts (sounds/cube-<name>.wav) plus the system beep and silence."""
    return sorted(p.stem[5:] for p in SOUNDS.glob("cube-*.wav")) + ["system beep", "off"]
_scaled = {}
def scaled(path, volume):
    """Copy of a PCM WAV with samples scaled to volume % (winsound has no volume control); cached."""
    if volume == 100:
        return path
    import array
    import tempfile
    import wave
    key = (path, os.path.getmtime(path), volume)
    if key in _scaled and os.path.exists(_scaled[key]):
        return _scaled[key]
    try:
        with wave.open(path) as w:
            params, width = w.getparams(), w.getsampwidth()
            frames = w.readframes(w.getnframes())
    except (wave.Error, EOFError):
        return path  # not plain PCM: play unchanged
    gain = volume / 100
    if width == 1:  # unsigned 8-bit
        frames = bytes(max(0, min(255, round((b - 128) * gain) + 128)) for b in frames)
    elif width in (2, 4):
        samples = array.array("h" if width == 2 else "i", frames)
        top = (1 << (width * 8 - 1)) - 1
        samples = array.array(samples.typecode, (max(-top - 1, min(top, round(s * gain))) for s in samples))
        frames = samples.tobytes()
    elif width == 3:
        out = bytearray()
        for i in range(0, len(frames) - 2, 3):
            s = int.from_bytes(frames[i:i + 3], "little", signed=True)
            out += max(-8388608, min(8388607, round(s * gain))).to_bytes(3, "little", signed=True)
        frames = bytes(out)
    else:
        return path
    folder = Path(tempfile.gettempdir()) / "DaevaRadar"
    folder.mkdir(exist_ok=True)
    target = str(folder / ("%d-%s" % (volume, Path(path).name)))
    with wave.open(target, "wb") as w:
        w.setparams(params)
        w.writeframes(frames)
    _scaled[key] = target
    return target
def mci(command):
    buf = ctypes.create_unicode_buffer(256)
    return ctypes.windll.winmm.mciSendStringW(command, buf, 256, None), buf.value
def play_mci(path, volume):
    """MP3 and other non-WAV audio through Windows MCI; its volume tops out at 100 %."""
    mci("close daevaradar")
    err, _ = mci('open "%s" type mpegvideo alias daevaradar' % path)
    if err:
        return False
    mci("setaudio daevaradar volume to %d" % min(1000, volume * 10))
    return mci("play daevaradar")[0] == 0
def play_linux(path, volume):
    """First installed player: WAV (volume already applied) via PipeWire/PulseAudio/ALSA; others via
    mpv (to 200 %), ffplay or mpg123 (to 100 %)."""
    import shutil
    if path.lower().endswith(".wav"):
        path = scaled(path, volume)
        players = [["paplay"], ["pw-play"], ["aplay", "-q"]]
    else:
        players = [["mpv", "--no-video", "--really-quiet", "--volume-max=200", "--volume=%d" % volume],
                   ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", "-volume", str(min(volume, 100))],
                   ["mpg123", "-q", "-f", str(int(32768 * min(volume, 100) / 100))]]
    for player in players:
        if shutil.which(player[0]):
            subprocess.Popen(player + [path], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
            return True
    return False
LINUX_BEEP = "/usr/share/sounds/freedesktop/stereo/complete.oga"
def play(voice, custom=None, volume=100):
    """voice: a bundled name, 'custom' (play the custom WAV/MP3), 'system beep' or 'off'."""
    if voice == "off" or volume <= 0:
        return
    if voice == "system beep":
        if WINDOWS:
            import winsound
            winsound.MessageBeep(winsound.MB_ICONASTERISK)
        elif not (os.path.exists(LINUX_BEEP) and play_linux(LINUX_BEEP, 100)):
            print("\a", end="", flush=True)
        return
    path = custom if voice == "custom" else str(SOUNDS / ("cube-%s.wav" % voice))
    if not path or not os.path.exists(path):
        return
    if not WINDOWS:
        play_linux(path, volume)
    elif path.lower().endswith(".wav"):
        import winsound
        winsound.PlaySound(scaled(path, volume), winsound.SND_FILENAME | winsound.SND_ASYNC)
    else:
        play_mci(path, volume)
