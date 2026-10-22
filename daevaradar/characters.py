"""The characters seen logging in, with the faction you set for each (Settings → Characters). Keyed by name: the
login message's 4-byte "character number" turned out not to be stable (one character showed 15 of them)."""
import datetime as _dt
import time

from .common import load_settings, save_settings
from .game import CLASSES, CLASS_ICONS, COUNTERS, CURRENCIES, ENERGY

FACTIONS = ("Elyos", "Asmodian")
HISTORY_DAYS, HISTORY_CHANGES = 120, 300  # what's kept per character: a value per day, and the latest changes
FACTION_MAPS = {"Elyos": {"poeta", "verteron", "eltnen"}, "Asmodian": {"ishalgen", "altgard", "morheim"}}
HOME_MAP = {"Elyos": "verteron", "Asmodian": "altgard"}  # each side's land where pet souls are farmed
SHARED_MAP = "reshanta-lower"  # both sides farm here


# Duty Command: Abyss (the Abyss Command Shop's scrolls): 4 tiers, 5 per tier a week per SERVER (20); and the plain
# Duty Command, 12 a week (32 together, as on aion2t). The game doesn't send the counts (2026-10-20), so the player
# fills them in; they're cleared at the weekly reset.
COMMAND_IDS = (134019901, 134019902, 134019903, 134019904)  # tier 1-4 (white, green, blue, gold): also their icons
COMMAND_MAX = 5
COMMAND_COST = (15000, 37500, 75000, 150000)  # kinah each, from the Command Shop (2026-10-22)
DUTY_COST = 15000  # "Command: Verteron (Bound)", 12 a week
DUTY_MAX = 12


def last_weekly_reset(now=None):
    """Wednesday 09:00 (this PC's clock, like the game's "Resets: Wed 09:00 AM") at or before now."""
    now = now or _dt.datetime.now()
    r = now.replace(hour=9, minute=0, second=0, microsecond=0)
    r -= _dt.timedelta(days=(r.weekday() - 2) % 7)
    return r - _dt.timedelta(days=7) if r > now else r


def commands_get():
    """-> dict(t=[n, n, n, n], max=5, ids=[...], duty=n, duty_max=12) for this week: what was saved, or zeros after a reset."""
    saved = load_settings().get("commands") or {}
    week = last_weekly_reset().isoformat()
    fresh = saved.get("week") == week
    counts = saved.get("t", []) if fresh else []
    counts = [max(0, min(COMMAND_MAX, int(counts[i]))) if i < len(counts) else 0 for i in range(len(COMMAND_IDS))]
    duty = max(0, min(DUTY_MAX, int(saved.get("duty", 0)))) if fresh else 0
    return dict(t=counts, max=COMMAND_MAX, ids=list(COMMAND_IDS), duty=duty, duty_max=DUTY_MAX, cost=list(COMMAND_COST), duty_cost=DUTY_COST, week=week)


def commands_set(tier, value):
    """tier 0-3 sets that tier's count; -1 sets every tier (the All box). The count is clamped to 0-5."""
    now = commands_get()
    counts = now["t"]
    for i in (range(len(counts)) if tier == -1 else [int(tier)]):
        counts[i] = max(0, min(COMMAND_MAX, int(value)))
    save_settings(dict(load_settings(), commands=dict(week=now["week"], t=counts, duty=now["duty"])))


def duty_set(value):
    """The plain Duty Command's count, clamped to 0-12."""
    now = commands_get()
    save_settings(dict(load_settings(), commands=dict(week=now["week"], t=now["t"], duty=max(0, min(DUTY_MAX, int(value))))))


# What refills by itself between two readings (the player's rules, 2026-10-22); the PC's clock, like the resets:
# Odyle energy +15 every 3 hours (at 0, 3, 6, 9 ...) up to 840; Nightmare +2 a day and Shugo keys +2 a day (+3 with
# the premium subscription) at the daily reset (09:00), up to their limits. Weekly counters refill in the page.
RESET_HOUR = 9  # the daily and weekly reset (Wednesday for the weekly one)
ENERGY_MAX, ENERGY_STEP, ENERGY_EVERY_H = 840, 15, 3
DAILY_GAIN = {4: 2}  # counter id -> gained at each daily reset; Shugo keys (1) are 2, or 3 for subscribers
SHUGO_KEYS = 1
_EPOCH = _dt.datetime(2000, 1, 1)


def ticks_between(a, b, every_h, anchor_h=0):
    """How many times the local clock passed anchor_h + n x every_h hours after time a, up to and including b."""
    def n(ts):
        seconds = (_dt.datetime.fromtimestamp(ts) - _EPOCH).total_seconds()
        return int((seconds - anchor_h * 3600) // (every_h * 3600))
    return max(0, n(b) - n(a))


class Characters:
    def __init__(self, app):
        self.app = app
        settings = load_settings()
        self.data = settings.get("characters") or {}  # name -> dict(faction, first, last, maps)
        for entry in settings.get("shop_offers", {}).get("characters", {}).values():  # seen before this page existed
            if entry.get("name"):
                self.data.setdefault(entry["name"], dict(faction=None, first=None, last=None, maps=[]))
        self.playing = None
        self.saved_at = 0
        self.sheet_seen = None  # the tracker's sheet_count already stored

    def save(self):
        save_settings(dict(load_settings(), characters=self.data))
        self.saved_at = time.time()

    def note(self, snap):
        """Every tick: the character playing (from the login message) and the map it's detected on."""
        self.note_sheet(snap)
        name = snap["own_name"]
        if not name:
            return
        entry = self.data.setdefault(name, dict(faction=None, first=time.strftime("%Y-%m-%d"), last=None, maps=[]))
        changed = name != self.playing
        profile = snap.get("own_profile")
        if profile and (entry.get("cls"), entry.get("level")) != tuple(profile):
            entry["cls"], entry["level"] = profile
            changed = True
        self.playing = name
        entry["last"] = time.strftime("%Y-%m-%d %H:%M")
        where = self.app.detected  # detected live, not picked by hand (that could be wrong)
        if where and where not in entry["maps"]:
            entry["maps"].append(where)
            changed = True
        if changed or time.time() - self.saved_at > 300:
            self.save()

    def note_sheet(self, snap):
        """The checklist's data: currencies and counters read from the game, stored with the character they're for."""
        sheet = snap.get("sheet")
        if not sheet or not sheet["owner"] or snap["sheet_count"] == self.sheet_seen:
            return
        self.sheet_seen = snap["sheet_count"]
        entry = self.data.setdefault(sheet["owner"], dict(faction=None, first=time.strftime("%Y-%m-%d"), last=None, maps=[]))
        kept = entry.get("sheet") or {}
        self.note_history(entry, kept, sheet)
        entry["sheet"] = dict(counters=dict(kept.get("counters", {}), **{str(k): v for k, v in sheet["counters"].items()}),
                              currencies=dict(kept.get("currencies", {}), **{str(k): v for k, v in sheet["currencies"].items()}),
                              read=time.strftime("%Y-%m-%d %H:%M", time.localtime(sheet["time"])),
                              read_at=sheet["time"])
        self.save()

    @staticmethod
    def tracked(counters, currencies):
        """-> {name: value} of what the history keeps: the named currencies and counters (left; Odyle energy now)."""
        out = {}
        for ident, name in CURRENCIES.items():
            if str(ident) in currencies:
                out[name] = currencies[str(ident)]
        for ident, c in COUNTERS.items():
            value = counters.get(str(ident))
            if value is not None:
                out[c["name"]] = value[0] if isinstance(value, list) else value
        energy = counters.get(str(ENERGY))
        if energy is not None:
            out["Odyle energy"] = energy[0] if isinstance(energy, list) else energy
        return out

    def note_history(self, entry, kept, sheet):
        """A reading -> the day's values, and a change line for each value that moved since the last reading."""
        new = self.tracked({str(k): v for k, v in sheet["counters"].items()} or kept.get("counters", {}),
                           {str(k): v for k, v in sheet["currencies"].items()} or kept.get("currencies", {}))
        old = self.tracked(kept.get("counters", {}), kept.get("currencies", {}))
        history = entry.setdefault("history", dict(days={}, changes=[]))
        stamp = time.localtime(sheet["time"])
        day = history["days"].setdefault(time.strftime("%Y-%m-%d", stamp), {})
        day.update(new)
        for name, value in new.items():
            if name in old and old[name] != value and name != "Odyle energy":  # energy ticks constantly
                history["changes"].append([time.strftime("%Y-%m-%d %H:%M", stamp), name, old[name], value])
        for date in sorted(history["days"])[:-HISTORY_DAYS]:
            del history["days"][date]
        del history["changes"][:-HISTORY_CHANGES]

    @staticmethod
    def checklist(entry, subscriber=False, now=None):
        """For the Checklist page: the last reading, grouped like the game's own lists, with what has refilled by
        itself since it was read (ticks: energy every 3 h, Nightmare and Shugo keys at the daily reset)."""
        now = time.time() if now is None else now
        sheet = entry.get("sheet")
        cls = CLASSES.get(entry.get("cls"), "Class %s" % entry["cls"] if entry.get("cls") else None)
        history = entry.get("history") or {}
        days = history.get("days", {})
        out = dict(cls=cls, icon=CLASS_ICONS.get(cls), level=entry.get("level"), read=None, read_at=None, energy=None,
                   stock=[], daily=[], weekly=[], currencies=[],
                   history=dict(days=[[d, days[d]] for d in sorted(days)], changes=history.get("changes", [])[-60:][::-1]))
        if not sheet:
            return out
        counters, money = sheet.get("counters", {}), sheet.get("currencies", {})
        out.update(read=sheet["read"], read_at=sheet.get("read_at"))
        read_at = sheet.get("read_at")
        daily_ticks = ticks_between(read_at, now, 24, RESET_HOUR) if read_at else 0
        energy = counters.get(str(ENERGY))
        out["energy"] = energy if isinstance(energy, list) else ([energy, 0] if energy is not None else None)
        if out["energy"] and read_at:
            gain = ticks_between(read_at, now, ENERGY_EVERY_H) * ENERGY_STEP
            if gain and out["energy"][0] < ENERGY_MAX:
                full = min(ENERGY_MAX, out["energy"][0] + gain)
                out["energy_proj"] = full - out["energy"][0]
                out["energy"] = [full] + list(out["energy"][1:])
        for ident, c in COUNTERS.items():
            if str(ident) not in counters:
                continue
            value = counters[str(ident)]
            left, bonus = (value[0], value[1]) if isinstance(value, list) else (value, 0)
            if c.get("bonus") and str(c["bonus"]) in counters:  # a separate bonus counter (Unknown Fissure's)
                extra = counters[str(c["bonus"])]
                bonus = extra[1] if isinstance(extra, list) else extra
                if isinstance(extra, list):  # its left count is in there too: [14, 30] = "14 (+30) / 14" (2026-10-22);
                    left = extra[0]          # the main counter (6) always reads 0 in the login message
            gain = daily_ticks * (DAILY_GAIN.get(ident, 0) or ((3 if subscriber else 2) if ident == SHUGO_KEYS else 0))
            proj = 0
            if gain and c["max"] and left < c["max"]:
                proj, left = min(c["max"], left + gain) - left, min(c["max"], left + gain)
            out[c["group"]].append(dict(name=c["name"], left=left, bonus=bonus, max=c["max"], scope=c["scope"],
                                        unit=c.get("unit"), note=c.get("note"), proj=proj))
        amounts = [(name, money[str(i)], i) for i, name in CURRENCIES.items() if str(i) in money]
        kinah = [v for n, v, _ in amounts if n.startswith("Kinah")]
        out["currencies"] = ([("Kinah (total)", sum(kinah), 930100030)] if len(kinah) == 2 else []) + amounts
        return out

    @staticmethod
    def guess(entry):
        """The faction its maps point to, or None (none seen, or both sides' maps: Reshanta is shared)."""
        sides = {f for f, maps in FACTION_MAPS.items() if maps & set(entry.get("maps", ()))}
        return sides.pop() if len(sides) == 1 else None

    def faction_of(self, name):
        entry = self.data.get(name)
        return entry and (entry.get("faction") or self.guess(entry))

    def set_faction(self, name, faction):
        if name in self.data:
            self.data[name]["faction"] = faction if faction in FACTIONS else None
            self.save()

    def forget(self, name):
        if self.data.pop(name, None) is not None:
            self.save()

    def listing(self, subscriber=False):
        """For the page: the one playing first, then the most recently played."""
        rows = [dict({k: v for k, v in e.items() if k != "sheet"}, name=name, playing=name == self.playing,
                     guess=self.guess(e), checklist=self.checklist(e, subscriber)) for name, e in self.data.items()]
        rows.sort(key=lambda r: r.get("last") or "", reverse=True)
        rows.sort(key=lambda r: not r["playing"])  # stable: the playing one first, the rest still newest first
        return rows
