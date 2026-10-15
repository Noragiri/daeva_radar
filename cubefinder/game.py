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
def own_position(data, own):
    if own is None or data[:2] not in (b"*8", b"+8"):
        return None
    try:
        entity, p = varint(data, 2)
    except ValueError:
        return None
    # Your position is the last 12 bytes of every '*8' / '+8' about you. Their length varies (40, 41, 43, 44, 45
    # bytes after the id seen live on 2026-10-14; all agreed to the metre whenever they arrived together), so
    # don't fix the length: accepting only 40 / 43 froze your position while walking.
    if entity != own or len(data) - p < 36:
        return None
    return xyz(data, len(data) - 12)
class Recorder:
    """--record FILE: every decoded game message as a JSON line {"t": time, "hex": ...}, for working out
    new message layouts offline. Plain (not gzip) so a killed process still leaves a readable file."""
    LIMIT = 500_000

    def __init__(self, path):
        self.file = open(path, "a", encoding="utf-8")
        self.count = 0

    def write(self, batch, when):
        if self.count >= self.LIMIT:
            return
        import json
        for data in batch:
            self.file.write(json.dumps({"t": round(when, 3), "hex": bytes(data).hex()}) + "\n")
        self.count += len(batch)
        self.file.flush()
class MapData:
    """Local copy of map markers (data/maps/<map>.json, written once by dev/fetch_map_data.py; credit
    Shugo.GG). Names live nodes by position, works out which map you're on, and lists nodes the game hasn't
    announced yet. Map pixels on a 4096 px map: pixel = game units / 200 + 2048 (checked live)."""
    CELL = 16  # px grid for lookups

    def __init__(self, folder=None):
        import json
        self.maps = {}  # key -> {"grid": {(cx, cy): [(x, y, name, item)]}, "points": {name: [(x, y)]}}
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
            if points:
                self.maps[data.get("map", path.stem)] = {"grid": grid, "points": points}

    @staticmethod
    def to_px(point):
        return point[0] / 200 + 2048, point[1] / 200 + 2048

    @staticmethod
    def to_world(px, py, z=0.0):
        return [(px - 2048) * 200, (py - 2048) * 200, z]

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
        self.cubes = {}
        self.monsters = {}  # entity -> dict(type, xyz, seen)
        self.track_cubes = self.track_monsters = True  # a feature switched off: its messages aren't parsed
        self.nodes = {}  # entity -> dict(type, xyz, seen): '46' objects that aren't cubes (gathering nodes...)
        self.track_nodes = True
        # Who you are. Learned from: the login message ('33 36'), the 'own state' message ('4a 36', frequent on
        # some characters, rare on others), the first mover landing on a teleport spot, or a remembered id.
        self.characters = {}  # id -> name, your characters seen logging in (saved by the app)
        self.on_character = None  # callback(id, name) when a new character of yours is seen
        self.own_name = None
        self.teleport = None  # (point, when) of the last teleport, until you're identified by it
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

    def set_position(self, point, when):
        if when < self.position_time:
            return
        self.position, self.position_time = list(point), when

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
        self.own = own
        if own not in self.characters:  # remember it, so next time you're recognised on your first move
            self.characters[own] = None  # name unknown until a login message is seen
            if self.on_character:
                self.on_character(own, None)
        self.own_name = self.characters.get(own)

    def identify(self, data, when):
        """Learn who you are from movement: the first mover landing on the spot you just teleported to (live:
        6 of 6 teleports, 0 cm away, within 0.06 s), or, while unknown, a character of yours seen before
        (ids stay the same across logins)."""
        try:
            entity, p = varint(data, 2)
        except ValueError:
            return
        if entity == self.own or len(data) - p < 36:
            return
        if self.teleport and when - self.teleport[1] <= 10:
            point = xyz(data, len(data) - 12)
            if point and math.dist(point[:2], self.teleport[0][:2]) < 300 and entity not in self.monsters:
                self.teleport = None
                self.set_own(entity)
                return
        if self.own is None and entity in self.characters:
            self.set_own(entity)

    def _feed(self, batch, when):
        self.messages += len(batch)
        for data in batch:  # who am I?
            kind = data[:2]
            if kind == b"36":  # login: you; also remember this character (with its name) for next time
                try:
                    self.set_own(varint(data, 2)[0])
                except ValueError:
                    continue
                login = login_name(data)
                if login:
                    entity, name = login
                    if self.characters.get(entity) != name:
                        self.characters[entity] = name
                        if self.on_character:
                            self.on_character(entity, name)
                    self.own_name = name
            elif kind == b"J6":
                try:
                    self.set_own(varint(data, 2)[0])
                except ValueError:
                    pass
            elif kind == b"#6" and len(data) == 26 and data[2:8] == bytes(6):
                point = xyz(data, 8)
                if point:
                    self.teleport = (point, when)
            elif kind in (b"*8", b"+8"):
                self.identify(data, when)
        for data in batch:
            kind = data[:2]
            if kind == b"#6" and len(data) == 26 and data[2:8] == bytes(6):  # teleport / zone-in
                point = xyz(data, 8)
                if point is not None:
                    self.clear()
                    self.set_position(point, when)
            point = own_position(data, self.own)
            if point is not None:
                self.set_position(point, when)
            if self.log:
                self.diagnose(data)
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
            monsters = []
            if self.position:
                for entity in list(self.monsters):
                    item = self.monsters[entity]
                    if math.dist(item["xyz"][:2], self.position[:2]) > MONSTER_RANGE_M * 100:
                        del self.monsters[entity]  # out of range: the server stops telling us about it
                        continue
                    monsters.append(dict(id=entity, type=item["type"], xyz=list(item["xyz"]),
                                         age=round(max(0, now - item["seen"]))))
            nodes = []
            if self.position:
                for entity in list(self.nodes):
                    item = self.nodes[entity]
                    if math.dist(item["xyz"][:2], self.position[:2]) > NODE_RANGE_M * 100:
                        del self.nodes[entity]
                        continue
                    nodes.append(dict(id=entity, type=item["type"], xyz=list(item["xyz"])))
            return dict(own=self.own, own_name=self.own_name, position=list(self.position) if self.position else None,
                        nodes=nodes,
                        position_age=max(0, now - self.position_time) if self.position else None,
                        cubes=cubes, monsters=monsters, kills=self.kills, messages=self.messages,
                        detections=self.detections, last_detection=self.last_detection)
