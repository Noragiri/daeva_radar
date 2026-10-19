"""Shop: the rotating shop's offers (3 tabs, new every hour at x:00) with names, discounts and whether you own each
item, and a pop-up for the discounted items you care about."""
import json
import re
import time

from ..common import ROOT, load_settings, save_settings
from ..sound import play
from ..ui import MUTED, Toast
from ..feature import Feature
from .. import page as P
from .pets import MAX_LEVEL, SOUL_CAP
from ..characters import HOME_MAP, SHARED_MAP
from ..game import map_name


SHOP_DEFAULT = dict(min_discount=30, missing_only=True, targets=True, unfarmable=True, sticky=True, kinds=["pet", "skin", "theostone"],
                    remind=False, sound=True)
TAB_ORDER = ("Special", "Pets", "Skins")
KIND_LABEL = {"pet": "Pet", "skin": "Skin", "theostone": "Theostone", "other": "Item"}
# a skin voucher's piece -> the wardrobe's piece name (data/skins.json)
PIECE_ALIAS = {"Breastplate": "Top", "Greaves": "Legs", "Boots": "Shoes", "Helmet": "Helm"}
DEAL = "#7cf08a"


class ShopFeature(Feature):
    """The rotating shop, one per character (each has its own offers). Opening it in game sends each tab's offers
    ('91 56': item ids, prices, discount); they're kept per character (from the login message, else "this
    character" until the next login);
    names come from data/shop_items.json (dev/fetch_shop_items.py, credit Shugo.GG). Pets are checked against
    your pet collection (Monsters → Pets), skins against your wardrobe. A pop-up lists the new offers that pass
    the Settings rules, once per shop hour."""
    title, icon, TABS = "Shop", "⚖", ("Offers", "Settings")
    off_text = "Offers aren't read and nothing pops up while it's off."

    def __init__(self, app):
        settings = load_settings()
        self.cfg = dict(SHOP_DEFAULT, **settings.get("shop", {}))
        saved = settings.get("shop_offers", {})
        # character name -> dict(name, tabs={tab: dict(items, time)}); "?" until a login is seen
        self.shops = self.by_name(saved.get("characters", {}))
        self.char = None  # the character playing now (its name)
        self.viewed = None  # the character whose shop the Offers tab shows (None: the one playing)
        try:
            raw = json.loads((ROOT / "data" / "shop_items.json").read_text(encoding="utf-8"))["items"]
            self.names = {int(k): v for k, v in raw.items()}
        except (OSError, ValueError, KeyError):
            self.names = {}
        self.seen = None
        self.announced = set(tuple(x) for x in saved.get("announced", []))  # (character, hour, tab, item)
        self.reminded = None
        self.rows = []
        super().__init__(app)

    @staticmethod
    def by_name(shops):
        """Shops were kept per login "character number", which isn't stable: merge them per name, newest tab wins."""
        out = {}
        for key, entry in shops.items():
            name = entry.get("name") or key
            merged = out.setdefault(name, dict(name=entry.get("name"), tabs={}))
            for tab, data in entry.get("tabs", {}).items():
                if data["time"] > merged["tabs"].get(tab, {}).get("time", 0):
                    merged["tabs"][tab] = data
        return out

    # ---- items
    def info(self, item):
        return self.names.get(item, dict(name="Item %d" % item, kind="other", grade=""))

    def pet_of(self, item):
        """-> (Pets helper, pet id) for a pet item, else (None, None)."""
        info = self.info(item)
        monsters = self.app.features.get("Monsters")
        pets = monsters.pets if monsters else None
        if info["kind"] != "pet" or not pets:
            return None, None
        name = info["name"].replace("Pet: ", "")
        return pets, next((i for i, p in pets.data.pets.items() if p["name"] == name), None)

    def faction(self, char):
        """The character's faction (Settings → Characters: set, else guessed from its maps), or None."""
        return self.app.characters.faction_of(char) if char and char != "?" else None

    def farm(self, item, char):
        """Can this character farm the pet's souls? -> None (not a pet we know), "no" (in none of the farm maps:
        dungeons, other regions), "other" (only in the other faction's land) or "yes"."""
        pets, pid = self.pet_of(item)
        if pid is None:
            return None
        maps = pets.data.farm_maps(pid)
        if not maps:
            return "no"
        faction = self.faction(char)
        if faction and not maps & {HOME_MAP[faction], SHARED_MAP}:
            return "other"
        return "yes"

    def farm_note(self, item, char):
        """"⊘ not farmable · 28/105" / "⇄ only in Altgard · 28/105" (souls toward Lv3) for the offer list and the
        pop-up, else ""."""
        farm = self.farm(item, char)
        if farm not in ("no", "other"):
            return ""
        pets, pid = self.pet_of(item)
        done = pets.pet(pid)[3]
        if farm == "no":
            note = "⊘ not farmable"
        else:
            note = "⇄ only in %s" % map_name(next(m for f, m in HOME_MAP.items() if f != self.faction(char)))
        return "%s · %s" % (note, "Lv%d ✓" % MAX_LEVEL if done >= SOUL_CAP else "%d/%d" % (done, SOUL_CAP))

    def unfarmable_open(self, item, char):
        """A pet this character can't farm, not at Lv3 yet: the shop is how it levels."""
        pets, pid = self.pet_of(item)
        return self.farm(item, char) in ("no", "other") and pets.levels.get(pid, 0) < MAX_LEVEL

    def status_of(self, item):
        """-> "owned" | "missing" | "target" | None (can't tell: theostones, accessories without names)."""
        info = self.info(item)
        if info["kind"] == "pet":
            pets, pid = self.pet_of(item)
            if pid is None:
                return None
            if pets.levels.get(pid):
                return "owned"
            return "target" if pid in pets.cfg["targets"] else "missing"
        if info["kind"] == "skin":
            wardrobe = self.app.features.get("Wardrobe")
            m = re.match(r"(.*) \(Skin: ([^)]+)\)", info["name"])
            if not wardrobe or not m:
                return None
            key = re.sub(r"[^a-z0-9]", "", m.group(1).lower())  # "Dawnfrost" in the shop is "Dawn Frost" in the wardrobe
            piece = PIECE_ALIAS.get(m.group(2), m.group(2))
            for set_id, s in wardrobe.data.sets.items():
                if re.sub(r"[^a-z0-9]", "", s["name"].lower()) == key:
                    num = next((int(n) for n, label in s["pieces"].items() if label == piece), None)
                    if num is not None:
                        return "owned" if set_id * 100 + num in wardrobe.skins else "missing"
            return None
        return None

    def wanted(self, offer, char=None):
        """Does this offer (in char's shop: the one playing by default) pass the notification rules?"""
        info, status = self.info(offer["item"]), self.status_of(offer["item"])
        char = char or self.char
        pets, pid = self.pet_of(offer["item"])
        if pid is not None and pets.levels.get(pid, 0) >= MAX_LEVEL:
            return False  # maxed: nothing left to buy it for
        if status == "target" and self.cfg["targets"] or self.cfg["unfarmable"] and self.unfarmable_open(offer["item"], char):
            return True
        if info["kind"] not in self.cfg["kinds"] or offer["discount"] < self.cfg["min_discount"]:
            return False
        return not self.cfg["missing_only"] or status in ("missing", "target") or \
            (status is None and info["kind"] == "theostone")

    @staticmethod
    def hour(when):
        return int(when // 3600)

    def stale(self, tab):
        """Seen before the last x:00 refresh: the shop has new offers since."""
        return tab not in self.offers or self.hour(self.offers[tab]["time"]) < self.hour(time.time())

    @property
    def offers(self):
        """tab -> dict(items, time) of the character shown."""
        return self.shops.get(self.viewed or self.char or "?", {}).get("tabs", {})

    def name_of(self, key):
        return self.shops.get(key, {}).get("name") or "this character"

    def who(self, snap):
        """The character playing: its name from the login message, else "?" until a login is seen."""
        if snap["own_name"]:
            key = snap["own_name"]
            entry = self.shops.setdefault(key, dict(name=key, tabs={}))
            if self.char != key and "?" in self.shops and key != "?":  # what was read before the login was seen
                unknown = self.shops.pop("?")
                for tab, data in unknown["tabs"].items():
                    if data["time"] > entry["tabs"].get(tab, {}).get("time", 0):
                        entry["tabs"][tab] = data
            return key
        return self.char or "?"

    # ---- new offers
    def sync(self, snap):
        char = self.who(snap)
        if char != self.char:
            self.char = char
        if snap["shop_count"] == self.seen:
            return
        first = self.seen is None
        self.seen = snap["shop_count"]
        fresh = []
        tabs = self.shops.setdefault(char, dict(name=None, tabs={}))["tabs"]
        for tab, data in snap["shop"].items():
            if any(s["tabs"].get(tab, {}).get("time") == data["time"] for s in self.shops.values()):
                continue  # already kept (the tracker's last list of each tab)
            tabs[tab] = dict(items=data["items"], time=data["time"])
            for offer in data["items"]:
                key = (char, self.hour(data["time"]), tab, offer["item"])
                if key not in self.announced and self.wanted(offer, char):
                    self.announced.add(key)
                    fresh.append((tab, offer))
        now = self.hour(time.time())
        self.announced = {k for k in self.announced if k[1] >= now - 1}
        save_settings(dict(load_settings(), shop_offers=dict(characters=self.shops, announced=sorted(self.announced))))
        if fresh and not first:
            self.notify(fresh)

    def notify(self, fresh):
        lines = ["-%d%% %s %s%s" % (o["discount"], self.info(o["item"])["name"], "{:,}".format(o["price"]),
                                    " (%s)" % self.farm_note(o["item"], self.char)[2:] if self.farm_note(o["item"], self.char) else "")
                 for tab, o in sorted(fresh, key=lambda f: -f[1]["discount"])]
        self.app.engine.say("Shop deals: " + "; ".join(lines))
        Toast(self.root, "%s's shop: %d deal%s\n%s" % (self.name_of(self.char), len(lines), "" if len(lines) == 1 else "s",
                                                  "\n".join(lines[:5]) + ("\n…" if len(lines) > 5 else "")), color=DEAL, label="Shop deals",
              seconds=None if self.cfg["sticky"] else 12)  # until clicked: deals are easy to miss mid-fight
        if self.cfg["sound"]:
            play("system beep")

    def remind(self):
        """At x:00 (optional): the shop has new offers; they're read when you open it."""
        now = time.localtime()
        if self.cfg["remind"] and now.tm_min == 0 and self.reminded != self.hour(time.time()):
            self.reminded = self.hour(time.time())
            Toast(self.root, "The shop has new offers\nOpen it in game to check the deals", color=DEAL,
                  seconds=None if self.cfg["sticky"] else 6,
                  label="Shop")

    # ---- Offers tab
    def page_offers(self, snap):
        now = time.time()
        left = 3600 - now % 3600
        shown_char = self.viewed or self.char
        chars = [(key, self.name_of(key) + (" (playing)" if key == self.char else ""))
                 for key in sorted(self.shops, key=lambda k: (k != self.char, self.name_of(k)))]
        seen = [self.offers[t]["time"] for t in TAB_ORDER if t in self.offers]
        sub = ("Last opened %s%s" % (time.strftime("%H:%M", time.localtime(max(seen))),
                                     " · out of date: open the shop for the new offers"
                                     if any(self.stale(t) for t in TAB_ORDER if t in self.offers) else "")
               if seen else "Open the shop in game (all three tabs) to read the offers.")
        rows, shown = [], self.cfg.get("tab", "All")
        faction = self.faction(shown_char)
        for tab in TAB_ORDER:
            if tab not in self.offers or shown not in ("All", tab):
                continue
            rows.append(("— %s%s" % (tab, "  (out of date)" if self.stale(tab) else ""), MUTED))
            for o in sorted(self.offers[tab]["items"], key=lambda o: -o["discount"]):
                info, status = self.info(o["item"]), self.status_of(o["item"])
                mark = {"owned": "✓", "missing": "✗", "target": "★"}.get(status, " ")
                rows.append((" %s -%2d%%  %-40.40s %10s  %s%s" % (
                    mark, o["discount"], info["name"], "{:,}".format(o["price"]), info["grade"],
                    ("  " + self.farm_note(o["item"], shown_char)) if self.farm_note(o["item"], shown_char) else ""),
                    DEAL if self.wanted(o, shown_char) and not self.stale(tab) else (MUTED if status == "owned" else None)))
        return [P.heading("Shop offers"),
                P.row(P.text("%s's shop · new offers in %d:%02d" % (self.name_of(shown_char), left // 60, left % 60),
                             bold=True),
                      P.choice("character", chars, shown_char, style="select") if chars else None),
                P.text(sub + ("  ·  %s" % faction if faction else
                             "  ·  Set this character's faction in Characters to mark pets only farmable on the other side."),
                       small=True),
                P.choice("tab", ["All"] + list(TAB_ORDER), shown),
                P.listbox("offer", rows, None, height=24, dbl=False, empty="No offers read yet."),
                P.text("✓ owned · ✗ missing · ★ target pet · ⊘ pet not farmable in Verteron, Altgard or Lower Reshanta"
                       " · ⇄ pet only farmable in the other faction's land · green: passes your notification rules "
                       "(Settings)", small=True)]

    def on_character(self, key):
        if key in self.shops:
            self.viewed = None if key == self.char else key

    def on_tab(self, value):
        self.cfg["tab"] = value
        self.save()

    def on_offer(self, index):
        pass  # nothing to pick: the list is for reading

    def save(self):
        save_settings(dict(load_settings(), shop=self.cfg))

    # ---- Settings tab
    FLAGS = (("missing_only", "Only items I don't own (pets, skins)"),
             ("targets", "Always for my target pets (any discount)"),
             ("unfarmable", "Always for pets I can't farm until they're Lv3 (any discount; with the character's "
                            "faction set, also those only in the other side's land)"),
             ("sound", "Beep with the pop-up"),
             ("remind", "Remind me at x:00 that the shop has new offers"),
             ("sticky", "Keep shop pop-ups until I click them (else they fade after a few seconds)"))

    def page_settings(self, snap):
        return ([P.heading("Shop settings", "Item names: AION 2 data © NCSOFT (official AION 2 web API), via Shugo.GG. "
                           "Pets are checked against your pet collection, skins against your wardrobe."),
                 P.section("Pop up for offers with at least"),
                 P.choice("min_discount", [(v, "-%d%%" % v) for v in (10, 20, 30, 50)], self.cfg["min_discount"]),
                 P.section("Which items")] +
                [P.toggle("kind_" + kind, KIND_LABEL[kind] + "s", kind in self.cfg["kinds"])
                 for kind in ("pet", "skin", "theostone")] +
                [P.section("Rules")] +
                [P.toggle("flag_" + key, text, self.cfg[key]) for key, text in self.FLAGS] +
                [P.text("The game sends the offers when you open the shop; each item pops up once per shop hour.",
                        small=True)])

    def on_min_discount(self, value):
        self.cfg["min_discount"] = int(value)
        self.save()

    def act(self, id, value):
        if id.startswith("kind_"):
            kinds = [k for k in self.cfg["kinds"] if k != id[5:]]
            self.cfg["kinds"] = kinds + [id[5:]] if value else kinds
            return self.save()
        return super().act(id, value)

    # ---- loop hooks
    def background(self, snap):
        self.sync(snap)
        self.remind()
        super().background(snap)

    def status(self, snap):
        tabs = self.shops.get(self.char or "?", {}).get("tabs", {})
        deals = sum(1 for t in TAB_ORDER if t in tabs and tabs[t]["time"] // 3600 == time.time() // 3600
                    for o in tabs[t]["items"] if self.wanted(o))
        return "%d shop deal%s" % (deals, "" if deals == 1 else "s") if deals else None
