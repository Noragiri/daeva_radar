"""Gathering nodes, named by matching their position to the local map data."""
import math
import time

from ..common import load_settings, save_settings
from ..game import MapData
from ..sound import play
from ..ui import BG, BUTTON, BUTTON_HOVER, FONT, MONO, MUTED, PANEL, TEXT, WATCH_COLORS, button, checkbox, draw_cone, draw_me, draw_rings, draw_text, heading, radar_point, section
from ..feature import Feature


GATHER_DEFAULT = dict(watch=[], mode="watched", map_nodes=True, alert=True, me_on_top=True, unnamed=False, map="auto")
MAP_NAMES = {"verteron": "Verteron", "altgard": "Altgard", "reshanta-lower": "Lower Reshanta"}
def map_name(key):
    return MAP_NAMES.get(key, key.replace("-", " ").title())
class GatherFeature(Feature):
    """Gathering nodes. Live nodes come from the game ('46' object messages); their names are learned once by
    matching their position to the local map data (data/maps, credit Shugo.GG) and then remembered by type,
    on every map. The map data also shows nodes the game hasn't announced yet (hollow)."""
    title, icon, TABS = "Gathering", "⛏", ("Radar", "Overlay", "Settings")
    overlay_key, overlay_color = "gather_overlay", "#7cf08a"
    overlay_text = "A see-through layer with the resources you watch, to place over the game's minimap."
    tracks = "nodes"

    def __init__(self, app, parent):
        self.cfg = dict(GATHER_DEFAULT, **load_settings().get("gathering", {}))
        self.learned = {int(k): v for k, v in load_settings().get("node_types", {}).items()}
        self.mapdata = MapData()
        self.map_key = None  # the map in use: picked by hand, else detected
        self.detected = None  # what the live nodes say, whatever is picked
        self.map_checked = 0
        self.range = 150
        self.alerted = set()
        self.last_alert = 0
        super().__init__(app, parent)

    def save(self):
        save_settings(dict(load_settings(), gathering=self.cfg))

    # ---- naming
    def name_of(self, node):
        return self.learned.get(node["type"])

    def picked(self):
        """The map picked by hand, or None for Auto (also when its data is gone)."""
        return self.cfg["map"] if self.cfg["map"] in self.mapdata.maps else None

    def learn(self, snap):
        """Which map you're on, and names for node types seen for the first time (saved for good)."""
        self.map_key = self.picked() or self.detected
        if not snap["position"] or time.time() - self.map_checked < 2:
            return
        self.map_checked = time.time()
        points = [n["xyz"] for n in snap["nodes"]]
        key = self.mapdata.which_map(points) if points else None
        if key and key != self.detected:
            self.detected = key
            self.app.engine.say("Gathering: map is %s" % key)
        self.map_key = self.picked() or self.detected
        if not self.map_key:
            return
        changed = False
        for n in snap["nodes"]:
            if n["type"] in self.learned:
                continue
            hit = self.mapdata.match(self.map_key, n["xyz"], 3)
            if hit:
                self.learned[n["type"]] = hit[0]
                self.app.engine.say("Gathering: learned type %d = %s" % (n["type"], hit[0]))
                changed = True
        if changed:
            save_settings(dict(load_settings(), node_types={str(k): v for k, v in self.learned.items()}))
            if hasattr(self, "watch_list"):
                self.refresh_search()

    def all_names(self):
        return sorted(set(self.mapdata.names()) | set(self.learned.values()))

    def watched(self, name):
        return name in self.cfg["watch"]

    def color(self, name):
        if name in self.cfg["watch"]:
            return WATCH_COLORS[(self.cfg["watch"].index(name) + 1) % len(WATCH_COLORS)]
        return "#8a8f98"

    def shown(self, snap):
        """-> live [(name, node)] and map-only [(name, xyz)] to draw, per the watch list and mode."""
        live = []
        for n in snap["nodes"]:
            name = self.name_of(n)
            if name is None:
                if not self.cfg["unnamed"]:
                    continue
                name = "Object %d" % n["type"]
            if self.cfg["mode"] == "watched" and name not in self.cfg["watch"]:
                continue
            live.append((name, n))
        extra = []
        if self.cfg["map_nodes"] and self.map_key and snap["position"]:
            names = self.cfg["watch"] if self.cfg["mode"] == "watched" else self.mapdata.names()
            live_px = [MapData.to_px(n["xyz"]) for _, n in live]
            for name, xyz in self.mapdata.nearby(self.map_key, names, snap["position"], 400):
                px, py = MapData.to_px(xyz)
                if not any(math.hypot(px - x, py - y) <= 3 for x, y in live_px):  # not already live
                    extra.append((name, xyz))
        return live, extra

    def toggle_watch(self, name):
        if name in self.cfg["watch"]:
            self.cfg["watch"].remove(name)
        else:
            self.cfg["watch"].append(name)
        self.save()
        self.refresh_watch()

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
        self.mode_var = tk.StringVar(value=self.cfg["mode"])
        for value, text in (("watched", "Watched"), ("all", "All")):
            tk.Radiobutton(bar, text=text, value=value, variable=self.mode_var, command=self.set_mode, bg=BG, fg=TEXT,
                           selectcolor=BUTTON, activebackground=BG, activeforeground=TEXT, font=(FONT, 10),
                           indicatoron=False, padx=10, pady=3, relief="flat", bd=0, offrelief="flat",
                           cursor="hand2").pack(side="left", padx=(0, 4))
        row = tk.Frame(f, bg=BG)
        row.pack(fill="x", pady=(6, 0))
        tk.Label(row, text="Map", bg=BG, fg=MUTED, font=(FONT, 9)).pack(side="left", padx=(0, 6))
        self.map_choices = [("auto", "Auto")] + [(k, map_name(k)) for k in sorted(self.mapdata.maps, key=map_name)]
        labels = dict(self.map_choices)
        self.map_var = tk.StringVar(value=labels.get(self.cfg["map"], "Auto"))
        menu = tk.OptionMenu(row, self.map_var, *[label for _, label in self.map_choices], command=self.set_map)
        menu.config(bg=BUTTON, fg=TEXT, activebackground=BUTTON_HOVER, activeforeground=TEXT, relief="flat", bd=0,
                    highlightthickness=0, font=(FONT, 9), width=14, cursor="hand2")
        menu["menu"].config(bg=PANEL, fg=TEXT, activebackground=BUTTON_HOVER, activeforeground=TEXT, font=(FONT, 9))
        menu.pack(side="left")
        self.map_label = tk.Label(row, text="", bg=BG, fg=MUTED, font=(FONT, 9))
        self.map_label.pack(side="left", padx=8)
        tk.Label(f, text="Nearby — click a name to watch / unwatch it  (● live  ○ on the map, not announced yet)",
                 bg=BG, fg=MUTED, font=(FONT, 9), anchor="w").pack(fill="x", pady=(8, 2))
        listing = tk.Frame(f, bg=BG)
        listing.pack(fill="both", expand=True)
        self.rows, self.row_names = [], []
        for i in range(8):
            row = tk.Label(listing, text="", bg=BG, fg=TEXT, anchor="w", font=(MONO, 10), cursor="hand2")
            row.pack(fill="x")
            row.bind("<Button-1>", lambda e, i=i: self.row_names[i] and self.toggle_watch(self.row_names[i]))
            self.rows.append(row)
            self.row_names.append(None)

    def zoom(self, factor):
        self.range = max(25, min(2000, self.range * factor))

    def set_map(self, label):
        self.cfg["map"] = next(key for key, text in self.map_choices if text == label)
        self.save()
        self.map_key = self.picked() or self.detected

    def map_text(self):
        detected = ("detected: %s" % map_name(self.detected)) if self.detected else "not detected yet"
        if not self.picked():
            return detected, MUTED
        if self.detected and self.detected != self.picked():
            return detected + " (differs!)", "#ffb347"
        return detected, MUTED

    def set_mode(self):
        self.cfg["mode"] = self.mode_var.get()
        self.save()

    def draw_radar(self, snap):
        c, size = self.canvas, self.size
        mid = size / 2
        c.delete("all")
        draw_cone(c, mid, mid, mid - 18, snap.get("cone"), fill="#2b3346")
        scale = draw_rings(c, size, self.range)
        position = snap["position"]
        text, color = self.map_text()
        self.map_label.config(text=text, fg=color)
        if not position:
            draw_text(c, mid, mid + 30, "waiting for your position\n(teleport)", "#b8c0cc", size=10, bold=False)
            for row in self.rows:
                row.config(text="")
            return
        live, extra = self.shown(snap)
        for name, xyz in extra:  # map-only: hollow, under the live ones
            x, y, beyond = radar_point(position, xyz, mid, scale, self.range)
            if not beyond:
                c.create_oval(x - 4, y - 4, x + 4, y + 4, outline=self.color(name), width=2)
        for name, n in sorted(live, key=lambda s: self.watched(s[0])):
            x, y, beyond = radar_point(position, n["xyz"], mid, scale, self.range)
            color, r = self.color(name), (6 if self.watched(name) else 4)
            c.create_oval(x - r - 1, y - r - 1, x + r + 1, y + r + 1, fill="#000000", outline="")
            c.create_oval(x - r, y - r, x + r, y + r, fill=color, outline="" if not beyond else color)
        if self.cfg["me_on_top"]:
            draw_me(c, mid, mid, 6)
        if not live and not extra:
            draw_text(c, mid, mid + 30, "no watched resources nearby" if self.cfg["mode"] == "watched"
                      else "no resources nearby", "#b8c0cc", size=10, bold=False)
        groups = {}
        for name, n in live:
            g = groups.setdefault(name, [0, 0, 1e9])
            g[0] += 1
            g[2] = min(g[2], math.dist(n["xyz"][:2], position[:2]) / 100)
        for name, xyz in extra:
            g = groups.setdefault(name, [0, 0, 1e9])
            g[1] += 1
            g[2] = min(g[2], math.dist(xyz[:2], position[:2]) / 100)
        order = sorted(groups.items(), key=lambda kv: (not self.watched(kv[0]), kv[1][2]))
        for i, row in enumerate(self.rows):
            if i < len(order):
                name, (n_live, n_map, d) = order[i]
                mark = "★" if self.watched(name) else "☆"
                row.config(text="%s %-22.22s ●%-3d ○%-3d %4d m" % (mark, name, n_live, n_map, d),
                           fg=self.color(name) if self.watched(name) else MUTED)
                self.row_names[i] = name
            else:
                row.config(text="Nothing in range." if i == 0 else "", fg=MUTED)
                self.row_names[i] = None

    # ---- Overlay tab
    def build_overlay(self, f):
        self.build_overlay_tab(f)

    def paint_overlay(self, overlay, c, snap):
        position = snap["position"]
        live, extra = self.shown(snap)
        for name, xyz in extra:
            x, y, edge = overlay.to_screen(position, xyz)
            if not edge:
                c.create_oval(x - 6, y - 6, x + 6, y + 6, outline="#000000", width=4)
                c.create_oval(x - 4, y - 4, x + 4, y + 4, outline=self.color(name), width=2)
        for name, n in sorted(live, key=lambda s: self.watched(s[0])):
            x, y, edge = overlay.to_screen(position, n["xyz"])
            color, r = self.color(name), (5 if self.watched(name) else 4)
            c.create_oval(x - r - 2, y - r - 2, x + r + 2, y + r + 2, fill="#000000", outline="")
            c.create_oval(x - r, y - r, x + r, y + r, fill=color if not edge else "", outline=color, width=2)
        if self.cfg["me_on_top"]:
            draw_me(c, overlay.cfg["w"] / 2, overlay.cfg["h"] / 2, 5)

    # ---- Settings tab
    def build_settings(self, f):
        tk = self.tk
        heading(tk, f, "Gathering settings", "Pick the resources to watch; each gets its own colour. Names come "
                "from the local map data (credit Shugo.GG), learned once per node type and remembered.")
        section(tk, f, "Find a resource")
        self.search = tk.StringVar()
        entry = tk.Entry(f, textvariable=self.search, bg=BUTTON, fg=TEXT, insertbackground=TEXT, relief="flat",
                         font=(FONT, 10))
        entry.pack(fill="x", ipady=3)
        self.search.trace_add("write", lambda *a: self.refresh_search())
        self.results = tk.Listbox(f, height=6, bg=PANEL, fg=TEXT, selectbackground=BUTTON_HOVER, relief="flat",
                                  highlightthickness=0, font=(FONT, 9), activestyle="none")
        self.results.pack(fill="x", pady=(6, 0))
        self.results.bind("<Double-Button-1>", lambda e: self.add_selected())
        row = tk.Frame(f, bg=BG)
        row.pack(fill="x", pady=(4, 0))
        button(tk, row, "★ Watch", self.add_selected).pack(side="left")
        tk.Label(row, text="or double-click a name", bg=BG, fg=MUTED, font=(FONT, 9)).pack(side="left", padx=8)
        section(tk, f, "Watching")
        self.watch_list = tk.Listbox(f, height=4, bg=PANEL, fg=TEXT, selectbackground=BUTTON_HOVER, relief="flat",
                                     highlightthickness=0, font=(FONT, 9, "bold"), activestyle="none")
        self.watch_list.pack(fill="x")
        self.watch_list.bind("<Double-Button-1>", lambda e: self.remove_selected())
        row = tk.Frame(f, bg=BG)
        row.pack(fill="x", pady=(4, 0))
        button(tk, row, "Remove", self.remove_selected).pack(side="left", padx=(0, 4))
        button(tk, row, "Clear all", self.clear_watch).pack(side="left")
        section(tk, f, "Show")
        self.flags = {}
        for key, text in (("map_nodes", "Nodes from the map data the game hasn't announced yet (hollow)"),
                          ("me_on_top", "Your position on top (radar and overlay)"),
                          ("alert", "Beep when a watched resource appears"),
                          ("unnamed", "Include objects without a known name")):
            self.flags[key] = tk.BooleanVar(value=self.cfg[key])
            checkbox(tk, f, text, self.flags[key], self.set_flags).pack(fill="x")
        self.refresh_watch()

    def refresh_search(self):
        if not hasattr(self, "results"):
            return
        text = self.search.get().strip().lower()
        self.results.delete(0, "end")
        for n in [n for n in self.all_names() if text in n.lower()]:
            self.results.insert("end", ("★ " if self.watched(n) else "   ") + n)

    def refresh_watch(self):
        if not hasattr(self, "watch_list"):
            return
        self.watch_list.delete(0, "end")
        for i, n in enumerate(self.cfg["watch"]):
            self.watch_list.insert("end", "● " + n)
            self.watch_list.itemconfig(i, fg=self.color(n))
        if not self.cfg["watch"]:
            self.watch_list.insert("end", "Nothing yet. Pick from the list above, or click a name on the Radar tab.")
            self.watch_list.itemconfig(0, fg=MUTED)
        self.refresh_search()

    def add_selected(self):
        sel = self.results.curselection()
        if sel:
            name = self.results.get(sel[0])[3:]
            if name and not self.watched(name):
                self.toggle_watch(name)

    def remove_selected(self):
        sel = self.watch_list.curselection()
        if sel and sel[0] < len(self.cfg["watch"]):
            self.toggle_watch(self.cfg["watch"][sel[0]])

    def clear_watch(self):
        self.cfg["watch"] = []
        self.save()
        self.refresh_watch()

    def set_flags(self):
        self.cfg.update({k: v.get() for k, v in self.flags.items()})
        self.save()

    # ---- loop hooks
    def background(self, snap):
        self.learn(snap)
        if self.cfg["alert"] and self.cfg["watch"]:
            present = {n["id"] for n in snap["nodes"] if self.name_of(n) in self.cfg["watch"]}
            new = present - self.alerted
            if new and time.time() - self.last_alert > 3:
                self.last_alert = time.time()
                names = sorted({self.name_of(n) for n in snap["nodes"] if n["id"] in new})
                self.app.engine.say("Watched resource: " + ", ".join(names))
                play("system beep")
            self.alerted = present | (self.alerted & present)
        super().background(snap)

    def update(self, snap, tab):
        if tab == "Radar":
            self.draw_radar(snap)
        super().update(snap, tab)

    def status(self, snap):
        if not self.cfg["watch"]:
            return None
        n = sum(1 for node in snap["nodes"] if self.name_of(node) in self.cfg["watch"])
        return "%d resource%s" % (n, "" if n == 1 else "s")
