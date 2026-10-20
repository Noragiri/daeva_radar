"""Boss timers: the field bosses' respawn times from the game's map menu (Altgard and Verteron have their own lists)."""
import json
import time

from ..common import ROOT, load_settings, save_settings
from ..feature import Feature
from ..ui import MUTED
from .. import page as P

MAP_TITLES = (("verteron", "Verteron"), ("altgard", "Altgard"))
UP, SOON, LATER = "#7cf08a", "#ffb347", "#d7dde6"
SOON_S = 600  # within 10 minutes of respawning


def ago(seconds):
    seconds = int(max(0, seconds))
    if seconds < 90:
        return "%d s" % seconds
    if seconds < 5400:
        return "%d min" % (seconds // 60)
    return "%d h %02d min" % (seconds // 3600, seconds % 3600 // 60)


def left(seconds):
    seconds = int(seconds)
    if seconds < 3600:
        return "%d:%02d" % (seconds // 60, seconds % 60)
    return "%d:%02d:%02d" % (seconds // 3600, seconds % 3600 // 60, seconds % 60)


class BossData:
    """data/bosses.json (boss list record id -> monster type, per map) and data/monsters.json (names, levels)."""

    def __init__(self):
        try:
            maps = json.loads((ROOT / "data" / "bosses.json").read_text(encoding="utf-8"))["maps"]
            self.maps = {m: {int(k): v for k, v in d["bosses"].items()} for m, d in maps.items()}
        except (OSError, ValueError, KeyError):
            self.maps = {}
        try:
            self.monsters = json.loads((ROOT / "data" / "monsters.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.monsters = {}

    def describe(self, map_name, boss):
        """-> (name, level) of a boss list record."""
        type_id = self.maps.get(map_name, {}).get(boss)
        info = self.monsters.get(str(type_id)) if type_id else None
        if info:
            return info[0], info[1]
        return "Unknown boss %d" % boss, None


class BossesFeature(Feature):
    """Respawn times of the bosses in the map menu. The game sends a map's list when you open it (and only the map
    you're in), so each map shows when it was last read; times are absolute, so they stay right while the map is closed."""
    title, icon, TABS = "Boss timers", "⏱", ("Timers",)
    off_text = "The boss lists aren't read while it's off."

    def __init__(self, app):
        saved = load_settings().get("bosses", {})
        self.lists = {m: dict(times={int(k): v for k, v in d.get("times", {}).items()}, time=d.get("time"))
                      for m, d in saved.items()}
        self.seen = None
        self.data = BossData()
        super().__init__(app)

    def sync(self, snap):
        if snap["boss_count"] == self.seen:
            return
        first = self.seen is None
        self.seen = snap["boss_count"]
        if first and not snap["bosses"]:
            return
        for m, d in snap["bosses"].items():
            if m not in self.lists or self.lists[m]["time"] != d["time"]:
                self.lists[m] = dict(times=dict(d["times"]), time=d["time"])
                self.app.engine.say("Boss timers: %s read (%d bosses)" % (m.title(), len(d["times"])))
        save_settings(dict(load_settings(), bosses={m: dict(time=d["time"], times={str(k): v for k, v in d["times"].items()})
                                                    for m, d in self.lists.items()}))

    def page_timers(self, snap):
        now = time.time()
        blocks = [P.heading("Boss timers"),
                  P.text("Open the map menu's boss list in the game to read a map: each map only sends its own list, "
                         "and Altgard can only be entered every 3 hours. Times are absolute, so they keep counting while "
                         "you're elsewhere. 'Up' means the respawn time passed: it may have been killed since.", small=True)]
        for m, title in MAP_TITLES:
            blocks.append(P.section(title))
            d = self.lists.get(m)
            if not d or not d["times"]:
                blocks.append(P.text("Not read yet: open the boss list of this map in the game.", small=True))
                continue
            rows = []
            for boss, at in sorted(d["times"].items(), key=lambda b: b[1]):
                name, level = self.data.describe(m, boss)
                rest = at - now
                if rest <= 0:
                    when, color = "Up (since %s)" % ago(-rest) if -rest < 86400 else "Up", UP
                else:
                    stamp = time.strftime("%H:%M" if rest < 72000 else "%a %H:%M", time.localtime(at))
                    when, color = "%9s   %s" % (left(rest), stamp), SOON if rest < SOON_S else LATER
                rows.append(("%-30.30s %-6s %s" % (name, "Lv %d" % level if level else "", when), color))
            blocks.append(P.text("read %s ago" % ago(now - d["time"]) if d["time"] else "", small=True))
            blocks.append(P.lines(rows))
        return blocks

    def background(self, snap):
        self.sync(snap)
        super().background(snap)

    def status(self, snap):
        n = sum(1 for d in self.lists.values() for at in d["times"].values() if at <= time.time())
        return "%d up" % n if n else None
