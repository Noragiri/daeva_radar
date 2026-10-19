"""The characters seen logging in, with the faction you set for each (Settings → Characters). Keyed by name: the
login message's 4-byte "character number" turned out not to be stable (one character showed 15 of them)."""
import time

from .common import load_settings, save_settings

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

    def save(self):
        save_settings(dict(load_settings(), characters=self.data))
        self.saved_at = time.time()

    def note(self, snap):
        """Every tick: the character playing (from the login message) and the map it's detected on."""
        name = snap["own_name"]
        if not name:
            return
        entry = self.data.setdefault(name, dict(faction=None, first=time.strftime("%Y-%m-%d"), last=None, maps=[]))
        changed = name != self.playing
        self.playing = name
        entry["last"] = time.strftime("%Y-%m-%d %H:%M")
        where = self.app.detected  # detected live, not picked by hand (that could be wrong)
        if where and where not in entry["maps"]:
            entry["maps"].append(where)
            changed = True
        if changed or time.time() - self.saved_at > 300:
            self.save()

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
        rows = [dict(name=name, playing=name == self.playing, guess=self.guess(e), **e) for name, e in self.data.items()]
        rows.sort(key=lambda r: r.get("last") or "", reverse=True)
        rows.sort(key=lambda r: not r["playing"])  # stable: the playing one first, the rest still newest first
        return rows
