"""Boss timers: the field bosses' respawn times from the game's map menu (Altgard and Verteron have their own lists)."""
import datetime as dt
import json
import math
import time

from ..common import ROOT, berlin_offset, load_settings, save_settings
from ..game import MapData
from ..feature import Feature
from ..sound import play
from ..ui import MUTED, Toast
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
ROUTE_DEFAULT = dict(window="30", map="auto", alerts=[], lead="5", at_up=True, beep=True)  # alerts: "map:boss id"
LEADS = (("0", "Off"), ("5", "5 min"), ("10", "10 min"), ("15", "15 min"), ("30", "30 min"))


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
    """Respawn times of the bosses in the map menu. The game sends the list of the map you're in every few
    seconds, whether or not the map menu is open, so each map shows when it was last read; times are absolute, so they
    stay right after you leave the map."""
    title, icon, TABS = "Boss timers", "⏱", ("Timers", "Route")
    off_text = "The boss lists aren't read while it's off."

    def __init__(self, app):
        saved = load_settings().get("bosses", {})
        self.lists = {m: dict(times={int(k): v for k, v in d.get("times", {}).items()}, time=d.get("time"))
                      for m, d in saved.items()}
        self.seen = None
        self.fired = set()  # (alert, respawn time, "lead" | "up") already announced
        self.rows = {}  # boss map -> the boss ids of its list rows, in order
        self.read_zone = {}  # boss map -> the zone count when its list was read: you were on that map then
        self.data = BossData()
        self.cfg = dict(ROUTE_DEFAULT, **load_settings().get("boss_route", {}))
        self.learned = {m: {int(k): v for k, v in d.items()} for m, d in load_settings().get("boss_places", {}).items()}
        for m, _title in MAP_TITLES:  # a click on a row of the map's list toggles that boss's alert
            setattr(self, "on_bell_" + m.replace(" ", "_"), lambda index, m=m: self.toggle_alert(m, index))
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
                self.read_zone[m] = snap.get("zone_count")
                self.lists[m] = dict(times=dict(d["times"]), time=d["time"])
                self.app.engine.say("Boss timers: %s read (%d bosses)" % (m.title(), len(d["times"])))
        save_settings(dict(load_settings(), bosses={m: dict(time=d["time"], times={str(k): v for k, v in d["times"].items()})
                                                    for m, d in self.lists.items()}))

    def page_timers(self, snap):
        now = time.time()
        blocks = [P.heading("Boss timers"),
                  P.text("Each map is read while you're on it. Up = the time passed (it may be dead since). "
                         "Click a boss to alert for it (★).", small=True),
                  P.row(P.choice("lead", LEADS, self.cfg["lead"], label="Alert before"),
                        P.toggle("flag_at_up", "Also when it's up", self.cfg["at_up"]),
                        P.toggle("flag_beep", "Beep", self.cfg["beep"]))]
        for m, title in MAP_TITLES:
            blocks.append(P.section(title))
            d = self.lists.get(m)
            if not d or not d["times"]:
                blocks.append(P.text("Not read yet: it is read while you are on this map.", small=True))
                continue
            rows, here = [], self.where(m, snap)
            times = {b: spawn_time(m, b, at, now) for b, at in d["times"].items()}
            order = sorted(times.items(), key=lambda b: (not b[1][0], b[1][0]))
            self.rows[m] = [b for b, _ in order]
            for boss, (at, plan) in order:
                name, level = self.data.describe(m, boss)
                rest = at - now
                if not at:  # the game sends no time for it
                    when, color = "no time sent", MUTED
                elif rest <= 0:
                    when, color = "Up (since %s)" % ago(-rest) if -rest < 86400 else "Up", UP
                else:
                    stamp = time.strftime("%H:%M" if rest < 72000 else "%a %H:%M", time.localtime(at))
                    when, color = "%9s   %s%s" % (left(rest), stamp, "  (schedule)" if plan else ""),                         SOON if rest < SOON_S else LATER
                place = self.boss_place(m, boss)
                away = "  · %d m" % (math.dist(here[:2], place[:2]) / 100) if here and place else ""
                bell = "★" if self.alert_key(m, boss) in self.cfg["alerts"] else " "
                rows.append(("%s %-30.30s %-6s %s%s" % (bell, name, "Lv %d" % level if level else "", when, away), color))
            blocks.append(P.text("read %s ago" % ago(now - d["time"]) if d["time"] else "", small=True))
            blocks.append(P.listbox("bell_" + m.replace(" ", "_"), rows, None, height=min(len(rows), 24), dbl=False))
        return blocks

    @staticmethod
    def alert_key(m, boss):
        return "%s:%d" % (m, boss)

    def toggle_alert(self, m, index):
        ids = self.rows.get(m, [])
        if 0 <= int(index) < len(ids):
            key = self.alert_key(m, ids[int(index)])
            alerts = self.cfg["alerts"]
            alerts.remove(key) if key in alerts else alerts.append(key)
            self.save()

    def on_lead(self, value):
        self.cfg["lead"] = value
        self.save()

    def check_alerts(self):
        """A pop-up (and beep) `lead` minutes before a watched boss respawns, and when it is up."""
        now, lead = time.time(), int(self.cfg["lead"]) * 60
        for key in self.cfg["alerts"]:
            m, _, boss = key.partition(":")
            d = self.lists.get(m)
            if not d or int(boss) not in d["times"]:
                continue
            at = spawn_time(m, int(boss), d["times"][int(boss)], now)[0]
            if not at:
                continue
            name = self.data.describe(m, int(boss))[0]
            for kind, due, text in (("lead", lead and at - lead <= now < at, "%s respawns in %d min" % (name, lead // 60)),
                                    ("up", self.cfg["at_up"] and at <= now < at + 120, "%s is up" % name)):
                if due and (key, at, kind) not in self.fired:
                    self.fired.add((key, at, kind))
                    self.app.engine.say("Boss timers: %s" % text)
                    Toast(self.root, "%s\n%s" % (text, m.title()), color=SOON if kind == "lead" else UP, seconds=15,
                          label="Boss timers")
                    if self.cfg["beep"]:
                        play("system beep")
        self.fired = {f for f in self.fired if f[1] > now - 600}

    def background(self, snap):
        self.sync(snap)
        if self.enabled:
            self.check_alerts()
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
        here = self.app.map_key()
        for m, key in MAP_KEYS.items():
            if key == here and m in self.lists:
                return m
        return max(self.lists, key=lambda m: self.lists[m]["time"] or 0, default="verteron")

    def where(self, m, snap):
        """Your position if you're on boss map m: the map the app works out (or you picked), else the one whose boss list
        you read since you last teleported (the list only comes for the map you're in)."""
        if not snap or not snap.get("position"):
            return None
        key = self.app.map_key()
        on = key == MAP_KEYS[m] if key else self.read_zone.get(m) == snap.get("zone_count")
        return snap["position"] if on else None

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
        here = self.where(m, self.app.last_snap) if self.app.last_snap else None
        steps, _unknown = self.plan(m, here, time.time(), int(self.cfg["window"]) * 60)
        return dict(copy=self.train(steps))

    def page_route(self, snap):
        now = time.time()
        m = self.route_map()
        blocks = [P.heading("Boss route"),
                  P.text("Bosses up or respawning soon, by distance: on foot or via the nearest obelisk. Places are learned when a "
                         "boss is up; assumes every obelisk is unlocked.", small=True),
                  P.row(P.choice("map", (("auto", "Auto"), ("verteron", "Verteron"), ("altgard", "Altgard"),
                                         ("lower reshanta", "Lower Reshanta")), self.cfg["map"]),
                        P.choice("window", WINDOWS_MIN, self.cfg["window"]))]
        if m not in self.lists or not self.lists[m]["times"]:
            blocks.append(P.text("%s's boss list isn't read yet: it is read while you are on that map." % m.title(), small=True))
            return blocks
        here = self.where(m, snap)
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
