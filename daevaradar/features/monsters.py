"""Monsters near you, named from data/monsters.json, with a watch list."""
import math
import time

from ..common import load_settings, save_settings
from ..game import monster_info, monster_names
from ..sound import play
from ..ui import BG, BUTTON, BUTTON_HOVER, FONT, MONO, MUTED, PANEL, TEXT, WATCH_COLORS, button, checkbox, draw_cone, draw_me, draw_rings, draw_text, heading, label_side, radar_point, section
from ..feature import Feature


MONSTER_DEFAULT = dict(watch=[], mode="watched", names=False, unnamed=False, alert=True,  # names: clutter
                       me_on_top=True)
class MonstersFeature(Feature):
    """Monsters near you (from the game's 'monster in range' messages), named from data/monsters.json.
    A watch list picks the ones to show: on the radar, on this feature's own overlay, and with an alert."""
    title, icon, TABS = "Monsters", "☠", ("Radar", "Overlay", "Settings")
    overlay_key, overlay_color = "monster_overlay", "#ff5c5c"
    overlay_text = "A see-through layer with the monsters you watch, to place over the game's minimap."
    tracks = "monsters"

    def __init__(self, app, parent):
        self.cfg = dict(MONSTER_DEFAULT, **load_settings().get("monsters", {}))
        self.range = 150
        self.alerted = set()
        self.last_alert = 0
        super().__init__(app, parent)

    def save(self):
        save_settings(dict(load_settings(), monsters=self.cfg))

    def watched(self, name):
        return name in self.cfg["watch"]

    def color(self, name):
        if name in self.cfg["watch"]:
            return WATCH_COLORS[self.cfg["watch"].index(name) % len(WATCH_COLORS)]
        return "#8a8f98"

    def visible(self, snap):
        """-> [(name, level, rank, monster)] to show, per the watch list and mode."""
        out = []
        for m in snap["monsters"]:
            name, level, rank = monster_info(m["type"])
            if rank == "?" and not self.cfg["unnamed"]:
                continue  # NPCs without a name in the table (townsfolk, quest givers...)
            if self.cfg["mode"] == "watched" and name not in self.cfg["watch"]:
                continue
            out.append((name, level, rank, m))
        return out

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
        tk.Label(f, text="Nearby — click a name to watch / unwatch it", bg=BG, fg=MUTED, font=(FONT, 9),
                 anchor="w").pack(fill="x", pady=(8, 2))
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

    def set_mode(self):
        self.cfg["mode"] = self.mode_var.get()
        self.save()

    def draw_radar(self, snap):
        c, size = self.canvas, self.size
        mid = size / 2
        c.delete("all")
        draw_cone(c, mid, mid, mid - 18, snap.get("cone"), fill="#2b3346")  # under the rings and pins
        scale = draw_rings(c, size, self.range)
        position = snap["position"]
        if not position:
            draw_text(c, mid, mid + 30, "waiting for your position\n(teleport)", "#b8c0cc", size=10, bold=False)
        shown = self.visible(snap) if position else []
        for name, level, rank, m in sorted(shown, key=lambda s: self.watched(s[0])):  # watched on top
            x, y, beyond = radar_point(position, m["xyz"], mid, scale, self.range)
            color, r = self.color(name), (6 if self.watched(name) else 4)
            if beyond:
                c.create_oval(x - r, y - r, x + r, y + r, outline=color, width=2)
            else:
                c.create_oval(x - r - 1, y - r - 1, x + r + 1, y + r + 1, fill="#000000", outline="")
                c.create_oval(x - r, y - r, x + r, y + r, fill=color, outline="")
        if position and self.cfg["me_on_top"]:  # you, above every monster dot
            draw_me(c, mid, mid, 6)
        if position and not shown:
            draw_text(c, mid, mid + 30, "no watched monsters nearby" if self.cfg["mode"] == "watched"
                      else "no monsters nearby", "#b8c0cc", size=10, bold=False)
        # nearby list: every named monster type in range, watched first, then nearest
        groups = {}
        for m in (snap["monsters"] if position else []):
            name, level, rank = monster_info(m["type"])
            if rank == "?" and not self.cfg["unnamed"]:
                continue
            d = math.dist(m["xyz"][:2], position[:2]) / 100
            g = groups.setdefault(name, [0, d, level, rank])
            g[0] += 1
            g[1] = min(g[1], d)
        order = sorted(groups.items(), key=lambda kv: (not self.watched(kv[0]), kv[1][1]))
        for i, row in enumerate(self.rows):
            if i < len(order):
                name, (count, d, level, rank) = order[i]
                mark = "★" if self.watched(name) else "☆"
                row.config(text="%s %-24.24s x%-2d %4d m  L%-2s %s" % (mark, name, count, d, level or "?", rank[:1]),
                           fg=self.color(name) if self.watched(name) else MUTED)
                self.row_names[i] = name
            else:
                row.config(text="No monsters in range." if i == 0 and position else "", fg=MUTED)
                self.row_names[i] = None

    # ---- Overlay tab
    def build_overlay(self, f):
        self.build_overlay_tab(f)

    def paint_overlay(self, overlay, c, snap):
        position = snap["position"]
        w = overlay.cfg["w"]
        shown = self.visible(snap)
        labels = self.cfg["names"] and len(shown) <= 12
        for name, level, rank, m in sorted(shown, key=lambda s: self.watched(s[0])):
            x, y, edge = overlay.to_screen(position, m["xyz"])
            color, r = self.color(name), (5 if self.watched(name) else 4)
            if edge:
                c.create_oval(x - r - 2, y - r - 2, x + r + 2, y + r + 2, outline="#000000", width=4)
                c.create_oval(x - r, y - r, x + r, y + r, outline=color, width=2)
            else:
                c.create_oval(x - r - 2, y - r - 2, x + r + 2, y + r + 2, fill="#000000", outline="")
                c.create_oval(x - r, y - r, x + r, y + r, fill=color, outline="")
            if labels:
                lx, anchor = label_side(x, w / 2, 6 * len(name), w, gap=9)
                draw_text(c, lx, y, name, color, outline="#000000", anchor=anchor, size=8)
        if self.cfg["me_on_top"]:  # you, above every monster dot (the overlay's centre is your position)
            draw_me(c, w / 2, overlay.cfg["h"] / 2, 5)

    # ---- Settings tab
    def build_settings(self, f):
        tk = self.tk
        heading(tk, f, "Monster settings", "Pick the monsters to watch. Several at once is fine; each gets "
                "its own colour on the radar and the overlay.")
        section(tk, f, "Find a monster")
        self.search = tk.StringVar()
        entry = tk.Entry(f, textvariable=self.search, bg=BUTTON, fg=TEXT, insertbackground=TEXT, relief="flat",
                         font=(FONT, 10))
        entry.pack(fill="x", ipady=3)
        self.search.trace_add("write", lambda *a: self.refresh_search())
        lists = tk.Frame(f, bg=BG)
        lists.pack(fill="x", pady=(6, 0))
        self.results = tk.Listbox(lists, height=7, bg=PANEL, fg=TEXT, selectbackground=BUTTON_HOVER, relief="flat",
                                  highlightthickness=0, font=(FONT, 9), activestyle="none")
        self.results.pack(fill="x")
        self.results.bind("<Double-Button-1>", lambda e: self.add_selected())
        row = tk.Frame(f, bg=BG)
        row.pack(fill="x", pady=(4, 0))
        button(tk, row, "★ Watch", self.add_selected).pack(side="left")
        tk.Label(row, text="or double-click a name", bg=BG, fg=MUTED, font=(FONT, 9)).pack(side="left", padx=8)
        section(tk, f, "Watching")
        self.watch_list = tk.Listbox(f, height=5, bg=PANEL, fg=TEXT, selectbackground=BUTTON_HOVER, relief="flat",
                                     highlightthickness=0, font=(FONT, 9, "bold"), activestyle="none")
        self.watch_list.pack(fill="x")
        self.watch_list.bind("<Double-Button-1>", lambda e: self.remove_selected())
        row = tk.Frame(f, bg=BG)
        row.pack(fill="x", pady=(4, 0))
        button(tk, row, "Remove", self.remove_selected).pack(side="left", padx=(0, 4))
        button(tk, row, "Clear all", self.clear_watch).pack(side="left")
        section(tk, f, "Show")
        self.names_var = tk.BooleanVar(value=self.cfg["names"])
        self.unnamed_var = tk.BooleanVar(value=self.cfg["unnamed"])
        self.alert_var = tk.BooleanVar(value=self.cfg["alert"])
        self.me_var = tk.BooleanVar(value=self.cfg["me_on_top"])
        checkbox(tk, f, "Names on the overlay", self.names_var, self.set_flags).pack(fill="x")
        checkbox(tk, f, "Include NPCs without a known name", self.unnamed_var, self.set_flags).pack(fill="x")
        checkbox(tk, f, "Beep when a watched monster appears", self.alert_var, self.set_flags).pack(fill="x")
        checkbox(tk, f, "Your position on top (radar and overlay)", self.me_var, self.set_flags).pack(fill="x")
        self.refresh_search()
        self.refresh_watch()

    def refresh_search(self):
        text = self.search.get().strip().lower()
        names = [n for n in monster_names() if text in n.lower()] if text else []
        self.results.delete(0, "end")
        for n in names[:200]:
            self.results.insert("end", ("★ " if self.watched(n) else "   ") + n)
        if text and not names:
            self.results.insert("end", "   (no monster called that)")

    def refresh_watch(self):
        if not hasattr(self, "watch_list"):
            return
        self.watch_list.delete(0, "end")
        for i, n in enumerate(self.cfg["watch"]):
            self.watch_list.insert("end", "● " + n)
            self.watch_list.itemconfig(i, fg=self.color(n))
        if not self.cfg["watch"]:
            self.watch_list.insert("end", "Nothing yet. Search above, or click a name on the Radar tab.")
            self.watch_list.itemconfig(0, fg=MUTED)
        self.refresh_search()

    def add_selected(self):
        sel = self.results.curselection()
        if sel:
            name = self.results.get(sel[0])[3:]
            if name and not name.startswith("(") and not self.watched(name):
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
        self.cfg.update(names=self.names_var.get(), unnamed=self.unnamed_var.get(), alert=self.alert_var.get(),
                        me_on_top=self.me_var.get())
        self.save()

    # ---- loop hooks
    def background(self, snap):
        if self.cfg["alert"] and self.cfg["watch"]:
            present = {m["id"] for m in snap["monsters"] if monster_info(m["type"])[0] in self.cfg["watch"]}
            new = present - self.alerted
            if new and time.time() - self.last_alert > 3:
                self.last_alert = time.time()
                names = sorted({monster_info(m["type"])[0] for m in snap["monsters"] if m["id"] in new})
                self.app.engine.say("Watched monster: " + ", ".join(names))
                play("system beep")
            self.alerted = present | (self.alerted & present)
        super().background(snap)

    def update(self, snap, tab):
        if tab == "Radar":
            self.draw_radar(snap)
        super().update(snap, tab)

    def status(self, snap):
        if not self.cfg["watch"]:
            return "%d monsters" % len(snap["monsters"])
        n = sum(1 for m in snap["monsters"] if monster_info(m["type"])[0] in self.cfg["watch"])
        return "%d watched" % n
