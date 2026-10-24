"""Monsters near you, named from data/monsters.json, with a watch list; and your pets (features/pets.py): their
progress to Lv3, soul-drop pop-ups, and the monsters that drop the souls of your target pets."""
import math
import time

from ..common import load_settings, save_settings
from ..game import monster_info, monster_names
from ..sound import play
from ..ui import MUTED, PANEL, WATCH_COLORS, draw_cone, draw_me, draw_rings, draw_text, label_side, radar_point
from ..feature import Feature
from .. import page as P
from .pets import Pets


MONSTER_DEFAULT = dict(watch=[], mode="watched", names=False, unnamed=False, alert=True,  # names: clutter
                       me_on_top=True)
class MonstersFeature(Feature):
    """Monsters near you (from the game's 'monster in range' messages), named from data/monsters.json.
    A watch list picks the ones to show: on the radar, on this feature's own overlay, and with an alert.
    Target pets (Pets tab) add the monsters that drop their souls: solid in range, hollow where they spawn."""
    title, icon, TABS = "Monsters", "☠", ("Radar", "Pets", "Settings")
    overlay_key, overlay_color = "monster_overlay", "#ff5c5c"
    overlay_text = ("A see-through layer with the monsters you watch and your target pets' soul monsters, to place "
                    "over the game's minimap.")
    off_text = "Nothing is tracked, alerted or drawn while it's off, and pet souls aren't counted."
    tracks = "monsters"

    def __init__(self, app):
        self.cfg = dict(MONSTER_DEFAULT, **load_settings().get("monsters", {}))
        self.range = 150
        self.alerted = set()
        self.last_alert = 0
        self.search, self.result_sel, self.watch_sel = "", None, None
        self.pets = Pets(app)
        super().__init__(app)

    def save(self):
        save_settings(dict(load_settings(), monsters=self.cfg))

    def watched(self, name):
        return name in self.cfg["watch"]

    def style(self, name, pet=None):
        """-> (colour, dot radius): a target pet's soul monster in its pet's colour, watched ones bigger."""
        if pet is not None:
            return self.pets.target_color(pet), 6
        return self.color(name), (6 if self.watched(name) else 4)

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

    def act(self, id, value):
        """The Pets tab's controls are the Pets helper's."""
        if id.startswith("pet"):
            if id.startswith("pet_flag_"):
                return self.pets.set_flag(id[9:], value)
            return getattr(self.pets, "on_" + id)(value)
        return super().act(id, value)

    # ---- Radar tab
    def page_radar(self, snap):
        c = P.Canvas(380, 380, PANEL)
        rows = self.draw_radar(c, snap)
        return [c.block(),
                P.row(P.button("zoom", "−", 1.5), P.button("zoom", "+", 1 / 1.5),
                      P.choice("mode", (("watched", "Watched"), ("all", "All")), self.cfg["mode"])),
                P.text("Nearby: click a name to watch / unwatch it", small=True),
                P.lines(rows, click="watch")]

    def on_zoom(self, factor):
        self.range = max(25, min(2000, self.range * float(factor)))

    def on_mode(self, value):
        self.cfg["mode"] = value
        self.save()

    def on_watch(self, name):
        self.toggle_watch(name)

    def page_pets(self, snap):
        return self.pets.page(snap)

    def draw_radar(self, c, snap):
        """Draws the radar on c -> the list lines [(text, colour, name to watch)]."""
        size = c.w
        mid = size / 2
        draw_cone(c, mid, mid, mid - 18, snap.get("cone"), fill="#2b3346")  # under the rings and pins
        scale = draw_rings(c, size, self.range)
        position = snap["position"]
        if not position:
            draw_text(c, mid, mid + 30, "waiting for your position\n(teleport)", "#b8c0cc", size=10, bold=False)
        soul = {m["id"]: pid for pid, m in self.pets.live(snap)} if position else {}
        spawn_rows = self.pets.draw_spots(c, snap, mid, scale, self.range) if position else []
        shown = self.visible(snap) if position else []
        ids = {m["id"] for *_, m in shown}
        shown += [monster_info(m["type"]) + (m,) for m in snap["monsters"] if m["id"] in soul and m["id"] not in ids]
        for name, level, rank, m in sorted(shown, key=lambda s: (s[3]["id"] in soul, self.watched(s[0]))):  # on top
            x, y, beyond = radar_point(position, m["xyz"], mid, scale, self.range)
            color, r = self.style(name, soul.get(m["id"]))
            if beyond:
                c.create_oval(x - r, y - r, x + r, y + r, outline=color, width=2)
            else:
                c.create_oval(x - r - 1, y - r - 1, x + r + 1, y + r + 1, fill="#000000", outline="")
                c.create_oval(x - r, y - r, x + r, y + r, fill=color, outline="")
        if position and self.cfg["me_on_top"]:  # you, above every monster dot
            draw_me(c, mid, mid, 6)
        if position and not shown and not spawn_rows:
            draw_text(c, mid, mid + 30, "no watched monsters nearby" if self.cfg["mode"] == "watched"
                      else "no monsters nearby", "#b8c0cc", size=10, bold=False)
        # nearby list: every named monster type in range, watched first, then nearest
        groups = {}
        for m in (snap["monsters"] if position else []):
            name, level, rank = monster_info(m["type"])
            if rank == "?" and not self.cfg["unnamed"]:
                continue
            d = math.dist(m["xyz"][:2], position[:2]) / 100
            g = groups.setdefault(name, [0, d, level, rank, soul.get(m["id"])])
            g[0] += 1
            g[1] = min(g[1], d)
        order = sorted(groups.items(), key=lambda kv: (kv[1][4] is None, not self.watched(kv[0]), kv[1][1]))
        lines = []
        for name, (count, d, level, rank, pid) in order:
            if pid is not None:  # drops a target pet's soul
                lines.append(("❀ %-24.24s x%-2d %4d m  → %s" % (name, count, d, self.pets.name(pid)),
                              self.pets.target_color(pid), name))
            else:
                lines.append(("%s %-24.24s x%-2d %4d m  L%-2s %s" % ("★" if self.watched(name) else "☆", name, count, d,
                                                                     level or "?", rank[:1]),
                              self.color(name) if self.watched(name) else MUTED, name))
        lines += [(text, color, None) for text, color in spawn_rows]  # soul monsters' nearest spawn points
        if not lines and position:
            lines.append(("No monsters in range.", MUTED, None))
        return lines[:14]

    # ---- Overlay
    def paint_overlay(self, overlay, c, snap):
        position = snap["position"]
        w = overlay.cfg["w"]
        self.pets.paint_spots(overlay, c, snap)
        soul = {m["id"] for _, m in self.pets.live(snap)}
        shown = [s for s in self.visible(snap) if s[3]["id"] not in soul]  # soul monsters: drawn by the pets below
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
        self.pets.paint_live(overlay, c, snap)
        if self.cfg["me_on_top"]:  # you, above every monster dot (the overlay's centre is your position)
            draw_me(c, w / 2, overlay.cfg["h"] / 2, 5)

    # ---- Settings tab
    def results(self):
        text = self.search.strip().lower()
        return [n for n in monster_names() if text in n.lower()][:200] if text else []

    def page_settings(self, snap):
        results = self.results()
        watch = self.cfg["watch"]
        return [P.heading("Monster settings", "Pick the monsters to watch; each gets a colour."),
                P.section("Find a monster"),
                P.field("search", self.search, "Monster name", width=320),
                P.listbox("result", [(("★ " if self.watched(n) else "   ") + n, None) for n in results],
                          self.result_sel, height=7, mono=False,
                          empty="(no monster called that)" if self.search.strip() else "Type a name above."),
                P.row(P.button("watch_result", "★ Watch", disabled=self.result_sel is None),
                      P.text("or double-click a name", small=True)),
                P.section("Watching"),
                P.listbox("watched", [("● " + n, self.color(n)) for n in watch], self.watch_sel, height=5, mono=False,
                          empty="Nothing yet. Search above, or click a name on the Radar tab."),
                P.row(P.button("unwatch", "Remove", disabled=self.watch_sel is None),
                      P.button("clear_watch", "Clear all", disabled=not watch, confirm="Stop watching every monster?")),
                P.section("Show"),
                P.toggle("flag_names", "Names on the overlay", self.cfg["names"]),
                P.toggle("flag_unnamed", "Include NPCs without a known name", self.cfg["unnamed"]),
                P.toggle("flag_alert", "Beep when a watched monster appears", self.cfg["alert"]),
                P.toggle("flag_me_on_top", "Your position on top (radar and overlay)", self.cfg["me_on_top"]),
                ] + self.pets.settings_blocks()

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
        self.pets.background(snap)
        souls = self.pets.new_live(snap)
        if souls and self.cfg["alert"] and time.time() - self.last_alert > 3:
            self.last_alert = time.time()
            self.app.engine.say("Soul monster: " + ", ".join(souls))
            play("system beep")
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

    def status(self, snap):
        souls = len(self.pets.live(snap))
        if not self.cfg["watch"]:
            text = "%d monsters" % len(snap["monsters"])
        else:
            text = "%d watched" % sum(1 for m in snap["monsters"] if monster_info(m["type"])[0] in self.cfg["watch"])
        return text + (" · %d soul monster%s" % (souls, "" if souls == 1 else "s") if souls else "") + " · " + self.pets.status()
