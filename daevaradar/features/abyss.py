"""Abyss Points: small gains come from monsters, gains of at least the amount you set (100) from killing players.
Player kills are logged (abyss_points.log) and can pop up; every gain counts toward the hourly totals, split players /
monsters (abyss_hours.json); each character's total over time (abyss_totals.json) is the graph."""
import json
import time

from ..common import CONFIG, load_settings, save_settings
from ..sound import play
from ..ui import Toast
from ..feature import Feature
from ..game import map_name
from .. import page as P


ABYSS_DEFAULT = dict(min=100, popup=True, sound=False, span=7)
ABYSS_LOG = CONFIG / "DaevaRadar" / "abyss_points.log"  # tab-separated: time, gained, total, character, map
HEADER = "time\tgained\ttotal\tcharacter\tmap\n"
COLOR = "#b48cff"
KEEP = 200  # log lines shown on the page
HOURS_FILE = CONFIG / "DaevaRadar" / "abyss_hours.json"  # {hour start (unix time): [from monsters, from players, kills]}
HOURS_KEPT = 30 * 24
TOTALS_FILE = CONFIG / "DaevaRadar" / "abyss_totals.json"  # {character: [[unix time, total], ...]}, a point a minute at most
TOTALS_KEPT = 30 * 24 * 3600
SPANS = ((1, "Day"), (7, "Week"), (30, "Month"))
SAVE_EVERY = 30  # seconds between writes of the hourly totals (and at exit)


class AbyssFeature(Feature):
    """The game sends your Abyss Points total with every change (an inventory update, see Tracker.note_abyss); a rise
    is a gain. Monsters give 10-30, a player kill 100 and more: gains of at least cfg["min"] are player kills, which go
    to the log file and the Log tab, and pop up (with a beep) if asked."""
    title, icon, TABS = "Abyss Points", "✦", ("Log", "Settings")
    off_text = "Gains aren't counted or logged and nothing pops up while it's off."

    def __init__(self, app):
        self.cfg = dict(ABYSS_DEFAULT, **load_settings().get("abyss", {}))
        self.rows = self.read_log()  # [time text, gain, total, character, map], newest last
        self.session = []  # (when, gain, from a player?) of every gain since the app started
        self.hours = self.read_hours()  # {hour start: [from monsters, from players, player kills]}
        self.totals = self.read_totals()  # {character: [[when, total]]}
        self.viewed = None  # the character the graph shows (None: the one playing)
        self.hours_dirty, self.hours_saved = False, 0
        super().__init__(app)

    @staticmethod
    def read_log():
        try:
            lines = ABYSS_LOG.read_text(encoding="utf-8").splitlines()[1:]
        except OSError:
            return []
        return [line.split("\t") for line in lines[-KEEP:] if line.count("\t") == 4]

    @staticmethod
    def read_hours():
        try:
            data = json.loads(HOURS_FILE.read_text(encoding="utf-8")).items()
            return {int(k): [int(x) for x in v] if isinstance(v, list) else [int(v), 0, 0] for k, v in data}
        except (OSError, ValueError, AttributeError):
            return {}

    @staticmethod
    def read_totals():
        try:
            data = json.loads(TOTALS_FILE.read_text(encoding="utf-8"))
            return {str(k): [[float(w), int(t)] for w, t in v] for k, v in data.items()}
        except (OSError, ValueError, AttributeError, TypeError):
            return {}

    def note_total(self, when, total, who):
        """A total read for `who`: a new point, or the last one moved up when it's from the same minute."""
        points = self.totals.setdefault(who, [])
        if points and points[-1][1] == total:
            return
        if points and int(points[-1][0] // 60) == int(when // 60) and len(points) > 1:
            points[-1] = [when, total]
        else:
            points.append([when, total])
        self.hours_dirty = True

    def save_hours(self):
        if not self.hours_dirty:
            return
        cut = time.time() - TOTALS_KEPT
        self.totals = {k: [p for p in v if p[0] >= cut] or v[-1:] for k, v in self.totals.items()}  # the last one stays
        try:
            TOTALS_FILE.parent.mkdir(parents=True, exist_ok=True)
            TOTALS_FILE.write_text(json.dumps({k: [[round(w), t] for w, t in v] for k, v in self.totals.items() if v}),
                                   encoding="utf-8")
        except OSError:
            pass
        keep = sorted(self.hours)[-HOURS_KEPT:]
        self.hours = {h: self.hours[h] for h in keep}
        try:
            HOURS_FILE.parent.mkdir(parents=True, exist_ok=True)
            HOURS_FILE.write_text(json.dumps({str(h): v for h, v in self.hours.items()}), encoding="utf-8")
        except OSError:
            pass
        self.hours_dirty, self.hours_saved = False, time.time()

    def close(self):
        self.save_hours()

    def save(self):
        save_settings(dict(load_settings(), abyss=self.cfg))

    def enabled_changed(self):
        self.app.tracker.take_abyss_gains()  # gains made while it was off aren't logged later
        self.app.tracker.take_abyss_readings()

    # ---- loop hooks
    def background(self, snap):
        who = snap.get("own_name")
        for when, total in self.app.tracker.take_abyss_readings():
            if who:  # a total before the login names you can't be told apart from another character's
                self.note_total(when, total, who)
        for when, gain, total in self.app.tracker.take_abyss_gains():
            player = gain >= self.cfg["min"]
            self.session.append((when, gain, player))
            bucket = self.hours.setdefault(hour_of(when), [0, 0, 0])
            bucket[player] += gain
            bucket[2] += player
            self.hours_dirty = True
            if player:
                self.log(when, gain, total, snap)
        if self.hours_dirty and time.time() - self.hours_saved > SAVE_EVERY:
            self.save_hours()
        super().background(snap)

    def log(self, when, gain, total, snap):
        detected = self.app.detected
        row = [time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(when)), "+%d" % gain, str(total),
               snap.get("own_name") or "?", map_name(detected) if detected else "?"]
        self.rows = (self.rows + [row])[-KEEP:]
        self.app.engine.say("Abyss Points: player kill, +%d (now %d)" % (gain, total))
        try:
            new = not ABYSS_LOG.exists()
            ABYSS_LOG.parent.mkdir(parents=True, exist_ok=True)
            with open(ABYSS_LOG, "a", encoding="utf-8") as f:
                f.write((HEADER if new else "") + "\t".join(row) + "\n")
        except OSError:
            pass
        if self.cfg["popup"]:
            Toast(self.root, "Player kill: +{:,} Abyss Points\nnow {:,}".format(gain, total), color=COLOR, label="Abyss Points",
                  seconds=6)
        if self.cfg["sound"]:
            play("system beep")

    def status(self, snap):
        total = sum(gain for _, gain, _ in self.session)
        kills = sum(player for _, _, player in self.session)
        return "+{:,} since start · {} player kill{}".format(total, kills, "" if kills == 1 else "s") if total else "No gains yet"

    # ---- Log tab
    def page_log(self, snap):
        now = time.time()
        this = hour_of(now)
        today = hour_of(time.mktime(time.localtime(now)[:3] + (0, 0, 0, 0, 0, -1)))
        played = [v for h, v in self.hours.items() if sum(v[:2]) and this - 7 * 24 * 3600 <= h < this]  # full hours with gains
        day = [v for h, v in self.hours.items() if h >= today]

        def card(buckets, what, average=False):
            """+total, then its split: players (kills) / monsters."""
            n = max(1, len(buckets)) if average else 1
            mobs, players, kills = (sum(b[i] for b in buckets) for i in range(3))
            split = "players +{:,} ({:g} kill{})\nmonsters +{:,}".format(
                round(players / n), round(kills / n, 1), "" if kills == n else "s", round(mobs / n))
            return ("+{:,}".format(round((mobs + players) / n)) if buckets or not average else "–", what + "\n" + split)
        rows = [("%s  %8s  %10s  %-14s %s" % (r[0][5:], "+{:,}".format(int(r[1])), "{:,}".format(int(r[2])), r[3], r[4]),
                 COLOR if i == 0 else None)
                for i, r in enumerate(reversed(self.rows))]
        return [P.heading("Abyss Points", "Monsters give small amounts; a gain of %s or more is a player kill: those are "
                          "listed below (and pop up, if that's on in Settings)." % "{:,}".format(self.cfg["min"])),
                P.stats(card([self.hours.get(this, [0, 0, 0])], "this hour (since %s)" % time.strftime("%H:%M", time.localtime(this))),
                        card([self.hours.get(this - 3600, [0, 0, 0])], "last hour"),
                        card(played, "per hour played (7 days, %d h)" % len(played), average=True),
                        card(day, "today")),
                P.section("Total"),
                P.row(P.choice("span", [list(x) for x in SPANS], self.cfg["span"]),
                      P.choice("character", [(k, k) for k in sorted(self.totals)], self.shown(snap), style="select")
                      if len(self.totals) > 1 else None),
                self.graph(self.shown(snap), now, self.cfg["span"]),
                P.section("Player kills (gains of %s or more, newest first)" % "{:,}".format(self.cfg["min"])),
                P.lines(rows) if rows else P.text("No player kills yet: they show up here as you get them.", small=True),
                P.button("open_log", "Open the log file"),
                P.text(str(ABYSS_LOG), small=True)]

    def shown(self, snap):
        """The character the graph is for: picked, else the one playing, else the last one with readings."""
        for who in (self.viewed, snap.get("own_name")):
            if who in self.totals:
                return who
        return max(self.totals, key=lambda k: self.totals[k][-1][0], default=None) if self.totals else None

    def graph(self, who, now, days):
        """The character's total over the last `days` days: a line through its readings, flat to now."""
        w, h, left, right, bottom, top = 760, 240, 70, 14, 26, 18
        c = P.Canvas(w, h)
        start = now - days * 86400
        points = self.totals.get(who, [])
        before = [p for p in points if p[0] < start][-1:]
        shown = [[start, before[0][1]]] if before else []
        shown += [p for p in points if p[0] >= start]
        if not shown:
            c.create_text(w / 2, h / 2, text="No readings yet: log in (or relog) with Daeva Radar running.",
                          fill="#8a95a5", font=("Segoe UI", 10))
            return c.block()
        shown.append([now, shown[-1][1]])
        lo, hi = min(t for _, t in shown), max(t for _, t in shown)
        step = nice_step(max(hi - lo, 100) / 4)
        lo, hi = step * int(lo // step), step * (int(hi // step) + 1)
        plot_w, plot_h = w - left - right, h - top - bottom
        x_of = lambda when: left + plot_w * (when - start) / (now - start)
        y_of = lambda total: h - bottom - plot_h * (total - lo) / (hi - lo)
        value = lo
        while value <= hi:  # grid lines with their values
            c.create_line(left, y_of(value), w - right, y_of(value), fill="#2a313c")
            c.create_text(left - 6, y_of(value), text="{:,}".format(int(value)), fill="#8a95a5", anchor="e", font=("Segoe UI", 8))
            value += step
        ticks = 6 if days == 1 else 7 if days == 7 else 6
        for i in range(ticks + 1):  # time labels
            when = start + (now - start) * i / ticks
            c.create_text(x_of(when), h - bottom + 12, text=time.strftime("%H:%M" if days == 1 else "%d %b", time.localtime(when)),
                          fill="#8a95a5", font=("Segoe UI", 8), anchor="e" if i == ticks else "w" if i == 0 else "center")
        line = []
        for when, total in shown:  # a step line: the total holds until the next reading
            if line:
                line += [x_of(when), line[-1]]
            line += [x_of(when), y_of(total)]
        area = [x_of(shown[0][0]), h - bottom] + line + [x_of(now), h - bottom]
        c.create_polygon(*area, fill="#2b2440", outline="")
        c.create_line(*line, fill=COLOR, width=2)
        last = shown[-1][1]
        gained = last - shown[0][1]
        c.create_oval(x_of(now) - 4, y_of(last) - 4, x_of(now) + 4, y_of(last) + 4, fill=COLOR, outline="")
        c.create_text(left + 8, top - 6, text="%s: {:,}  ({}{:,} in this view)".format(last, "+" if gained >= 0 else "", gained) % who,
                      fill="#e6ebf2", anchor="w", font=("Segoe UI", 9, "bold"))
        return c.block()

    def on_span(self, value):
        self.cfg["span"] = int(value)
        self.save()

    def on_character(self, value):
        self.viewed = value

    def on_open_log(self, value):
        if not ABYSS_LOG.exists():
            return {"message": "No log yet: it's made with the first gain."}
        import os
        import subprocess
        import sys
        if sys.platform == "win32":
            os.startfile(str(ABYSS_LOG))
        else:
            subprocess.Popen(["xdg-open", str(ABYSS_LOG)])

    # ---- Settings tab
    def page_settings(self, snap):
        return [P.heading("Abyss Points settings"),
                P.section("A gain of at least this much is a player kill"),
                P.number("min", self.cfg["min"], 1, 100000, step=10, unit="Abyss Points"),
                P.choice("preset", [(v, "{:,}".format(v)) for v in (50, 100, 500, 1000)],
                         self.cfg["min"] if self.cfg["min"] in (50, 100, 500, 1000) else None),
                P.section("When you kill a player"),
                P.toggle("flag_popup", "Show a pop-up", self.cfg["popup"]),
                P.toggle("flag_sound", "Beep", self.cfg["sound"]),
                P.text("Monsters give about 10-30 Abyss Points, players 100 and more. Player kills are listed on the Log "
                       "tab and in the log file; changing the amount only affects new gains.", small=True)]

    def on_min(self, value):
        self.cfg["min"] = max(1, int(float(value)))
        self.save()

    def on_preset(self, value):
        self.on_min(value)


def hour_of(when):
    """The start of when's clock hour (local time)."""
    t = time.localtime(when)
    return int(when - t.tm_min * 60 - t.tm_sec - (when % 1))


def nice_step(raw):
    """A round grid step (1, 2, 5 x 10^n) of about raw."""
    mag = 10 ** max(0, len(str(int(max(raw, 1)))) - 1)
    return next(m * mag for m in (1, 2, 5, 10) if m * mag >= raw)


def short(value):
    return "%.1fk" % (value / 1000) if value >= 10000 else "{:,}".format(value)
