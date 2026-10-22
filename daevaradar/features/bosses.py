"""Boss timers: the field bosses' respawn times from the game's map menu (Altgard and Verteron have their own lists)."""
import datetime as dt
import json
import math
import time

from ..common import ROOT, load_settings, save_settings
from ..game import MapData
from ..feature import Feature
from ..ui import MUTED
from .. import page as P

MAP_TITLES = (("verteron", "Verteron"), ("altgard", "Altgard"), ("lower reshanta", "Lower Reshanta"))
UP, SOON, LATER = "#7cf08a", "#ffb347", "#d7dde6"
SOON_S = 600  # within 10 minutes of respawning
MAP_KEYS = {"verteron": "verteron", "altgard": "altgard", "lower reshanta": "reshanta-lower"}  # boss list -> map data name
TRAIN_MAX = 100  # characters the game's chat takes
NO_TELEPORT = {"lower reshanta"}  # no obelisks there: routes are on foot
RUN_MS = 8.0   # metres a second on foot (a guess: a mount is faster)
TELEPORT_S = 25  # a teleport: the loading and the cast
WINDOWS_MIN = (("0", "Up now"), ("15", "15 min"), ("30", "30 min"), ("60", "1 h"), ("120", "2 h"), ("240", "4 h"))
ROUTE_DEFAULT = dict(window="30", map="auto")


def ago(seconds):
    seconds = int(max(0, seconds))
    if seconds < 90:
        return "%d s" % seconds
    if seconds < 5400:
        return "%d min" % (seconds // 60)
    return "%d h %02d min" % (seconds // 3600, seconds % 3600 // 60)


def left(seconds):
    seconds = int(seconds)
    if seconds >= 86400:
        return "%dd %dh %02dm" % (seconds // 86400, seconds % 86400 // 3600, seconds % 3600 // 60)
    if seconds < 3600:
        return "%d:%02d" % (seconds // 60, seconds % 60)
    return "%d:%02d:%02d" % (seconds // 3600, seconds % 3600 // 60, seconds % 60)


def berlin_offset(ts):
    """Seconds Europe/Berlin is ahead of UTC at unix time ts (CEST from the last Sunday of March 01:00 UTC to the last
    Sunday of October 01:00 UTC; written out because Windows has no tz database in Python)."""
    year = dt.datetime.fromtimestamp(ts, dt.timezone.utc).year

    def last_sunday(month):
        day = dt.datetime(year, month + 1, 1, 1, tzinfo=dt.timezone.utc) - dt.timedelta(days=1) if month < 12 else             dt.datetime(year, 12, 31, 1, tzinfo=dt.timezone.utc)
        return day - dt.timedelta(days=(day.weekday() + 1) % 7)

    return 7200 if last_sunday(3).timestamp() <= ts < last_sunday(10).timestamp() else 3600


def next_every_three_hours(now):
    """The next spawn of a boss that comes every 3 hours from 01:00 server time (Europe/Berlin: 01, 04, 07 ... 22)."""
    off = berlin_offset(now)
    local = dt.datetime.fromtimestamp(now + off, dt.timezone.utc)
    start = local.replace(hour=1, minute=0, second=0, microsecond=0)
    steps = int((local - start).total_seconds() // 10800) + 1
    return (start + dt.timedelta(hours=3 * steps)).timestamp() - off


# bosses whose list entry has no time but which have a fixed schedule (questlog.gg's Boss Schedule, 2026-10-22):
# Watcher Kaira, Lower Reshanta: every 3 hours from 01:00 server time, at a random place
SCHEDULED = {("lower reshanta", 2002): next_every_three_hours}


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
        try:
            raw = json.loads((ROOT / "data" / "bosses.json").read_text(encoding="utf-8"))["maps"]
            self.places = {m: {int(k): v for k, v in d.get("places", {}).items()} for m, d in raw.items()}
        except (OSError, ValueError, KeyError):
            self.places = {}
        try:
            self.teleports = json.loads((ROOT / "data" / "teleports.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.teleports = {}

    def describe(self, map_name, boss):
        """-> (name, level) of a boss list record."""
        type_id = self.maps.get(map_name, {}).get(boss)
        info = self.monsters.get(str(type_id)) if type_id else None
        if info:
            return info[0], info[1]
        return "Unknown boss %d" % boss, None


def spawn_time(m, boss, at, now):
    """-> (time, from the schedule?): the list's time, or the schedule's when the game sends none."""
    if not at and (m, boss) in SCHEDULED:
        return SCHEDULED[(m, boss)](now), True
    return at, False


class BossesFeature(Feature):
    """Respawn times of the bosses in the map menu. The game sends a map's list when you open it (and only the map
    you're in), so each map shows when it was last read; times are absolute, so they stay right while the map is closed."""
    title, icon, TABS = "Boss timers", "⏱", ("Timers", "Route")
    off_text = "The boss lists aren't read while it's off."

    def __init__(self, app):
        saved = load_settings().get("bosses", {})
        self.lists = {m: dict(times={int(k): v for k, v in d.get("times", {}).items()}, time=d.get("time"))
                      for m, d in saved.items()}
        self.seen = None
        self.data = BossData()
        self.cfg = dict(ROUTE_DEFAULT, **load_settings().get("boss_route", {}))
        self.learned = {m: {int(k): v for k, v in d.items()} for m, d in load_settings().get("boss_places", {}).items()}
        super().__init__(app)

    def sync(self, snap):
        if snap["boss_count"] == self.seen:
            return
        first = self.seen is None
        self.seen = snap["boss_count"]
        if first and not snap["bosses"]:
            return
        for m, d in snap["bosses"].items():
            seen = self.learned.setdefault(m, {})
            for boss, place in d.get("places", {}).items():  # a boss that is up comes with its place: remember it
                if seen.get(boss) != [round(v) for v in place]:
                    seen[boss] = [round(v) for v in place]
                    save_settings(dict(load_settings(), boss_places={k: {str(b): p for b, p in v.items()}
                                                                    for k, v in self.learned.items()}))
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
            times = {b: spawn_time(m, b, at, now) for b, at in d["times"].items()}
            for boss, (at, plan) in sorted(times.items(), key=lambda b: (not b[1][0], b[1][0])):
                name, level = self.data.describe(m, boss)
                rest = at - now
                if not at:  # the game sends no time for it
                    when, color = "no time sent", MUTED
                elif rest <= 0:
                    when, color = "Up (since %s)" % ago(-rest) if -rest < 86400 else "Up", UP
                else:
                    stamp = time.strftime("%H:%M" if rest < 72000 else "%a %H:%M", time.localtime(at))
                    when, color = "%9s   %s%s" % (left(rest), stamp, "  (schedule)" if plan else ""),                         SOON if rest < SOON_S else LATER
                rows.append(("%-30.30s %-6s %s" % (name, "Lv %d" % level if level else "", when), color))
            blocks.append(P.text("read %s ago" % ago(now - d["time"]) if d["time"] else "", small=True))
            blocks.append(P.lines(rows))
        return blocks

    def background(self, snap):
        self.sync(snap)
        super().background(snap)

    def status(self, snap):
        n = sum(1 for d in self.lists.values() for at in d["times"].values() if 0 < at <= time.time())
        return "%d up" % n if n else None

    # ---- Route
    def save(self):
        save_settings(dict(load_settings(), boss_route=self.cfg))

    def on_window(self, value):
        self.cfg["window"] = value
        self.save()

    def on_map(self, value):
        self.cfg["map"] = value
        self.save()

    def route_map(self):
        """The boss map the route is for: the one picked, else the one you're on, else the list read last."""
        if self.cfg["map"] in MAP_KEYS:
            return self.cfg["map"]
        here = self.app.map_key
        for m, key in MAP_KEYS.items():
            if key == here and m in self.lists:
                return m
        return max(self.lists, key=lambda m: self.lists[m]["time"] or 0, default="verteron")

    def boss_place(self, m, boss):
        return self.learned.get(m, {}).get(boss) or self.data.places.get(m, {}).get(boss)

    def obelisks(self, m):
        """-> [(number or None, name, [x, y] game units)] of the map's obelisks (teleports)."""
        if m in NO_TELEPORT:
            return []
        return [(n, name, MapData.to_world(px, py)[:2]) for n, name, px, py in self.data.teleports.get(MAP_KEYS[m], [])]

    def plan(self, m, here, now, window_s):
        """The bosses that are up or respawn within the window, in the order to visit them: each step takes the boss
        that is done soonest (arrival, or its respawn if later), arriving on foot or by the obelisk nearest to it.
        -> (steps, bosses without a known place); a step is dict(boss, name, level, ready, tele, run_m, at, done)."""
        d = self.lists[m]
        tele, steps, unknown, todo = self.obelisks(m), [], [], []
        for boss, at in d["times"].items():
            at = spawn_time(m, boss, at, now)[0]
            if not at or at - now > window_s:
                continue
            place = self.boss_place(m, boss)
            if place is None:
                unknown.append(boss)
            else:
                todo.append((boss, place[:2], max(0.0, at - now)))
        t, pos = 0.0, here[:2] if here else None
        while todo:
            best = None
            for boss, xy, ready in todo:
                by_tele = min(tele, key=lambda p: math.dist(p[2], xy), default=None)
                opts = []
                if by_tele:
                    run = math.dist(by_tele[2], xy) / 100
                    opts.append((t + TELEPORT_S + run / RUN_MS, by_tele, run))
                if pos:
                    run = math.dist(pos, xy) / 100
                    opts.append((t + run / RUN_MS, None, run))
                if not opts:
                    opts.append((t, None, 0.0))
                arrive, via, run = min(opts, key=lambda o: o[0])
                done = max(arrive, ready)
                if best is None or (done, arrive) < (best[0], best[1]):
                    best = (done, arrive, boss, xy, ready, via, run)
            done, arrive, boss, xy, ready, via, run = best
            name, level = self.data.describe(m, boss)
            steps.append(dict(boss=boss, name=name, level=level, ready=ready, tele=via, run_m=run, at=arrive, done=done))
            todo = [b for b in todo if b[0] != boss]
            t, pos = done, xy
        return steps, unknown

    @staticmethod
    def train(steps):
        """'TP #6 Boss -> TP #4 Boss -> ...' for up to five stops (on foot: just the name), cut after the last whole
        stop that fits the game's chat limit of 100 characters (checked 2026-10-22)."""
        text = ""
        for s in steps[:5]:
            tp = s["tele"]
            part = "TP %s %s" % ("#%d" % tp[0] if tp[0] else tp[1], s["name"]) if tp else s["name"]
            joined = text + " -> " + part if text else part
            if len(joined) > TRAIN_MAX:
                break
            text = joined
        return text

    def on_copy_train(self, _):
        m = self.route_map()
        if m not in self.lists:
            return None
        here = self.app.last_snap["position"] if self.app.last_snap and MAP_KEYS[m] == self.app.map_key else None
        steps, _unknown = self.plan(m, here, time.time(), int(self.cfg["window"]) * 60)
        return dict(copy=self.train(steps))

    def page_route(self, snap):
        now = time.time()
        m = self.route_map()
        blocks = [P.heading("Boss route"),
                  P.text("A route through the bosses that are up or about to respawn, by distance: on foot from where you "
                         "are, or by the obelisk (teleport) nearest to the boss. A boss's place is learned when it is "
                         "up (open the boss list in the game), so the route grows with use. It assumes every obelisk is "
                         "unlocked (Lower Reshanta has none: on foot).", small=True),
                  P.row(P.choice("map", (("auto", "Auto"), ("verteron", "Verteron"), ("altgard", "Altgard"),
                                         ("lower reshanta", "Lower Reshanta")), self.cfg["map"]),
                        P.choice("window", WINDOWS_MIN, self.cfg["window"]))]
        if m not in self.lists or not self.lists[m]["times"]:
            blocks.append(P.text("%s's boss list isn't read yet: open it in the game." % m.title(), small=True))
            return blocks
        here = snap["position"] if MAP_KEYS[m] == self.app.map_key else None
        steps, unknown = self.plan(m, here, now, int(self.cfg["window"]) * 60)
        rows = []
        for i, s in enumerate(steps, 1):
            tp = s["tele"]
            via = "TP %s%s, then " % ("#%d " % tp[0] if tp[0] else "", tp[1]) if tp else "on foot, "
            when = "up" if s["ready"] <= 0 else "in " + left(s["ready"])
            level = "Lv %d" % s["level"] if s["level"] else ""
            rows.append(("%2d. %-28.28s %-6s %s%d m  ·  %s" % (i, s["name"], level, via, s["run_m"], when),
                         UP if s["ready"] <= 0 else (SOON if s["ready"] < SOON_S else LATER)))
        total = steps[-1]["done"] if steps else 0
        train = self.train(steps)
        if train:
            blocks.append(P.section("Boss train (up to 5 · max 100 characters)"))
            blocks.append(P.text(train, bold=True))
            blocks.append(P.button("copy_train", "Copy"))
        blocks.append(P.section("%s · %d bosses%s" % (m.title(), len(steps), " · about %s" % ago(total) if steps else "")))
        blocks.append(P.lines(rows) if rows else P.text("No boss with a known place is up or respawning in this window.",
                                                        small=True))
        if unknown:
            names = ", ".join(self.data.describe(m, b)[0] for b in unknown)
            blocks.append(P.text("Place not known yet (learned when each is up): %s" % names, small=True))
        return blocks
