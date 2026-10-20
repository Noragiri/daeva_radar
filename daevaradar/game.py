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
    """'46' object spawn -> (entity, template, point), any template; None if not that layout.
    entity (LEB128) | flags 04 00 (cubes, gathering nodes) or 0c 00 (feathers) | template u32 | xyz f32."""
    if data[:2] != b"46":
        return None
    try:
        entity, p = varint(data, 2)
    except ValueError:
        return None
    if len(data) < p + 18 or data[p:p + 2] not in (b"\x04\x00", b"\x0c\x00"):  # 0c: Empyrean Traces (2026-10-15)
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
MAP_NAMES = {"verteron": "Verteron", "altgard": "Altgard", "reshanta-lower": "Lower Reshanta", "reshanta-middle": "Middle Reshanta",
             "reshanta-upper": "Upper Reshanta"}
def map_name(key):
    return MAP_NAMES.get(key, key.replace("-", " ").title())
class MapData:
    """Local copy of map markers (data/maps/<map>.json, written once by dev/fetch_map_data.py; credit
    Shugo.GG). Names live nodes by position, works out which map you're on, and lists nodes the game hasn't
    announced yet, and hidden-cube spawn points. Map pixels on a 4096 px map: pixel = game units / 200 + 2048
    (checked live)."""
    CELL = 16  # px grid for lookups

    def __init__(self, folder=None):
        import json
        self.maps = {}  # key -> {"grid": {(cx, cy): [(x, y, name, item)]}, "points": {name: [(x, y)]}}
        self.items = {}  # resource name -> item id (its group is the id's first five digits, see GATHER_GROUPS)
        self.cubes = {}  # key -> [(x, y)] hidden-cube spawn points
        self.traces = {}  # key -> [(x, y)] Empyrean Trace (feather) spots
        folder = folder or ROOT / "data" / "maps"
        for path in sorted(folder.glob("*.json")) if folder.exists() else []:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            grid, points = {}, {}
            for cat_id, cat in data.get("categories", {}).items():
                if cat_id == "HiddenCube":
                    self.cubes[data.get("map", path.stem)] = [tuple(p) for p in cat["points"]]
                if cat_id == "EmpyreanTrace":
                    self.traces[data.get("map", path.stem)] = [tuple(p) for p in cat["points"]]
                if not cat.get("item"):
                    continue  # gathering resources only
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

    def keys(self):
        """Every map with data: gathering nodes, cube spawn points or feather spots."""
        return sorted(set(self.maps) | set(self.cubes) | set(self.traces), key=map_name)

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

    def cube_map(self, points):
        """The map whose cube spawn points line up with these live cube positions, or None if unclear."""
        votes = {}
        for key, spawns in self.cubes.items():
            px = [self.to_px(p) for p in points]
            votes[key] = sum(1 for x, y in px if any(math.hypot(x - sx, y - sy) <= 3 for sx, sy in spawns))
        ranked = sorted(votes.items(), key=lambda kv: -kv[1])
        if not ranked:
            return None
        best, hits = ranked[0]
        runner = ranked[1][1] if len(ranked) > 1 else 0
        return best if hits >= 2 and hits >= 2 * runner + 1 else None  # one cube near a spawn can be chance

    def cubes_nearby(self, key, centre, radius_m):
        """Hidden-cube spawn points (game units, at your height) within radius of centre on map key."""
        return [self.to_world(x, y, centre[2]) for x, y in self.spots_near(self.cubes.get(key, ()), centre, radius_m)]

    def traces_nearby(self, key, centre, radius_m):
        """Empyrean Trace spots within radius of centre on map key -> [((x, y) map px, world point)]."""
        return [((x, y), self.to_world(x, y, centre[2]))
                for x, y in self.spots_near(self.traces.get(key, ()), centre, radius_m)]

    def trace_at(self, key, point, radius_px=3):
        """The Empyrean Trace spot (map px) at a game position on map key, or None."""
        px, py = self.to_px(point)
        best = min(self.traces.get(key, ()), key=lambda s: math.hypot(s[0] - px, s[1] - py), default=None)
        return best if best and math.hypot(best[0] - px, best[1] - py) <= radius_px else None

    def spots_near(self, spots, centre, radius_m):
        cx, cy = self.to_px(centre)
        r = radius_m / 2  # metres -> px
        return [(x, y) for x, y in spots if abs(x - cx) <= r and abs(y - cy) <= r and math.hypot(x - cx, y - cy) <= r]
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
PET_MARK = b"\x1f\x10"  # an 'A6' spawn of a summoned pet (anyone's) starts with this after the id; monsters don't
                       # (summoned pets aren't monsters: they're left off the monster list)
def is_pet_intro(data):
    """Is this 'A6' spawn a summoned pet's? (5 of 5 pets, 4 kinds, live 2026-10-15: '1f 10 00' after the id;
    monsters had '04 20 00', '0c 20 00', '0c 2c 00', '0d 20 00'.)"""
    try:
        entity, p = varint(data, 2)
    except ValueError:
        return False
    return data[p:p + 2] == PET_MARK
SOUL_STAGES = (5, 25, 75)  # pet souls per stage: unlock (Lv1), Lv2, Lv3 (the counter restarts at each level)
def pet_collection(data):
    """'00 90' your pet collection (account-wide; sent when you enter the world, live 2026-10-15):
    u32 | count (LEB128) | count x (pet u32, pet u32 again, level u32) | count (LEB128) | count x (pet u32,
    souls in the current stage u32) | genus insight data. -> ({pet: level}, {pet: souls}) or None."""
    if data[:2] != b"\x00\x90":
        return None
    try:
        n, p = varint(data, 6)
        if not 0 < n < 1000 or len(data) < p + 12 * n:
            return None
        levels = {}
        for _ in range(n):
            pet, again, level = struct.unpack_from("<III", data, p)
            p += 12
            if pet != again or not 1000 <= pet < 10000 or not 1 <= level <= 5:
                return None
            levels[pet] = level
        m, p = varint(data, p)
        if m > 1000 or len(data) < p + 8 * m:
            return None
        souls = {}
        for _ in range(m):
            pet, count = struct.unpack_from("<II", data, p)
            p += 8
            if not 1000 <= pet < 10000 or count > 1000:
                return None
            souls[pet] = count
        return levels, souls
    except (ValueError, IndexError, struct.error):
        return None
def pet_souls(data):
    """Live pet soul updates (live 2026-10-15; a level up came as 0d 90, 14 90, 0e 90 in that order):
      '0d 90' count (LEB128) | count x (pet u32, souls gained u32): a soul drop
      '14 90' 2 bytes | pet u32 | new level u32 | genus insight data: a level up (or unlock)
      '0e 90' count (LEB128) | count x (pet u32, souls spent u32): the souls the level up used
    -> ("gain" | "level" | "spent", [(pet, value)]) or None."""
    kind = {b"\r\x90": "gain", b"\x14\x90": "level", b"\x0e\x90": "spent"}.get(bytes(data[:2]))
    try:
        if kind == "level" and len(data) >= 12:
            pet, level = struct.unpack_from("<II", data, 4)
            return (kind, [(pet, level)]) if 1000 <= pet < 10000 and 1 <= level <= 5 else None
        if kind in ("gain", "spent"):
            n, p = varint(data, 2)
            if not 0 < n < 50 or len(data) != p + 8 * n:
                return None
            pairs = [struct.unpack_from("<II", data, p + 8 * i) for i in range(n)]
            return (kind, pairs) if all(1000 <= pet < 10000 and v <= 1000 for pet, v in pairs) else None
    except (ValueError, IndexError, struct.error):
        pass
    return None
def wardrobe(data):
    """'5b 8d' your appearance (wardrobe) collection, sent when you enter the world (live 2026-10-15, 219 pieces):
    count (LEB128) | count x (skin item id u32, a small number u32: 1 for most, up to 8) | slot data.
    Skin ids are set x 100 + piece (30063103: set 300631, piece 3). -> {skin id: number} or None."""
    if data[:2] != b"[\x8d":
        return None
    try:
        n, p = varint(data, 2)
        if not 0 < n < 5000 or len(data) < p + 8 * n:
            return None
        out = {}
        for i in range(n):
            skin, value = struct.unpack_from("<II", data, p + 8 * i)
            if not 10_000_000 <= skin < 100_000_000 or value > 1000:
                return None
            out[skin] = value
        return out
    except (ValueError, struct.error):
        return None
# ---- your character's sheet: currencies and content counters (Settings → Characters' checklist)
# Recorded 2026-10-19 on Noragiri, against the game's own screens:
# '11 56' at login lists the inventory, currencies too; '1b 56' updates counts. A record is
# [3 bytes] 00 [u16] 00 | item id u32 | count u64 | 01 ...  The currencies' item ids:
CURRENCIES = {930100031: "Kinah", 930100001: "Kinah (bound)", 930100003: "Abyss Points", 930100017: "Nightmare Points",
              930100019: "Oath Coin", 930100013: "Silver Medal of Merit", 930100014: "Gold Medal of Merit",
              930100015: "Platinum Medal of Merit", 930100011: "Centuryroot Token", 930100024: "Daeva Season Pass Coin",
              930100023: "Enhance Stone", 930200001: "Quna"}
# only these are tracked (not the Customization Voucher 930100021, nor event currencies 9603…); ids from the game,
# checked against the game and Inven's AION 2 DB (Quna 930200001, the gold / platinum medals)
CURRENCY_RANGES = ((930100000, 930100100), (930200000, 930200100))


def currencies(data):
    """'11 56' / '1b 56' -> {item id: count} of the currency records in it."""
    out = {}
    b = bytes(data)
    for lo, hi in CURRENCY_RANGES:
        prefix = (lo // 0x100).to_bytes(3, "little")[1:]  # the id's top bytes, to find candidates fast
        p = b.find(prefix)
        while p >= 0:
            start = p - 2
            if start >= 7 and start + 12 <= len(b) and b[start - 1] == 0 and b[start - 4] == 0:
                item = int.from_bytes(b[start:start + 4], "little")
                if lo <= item < hi and item in CURRENCIES:
                    count = int.from_bytes(b[start + 4:start + 12], "little")
                    if count < 10 ** 12:
                        out[item] = count
            p = b.find(prefix, p + 1)
    return out


# '0b 61' at login: every content counter; '0c 61': one counter changed. An entry is [type][u32 id][value], the
# value's size by type: 00 none (= 0 left), 01 u64, 03 two u64, 04 / 08 one byte, 0c two LEB128 numbers. Values
# are what's LEFT (entries, attempts, time in ms). Named ones: 4 = Nightmare runs (went 2 -> 1 on a run,
# 2026-10-19), 80000001 = Ascension Trial, 3 = Abyss time (ms; 7 h = 25,200,000).
# Checked 2026-10-19 on three characters against the game and aion2t: 1 Shugo keys (12 / 21), 8 Invasion keys (5 / 7),
# 4 Nightmare ([8, 2] = "8 (+2) / 14"), 6 Unknown Fissure with its bonus in 101 ("0 (+26) / 14"), 80000001
# Ascension Trial, 3 Abyss time (ms: 25,138,000 = "6h58m58s / 7h"), 60000001 Odyle energy ([325, 970] = "325 (+970)").
# A two-number value is [left, bonus]: type 0c has both, type 08 only a bonus (left 0), 04 only left, 00 nothing.
# Bonuses ("(+1)") are extras that resets don't touch. Server = shared by the account's characters on the server.
COUNTERS = {
    4: dict(name="Nightmare", max=14, group="stock", scope="char"),
    1: dict(name="Shugo keys", max=21, group="stock", scope="server"),
    8: dict(name="Invasion keys", max=7, group="stock", scope="server"),
    6: dict(name="Unknown Fissure", max=14, group="weekly", scope="server", bonus=101, note="14 entries a week"),
    80000001: dict(name="Ascension Trial", max=3, group="weekly", scope="char", note="3 entries a week"),
    3: dict(name="Abyss time", max=7 * 3600 * 1000, group="weekly", scope="char", unit="ms", note="7 hours a week"),
    # the Abyss artifacts' passage time (Reshanta's Root, Sulfur Tree Island, Siel's Wings): 6m30s a day (3 x 2m10s) per
    # character, ms left. Nowagiwi 130000 = "Passage Usage Time 02:10"; 0 = "Usage Time Expired" (2026-10-20). It does NOT
    # refill daily (aion2t lists it as daily, the game doesn't), so it's a "stock": shown as last read, never refilled.
    10000003: dict(name="Abyss Corridors", max=390000, group="stock", scope="char", unit="ms", note="2m10s per corridor"),
}
ENERGY = 60000001  # Odyle energy: [now, bonus]


def _counter(b, p):
    """-> (id, value, next offset) of the entry at p; value None = none left."""
    kind, ident = b[p], int.from_bytes(b[p + 1:p + 5], "little")
    p += 5
    if kind == 0:
        return ident, 0, p
    if kind == 1:
        return ident, int.from_bytes(b[p:p + 8], "little", signed=True), p + 8
    if kind == 3:  # [left, bonus]: Abyss time with the added hour on top ("3h19m (+1h)")
        return ident, [int.from_bytes(b[p:p + 8], "little", signed=True), int.from_bytes(b[p + 8:p + 16], "little", signed=True)], p + 16
    if kind == 4:  # attempts left
        return ident, b[p], p + 1
    if kind == 8:  # a bonus only: "0 (+1) / 3" is a type 08 entry of 1 (checked 2026-10-20 on Noragiri's Ascension Trial)
        return ident, [0, b[p]], p + 1
    if kind == 0x0c:
        first, p = varint(b, p)
        second, p = varint(b, p)
        return ident, [first, second], p
    raise ValueError("counter type %x" % kind)


def counters(data, problems=None):
    """'0b 61' -> {id: value} of the counters it could read, or None if it isn't a counter list at all. An entry type
    it doesn't know stops the reading there (its size is unknown) but keeps what came before; the problem goes to
    problems (a list), for the log."""
    b = bytes(data)
    if len(b) < 8:
        return None
    n, p, out = b[2], 3, {}
    while p < len(b):
        try:
            ident, value, p = _counter(b, p)
        except (ValueError, IndexError) as e:
            if problems is not None:
                problems.append("counter list: %s at byte %d of %d (read %d of %d)" % (e, p, len(b), len(out), n))
            break
        out[ident] = value
    return out if out else None


def counter_update(data):
    """'0c 61' (00 | one entry | reason byte) -> (id, value) or None."""
    b = bytes(data)
    try:
        ident, value, p = _counter(b, 3)
        return (ident, value) if p == len(b) - 1 else None
    except (ValueError, IndexError):
        return None


SHOP_TABS = {0x65: "Special", 0x68: "Pets", 0x67: "Skins"}  # the rotating shop's tabs (refreshes at x:00)
SHOP_DISCOUNT = {0: 10, 1: 20, 2: 30, 3: 50}  # discount code -> percent (checked against the prices)
def shop_tab(data):
    """'91 56' one tab of the rotating shop, sent when you open it / switch tabs (live 2026-10-15, 3 tabs x 10):
    tab u8 | count u8 | count x record; a record is product u32 | item u32 | quantity u32 | 0 u32 | discount u8 |
    5d | original price u64 | price u64 (one record had an extra byte in front: records are found by their 5d
    marker and prices). -> (tab name, [dict(item, quantity, discount %, original, price)]) or None."""
    if data[:2] != b"\x91V" or len(data) < 40:
        return None
    tab, count = data[2], data[3]
    items = []
    for p in range(21, len(data) - 16):
        if data[p] != 0x5d:
            continue
        original, price = struct.unpack_from("<QQ", data, p + 1)
        if not 0 < price <= original < 10 ** 11:
            continue
        product, item, quantity, zero = struct.unpack_from("<IIII", data, p - 17)
        if zero or not 0 < quantity < 1000 or not 100_000_000 <= item < 1_000_000_000:
            continue
        percent = SHOP_DISCOUNT.get(data[p - 1], round(100 - 100 * price / original))
        items.append(dict(item=item, quantity=quantity, discount=percent, original=original, price=price))
    if not items or len(items) != count:
        return None
    return SHOP_TABS.get(tab, "Tab %02x" % tab), items
NODE_RANGE_M = 200  # objects further than this are dropped (they leave range silently too)
MONSTER_RANGE_M = 150  # monsters further than this from you are dropped (no "left range" message is sent)
def login_name(data):
    """'33 36' your character at login: id (LEB128) | character number (u32) | 1 byte | name (length byte + UTF-8).
    -> (entity id, name, character number). The entity id changes every login. The "character number" matched
    the charNo other tools keep on 2026-10-15, but several of its bits change between logins (one character had 15
    values by 2026-10-19), so characters are told apart by name (characters.py, the Shop)."""
    try:
        entity, p = varint(data, 2)
        n = data[p + 5]
        return entity, bytes(data[p + 6:p + 6 + n]).decode("utf-8"), int.from_bytes(data[p:p + 4], "little") & ~0x80
    except (ValueError, IndexError, UnicodeDecodeError):
        return None
# the login message, after the name: 28 05 | class u32 | 01 | level u32 (checked 2026-10-19: Noragiri 22 / 45,
# NoragiriTwo 30 / 23, WaitingSkip 30 / 45, Nowagiwi 34 / 45 against the game). Class ids seen so far:
CLASSES = {22: "Spiritmaster", 30: "Cleric", 34: "Chanter"}
CLASS_ICONS = {"Spiritmaster": "elementalist", "Cleric": "cleric", "Chanter": "chanter", "Gladiator": "gladiator",
               "Templar": "templar", "Assassin": "assassin", "Ranger": "ranger", "Sorcerer": "sorcerer"}


def login_profile(data):
    """'33 36' -> (class id, level) or None."""
    b = bytes(data)
    try:
        _, p = varint(b, 2)
        n = b[p + 5]
        q = p + 6 + n
        if b[q:q + 2] != b"\x28\x05" or b[q + 6] != 1:
            return None
        return int.from_bytes(b[q + 2:q + 6], "little"), int.from_bytes(b[q + 7:q + 11], "little")
    except (ValueError, IndexError):
        return None


class Tracker:
    def __init__(self):
        self.lock = threading.Lock()
        self.own = None
        self.position = None
        self.position_time = 0
        self.position_source = None  # the message that last set your position (for the debug window)
        self.position_updates = 0
        self.cubes = {}
        self.monsters = {}  # entity -> dict(type, xyz, seen)
        self.track_cubes = self.track_monsters = True  # a feature switched off: its messages aren't parsed
        self.nodes = {}  # entity -> dict(type, xyz, seen): '46' objects that aren't cubes (gathering nodes...)
        self.used = []  # the last objects used up ('56' 04 03 00): dict(type, xyz, n), n counting up
        self.used_count = 0
        self.track_nodes = True
        # Your pet collection (account-wide): the full list when you enter the world, then soul drops / level ups.
        self.soul_sync = None  # dict(levels, souls, time) from the last '00 90'
        self.soul_sync_count = 0
        self.soul_events = []  # the last soul updates: dict(kind, pairs, time, n), n counting up
        self.soul_count = 0
        self.wardrobe = None  # dict(skins={id: number}, time) from the last '5b 8d'
        self.wardrobe_count = 0
        self.shop = {}  # tab -> dict(items, time) from the last '91 56' of that tab
        self.shop_count = 0
        # your sheet: dict(owner, counters, currencies, time). A login sends the counters and the inventory just
        # BEFORE the message with the name: a full counter list starts a new sheet, the next login names it.
        self.sheet = None
        self.sheet_count = 0
        self.paused = False
        self.own_profile = None  # (class id, level) from the login message
        # Who you are: only the login message ('33 36') and the 'own state' message ('4a 36'), as in the version
        # that identified every character correctly. (Guessing from teleports or remembered ids was unsafe: other
        # players land on the same teleport spots, and ids may be reused between sessions.)
        self.own_name = None  # display only, from the login message
        self.own_char_no = None  # your character's permanent number, from the login message
        self.client_messages = 0  # messages from your client to the server
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

    def feed_client(self, batch, when):
        """Messages your own client sends to the server (recorded for now; your movement is in here)."""
        self.client_messages += len(batch)
        if self.recorder:
            self.recorder.write(batch, when, "c")

    def feed(self, batch, when):
        if self.paused:  # the first-run notice isn't agreed yet: nothing is read
            return
        if self.recorder:
            self.recorder.write(batch, when)
        with self.lock:
            self._feed(batch, when)

    def new_sheet(self, when):
        """A login is under way: its counters and inventory come before the message with the name, which names it."""
        self.sheet = dict(owner=None, counters={}, currencies={}, time=when, start=when)

    def note_sheet(self, kind, data, when):
        """Counters and currencies -> self.sheet (see __init__). Either of the login messages ('0b 61' the counters,
        '11 56' the whole inventory) starts a new sheet, so a character is caught even when one of them can't be read;
        both come within a second, so the second one joins the sheet the first started."""
        login = kind in (b"\x0ba", b"\x11V")
        if login and (self.sheet is None or self.sheet["owner"] is not None and when - self.sheet.get("start", 0) > 10):
            self.new_sheet(when)
        if kind == b"\x0ba":
            problems = []
            found = counters(data, problems)
            for problem in problems:
                if self.log:
                    self.log("Sheet: " + problem)
            if found is None:
                return False
            self.sheet["counters"] = found
        elif kind == b"\x0ca":
            change = counter_update(data)
            if change is None:
                return False
            if self.sheet is None:
                self.sheet = dict(owner=self.own_name, counters={}, currencies={}, time=when)
            self.sheet["counters"][change[0]] = change[1]
        else:
            found = currencies(data)
            if not found and not login:
                return False
            if self.sheet is None:
                self.sheet = dict(owner=self.own_name, counters={}, currencies={}, time=when)
            self.sheet["currencies"].update(found)
        self.sheet["time"] = when
        self.sheet_count += 1
        return True

    def set_own(self, own):
        """You are `own`. Switching from another character drops everything about the old one."""
        if own == self.own:
            return
        if self.own is not None:
            self.clear()
            self.own_name = self.own_char_no = None
        self.own = own

    def _feed(self, batch, when):
        self.messages += len(batch)
        sheet = self.sheet  # started but no login named it (the inventory sent mid-game): it's who's playing
        if sheet and sheet["owner"] is None and self.own_name and when - sheet.get("start", when) > 20:
            sheet["owner"] = self.own_name
            self.sheet_count += 1
        for data in batch:  # who am I?
            kind = data[:2]
            if kind == b"36":  # login: you (and your character's name, for display)
                try:
                    self.set_own(varint(data, 2)[0])
                except ValueError:
                    continue
                login = login_name(data)
                if login:
                    self.own_name, self.own_char_no = login[1], login[2]
                    self.own_profile = login_profile(data)
                    if self.sheet is not None and self.sheet["owner"] is None:
                        self.sheet["owner"] = login[1]
                        self.sheet_count += 1
            elif kind == b"J6":
                try:
                    self.set_own(varint(data, 2)[0])
                except ValueError:
                    pass

        for data in batch:
            kind = data[:2]
            if kind[1:] == b"\x90" and self.note_souls(data, when):
                continue
            if kind in (b"\x0ba", b"\x0ca", b"\x11V", b"\x1bV") and self.note_sheet(kind, data, when):
                continue
            if kind == b"\x91V":
                tab = shop_tab(data)
                if tab:
                    self.shop[tab[0]] = dict(items=tab[1], time=when)
                    self.shop_count += 1
                    continue
            if kind == b"[\x8d":
                skins = wardrobe(data)
                if skins:
                    self.wardrobe = dict(skins=skins, time=when)
                    self.wardrobe_count += 1
                    continue
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
            if kind in (b"A6", b"*8", b"+8", b"\x04\x8d") and not self.track_monsters:
                continue
            if kind == b"A6":
                spawn = monster_intro(data)
                if spawn and not is_pet_intro(data):  # summoned pets (yours, others') aren't monsters
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
            if self.track_nodes and kind in (b":6", b":\xe3"):
                self.note_pickup(data, kind, when)
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
                        gone = self.nodes.pop(entity)
                        if bytes(data[p:]) == b"\x04\x03\x00":
                            self.add_used(gone["type"], gone["xyz"])
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

    def note_souls(self, data, when):
        """Pet collection messages -> soul_sync / soul_events. True if the message was one."""
        sync = pet_collection(data)
        if sync:
            self.soul_sync = dict(levels=sync[0], souls=sync[1], time=when)
            self.soul_sync_count += 1
            return True
        update = pet_souls(data)
        if update:
            self.soul_count += 1
            self.soul_events = self.soul_events[-99:] + [dict(kind=update[0], pairs=update[1], time=when,
                                                               n=self.soul_count)]
            return True
        return False

    def add_used(self, type_id, point):
        self.used_count += 1
        self.used = self.used[-49:] + [dict(type=type_id, xyz=list(point), n=self.used_count)]

    def note_pickup(self, data, kind, when):
        """You picked an object up (an Empyrean Trace, recorded 2026-10-15): it stays there for other players,
        so there's no '56' used-up. Two messages say so; either is enough:
          ':6'   object (LEB128) | who (LEB128) | ...: who finished interacting with it (you: picked up)
          ':\xe3' object (u32) | xyz f32: sent to you only, right after your pickup (no type: from the spawn)"""
        if kind == b":6":
            try:
                entity, p = varint(data, 2)
                who = varint(data, p)[0]
            except ValueError:
                return
            node = self.nodes.get(entity)
            if node and who == self.own:
                self.add_used(node["type"], node["xyz"])
        elif len(data) >= 18:
            entity = int.from_bytes(data[2:6], "little")
            point = xyz(data, 6)
            node = self.nodes.get(entity)
            if point is not None:
                self.add_used(node["type"] if node else None, point)

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
            here = self.position
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
            return dict(own=self.own, own_name=self.own_name, own_char_no=self.own_char_no, position=list(self.position) if self.position else None,
                        nodes=nodes,
                        position_age=max(0, now - self.position_time) if self.position else None,
                        position_source=self.position_source, position_updates=self.position_updates,
                        position_time=self.position_time if self.position else None,
                        cubes=cubes, monsters=monsters, kills=self.kills, messages=self.messages,
                        detections=self.detections, last_detection=self.last_detection,
                        used=list(self.used), used_count=self.used_count,
                        soul_sync=self.soul_sync, soul_sync_count=self.soul_sync_count,
                        soul_events=list(self.soul_events), soul_count=self.soul_count,
                        wardrobe=self.wardrobe, wardrobe_count=self.wardrobe_count,
                        shop=dict(self.shop), shop_count=self.shop_count,
                        sheet=None if self.sheet is None else dict(self.sheet, counters=dict(self.sheet["counters"]),
                                                                   currencies=dict(self.sheet["currencies"])),
                        sheet_count=self.sheet_count, own_profile=self.own_profile)
