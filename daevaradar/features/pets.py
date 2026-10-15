"""Pets: your collection's progress to Lv3 (live from the game), soul-drop popups, and where the monsters that
drop the souls you want spawn (radar, map, overlay)."""
import json
import math
import time

from ..common import ROOT, load_settings, save_settings
from ..game import SOUL_STAGES, MapData, map_name
from ..sound import play
from ..ui import BG, BUTTON, BUTTON_HOVER, FONT, MONO, MUTED, PANEL, TEXT, Toast, WATCH_COLORS, button, checkbox, dim, draw_cone, draw_me, draw_rings, draw_text, heading, label_side, radar_point, relative, section
from ..feature import Feature


PET_DEFAULT = dict(targets=[], notify="all", sound=True, spawns=True, live=True, filter="open", me_on_top=True)
MAX_LEVEL = len(SOUL_STAGES)
SOUL_CAP = sum(SOUL_STAGES)
GENUS_COLOR = {"Cogni": "#c58cff", "Fera": "#7cf08a", "Natura": "#ffd24a", "Varian": "#ff6ad5", "Special": "#9aa0aa"}
MAP_PX = 4096


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
        self.pets_of_type = {}  # monster type id -> [pet ids whose soul it drops]
        for pid, p in self.pets.items():
            for s in p.get("souls", ()):
                for t in s["types"]:
                    self.pets_of_type.setdefault(t, []).append(pid)
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


class PetsFeature(Feature):
    """Pet collection. The game sends the whole collection when you enter the world ('00 90') and every soul
    drop / level up after that ('0d 90', '14 90'); this keeps your levels and souls, pops up each drop with what's
    left to Lv3, and shows where the monsters that drop the souls of your target pets spawn: hollow points from
    the map data (credit Shugo.GG; which monster drops what: metabot.gg), solid when one is in range."""
    title, icon, TABS = "Pets", "❀", ("Progress", "Radar", "Map", "Overlay", "Settings")
    overlay_key, overlay_color = "pet_overlay", "#7cf08a"
    overlay_text = "A see-through layer with the spawn points of your target pets' soul monsters, for the minimap."
    tracks = "monsters"
    off_text = "Souls aren't counted and nothing pops up while it's off."

    def __init__(self, app, parent):
        settings = load_settings()
        self.cfg = dict(PET_DEFAULT, **settings.get("pets", {}))
        saved = settings.get("pet_collection", {})
        self.levels = {int(k): v for k, v in saved.get("levels", {}).items()}
        self.souls = {int(k): v for k, v in saved.get("souls", {}).items()}
        self.synced = saved.get("synced")  # "YYYY-MM-DD HH:MM" of the last full sync from the game
        self.data = PetData()
        self.range = 200
        self.sync_seen = self.events_seen = None
        self.detected = None
        self.map_view = None  # (map key, centre x px, centre y px, px per canvas px) of the Map tab
        self.list_ids = []
        self.selected = None
        self.last_beep = 0
        super().__init__(app, parent)

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

    def target_color(self, pid):
        targets = self.cfg["targets"]
        return WATCH_COLORS[targets.index(pid) % len(WATCH_COLORS)] if pid in targets else MUTED

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
        name = self.data.pets.get(pid, {}).get("name", "Pet %d" % pid)
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
            Toast(self.root, "❀ %s  %s\n%s" % (name, what, line), color=self.target_color(pid) if target else "#7cf08a",
                  seconds=6)
            if self.cfg["sound"] and time.time() - self.last_beep > 1:
                self.last_beep = time.time()
                play("system beep")

    def summary(self):
        ids = self.data.collection
        owned = sum(1 for i in ids if self.levels.get(i))
        maxed = sum(1 for i in ids if self.levels.get(i, 0) >= MAX_LEVEL)
        souls = sum(self.pet(i)[3] for i in ids)
        return owned, maxed, souls, len(ids)

    # ---- Progress tab
    def build_progress(self, f):
        tk = self.tk
        self.summary_label = tk.Label(f, text="", bg=BG, fg=TEXT, font=(FONT, 10, "bold"), anchor="w", justify="left")
        self.summary_label.pack(fill="x")
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
        tk.Label(row, text="or double-click · targets show on Radar, Map, Overlay", bg=BG, fg=MUTED,
                 font=(FONT, 9)).pack(side="left", padx=8)
        self.detail = tk.Label(f, text="", bg=BG, fg=MUTED, font=(MONO, 9), anchor="nw", justify="left", height=6)
        self.detail.pack(fill="x", pady=(6, 0))
        self.refresh_list()

    def set_filter(self):
        self.cfg["filter"] = self.filter_var.get()
        self.save()
        self.refresh_list()

    def refresh_list(self):
        if not hasattr(self, "listbox"):
            return
        owned, maxed, souls, total = self.summary()
        self.summary_label.config(text="Collected %d/%d · Lv3 %d/%d · souls %d/%d (%d%%)" % (
            owned, total, maxed, total, souls, total * SOUL_CAP, 100 * souls / max(1, total * SOUL_CAP)))
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
                mark, self.data.pets[pid]["name"], level, self.bar(level, souls, need), stage, done, SOUL_CAP))
            self.listbox.itemconfig(i, fg=self.target_color(pid) if pid in self.cfg["targets"] else
                                    (TEXT if souls or level else MUTED))
        if not self.list_ids:
            self.listbox.insert("end", "  Nothing matches.")
        self.show_detail()

    @staticmethod
    def bar(level, souls, need):
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

    # ---- Radar tab
    def build_radar(self, f):
        tk = self.tk
        self.size = size = 380
        self.canvas = tk.Canvas(f, width=size, height=size, bg=PANEL, highlightthickness=0)
        self.canvas.pack(anchor="w")
        bar = tk.Frame(f, bg=BG)
        bar.pack(fill="x", pady=(6, 0))
        button(tk, bar, "−", lambda: self.zoom(1.5), width=3).pack(side="left", padx=(0, 4))
        button(tk, bar, "+", lambda: self.zoom(1 / 1.5), width=3).pack(side="left", padx=(0, 8))
        listing = tk.Frame(f, bg=BG)
        listing.pack(fill="both", expand=True, pady=(4, 0))
        self.rows = [tk.Label(listing, text="", bg=BG, fg=TEXT, anchor="w", font=(MONO, 10)) for _ in range(7)]
        for row in self.rows:
            row.pack(fill="x")

    def zoom(self, factor):
        self.range = max(25, min(2000, self.range * factor))

    def live(self, snap):
        """Monsters in range that drop a target pet's soul -> [(pet, monster)]."""
        if not self.cfg["live"]:
            return []
        types = self.data.types_for(self.cfg["targets"])
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

    def draw_radar(self, snap):
        c, size = self.canvas, self.size
        mid = size / 2
        c.delete("all")
        draw_cone(c, mid, mid, mid - 18, snap.get("cone"), fill="#2b3346")
        scale = draw_rings(c, size, self.range)
        position = snap["position"]
        if not self.cfg["targets"]:
            draw_text(c, mid, mid + 30, "no target pets yet\n(Progress tab: double-click a pet)", "#b8c0cc", size=10, bold=False)
        elif not position:
            draw_text(c, mid, mid + 30, "waiting for your position\n(teleport)", "#b8c0cc", size=10, bold=False)
        lines = []
        if position and self.cfg["targets"]:
            spots = self.spots(snap, self.range)
            for pid, t, xyz in spots:
                x, y, _ = radar_point(position, xyz, mid, scale, self.range)
                c.create_oval(x - 4, y - 4, x + 4, y + 4, outline=self.target_color(pid), width=2)
            live = self.live(snap)
            for pid, m in live:
                x, y, beyond = radar_point(position, m["xyz"], mid, scale, self.range)
                color = self.target_color(pid)
                c.create_oval(x - 7, y - 7, x + 7, y + 7, fill="#000000", outline="")
                c.create_oval(x - 6, y - 6, x + 6, y + 6, fill=color if not beyond else "", outline=color, width=2)
            if self.cfg["me_on_top"]:
                draw_me(c, mid, mid, 6)
            for pid, m in sorted(live, key=lambda pm: relative(position, pm[1]["xyz"])[0]):
                dist, dz, direction = relative(position, m["xyz"])
                lines.append(("● %5d m %-2s %-18.18s → %s" % (dist, direction, self.monster_name(m["type"]),
                                                             self.data.pets[pid]["name"]), self.target_color(pid)))
            nearest = {}
            for pid, t, xyz in spots:
                d = relative(position, xyz)
                if t not in nearest or d[0] < nearest[t][1][0]:
                    nearest[t] = (pid, d)
            for t, (pid, (dist, dz, direction)) in sorted(nearest.items(), key=lambda kv: kv[1][1][0]):
                lines.append(("○ %5d m %-2s %-18.18s → %s" % (dist, direction, self.monster_name(t),
                                                             self.data.pets[pid]["name"]), dim(self.target_color(pid), 0.25)))
        key = self.map_key()
        foot = ("%s · %d target%s" % (map_name(key), len(self.cfg["targets"]), "" if len(self.cfg["targets"]) == 1 else "s")
                if key else "Map not known yet: pick it in the sidebar, or meet a soul monster.")
        lines = lines[:len(self.rows) - 1] + [(foot, MUTED)]
        for i, row in enumerate(self.rows):
            text, color = lines[i] if i < len(lines) else ("", MUTED)
            row.config(text=text, fg=color)

    def monster_name(self, type_id):
        for spawns in self.data.spawns.values():
            if type_id in spawns:
                return spawns[type_id]["name"]
        return "Monster %d" % type_id

    # ---- Map tab: the whole map, drag to pan, wheel to zoom, hover a point for its monster
    def build_map(self, f):
        tk = self.tk
        bar = tk.Frame(f, bg=BG)
        bar.pack(fill="x", pady=(0, 6))
        keys = self.data.keys()
        self.map_choices = [("auto", "Current map")] + [(k, map_name(k)) for k in keys]
        self.map_pick = tk.StringVar(value="Current map")
        menu = tk.OptionMenu(bar, self.map_pick, *[label for _, label in self.map_choices], command=lambda v: self.reset_view())
        menu.config(bg=BUTTON, fg=TEXT, activebackground=BUTTON_HOVER, activeforeground=TEXT, relief="flat", bd=0,
                    highlightthickness=0, font=(FONT, 9), cursor="hand2")
        menu["menu"].config(bg=PANEL, fg=TEXT, activebackground=BUTTON_HOVER, activeforeground=TEXT, font=(FONT, 9))
        menu.pack(side="left")
        button(tk, bar, "Fit targets", self.fit_targets).pack(side="left", padx=6)
        button(tk, bar, "On me", self.center_me).pack(side="left")
        self.msize = 380
        self.mcanvas = tk.Canvas(f, width=self.msize, height=self.msize, bg="#0f1115", highlightthickness=0, cursor="fleur")
        self.mcanvas.pack(anchor="w")
        self.mcanvas.bind("<ButtonPress-1>", self.map_press)
        self.mcanvas.bind("<B1-Motion>", self.map_drag)
        self.mcanvas.bind("<MouseWheel>", lambda e: self.map_zoom(e.x, e.y, 1 / 1.25 if e.delta > 0 else 1.25))
        self.mcanvas.bind("<Button-4>", lambda e: self.map_zoom(e.x, e.y, 1 / 1.25))
        self.mcanvas.bind("<Button-5>", lambda e: self.map_zoom(e.x, e.y, 1.25))
        self.mcanvas.bind("<Motion>", self.map_hover)
        self.map_info = tk.Label(f, text="", bg=BG, fg=MUTED, font=(MONO, 9), anchor="w", justify="left", height=3)
        self.map_info.pack(fill="x", pady=(4, 0))
        self.map_points = []  # (canvas x, y, pet, type) drawn, for hover

    def shown_map(self):
        pick = next((k for k, label in self.map_choices if label == self.map_pick.get()), "auto")
        return self.map_key() if pick == "auto" else pick

    def reset_view(self):
        self.map_view = None

    def view(self, key):
        if not self.map_view or self.map_view[0] != key:
            self.map_view = [key, MAP_PX / 2, MAP_PX / 2, MAP_PX / self.msize]
        return self.map_view

    def to_canvas(self, v, x, y):
        return (x - v[1]) / v[3] + self.msize / 2, (y - v[2]) / v[3] + self.msize / 2

    def map_press(self, e):
        self.drag = (e.x, e.y)

    def map_drag(self, e):
        key = self.shown_map()
        if not key:
            return
        v = self.view(key)
        v[1] -= (e.x - self.drag[0]) * v[3]
        v[2] -= (e.y - self.drag[1]) * v[3]
        self.drag = (e.x, e.y)
        self.draw_map(self.app.snapshot())

    def map_zoom(self, x, y, factor):
        key = self.shown_map()
        if not key:
            return
        v = self.view(key)
        px, py = v[1] + (x - self.msize / 2) * v[3], v[2] + (y - self.msize / 2) * v[3]  # keep this point under the mouse
        v[3] = max(0.5, min(MAP_PX / self.msize * 1.5, v[3] * factor))
        v[1], v[2] = px - (x - self.msize / 2) * v[3], py - (y - self.msize / 2) * v[3]
        self.draw_map(self.app.snapshot())

    def fit_targets(self):
        key = self.shown_map()
        pts = self.data.spawn_points(key, self.cfg["targets"]) if key else []
        if not pts:
            return
        xs, ys = [p[2] for p in pts], [p[3] for p in pts]
        span = max(max(xs) - min(xs), max(ys) - min(ys), 120) * 1.3
        self.map_view = [key, (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, span / self.msize]
        self.draw_map(self.app.snapshot())

    def center_me(self):
        snap = self.app.snapshot()
        key = self.shown_map()
        if key and snap["position"]:
            x, y = MapData.to_px(snap["position"])
            self.map_view = [key, x, y, 400 / self.msize]
            self.draw_map(snap)

    def draw_map(self, snap):
        c, size = self.mcanvas, self.msize
        c.delete("all")
        key = self.shown_map()
        self.map_points = []
        if not key:
            draw_text(c, size / 2, size / 2, "Map not known yet:\npick one above or in the sidebar", "#b8c0cc", size=10, bold=False)
            return
        v = self.view(key)
        x0, y0 = self.to_canvas(v, 0, 0)
        x1, y1 = self.to_canvas(v, MAP_PX, MAP_PX)
        c.create_rectangle(x0, y0, x1, y1, outline="#2a2e37", fill=PANEL)
        for gx in range(512, MAP_PX, 512):  # grid every 1 km
            ax, _ = self.to_canvas(v, gx, 0)
            _, ay = self.to_canvas(v, 0, gx)
            c.create_line(ax, y0, ax, y1, fill="#23262e")
            c.create_line(x0, ay, x1, ay, fill="#23262e")
        for name, kind, px, py in self.data.places.get(key, ()):
            x, y = self.to_canvas(v, px, py)
            if -40 <= x <= size + 40 and -10 <= y <= size + 10:
                if kind == "Kibelisk":
                    c.create_rectangle(x - 2, y - 2, x + 2, y + 2, fill="#4fd8ff", outline="")
                elif kind == "BattleField" and v[3] < 2.5 or kind == "Village" and v[3] < 1.2:  # zoomed in only
                    c.create_text(x, y, text=name, fill="#6b7590" if kind == "BattleField" else "#525b6e",
                                  font=(FONT, 8, "italic" if kind == "Village" else "normal"))
        r = 3 if v[3] > 4 else 4
        for pid, t, px, py in self.data.spawn_points(key, self.cfg["targets"]):
            x, y = self.to_canvas(v, px, py)
            if -5 <= x <= size + 5 and -5 <= y <= size + 5:
                color = self.target_color(pid)
                c.create_oval(x - r - 1, y - r - 1, x + r + 1, y + r + 1, fill="#000000", outline="")
                c.create_oval(x - r, y - r, x + r, y + r, fill=color, outline="")
                self.map_points.append((x, y, pid, t))
        if snap["position"] and key == self.map_key():
            x, y = self.to_canvas(v, *MapData.to_px(snap["position"]))
            if 0 <= x <= size and 0 <= y <= size:
                draw_me(c, x, y, 5)
        x = 6
        for pid in self.cfg["targets"][:6]:
            label = self.data.pets.get(pid, {}).get("name", str(pid))
            draw_text(c, x, size - 10, "● " + label, self.target_color(pid), anchor="w", size=8)
            x += 14 + 6 * len(label)
        c.create_text(size - 6, 8, text="%d m across" % (size * v[3] * 2), fill="#6b7590", anchor="ne", font=(FONT, 8))
        if not self.cfg["targets"]:
            draw_text(c, size / 2, size / 2, "No target pets: double-click pets\non the Progress tab", "#b8c0cc", size=10, bold=False)

    def map_hover(self, e):
        near = [(math.hypot(x - e.x, y - e.y), pid, t) for x, y, pid, t in self.map_points]
        best = min(near, default=None)
        if not best or best[0] > 8:
            self.map_info.config(text="Drag to pan · wheel to zoom · hover a point for its monster")
            return
        _, pid, t = best
        key = self.shown_map()
        m = self.data.spawns[key][t]
        soul = next((s for s in self.data.pets[pid].get("souls", ()) if t in s["types"]), None)
        level, souls, need, done = self.pet(pid)
        self.map_info.config(text="%s (L%s) drops %s %s\n%s: Lv%d, %d/%d to Lv3 · %d spawn points of it here" % (
            m["name"], m["level"], self.data.pets[pid]["name"], ("(%.1f%%)" % soul["chance"]) if soul else "",
            self.data.pets[pid]["name"], level, done, SOUL_CAP, len(m["points"])), fg=self.target_color(pid))

    # ---- Overlay tab
    def build_overlay(self, f):
        self.build_overlay_tab(f)

    def paint_overlay(self, overlay, c, snap):
        position = snap["position"]
        w, h = overlay.cfg["w"], overlay.cfg["h"]
        for pid, t, xyz in self.spots(snap, math.hypot(w, h) / 2 / overlay.cfg["ppm"]):
            x, y, edge = overlay.to_screen(position, xyz)
            if not edge:
                c.create_oval(x - 6, y - 6, x + 6, y + 6, outline="#000000", width=4)
                c.create_oval(x - 4, y - 4, x + 4, y + 4, outline=self.target_color(pid), width=2)
        for pid, m in self.live(snap):
            x, y, edge = overlay.to_screen(position, m["xyz"])
            color = self.target_color(pid)
            c.create_oval(x - 7, y - 7, x + 7, y + 7, fill="#000000" if not edge else "", outline="#000000", width=2)
            c.create_oval(x - 5, y - 5, x + 5, y + 5, fill=color if not edge else "", outline=color, width=2)
            dist = relative(position, m["xyz"])[0]
            lx, anchor = label_side(x, w / 2, 7 * 6, w, gap=10)
            draw_text(c, lx, y, "%d m" % dist, color, outline="#000000", anchor=anchor, size=9)
        if self.cfg["me_on_top"]:
            draw_me(c, w / 2, h / 2, 5)

    # ---- Settings tab
    def build_settings(self, f):
        tk = self.tk
        heading(tk, f, "Pet settings", "Your collection comes from the game when you enter the world; soul drops "
                "and level ups are counted live. Spawn points: Shugo.GG map data; which monster drops which soul: "
                "metabot.gg (data/SOURCE.md).")
        section(tk, f, "Pop up on a soul drop")
        self.notify_var = tk.StringVar(value=self.cfg["notify"])
        for value, text in (("all", "Every pet"), ("targets", "Target pets only"), ("off", "Off")):
            tk.Radiobutton(f, text=text, value=value, variable=self.notify_var, command=self.set_flags, bg=BG, fg=TEXT,
                           selectcolor=BUTTON, activebackground=BG, activeforeground=TEXT, font=(FONT, 10),
                           anchor="w").pack(fill="x")
        section(tk, f, "Show")
        self.flags = {}
        for key, text in (("sound", "Beep with the pop-up"),
                          ("spawns", "Spawn points of target pets' soul monsters (hollow)"),
                          ("live", "Soul monsters in range (solid)"),
                          ("me_on_top", "Your position on top (radar and overlay)")):
            self.flags[key] = tk.BooleanVar(value=self.cfg[key])
            checkbox(tk, f, text, self.flags[key], self.set_flags).pack(fill="x")
        section(tk, f, "Data")
        tk.Label(f, bg=BG, fg=MUTED, font=(FONT, 9), anchor="w", justify="left", wraplength=390, text=(
            "%d pets, spawn data for %s." % (len(self.data.pets), ", ".join(map_name(k) for k in self.data.keys()) or "no map")
        )).pack(fill="x")
        button(tk, f, "Clear targets", self.clear_targets).pack(anchor="w", pady=(6, 0))

    def set_flags(self):
        self.cfg.update({k: v.get() for k, v in self.flags.items()}, notify=self.notify_var.get())
        self.save()

    def clear_targets(self):
        self.cfg["targets"] = []
        self.save()
        self.refresh_list()

    # ---- loop hooks
    def background(self, snap):
        self.sync(snap)
        if not self.app.map_key() in self.data.spawns and snap["monsters"]:
            key = self.data.which_map({m["type"] for m in snap["monsters"]})
            if key and key != self.detected:
                self.detected = key
                self.app.engine.say("Pets: map is %s (from soul monsters)" % key)
        super().background(snap)

    def update(self, snap, tab):
        if tab == "Radar":
            self.draw_radar(snap)
        elif tab == "Map":
            self.draw_map(snap)
        super().update(snap, tab)

    def status(self, snap):
        owned, maxed, souls, total = self.summary()
        live = len(self.live(snap)) if self.cfg["targets"] else 0
        return "pets %d/%d" % (owned, total) + (" · %d soul monster%s near" % (live, "" if live == 1 else "s") if live else "")
