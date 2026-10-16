"""The Map window: the world map (data/map_tiles) with every pet's soul monster spawns coloured by genus, hidden
cubes and gathering nodes as layers, and any resource highlighted (= watched in Gathering). Opened from the
Monsters feature's Pets tab or from Gathering; one window for both (App.open_map)."""
import math
from collections import OrderedDict

from .common import ROOT, load_settings, save_settings
from .game import GATHER_GROUPS, SOUL_STAGES, MapData, map_name
from .ui import ACCENT, BG, BUTTON, BUTTON_HOVER, FONT, MONO, MUTED, PANEL, SIDEBAR, TEXT, button


TILES = ROOT / "data" / "map_tiles"
MAX_LEVEL, SOUL_CAP = len(SOUL_STAGES), sum(SOUL_STAGES)
MAP_PX, TILE, MAX_ZOOM = 4096, 256, 4
GENUS_COLOR = {"Cogni": "#4fa3ff", "Fera": "#ff5c5c", "Natura": "#7cf08a", "Varian": "#ffd24a", "Special": "#c58cff"}
GENUS_ICON = {"Cogni": "◉", "Fera": "▼", "Natura": "❦", "Varian": "◆", "Special": "★"}
# (layer, label, colour): resources by their group in the map data (game.GATHER_GROUPS)
LAYERS = (("Pets", "Pets", None), ("Cube", "Hidden Cube", "#c58cff"), ("Ore", "Ore", "#b0b8c8"),
          ("Gem", "Gems", "#ff9ad9"), ("Odyle", "Odyle", "#4fd8ff"), ("Herb", "Herbs", "#9be37c"),
          ("Log", "Wood", "#c8955a"), ("Ingredient", "Ingredients", "#ffb347"))
LAYER_COLOR = {key: color for key, _, color in LAYERS}
GROUP_ORDER = [group for group, _ in GATHER_GROUPS]
DEFAULT = dict(layers=["Pets"], genus="All types", unfinished=True, geometry="1000x740", mode="Pets")


class WorldMap:
    def __init__(self, app, mode=None):
        from PIL import Image, ImageTk  # required for the WebP map tiles (pip install pillow)
        self.Image, self.ImageTk = Image, ImageTk
        self.app, tk = app, app.tk
        self.tk = tk
        settings = load_settings()
        self.cfg = dict(DEFAULT, **settings.get("world_map", settings.get("pet_map", {})))  # was the Pet map
        self.requested = mode  # the side panel asked for (Pets / Resources), applied once the window is built
        self.win = win = tk.Toplevel(app.root)
        win.title("Daeva Radar · Map")
        win.configure(bg=BG)
        win.geometry(self.cfg["geometry"])
        win.minsize(760, 520)
        win.protocol("WM_DELETE_WINDOW", self.close)
        self.topmost = app.topmost.get()  # follows the main window's "Keep on top" (else it opens behind the game)
        win.attributes("-topmost", self.topmost)
        if getattr(app, "icon", None):
            try:
                win.iconphoto(False, app.icon)
            except tk.TclError:
                pass
        self.cache = OrderedDict()  # (map, z, x, y, w, h) -> PhotoImage
        self.view = None  # [map, centre x px, centre y px, screen px per map px]
        self.drawn = []  # (screen x, y, kind, info) for clicks
        self.selected = None  # a pet id (Pets) or a resource name (Resources)
        self.seen = None  # what the side panel was built from, to rebuild it when it changes
        self.rows = []
        self.need_fit = True  # fit the view once the canvas has its size
        self.build()
        self.set_map(self.initial_map())

    # ---- the features this shows
    @property
    def pets(self):
        monsters = self.app.features.get("Monsters")
        return monsters.pets if monsters else None

    @property
    def gathering(self):
        return self.app.features.get("Gathering")

    def highlighted(self):
        """Resources highlighted on the map: the ones watched in Gathering."""
        return list(self.gathering.cfg["watch"]) if self.gathering else []

    def highlights_shown(self):
        """Highlighted resources drawn: all of them on the Resources side, only ticked layers' on the Pets side."""
        lit = self.highlighted()
        if self.cfg["mode"] == "Resources":
            return lit
        md = self.app.mapdata
        return [n for n in lit if self.layer_vars.get(md.group_of(n)) and self.layer_vars[md.group_of(n)].get()]

    def highlight_color(self, name):
        return self.gathering.color(name) if self.gathering else ACCENT

    # ---- layout
    def build(self):
        tk, win = self.tk, self.win
        top = tk.Frame(win, bg=BG)
        top.pack(fill="x", padx=12, pady=(10, 6))
        self.keys = sorted((p.name for p in TILES.iterdir() if p.is_dir()), key=map_name) if TILES.exists() else []
        self.map_var = tk.StringVar()
        self.menu(top, self.map_var, [map_name(k) for k in self.keys], lambda v: self.set_map(self.key_of(v)), 18)
        self.genus_var = tk.StringVar(value=self.cfg["genus"])
        self.menu(top, self.genus_var, ["All types"] + list(GENUS_COLOR)[:4], lambda v: self.filters_changed(), 10)
        self.search = tk.StringVar()
        entry = tk.Entry(top, textvariable=self.search, bg=PANEL, fg=TEXT, insertbackground=TEXT, relief="flat",
                         font=(FONT, 10))
        entry.pack(side="left", fill="x", expand=True, ipady=4, padx=(6, 6))
        self.search.trace_add("write", lambda *a: self.filters_changed())
        self.unfinished = tk.BooleanVar(value=self.cfg["unfinished"])
        tk.Checkbutton(top, text="Unfinished pets only", variable=self.unfinished, command=self.filters_changed, bg=BG,
                       fg=TEXT, selectcolor=BUTTON, activebackground=BG, activeforeground=TEXT, font=(FONT, 9)).pack(side="left")

        layers = tk.LabelFrame(win, text=" Map layers ", bg=BG, fg=MUTED, font=(FONT, 9), bd=1, relief="solid",
                               highlightthickness=0)
        layers.pack(fill="x", padx=12)
        self.layer_vars = {}
        for key, label, color in LAYERS:
            var = self.layer_vars[key] = tk.BooleanVar(value=key in self.cfg["layers"])
            tk.Checkbutton(layers, text=("❀ " if key == "Pets" else "● ") + label, variable=var, command=self.filters_changed,
                           bg=BG, fg=color or "#ff9ad9", selectcolor=BUTTON, activebackground=BG, activeforeground=TEXT,
                           font=(FONT, 9)).pack(side="left", padx=(4, 8), pady=2)
        tk.Label(win, text="Highlighted resources (★) are the ones watched in Gathering: shown bigger, always on the Resources side. Pet levels "
                 "come from the game when you enter the world.", bg=BG, fg=MUTED, font=(FONT, 9), anchor="w").pack(
            fill="x", padx=12, pady=(4, 6))

        bottom = tk.Frame(win, bg=SIDEBAR)
        bottom.pack(side="bottom", fill="x")
        tk.Label(bottom, text="AION 2 data and map art © NCSOFT (official AION 2 web API), via Shugo.GG and metabot.gg · "
                 "wheel: zoom · drag: pan · click a point", bg=SIDEBAR, fg=MUTED, font=(FONT, 8), anchor="w").pack(fill="x", padx=12, pady=4)
        legend = tk.Frame(win, bg=BG)
        legend.pack(side="bottom", fill="x", padx=12, pady=6)
        for genus, color in GENUS_COLOR.items():
            tk.Label(legend, text="%s %s" % (GENUS_ICON[genus], genus), bg=BG, fg=color, font=(FONT, 9, "bold")).pack(
                side="left", padx=(0, 12))
        self.totals = tk.Label(legend, text="", bg=BG, fg=TEXT, font=(FONT, 9), anchor="e")
        self.totals.pack(side="right")

        body = tk.Frame(win, bg=BG)
        body.pack(fill="both", expand=True)
        side = tk.Frame(body, bg=PANEL, width=290)
        side.pack(side="right", fill="y")
        side.pack_propagate(False)
        modes = tk.Frame(side, bg=PANEL)
        modes.pack(fill="x")
        self.mode_buttons = {}
        for mode in ("Pets", "Resources"):
            b = tk.Label(modes, text=mode, bg=PANEL, fg=MUTED, font=(FONT, 10, "bold"), padx=14, pady=6, cursor="hand2")
            b.pack(side="left")
            b.bind("<Button-1>", lambda e, m=mode: self.set_mode(m))
            self.mode_buttons[mode] = b
        self.header = tk.Label(side, text="", bg="#23262e", fg=TEXT, font=(FONT, 10, "bold"), anchor="w", padx=10, pady=8)
        self.header.pack(fill="x")
        self.listbox = tk.Listbox(side, bg=PANEL, fg=TEXT, selectbackground=BUTTON_HOVER, relief="flat",
                                  highlightthickness=0, font=(MONO, 9), activestyle="none")
        self.listbox.pack(fill="both", expand=True, padx=4, pady=4)
        self.listbox.bind("<<ListboxSelect>>", lambda e: self.pick_row())
        self.listbox.bind("<Double-Button-1>", lambda e: self.toggle_selected())
        self.star = button(tk, side, "", self.toggle_selected)
        self.star.pack(fill="x", padx=6)
        self.info = tk.Label(side, text="", bg=PANEL, fg=MUTED, font=(FONT, 9), anchor="nw", justify="left",
                             wraplength=270, height=6, padx=8)
        self.info.pack(fill="x", pady=(4, 6))
        self.canvas = c = tk.Canvas(body, bg="#0b0c0f", highlightthickness=0, cursor="fleur")
        c.pack(side="left", fill="both", expand=True)
        zoom = tk.Frame(c, bg=BG)
        zoom.place(relx=1, x=-10, y=10, anchor="ne")
        for text, cmd in (("+", lambda: self.zoom_at(None, 1.5)), ("−", lambda: self.zoom_at(None, 1 / 1.5)),
                          ("⤢", self.fit), ("⌖", self.center_me)):
            button(tk, zoom, text, cmd, width=2).pack(pady=1)
        c.bind("<Configure>", lambda e: self.fit() if self.need_fit else self.redraw())
        c.bind("<ButtonPress-1>", self.press)
        c.bind("<B1-Motion>", self.drag)
        c.bind("<ButtonRelease-1>", self.release)
        c.bind("<MouseWheel>", lambda e: self.zoom_at(e, 1.25 if e.delta > 0 else 1 / 1.25))
        c.bind("<Button-4>", lambda e: self.zoom_at(e, 1.25))
        c.bind("<Button-5>", lambda e: self.zoom_at(e, 1 / 1.25))

    def menu(self, parent, var, labels, command, width):
        tk = self.tk
        m = tk.OptionMenu(parent, var, *labels, command=command)
        m.config(bg=PANEL, fg=TEXT, activebackground=BUTTON_HOVER, activeforeground=TEXT, relief="flat", bd=0, width=width,
                 highlightthickness=0, font=(FONT, 10), anchor="w", cursor="hand2")
        m["menu"].config(bg=PANEL, fg=TEXT, activebackground=BUTTON_HOVER, activeforeground=TEXT, font=(FONT, 10))
        m.pack(side="left", padx=(0, 6), ipady=2)

    def key_of(self, label):
        return next(k for k in self.keys if map_name(k) == label)

    def current_map(self):
        """The map you're on: the app's (sidebar / detected), else the one the pets worked out from soul monsters."""
        return self.pets.map_key() if self.pets else self.app.map_key()

    def initial_map(self):
        key = self.current_map() or self.cfg.get("map")
        return key if key in self.keys else ("verteron" if "verteron" in self.keys else self.keys[0])

    def save(self):
        self.cfg.update(layers=[k for k, v in self.layer_vars.items() if v.get()], genus=self.genus_var.get(),
                        unfinished=self.unfinished.get(), map=self.view[0] if self.view else None)
        if self.win.winfo_exists() and self.win.state() == "normal":
            self.cfg["geometry"] = self.win.geometry()
        save_settings(dict(load_settings(), world_map=self.cfg))

    # ---- what's shown
    def pets_here(self):
        """Pets (after the filters) with spawn points on this map -> [(pet, [(type, x, y)])]."""
        pets = self.pets
        if not pets:
            return []
        key, data = self.view[0], pets.data
        spawns = data.spawns.get(key, {})
        genus, text = self.genus_var.get(), self.search.get().strip().lower()
        out = []
        for pid in data.collection:
            p = data.pets[pid]
            if genus != "All types" and p["genus"] != genus:
                continue
            if self.unfinished.get() and pets.pet(pid)[0] >= MAX_LEVEL:
                continue
            pts = [(t, x, y) for s in p.get("souls", ()) for t in s["types"] if t in spawns
                   for x, y in spawns[t]["points"]]
            if not pts:
                continue
            if text and text not in p["name"].lower() and not any(text in spawns[t]["name"].lower() for t, _, _ in pts):
                continue
            out.append((pid, pts))
        return out

    def resources_here(self):
        """Gathering resources on this map (after the search) -> [(name, group, [(x, y)])], by group then name."""
        md, text = self.app.mapdata, self.search.get().strip().lower()
        out = []
        for name, points in md.maps.get(self.view[0], {}).get("points", {}).items():
            if text and text not in name.lower():
                continue
            group = md.group_of(name) or "Other"
            out.append((name, group, points))
        order = {g: i for i, g in enumerate(GROUP_ORDER)}
        return sorted(out, key=lambda r: (order.get(r[1], 99), r[0]))

    def filters_changed(self):
        self.save()
        self.refresh_side()
        self.redraw()

    def set_map(self, key):
        self.map_var.set(map_name(key))
        self.view = [key, MAP_PX / 2, MAP_PX / 2, 0.15]
        self.selected = None
        self.set_mode(self.requested or self.cfg["mode"], fit=False)
        self.requested = None
        if self.canvas.winfo_width() > 50:
            self.fit()
        else:
            self.need_fit = True

    def set_mode(self, mode, fit=True):
        """Side panel: Pets or Resources. Switching shows that side's points: Resources hides the pets' spawns (the
        Pets layer can be ticked again) and turns on the resource layers if neither they nor a highlight is on."""
        if mode != self.cfg["mode"]:
            self.selected = None
            self.info.config(text="")
            self.layer_vars["Pets"].set(mode == "Pets")
        self.cfg["mode"] = mode
        for m, b in self.mode_buttons.items():
            b.config(fg=ACCENT if m == mode else MUTED, bg=BUTTON if m == mode else PANEL)
        if mode == "Resources" and not any(self.layer_vars[k].get() for k in GROUP_ORDER) and not self.highlighted():
            for k in GROUP_ORDER:
                self.layer_vars[k].set(True)
        if mode == "Pets" and not self.layer_vars["Pets"].get():
            self.layer_vars["Pets"].set(True)
        self.save()
        self.refresh_side()
        if fit:
            self.redraw()

    # ---- side panel
    def refresh_side(self):
        if not self.view:
            return
        (self.refresh_resources if self.cfg["mode"] == "Resources" else self.refresh_pets)()
        self.seen = self.state()

    def state(self):
        pets = self.pets
        return (self.cfg["mode"], pets.version if pets else None, tuple(self.highlighted()))

    def refresh_pets(self):
        f = self.pets
        rows = self.pets_here() if self.layer_vars["Pets"].get() else []
        self.rows = [pid for pid, _ in sorted(rows, key=lambda r: (r[0] not in f.cfg["targets"], f.data.pets[r[0]]["name"]))]
        self.header.config(text="%d pets · %d spawn points" % (len(rows), sum(len(p) for _, p in rows)))
        self.listbox.delete(0, "end")
        for i, pid in enumerate(self.rows):
            p = f.data.pets[pid]
            level, souls, need, done = f.pet(pid)
            star = "★" if pid in f.cfg["targets"] else GENUS_ICON[p["genus"]]
            state = "Lv3 ✓" if level >= MAX_LEVEL else "Lv%d %d/%d" % (level, souls, need)
            self.listbox.insert("end", "%s %-21.21s %s" % (star, p["name"], state))
            self.listbox.itemconfig(i, fg=GENUS_COLOR[p["genus"]])
        if not self.rows:
            self.listbox.insert("end", "  No pets match." if f.levels or f.synced else "  Waiting for the pet collection.")
        ids = [pid for pid, _ in rows]
        maxed = sum(1 for pid in ids if f.pet(pid)[0] >= MAX_LEVEL)
        left = sum(SOUL_CAP - f.pet(pid)[3] for pid in ids)
        self.totals.config(text="Level 3: %d/%d · To finish: %d souls" % (maxed, len(ids), left) if ids else "Level 3: — · To finish: —")
        self.star.config(text="★ Target / untarget the selected pet")
        self.select_in_list()

    def refresh_resources(self):
        rows = self.resources_here()
        lit = self.highlighted()
        self.rows = [name for name, _, _ in sorted(rows, key=lambda r: r[0] not in lit)]
        by_name = {name: (group, points) for name, group, points in rows}
        self.header.config(text="%d resources · %d nodes" % (len(rows), sum(len(p) for *_, p in rows)))
        self.listbox.delete(0, "end")
        for i, name in enumerate(self.rows):
            group, points = by_name[name]
            mark = "★" if name in lit else "●"
            self.listbox.insert("end", "%s %-22.22s %4d" % (mark, name, len(points)))
            self.listbox.itemconfig(i, fg=self.highlight_color(name) if name in lit else LAYER_COLOR.get(group, TEXT))
        if not self.rows:
            self.listbox.insert("end", "  No gathering data for this map." if not self.search.get() else "  Nothing matches.")
        here = [name for name in lit if name in by_name]
        self.totals.config(text="Highlighted: %d resource%s · %d nodes on this map" % (
            len(here), "" if len(here) == 1 else "s", sum(len(by_name[n][1]) for n in here)) if here else
            "Highlight resources: double-click them in the list")
        self.star.config(text="★ Highlight / unhighlight (= watch in Gathering)")
        self.select_in_list()

    def select_in_list(self):
        if self.selected in self.rows:
            i = self.rows.index(self.selected)
            self.listbox.selection_set(i)
            self.listbox.see(i)

    def pick_row(self):
        sel = self.listbox.curselection()
        if not sel or sel[0] >= len(self.rows):
            return
        self.selected = self.rows[sel[0]]
        if self.cfg["mode"] == "Pets":
            self.show_pet(self.selected)
            pts = next(([(x, y) for _, x, y in p] for pid, p in self.pets_here() if pid == self.selected), [])
        else:
            self.show_resource(self.selected)
            pts = next((p for name, _, p in self.resources_here() if name == self.selected), [])
        if pts:
            self.fit(pts)
        else:
            self.redraw()

    def toggle_selected(self):
        """Pets: target / untarget; Resources: highlight / unhighlight (watch in Gathering)."""
        if self.selected is None:
            return
        if self.cfg["mode"] == "Pets" and self.pets:
            self.pets.toggle_target(self.selected)
        elif self.cfg["mode"] == "Resources" and self.gathering:
            self.gathering.toggle_watch(self.selected)
        self.refresh_side()
        self.redraw()

    def show_pet(self, pid, monster=None):
        f = self.pets
        p = f.data.pets[pid]
        level, souls, need, done = f.pet(pid)
        lines = ["%s (%s)%s" % (p["name"], p["genus"], "  ★ target" if pid in f.cfg["targets"] else ""),
                 "Lv%d reached" % MAX_LEVEL if level >= MAX_LEVEL else "Lv%d · %d/%d to Lv%d · %d/%d (%d left)" % (
                     level, souls, need, level + 1, done, SOUL_CAP, SOUL_CAP - done)]
        for s in p.get("souls", ()):
            if monster is None or monster in s["types"]:
                lines.append("%s L%d · %.1f%%" % (s["monster"], s["level"], s["chance"]))
        self.info.config(text="\n".join(lines[:6]), fg=GENUS_COLOR[p["genus"]])

    def show_resource(self, name):
        md = self.app.mapdata
        lit = name in self.highlighted()
        here = len(md.maps.get(self.view[0], {}).get("points", {}).get(name, ()))
        other = ", ".join("%s %d" % (map_name(k), len(m["points"][name])) for k, m in sorted(md.maps.items())
                          if k != self.view[0] and name in m["points"])
        self.info.config(text="%s (%s)%s\n%d nodes on this map%s" % (
            name, md.group_of(name) or "resource", "  ★ highlighted" if lit else "", here,
            ("\nAlso: " + other) if other else ""), fg=self.highlight_color(name) if lit else
            LAYER_COLOR.get(md.group_of(name), TEXT))

    # ---- drawing
    def to_screen(self, x, y):
        _, cx, cy, s = self.view
        return (x - cx) * s + self.canvas.winfo_width() / 2, (y - cy) * s + self.canvas.winfo_height() / 2

    def to_map(self, sx, sy):
        _, cx, cy, s = self.view
        return (sx - self.canvas.winfo_width() / 2) / s + cx, (sy - self.canvas.winfo_height() / 2) / s + cy

    def tile_image(self, key, z, x, y, w, h):
        k = (key, z, x, y, w, h)
        if k in self.cache:
            self.cache.move_to_end(k)
            return self.cache[k]
        path = TILES / key / str(z) / ("%d_%d.webp" % (x, y))
        if not path.exists():
            return None
        img = self.ImageTk.PhotoImage(self.Image.open(path).convert("RGB").resize((w, h), self.Image.BILINEAR))
        self.cache[k] = img
        if len(self.cache) > 400:
            self.cache.popitem(last=False)
        return img

    def redraw(self):
        if not self.view or not self.win.winfo_exists():
            return
        c = self.canvas
        c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        key, cx, cy, s = self.view
        # tiles: the coarsest zoom level whose tiles aren't stretched more than 1.5x
        z = max(0, min(MAX_ZOOM, math.ceil(math.log2(max(1e-6, s * MAP_PX / TILE / 1.5)))))
        while z > 0 and not (TILES / key / str(z)).exists():
            z -= 1
        span = MAP_PX / 2 ** z  # map px per tile
        x0, y0 = self.to_map(0, 0)
        x1, y1 = self.to_map(w, h)
        for tx in range(max(0, int(x0 // span)), min(2 ** z, int(x1 // span) + 1)):
            for ty in range(max(0, int(y0 // span)), min(2 ** z, int(y1 // span) + 1)):
                sx, sy = (round(v) for v in self.to_screen(tx * span, ty * span))
                ex, ey = (round(v) for v in self.to_screen((tx + 1) * span, (ty + 1) * span))
                img = self.tile_image(key, z, tx, ty, max(1, ex - sx + 1), max(1, ey - sy + 1))  # +1: no seams
                if img:
                    c.create_image(sx, sy, image=img, anchor="nw")
        self.drawn = []

        def visible(px, py):
            return x0 - 5 / s <= px <= x1 + 5 / s and y0 - 5 / s <= py <= y1 + 5 / s

        md = self.app.mapdata
        r = 2 if s < 0.3 else 3
        if self.layer_vars["Cube"].get():
            for px, py in md.cubes.get(key, ()):
                if visible(px, py):
                    sx, sy = self.to_screen(px, py)
                    c.create_rectangle(sx - r, sy - r, sx + r, sy + r, fill=LAYER_COLOR["Cube"], outline="#000000")
                    self.drawn.append((sx, sy, "node", "Hidden cube"))
        lit = self.highlights_shown()
        resources = md.maps.get(key, {}).get("points", {})
        for name, points in resources.items():  # layers first, highlighted ones on top
            group = md.group_of(name)
            if name in lit or not self.layer_vars.get(group) or not self.layer_vars[group].get():
                continue
            for px, py in points:
                if visible(px, py):
                    sx, sy = self.to_screen(px, py)
                    c.create_rectangle(sx - r, sy - r, sx + r, sy + r, fill=LAYER_COLOR[group], outline="#000000")
                    self.drawn.append((sx, sy, "node", name))
        if self.layer_vars["Pets"].get() and self.pets:
            f = self.pets
            pr = 3 if s < 0.3 else 4 if s < 1 else 5
            chosen = self.selected if self.cfg["mode"] == "Pets" else None
            for pid, pts in sorted(self.pets_here(), key=lambda q: (q[0] == chosen, q[0] in f.cfg["targets"])):
                color = GENUS_COLOR[f.data.pets[pid]["genus"]]
                ring = "#ffffff" if pid == chosen else "#000000"
                big = pr + (2 if pid == chosen or pid in f.cfg["targets"] else 0)
                for t, px, py in pts:
                    if visible(px, py):
                        sx, sy = self.to_screen(px, py)
                        c.create_oval(sx - big, sy - big, sx + big, sy + big, fill=color, outline=ring,
                                      width=2 if ring == "#ffffff" or pid in f.cfg["targets"] else 1)
                        self.drawn.append((sx, sy, "pet", (pid, t)))
        hr = r + 2
        chosen = self.selected if self.cfg["mode"] == "Resources" else None
        for name in sorted(set(lit) | ({chosen} if chosen in resources else set()), key=lambda n: n == chosen):
            color = self.highlight_color(name) if name in lit else LAYER_COLOR.get(md.group_of(name), ACCENT)
            for px, py in resources.get(name, ()):
                if visible(px, py):
                    sx, sy = self.to_screen(px, py)
                    c.create_rectangle(sx - hr, sy - hr, sx + hr, sy + hr, fill=color,
                                       outline="#ffffff" if name == chosen else "#000000", width=2)
                    self.drawn.append((sx, sy, "node", name))
        self.draw_me()
        c.create_text(10, h - 10, text="%d m across" % (w / s * 2), fill="#ffffff", anchor="sw", font=(FONT, 8))

    def draw_me(self):
        c = self.canvas
        c.delete("me")
        snap = self.app.snapshot()
        if snap["position"] and self.view and self.current_map() == self.view[0]:
            sx, sy = self.to_screen(*MapData.to_px(snap["position"]))
            c.create_oval(sx - 9, sy - 9, sx + 9, sy + 9, outline=ACCENT, width=2, tags="me")
            c.create_oval(sx - 7, sy - 7, sx + 7, sy + 7, fill="#000000", outline="", tags="me")
            c.create_oval(sx - 5, sy - 5, sx + 5, sy + 5, fill="#ffffff", outline="", tags="me")

    # ---- navigation
    def fit(self, points=None):
        if not self.view:
            return
        c = self.canvas
        if c.winfo_width() <= 50:
            return
        self.need_fit = False
        w, h = c.winfo_width(), c.winfo_height()
        if points is None:
            if self.cfg["mode"] == "Pets" and self.layer_vars["Pets"].get():
                pts = [(x, y) for _, p in self.pets_here() for _, x, y in p]
            else:
                pts = [p for name, _, ps in self.resources_here() if name in self.highlighted() for p in ps]
            if not pts:  # the drawn area of the map: the tiles there are
                tiles = [tuple(map(int, p.stem.split("_"))) for p in (TILES / self.view[0] / "3").glob("*.webp")]
                span = MAP_PX / 8
                pts = [(x * span, y * span) for x, y in tiles] + [((x + 1) * span, (y + 1) * span) for x, y in tiles]
            points = pts
        if not points:
            return
        xs, ys = [p[0] for p in points], [p[1] for p in points]
        spanx, spany = max(max(xs) - min(xs), 60), max(max(ys) - min(ys), 60)
        self.view[1:] = [(min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, min(w / spanx, h / spany) * 0.85]
        self.redraw()

    def center_me(self):
        snap = self.app.snapshot()
        key = self.current_map()
        if snap["position"] and key in self.keys:
            if self.view[0] != key:
                self.map_var.set(map_name(key))
                self.view = [key, 0, 0, 1]
                self.refresh_side()
            x, y = MapData.to_px(snap["position"])
            self.view[1:] = [x, y, max(self.view[3], 1.0)]
            self.redraw()

    def zoom_at(self, e, factor):
        if not self.view:
            return
        c = self.canvas
        sx, sy = (e.x, e.y) if e else (c.winfo_width() / 2, c.winfo_height() / 2)
        px, py = self.to_map(sx, sy)
        self.view[3] = max(0.05, min(4.0, self.view[3] * factor))
        self.view[1] = px - (sx - c.winfo_width() / 2) / self.view[3]
        self.view[2] = py - (sy - c.winfo_height() / 2) / self.view[3]
        self.redraw()

    def press(self, e):
        self.press_at = self.last = (e.x, e.y)

    def drag(self, e):
        dx, dy = e.x - self.last[0], e.y - self.last[1]
        self.last = (e.x, e.y)
        self.canvas.move("all", dx, dy)
        self.view[1] -= dx / self.view[3]
        self.view[2] -= dy / self.view[3]
        self.drawn = [(x + dx, y + dy, k, i) for x, y, k, i in self.drawn]

    def release(self, e):
        if math.hypot(e.x - self.press_at[0], e.y - self.press_at[1]) < 4:
            self.click(e)
        else:
            self.redraw()  # tiles that came into view

    def click(self, e):
        near = min(((math.hypot(x - e.x, y - e.y), k, i) for x, y, k, i in self.drawn), default=None, key=lambda n: n[0])
        if not near or near[0] > 9:
            return
        _, kind, info = near
        if kind == "node":
            if info == "Hidden cube":
                self.info.config(text="Hidden cube spawn point", fg=LAYER_COLOR["Cube"])
                return
            if self.cfg["mode"] != "Resources":
                self.set_mode("Resources", fit=False)
            self.selected = info
            self.show_resource(info)
        else:
            pid, t = info
            if self.cfg["mode"] != "Pets":
                self.set_mode("Pets", fit=False)
            m = self.pets.data.spawns[self.view[0]][t]
            self.selected = pid
            self.show_pet(pid, t)
            self.info.config(text="%s (L%s) · %d spawn points here\n" % (m["name"], m["level"], len(m["points"])) +
                             self.info.cget("text"))
        self.listbox.selection_clear(0, "end")
        self.select_in_list()
        self.redraw()

    # ---- hooks
    def update(self):
        """Every tick (App): your marker; the side panel when the collection, targets or watched resources changed."""
        if not self.win.winfo_exists():
            return
        if self.app.topmost.get() != self.topmost:
            self.topmost = self.app.topmost.get()
            self.win.attributes("-topmost", self.topmost)
        if self.state() != self.seen:
            self.refresh_side()
            self.redraw()
        else:
            self.draw_me()

    def close(self, quitting=False):
        """quitting: the app is closing, so the map reopens at the next start."""
        self.cfg["open"] = quitting
        self.save()
        self.app.world_map = None
        self.win.destroy()
