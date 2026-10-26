"""The World map's data (the page is assets/web/map.js, in its own window: App.open_map). The world map images
(data/map_tiles, served at /tiles/), every pet's soul monster spawns, hidden cubes and gathering nodes per map, and the
live part: where you are, your pets' progress, target pets and highlighted (= watched in Gathering) resources."""
from .common import ROOT, load_settings, save_settings
from .game import GATHER_GROUPS, SOUL_STAGES, MapData, map_name
from .features.pets import GENUS_COLORS
from .characters import HOME_MAP

TILES = ROOT / "data" / "map_tiles"
MAX_LEVEL, SOUL_CAP = len(SOUL_STAGES), sum(SOUL_STAGES)
GROUP_ORDER = [group for group, _ in GATHER_GROUPS]
DEFAULT = dict(layers=["Pets"], genus="All types", unfinished=True, not_home=False, mode="Pets", map=None, open=False)


class WorldMap:
    def __init__(self, app):
        self.app = app
        settings = load_settings()
        self.cfg = dict(DEFAULT, **settings.get("world_map", settings.get("pet_map", {})))  # was the Pet map
        self.cfg.pop("geometry", None)  # the old Tk window's
        self.keys = sorted((p.name for p in TILES.iterdir() if p.is_dir()), key=map_name) if TILES.exists() else []
        self.cache = {}

    @property
    def pets(self):
        monsters = self.app.features.get("Monsters")
        return monsters.pets if monsters else None

    @property
    def gathering(self):
        return self.app.features.get("Gathering")

    def save(self):
        save_settings(dict(load_settings(), world_map=self.cfg))

    def current_map(self):
        """The map you're on: the app's (picked / detected), else the one the pets worked out from soul monsters."""
        return self.pets.map_key() if self.pets else self.app.map_key()

    def initial_map(self):
        key = self.current_map() or self.cfg.get("map")
        return key if key in self.keys else ("verteron" if "verteron" in self.keys else (self.keys or [None])[0])

    # ---- one map's fixed data (cached): pets' spawn points, monsters, resources, cubes
    def data(self, key):
        if key not in self.keys:
            raise ValueError("no map %s" % key)
        if key in self.cache:
            return self.cache[key]
        md, pets = self.app.mapdata, self.pets
        out = dict(map=key, name=map_name(key), keys=[[k, map_name(k)] for k in self.keys], pets=[], monsters={},
                   resources=[], cubes=[list(p) for p in md.cubes.get(key, ())], groups=GROUP_ORDER,
                   genus_colors=GENUS_COLORS)
        if pets:
            spawns = pets.data.spawns.get(key, {})
            for t, m in spawns.items():
                out["monsters"][str(t)] = dict(name=m["name"], level=m["level"], count=len(m["points"]))
            for pid in pets.data.collection:
                p = pets.data.pets[pid]
                points = [[x, y, t] for s in p.get("souls", ()) for t in s["types"] if t in spawns
                          for x, y in spawns[t]["points"]]
                if points:
                    out["pets"].append(dict(id=pid, name=p["name"], genus=p["genus"], points=points,
                                            farm=sorted(pets.data.farm_maps(pid)),  # every farm map with its souls
                                            souls=[dict(monster=s["monster"], level=s["level"], chance=s["chance"],
                                                        types=s["types"]) for s in p.get("souls", ())]))
        order = {g: i for i, g in enumerate(GROUP_ORDER)}
        for name, points in md.maps.get(key, {}).get("points", {}).items():
            elsewhere = ", ".join("%s %d" % (map_name(k), len(m["points"][name])) for k, m in sorted(md.maps.items())
                                  if k != key and name in m["points"])
            out["resources"].append(dict(name=name, group=md.group_of(name) or "Other", points=[list(p) for p in points],
                                         elsewhere=elsewhere))
        out["resources"].sort(key=lambda r: (order.get(r["group"], 99), r["name"]))
        self.cache[key] = out
        return out

    # ---- the live part, polled by the page
    def state(self):
        pets, gathering, snap = self.pets, self.gathering, self.app.snapshot()
        progress, targets, colors, synced = {}, [], {}, False
        if pets:
            progress = {str(pid): list(pets.pet(pid)) for pid in pets.data.collection}
            targets = list(pets.cfg["targets"])
            colors = {str(pid): pets.target_color(pid) for pid in targets}
            synced = bool(pets.levels or pets.synced)
        watch = list(gathering.cfg["watch"]) if gathering else []
        here = self.current_map()
        return dict(cfg=self.cfg, current=here, initial=self.initial_map(), home=self.home(),
                    position=list(MapData.to_px(snap["position"])) if snap["position"] else None,
                    progress=progress, targets=targets, target_colors=colors, synced=synced,
                    watch=watch, watch_colors={n: gathering.color(n) for n in watch} if gathering else {},
                    max_level=MAX_LEVEL, soul_cap=SOUL_CAP, topmost=self.app.topmost.get(), focus=self.app.map_focus)

    def home(self):
        """The playing character's own land, where its pet souls are farmed (by its faction, Settings → Characters):
        dict(map, name, char), or dict(map=None, char) while the faction isn't known, or None before a login."""
        chars = getattr(self.app, "characters", None)
        name = chars.playing if chars else None
        if not name:
            return None
        key = HOME_MAP.get(chars.faction_of(name))
        return dict(map=key, name=map_name(key) if key else None, char=name)

    # ---- what the page does
    def set_cfg(self, values):
        for key in ("layers", "genus", "unfinished", "not_home", "mode", "map"):
            if key in values:
                self.cfg[key] = values[key]
        self.save()

    def toggle_target(self, pid):
        if self.pets:
            self.pets.toggle_target(int(pid))

    def toggle_watch(self, name):
        if self.gathering:
            self.gathering.toggle_watch(name)
