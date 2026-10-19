"""The characters seen logging in, with the faction you set for each (Settings → Characters). Keyed by name: the
login message's 4-byte "character number" turned out not to be stable (one character showed 15 of them)."""
import time

from .common import load_settings, save_settings
from .game import CLASSES, CLASS_ICONS, COUNTERS, CURRENCIES, ENERGY

FACTIONS = ("Elyos", "Asmodian")
FACTION_MAPS = {"Elyos": {"poeta", "verteron", "eltnen"}, "Asmodian": {"ishalgen", "altgard", "morheim"}}
HOME_MAP = {"Elyos": "verteron", "Asmodian": "altgard"}  # each side's land where pet souls are farmed
SHARED_MAP = "reshanta-lower"  # both sides farm here


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
        entry["sheet"] = dict(counters=dict(kept.get("counters", {}), **{str(k): v for k, v in sheet["counters"].items()}),
                              currencies=dict(kept.get("currencies", {}), **{str(k): v for k, v in sheet["currencies"].items()}),
                              read=time.strftime("%Y-%m-%d %H:%M", time.localtime(sheet["time"])),
                              read_at=sheet["time"])
        self.save()

    @staticmethod
    def checklist(entry):
        """For the Checklist page: the last reading, grouped like the game's own lists."""
        sheet = entry.get("sheet")
        cls = CLASSES.get(entry.get("cls"), "Class %s" % entry["cls"] if entry.get("cls") else None)
        out = dict(cls=cls, icon=CLASS_ICONS.get(cls), level=entry.get("level"), read=None, read_at=None, energy=None,
                   stock=[], daily=[], weekly=[], currencies=[])
        if not sheet:
            return out
        counters, money = sheet.get("counters", {}), sheet.get("currencies", {})
        out.update(read=sheet["read"], read_at=sheet.get("read_at"))
        energy = counters.get(str(ENERGY))
        out["energy"] = energy if isinstance(energy, list) else ([energy, 0] if energy is not None else None)
        for ident, c in COUNTERS.items():
            if str(ident) not in counters:
                continue
            value = counters[str(ident)]
            left, bonus = (value[0], value[1]) if isinstance(value, list) else (value, 0)
            if c.get("bonus") and str(c["bonus"]) in counters:
                bonus = counters[str(c["bonus"])]
            out[c["group"]].append(dict(name=c["name"], left=left, bonus=bonus, max=c["max"], scope=c["scope"],
                                        unit=c.get("unit"), note=c.get("note")))
        amounts = [(name, money[str(i)]) for i, name in CURRENCIES.items() if str(i) in money]
        kinah = [v for n, v in amounts if n.startswith("Kinah")]
        out["currencies"] = ([("Kinah (total)", sum(kinah))] if len(kinah) == 2 else []) + amounts
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

    def listing(self):
        """For the page: the one playing first, then the most recently played."""
        rows = [dict({k: v for k, v in e.items() if k != "sheet"}, name=name, playing=name == self.playing,
                     guess=self.guess(e), checklist=self.checklist(e)) for name, e in self.data.items()]
        rows.sort(key=lambda r: r.get("last") or "", reverse=True)
        rows.sort(key=lambda r: not r["playing"])  # stable: the playing one first, the rest still newest first
        return rows
