"""Gathering nodes, named by matching their position to the local map data."""
import math
import time

from ..common import load_settings, save_settings
from ..game import GATHER_GROUPS, MapData
from ..sound import play
from ..ui import MUTED, PANEL, WATCH_COLORS, draw_cone, draw_me, draw_rings, draw_text, radar_point
from ..feature import Feature
from .. import page as P


GATHER_DEFAULT = dict(watch=[], mode="watched", map_nodes=True, alert=True, me_on_top=True, unnamed=False)
class GatherFeature(Feature):
    """Gathering nodes. Live nodes come from the game ('46' object messages); their names are learned once by
    matching their position to the local map data (data/maps, credit Shugo.GG) and then remembered by type,
    on every map. The map data also shows nodes the game hasn't announced yet (hollow). The map is the app's
    (sidebar picker, else detected)."""
    title, icon, TABS = "Gathering", "⛏", ("Radar", "Settings")
    overlay_key, overlay_color = "gather_overlay", "#7cf08a"
    overlay_text = "A see-through layer with the resources you watch, to place over the game's minimap."
    tracks = "nodes"

    def __init__(self, app):
        self.cfg = dict(GATHER_DEFAULT, **load_settings().get("gathering", {}))
        self.learned = {int(k): v for k, v in load_settings().get("node_types", {}).items()}
        self.mapdata = app.mapdata
        self.learn_checked = 0
        self.range = 150
        self.alerted = set()
        self.last_alert = 0
        self.search, self.result_sel, self.watch_sel = "", None, None
        super().__init__(app)

    def save(self):
        save_settings(dict(load_settings(), gathering={k: v for k, v in self.cfg.items() if k != "map"}))

    # ---- naming
    def name_of(self, node):
        return self.learned.get(node["type"])

    @property
    def map_key(self):
        return self.app.map_key()

    def learn(self, snap):
        """Names for node types seen for the first time (saved for good)."""
        if not snap["position"] or time.time() - self.learn_checked < 2:
            return
        self.learn_checked = time.time()
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

    def all_names(self):
        return sorted(set(self.mapdata.names()) | set(self.learned.values()))

    def watched(self, name):
        return name in self.cfg["watch"]

    def color(self, name):
        if name in self.cfg["watch"]:
            return WATCH_COLORS[self.cfg["watch"].index(name) % len(WATCH_COLORS)]
        return "#8a8f98"

    def shown(self, snap):
        """-> live [(name, node)] and map-only [(name, xyz)] to draw, per the watch list and mode."""
        live = []
        feathers = self.app.features.get("Feathers")
        for n in snap["nodes"]:
            if feathers and n["type"] in feathers.types:
                continue  # the Feathers page shows those
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

    # ---- Radar tab
    def page_radar(self, snap):
        c = P.Canvas(380, 380, PANEL)
        rows = self.draw_radar(c, snap)
        return [c.block(),
                P.row(P.button("zoom", "−", 1.5), P.button("zoom", "+", 1 / 1.5),
                      P.choice("mode", (("watched", "Watched"), ("all", "All")), self.cfg["mode"]),
                      P.button("map", "Map ↗")),
                P.text("Nearby: click a name to watch / unwatch it  (● live  ○ on the map, not announced yet)", small=True),
                P.lines(rows, click="watch")]

    def on_zoom(self, factor):
        self.range = max(25, min(2000, self.range * float(factor)))

    def on_mode(self, value):
        self.cfg["mode"] = value
        self.save()

    def on_map(self, _):
        self.app.open_map("Resources")

    def on_watch(self, name):
        self.toggle_watch(name)

    def draw_radar(self, c, snap):
        """Draws the radar on c -> the list lines [(text, colour, name to watch)]."""
        size = c.w
        mid = size / 2
        draw_cone(c, mid, mid, mid - 18, snap.get("cone"), fill="#2b3346")
        scale = draw_rings(c, size, self.range)
        position = snap["position"]
        if not position:
            draw_text(c, mid, mid + 30, "waiting for your position\n(teleport)", "#b8c0cc", size=10, bold=False)
            return []
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
        lines = [("%s %-22.22s ●%-3d ○%-3d %4d m" % ("★" if self.watched(name) else "☆", name, n_live, n_map, d),
                  self.color(name) if self.watched(name) else MUTED, name)
                 for name, (n_live, n_map, d) in order[:14]]
        return lines or [("Nothing in range.", MUTED, None)]

    # ---- Overlay
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
    def results(self):
        text = self.search.strip().lower()
        return [n for n in self.all_names() if text in n.lower()]

    def group_names(self, group):
        return [n for n in self.all_names() if self.mapdata.group_of(n) == group]

    def toggle_group(self, group):
        """Watch every resource of the group, or, when all of them already are, unwatch them all."""
        names = self.group_names(group)
        if names and all(self.watched(n) for n in names):
            self.cfg["watch"] = [n for n in self.cfg["watch"] if n not in names]
        else:
            self.cfg["watch"] += [n for n in names if not self.watched(n)]
        self.save()

    def page_settings(self, snap):
        groups = []
        for group, _ in GATHER_GROUPS:  # ✓ all watched, ◐ some
            names = self.group_names(group)
            n = sum(1 for name in names if self.watched(name))
            mark = "✓ " if names and n == len(names) else "◐ " if n else ""
            groups.append(P.button("group", "%s%s (%d)" % (mark, group, len(names)), group, disabled=not names))
        results, watch = self.results(), self.cfg["watch"]
        return [P.heading("Gathering settings", "Pick the resources to watch; each gets its own colour. Names come from "
                          "the local map data (© NCSOFT, via Shugo.GG), learned once per node type and remembered."),
                P.section("Find a resource"),
                P.field("search", self.search, "Resource name", width=320),
                P.listbox("result", [(("★ " if self.watched(n) else "   ") + n, None) for n in results], self.result_sel,
                          height=6, mono=False, empty="(nothing called that)"),
                P.row(P.button("watch_result", "★ Watch", disabled=self.result_sel is None),
                      P.text("or double-click a name", small=True)),
                P.section("Groups: click to watch / unwatch all of a kind"),
                P.row(*groups),
                P.section("Watching"),
                P.listbox("watched", [("● " + n, self.color(n)) for n in watch], self.watch_sel, height=4, mono=False,
                          empty="Nothing yet. Pick from the list above, or click a name on the Radar tab."),
                P.row(P.button("unwatch", "Remove", disabled=self.watch_sel is None),
                      P.button("clear_watch", "Clear all", disabled=not watch, confirm="Stop watching every resource?")),
                P.section("Show"),
                P.toggle("flag_map_nodes", "Nodes from the map data the game hasn't announced yet (hollow)",
                         self.cfg["map_nodes"]),
                P.toggle("flag_me_on_top", "Your position on top (radar and overlay)", self.cfg["me_on_top"]),
                P.toggle("flag_alert", "Beep when a watched resource appears", self.cfg["alert"]),
                P.toggle("flag_unnamed", "Include objects without a known name", self.cfg["unnamed"])]

    def on_group(self, group):
        self.toggle_group(group)

    def on_search(self, text):
        self.search, self.result_sel = text or "", None

    def on_result(self, index):
        self.result_sel = int(index) if int(index) < len(self.results()) else None

    def on_result_open(self, index):
        self.on_result(index)
        self.on_watch_result(None)

    def on_watch_result(self, _):
        results = self.results()
        if self.result_sel is not None and self.result_sel < len(results) and not self.watched(results[self.result_sel]):
            self.toggle_watch(results[self.result_sel])

    def on_watched(self, index):
        self.watch_sel = int(index) if int(index) < len(self.cfg["watch"]) else None

    def on_watched_open(self, index):
        self.on_watched(index)
        self.on_unwatch(None)

    def on_unwatch(self, _):
        if self.watch_sel is not None and self.watch_sel < len(self.cfg["watch"]):
            self.toggle_watch(self.cfg["watch"][self.watch_sel])
        self.watch_sel = None

    def on_clear_watch(self, _):
        self.cfg["watch"] = []
        self.watch_sel = None
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

    def status(self, snap):
        if not self.cfg["watch"]:
            return None
        n = sum(1 for node in snap["nodes"] if self.name_of(node) in self.cfg["watch"])
        return "%d resource%s" % (n, "" if n == 1 else "s")
