"""Pets, part of the Monsters feature: your collection's progress to Lv3 (live from the game), soul-drop pop-ups,
and which monsters drop the souls of your target pets and where they spawn (Monsters radar and overlay, Map window)."""
import json
import math
import time

from ..common import ROOT, load_settings, save_settings
from ..game import SOUL_STAGES, MapData, map_name
from ..sound import play
from ..ui import BG, BUTTON, BUTTON_HOVER, FONT, MONO, MUTED, PANEL, TEXT, LiveToast, Toast, WATCH_COLORS, button, checkbox, dim, draw_text, label_side, radar_point, relative, section


PET_DEFAULT = dict(targets=[], notify="all", sound=True, spawns=True, live=True, filter="open")
MAX_LEVEL = len(SOUL_STAGES)
SOUL_CAP = sum(SOUL_STAGES)
PET_COLORS = WATCH_COLORS[::-1]  # target pets, in the order picked (reversed: watched monsters start at the front)


class PetData:
    """data/pets.json (pets, genus, which monsters drop each soul) and data/pet_spawns/<map>.json (where those
    monsters spawn, map px, credit Shugo.GG), both written by dev/fetch_pet_data.py."""

    def __init__(self, folder=None):
        folder = folder or ROOT / "data"
        try:
            raw = json.loads((folder / "pets.json").read_text(encoding="utf-8"))["pets"]
        except (OSError, ValueError, KeyError):
            raw = {}
        self.pets = {int(k): v for k, v in raw.items()}
        self.collection = sorted((i for i, p in self.pets.items() if p["genus"] != "Special"),
                                 key=lambda i: self.pets[i]["name"])
        self.spawns, self.places = {}, {}  # map -> {type: {name, level, points}} / [(name, kind, x, y)]
        for path in sorted((folder / "pet_spawns").glob("*.json")) if (folder / "pet_spawns").exists() else []:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if data.get("monsters"):
                self.spawns[data["map"]] = {int(k): v for k, v in data["monsters"].items()}
                self.places[data["map"]] = data.get("places", [])

    def keys(self):
        return sorted(self.spawns, key=map_name)

    def types_for(self, pets):
        """Monster type id -> the (first) of these pets whose soul it drops."""
        return {t: pid for pid in pets for s in self.pets.get(pid, {}).get("souls", ()) for t in s["types"]}

    def spawn_points(self, key, pets):
        """-> [(pet, type, x px, y px)] of the monsters that drop these pets' souls on map key."""
        out = []
        for t, pid in self.types_for(pets).items():
            for x, y in self.spawns.get(key, {}).get(t, {}).get("points", ()):
                out.append((pid, t, x, y))
        return out

    def which_map(self, types):
        """The map whose soul monsters these live monster types belong to, or None if unclear."""
        votes = {key: sum(1 for t in types if t in spawns) for key, spawns in self.spawns.items()}
        ranked = sorted(votes.items(), key=lambda kv: -kv[1])
        if not ranked or ranked[0][1] < 2:
            return None
        return ranked[0][0] if len(ranked) == 1 or ranked[0][1] >= 2 * ranked[1][1] + 1 else None


def progress(level, souls):
    """Souls toward Lv3 so far (out of SOUL_CAP)."""
    return SOUL_CAP if level >= MAX_LEVEL else sum(SOUL_STAGES[:level]) + souls


class Pets:
    """The pet collection inside the Monsters feature. The game sends the whole collection when you enter the world
    ('00 90') and every soul drop / level up after that ('0d 90', '14 90'): this keeps your levels and souls and
    pops up each drop with what's left to Lv3. Target pets put their soul monsters on the Monsters radar and
    overlay: hollow where they spawn (map data, credit Shugo.GG; which monster drops what: metabot.gg), solid
    when one is in range. The Map window (App.open_map) shows every pet's spawns."""

    def __init__(self, app):
        self.app, self.tk, self.root = app, app.tk, app.root
        settings = load_settings()
        self.cfg = dict(PET_DEFAULT, **settings.get("pets", {}))
        saved = settings.get("pet_collection", {})
        self.levels = {int(k): v for k, v in saved.get("levels", {}).items()}
        self.souls = {int(k): v for k, v in saved.get("souls", {}).items()}
        self.synced = saved.get("synced")  # "YYYY-MM-DD HH:MM" of the last full sync from the game
        self.data = PetData()
        self.sync_seen = self.events_seen = None
        self.detected = None
        self.list_ids = []
        self.selected = None
        self.last_beep = 0
        self.alerted = set()
        self.toast, self.burst = None, {}  # the soul-drop notification and the gains it shows
        self.version = 0  # counts collection / target changes, for the Map window

    # ---- state
    def save(self):
        save_settings(dict(load_settings(), pets=self.cfg))

    def save_collection(self):
        save_settings(dict(load_settings(), pet_collection=dict(levels=self.levels, souls=self.souls, synced=self.synced)))

    def pet(self, pid):
        """-> (level, souls in stage, souls needed for the next stage or 0, total toward Lv3)."""
        level = self.levels.get(pid, 0)
        souls = 0 if level >= MAX_LEVEL else self.souls.get(pid, 0)
        return level, souls, (0 if level >= MAX_LEVEL else SOUL_STAGES[level]), progress(level, souls)

    def name(self, pid):
        return self.data.pets.get(pid, {}).get("name", "Pet %d" % pid)

    def target_color(self, pid):
        targets = self.cfg["targets"]
        return PET_COLORS[targets.index(pid) % len(PET_COLORS)] if pid in targets else MUTED

    def toggle_target(self, pid):
        targets = self.cfg["targets"]
        if pid in targets:
            targets.remove(pid)
        else:
            targets.append(pid)
        self.save()
        self.refresh_list()

    def map_key(self):
        """The app's map when there's pet data for it, else the one the live soul monsters belong to."""
        key = self.app.map_key()
        return key if key in self.data.spawns else self.detected

    def sync(self, snap):
        """Take the game's full collection, then apply soul drops and level ups as they come."""
        if snap["soul_sync"] and snap["soul_sync_count"] != self.sync_seen:
            first = self.sync_seen is None and not self.levels
            self.sync_seen = snap["soul_sync_count"]
            sync = snap["soul_sync"]
            gained = [pid for pid in sync["levels"] if progress(sync["levels"][pid], sync["souls"].get(pid, 0)) >
                      progress(self.levels.get(pid, 0), self.souls.get(pid, 0))]
            self.levels, self.souls = dict(sync["levels"]), dict(sync["souls"])
            self.synced = time.strftime("%Y-%m-%d %H:%M")
            self.save_collection()
            self.app.engine.say("Pets: collection synced (%d pets)" % len(self.levels))
            if gained and not first and self.cfg["notify"] != "off":
                Toast(self.root, "❀ Pet collection synced\n%d pets progressed since last time" % len(gained),
                      color="#7cf08a")
            self.refresh_list()
        if self.events_seen is None or snap["soul_count"] < self.events_seen:
            self.events_seen = snap["soul_count"]  # first tick, or the tracker was replaced
            return
        changed = []
        for event in snap["soul_events"]:
            if event["n"] <= self.events_seen:
                continue
            for pid, value in event["pairs"]:
                level, souls = self.levels.get(pid, 0), self.souls.get(pid, 0)
                if event["kind"] == "gain":  # thresholds applied here; the level up / spent that follow confirm it
                    souls += value
                    while level < MAX_LEVEL and souls >= SOUL_STAGES[level]:
                        souls -= SOUL_STAGES[level]
                        level += 1
                    changed.append((pid, value, self.levels.get(pid, 0)))
                elif event["kind"] == "level" and value > level:
                    level = value
                else:
                    continue
                self.levels[pid], self.souls[pid] = level, (0 if level >= MAX_LEVEL else souls)
        self.events_seen = snap["soul_count"]
        if changed:
            self.save_collection()
            self.refresh_list()
            for pid, gained, before in changed:
                self.notify(pid, gained, before)

    def notify(self, pid, gained, before):
        name = self.name(pid)
        level, souls, need, total = self.pet(pid)
        what = "unlocked" if before == 0 and level > 0 else ("Lv%d!" % level) if level > before else "+%d" % gained
        if level >= MAX_LEVEL:
            line = "Lv%d reached: maxed" % MAX_LEVEL
        else:
            line = "Lv%d · %d/%d to Lv%d · %d/%d to Lv%d (%d left)" % (
                level, souls, need, level + 1, total, SOUL_CAP, MAX_LEVEL, SOUL_CAP - total)
        self.app.engine.say("Pets: %s %s (%s)" % (name, what, line))
        target = pid in self.cfg["targets"]
        if self.cfg["notify"] == "all" or (self.cfg["notify"] == "targets" and target):
            # one notification for a burst of drops: a line per pet, its gains added up while it's shown
            if self.toast is None or not self.toast.visible:
                self.toast, self.burst = LiveToast(self.root, "❀ Pet souls", color="#7cf08a", seconds=6), {}
            self.burst[pid] = self.burst.get(pid, 0) + gained
            if level > before or (before == 0 and level > 0):
                what = "unlocked" if before == 0 else "Lv%d!" % level
            else:
                what = "+%d" % self.burst[pid]
            self.toast.add(pid, "%s  %s   %s" % (name, what, line), color=self.target_color(pid) if target else TEXT)
            if self.cfg["sound"] and time.time() - self.last_beep > 1:
                self.last_beep = time.time()
                play("system beep")

    def summary(self):
        ids = self.data.collection
        owned = sum(1 for i in ids if self.levels.get(i))
        maxed = sum(1 for i in ids if self.levels.get(i, 0) >= MAX_LEVEL)
        souls = sum(self.pet(i)[3] for i in ids)
        return owned, maxed, souls, len(ids)

    # ---- the Pets tab
    def build(self, f):
        tk = self.tk
        top = tk.Frame(f, bg=BG)
        top.pack(fill="x")
        self.summary_label = tk.Label(top, text="", bg=BG, fg=TEXT, font=(FONT, 10, "bold"), anchor="w", justify="left")
        self.summary_label.pack(side="left", fill="x")
        button(tk, top, "Map ↗", lambda: self.app.open_map("Pets")).pack(side="right")
        self.genus_label = tk.Label(f, text="", bg=BG, fg=MUTED, font=(FONT, 9), anchor="w", justify="left")
        self.genus_label.pack(fill="x", pady=(0, 6))
        row = tk.Frame(f, bg=BG)
        row.pack(fill="x")
        self.search = tk.StringVar()
        tk.Entry(row, textvariable=self.search, bg=BUTTON, fg=TEXT, insertbackground=TEXT, relief="flat",
                 font=(FONT, 10), width=14).pack(side="left", ipady=3, padx=(0, 6))
        self.search.trace_add("write", lambda *a: self.refresh_list())
        self.filter_var = tk.StringVar(value=self.cfg["filter"])
        for value, text in (("open", "Not Lv3"), ("missing", "Missing"), ("souls", "Has souls"), ("targets", "Targets"),
                            ("all", "All")):
            tk.Radiobutton(row, text=text, value=value, variable=self.filter_var, command=self.set_filter, bg=BG,
                           fg=TEXT, selectcolor=BUTTON, activebackground=BG, activeforeground=TEXT, font=(FONT, 9),
                           indicatoron=False, padx=6, pady=2, relief="flat", bd=0, offrelief="flat",
                           cursor="hand2").pack(side="left", padx=(0, 2))
        self.listbox = tk.Listbox(f, height=13, bg=PANEL, fg=TEXT, selectbackground=BUTTON_HOVER, relief="flat",
                                  highlightthickness=0, font=(MONO, 9), activestyle="none")
        self.listbox.pack(fill="x", pady=(6, 0))
        self.listbox.bind("<<ListboxSelect>>", lambda e: self.select_row())
        self.listbox.bind("<Double-Button-1>", lambda e: self.selected and self.toggle_target(self.selected))
        row = tk.Frame(f, bg=BG)
        row.pack(fill="x", pady=(4, 0))
        button(tk, row, "★ Target / untarget", lambda: self.selected and self.toggle_target(self.selected)).pack(side="left")
        tk.Label(row, text="or double-click · targets' soul monsters show on the Radar and Overlay", bg=BG, fg=MUTED,
                 font=(FONT, 9)).pack(side="left", padx=8)
        self.detail = tk.Label(f, text="", bg=BG, fg=MUTED, font=(MONO, 9), anchor="nw", justify="left", height=6)
        self.detail.pack(fill="x", pady=(6, 0))
        self.refresh_list()

    def set_filter(self):
        self.cfg["filter"] = self.filter_var.get()
        self.save()
        self.refresh_list()

    def refresh_list(self):
        self.version += 1
        if not hasattr(self, "listbox"):
            return
        owned, maxed, souls, total = self.summary()
        self.summary_label.config(text="Collected %d/%d · Lv3 %d/%d · souls %d%%" % (
            owned, total, maxed, total, 100 * souls / max(1, total * SOUL_CAP)))
        by = {}
        for i in self.data.collection:
            g = by.setdefault(self.data.pets[i]["genus"], [0, 0])
            g[0] += bool(self.levels.get(i))
            g[1] += 1
        sync = ("synced %s" % self.synced) if self.synced else "not synced yet: enter the world with the radar running"
        self.genus_label.config(text="  ".join("%s %d/%d" % (g, o, t) for g, (o, t) in sorted(by.items())) + "  ·  " + sync)
        text, mode = self.search.get().strip().lower(), self.cfg["filter"]
        rows = []
        for pid in self.data.collection:
            p = self.data.pets[pid]
            level, souls, need, done = self.pet(pid)
            if text and text not in p["name"].lower():
                continue
            if (mode == "open" and level >= MAX_LEVEL or mode == "missing" and level or
                    mode == "souls" and (level >= MAX_LEVEL or not souls) or mode == "targets" and pid not in self.cfg["targets"]):
                continue
            rows.append((pid not in self.cfg["targets"], -bool(souls), SOUL_CAP - done, p["name"], pid))
        rows.sort()
        self.list_ids = [r[-1] for r in rows]
        self.listbox.delete(0, "end")
        for i, pid in enumerate(self.list_ids):
            level, souls, need, done = self.pet(pid)
            mark = "★" if pid in self.cfg["targets"] else " "
            stage = "Lv3 ✓" if level >= MAX_LEVEL else "%2d/%-2d" % (souls, need)
            self.listbox.insert("end", "%s %-26.26s Lv%d %s %s %3d/%d" % (
                mark, self.data.pets[pid]["name"], level, self.bar(level, souls), stage, done, SOUL_CAP))
            self.listbox.itemconfig(i, fg=self.target_color(pid) if pid in self.cfg["targets"] else
                                    (TEXT if souls or level else MUTED))
        if not self.list_ids:
            self.listbox.insert("end", "  Nothing matches.")
        self.show_detail()

    @staticmethod
    def bar(level, souls):
        """[#+.]: one mark per stage, done / souls in / empty."""
        return "[%s]" % "".join("#" if level > i else ("+" if level == i and souls else ".") for i in range(MAX_LEVEL))

    def select_row(self):
        sel = self.listbox.curselection()
        if sel and sel[0] < len(self.list_ids):
            self.selected = self.list_ids[sel[0]]
            self.show_detail()

    def show_detail(self):
        pid = self.selected
        if pid is None or pid not in self.data.pets:
            self.detail.config(text="Select a pet for where its souls drop.")
            return
        p = self.data.pets[pid]
        level, souls, need, done = self.pet(pid)
        lines = ["%s (%s) · %s" % (p["name"], p["genus"], "maxed" if level >= MAX_LEVEL else
                                    "%d souls to Lv%d" % (SOUL_CAP - done, MAX_LEVEL))]
        for s in sorted(p.get("souls", ()), key=lambda s: -s["chance"])[:4]:
            counts = {k: sum(len(self.data.spawns[k][t]["points"]) for t in s["types"] if t in self.data.spawns[k])
                      for k in self.data.keys()}
            where = ", ".join("%s ×%d" % (map_name(k), n) for k, n in counts.items() if n)
            lines.append("  %-26.26s L%-2d %5.1f%%  %s" % (s["monster"], s["level"], s["chance"], where or "no map spawn"))
        if not p.get("souls"):
            lines.append("  No monster drops this soul (chests, shops, quests).")
        self.detail.config(text="\n".join(lines))

    # ---- on the Monsters radar and overlay
    def soul_types(self):
        """Monster type id -> target pet whose soul it drops."""
        return self.data.types_for(self.cfg["targets"]) if self.cfg["live"] else {}

    def live(self, snap):
        """Monsters in range that drop a target pet's soul -> [(pet, monster)]."""
        types = self.soul_types()
        return [(types[m["type"]], m) for m in snap["monsters"] if m["type"] in types]

    def spots(self, snap, radius_m):
        """Spawn points of the target pets' soul monsters within radius of you -> [(pet, type, world point)]."""
        key, position = self.map_key(), snap["position"]
        if not self.cfg["spawns"] or not key or not position:
            return []
        cx, cy = MapData.to_px(position)
        r = radius_m / 2  # metres -> px
        return [(pid, t, MapData.to_world(x, y, position[2])) for pid, t, x, y in self.data.spawn_points(key, self.cfg["targets"])
                if abs(x - cx) <= r and abs(y - cy) <= r and math.hypot(x - cx, y - cy) <= r]

    def monster_name(self, type_id):
        for spawns in self.data.spawns.values():
            if type_id in spawns:
                return spawns[type_id]["name"]
        return "Monster %d" % type_id

    def draw_spots(self, c, snap, mid, scale, range_m):
        """Hollow spawn points on the radar -> list rows for the nearest spawn of each soul monster."""
        position, spots = snap["position"], self.spots(snap, range_m)
        nearest = {}
        for pid, t, xyz in spots:
            x, y, _ = radar_point(position, xyz, mid, scale, range_m)
            c.create_oval(x - 4, y - 4, x + 4, y + 4, outline=self.target_color(pid), width=2)
            d = relative(position, xyz)
            if t not in nearest or d[0] < nearest[t][1][0]:
                nearest[t] = (pid, d)
        return [("○ %-24.24s %4d m %-2s → %s" % (self.monster_name(t), dist, direction, self.name(pid)),
                 dim(self.target_color(pid), 0.25))
                for t, (pid, (dist, dz, direction)) in sorted(nearest.items(), key=lambda kv: kv[1][1][0])]

    def paint_spots(self, overlay, c, snap):
        w, h = overlay.cfg["w"], overlay.cfg["h"]
        for pid, t, xyz in self.spots(snap, math.hypot(w, h) / 2 / overlay.cfg["ppm"]):
            x, y, edge = overlay.to_screen(snap["position"], xyz)
            if not edge:
                c.create_oval(x - 6, y - 6, x + 6, y + 6, outline="#000000", width=4)
                c.create_oval(x - 4, y - 4, x + 4, y + 4, outline=self.target_color(pid), width=2)

    def paint_live(self, overlay, c, snap):
        w = overlay.cfg["w"]
        for pid, m in self.live(snap):
            x, y, edge = overlay.to_screen(snap["position"], m["xyz"])
            color = self.target_color(pid)
            c.create_oval(x - 7, y - 7, x + 7, y + 7, fill="#000000" if not edge else "", outline="#000000", width=2)
            c.create_oval(x - 5, y - 5, x + 5, y + 5, fill=color if not edge else "", outline=color, width=2)
            lx, anchor = label_side(x, w / 2, 7 * 6, w, gap=10)
            draw_text(c, lx, y, "%d m" % relative(snap["position"], m["xyz"])[0], color, outline="#000000",
                      anchor=anchor, size=9)

    # ---- in the Monsters Settings tab
    def build_settings(self, f):
        tk = self.tk
        section(tk, f, "Pets: pop up on a soul drop")
        row = tk.Frame(f, bg=BG)
        row.pack(fill="x")
        self.notify_var = tk.StringVar(value=self.cfg["notify"])
        for value, text in (("all", "Every pet"), ("targets", "Target pets"), ("off", "Off")):
            tk.Radiobutton(row, text=text, value=value, variable=self.notify_var, command=self.set_flags, bg=BG, fg=TEXT,
                           selectcolor=BUTTON, activebackground=BG, activeforeground=TEXT, font=(FONT, 10),
                           anchor="w").pack(side="left", padx=(0, 8))
        self.flags = {}
        for key, text in (("sound", "Beep with the pop-up"),
                          ("spawns", "Spawn points of target pets' soul monsters (hollow)"),
                          ("live", "Target pets' soul monsters in range (solid)")):
            self.flags[key] = tk.BooleanVar(value=self.cfg[key])
            checkbox(tk, f, text, self.flags[key], self.set_flags).pack(fill="x")

    def set_flags(self):
        self.cfg.update({k: v.get() for k, v in self.flags.items()}, notify=self.notify_var.get())
        self.save()

    # ---- loop hooks (from the Monsters feature)
    def background(self, snap):
        self.sync(snap)
        if self.app.map_key() not in self.data.spawns and snap["monsters"]:
            key = self.data.which_map({m["type"] for m in snap["monsters"]})
            if key and key != self.detected:
                self.detected = key
                self.app.engine.say("Pets: map is %s (from soul monsters)" % key)

    def new_live(self, snap):
        """Target pets' soul monsters that came into range since the last call -> their names (for the alert)."""
        live = self.live(snap)
        present = {m["id"] for _, m in live}
        new = sorted({"%s (%s)" % (self.monster_name(m["type"]), self.name(pid)) for pid, m in live
                      if m["id"] not in self.alerted})
        self.alerted = present
        return new

    def status(self):
        owned, maxed, souls, total = self.summary()
        return "pets %d/%d" % (owned, total)
