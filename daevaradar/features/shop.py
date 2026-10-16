"""Shop: the rotating shop's offers (3 tabs, new every hour at x:00) with names, discounts and whether you own each
item, and a pop-up for the discounted items you care about."""
import json
import re
import time

from ..common import ROOT, load_settings, save_settings
from ..sound import play
from ..ui import BG, BUTTON, BUTTON_HOVER, FONT, MONO, MUTED, PANEL, TEXT, Toast, checkbox, heading, section
from ..feature import Feature


SHOP_DEFAULT = dict(min_discount=30, missing_only=True, targets=True, kinds=["pet", "skin", "theostone"],
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

    def __init__(self, app, parent):
        settings = load_settings()
        self.cfg = dict(SHOP_DEFAULT, **settings.get("shop", {}))
        saved = settings.get("shop_offers", {})
        self.shops = saved.get("characters", {})  # character key -> dict(name, tabs={tab: dict(items, time)})
        self.char = None  # the character playing now (key)
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
        super().__init__(app, parent)

    # ---- items
    def info(self, item):
        return self.names.get(item, dict(name="Item %d" % item, kind="other", grade=""))

    def status_of(self, item):
        """-> "owned" | "missing" | "target" | None (can't tell: theostones, accessories without names)."""
        info = self.info(item)
        if info["kind"] == "pet":
            monsters = self.app.features.get("Monsters")
            pets = monsters.pets if monsters else None
            name = info["name"].replace("Pet: ", "")
            pid = next((i for i, p in pets.data.pets.items() if p["name"] == name), None) if pets else None
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

    def wanted(self, offer):
        """Does this offer pass the notification rules?"""
        info, status = self.info(offer["item"]), self.status_of(offer["item"])
        if status == "target" and self.cfg["targets"]:
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
        """The character playing: its permanent number from the login message, else "?" until a login is seen."""
        if snap["own_char_no"]:
            key = str(snap["own_char_no"])
            entry = self.shops.setdefault(key, dict(name=None, tabs={}))
            entry["name"] = snap["own_name"] or entry["name"]
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
            self.refresh_characters()
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
                if key not in self.announced and self.wanted(offer):
                    self.announced.add(key)
                    fresh.append((tab, offer))
        now = self.hour(time.time())
        self.announced = {k for k in self.announced if k[1] >= now - 1}
        save_settings(dict(load_settings(), shop_offers=dict(characters=self.shops, announced=sorted(self.announced))))
        if fresh and not first:
            self.notify(fresh)
        self.refresh_characters()
        self.refresh()

    def notify(self, fresh):
        lines = ["-%d%% %s %s" % (o["discount"], self.info(o["item"])["name"], "{:,}".format(o["price"]))
                 for tab, o in sorted(fresh, key=lambda f: -f[1]["discount"])]
        self.app.engine.say("Shop deals: " + "; ".join(lines))
        Toast(self.root, "⚖ %s's shop: %d deal%s\n%s" % (self.name_of(self.char), len(lines), "" if len(lines) == 1 else "s",
                                                  "\n".join(lines[:5]) + ("\n…" if len(lines) > 5 else "")), color=DEAL)
        if self.cfg["sound"]:
            play("system beep")

    def remind(self):
        """At x:00 (optional): the shop has new offers; they're read when you open it."""
        now = time.localtime()
        if self.cfg["remind"] and now.tm_min == 0 and self.reminded != self.hour(time.time()):
            self.reminded = self.hour(time.time())
            Toast(self.root, "⚖ The shop has new offers\nOpen it to check the deals", color=DEAL, seconds=6)

    # ---- Offers tab
    def build_offers(self, f):
        tk = self.tk
        heading(tk, f, "Shop offers", None)
        row = tk.Frame(f, bg=BG)
        row.pack(fill="x")
        self.head = tk.Label(row, text="", bg=BG, fg=TEXT, font=(FONT, 10, "bold"), anchor="w", justify="left")
        self.head.pack(side="left", fill="x")
        self.char_var = tk.StringVar(value="")
        self.char_menu = tk.OptionMenu(row, self.char_var, "")
        self.char_menu.config(bg=BUTTON, fg=TEXT, activebackground=BUTTON_HOVER, activeforeground=TEXT, relief="flat",
                              bd=0, highlightthickness=0, font=(FONT, 9), cursor="hand2")
        self.char_menu["menu"].config(bg=PANEL, fg=TEXT, activebackground=BUTTON_HOVER, activeforeground=TEXT)
        self.char_menu.pack(side="right")
        self.sub = tk.Label(f, text="", bg=BG, fg=MUTED, font=(FONT, 9), anchor="w", justify="left")
        self.sub.pack(fill="x", pady=(0, 6))
        row = tk.Frame(f, bg=BG)
        row.pack(fill="x", pady=(0, 4))
        self.tab_var = tk.StringVar(value=self.cfg.get("tab", "All"))
        for value in ("All",) + TAB_ORDER:  # one shop tab at a time fits without scrolling
            tk.Radiobutton(row, text=value, value=value, variable=self.tab_var, command=self.set_tab, bg=BG, fg=TEXT,
                           selectcolor=BUTTON, activebackground=BG, activeforeground=TEXT, font=(FONT, 9),
                           indicatoron=False, padx=8, pady=2, relief="flat", bd=0, offrelief="flat",
                           cursor="hand2").pack(side="left", padx=(0, 2))
        self.listbox = tk.Listbox(f, height=24, bg=PANEL, fg=TEXT, selectbackground=BUTTON_HOVER, relief="flat",
                                  highlightthickness=0, font=(MONO, 9), activestyle="none")
        self.listbox.pack(fill="x")
        tk.Label(f, text="✓ owned · ✗ missing · ★ target pet · green: passes your notification rules (Settings)",
                 bg=BG, fg=MUTED, font=(FONT, 9), anchor="w").pack(fill="x", pady=(6, 0))
        self.refresh()

    def refresh_characters(self):
        """The character picker: whose shop to show (the one playing first)."""
        if not hasattr(self, "char_menu"):
            return
        menu = self.char_menu["menu"]
        menu.delete(0, "end")
        for key in sorted(self.shops, key=lambda k: (k != self.char, self.name_of(k))):
            label = self.name_of(key) + (" (playing)" if key == self.char else "")
            menu.add_command(label=label, command=lambda k=key: self.view(k))
        shown = self.viewed or self.char
        self.char_var.set(self.name_of(shown) + (" (playing)" if shown == self.char else "") if shown else "")

    def view(self, key):
        self.viewed = None if key == self.char else key
        self.refresh_characters()
        self.refresh()

    def refresh(self):
        if not hasattr(self, "listbox"):
            return
        now = time.time()
        left = 3600 - now % 3600
        self.head.config(text="%s's shop · new offers in %d:%02d" % (
            self.name_of(self.viewed or self.char), left // 60, left % 60))
        seen = [self.offers[t]["time"] for t in TAB_ORDER if t in self.offers]
        self.sub.config(text=("Last opened %s%s" % (time.strftime("%H:%M", time.localtime(max(seen))),
                                                    " · out of date: open the shop for the new offers"
                                                    if any(self.stale(t) for t in TAB_ORDER if t in self.offers) else ""))
                        if seen else "Open the shop in game (all three tabs) to read the offers.")
        self.listbox.delete(0, "end")
        shown = self.tab_var.get()
        for tab in TAB_ORDER:
            if tab not in self.offers or shown not in ("All", tab):
                continue
            self.listbox.insert("end", "— %s%s" % (tab, "  (out of date)" if self.stale(tab) else ""))
            self.listbox.itemconfig("end", fg=MUTED)
            for o in sorted(self.offers[tab]["items"], key=lambda o: -o["discount"]):
                info, status = self.info(o["item"]), self.status_of(o["item"])
                mark = {"owned": "✓", "missing": "✗", "target": "★"}.get(status, " ")
                self.listbox.insert("end", " %s -%2d%%  %-40.40s %10s  %s" % (
                    mark, o["discount"], info["name"], "{:,}".format(o["price"]), info["grade"]))
                self.listbox.itemconfig("end", fg=DEAL if self.wanted(o) and not self.stale(tab) else
                                        (MUTED if status == "owned" else TEXT))

    def set_tab(self):
        self.cfg["tab"] = self.tab_var.get()
        save_settings(dict(load_settings(), shop=self.cfg))
        self.refresh()

    # ---- Settings tab
    def build_settings(self, f):
        tk = self.tk
        heading(tk, f, "Shop settings", "Item names: Shugo.GG (data/shop_items.json). Pets are checked against your "
                "pet collection, skins against your wardrobe.")
        section(tk, f, "Pop up for offers with at least")
        row = tk.Frame(f, bg=BG)
        row.pack(fill="x")
        self.min_var = tk.IntVar(value=self.cfg["min_discount"])
        for value in (10, 20, 30, 50):
            tk.Radiobutton(row, text="-%d%%" % value, value=value, variable=self.min_var, command=self.set_flags, bg=BG,
                           fg=TEXT, selectcolor=BUTTON, activebackground=BG, activeforeground=TEXT,
                           font=(FONT, 10)).pack(side="left", padx=(0, 8))
        section(tk, f, "Which items")
        self.kind_vars = {}
        row = tk.Frame(f, bg=BG)
        row.pack(fill="x")
        for kind in ("pet", "skin", "theostone"):
            self.kind_vars[kind] = tk.BooleanVar(value=kind in self.cfg["kinds"])
            checkbox(tk, row, KIND_LABEL[kind] + "s", self.kind_vars[kind], self.set_flags).pack(side="left", padx=(0, 8))
        self.flags = {}
        for key, text in (("missing_only", "Only items I don't own (pets, skins)"),
                          ("targets", "Always for my target pets (any discount)"),
                          ("sound", "Beep with the pop-up"),
                          ("remind", "Remind me at x:00 that the shop has new offers")):
            self.flags[key] = tk.BooleanVar(value=self.cfg[key])
            checkbox(tk, f, text, self.flags[key], self.set_flags).pack(fill="x")
        tk.Label(f, text="The game sends the offers when you open the shop; each item pops up once per shop hour.",
                 bg=BG, fg=MUTED, font=(FONT, 9), anchor="w", justify="left", wraplength=390).pack(fill="x", pady=(8, 0))

    def set_flags(self):
        self.cfg.update({k: v.get() for k, v in self.flags.items()}, min_discount=self.min_var.get(),
                        kinds=[k for k, v in self.kind_vars.items() if v.get()])
        save_settings(dict(load_settings(), shop=self.cfg))
        self.refresh()

    # ---- loop hooks
    def background(self, snap):
        self.sync(snap)
        self.remind()
        super().background(snap)

    def update(self, snap, tab):
        if tab == "Offers" and int(time.time()) % 5 == 0:
            self.refresh()  # the countdown
        super().update(snap, tab)

    def status(self, snap):
        tabs = self.shops.get(self.char or "?", {}).get("tabs", {})
        deals = sum(1 for t in TAB_ORDER if t in tabs and tabs[t]["time"] // 3600 == time.time() // 3600
                    for o in tabs[t]["items"] if self.wanted(o))
        return "%d shop deal%s" % (deals, "" if deals == 1 else "s") if deals else None
