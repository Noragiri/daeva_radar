"""Game messages -> what's around you: your character and position, cubes, monsters, gathering nodes.
Also the local map data (data/maps) used to name gathering nodes."""
import math
import struct
import threading
import time

from .common import ROOT
from .protocol import varint


TEMPLATES = {
    1200039,  # Verteron cube (confirmed live: opened 2026-10-14)
    1301199, 1301200,  # Altgard Hidden Cube (common, rare-unconfirmed)
    1301242, 1301243, 1301244, 1301245,  # Morheim cubes (from the original's notes, unconfirmed)
}
MAP_SIZE = 8192
def xyz(data, offset):
    if offset < 0 or offset + 12 > len(data):
        return None
    point = struct.unpack_from("<fff", data, offset)
    if not all(math.isfinite(v) and abs(v) < 1e6 for v in point):
        return None
    if any(0 < abs(v) < 1e-6 for v in point):
        return None
    return list(point)
def project(point):
    """World units (cm) -> Altgard map pixels (1 px = 1 m)."""
    return [point[0] / 100 + 4096, point[1] / 100 + 4096]
def object_intro(data):
    """'46' object spawn -> (entity, template, point), any template; None if not that layout."""
    if data[:2] != b"46":
        return None
    try:
        entity, p = varint(data, 2)
    except ValueError:
        return None
    if len(data) < p + 18 or data[p:p + 2] != b"\x04\x00":
        return None
    point = xyz(data, p + 6)
    if point is None:
        return None
    return entity, int.from_bytes(data[p + 2:p + 6], "little"), point
def cube_intro(data):
    intro = object_intro(data)
    if intro is None or intro[1] not in TEMPLATES:
        return None
    return intro[0], intro[2]
POSITION_MODE = "any"  # "old": only the old version's exact layouts (--position old, to compare in game)


def own_position(data, own):
    if own is None or data[:2] not in (b"*8", b"+8"):
        return None
    try:
        entity, p = varint(data, 2)
    except ValueError:
        return None
    if entity != own:
        return None
    if POSITION_MODE == "old":  # the old version's exact layouts only ('*8' 40 bytes, '+8' 43 bytes after the id)
        offset = p + (28 if data[:2] == b"*8" else 31)
        return xyz(data, offset) if len(data) == offset + 12 else None
    # default: the last 12 bytes of every '*8' / '+8' about you, whatever its length (39-45 bytes seen; where both
    # kinds arrived together they agreed within ~1.5 m). Later sessions sent no 40/43-byte messages at all.
    if len(data) - p < 36:
        return None
    return xyz(data, len(data) - 12)
class Recorder:
    """--record FILE: every decoded game message as a JSON line {"t": time, "hex": ...}, for working out
    new message layouts offline. Plain (not gzip) so a killed process still leaves a readable file."""
    LIMIT = 500_000

    def __init__(self, path):
        self.file = open(path, "a", encoding="utf-8")
        self.count = 0

    def write(self, batch, when, direction="s"):
        """direction: "s" = from the server, "c" = from your client."""
        if self.count >= self.LIMIT:
            return
        import json
        for data in batch:
            self.file.write(json.dumps({"t": round(when, 3), "d": direction, "hex": bytes(data).hex()}) + "\n")
        self.count += len(batch)
        self.file.flush()
# Gathering groups by the first five digits of the item id (map data, 2026-10-15): 61061 Odyle, 61062 ores
# (Orichalcum), 61065 logs, 61066 gemstones, 61067 herbs (Aria, Azpha...), 61068 cooking ingredients.
GATHER_GROUPS = (("Ore", (61062,)), ("Odyle", (61061,)), ("Gem", (61066,)), ("Log", (61065,)), ("Herb", (61067,)),
                 ("Ingredient", (61068,)))
class MapData:
    """Local copy of map markers (data/maps/<map>.json, written once by dev/fetch_map_data.py; credit
    Shugo.GG). Names live nodes by position, works out which map you're on, and lists nodes the game hasn't
    announced yet. Map pixels on a 4096 px map: pixel = game units / 200 + 2048 (checked live)."""
    CELL = 16  # px grid for lookups

    def __init__(self, folder=None):
        import json
        self.maps = {}  # key -> {"grid": {(cx, cy): [(x, y, name, item)]}, "points": {name: [(x, y)]}}
        self.items = {}  # resource name -> item id (its group is the id's first five digits, see GATHER_GROUPS)
        folder = folder or ROOT / "data" / "maps"
        for path in sorted(folder.glob("*.json")) if folder.exists() else []:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            grid, points = {}, {}
            for cat in data.get("categories", {}).values():
                if not cat.get("item"):
                    continue  # gathering resources only (hidden cubes are their own feature)
                for x, y in cat["points"]:
                    grid.setdefault((x // self.CELL, y // self.CELL), []).append((x, y, cat["name"], cat["item"]))
                    points.setdefault(cat["name"], []).append((x, y))
                self.items[cat["name"]] = cat["item"]
            if points:
                self.maps[data.get("map", path.stem)] = {"grid": grid, "points": points}

    @staticmethod
    def to_px(point):
        return point[0] / 200 + 2048, point[1] / 200 + 2048

    @staticmethod
    def to_world(px, py, z=0.0):
        return [(px - 2048) * 200, (py - 2048) * 200, z]

    def group_of(self, name):
        """The gathering group ('Ore', 'Gem', ...) of a resource, from its item id; None if unknown."""
        item = self.items.get(name)
        if item is None:
            return None
        return next((group for group, prefixes in GATHER_GROUPS if item // 10000 in prefixes), None)

    def names(self):
        return sorted({n for m in self.maps.values() for n in m["points"]})

    def match(self, key, point, radius_px=4):
        """The resource marker at a game position on map `key` -> (name, item) or None."""
        m = self.maps.get(key)
        if not m:
            return None
        px, py = self.to_px(point)
        best = None
        for gx in range(int(px // self.CELL) - 1, int(px // self.CELL) + 2):
            for gy in range(int(py // self.CELL) - 1, int(py // self.CELL) + 2):
                for x, y, name, item in m["grid"].get((gx, gy), ()):
                    d = math.hypot(px - x, py - y)
                    if d <= radius_px and (best is None or d < best[0]):
                        best = (d, name, item)
        return best[1:] if best else None

    def which_map(self, points):
        """The map whose resource markers line up with these live node positions, or None if unclear."""
        votes = {key: sum(1 for p in points if self.match(key, p, 3)) for key in self.maps}
        if not votes:
            return None
        ranked = sorted(votes.items(), key=lambda kv: -kv[1])
        best, hits = ranked[0]
        runner = ranked[1][1] if len(ranked) > 1 else 0
        return best if hits >= 2 and hits >= 2 * runner + 1 else None

    def nearby(self, key, names, centre, radius_m):
        """Marker positions (game units) of these resources within radius of centre on map key."""
        m = self.maps.get(key)
        if not m:
            return []
        cx, cy = self.to_px(centre)
        r = radius_m / 2  # metres -> px
        out = []
        for name in names:
            for x, y in m["points"].get(name, ()):
                if abs(x - cx) <= r and abs(y - cy) <= r and math.hypot(x - cx, y - cy) <= r:
                    out.append((name, self.to_world(x, y, centre[2])))
        return out
_MONSTERS = None
def monster_info(type_id):
    """Monster type id -> (name, level, rank) from data/monsters.json; unknown types get a placeholder."""
    global _MONSTERS
    if _MONSTERS is None:
        import json
        try:
            path = ROOT / "data" / "monsters.json"
            _MONSTERS = {int(k): tuple(v) for k, v in json.loads(path.read_text(encoding="utf-8")).items()}
        except (OSError, ValueError):
            _MONSTERS = {}
    return _MONSTERS.get(type_id, ("Monster %d" % type_id, 0, "?"))
def monster_names():
    """Every known monster name, sorted (for the watch-list search)."""
    monster_info(0)
    return sorted({v[0] for v in _MONSTERS.values()})
def monster_intro(data):
    """'41 36' a monster in range: id (LEB128) | 3 bytes | type u32 | 2 bytes | x, y, z floats | ...
    (live capture, 2026-10-14: positions 11-29 m from the player, names matched the game)."""
    if data[:2] != b"A6":
        return None
    try:
        entity, p = varint(data, 2)
    except ValueError:
        return None
    if len(data) < p + 21:
        return None
    point = xyz(data, p + 9)
    if point is None:
        return None
    return entity, int.from_bytes(data[p + 3:p + 7], "little"), point
def entity_move(data):
    """'*8' / '+8' movement of any entity: id (LEB128) | ... | x, y, z floats as the last 12 bytes.
    For monsters that's where they are or are heading (a chasing monster's point is the player)."""
    if data[:2] not in (b"*8", b"+8"):
        return None
    try:
        entity, p = varint(data, 2)
    except ValueError:
        return None
    if len(data) < p + 20:
        return None
    point = xyz(data, len(data) - 12)
    return (entity, point) if point else None
def pet_move(data):
    """Your summoned pet's movement (live 2026-10-15): '28 37' starts / '29 37' moves: id (LEB128) | 1 byte |
    x, y, z | velocity x, y, z (units/s) | ...; '1a 37' stops: id | 2 bytes | x, y, z.
    -> (entity, point, velocity) or None."""
    kind = data[:2]
    if kind not in (b"(7", b")7", b"\x1a7"):
        return None
    try:
        entity, p = varint(data, 2)
    except ValueError:
        return None
    if kind == b"\x1a7":
        point = xyz(data, p + 2)
        return (entity, point, (0.0, 0.0, 0.0)) if point else None
    point = xyz(data, p + 1)
    if point is None or len(data) < p + 25:
        return None
    velocity = struct.unpack_from("<fff", data, p + 13)
    if not all(math.isfinite(v) and abs(v) < 1e5 for v in velocity):
        return None
    return entity, point, velocity
PET_SPAWN_CM = 500  # your pet appears beside you (0-2.5 m seen): within this, right after one of your skills
PET_MARK = b"\x1f\x10"  # an 'A6' spawn of a summoned pet (anyone's) starts with this after the id; monsters don't
def is_pet_intro(data):
    """Is this 'A6' spawn a summoned pet's? (5 of 5 pets, 4 kinds, live 2026-10-15: '1f 10 00' after the id;
    monsters had '04 20 00', '0c 20 00', '0c 2c 00', '0d 20 00'.)"""
    try:
        entity, p = varint(data, 2)
    except ValueError:
        return False
    return data[p:p + 2] == PET_MARK
NODE_RANGE_M = 200  # objects further than this are dropped (they leave range silently too)
MONSTER_RANGE_M = 150  # monsters further than this from you are dropped (no "left range" message is sent)
def login_name(data):
    """'33 36' your character at login: id (LEB128) | 4 bytes | 1 byte | name (length byte + UTF-8)."""
    try:
        entity, p = varint(data, 2)
        n = data[p + 5]
        return entity, bytes(data[p + 6:p + 6 + n]).decode("utf-8")
    except (ValueError, IndexError, UnicodeDecodeError):
        return None
class Tracker:
    def __init__(self):
        self.lock = threading.Lock()
        self.own = None
        self.position = None
        self.position_time = 0
        self.position_source = None  # the message that last set your position (for the debug window)
        self.position_updates = 0
        self.estimate = None  # your position followed between packets (the app's, via your pet), for range checks
        self.cubes = {}
        self.monsters = {}  # entity -> dict(type, xyz, seen)
        self.track_cubes = self.track_monsters = True  # a feature switched off: its messages aren't parsed
        self.nodes = {}  # entity -> dict(type, xyz, seen): '46' objects that aren't cubes (gathering nodes...)
        self.track_nodes = True
        # Who you are: only the login message ('33 36') and the 'own state' message ('4a 36'), as in the version
        # that identified every character correctly. (Guessing from teleports or remembered ids was unsafe: other
        # players land on the same teleport spots, and ids may be reused between sessions.)
        self.own_name = None  # display only, from the login message
        self.client_messages = 0  # messages from your client to the server
        # Your summoned pet: the server sends its movement while yours goes unsent, and it follows you (about
        # 1-1.5 s behind). Recognised as the 'A6' spawn on top of you right after one of your skills ('02 38');
        # its type is then remembered, so it's also recognised when it reappears with you (teleport, relog).
        self.pet = None  # dict(id, type, xyz, velocity, time) while it's out
        self.pet_types = set()
        self.last_cast = 0.0  # when you last used a skill
        self.kills = 0
        self.messages = 0
        self.detections = 0
        self.last_detection = None
        self.log = None  # set for --diagnose: report objects you interact with
        self.recorder = None  # set for --record: raw messages to a file
        self.objects = {}  # entity -> (template, point) for every spawned object
        self.reported = set()

    def clear(self):
        self.cubes.clear()
        self.monsters.clear()
        self.nodes.clear()
        self.position = None
        self.position_time = 0
        self.estimate = None
        self.pet = None

    def set_position(self, point, when, source=None):
        if when < self.position_time:
            return
        self.position, self.position_time = list(point), when
        self.position_source = source
        self.position_updates += 1

    def diagnose(self, data):
        """Log '56' events of objects within 10 m of you, once per (object, event)."""
        intro = object_intro(data)
        if intro:
            self.objects[intro[0]] = intro[1:]
            if len(self.objects) > 20000:
                self.objects.pop(next(iter(self.objects)))
            return
        if data[:2] not in (b"56", b"\x05\x8d") or not self.position:
            return
        try:
            entity, p = varint(data, 2)
        except ValueError:
            return
        obj = self.objects.get(entity)
        if obj is None:
            return
        dist = math.dist(obj[1], self.position) / 100
        key = (entity, data[:2], bytes(data[p:p + 8]))
        if dist <= 10 and key not in self.reported:
            self.reported.add(key)
            self.log("object event: id %d template %d at %.1f m, message %s %s" % (
                entity, obj[0], dist, data[:2].hex(), bytes(data[p:]).hex()))

    def is_pet_spawn(self, data, spawn, when):
        """Is this 'A6' spawn your pet? A pet's spawn beside you right after a skill of yours (the summon), or
        a type already known as your pet appearing near you (it comes along through teleports)."""
        entity, type_id, point = spawn
        if when - self.position_time > 3 or not is_pet_intro(data):
            return False
        dist = math.dist(point[:2], self.position[:2])
        if type_id in self.pet_types:
            return dist < 1000
        return dist < PET_SPAWN_CM and 0 <= when - self.last_cast <= 2

    def feed_client(self, batch, when):
        """Messages your own client sends to the server (recorded for now; your movement is in here)."""
        self.client_messages += len(batch)
        if self.recorder:
            self.recorder.write(batch, when, "c")

    def feed(self, batch, when):
        if self.recorder:
            self.recorder.write(batch, when)
        with self.lock:
            self._feed(batch, when)

    def set_own(self, own):
        """You are `own`. Switching from another character drops everything about the old one."""
        if own == self.own:
            return
        if self.own is not None:
            self.clear()
            self.own_name = None
        self.own = own

    def _feed(self, batch, when):
        self.messages += len(batch)
        for data in batch:  # who am I?
            kind = data[:2]
            if kind == b"36":  # login: you (and your character's name, for display)
                try:
                    self.set_own(varint(data, 2)[0])
                except ValueError:
                    continue
                login = login_name(data)
                if login:
                    self.own_name = login[1]
            elif kind == b"J6":
                try:
                    self.set_own(varint(data, 2)[0])
                except ValueError:
                    pass

        for data in batch:
            kind = data[:2]
            if kind == b"#6" and len(data) == 26 and data[2:8] == bytes(6):  # teleport / zone-in
                point = xyz(data, 8)
                if point is not None:
                    self.clear()
                    self.set_position(point, when, "#6 teleport")
            point = own_position(data, self.own)
            if point is not None:
                self.set_position(point, when, "%s move, %d bytes" % (kind.decode("latin-1"), len(data)))
            if self.log:
                self.diagnose(data)
            if kind == b"\x028" and self.own is not None:  # a skill used
                try:
                    if varint(data, 2)[0] == self.own:
                        self.last_cast = when
                except ValueError:
                    pass
            if kind == b"A6" and self.position:
                spawn = monster_intro(data)
                if spawn and self.is_pet_spawn(data, spawn, when):
                    entity, type_id, point = spawn
                    self.pet_types.add(type_id)
                    self.pet = dict(id=entity, type=type_id, xyz=point, velocity=(0.0, 0.0, 0.0), time=when)
                    self.monsters.pop(entity, None)
                    continue
            if self.pet:
                move = pet_move(data)
                if move and move[0] == self.pet["id"]:
                    self.pet.update(xyz=move[1], velocity=move[2], time=when)
                    continue
                if kind in (b"B6", b"\x04\x8d"):  # dismissed / gone
                    try:
                        if varint(data, 2)[0] == self.pet["id"]:
                            self.pet = None
                    except ValueError:
                        pass
            if kind in (b"A6", b"*8", b"+8", b"\x04\x8d") and not self.track_monsters:
                continue
            if kind == b"A6":
                spawn = monster_intro(data)
                if spawn:
                    entity, type_id, point = spawn
                    self.monsters[entity] = dict(id=entity, type=type_id, xyz=point, seen=when)
                    if len(self.monsters) > 3000:
                        del self.monsters[min(self.monsters, key=lambda i: self.monsters[i]["seen"])]
                continue
            if kind in (b"*8", b"+8") and self.monsters:
                move = entity_move(data)
                if move and move[0] in self.monsters:
                    item = self.monsters[move[0]]
                    if math.dist(move[1][:2], item["xyz"][:2]) < 30000:  # a jump over 300 m is not a move
                        item.update(xyz=move[1], seen=when)
                continue
            if kind == b"\x04\x8d":  # something died
                try:
                    if self.monsters.pop(varint(data, 2)[0], None):
                        self.kills += 1
                except ValueError:
                    pass
                continue
            if self.track_nodes and kind in (b"46", b"56", b"\x05\x8d"):
                if kind == b"46":
                    obj = object_intro(data)
                    if obj and obj[1] not in TEMPLATES:
                        self.nodes[obj[0]] = dict(id=obj[0], type=obj[1], xyz=obj[2], seen=when)
                        if len(self.nodes) > 3000:
                            del self.nodes[min(self.nodes, key=lambda i: self.nodes[i]["seen"])]
                else:  # left range ('56' 01 01 00 / '05 8d') or used up ('56' 04 03 00): gone
                    try:
                        entity, p = varint(data, 2)
                    except ValueError:
                        entity = None
                    if entity in self.nodes and (kind == b"\x05\x8d" or bytes(data[p:]) in (b"\x01\x01\x00", b"\x04\x03\x00")):
                        del self.nodes[entity]
            if not self.track_cubes:
                continue
            intro = cube_intro(data)
            if intro:
                entity, point = intro
                old = self.cubes.get(entity)
                fresh = old is None or old["xyz"] != point or old["status"] == "opened"
                if fresh:
                    self.detections += 1
                    self.last_detection = entity
                first = old["first"] if not fresh else when
                self.cubes[entity] = dict(id=entity, xyz=point, first=first, seen=when, status="nearby")
                if len(self.cubes) > 500:
                    del self.cubes[min(self.cubes, key=lambda i: self.cubes[i]["seen"])]
                continue
            if kind not in (b"56", b"\x05\x8d"):
                continue
            try:
                entity, p = varint(data, 2)
            except ValueError:
                continue
            item = self.cubes.get(entity)
            if item is None:
                continue
            if kind == b"56" and data[p:] == b"\x04\x03\x00":
                item.update(status="opened", seen=when)
            elif item["status"] != "opened" and (kind == b"\x05\x8d" or data[p:] == b"\x01\x01\x00"):
                item.update(status="last seen", seen=when)

    def snapshot(self, now=None):
        now = time.time() if now is None else now
        with self.lock:
            cubes = []
            for item in self.cubes.values():
                age = max(0, now - item["seen"])
                if age > 1200:
                    continue
                status = "last seen" if item["status"] == "nearby" and age > 60 else item["status"]
                cubes.append(dict(id=item["id"], xyz=list(item["xyz"]), status=status, age=round(age)))
            here = self.estimate or self.position
            monsters = []
            if here:
                for entity in list(self.monsters):
                    item = self.monsters[entity]
                    if math.dist(item["xyz"][:2], here[:2]) > MONSTER_RANGE_M * 100:
                        del self.monsters[entity]  # out of range: the server stops telling us about it
                        continue
                    monsters.append(dict(id=entity, type=item["type"], xyz=list(item["xyz"]),
                                         age=round(max(0, now - item["seen"]))))
            nodes = []
            if here:
                for entity in list(self.nodes):
                    item = self.nodes[entity]
                    if math.dist(item["xyz"][:2], here[:2]) > NODE_RANGE_M * 100:
                        del self.nodes[entity]
                        continue
                    nodes.append(dict(id=entity, type=item["type"], xyz=list(item["xyz"])))
            return dict(own=self.own, own_name=self.own_name, position=list(self.position) if self.position else None,
                        nodes=nodes,
                        position_age=max(0, now - self.position_time) if self.position else None,
                        position_source=self.position_source, position_updates=self.position_updates,
                        position_time=self.position_time if self.position else None,
                        pet=dict(self.pet, xyz=list(self.pet["xyz"])) if self.pet else None,
                        cubes=cubes, monsters=monsters, kills=self.kills, messages=self.messages,
                        detections=self.detections, last_detection=self.last_detection)
