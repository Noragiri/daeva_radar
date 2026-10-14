"""Cube Finder: standalone Altgard Hidden Cube radar for AION2.

Passively copies the game's incoming TCP traffic with WinDivert (SNIFF | RECV_ONLY:
packets are never delayed, modified or injected), decodes the game's message
framing and LZ4 blocks, and shows Hidden Cube objects the server sent to your
client, relative to your character.

Only objects your client receives are visible - this is not a whole-map scan.
Requires Windows 10/11 x64, administrator rights and WinDivert 2.2
(WinDivert.dll + WinDivert64.sys).
"""
import argparse
import collections
import ctypes
import math
import os
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

# ---------------------------------------------------------------- protocol

SEQ = 1 << 32
MAX_FRAME = 10_000_000


def varint(b, pos):
    """-> (value, position after it). At most 5 bytes."""
    n = shift = 0
    start = pos
    while pos < len(b) and pos - start < 5:
        v = b[pos]
        pos += 1
        n |= (v & 127) << shift
        if v < 128:
            return n, pos
        shift += 7
    raise ValueError("invalid prefix")


def lz4_block(src, limit):
    out = bytearray()
    pos = 0
    while pos < len(src):
        token = src[pos]
        pos += 1
        literal = token >> 4
        if literal == 15:
            while True:
                n = src[pos]
                pos += 1
                literal += n
                if n != 255:
                    break
        if pos + literal > len(src):
            raise ValueError("literal overrun")
        if len(out) + literal > limit:
            raise ValueError("oversize")
        out += src[pos:pos + literal]
        pos += literal
        if pos == len(src):
            break
        offset = src[pos] | src[pos + 1] << 8
        pos += 2
        count = (token & 15) + 4
        if token & 15 == 15:
            while True:
                n = src[pos]
                pos += 1
                count += n
                if n != 255:
                    break
        if not offset or offset > len(out):
            raise ValueError("invalid offset")
        if len(out) + count > limit:
            raise ValueError("oversize")
        start = len(out) - offset
        if count <= offset:
            out += out[start:start + count]
        else:  # overlapping copy repeats the last `offset` bytes
            out += (out[start:] * (count // offset + 1))[:count]
    return bytes(out)


def decode_frames(data, stats, messages, depth=0):
    """Split a frame run into message bodies; b'\\xff\\xff' bodies are LZ4 blocks of more frames."""
    pos = 0
    while pos < len(data):
        n, end = varint(data, pos)
        length = n - 4 + end - pos
        if length < end - pos or pos + length > len(data):
            raise ValueError("incomplete frame at " + str(pos))
        body = bytes(data[end:pos + length])
        pos += length
        stats["frames"] += 1
        if body[:2] == b"\xff\xff":
            if depth >= 8:
                raise ValueError("excess nesting")
            expected = int.from_bytes(body[2:6], "little")
            if expected > MAX_FRAME:
                raise ValueError("oversize")
            raw = lz4_block(body[6:], expected)
            if len(raw) != expected:
                raise ValueError("decompressed length mismatch")
            stats["compressed_frames"] += 1
            decode_frames(raw, stats, messages, depth + 1)
        else:
            messages.append(body)
    return messages


# ---------------------------------------------------------------- TCP streams

class Flow:
    """One server-to-client TCP direction, reassembled into whole frames."""

    def __init__(self, seq, synced_start, recovering=False):
        self.next = seq
        self.buf = bytearray()
        self.pending = {}
        self.pending_since = None
        self.lost = None
        self.told = False
        self.trusted = synced_start
        self.seen = self.bytes = 0
        self.deferred = []
        self.recovering = self.seek = recovering
        self.scan = 0
        self.retry_after = 0

    def seek_compressed(self):
        """Resync only on a fully validated compressed frame, never a plausible plain header."""
        marker = self.buf.find(b"\xff\xff", self.scan)
        incomplete = None
        while marker >= 0:
            for start in range(max(0, marker - 5), marker):
                try:
                    n, end = varint(self.buf, start)
                except ValueError:
                    continue
                size = n - 4 + end - start
                if end != marker or not 8 <= size <= 2_000_000:
                    continue
                if marker + 6 > len(self.buf):
                    incomplete = marker if incomplete is None else min(incomplete, marker)
                    continue
                expected = int.from_bytes(self.buf[marker + 2:marker + 6], "little")
                if not 2 <= expected <= 2_000_000:
                    continue
                if start + size > len(self.buf):
                    incomplete = marker if incomplete is None else min(incomplete, marker)
                    continue
                messages, stats = [], collections.Counter()
                try:
                    decode_frames(bytes(self.buf[start:start + size]), stats, messages)
                except (ValueError, IndexError):
                    continue
                if not stats["compressed_frames"] or not messages or not all(len(m) >= 2 for m in messages):
                    continue
                del self.buf[:start]
                self.seek = False
                return True
            marker = self.buf.find(b"\xff\xff", marker + 1)
        self.scan = incomplete if incomplete is not None else max(0, len(self.buf) - 1)
        return False

    def add(self, seq, data, when):
        """-> list of complete frames; sets self.lost to a reason when sync is lost."""
        if self.lost:
            return []
        d = (seq - self.next) % SEQ
        if d and d < SEQ // 2:  # ahead of what we have: hold until the gap fills
            self.pending[seq] = data
            if self.pending_since is None:
                self.pending_since = when
            if sum(map(len, self.pending.values())) > 1_000_000:
                self.lost = "a gap in the stream never filled (packets dropped)"
            elif when - self.pending_since >= 5:
                self.lost = "a gap in the stream stayed open for 5 seconds (packets dropped)"
            return []
        if d:  # retransmission overlapping data we already have
            back = SEQ - d
            if back >= len(data):
                return []
            data = data[back:]
        self.buf += data
        self.next = (self.next + len(data)) % SEQ
        self.bytes += len(data)
        while True:
            s = next((s for s in self.pending if not 0 < (s - self.next) % SEQ < SEQ // 2), None)
            if s is None:
                break
            part = self.pending.pop(s)
            d = (s - self.next) % SEQ
            back = SEQ - d if d else 0
            if back < len(part):
                self.buf += part[back:]
                self.next = (self.next + len(part) - back) % SEQ
        if not self.pending:
            self.pending_since = None
        if self.seek and not self.seek_compressed():
            return []
        frames = []
        while self.buf:
            try:
                n, p = varint(self.buf, 0)
            except ValueError:
                if len(self.buf) < 5:
                    return frames
                self.lost = "bad frame start"
                return frames
            size = n - 4 + p
            if size < p or size > MAX_FRAME:
                self.lost = "bad frame size"
                return frames
            if len(self.buf) < size:
                return frames
            frames.append(bytes(self.buf[:size]))
            del self.buf[:size]
        return frames


class StreamReader:
    """Feeds IPv4/TCP packets of one game route into Flows; calls on_messages(batch, when)."""

    def __init__(self, on_messages, on_gap, say):
        self.flows = {}
        self.on_messages = on_messages
        self.on_gap = on_gap
        self.say = say

    def packet(self, q, when):
        ihl = (q[0] & 15) * 4
        if len(q) < ihl + 20:
            return
        tcp = ihl
        flags = q[tcp + 13]
        seq = struct.unpack_from("!I", q, tcp + 4)[0]
        start = tcp + (q[tcp + 12] >> 4) * 4
        end = min(len(q), struct.unpack_from("!H", q, 2)[0])
        key = bytes(q[12:20]) + bytes(q[tcp:tcp + 4])
        if flags & 2:  # SYN: the stream starts at a known frame boundary
            self.flows[key] = Flow((seq + 1) % SEQ, True)
            return
        f = self.flows.get(key)
        if f and f.lost and end > start and when >= f.retry_after:
            behind = (seq - f.next) % SEQ
            if behind >= SEQ // 2:
                overlap = SEQ - behind
                if overlap >= end - start:
                    return
                start += overlap
                seq = f.next
            f = self.flows[key] = Flow(seq, False, recovering=True)
        if f is None and end > start:  # joined mid-stream
            f = self.flows[key] = Flow(seq, False)
        frames = f.add(seq, bytes(q[start:end]), when) if f and end > start else []
        if f and not f.trusted and not f.lost and f.bytes > 2_000_000:
            f.lost = "could not line up with the messages"
        if flags & 5:  # FIN / RST
            self.flows.pop(key, None)
        for frame in frames:
            messages, stats = [], collections.Counter()
            try:
                decode_frames(frame, stats, messages)
            except (ValueError, IndexError) as e:
                f.lost = "frame did not decode (" + str(e) + ")"
                break
            f.seen += 1
            if not f.trusted:
                f.trusted = stats["compressed_frames"] > 0 or (not f.recovering and f.seen >= 100)
                if not f.trusted:
                    f.deferred.append((messages, when))
                    if f.seen >= 200:
                        f.lost = "could not line up with the messages"
                        break
                    continue
                if f.recovering:
                    self.say("Game stream recovered. Waiting for fresh object data.")
                    f.recovering = False
            ready, f.deferred = f.deferred + [(messages, when)], []
            for batch, observed in ready:
                self.on_messages(batch, observed)
        if f and f.lost:
            f.deferred = []
            if not f.told:
                f.told = True
                f.retry_after = when + 1
                self.on_gap(f.lost)
                self.say("Lost track of the game stream: " + f.lost + ". Recovering; change zone or relog if it persists.")


# ---------------------------------------------------------------- cube tracker

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
        folder = folder or Path(__file__).resolve().parent / "data" / "maps"
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
            path = Path(__file__).resolve().parent / "data" / "monsters.json"
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

    def _feed(self, batch, when):
        self.messages += len(batch)
        for data in batch:  # who am I? a new identity invalidates everything
            if data[:2] not in (b"36", b"J6"):
                continue
            try:
                own, _ = varint(data, 2)
            except ValueError:
                continue
            if own != self.own:
                self.clear()
            self.own = own
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
            return dict(own=self.own, position=list(self.position) if self.position else None, nodes=nodes,
                        position_age=max(0, now - self.position_time) if self.position else None,
                        cubes=cubes, monsters=monsters, kills=self.kills, messages=self.messages,
                        detections=self.detections, last_detection=self.last_detection)


# ---------------------------------------------------------------- WinDivert capture

SNIFF, RECV_ONLY = 1, 4
DEFAULT_PORTS = {13328, 1111}
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
WINDOWS = sys.platform == "win32"
LINUX = sys.platform.startswith("linux")


class FatalCapture(OSError):
    """Capture cannot work (no rights, cancelled password prompt): don't retry."""


def process_list():
    """'"name","pid"' lines via Toolhelp (no tasklist/WMI, no console)."""
    from ctypes import wintypes as w

    class Entry(ctypes.Structure):
        _fields_ = [("dwSize", w.DWORD), ("cntUsage", w.DWORD), ("th32ProcessID", w.DWORD),
                    ("th32DefaultHeapID", ctypes.c_void_p), ("th32ModuleID", w.DWORD),
                    ("cntThreads", w.DWORD), ("th32ParentProcessID", w.DWORD),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", w.DWORD),
                    ("szExeFile", ctypes.c_wchar * 260)]

    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateToolhelp32Snapshot.argtypes = [w.DWORD, w.DWORD]
    k.CreateToolhelp32Snapshot.restype = w.HANDLE
    k.Process32FirstW.argtypes = k.Process32NextW.argtypes = [w.HANDLE, ctypes.POINTER(Entry)]
    k.Process32FirstW.restype = k.Process32NextW.restype = w.BOOL
    k.CloseHandle.argtypes = [w.HANDLE]
    handle = k.CreateToolhelp32Snapshot(2, 0)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    rows = {}
    try:
        entry = Entry()
        entry.dwSize = ctypes.sizeof(Entry)
        ok = k.Process32FirstW(handle, ctypes.byref(entry))
        while ok:
            rows[entry.th32ProcessID] = entry.szExeFile
            ok = k.Process32NextW(handle, ctypes.byref(entry))
    finally:
        k.CloseHandle(handle)
    return rows


def game_routes(connections):
    """(local ip, local port, peer ip, peer port) of the game's TCP sockets -> server-to-game routes."""
    routes = set()
    for local, local_port, peer, peer_port in connections:
        if ":" in local or ":" in peer or (peer == local and local_port == peer_port):
            continue
        if peer_port == 443 and not peer.startswith("127."):
            continue  # HTTPS services, not the game stream
        routes.add((peer, peer_port, local, local_port))
    # Drop the game's connections to itself (both ends listed).
    return {r for r in routes if (r[2], r[3], r[0], r[1]) not in routes}


def discover_routes():
    """Server-to-game IPv4 tuples (src ip, src port, dst ip, dst port) of aion2.exe's TCP sockets."""
    return discover_routes_linux() if LINUX else discover_routes_windows()


def discover_routes_windows():
    pids = {str(pid) for pid, name in process_list().items() if name.lower() == "aion2.exe"}
    if not pids:
        return set()
    result = subprocess.run(["netstat", "-ano", "-p", "tcp"], capture_output=True, text=True,
                            timeout=5, creationflags=NO_WINDOW)
    if result.returncode:
        raise OSError("netstat failed: " + result.stderr.strip())
    connections = []
    for line in result.stdout.splitlines():
        row = line.split()
        if len(row) != 5 or row[0] != "TCP" or row[3] not in ("ESTABLISHED", "SYN_SENT", "SYN_RECEIVED") \
                or row[4] not in pids:
            continue
        try:
            local, local_port = row[1].rsplit(":", 1)
            peer, peer_port = row[2].rsplit(":", 1)
            connections.append((local, int(local_port), peer, int(peer_port)))
        except ValueError:
            continue
    return game_routes(connections)


def is_game_process(pid_dir):
    """aion2.exe under Wine/Proton: by thread name or the Windows path in argv[0]."""
    try:
        if (pid_dir / "comm").read_text(errors="replace").strip().lower() == "aion2.exe":
            return True
        argv0 = (pid_dir / "cmdline").read_bytes().split(b"\0", 1)[0].decode(errors="replace")
    except OSError:
        return False
    return argv0.replace("\\", "/").rsplit("/", 1)[-1].lower() == "aion2.exe"


def proc_tcp_ip(text):
    """'0100007F:1F90' (/proc/net/tcp, little-endian IPv4) -> ('127.0.0.1', 8080)."""
    ip, port = text.split(":")
    return ".".join(str(b) for b in bytes.fromhex(ip)[::-1]), int(port, 16)


def discover_routes_linux(proc=Path("/proc")):
    inodes, table = set(), None
    for pid_dir in proc.iterdir():
        if not pid_dir.name.isdigit() or not is_game_process(pid_dir):
            continue
        try:
            for fd in (pid_dir / "fd").iterdir():
                link = os.readlink(fd)
                if link.startswith("socket:["):
                    inodes.add(link[8:-1])
        except OSError:
            continue
        table = table or pid_dir / "net" / "tcp"  # the game's network namespace
    if not inodes:
        return set()
    connections = []
    for line in table.read_text().splitlines()[1:]:
        row = line.split()
        # 01 ESTABLISHED, 02 SYN_SENT, 03 SYN_RECV
        if len(row) < 10 or row[3] not in ("01", "02", "03") or row[9] not in inodes:
            continue
        connections.append(proc_tcp_ip(row[1]) + proc_tcp_ip(row[2]))
    return game_routes(connections)


def packet_route(q):
    tcp = (q[0] & 15) * 4
    return (".".join(map(str, q[12:16])), int.from_bytes(q[tcp:tcp + 2], "big"),
            ".".join(map(str, q[16:20])), int.from_bytes(q[tcp + 2:tcp + 4], "big"))


def find_windivert(explicit):
    here = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent
    candidates = [Path(explicit)] if explicit else []
    candidates += [here / "windivert", here]
    if os.environ.get("APPDATA"):
        candidates.append(Path(os.environ["APPDATA"]) / "GripStrengthMeter" / "windivert")
    for d in candidates:
        if (d / "WinDivert.dll").exists() and (d / "WinDivert64.sys").exists():
            return d
    raise OSError("WinDivert.dll + WinDivert64.sys not found. Put them beside cube_finder.py "
                  "(from the official WinDivert 2.2.2 x64 release) or pass --windivert DIR.")


def usable(q):
    """IPv4 TCP packet, not a fragment, long enough for the headers."""
    return len(q) >= 40 and q[0] >> 4 == 4 and q[9] == 6 and not int.from_bytes(q[6:8], "big") & 0x3FFF


class Capture:
    """Windows: WinDivert, filtered to the game ports (+ loopback)."""
    filters_ports = True

    def __init__(self, folder, ports):
        self.dll = ctypes.WinDLL(str(folder / "WinDivert.dll"), use_last_error=True)
        self.dll.WinDivertOpen.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_short, ctypes.c_ulonglong]
        self.dll.WinDivertOpen.restype = ctypes.c_void_p
        self.dll.WinDivertRecv.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint,
                                           ctypes.POINTER(ctypes.c_uint), ctypes.c_void_p]
        self.dll.WinDivertRecv.restype = ctypes.c_int
        self.dll.WinDivertShutdown.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self.dll.WinDivertClose.argtypes = [ctypes.c_void_p]
        self.ports = set(ports)
        expression = " or ".join("tcp.SrcPort == %d" % p for p in sorted(self.ports))
        self.handle = self.dll.WinDivertOpen(("ip and tcp and (%s or loopback)" % expression).encode(),
                                             0, -30000, SNIFF | RECV_ONLY)
        if self.handle is None or self.handle == ctypes.c_void_p(-1).value:
            err = ctypes.get_last_error()
            if err == 5:
                raise FatalCapture("WinDivert needs administrator access.")
            raise OSError(err, "WinDivert could not start: " + ctypes.FormatError(err).strip())
        self.buffer = ctypes.create_string_buffer(65535)

    def receive(self):
        length = ctypes.c_uint()
        if not self.dll.WinDivertRecv(self.handle, self.buffer, len(self.buffer), ctypes.byref(length), None):
            err = ctypes.get_last_error()
            raise OSError(err, "capture stopped: " + ctypes.FormatError(err).strip())
        q = self.buffer.raw[:length.value]
        return q if usable(q) else None

    def shutdown(self):
        if self.handle is not None:
            self.dll.WinDivertShutdown(self.handle, 1)

    def close(self):
        if self.handle is not None:
            self.dll.WinDivertClose(self.handle)
            self.handle = None


class RawCapture:
    """Linux: a read-only AF_PACKET socket (needs root or CAP_NET_RAW). Sees all IPv4; routes filter it."""
    filters_ports = False
    PACKET_OUTGOING = 4

    def __init__(self):
        import socket
        try:
            self.sock = socket.socket(socket.AF_PACKET, socket.SOCK_DGRAM, socket.htons(0x0800))
        except PermissionError:
            raise FatalCapture("Packet capture needs root (or CAP_NET_RAW).")
        self.sock.settimeout(1)
        self.closed = False

    def receive(self):
        import socket
        if self.closed:
            raise OSError("capture closed")
        try:
            q, addr = self.sock.recvfrom(65535)
        except socket.timeout:
            return None
        if addr[2] == self.PACKET_OUTGOING:  # our own sends (and the 2nd copy of each loopback packet)
            return None
        return q if usable(q) else None

    def shutdown(self):
        self.closed = True

    def close(self):
        self.closed = True
        self.sock.close()


class HelperCapture:
    """Linux without root: a root helper (this script with --capture-helper, via pkexec or sudo)
    captures; the window and sound keep running as you. Packets come back length-prefixed on a pipe."""
    filters_ports = False

    def __init__(self):
        import shutil
        if shutil.which("pkexec") and (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
            launcher = ["pkexec"]
        elif shutil.which("sudo"):
            launcher = ["sudo"]
        else:
            raise FatalCapture("Packet capture needs root: run with sudo, or install pkexec.")
        self.proc = subprocess.Popen(launcher + [sys.executable, str(Path(__file__).resolve()), "--capture-helper"],
                                     stdout=subprocess.PIPE, bufsize=0)
        self.out = self.proc.stdout

    def read(self, n):
        data = b""
        while len(data) < n:
            part = self.out.read(n - len(data))
            if not part:
                raise FatalCapture("The capture helper stopped (password prompt cancelled or capture failed).")
            data += part
        return data

    def receive(self):
        q = self.read(int.from_bytes(self.read(4), "little"))
        return q if usable(q) else None

    def shutdown(self):
        pass  # nothing to refresh: the helper doesn't filter ports

    def close(self):
        self.out.close()  # the helper exits on its next write (it runs as root, so we can't signal it)


def capture_helper():
    """Root side of HelperCapture: forward IPv4 TCP packets to stdout until the reader goes away."""
    capture = RawCapture()
    out = sys.stdout.buffer
    parent = os.getppid()
    while True:
        q = capture.receive()
        if q is None:
            if os.getppid() != parent:  # the window closed without traffic to notice it by
                return
            continue
        tcp = (q[0] & 15) * 4
        if int.from_bytes(q[tcp:tcp + 2], "big") in (80, 443) and q[12] != 127:
            continue  # web traffic is never the game stream; skip it to keep the pipe light
        try:
            out.write(len(q).to_bytes(4, "little") + q)
            out.flush()
        except (BrokenPipeError, OSError, ValueError):
            return


def open_capture(folder, ports):
    if WINDOWS:
        return Capture(folder, ports)
    if os.geteuid() == 0:
        return RawCapture()
    return HelperCapture()


class Engine:
    def __init__(self, tracker, folder, extra_port=None, log=print):
        self.tracker = tracker
        self.folder = folder
        self.ports = set(DEFAULT_PORTS) | ({extra_port} if extra_port else set())
        self.log = log
        self.routes = set()
        self.readers = {}
        self.capture = None
        self.refresh = threading.Event()
        self.status = "Starting"
        self.packets = 0
        self.stop = False

    def say(self, text):
        self.log(time.strftime("%H:%M:%S ") + text)

    def heartbeat(self):
        """Periodic one-line status for --log."""
        while not self.stop:
            time.sleep(10)
            snap = self.tracker.snapshot()
            pos = snap["position"]
            self.say("status: %s | %d packets, %d messages, %d objects | own %s | position %s | %d cubes (%s)" % (
                self.status, self.packets, snap["messages"], len(self.tracker.objects), snap["own"],
                "%.0f,%.0f,%.0f" % tuple(pos) if pos else "unknown", len(snap["cubes"]),
                ", ".join("%s %s" % (c["id"], c["status"]) for c in snap["cubes"])))

    def on_gap(self, reason):
        with self.tracker.lock:  # missed messages may have opened/moved anything
            self.tracker.clear()

    def monitor_routes(self):
        while not self.stop:
            try:
                routes = discover_routes()
                if routes != self.routes:
                    old = {r[1] for r in self.routes if not r[0].startswith("127.")}
                    new = {r[1] for r in routes if not r[0].startswith("127.")}
                    self.routes = routes
                    self.say("AION2 routes: " + (", ".join("%s:%d" % r[:2] for r in sorted(routes)) or "none"))
                    if new - old - self.ports and getattr(self.capture, "filters_ports", False):
                        # the capture filter must include the new server port
                        self.refresh.set()
                        if self.capture:
                            self.capture.shutdown()
            except (OSError, subprocess.TimeoutExpired) as e:
                self.say("Route discovery: " + str(e))
            time.sleep(3)

    def run(self):
        threading.Thread(target=self.monitor_routes, daemon=True).start()
        pending, next_discovery = set(), 0
        while not self.stop:
            try:
                self.refresh.clear()
                ports = self.ports | {r[1] for r in self.routes}
                self.capture = open_capture(self.folder, ports)
                self.status = "Waiting for game traffic"
                if self.capture.filters_ports:
                    self.say("Capture open (ports %s + loopback)" % ", ".join(map(str, sorted(ports))))
                else:
                    self.say("Capture open (all IPv4 TCP; filtered to the game's connections)")
                while not self.refresh.is_set() and not self.stop:
                    q = self.capture.receive()
                    if q is None:
                        continue
                    route = packet_route(q)
                    routes = self.routes
                    if routes and route not in routes:
                        now = time.monotonic()
                        if now >= next_discovery:  # a brand-new socket (map change, reconnect)
                            next_discovery = now + 0.5
                            try:
                                pending = discover_routes()
                            except (OSError, subprocess.TimeoutExpired):
                                pending = set()
                        if route not in pending:
                            continue
                    if not routes and route[1] not in self.ports:
                        continue
                    reader = self.readers.get(route)
                    if reader is None:
                        reader = self.readers[route] = StreamReader(self.tracker.feed, self.on_gap, self.say)
                    self.packets += 1
                    self.status = "Reading the game"
                    reader.packet(q, time.time())
            except OSError as e:
                if not self.refresh.is_set() and not self.stop:
                    self.status = str(e)
                    self.say(self.status)
                    if isinstance(e, FatalCapture):
                        return
                    time.sleep(5)
            finally:
                if self.capture:
                    self.capture.close()
                    self.capture = None


# ---------------------------------------------------------------- UI

COMPASS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")
SOUNDS = Path(__file__).resolve().parent / "sounds"
SETTINGS = Path(os.environ.get("APPDATA") or os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") \
    / "CubeFinder" / "settings.json"


def voices():
    """Bundled callouts (sounds/cube-<name>.wav) plus the system beep and silence."""
    return sorted(p.stem[5:] for p in SOUNDS.glob("cube-*.wav")) + ["system beep", "off"]


def load_settings():
    import json
    try:
        return json.loads(SETTINGS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_settings(settings):
    import json
    try:
        SETTINGS.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS.write_text(json.dumps(settings), encoding="utf-8")
    except OSError:
        pass


_scaled = {}


def scaled(path, volume):
    """Copy of a PCM WAV with samples scaled to volume % (winsound has no volume control); cached."""
    if volume == 100:
        return path
    import array
    import tempfile
    import wave
    key = (path, os.path.getmtime(path), volume)
    if key in _scaled and os.path.exists(_scaled[key]):
        return _scaled[key]
    try:
        with wave.open(path) as w:
            params, width = w.getparams(), w.getsampwidth()
            frames = w.readframes(w.getnframes())
    except (wave.Error, EOFError):
        return path  # not plain PCM: play unchanged
    gain = volume / 100
    if width == 1:  # unsigned 8-bit
        frames = bytes(max(0, min(255, round((b - 128) * gain) + 128)) for b in frames)
    elif width in (2, 4):
        samples = array.array("h" if width == 2 else "i", frames)
        top = (1 << (width * 8 - 1)) - 1
        samples = array.array(samples.typecode, (max(-top - 1, min(top, round(s * gain))) for s in samples))
        frames = samples.tobytes()
    elif width == 3:
        out = bytearray()
        for i in range(0, len(frames) - 2, 3):
            s = int.from_bytes(frames[i:i + 3], "little", signed=True)
            out += max(-8388608, min(8388607, round(s * gain))).to_bytes(3, "little", signed=True)
        frames = bytes(out)
    else:
        return path
    folder = Path(tempfile.gettempdir()) / "CubeFinder"
    folder.mkdir(exist_ok=True)
    target = str(folder / ("%d-%s" % (volume, Path(path).name)))
    with wave.open(target, "wb") as w:
        w.setparams(params)
        w.writeframes(frames)
    _scaled[key] = target
    return target


def mci(command):
    buf = ctypes.create_unicode_buffer(256)
    return ctypes.windll.winmm.mciSendStringW(command, buf, 256, None), buf.value


def play_mci(path, volume):
    """MP3 and other non-WAV audio through Windows MCI; its volume tops out at 100 %."""
    mci("close cubefinder")
    err, _ = mci('open "%s" type mpegvideo alias cubefinder' % path)
    if err:
        return False
    mci("setaudio cubefinder volume to %d" % min(1000, volume * 10))
    return mci("play cubefinder")[0] == 0


def play_linux(path, volume):
    """First installed player: WAV (volume already applied) via PipeWire/PulseAudio/ALSA; others via
    mpv (to 200 %), ffplay or mpg123 (to 100 %)."""
    import shutil
    if path.lower().endswith(".wav"):
        path = scaled(path, volume)
        players = [["paplay"], ["pw-play"], ["aplay", "-q"]]
    else:
        players = [["mpv", "--no-video", "--really-quiet", "--volume-max=200", "--volume=%d" % volume],
                   ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", "-volume", str(min(volume, 100))],
                   ["mpg123", "-q", "-f", str(int(32768 * min(volume, 100) / 100))]]
    for player in players:
        if shutil.which(player[0]):
            subprocess.Popen(player + [path], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
            return True
    return False


LINUX_BEEP = "/usr/share/sounds/freedesktop/stereo/complete.oga"


def play(voice, custom=None, volume=100):
    """voice: a bundled name, 'custom' (play the custom WAV/MP3), 'system beep' or 'off'."""
    if voice == "off" or volume <= 0:
        return
    if voice == "system beep":
        if WINDOWS:
            import winsound
            winsound.MessageBeep(winsound.MB_ICONASTERISK)
        elif not (os.path.exists(LINUX_BEEP) and play_linux(LINUX_BEEP, 100)):
            print("\a", end="", flush=True)
        return
    path = custom if voice == "custom" else str(SOUNDS / ("cube-%s.wav" % voice))
    if not path or not os.path.exists(path):
        return
    if not WINDOWS:
        play_linux(path, volume)
    elif path.lower().endswith(".wav"):
        import winsound
        winsound.PlaySound(scaled(path, volume), winsound.SND_FILENAME | winsound.SND_ASYNC)
    else:
        play_mci(path, volume)


def bearing(position, point):
    """Map bearing from you to point in degrees, 0 = map top (N), clockwise."""
    return math.degrees(math.atan2(point[0] - position[0], -(point[1] - position[1]))) % 360


def relative(position, point):
    """-> (horizontal metres, height metres, compass) from you to point. Map top = N."""
    dist = math.hypot(point[0] - position[0], point[1] - position[1]) / 100
    dz = (point[2] - position[2]) / 100
    return dist, dz, COMPASS[int((bearing(position, point) + 22.5) // 45) % 8]


FONT = "Segoe UI" if WINDOWS else "DejaVu Sans"
MONO = "Consolas" if WINDOWS else "DejaVu Sans Mono"
BG, PANEL = "#15171c", "#1d2027"
# Height classes: colour + shape, so colour is never the only cue.
LEVEL_M = 3
HEIGHT = {"above": ("#4fd8ff", "▲"), "below": ("#ff6ad5", "▼"), "level": ("#ffd24a", "■")}


def height_class(dz):
    return "above" if dz >= LEVEL_M else "below" if dz <= -LEVEL_M else "level"


def dim(color, amount=0.55):
    """Blend a colour toward the panel background (for 'last seen')."""
    a = [int(color[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(PANEL[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(round(x + (y - x) * amount) for x, y in zip(a, b))


OUTLINE_OFFSETS = ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (1, 1), (-1, 1), (1, -1))


def draw_text(canvas, x, y, text, color, outline=PANEL, anchor="center", size=10, bold=True):
    """Canvas text with a dark outline so it stays readable over rings, pins and the game map."""
    font = (FONT, size, "bold" if bold else "normal")
    for ox, oy in OUTLINE_OFFSETS:
        canvas.create_text(x + ox, y + oy, text=text, fill=outline, anchor=anchor, font=font)
    canvas.create_text(x, y, text=text, fill=color, anchor=anchor, font=font)


def pin_points(x, y, kind, r):
    if kind == "above":
        return (x, y - r, x + r, y + r * 0.8, x - r, y + r * 0.8)
    if kind == "below":
        return (x, y + r, x + r, y - r * 0.8, x - r, y - r * 0.8)
    r -= 1
    return (x - r, y - r, x + r, y - r, x + r, y + r, x - r, y + r)


def draw_pin(canvas, x, y, kind, color, solid, r=8, halo=None):
    """▲ above, ▼ below, ■ level. Solid = in range; outline = last seen / off the edge.
    halo: a dark rim behind the pin, for busy backgrounds like the game's minimap."""
    if halo:
        canvas.create_polygon(pin_points(x, y, kind, r + 2), fill=halo if solid else "", outline=halo,
                              width=1 if solid else 4)
    canvas.create_polygon(pin_points(x, y, kind, r), fill=color if solid else "", outline=color,
                          width=1 if solid else 2)


def label_side(x, mid, width, size, gap=12):
    """Put a pin label on the side away from the centre, unless that runs off the canvas."""
    left = x < mid - 1
    if left and x - gap - width < 0:
        left = False
    elif not left and x + gap + width > size:
        left = True
    return (x - gap, "e") if left else (x + gap, "w")


def click_through(window, on):
    """Windows: let mouse clicks pass through the window to the game (or stop doing so).
    Also keeps it off the taskbar / Alt+Tab and from taking focus from the game."""
    if not WINDOWS:
        return
    user32 = ctypes.windll.user32
    user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
    user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
    hwnd = int(window.wm_frame(), 16)
    style = user32.GetWindowLongPtrW(hwnd, -20)  # GWL_EXSTYLE
    style |= 0x80000 | 0x80 | 0x08000000  # LAYERED | TOOLWINDOW | NOACTIVATE
    style = style | 0x20 if on else style & ~0x20  # TRANSPARENT = click-through
    user32.SetWindowLongPtrW(hwnd, -20, style)


def hotkey_thread(flag):
    """Windows: global Ctrl+Shift+O (works while the game has focus) sets flag."""
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    if not user32.RegisterHotKey(None, 1, 0x2 | 0x4 | 0x4000, ord("O")):  # CTRL | SHIFT | NOREPEAT
        return
    msg = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        if msg.message == 0x312:  # WM_HOTKEY
            flag.set()


HERE = Path(__file__).resolve().parent
ASSETS = HERE / "assets"


def set_icon(window):
    """Window/taskbar icon from assets/icon*.png; returns the images so they stay alive. (icon.ico is
    for the shortcut only: Tk's own .ico loading replaced the icon with a blank one.)"""
    import tkinter as tk
    try:
        # big + simplified small sizes, so the title bar / taskbar don't shrink the detailed one
        images = [tk.PhotoImage(file=str(ASSETS / name)) for name in ("icon.png", "icon-32.png", "icon-16.png")
                  if (ASSETS / name).exists()]
        window.iconphoto(True, *images)
        return images
    except (tk.TclError, OSError):
        return None


def show_error(text):
    """Errors must stay visible when there is no console (pythonw)."""
    if sys.stdout is not None and sys.stdin is not None and sys.stdin.isatty():
        print("Error:", text)
        input("Press Enter to close.")
        return
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        set_icon(root)
        messagebox.showerror("Cube Finder", str(text), parent=root)
        root.destroy()
    except Exception:
        pass


def make_shortcut():
    """Windows: 'Cube Finder.lnk' beside the script and on the Desktop: pythonw (no console) + icon."""
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    target = pythonw if pythonw.exists() else Path(sys.executable)
    script = (
        "$s = New-Object -ComObject WScript.Shell; "
        "foreach ($dir in @('{here}', [Environment]::GetFolderPath('Desktop'))) {{ "
        "$l = $s.CreateShortcut((Join-Path $dir 'Cube Finder.lnk')); "
        "$l.TargetPath = '{target}'; $l.Arguments = '\"{main}\"'; $l.WorkingDirectory = '{here}'; "
        "$l.IconLocation = '{icon},0'; $l.Description = 'Cube Finder: AION2 cube radar'; $l.Save() }}"
    ).format(here=HERE, target=target, main=HERE / "cube_finder.py", icon=ASSETS / "icon.ico")
    subprocess.run(["powershell", "-NoProfile", "-Command", script], check=True, creationflags=NO_WINDOW)
    print("Created 'Cube Finder.lnk' in %s and on your Desktop." % HERE)


OVERLAY_CONE = "#4fd8ff"  # the overlays' cone outline: not white, so the cone reader never mistakes it for the game's
OVERLAY_KEY = "#0b0c0e"  # colour made transparent on Windows; never used for drawing
OVERLAY_DEFAULT = dict(x=200, y=200, w=320, h=240, ppm=1.0, locked=False, shown=False, cone=True)


class Overlay:
    """Borderless, see-through layer to place over the game's minimap (north-up, like it). Each feature
    has its own, saved under its own settings key, and draws its content via owner.paint_overlay().
    Unlocked: drag to move, drag the bottom-right corner to resize, mouse wheel to change the scale,
    right-click to lock. Locked: only the feature's marks; clicks go through to the game (Windows)."""
    GRIP = 18

    def __init__(self, owner):
        """owner: the feature (needs .tk, .root, .overlay_key, .paint_overlay() and .overlay_changed())."""
        tk = owner.tk
        self.owner = owner
        self.cfg = dict(OVERLAY_DEFAULT, **load_settings().get(owner.overlay_key, {}))
        self.drag = None
        self.win = win = tk.Toplevel(owner.root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg=OVERLAY_KEY)
        if WINDOWS:
            win.attributes("-transparentcolor", OVERLAY_KEY)
        else:
            win.attributes("-alpha", 0.8)  # Tk has no colour-key transparency on Linux
        self.canvas = c = tk.Canvas(win, bg=OVERLAY_KEY, highlightthickness=0)
        c.pack(fill="both", expand=True)
        self.place()
        c.bind("<ButtonPress-1>", self.press)
        c.bind("<B1-Motion>", self.motion)
        c.bind("<ButtonRelease-1>", lambda e: self.save())
        c.bind("<Button-3>", lambda e: self.set_locked(True))
        c.bind("<MouseWheel>", lambda e: self.wheel(e.delta > 0))
        c.bind("<Button-4>", lambda e: self.wheel(True))
        c.bind("<Button-5>", lambda e: self.wheel(False))
        win.update_idletasks()
        # visible to screenshots: nothing it draws is near-white over the minimap (cone outline in OVERLAY_CONE,
        # your white dot sits inside the arrow area the cone reader skips), so it can't fool the cone reader
        self.set_locked(self.cfg["locked"])

    def place(self):
        c = self.cfg
        self.win.geometry("%dx%d+%d+%d" % (c["w"], c["h"], c["x"], c["y"]))

    def save(self):
        save_settings(dict(load_settings(), **{self.owner.overlay_key: self.cfg}))

    def press(self, e):
        resize = e.x > self.cfg["w"] - self.GRIP and e.y > self.cfg["h"] - self.GRIP
        self.drag = (e.x_root, e.y_root, dict(self.cfg), resize)

    def motion(self, e):
        if self.cfg["locked"] or not self.drag:
            return
        x0, y0, start, resize = self.drag
        dx, dy = e.x_root - x0, e.y_root - y0
        if resize:
            self.cfg["w"], self.cfg["h"] = max(80, start["w"] + dx), max(80, start["h"] + dy)
        else:
            self.cfg["x"], self.cfg["y"] = start["x"] + dx, start["y"] + dy
        self.place()

    def wheel(self, up):
        if not self.cfg["locked"]:
            self.cfg["ppm"] = max(0.05, min(20.0, self.cfg["ppm"] * (1.1 if up else 1 / 1.1)))
            self.save()

    def set_locked(self, locked):
        self.cfg["locked"] = locked
        click_through(self.win, locked)
        self.save()
        self.owner.overlay_changed()

    def close(self):
        self.win.destroy()

    def to_screen(self, position, point, margin=10):
        """World point -> (x, y, on_edge): offset from you at the overlay's scale, pinned to its border
        (pointing the right way) when outside it."""
        w, h, ppm = self.cfg["w"], self.cfg["h"], self.cfg["ppm"]
        cx, cy = w / 2, h / 2
        dx = (point[0] - position[0]) / 100 * ppm
        dy = (point[1] - position[1]) / 100 * ppm
        edge = abs(dx) > cx - margin or abs(dy) > cy - margin
        if edge:
            f = min((cx - margin) / abs(dx) if dx else math.inf, (cy - margin) / abs(dy) if dy else math.inf)
            dx, dy = dx * f, dy * f
        return cx + dx, cy + dy, edge

    def draw(self, snap):
        c = self.canvas
        w, h, ppm = self.cfg["w"], self.cfg["h"], self.cfg["ppm"]
        cx, cy = w / 2, h / 2
        c.delete("all")
        if not self.cfg["locked"]:  # alignment aids
            c.create_rectangle(1, 1, w - 2, h - 2, outline=self.owner.overlay_color, width=2, dash=(6, 4))
            for metres in (25, 50, 100, 200, 500, 1000, 2000):
                r = metres * ppm
                if 18 <= r <= max(w, h):
                    c.create_oval(cx - r, cy - r, cx + r, cy + r, outline="#9fb0d0", dash=(3, 4))
                    draw_text(c, cx + 3, cy - r + 2, "%d m" % metres, "#c8d2e6", outline="#000000",
                              anchor="nw", size=8, bold=False)
            c.create_line(cx - 12, cy, cx + 12, cy, fill="#ffffff", width=2)
            c.create_line(cx, cy - 12, cx, cy + 12, fill="#ffffff", width=2)
            c.create_polygon(w - 2, h - self.GRIP, w - 2, h - 2, w - self.GRIP, h - 2, fill=self.owner.overlay_color)
            draw_text(c, 6, 6, "%s overlay · drag: move · corner: size\nwheel: scale · right-click or Ctrl+Shift+O: lock"
                      % self.owner.title, "#ffffff", outline="#000000", anchor="nw", size=8, bold=False)
        if not snap["position"]:
            if not self.cfg["locked"]:
                draw_text(c, cx, cy + 24, "waiting for your position", "#ffffff", outline="#000000", size=9)
            return
        if self.cfg["cone"]:
            draw_cone(c, cx, cy, min(w, h) / 2 - 6, snap.get("cone"), outline=OVERLAY_CONE)
        self.owner.paint_overlay(self, c, snap)


SIDEBAR, ACCENT, TEXT, MUTED, BUTTON, BUTTON_HOVER = "#101216", "#4fd8ff", "#e6e6e6", "#9aa0aa", "#2a2e37", "#3a3f4b"


def button(tk, parent, text, command, width=None, **kw):
    return tk.Button(parent, text=text, command=command, bg=BUTTON, fg=TEXT, relief="flat", bd=0,
                     activebackground=BUTTON_HOVER, activeforeground=TEXT, padx=10, pady=4,
                     font=(FONT, 10), width=width, cursor="hand2", **kw)


def heading(tk, parent, text, sub=None):
    tk.Label(parent, text=text, bg=BG, fg=TEXT, font=(FONT, 13, "bold"), anchor="w").pack(fill="x")
    if sub:
        tk.Label(parent, text=sub, bg=BG, fg=MUTED, font=(FONT, 9), anchor="w", justify="left",
                 wraplength=390).pack(fill="x", pady=(0, 8))


def section(tk, parent, text):
    tk.Label(parent, text=text.upper(), bg=BG, fg="#6b7590", font=(FONT, 8, "bold"), anchor="w").pack(
        fill="x", pady=(12, 4))


def checkbox(tk, parent, text, variable, command):
    return tk.Checkbutton(parent, text=text, variable=variable, command=command, bg=BG, fg=TEXT,
                          selectcolor=BUTTON, activebackground=BG, activeforeground=TEXT, font=(FONT, 10), anchor="w")


def draw_rings(canvas, size, range_m):
    """The radar background shared by features: distance rings, N, and you in the middle.
    -> pixels per metre."""
    mid = size / 2
    scale = (mid - 18) / range_m
    for metres in (25, 50, 100, 200, 500, 1000):
        if metres <= range_m and metres * scale > 14:
            r = metres * scale
            canvas.create_oval(mid - r, mid - r, mid + r, mid + r, outline="#3a3f4b" if metres != 100 else "#5a6580")
            canvas.create_text(mid + 4, mid - r + 3, text="%d m" % metres, fill="#6b7590", anchor="nw", font=(FONT, 8))
    draw_text(canvas, mid, 11, "N", "#9fb0d0", size=11)
    canvas.create_oval(mid - 6, mid - 6, mid + 6, mid + 6, fill="#ffffff", outline="#0b0c0f", width=2)
    return scale


def draw_me(canvas, x, y, r):
    """Your position: a white dot with a dark rim, readable on top of anything."""
    canvas.create_oval(x - r - 2, y - r - 2, x + r + 2, y + r + 2, fill="#000000", outline="")
    canvas.create_oval(x - r, y - r, x + r, y + r, fill="#ffffff", outline="")


def radar_point(position, point, mid, scale, range_m):
    """World point -> radar (x, y, beyond_range); beyond range it sits on the edge."""
    dist = math.hypot(point[0] - position[0], point[1] - position[1]) / 100
    a = math.radians(bearing(position, point))
    shown = min(dist, range_m)
    return mid + math.sin(a) * shown * scale, mid - math.cos(a) * shown * scale, dist > range_m


class Feature:
    """One sidebar entry: a self-contained feature with its own tabs, overlay and settings.
    To add a feature: subclass, set title/icon/TABS, implement build_<tab>(frame) for each tab, list the
    class in App.FEATURES. For an overlay: set overlay_key, implement paint_overlay(), and call
    build_overlay_tab() from build_overlay().
      background(snap): every tick, whatever is shown (alerts, the overlay)
      update(snap, tab): every tick, only while this feature and that tab are shown"""
    title, icon, TABS = "", "", ()
    overlay_key, overlay_color, overlay_text = None, ACCENT, ""
    tracks = None  # what the tracker parses for this feature ("cubes", "monsters"); skipped while off
    off_text = "Nothing is tracked, alerted or drawn while it's off."

    def __init__(self, app, parent):
        self.app, self.tk, self.root = app, app.tk, app.root
        self.overlay = None
        self.enabled = self.load_enabled()
        tk = self.tk
        self.frame = tk.Frame(parent, bg=BG)
        bar = tk.Frame(self.frame, bg=BG)
        bar.pack(fill="x", pady=(0, 10))
        self.body = tk.Frame(self.frame, bg=BG)
        self.body.pack(fill="both", expand=True)
        self.tabs, self.tab_buttons, self.tab = {}, {}, None
        for name in self.TABS:
            self.tabs[name] = tk.Frame(self.body, bg=BG)
            getattr(self, "build_" + name.lower().replace(" ", "_"))(self.tabs[name])
            b = tk.Label(bar, text=name, bg=BG, fg=MUTED, font=(FONT, 10, "bold"), padx=12, pady=5, cursor="hand2")
            b.pack(side="left")
            b.bind("<Button-1>", lambda e, n=name: self.show_tab(n))
            self.tab_buttons[name] = b
        self.switch = tk.Label(bar, text="", font=(FONT, 10, "bold"), padx=12, pady=5, cursor="hand2")
        self.switch.pack(side="right")
        self.switch.bind("<Button-1>", lambda e: self.set_enabled(not self.enabled))
        self.banner = tk.Label(self.frame, text="%s is off. %s Settings can still be changed." % (self.title, self.off_text),
                               bg="#2a2230", fg="#ff9ad9", font=(FONT, 9), anchor="w", justify="left", wraplength=400,
                               padx=10, pady=6)
        saved = load_settings().get("tabs", {}).get(self.title)
        self.show_tab(saved if saved in self.tabs else self.TABS[0])
        if self.enabled and self.overlay_key and load_settings().get(self.overlay_key, {}).get("shown"):
            self.toggle_overlay()
        self.enabled_changed()
        self.overlay_changed()

    # ---- on / off
    def load_enabled(self):
        return load_settings().get("features", {}).get(self.title, True)

    def store_enabled(self, on):
        settings = load_settings()
        save_settings(dict(settings, features=dict(settings.get("features", {}), **{self.title: on})))

    def set_enabled(self, on):
        if on == self.enabled:
            return
        self.enabled = on
        self.store_enabled(on)
        if self.overlay_key:
            if not on and self.overlay:  # hide it, but remember it was shown for when it's back on
                self.overlay.close()
                self.overlay = None
            elif on and load_settings().get(self.overlay_key, {}).get("shown"):
                self.toggle_overlay()
        self.enabled_changed()
        self.overlay_changed()

    def enabled_changed(self):
        """Switch, banner, sidebar entry, and what the tracker parses."""
        self.switch.config(text="● On" if self.enabled else "○ Off", bg="#1f3a2a" if self.enabled else BUTTON,
                           fg="#7cf08a" if self.enabled else MUTED)
        if self.enabled:
            self.banner.pack_forget()
        else:
            self.banner.pack(fill="x", pady=(0, 8), before=self.body)
        if self.tracks:
            setattr(self.app.tracker, "track_" + self.tracks, self.enabled)
            if not self.enabled:
                with self.app.tracker.lock:
                    getattr(self.app.tracker, self.tracks).clear()
        self.app.feature_state_changed(self)

    def show_tab(self, name):
        if self.tab:
            self.tabs[self.tab].pack_forget()
            self.tab_buttons[self.tab].config(fg=MUTED, bg=BG)
        self.tab = name
        self.tabs[name].pack(fill="both", expand=True)
        self.tab_buttons[name].config(fg=ACCENT, bg=BUTTON)
        settings = load_settings()
        save_settings(dict(settings, tabs=dict(settings.get("tabs", {}), **{self.title: name})))
        self.update(self.app.tracker.snapshot(), name)

    # ---- overlay (shared by every feature that has one)
    def build_overlay_tab(self, f):
        tk = self.tk
        heading(tk, f, "%s overlay" % self.title, self.overlay_text +
                " It keeps running whichever page is open. The game must be in borderless or windowed mode.")
        row = tk.Frame(f, bg=BG)
        row.pack(fill="x")
        self.show_button = button(tk, row, "Show overlay", self.toggle_overlay, width=14)
        self.show_button.pack(side="left", padx=(0, 6))
        self.lock_button = button(tk, row, "Lock", self.toggle_lock, width=10)
        self.lock_button.pack(side="left")
        self.overlay_state = tk.Label(f, text="", bg=BG, fg=MUTED, font=(FONT, 9), anchor="w", justify="left")
        self.overlay_state.pack(fill="x", pady=(8, 0))
        self.cone_var = tk.BooleanVar(value=self.overlay_cfg()["cone"])
        checkbox(tk, f, "Show view cone on this overlay (needs View cone on)", self.cone_var,
                 self.set_overlay_cone).pack(fill="x", pady=(6, 0))
        section(tk, f, "Scale")
        row = tk.Frame(f, bg=BG)
        row.pack(fill="x")
        button(tk, row, "−", lambda: self.overlay_scale(1 / 1.1), width=3).pack(side="left", padx=(0, 4))
        button(tk, row, "+", lambda: self.overlay_scale(1.1), width=3).pack(side="left", padx=(0, 8))
        self.scale_label = tk.Label(row, text="", bg=BG, fg=TEXT, font=(FONT, 10), anchor="w")
        self.scale_label.pack(side="left")
        section(tk, f, "Position")
        row = tk.Frame(f, bg=BG)
        row.pack(fill="x")
        button(tk, row, "Bring it here", self.overlay_recenter).pack(side="left")
        tk.Label(row, text="if it got lost off-screen", bg=BG, fg=MUTED, font=(FONT, 9)).pack(side="left", padx=8)
        section(tk, f, "How to line it up")
        tk.Label(f, bg=BG, fg=MUTED, font=(FONT, 9), anchor="w", justify="left", wraplength=390, text=(
            "1. Unlocked, drag the overlay so its crosshair sits on your arrow.\n"
            "2. Drag its bottom-right corner to the minimap's size.\n"
            "3. Mouse wheel (or − / + here) until its rings match the game's distances.\n"
            "4. Lock: right-click it, the Lock button, or Ctrl+Shift+O (works in game). Locked, "
            "clicks go through to the game. Ctrl+Shift+O locks / unlocks every shown overlay.")).pack(fill="x")

    def overlay_cfg(self):
        return self.overlay.cfg if self.overlay else dict(OVERLAY_DEFAULT, **load_settings().get(self.overlay_key, {}))

    def toggle_overlay(self):
        if not self.enabled and not self.overlay:
            return
        if self.overlay:
            self.overlay.close()
            self.overlay = None
            save_settings(dict(load_settings(), **{self.overlay_key: dict(self.overlay_cfg(), shown=False)}))
        else:
            self.overlay = Overlay(self)
            self.overlay.cfg["shown"] = True
            self.overlay.save()
        self.overlay_changed()

    def toggle_lock(self):
        if self.overlay:
            self.overlay.set_locked(not self.overlay.cfg["locked"])

    def set_overlay_cone(self):
        cfg = self.overlay_cfg()
        cfg["cone"] = self.cone_var.get()
        save_settings(dict(load_settings(), **{self.overlay_key: cfg}))
        if self.overlay and not cfg["cone"]:
            self.overlay.canvas.delete("cone")
        self.app.cone_serial = -1  # redraw (or drop) the cone right away

    def overlay_scale(self, factor):
        cfg = self.overlay_cfg()
        cfg["ppm"] = max(0.05, min(20.0, cfg["ppm"] * factor))
        save_settings(dict(load_settings(), **{self.overlay_key: cfg}))
        self.overlay_changed()

    def overlay_recenter(self):
        if not self.overlay:
            self.toggle_overlay()
        cfg = self.overlay.cfg
        cfg["x"] = self.root.winfo_rootx() + self.root.winfo_width() // 2 - cfg["w"] // 2
        cfg["y"] = self.root.winfo_rooty() + self.root.winfo_height() // 2 - cfg["h"] // 2
        self.overlay.place()
        if cfg["locked"]:
            self.overlay.set_locked(False)  # so it can be dragged into place
        self.overlay.save()
        self.overlay_changed()

    def overlay_changed(self):
        if not self.overlay_key or not hasattr(self, "show_button"):
            return  # no overlay, or still building
        overlay, cfg = self.overlay, self.overlay_cfg()
        self.show_button.config(text="Hide overlay" if overlay else "Show overlay",
                                state="normal" if self.enabled else "disabled")
        self.lock_button.config(state="normal" if overlay else "disabled", text="Unlock" if cfg["locked"] else "Lock")
        if overlay:
            state = "Shown · %s · %d×%d at %d, %d" % (
                "locked (clicks go to the game)" if cfg["locked"] else "unlocked (drag / resize / wheel)",
                cfg["w"], cfg["h"], cfg["x"], cfg["y"])
        else:
            state = "Hidden"
        self.overlay_state.config(text=state + ("\nHotkey: Ctrl+Shift+O locks / unlocks shown overlays." if WINDOWS else ""))
        self.scale_label.config(text="%d m across (1 px = %.2f m)" % (cfg["w"] / cfg["ppm"], 1 / cfg["ppm"]))
        self.app.set_badge(self, None if not overlay else "overlay " + ("locked" if cfg["locked"] else "unlocked"))

    def paint_overlay(self, overlay, canvas, snap):
        pass

    # ---- loop hooks
    def background(self, snap):
        if self.overlay:
            self.overlay.draw(snap)

    def update(self, snap, tab):
        if tab == "Overlay":
            self.overlay_changed()  # it can be dragged or scaled from the overlay itself

    def status(self, snap):
        return None

    def close(self):
        pass


class CubesFeature(Feature):
    """Hidden Cubes: radar + list, the minimap overlay, and the alert settings."""
    title, icon, TABS = "Cubes", "◈", ("Radar", "Overlay", "Settings")
    overlay_key, overlay_color = "overlay", "#4fd8ff"
    overlay_text = "A see-through layer with only the cube pins, to place over the game's minimap."
    tracks = "cubes"

    def __init__(self, app, parent):
        tk, args = app.tk, app.args
        self.range = args.range
        self.seen_detections = 0
        self.voice = tk.StringVar(value=args.voice)
        self.volume = tk.IntVar(value=args.volume)
        super().__init__(app, parent)

    # ---- Radar tab
    def build_radar(self, f):
        tk = self.tk
        self.size = size = 380
        self.canvas = tk.Canvas(f, width=size, height=size, bg=PANEL, highlightthickness=0)
        self.canvas.pack(anchor="w")
        bar = tk.Frame(f, bg=BG)
        bar.pack(fill="x", pady=(6, 0))
        button(tk, bar, "−", lambda: self.zoom(1.5), width=3).pack(side="left", padx=(0, 4))
        button(tk, bar, "+", lambda: self.zoom(1 / 1.5), width=3).pack(side="left", padx=(0, 4))
        button(tk, bar, "Clear", self.clear_cubes).pack(side="left")
        listing = tk.Frame(f, bg=BG)
        listing.pack(fill="both", expand=True, pady=(8, 0))
        self.rows = [tk.Label(listing, text="", bg=BG, fg=TEXT, anchor="w", font=(MONO, 11)) for _ in range(8)]
        for row in self.rows:
            row.pack(fill="x")

    def zoom(self, factor):
        self.range = max(25, min(2000, self.range * factor))

    def clear_cubes(self):
        with self.app.tracker.lock:
            self.app.tracker.cubes.clear()

    def draw_radar(self, snap):
        c, size = self.canvas, self.size
        mid = size / 2
        c.delete("all")
        draw_cone(c, mid, mid, mid - 18, snap.get("cone"), fill="#2b3346")  # under the rings and pins
        scale = draw_rings(c, size, self.range)
        x = 8  # legend
        for kind in ("above", "level", "below"):
            color, mark = HEIGHT[kind]
            label = "%s %s" % (mark, kind)
            c.create_text(x, size - 9, text=label, fill=color, anchor="w", font=(FONT, 9, "bold"))
            x += 8 + 7 * len(label)
        position = snap["position"]
        rows = []
        if position:
            for cube in snap["cubes"]:
                dist, dz, direction = relative(position, cube["xyz"])
                rows.append((dist, dz, direction, cube))
            rows.sort(key=lambda r: (r[3]["status"] == "opened", r[3]["status"] != "nearby", r[0]))
            for dist, dz, direction, cube in reversed(rows):
                if cube["status"] == "opened":
                    continue
                kind = height_class(dz)
                nearby = cube["status"] == "nearby"
                color = HEIGHT[kind][0] if nearby else dim(HEIGHT[kind][0])
                x, y, beyond = radar_point(position, cube["xyz"], mid, scale, self.range)
                draw_pin(c, x, y, kind, color, solid=nearby and not beyond)
                label = "%d m" % dist
                if kind != "level":
                    label += " %s%d" % ("+" if dz > 0 else "−", abs(dz))  # the pin's shape already says ▲/▼
                lx, anchor = label_side(x, mid, 8 * len(label), size)  # rough bold 10 pt width
                draw_text(c, lx, y, label, color, anchor=anchor)
        else:
            draw_text(c, mid, mid + 30, "waiting for your position\n(teleport)", "#b8c0cc", size=10, bold=False)
        for i, row in enumerate(self.rows):
            if i < len(rows):
                dist, dz, direction, cube = rows[i]
                kind = height_class(dz)
                height = "%s %3d m" % (HEIGHT[kind][1], abs(dz)) if kind != "level" else "  level"
                if cube["status"] == "nearby":
                    color = HEIGHT[kind][0]
                else:
                    color = "#5f6670" if cube["status"] == "opened" else dim(HEIGHT[kind][0], 0.35)
                row.config(text="%5d m  %-2s  %s  %-9s %4ds" % (dist, direction, height, cube["status"], cube["age"]),
                           fg=color)
            elif i == 0 and position:
                row.config(text="No cubes received yet.", fg=MUTED)
            else:
                row.config(text="")

    # ---- Overlay tab
    def build_overlay(self, f):
        self.build_overlay_tab(f)

    def paint_overlay(self, overlay, c, snap):
        position = snap["position"]
        w = overlay.cfg["w"]
        cubes = sorted(snap["cubes"], key=lambda cube: -math.dist(cube["xyz"][:2], position[:2]))
        for cube in cubes:  # nearest drawn last, on top
            if cube["status"] == "opened":
                continue
            dist, dz, _ = relative(position, cube["xyz"])
            kind = height_class(dz)
            nearby = cube["status"] == "nearby"
            color = HEIGHT[kind][0] if nearby else dim(HEIGHT[kind][0], 0.35)
            x, y, edge = overlay.to_screen(position, cube["xyz"])
            draw_pin(c, x, y, kind, color, solid=nearby and not edge, r=7, halo="#000000")
            label = "%d m" % dist
            if kind != "level":
                label += " %s%d" % ("+" if dz > 0 else "−", abs(dz))
            lx, anchor = label_side(x, w / 2, 7 * len(label), w, gap=10)
            draw_text(c, lx, y, label, color, outline="#000000", anchor=anchor, size=9)

    # ---- Settings tab
    def build_settings(self, f):
        tk = self.tk
        heading(tk, f, "Cube settings")
        section(tk, f, "Alert when a cube appears")
        row = tk.Frame(f, bg=BG)
        row.pack(fill="x")
        menu = tk.OptionMenu(row, self.voice, *voices(), "custom", command=self.pick_voice)
        menu.config(bg=BUTTON, fg=TEXT, activebackground=BUTTON_HOVER, activeforeground=TEXT, relief="flat",
                    highlightthickness=0, width=13, font=(FONT, 10))
        menu["menu"].config(bg=BUTTON, fg=TEXT, font=(FONT, 10))
        menu.pack(side="left", padx=(0, 6))
        button(tk, row, "▶ Test", self.test_sound).pack(side="left", padx=(0, 4))
        button(tk, row, "File…", self.pick_file).pack(side="left")
        self.file_label = tk.Label(f, text="", bg=BG, fg=MUTED, font=(FONT, 9), anchor="w")
        self.file_label.pack(fill="x", pady=(4, 0))
        row = tk.Frame(f, bg=BG)
        row.pack(fill="x", pady=(6, 0))
        tk.Label(row, text="Volume", bg=BG, fg=MUTED, font=(FONT, 10)).pack(side="left")
        scale = tk.Scale(row, from_=0, to=200, orient="horizontal", variable=self.volume, showvalue=True,
                         resolution=5, length=250, bg=BG, fg=TEXT, troughcolor=BUTTON, highlightthickness=0,
                         activebackground=ACCENT, font=(FONT, 9))
        scale.pack(side="left", padx=6)
        scale.bind("<ButtonRelease-1>", lambda e: self.set_volume())
        tk.Label(row, text="%", bg=BG, fg=MUTED, font=(FONT, 10)).pack(side="left")
        section(tk, f, "Cube types tracked")
        tk.Label(f, bg=BG, fg=MUTED, font=(MONO, 9), anchor="w", justify="left", wraplength=380,
                 text="  ".join(str(t) for t in sorted(TEMPLATES)) +
                 "\nAdd more with --template ID (find ids with --log).").pack(fill="x")
        self.show_file()

    def show_file(self):
        name = Path(self.app.args.sound).name if self.app.args.sound else ""
        self.file_label.config(text="Custom file: " + name if self.voice.get() == "custom" and name else "")

    def test_sound(self):
        play(self.voice.get(), self.app.args.sound, self.volume.get())

    def pick_voice(self, voice):
        if voice == "custom" and not self.app.args.sound:
            return self.pick_file()
        save_settings(dict(load_settings(), voice=voice))
        self.show_file()
        self.test_sound()

    def pick_file(self):
        from tkinter import filedialog
        path = filedialog.askopenfilename(parent=self.root, title="Cube alert sound", initialdir=str(SOUNDS / "custom"),
                                          filetypes=[("Audio", "*.wav *.mp3"), ("WAV", "*.wav"), ("MP3", "*.mp3"),
                                                     ("All files", "*.*")])
        if not path:
            if self.voice.get() == "custom" and not self.app.args.sound:
                self.voice.set(load_settings().get("voice", "calm"))
            return
        self.app.args.sound = os.path.normpath(path)
        self.voice.set("custom")
        save_settings(dict(load_settings(), voice="custom", custom=self.app.args.sound))
        self.show_file()
        self.test_sound()

    def set_volume(self):
        save_settings(dict(load_settings(), volume=self.volume.get()))
        self.test_sound()

    # ---- loop hooks
    def background(self, snap):
        if snap["detections"] > self.seen_detections:  # alerts: whatever is shown
            new = next((c for c in snap["cubes"] if c["id"] == snap["last_detection"]), None)
            where = ""
            if new and snap["position"]:
                dist, dz, compass = relative(snap["position"], new["xyz"])
                where = " %d m %s" % (dist, compass)
            self.app.engine.say("Cube!" + where)
            play(self.voice.get(), self.app.args.sound, self.volume.get())
            self.seen_detections = snap["detections"]
        super().background(snap)

    def update(self, snap, tab):
        if tab == "Radar":
            self.draw_radar(snap)
        super().update(snap, tab)

    def status(self, snap):
        nearby = sum(1 for c in snap["cubes"] if c["status"] == "nearby")
        return "%d cube%s nearby" % (nearby, "" if nearby == 1 else "s")


WATCH_COLORS = ("#ff5c5c", "#7cf08a", "#ffb347", "#c58cff", "#4fd8ff", "#ffd24a", "#ff6ad5", "#9fe3ff")
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


GATHER_DEFAULT = dict(watch=[], mode="watched", map_nodes=True, alert=True, me_on_top=True, unnamed=False)


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
        self.map_key = None
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

    def learn(self, snap):
        """Which map you're on, and names for node types seen for the first time (saved for good)."""
        if not snap["position"] or time.time() - self.map_checked < 2:
            return
        self.map_checked = time.time()
        points = [n["xyz"] for n in snap["nodes"]]
        key = self.mapdata.which_map(points) if points else None
        if key and key != self.map_key:
            self.map_key = key
            self.app.engine.say("Gathering: map is %s" % key)
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
        self.map_label = tk.Label(bar, text="", bg=BG, fg=MUTED, font=(FONT, 9))
        self.map_label.pack(side="right")
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
        self.map_label.config(text=("map: %s" % self.map_key) if self.map_key else "map: not known yet")
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


import datetime as _dt

# Built-in events, in server time (EU servers run on Berlin time; see the clock offset in Alerts > Settings).
# Times as published for every Global region (checked 2026-10-14).
BUILTIN_ALARMS = [
    dict(id="shugo", name="Shugo Festival", rule=dict(type="hourly", minute=0), lead=2, builtin=True),
    dict(id="invasion", name="Dimensional Invasion", rule=dict(type="hourly", minute=30), lead=2, builtin=True),
    dict(id="rift", name="Spacetime Rift", rule=dict(type="every", hours=3, start="02:00"), lead=5, builtin=True),
]
ALERTS_DEFAULT = dict(voice="system beep", custom=None, volume=100, popup=True, at_start=False, offset=0,
                      disabled=[], custom_alarms=[])


def next_occurrence(rule, now):
    """Next start (naive datetime, server clock) of a rule strictly after `now`, or None (a past one-off)."""
    kind = rule["type"]
    if kind == "hourly":
        t = now.replace(minute=rule["minute"], second=0, microsecond=0)
        return t if t > now else t + _dt.timedelta(hours=1)
    if kind == "daily":
        hh, mm = map(int, rule["time"].split(":"))
        t = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        return t if t > now else t + _dt.timedelta(days=1)
    if kind == "every":  # every N hours from HH:MM, repeating across midnight on a 24 h cycle
        hh, mm = map(int, rule["start"].split(":"))
        step = int(rule["hours"])
        base = now.replace(hour=hh, minute=mm, second=0, microsecond=0) - _dt.timedelta(days=1)
        if 24 % step == 0:  # the same times every day: 02:00, 05:00, ... for every 3 h from 02:00
            while base <= now:
                base += _dt.timedelta(hours=step)
            return base
        t = base
        while t <= now:
            t += _dt.timedelta(hours=step)
        return t
    if kind == "once":
        t = _dt.datetime.strptime(rule["at"], "%Y-%m-%d %H:%M")
        return t if t > now else None
    return None


def describe(rule):
    kind = rule["type"]
    if kind == "hourly":
        return "every hour at :%02d" % rule["minute"]
    if kind == "daily":
        return "daily at %s" % rule["time"]
    if kind == "every":
        return "every %dh from %s" % (int(rule["hours"]), rule["start"])
    if kind == "once":
        return "once, %s" % rule["at"]
    return "?"


class Toast:
    """A small notification on top of everything (the game too) for a few seconds; click to dismiss.
    Doesn't take focus from the game."""

    def __init__(self, root, text, color=ACCENT, seconds=8):
        tk = __import__("tkinter")
        self.win = win = tk.Toplevel(root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg="#000000")
        frame = tk.Frame(win, bg=PANEL, highlightbackground=color, highlightthickness=2)
        frame.pack(padx=1, pady=1)
        label = tk.Label(frame, text=text, bg=PANEL, fg=TEXT, font=(FONT, 12, "bold"), padx=16, pady=10, justify="left")
        label.pack()
        win.update_idletasks()
        x = (root.winfo_screenwidth() - win.winfo_reqwidth()) // 2
        win.geometry("+%d+%d" % (x, 60))
        click_through(win, False)  # tool window, never activated: the game keeps focus
        for widget in (win, frame, label):
            widget.bind("<Button-1>", lambda e: self.close())
        win.after(seconds * 1000, self.close)

    def close(self):
        try:
            self.win.destroy()
        except Exception:
            pass


class AlertsFeature(Feature):
    """Timed alarms: the game's recurring events (Shugo Festival, Dimensional Invasion, Spacetime Rift) and your
    own. Each warns a set number of minutes before (sound + popup on top of the game), whatever page is shown."""
    title, icon, TABS = "Alerts", "⏰", ("Upcoming", "Add alarm", "Settings")
    off_text = "No alarms ring while it's off."

    def __init__(self, app, parent):
        self.cfg = dict(ALERTS_DEFAULT, **load_settings().get("alerts", {}))
        self.fired = set()
        self.row_widgets = []
        self.rows_key = None
        super().__init__(app, parent)

    def save(self):
        save_settings(dict(load_settings(), alerts=self.cfg))

    def now(self):
        return _dt.datetime.now() + _dt.timedelta(hours=self.cfg["offset"])

    def alarms(self):
        return BUILTIN_ALARMS + self.cfg["custom_alarms"]

    def enabled_alarm(self, alarm):
        return alarm["id"] not in self.cfg["disabled"]

    def upcoming(self):
        now = self.now()
        out = []
        for alarm in self.alarms():
            when = next_occurrence(alarm["rule"], now)
            if when is not None:
                out.append((when, alarm))
        return sorted(out, key=lambda x: x[0])

    # ---- Upcoming tab
    def build_upcoming(self, f):
        tk = self.tk
        heading(tk, f, "Upcoming", "Click a name to switch that alarm on or off. Your own alarms have a × to delete "
                "them. Times are server time (EU servers: Berlin time).")
        self.clock = tk.Label(f, text="", bg=BG, fg=MUTED, font=(MONO, 10), anchor="w")
        self.clock.pack(fill="x", pady=(0, 6))
        self.rows_frame = tk.Frame(f, bg=BG)
        self.rows_frame.pack(fill="both", expand=True)

    def rebuild_rows(self, items):
        tk = self.tk
        for w in self.row_widgets:
            w.destroy()
        self.row_widgets = []
        for when, alarm in items:
            row = tk.Frame(self.rows_frame, bg=BG)
            row.pack(fill="x", pady=1)
            name = tk.Label(row, text="", bg=BG, fg=TEXT, font=(MONO, 10), anchor="w", cursor="hand2")
            name.pack(side="left", fill="x", expand=True)
            name.bind("<Button-1>", lambda e, a=alarm: self.toggle_alarm(a))
            if not alarm.get("builtin"):
                x = tk.Label(row, text=" × ", bg=BG, fg="#ff6ad5", font=(FONT, 11, "bold"), cursor="hand2")
                x.pack(side="right")
                x.bind("<Button-1>", lambda e, a=alarm: self.delete_alarm(a))
            self.row_widgets.append(row)
            row.name_label = name

    def draw_upcoming(self):
        items = self.upcoming()
        key = tuple(a["id"] for _, a in items)
        if key != self.rows_key:
            self.rows_key = key
            self.rebuild_rows(items)
        now = self.now()
        self.clock.config(text="Server time %s%s" % (now.strftime("%H:%M:%S"),
                                                     "" if not self.cfg["offset"] else "  (PC %+dh)" % self.cfg["offset"]))
        for (when, alarm), row in zip(items, self.row_widgets):
            left = int((when - now).total_seconds())
            on = self.enabled_alarm(alarm)
            countdown = "%d:%02d:%02d" % (left // 3600, left // 60 % 60, left % 60) if left >= 3600 else "%d:%02d" % (left // 60, left % 60)
            row.name_label.config(
                text="%s %-22.22s %s  in %8s   %s" % ("●" if on else "○", alarm["name"], when.strftime("%H:%M"),
                                                     countdown, describe(alarm["rule"])),
                fg=(ACCENT if left <= alarm["lead"] * 60 else TEXT) if on else "#5a606a")

    def toggle_alarm(self, alarm):
        if alarm["id"] in self.cfg["disabled"]:
            self.cfg["disabled"].remove(alarm["id"])
        else:
            self.cfg["disabled"].append(alarm["id"])
        self.save()

    def delete_alarm(self, alarm):
        self.cfg["custom_alarms"] = [a for a in self.cfg["custom_alarms"] if a["id"] != alarm["id"]]
        self.save()

    # ---- Add alarm tab
    def build_add_alarm(self, f):
        tk = self.tk
        heading(tk, f, "Add your own alarm")
        section(tk, f, "Name")
        self.new_name = tk.StringVar(value="")
        tk.Entry(f, textvariable=self.new_name, bg=BUTTON, fg=TEXT, insertbackground=TEXT, relief="flat",
                 font=(FONT, 10)).pack(fill="x", ipady=3)
        section(tk, f, "When")
        self.new_kind = tk.StringVar(value="daily")
        self.new_time = tk.StringVar(value=(_dt.datetime.now() + _dt.timedelta(minutes=10)).strftime("%H:%M"))
        self.new_hours = tk.StringVar(value="3")
        self.new_minute = tk.StringVar(value="0")
        for kind, text in (("daily", "Every day at"), ("every", "Every … hours, starting at"),
                           ("hourly", "Every hour at minute"), ("once", "Once, next time it's")):
            tk.Radiobutton(f, text=text, value=kind, variable=self.new_kind, bg=BG, fg=TEXT, selectcolor=BUTTON,
                           activebackground=BG, activeforeground=TEXT, font=(FONT, 10), anchor="w").pack(fill="x")
        row = tk.Frame(f, bg=BG)
        row.pack(fill="x", pady=(6, 0))
        for label, var, width in (("time HH:MM", self.new_time, 6), ("every hours", self.new_hours, 3),
                                  ("minute", self.new_minute, 3)):
            tk.Label(row, text=label, bg=BG, fg=MUTED, font=(FONT, 9)).pack(side="left", padx=(0, 4))
            tk.Entry(row, textvariable=var, width=width, bg=BUTTON, fg=TEXT, insertbackground=TEXT, relief="flat",
                     font=(MONO, 10), justify="center").pack(side="left", padx=(0, 12), ipady=2)
        section(tk, f, "Warn me")
        row = tk.Frame(f, bg=BG)
        row.pack(fill="x")
        self.new_lead = tk.StringVar(value="2")
        tk.Entry(row, textvariable=self.new_lead, width=4, bg=BUTTON, fg=TEXT, insertbackground=TEXT, relief="flat",
                 font=(MONO, 10), justify="center").pack(side="left", ipady=2)
        tk.Label(row, text="minutes before (0 = when it starts)", bg=BG, fg=MUTED, font=(FONT, 9)).pack(side="left", padx=6)
        row = tk.Frame(f, bg=BG)
        row.pack(fill="x", pady=(12, 0))
        button(tk, row, "+ Add alarm", self.add_alarm).pack(side="left")
        self.add_message = tk.Label(row, text="", bg=BG, fg=MUTED, font=(FONT, 9))
        self.add_message.pack(side="left", padx=8)

    def add_alarm(self):
        name = self.new_name.get().strip()
        kind = self.new_kind.get()
        try:
            if not name:
                raise ValueError("give it a name")
            lead = int(self.new_lead.get())
            if not 0 <= lead <= 180:
                raise ValueError("warn 0-180 minutes before")
            if kind in ("daily", "every", "once"):
                hh, mm = map(int, self.new_time.get().strip().split(":"))
                if not (0 <= hh < 24 and 0 <= mm < 60):
                    raise ValueError
                hhmm = "%02d:%02d" % (hh, mm)
            if kind == "daily":
                rule = dict(type="daily", time=hhmm)
            elif kind == "every":
                hours = int(self.new_hours.get())
                if not 1 <= hours <= 24:
                    raise ValueError("every 1-24 hours")
                rule = dict(type="every", hours=hours, start=hhmm)
            elif kind == "hourly":
                minute = int(self.new_minute.get())
                if not 0 <= minute < 60:
                    raise ValueError("minute 0-59")
                rule = dict(type="hourly", minute=minute)
            else:
                now = self.now()
                t = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
                if t <= now:
                    t += _dt.timedelta(days=1)
                rule = dict(type="once", at=t.strftime("%Y-%m-%d %H:%M"))
        except ValueError as e:
            self.add_message.config(text="Check the fields: " + (str(e) or "time as HH:MM"), fg="#ff6ad5")
            return
        alarm = dict(id="custom-%d" % int(time.time() * 1000), name=name, rule=rule, lead=lead)
        self.cfg["custom_alarms"].append(alarm)
        self.save()
        when = next_occurrence(rule, self.now())
        self.add_message.config(text="Added: %s, next at %s" % (name, when.strftime("%H:%M") if when else "?"), fg="#7cf08a")
        self.new_name.set("")

    # ---- Settings tab
    def build_settings(self, f):
        tk = self.tk
        heading(tk, f, "Alert settings")
        section(tk, f, "Sound")
        row = tk.Frame(f, bg=BG)
        row.pack(fill="x")
        self.voice = tk.StringVar(value=self.cfg["voice"])
        menu = tk.OptionMenu(row, self.voice, *voices(), "custom", command=self.pick_voice)
        menu.config(bg=BUTTON, fg=TEXT, activebackground=BUTTON_HOVER, activeforeground=TEXT, relief="flat",
                    highlightthickness=0, width=13, font=(FONT, 10))
        menu["menu"].config(bg=BUTTON, fg=TEXT, font=(FONT, 10))
        menu.pack(side="left", padx=(0, 6))
        button(tk, row, "▶ Test", self.test_alert).pack(side="left", padx=(0, 4))
        button(tk, row, "File…", self.pick_file).pack(side="left")
        self.file_label = tk.Label(f, text="", bg=BG, fg=MUTED, font=(FONT, 9), anchor="w")
        self.file_label.pack(fill="x", pady=(4, 0))
        row = tk.Frame(f, bg=BG)
        row.pack(fill="x", pady=(6, 0))
        tk.Label(row, text="Volume", bg=BG, fg=MUTED, font=(FONT, 10)).pack(side="left")
        self.volume = tk.IntVar(value=self.cfg["volume"])
        scale = tk.Scale(row, from_=0, to=200, orient="horizontal", variable=self.volume, showvalue=True,
                         resolution=5, length=250, bg=BG, fg=TEXT, troughcolor=BUTTON, highlightthickness=0,
                         activebackground=ACCENT, font=(FONT, 9))
        scale.pack(side="left", padx=6)
        scale.bind("<ButtonRelease-1>", lambda e: self.set_flags())
        section(tk, f, "Show")
        self.popup = tk.BooleanVar(value=self.cfg["popup"])
        self.at_start = tk.BooleanVar(value=self.cfg["at_start"])
        checkbox(tk, f, "Popup on top of the game", self.popup, self.set_flags).pack(fill="x")
        checkbox(tk, f, "Also alert when it starts", self.at_start, self.set_flags).pack(fill="x")
        section(tk, f, "Server clock")
        row = tk.Frame(f, bg=BG)
        row.pack(fill="x")
        self.offset = tk.IntVar(value=self.cfg["offset"])
        tk.Spinbox(row, from_=-23, to=23, textvariable=self.offset, width=4, command=self.set_flags, bg=BUTTON, fg=TEXT,
                   buttonbackground=BUTTON, relief="flat", font=(MONO, 10), justify="center").pack(side="left")
        tk.Label(row, text="hours from this PC's clock (0 if you play on servers in your time zone; EU servers "
                 "run on Berlin time)", bg=BG, fg=MUTED, font=(FONT, 9), wraplength=330, justify="left").pack(side="left", padx=6)
        self.show_file()

    def show_file(self):
        name = Path(self.cfg["custom"]).name if self.cfg["custom"] else ""
        self.file_label.config(text="Custom file: " + name if self.voice.get() == "custom" and name else "")

    def pick_voice(self, voice):
        if voice == "custom" and not self.cfg["custom"]:
            return self.pick_file()
        self.set_flags()
        self.test_alert()

    def pick_file(self):
        from tkinter import filedialog
        path = filedialog.askopenfilename(parent=self.root, title="Alarm sound", initialdir=str(SOUNDS / "custom"),
                                          filetypes=[("Audio", "*.wav *.mp3"), ("All files", "*.*")])
        if path:
            self.cfg["custom"] = os.path.normpath(path)
            self.voice.set("custom")
        elif self.voice.get() == "custom" and not self.cfg["custom"]:
            self.voice.set("system beep")
        self.set_flags()

    def set_flags(self):
        try:
            offset = int(self.offset.get())
        except (ValueError, self.tk.TclError):
            offset = 0
        self.cfg.update(voice=self.voice.get(), volume=self.volume.get(), popup=self.popup.get(),
                        at_start=self.at_start.get(), offset=max(-23, min(23, offset)))
        self.save()
        self.show_file()

    def test_alert(self):
        self.ring("Test alarm", "This is how alarms look and sound.")

    # ---- ringing
    def ring(self, title, detail):
        self.app.engine.say("Alarm: %s - %s" % (title, detail))
        play(self.cfg["voice"], self.cfg["custom"], self.cfg["volume"])
        if self.cfg["popup"]:
            Toast(self.root, "⏰ %s\n%s" % (title, detail))

    def background(self, snap):
        now = self.now()
        for when, alarm in self.upcoming():
            if not self.enabled_alarm(alarm):
                continue
            left = (when - now).total_seconds()
            key = (alarm["id"], when.isoformat())
            if left <= alarm["lead"] * 60 and key not in self.fired:
                self.fired.add(key)
                minutes = int(round(left / 60))
                self.ring(alarm["name"], "starts at %s (in %d min)" % (when.strftime("%H:%M"), minutes) if minutes >= 1
                          else "starting now")
            start_key = key + ("start",)
            if self.cfg["at_start"] and alarm["lead"] > 0 and left <= 1 and start_key not in self.fired:
                self.fired.add(start_key)
                self.ring(alarm["name"], "starting now")
        if len(self.fired) > 500:
            self.fired = set(list(self.fired)[-200:])

    def update(self, snap, tab):
        if tab == "Upcoming":
            self.draw_upcoming()
        super().update(snap, tab)

    def status(self, snap):
        items = [(w, a) for w, a in self.upcoming() if self.enabled_alarm(a)]
        if not items:
            return None
        when, alarm = items[0]
        left = int((when - self.now()).total_seconds())
        return "%s in %d:%02d" % (alarm["name"].split()[0], left // 60, left % 60) if left < 3600 else \
            "%s at %s" % (alarm["name"].split()[0], when.strftime("%H:%M"))


# ---------------------------------------------------------------- vision cone (read from the game's minimap)

def screen_grab(x, y, w, h):
    """Windows: the screen pixels in (x, y, w, h) as top-down BGRA bytes (GDI, no extra packages)."""
    from ctypes import wintypes
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    user32.GetDC.restype = gdi32.CreateCompatibleDC.restype = gdi32.CreateCompatibleBitmap.restype = ctypes.c_void_p
    gdi32.SelectObject.restype = ctypes.c_void_p
    for f, args in ((user32.GetDC, [ctypes.c_void_p]), (user32.ReleaseDC, [ctypes.c_void_p, ctypes.c_void_p]),
                    (gdi32.CreateCompatibleDC, [ctypes.c_void_p]), (gdi32.DeleteDC, [ctypes.c_void_p]),
                    (gdi32.CreateCompatibleBitmap, [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]),
                    (gdi32.SelectObject, [ctypes.c_void_p, ctypes.c_void_p]), (gdi32.DeleteObject, [ctypes.c_void_p]),
                    (gdi32.BitBlt, [ctypes.c_void_p] + [ctypes.c_int] * 4 + [ctypes.c_void_p, ctypes.c_int,
                                                                              ctypes.c_int, wintypes.DWORD]),
                    (gdi32.GetDIBits, [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p,
                                       ctypes.c_void_p, ctypes.c_uint])):
        f.argtypes = args

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                    ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                    ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD),
                    ("biClrImportant", wintypes.DWORD)]

    screen = user32.GetDC(None)
    dc = gdi32.CreateCompatibleDC(screen)
    bitmap = gdi32.CreateCompatibleBitmap(screen, w, h)
    old = gdi32.SelectObject(dc, bitmap)
    try:
        gdi32.BitBlt(dc, 0, 0, w, h, screen, x, y, 0x00CC0020)  # SRCCOPY
        info = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)  # top-down
        buf = ctypes.create_string_buffer(w * h * 4)
        gdi32.GetDIBits(dc, bitmap, 0, h, buf, ctypes.byref(info), 0)
        return buf.raw
    finally:
        gdi32.SelectObject(dc, old)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(dc)
        user32.ReleaseDC(None, screen)


def exclude_from_capture(window):
    """Windows 10 2004+: keep our own overlays out of screen captures, so they never fool the cone reader."""
    if WINDOWS:
        try:
            ctypes.windll.user32.SetWindowDisplayAffinity(ctypes.c_void_p(int(window.wm_frame(), 16)), 0x11)
        except (OSError, ValueError):
            pass


_HOUGH_TRIG = [(math.cos(math.radians(t)), math.sin(math.radians(t))) for t in range(0, 180, 2)]
_RING_CACHE = {}


def _ring_pixels(w, h, r):
    """(byte offset, x, y relative to the centre) of every 2nd pixel outside 0.4 r, cached per size."""
    key = (w, h, r)
    if key not in _RING_CACHE:
        cx, cy = w / 2, h / 2
        _RING_CACHE[key] = [((py * w + px) * 4, px - cx, py - cy) for py in range(0, h, 2) for px in range(0, w, 2)
                            if math.hypot(px - cx, py - cy) >= 0.4 * r]
    return _RING_CACHE[key]


def detect_cone_edges(pixels, w, h, r):
    """AION 2's minimap draws the cone with two white edge lines (live screenshots, 2026-10-14). The camera
    sits behind your character, so the lines don't start at your arrow; they pass beside the white disc
    around it. So: find the two longest straight white lines in the ring around the arrow (a Hough
    transform over near-white pixels), take each one's outward direction, and the cone is between them.
    -> (bearing, width, strength 0..1) or None."""
    points = []
    for k, x, y in _ring_pixels(w, h, r):  # every 2nd pixel outside the arrow's disc (the lines are 2+ px)
        b, g, red = pixels[k], pixels[k + 1], pixels[k + 2]
        if b > 185 and g > 185 and red > 185 and max(b, g, red) - min(b, g, red) < 60:
            points.append((x, y))
    if len(points) < r * 0.08:
        return None
    trig = _HOUGH_TRIG
    votes = {}
    for x, y in points:
        for ti, (ct, st) in enumerate(trig):
            key = (ti, int(round((x * ct + y * st) / 2)))  # rho in 2 px bins
            votes[key] = votes.get(key, 0) + 1
    # candidates: the strongest Hough lines (each suppressed before the next), judged by length: the
    # longest unbroken white run along them. Cone edges run from the disc to the frame; icon outlines
    # are straight too, but short.
    candidates, chosen = [], []
    for (ti, rb), count in sorted(votes.items(), key=lambda kv: -kv[1]):  # ranked once
        if len(chosen) >= 10 or count < 3:
            break
        if any(abs((ti - cti + 45) % 90 - 45) <= 5 and abs(rb - crb) <= 4 for cti, crb in chosen):
            continue  # part of a line already taken
        chosen.append((ti, rb))
        ct, st = trig[ti]
        dx, dy = -st, ct  # along the line
        along = sorted(x * dx + y * dy for x, y in points if abs(x * ct + y * st - rb * 2) <= 2)
        best = run = 0.0
        for prev, cur in zip(along, along[1:]):
            run = run + (cur - prev) if cur - prev <= 8 else 0.0  # icons drawn over a line leave gaps
            best = max(best, run)
        if best < 0.35 * r:
            continue
        on = [(x, y) for x, y in points if abs(x * ct + y * st - rb * 2) <= 2]
        mx, my = sum(x for x, y in on) / len(on), sum(y for x, y in on) / len(on)
        if dx * mx + dy * my < 0:  # point it away from the arrow
            dx, dy = -dx, -dy
        candidates.append((best, math.degrees(math.atan2(dx, -dy)) % 360))
    candidates.sort(reverse=True)
    pair = None
    for i in range(len(candidates)):
        for j in range(i + 1, len(candidates)):
            gap = abs((candidates[i][1] - candidates[j][1] + 180) % 360 - 180)
            if 80 <= gap <= 150:  # the game's cone is ~110-120 degrees wide (fixed camera field of view)
                pair = (candidates[i], candidates[j])
                break
        if pair:
            break
    if not pair:
        if candidates and candidates[0][0] >= 0.45 * r:
            # only one edge visible (the other hidden under icons): the cone lies on its lighter side,
            # at the usual width
            edge = candidates[0][1]
            side = 1 if arc_light(pixels, w, h, r, edge + 10, edge + 100) >= arc_light(pixels, w, h, r, edge - 100, edge - 10) else -1
            return (edge + side * 57) % 360, 114, 0.5
        return None
    directions = [pair[0][1], pair[1][1]]
    lines = [(0, 0, min(pair[0][0], pair[1][0]))]  # strength from the shorter edge
    a1, a2 = directions
    span = (a2 - a1) % 360
    if span > 180:
        a1, span = a2, 360 - span
    if not 80 <= span <= 150:
        return None
    strength = min(1.0, lines[0][2] / (0.6 * r))
    return (a1 + span / 2) % 360, span, strength


def arc_light(pixels, w, h, r, start, end):
    """Mean brightness of the ring 0.5-0.9 r between two bearings (degrees, clockwise)."""
    cx, cy = w / 2, h / 2
    total = count = 0
    for deg in range(int(start), int(end), 3):
        a = math.radians(deg)
        for f in (0.5, 0.7, 0.9):
            px, py = int(cx + math.sin(a) * r * f), int(cy - math.cos(a) * r * f)
            if 0 <= px < w and 0 <= py < h:
                k = (py * w + px) * 4
                total += 0.114 * pixels[k] + 0.587 * pixels[k + 1] + 0.299 * pixels[k + 2]
                count += 1
    return total / count if count else 0


def detect_cone(pixels, w, h, r):
    edges = detect_cone_edges(pixels, w, h, r)
    if edges and edges[2] >= 0.5:  # two clear white edge lines: confidence 1..2
        return edges[0], edges[1], 1 + edges[2]
    return detect_cone_light(pixels, w, h, r)


def detect_cone_light(pixels, w, h, r):
    """The minimap's vision cone: a lighter wedge from your arrow at the image centre.
    -> (bearing degrees, 0 = up, clockwise; width degrees; confidence) or None.
    Brightness is sampled on rings around the arrow (skipping the arrow itself); the brightest
    ~60-degree arc is the cone, and its contrast against the rest of the ring is the confidence."""
    cx, cy, step = w / 2, h / 2, 4
    n = 360 // step
    profile = []
    for i in range(n):
        a = math.radians(i * step)
        total = count = 0
        for f in (0.4, 0.55, 0.7, 0.85):
            px, py = int(cx + math.sin(a) * r * f), int(cy - math.cos(a) * r * f)
            if 0 <= px < w and 0 <= py < h:
                k = (py * w + px) * 4
                total += 0.114 * pixels[k] + 0.587 * pixels[k + 1] + 0.299 * pixels[k + 2]
                count += 1
        profile.append(total / count if count else 0)
    window = 60 // step
    smooth = [sum(profile[(i + j - window // 2) % n] for j in range(window)) / window for i in range(n)]
    peak = max(range(n), key=smooth.__getitem__)
    ordered = sorted(profile)
    base = ordered[n // 2]
    spread = (ordered[int(n * 0.9)] - ordered[int(n * 0.1)]) or 1
    contrast = smooth[peak] - base
    # cone edges: where the lightly smoothed (12 degree) brightness falls below halfway; noisy map art
    # would otherwise cut the arc short
    soft = [(profile[(i - 1) % n] + profile[i] + profile[(i + 1) % n]) / 3 for i in range(n)]
    half = base + contrast * 0.5
    left = right = 0
    while left < n // 2 and soft[(peak - left - 1) % n] > half:
        left += 1
    while right < n // 2 and soft[(peak + right + 1) % n] > half:
        right += 1
    # centre: brightness-weighted middle of that arc
    weight = total = 0.0
    for k in range(-left, right + 1):
        excess = max(0.0, soft[(peak + k) % n] - base)
        weight += excess
        total += excess * k
    centre = (peak + (total / weight if weight else 0)) * step % 360
    width = max(30, min(140, (left + right + 1) * step))
    return centre, width, contrast / spread


VIEW_DEFAULT = dict(enabled=False, x=None, y=None, r=60, threshold=0.6)


class ViewCone:
    """Reads the vision cone from the minimap on its own thread, about 15 times a second (Windows), but
    only while something shows it. Each new reading bumps .serial so the window can redraw just the cone.
    The result also goes into every snapshot as snap['cone'] = (bearing, width) or None."""
    RATE = 15  # readings per second

    def __init__(self):
        self.cfg = dict(VIEW_DEFAULT, **load_settings().get("view", {}))
        self.cone = None  # (bearing, width)
        self.raw = None  # last detection incl. confidence
        self.pixels = None
        self.serial = 0
        self.needed = False
        self.running = True
        threading.Thread(target=self.run, daemon=True).start()

    def save(self):
        save_settings(dict(load_settings(), view=self.cfg))

    def ready(self):
        return WINDOWS and self.cfg["enabled"] and self.cfg["x"] is not None

    def update(self, needed=True):
        """needed: something will draw the cone. If nothing does, nothing is captured or analysed and the
        reading is dropped, so a stale cone never shows when it's needed again."""
        self.needed = needed
        if not (needed and self.ready()) and (self.cone or self.raw or self.pixels):
            self.cone = self.raw = self.pixels = None
            self.serial += 1

    def run(self):
        while self.running:
            if not (self.needed and self.ready()):
                time.sleep(0.1)  # idle: no capture, no analysis
                continue
            start = time.perf_counter()
            self.read_once()
            time.sleep(max(0.005, 1 / self.RATE - (time.perf_counter() - start)))

    def read_once(self):
        r = int(self.cfg["r"])
        size = 2 * r + 2
        try:
            pixels = (size, size, screen_grab(int(self.cfg["x"]) - r - 1, int(self.cfg["y"]) - r - 1, size, size))
        except OSError:
            self.pixels, self.cone = None, None
            self.serial += 1
            return
        raw = detect_cone(pixels[2], size, size, r)
        self.pixels, self.raw = pixels, raw
        bearing_deg, width, confidence = raw
        if confidence < self.cfg["threshold"]:
            cone = None
        elif self.cone:  # smooth: move most of the way, the short way round
            delta = (bearing_deg - self.cone[0] + 180) % 360 - 180
            cone = ((self.cone[0] + delta * 0.7) % 360, self.cone[1] * 0.5 + width * 0.5)
        else:
            cone = (bearing_deg, width)
        if cone != self.cone:
            self.cone = cone
            self.serial += 1


def draw_cone(canvas, cx, cy, length, cone, fill=None, outline=None, below=False):
    """The vision cone as a wedge from (cx, cy): filled (the app's radars) or outlined (overlays, so the
    game's minimap stays visible underneath)."""
    if not cone:
        return
    bearing_deg, width = cone
    # Tk arcs: start angle counter-clockwise from 3 o'clock; our bearing is clockwise from 12
    start = 90 - bearing_deg - width / 2
    box = (cx - length, cy - length, cx + length, cy + length)
    if fill:
        canvas.create_arc(*box, start=start, extent=width, style="pieslice", fill=fill, outline="", tags="cone")
    if outline:
        canvas.create_arc(*box, start=start, extent=width, style="pieslice", outline="#000000", width=4, tags="cone")
        canvas.create_arc(*box, start=start, extent=width, style="pieslice", outline=outline, width=2, tags="cone")
    if below:
        canvas.tag_lower("cone")  # under the rings and pins


class Calibrator:
    """A draggable circle to put on the minimap: centre on your arrow, size to the cone's length.
    Drag: move · mouse wheel: size · right-click: done."""

    def __init__(self, feature):
        tk, view = feature.tk, feature.view
        self.feature, self.view = feature, view
        if view.cfg["x"] is None:
            root = feature.root
            view.cfg.update(x=root.winfo_rootx() + root.winfo_width() // 2, y=root.winfo_rooty() + 120)
        self.win = win = tk.Toplevel(feature.root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg=OVERLAY_KEY)
        if WINDOWS:
            win.attributes("-transparentcolor", OVERLAY_KEY)
        self.canvas = c = tk.Canvas(win, bg=OVERLAY_KEY, highlightthickness=0)
        c.pack(fill="both", expand=True)
        c.bind("<ButtonPress-1>", lambda e: setattr(self, "drag", (e.x_root, e.y_root, view.cfg["x"], view.cfg["y"])))
        c.bind("<B1-Motion>", self.motion)
        c.bind("<ButtonRelease-1>", lambda e: view.save())
        c.bind("<MouseWheel>", lambda e: self.resize(e.delta > 0))
        c.bind("<Button-4>", lambda e: self.resize(True))
        c.bind("<Button-5>", lambda e: self.resize(False))
        c.bind("<Button-3>", lambda e: feature.toggle_calibrate())
        self.place()
        win.update_idletasks()
        exclude_from_capture(win)

    def place(self):
        cfg, pad = self.view.cfg, 40
        size = 2 * int(cfg["r"]) + 2 * pad
        self.win.geometry("%dx%d+%d+%d" % (size, size, int(cfg["x"]) - size // 2, int(cfg["y"]) - size // 2))
        c, mid, r = self.canvas, size / 2, cfg["r"]
        c.delete("all")
        c.create_oval(mid - r, mid - r, mid + r, mid + r, outline="#000000", width=4)
        c.create_oval(mid - r, mid - r, mid + r, mid + r, outline="#ffd24a", width=2)
        c.create_line(mid - 10, mid, mid + 10, mid, fill="#ffd24a", width=2)
        c.create_line(mid, mid - 10, mid, mid + 10, fill="#ffd24a", width=2)
        draw_text(c, mid, 8, "centre on your arrow · wheel: cone length · right-click: done", "#ffffff",
                  outline="#000000", size=8, bold=False, anchor="n")

    def motion(self, e):
        x0, y0, sx, sy = self.drag
        self.view.cfg["x"], self.view.cfg["y"] = sx + e.x_root - x0, sy + e.y_root - y0
        self.place()

    def resize(self, up):
        self.view.cfg["r"] = max(20, min(300, self.view.cfg["r"] + (4 if up else -4)))
        self.view.save()
        self.place()

    def close(self):
        self.view.save()
        self.win.destroy()


class ViewFeature(Feature):
    """Reads your camera direction from the game's minimap (its vision cone) and shares it with the other
    features' radars and overlays. Windows only."""
    title, icon, TABS = "View cone", "◔", ("Setup",)
    off_text = "The screen isn't read while it's off."

    def load_enabled(self):
        return self.app.view.cfg["enabled"]

    def store_enabled(self, on):
        self.view.cfg["enabled"] = on
        if not on:
            self.view.cone = None
        self.view.save()

    def enabled_changed(self):
        super().enabled_changed()
        if self.enabled and WINDOWS and self.view.cfg["x"] is None and not self.calibrator:
            self.toggle_calibrate()  # first time on: show where the minimap is

    def __init__(self, app, parent):
        self.view = app.view
        self.calibrator = None
        self.preview_image = None
        super().__init__(app, parent)

    def build_setup(self, f):
        tk = self.tk
        heading(tk, f, "Vision cone", "Mirrors the camera cone from the game's minimap onto the Cubes and "
                "Monsters radars and overlays. It's read from the screen (the game traffic doesn't contain "
                "your view direction), so the minimap must be visible: borderless or windowed mode.")
        if not WINDOWS:
            tk.Label(f, text="Windows only for now.", bg=BG, fg="#ff6ad5", font=(FONT, 10)).pack(fill="x")
            return
        tk.Label(f, text="Turn it on with the switch at the top right.", bg=BG, fg=MUTED, font=(FONT, 9),
                 anchor="w").pack(fill="x")
        row = tk.Frame(f, bg=BG)
        row.pack(fill="x", pady=(8, 0))
        self.calibrate_button = button(tk, row, "Locate minimap", self.toggle_calibrate, width=16)
        self.calibrate_button.pack(side="left")
        tk.Label(row, text="circle on your arrow, wheel to the cone's length", bg=BG, fg=MUTED,
                 font=(FONT, 9)).pack(side="left", padx=8)
        section(tk, f, "What it sees")
        row = tk.Frame(f, bg=BG)
        row.pack(fill="x")
        self.preview = tk.Canvas(row, width=200, height=200, bg=PANEL, highlightthickness=0)
        self.preview.pack(side="left")
        self.readout = tk.Label(row, text="", bg=BG, fg=TEXT, font=(MONO, 9), justify="left", anchor="nw")
        self.readout.pack(side="left", fill="both", padx=10)
        section(tk, f, "Sensitivity")
        row = tk.Frame(f, bg=BG)
        row.pack(fill="x")
        self.threshold = tk.DoubleVar(value=self.view.cfg["threshold"])
        tk.Scale(row, from_=0.2, to=2.0, resolution=0.05, orient="horizontal", variable=self.threshold,
                 command=lambda v: self.set_threshold(), length=250, bg=BG, fg=TEXT, troughcolor=BUTTON,
                 highlightthickness=0, activebackground=ACCENT, font=(FONT, 9), showvalue=True).pack(side="left")
        tk.Label(f, text="Lower = shows the cone more often; higher = fewer wrong cones on busy map art.",
                 bg=BG, fg=MUTED, font=(FONT, 9), anchor="w", wraplength=390, justify="left").pack(fill="x")

    def set_threshold(self):
        self.view.cfg["threshold"] = round(self.threshold.get(), 2)
        self.view.save()

    def toggle_calibrate(self):
        if self.calibrator:
            self.calibrator.close()
            self.calibrator = None
        else:
            self.calibrator = Calibrator(self)
            self.set_enabled(True)
        self.calibrate_button.config(text="Done" if self.calibrator else "Locate minimap")

    def update(self, snap, tab):
        if not WINDOWS:
            return
        c = self.preview
        c.delete("all")
        view = self.view
        if view.pixels:
            w, h, raw = view.pixels
            rgb = bytearray(w * h * 3)
            rgb[0::3], rgb[1::3], rgb[2::3] = raw[2::4], raw[1::4], raw[0::4]
            ppm = b"P6\n%d %d\n255\n" % (w, h) + bytes(rgb)
            zoom = max(1, 200 // w)
            image = self.tk.PhotoImage(data=ppm)  # Tk takes binary PPM as is (not base64)
            if zoom > 1:
                image = image.zoom(zoom)
            self.preview_image = image  # keep a reference
            c.create_image(100, 100, image=image)
            draw_cone(c, 100, 100, view.cfg["r"] * min(zoom, 200 / w) * 0.95, view.cone, outline="#4fd8ff")
        else:
            c.create_text(100, 100, text="not reading\n(enable + locate minimap)", fill=MUTED, justify="center",
                          font=(FONT, 9))
        if view.raw and view.ready():
            b, w_deg, conf = view.raw
            state = "cone found" if view.cone else "no clear cone"
            self.readout.config(text="%s\n\ndirection %3d°\nwidth     %3d°\nclarity   %.2f\n(needs %.2f)\n\n"
                                "minimap at %d, %d\nradius %d px" % (state, b, w_deg, conf, view.cfg["threshold"],
                                                                     view.cfg["x"], view.cfg["y"], view.cfg["r"]),
                                fg=ACCENT if view.cone else MUTED)
        else:
            self.readout.config(text="off", fg=MUTED)

    def status(self, snap):
        return ("view %d°" % snap["cone"][0]) if snap.get("cone") else None

    def close(self):
        if self.calibrator:
            self.calibrator.close()


class App:
    """Main window: a sidebar of features. Every feature's background() runs each tick whatever is shown;
    only the shown feature/tab is redrawn."""
    FEATURES = (CubesFeature, MonstersFeature, GatherFeature, AlertsFeature, ViewFeature)

    def __init__(self, tracker, engine, args):
        import tkinter as tk
        self.tk = tk
        self.tracker, self.engine, self.args = tracker, engine, args
        self.view = ViewCone()
        self.root = root = tk.Tk()
        root.title("Cube Finder")
        self.icon = set_icon(root)
        root.configure(bg=BG)
        settings = load_settings()
        self.topmost = tk.BooleanVar(value=settings.get("topmost", True) and not args.no_topmost)
        root.attributes("-topmost", self.topmost.get())
        self.hotkey = threading.Event()
        if WINDOWS:
            threading.Thread(target=hotkey_thread, args=(self.hotkey,), daemon=True).start()

        side = tk.Frame(root, bg=SIDEBAR, width=150)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        brand = tk.Frame(side, bg=SIDEBAR)
        brand.pack(fill="x", padx=12, pady=(14, 18))
        try:
            self.logo = tk.PhotoImage(file=str(ASSETS / "icon-32.png"))
            tk.Label(brand, image=self.logo, bg=SIDEBAR).pack(side="left")
        except tk.TclError:
            pass
        tk.Label(brand, text="Cube\nFinder", bg=SIDEBAR, fg=TEXT, font=(FONT, 11, "bold"), justify="left").pack(
            side="left", padx=8)
        tk.Checkbutton(side, text="Keep on top", variable=self.topmost, command=self.set_topmost, bg=SIDEBAR,
                       fg=MUTED, selectcolor=BUTTON, activebackground=SIDEBAR, activeforeground=TEXT,
                       font=(FONT, 9), anchor="w", padx=10).pack(side="bottom", fill="x", pady=(0, 10))
        self.badges = tk.Label(side, text="", bg=SIDEBAR, fg=ACCENT, font=(FONT, 9), anchor="w", padx=14,
                               justify="left")
        self.badges.pack(side="bottom", fill="x", pady=(0, 6))
        self.badge_text = {}
        main = tk.Frame(root, bg=BG)
        main.pack(side="left", fill="both", expand=True)
        content = tk.Frame(main, bg=BG)
        content.pack(fill="both", expand=True, padx=14, pady=(12, 6))
        self.status = tk.Label(main, text="", bg="#0f1115", fg=MUTED, font=(FONT, 9), anchor="w", padx=10, pady=4)
        self.status.pack(fill="x", side="bottom")

        self.features, self.nav, self.current = {}, {}, None
        for cls in self.FEATURES:
            item = tk.Label(side, text="", bg=SIDEBAR, fg=MUTED, anchor="w", font=(FONT, 11), padx=10, pady=8,
                            cursor="hand2")
            item.pack(fill="x")
            item.bind("<Button-1>", lambda e, t=cls.title: self.show(t))
            item.bind("<Enter>", lambda e, w=item: w.config(bg=BUTTON) if w is not self.nav.get(self.current) else None)
            item.bind("<Leave>", lambda e, w=item: w.config(bg=SIDEBAR) if w is not self.nav.get(self.current) else None)
            self.nav[cls.title] = item
            self.features[cls.title] = cls(self, content)
            self.feature_state_changed(self.features[cls.title])
        # one fixed size for every feature and tab (the largest), so switching doesn't resize the window
        root.update_idletasks()
        banner = max(f.banner.winfo_reqheight() + 8 for f in self.features.values())  # shown while a feature is off
        sizes = [(frame.winfo_reqwidth(), frame.winfo_reqheight() + 45 + banner)  # + tab bar + off banner
                 for feature in self.features.values() for frame in feature.tabs.values()]
        content.config(width=max(w for w, h in sizes), height=max(h for w, h in sizes))
        content.pack_propagate(False)
        saved = settings.get("page")
        self.show(saved if saved in self.features else self.FEATURES[0].title)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.cone_serial = -1
        self.tick()
        self.cone_tick()

    def show(self, title):
        if self.current:
            self.features[self.current].frame.pack_forget()
            self.nav[self.current].config(bg=SIDEBAR)
        self.current = title
        feature = self.features[title]
        feature.frame.pack(fill="both", expand=True)
        self.nav[title].config(bg=BUTTON)
        for f in self.features.values():
            self.feature_state_changed(f)
        save_settings(dict(load_settings(), page=title))
        feature.update(self.tracker.snapshot(), feature.tab)

    def feature_state_changed(self, feature):
        """Sidebar entry: '(off)' and dimmed when the feature is switched off."""
        item = getattr(self, "nav", {}).get(feature.title)
        if item is not None:
            item.config(text="  %s   %s%s" % (feature.icon, feature.title, "" if feature.enabled else "  · off"),
                        fg=(TEXT if feature.title == self.current else MUTED) if feature.enabled else "#5a606a")

    def set_badge(self, feature, text):
        """Short always-visible state in the sidebar (e.g. a feature's overlay is on)."""
        if text:
            self.badge_text[feature.title] = "%s %s:\n    %s" % (feature.icon, feature.title, text)
        else:
            self.badge_text.pop(feature.title, None)
        if hasattr(self, "badges"):
            self.badges.config(text="\n".join(self.badge_text.values()))

    def set_topmost(self):
        self.root.attributes("-topmost", self.topmost.get())
        save_settings(dict(load_settings(), topmost=self.topmost.get()))

    def cone_tick(self):
        """Every 50 ms: if the cone reading changed, redraw only the cone (radar tab and overlays), so it
        follows the camera without waiting for the full 250 ms refresh."""
        if self.view.serial != self.cone_serial:
            self.cone_serial = self.view.serial
            cone = self.view.cone if self.view.ready() else None
            current = self.features[self.current]
            for feature in self.features.values():
                if not feature.enabled:
                    continue
                if feature is current and feature.tab == "Radar" and hasattr(feature, "canvas"):
                    c, mid = feature.canvas, feature.size / 2
                    c.delete("cone")
                    draw_cone(c, mid, mid, mid - 18, cone, fill="#2b3346", below=True)
                if feature.overlay:
                    o = feature.overlay
                    o.canvas.delete("cone")
                    if self.tracker.position is not None and o.cfg["cone"]:
                        draw_cone(o.canvas, o.cfg["w"] / 2, o.cfg["h"] / 2, min(o.cfg["w"], o.cfg["h"]) / 2 - 6, cone,
                                  outline=OVERLAY_CONE, below=True)
        self.root.after(50, self.cone_tick)

    def cone_needed(self):
        """Is anything showing the vision cone right now? Only then is the screen read for it."""
        if any(f.overlay and f.overlay.cfg["cone"] for f in self.features.values() if f.enabled):
            return True  # overlays show it whatever the main window does
        if self.root.state() == "iconic":
            return False  # minimised: nothing in the window is visible
        current = self.features[self.current]
        return current.title == "View cone" or (current.tab == "Radar" and current.enabled)

    def on_hotkey(self):
        """Ctrl+Shift+O: lock every shown overlay if any is unlocked, else unlock them all; with none shown,
        show the current feature's overlay."""
        shown = [f for f in self.features.values() if f.overlay and f.enabled]
        if not shown:
            current = self.features[self.current]
            if current.overlay_key and current.enabled:
                current.toggle_overlay()
            return
        lock = any(not f.overlay.cfg["locked"] for f in shown)
        for f in shown:
            f.overlay.set_locked(lock)

    def tick(self):
        snap = self.tracker.snapshot()
        self.view.update(self.cone_needed())
        snap["cone"] = self.view.cone if self.view.ready() else None
        if self.hotkey.is_set():
            self.hotkey.clear()
            self.on_hotkey()
        for feature in self.features.values():
            if feature.enabled:
                feature.background(snap)
        current = self.features[self.current]
        current.update(snap, current.tab)
        parts = [self.engine.status] + [s for s in (f.status(snap) for f in self.features.values() if f.enabled) if s]
        self.status.config(text=" · ".join(parts + ["%d msgs" % snap["messages"], "%d pkts" % self.engine.packets]))
        self.root.after(250, self.tick)

    def close(self):
        self.view.running = False
        for feature in self.features.values():
            feature.close()
        self.engine.stop = True
        if self.engine.capture:
            self.engine.capture.shutdown()
        self.root.destroy()


Radar = App  # older name


# ---------------------------------------------------------------- main

def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except OSError:
        return False


def main():
    if sys.argv[1:] == ["--capture-helper"]:  # Linux root side, started by HelperCapture
        return capture_helper()
    parser = argparse.ArgumentParser(description="Standalone Hidden Cube radar for AION2 (passive capture: "
                                                 "WinDivert on Windows, a raw socket on Linux).")
    parser.add_argument("--windivert", help="Windows: folder with WinDivert.dll and WinDivert64.sys")
    parser.add_argument("--port", type=int, help="extra game server port to capture")
    parser.add_argument("--range", type=float, default=150, help="radar radius in metres (default 150)")
    parser.add_argument("--voice", help="cube callout: %s (default: last picked, else calm)" % ", ".join(voices()))
    parser.add_argument("--sound", help="your own WAV file to play when a cube appears")
    parser.add_argument("--volume", type=int, help="alert volume 0-200 %% (default: last set, else 100)")
    parser.add_argument("--no-sound", action="store_true", help="no alert sound")
    parser.add_argument("--no-topmost", action="store_true", help="don't keep the window on top")
    parser.add_argument("--console", action="store_true", help="no window; print cubes to the console")
    parser.add_argument("--record", help="save every decoded game message to this file (JSON lines), "
                        "to work out new message layouts")
    parser.add_argument("--log", help="also append messages and a 10 s status line to this file, "
                        "including the type id of any object you open within 10 m")
    parser.add_argument("--template", type=int, action="append", default=[],
                        help="extra cube type id to track (repeatable)")
    parser.add_argument("--shortcut", action="store_true",
                        help="Windows: create a 'Cube Finder' shortcut (icon, no console) here and on the Desktop")
    parser.add_argument("--demo", action="store_true",
                        help="no capture or admin rights: simulated player and cubes, to try the window")
    args = parser.parse_args()
    TEMPLATES.update(args.template)
    settings = load_settings()
    if args.no_sound:
        args.voice = "off"
    elif args.sound:
        args.voice = "custom"
    elif args.voice is None:
        args.voice = settings.get("voice", "calm")
    if args.sound is None and settings.get("custom") and os.path.exists(settings["custom"]):
        args.sound = settings["custom"]
    if args.voice == "custom" and not args.sound:
        args.voice = "calm"
    if args.volume is None:
        args.volume = settings.get("volume", 100)
    args.volume = max(0, min(200, args.volume))
    if args.voice not in voices() + ["custom"]:
        parser.error("--voice must be one of: " + ", ".join(voices()))
    if not (WINDOWS or LINUX):
        sys.exit("Windows or Linux only.")
    if args.shortcut:
        return make_shortcut() if WINDOWS else sys.exit("--shortcut is Windows-only.")
    if WINDOWS:  # real pixels everywhere: screen capture and window positions must agree
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except (OSError, AttributeError):
            ctypes.windll.user32.SetProcessDPIAware()
    if WINDOWS:  # our own taskbar entry and icon, not Python's
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("CubeFinder.Radar")
    if WINDOWS and not is_admin() and not args.demo:  # WinDivert needs elevation: relaunch through UAC (it starts in System32)
        argv = [str(Path(__file__).resolve())]
        for prev, a in zip([None] + sys.argv[1:], sys.argv[1:]):
            argv.append(os.path.abspath(a) if os.path.exists(a) or prev in ("--log", "--record") else a)
        params = subprocess.list2cmdline(argv)
        # The elevated copy runs without a console window unless one was asked for.
        exe = Path(sys.executable)
        if not args.console and exe.with_name("pythonw.exe").exists():
            exe = exe.with_name("pythonw.exe")
        if ctypes.windll.shell32.ShellExecuteW(None, "runas", str(exe), params, None, 1) <= 32:
            show_error("Administrator rights are required (WinDivert needs them).")
        return
    try:
        folder = find_windivert(args.windivert) if WINDOWS and not args.demo else None
        tracker = Tracker()
        log = print
        if args.log:
            def log(text):
                print(text)
                with open(args.log, "a", encoding="utf-8") as f:
                    f.write(text + "\n")
        engine = Engine(tracker, folder, args.port, log)
        if args.log:
            tracker.log = engine.say
        if args.record:
            tracker.recorder = Recorder(args.record)
            engine.say("Recording game messages to " + args.record)
            threading.Thread(target=engine.heartbeat, daemon=True).start()
        if folder:
            engine.say("WinDivert from " + str(folder))
        run = (lambda: demo(tracker, engine)) if args.demo else engine.run
        if not args.demo:
            engine.say("Start this before logging in or changing zone; cubes appear as the server sends them.")
        if args.console:
            threading.Thread(target=console_loop, args=(tracker, engine, args), daemon=True).start()
            run()
        else:
            threading.Thread(target=run, daemon=True).start()
            Radar(tracker, engine, args).root.mainloop()
    except Exception as e:
        show_error(e)


def demo(tracker, engine):
    """--demo: no capture. Feeds made-up game messages through the real decoder: you walk a slow
    circle while cubes appear around you at various heights, then get opened or drift out of range,
    and monsters spawn, wander and die."""
    import random

    def varint_bytes(n):
        out = bytearray()
        while True:
            b, n = n & 127, n >> 7
            out.append(b | (128 if n else 0))
            if not n:
                return bytes(out)

    def point(x, y, z):
        return struct.pack("<fff", x, y, z)

    own, centre, step, next_id, live = 6190, (10000.0, 20000.0, 500.0), 0, 50000, {}
    mobs = {}
    # Krall Soldier (two variants), Krall Healer, Krall Karnif, Drana Test Subject, Failed Drana Mutant
    MOB_TYPES = (2700977, 2700972, 2700978, 2700980, 2110450, 2110451)
    engine.status = "Demo (simulated, no capture)"
    engine.say("Demo mode: simulated player, cubes and monsters; nothing is captured.")
    teleport = b"#6" + bytes(6) + point(*centre) + bytes(6)
    tracker.feed([b"36" + varint_bytes(own) + bytes(5), teleport], time.time())
    while not engine.stop:
        step += 1
        angle = step * 0.01  # one lap of a 40 m circle every ~2.5 minutes
        me = (centre[0] + 4000 * math.sin(angle), centre[1] - 4000 * math.cos(angle), centre[2])
        batch = [b"*8" + varint_bytes(own) + bytes(28) + point(*me)]
        if step % 16 == 1 and len(live) < 7:  # a cube every ~4 s
            dist, a = random.uniform(15, 190), random.uniform(0, 2 * math.pi)
            dz = random.choice((random.uniform(-25, -4), random.uniform(-2, 2), random.uniform(4, 30)))
            xyz = (me[0] + dist * 100 * math.sin(a), me[1] - dist * 100 * math.cos(a), me[2] + dz * 100)
            batch.append(b"46" + varint_bytes(next_id) + b"\x04\x00" + struct.pack("<I", 1200039) + point(*xyz))
            live[next_id] = time.time()
            next_id += 1
        for entity, born in list(live.items()):
            if time.time() - born > 20 and random.random() < 0.02:
                gone = random.random() < 0.5
                batch.append((b"\x05\x8d" if gone else b"56") + varint_bytes(entity) + (b"" if gone else b"\x04\x03\x00"))
                del live[entity]
        # monsters (real types from data/monsters.json): spawn ('41 36'), wander ('*8'), sometimes die ('04 8d')
        if step % 6 == 1 and len(mobs) < 14:
            type_id = random.choice(MOB_TYPES)
            dist, a = random.uniform(10, 120), random.uniform(0, 2 * math.pi)
            xyz = [me[0] + dist * 100 * math.sin(a), me[1] - dist * 100 * math.cos(a), me[2]]
            batch.append(b"A6" + varint_bytes(next_id) + bytes(3) + struct.pack("<I", type_id) + b"\x40\x02"
                         + point(*xyz) + bytes(40))
            mobs[next_id] = xyz
            next_id += 1
        for entity, xyz in list(mobs.items()):
            r = random.random()
            if r < 0.004:
                batch.append(b"\x04\x8d" + varint_bytes(entity) + bytes(4))
                del mobs[entity]
            elif r < 0.08:
                xyz[0] += random.uniform(-600, 600)
                xyz[1] += random.uniform(-600, 600)
                batch.append(b"*8" + varint_bytes(entity) + bytes(31) + point(*xyz))
        engine.packets += 1
        tracker.feed(batch, time.time())
        time.sleep(0.25)


def console_loop(tracker, engine, args):
    seen = 0
    while not engine.stop:
        snap = tracker.snapshot()
        if snap["detections"] > seen:
            seen = snap["detections"]
            for cube in snap["cubes"]:
                if cube["id"] == snap["last_detection"] and snap["position"]:
                    dist, dz, compass = relative(snap["position"], cube["xyz"])
                    engine.say("Cube! %d m %s (height %+d m)" % (dist, compass, dz))
                    break
            else:
                engine.say("Cube! (your position not known yet)")
            play(args.voice, args.sound, args.volume)
        time.sleep(0.25)


if __name__ == "__main__":
    main()
