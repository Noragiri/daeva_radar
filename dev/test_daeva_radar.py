"""Offline decoder/tracker tests: python dev/test_daeva_radar.py (no game, capture or admin)."""
import sys, struct, random
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from types import SimpleNamespace
from daevaradar import protocol, game, ui
cf = SimpleNamespace(**{k: v for m in (protocol, game) for k, v in vars(m).items() if not k.startswith("__")},
                     relative=ui.relative)  # the names these tests use

def venc(n):
    out = bytearray()
    while True:
        b = n & 127; n >>= 7
        out.append(b | (128 if n else 0))
        if not n: return bytes(out)

def frame(body): return venc(len(body) + 4) + body
def lit(raw):
    L = len(raw); tok = (min(L, 15) << 4); ext = b''
    if L >= 15:
        r = L - 15
        while r >= 255: ext += b'\xff'; r -= 255
        ext += bytes([r])
    return bytes([tok]) + ext + raw
def compressed(frames):
    raw = b''.join(frames)
    return frame(b'\xff\xff' + struct.pack('<I', len(raw)) + lit(raw))

# LZ4 match + overlap
blk = bytes([0x44]) + b'abcd' + b'\x04\x00' + bytes([0x10]) + b'x'
assert cf.lz4_block(blk, 13) == b'abcdabcdabcdx', cf.lz4_block(blk, 13)
blk = bytes([0x15]) + b'a' + b'\x01\x00' + bytes([0x00])  # 'a' then 9 more 'a' (overlap offset 1)
assert cf.lz4_block(blk, 10) == b'a' * 10

OWN, CUBE = 4268, 9001
me_id = b'36' + venc(OWN) + b'\x00' * 5
pos = b'*8' + venc(OWN) + b'\x00' * 28 + struct.pack('<fff', 100.0, 200.0, 50.0)  # position near map centre
cube_xyz = (100.0 + 3000.0, 200.0 - 4000.0, 1250.0)  # 30 m east, 40 m north, 12 m up
cube = b'46' + venc(CUBE) + b'\x04\x00' + struct.pack('<I', 1301199) + struct.pack('<fff', *cube_xyz)
other = b'46' + venc(77) + b'\x04\x00' + struct.pack('<I', 1234) + struct.pack('<fff', 1.0, 1.0, 1.0)
opened = b'56' + venc(CUBE) + b'\x04\x03\x00'

stream = compressed([frame(me_id), frame(pos)]) + frame(b'zz' + b'q' * 30) + compressed([frame(cube), frame(other)])
stream += b''.join(compressed([frame(b'zz' + bytes([i]) * 40)]) for i in range(30))

def ip_packet(seq, payload, flags=0x18):
    tcp = struct.pack('!HHIIBBHHH', 13328, 50000, seq, 0, 5 << 4, flags, 65535, 0, 0)
    total = 20 + len(tcp) + len(payload)
    ip = struct.pack('!BBHHHBBH4s4s', 0x45, 0, total, 0, 0x4000, 64, 6, 0, bytes([1,2,3,4]), bytes([10,0,0,2]))
    return ip + tcp + payload

def run(packets, label, step=0.01):
    t = cf.Tracker(); logs = []
    r = cf.StreamReader(t.feed, lambda reason: t.clear(), logs.append)
    for i, p in enumerate(packets): r.packet(p, 1000.0 + i * step)
    s = t.snapshot(now=1000.0 + len(packets) * step)
    print(label, 'own', s['own'], 'pos', s['position'], 'cubes', [(c['id'], c['status']) for c in s['cubes']], 'det', s['detections'], logs)
    return t, s

# 1) SYN start, segments shuffled within a small window
isn = 123456
segs = []; off = 0
while off < len(stream):
    n = random.randint(1, 60); segs.append(ip_packet((isn + 1 + off) % cf.SEQ, stream[off:off + n])); off += n
random.seed(1)
shuffled = segs[:]
for i in range(0, len(shuffled) - 3, 4):
    blk4 = shuffled[i:i+4]; random.shuffle(blk4); shuffled[i:i+4] = blk4
t, s = run([ip_packet(isn, b'', 0x12)] + shuffled + [segs[5]], 'syn+reorder+retransmit:')
assert s['own'] == OWN and s['position'] == [100.0, 200.0, 50.0]
assert [(c['id'], c['status']) for c in s['cubes']] == [(CUBE, 'nearby')] and s['detections'] == 1
d, dz, comp = cf.relative(s['position'], s['cubes'][0]['xyz'])
print('relative', round(d, 1), round(dz, 1), comp); assert round(d) == 50 and round(dz) == 12 and comp == 'NE'

# 2) wrap-around sequence numbers
isn = cf.SEQ - 50
segs = []; off = 0
while off < len(stream):
    segs.append(ip_packet((isn + 1 + off) % cf.SEQ, stream[off:off + 37])); off += 37
t, s = run([ip_packet(isn, b'', 0x12)] + segs, 'seq wrap:')
assert s['detections'] == 1

# 3) joined mid-stream (no SYN, starts mid-frame) -> lost -> recovers on compressed frame
prefix = b'\x07garbage-tail-of-previous-frame'
full = prefix + stream + stream
segs = []; off = 0
while off < len(full):
    segs.append(ip_packet((5000 + off) % cf.SEQ, full[off:off + 50])); off += 50
t, s = run(segs, 'mid-stream:', step=0.1)
assert s['detections'] >= 1 and s['own'] == OWN and s['position']
print('cubes after recovery', s['cubes'])

# 4) opened status
t = cf.Tracker(); t.feed([me_id, pos, cube], 1.0); t.feed([opened], 2.0)
assert t.snapshot(now=3.0)['cubes'][0]['status'] == 'opened'
# 5) identity change clears
t.feed([b'36' + venc(5)], 4.0); assert t.snapshot(now=5)['cubes'] == []
# 6) teleport message
tp = b'#6' + bytes(6) + struct.pack('<fff', 500.0, 600.0, 7.0) + bytes(6)
assert len(tp) == 26
t.feed([tp], 6.0); assert t.snapshot(now=6)['position'] == [500.0, 600.0, 7.0]

# 7) your summoned pet: the 'A6' spawn on you right after your skill, then its '29 37' moves, then dismissed
ME, PET, FAR = 77, 4242, 4343
t = cf.Tracker()
me_pos = b'*8' + venc(ME) + bytes(28) + struct.pack('<fff', 1000.0, 2000.0, 300.0)
skill = b'\x028' + venc(ME) + bytes(10)
def spawn(e, x, y, head=b'\x1f\x10\x00'):  # pets' spawns start with 1f 10 after the id, monsters' don't
    return b'A6' + venc(e) + head + struct.pack('<I', 2920114) + bytes(2) + struct.pack('<fff', x, y, 300.0) + bytes(4)
pet_move = b')7' + venc(PET) + b'\x02' + struct.pack('<fff', 1500.0, 2500.0, 300.0) + struct.pack('<fff', 600.0, 0.0, 0.0) + b'\x01\x02\x03'
t.feed([b'36' + venc(ME)], 1.0); t.feed([me_pos], 1.1)
t.feed([spawn(FAR, 9000.0, 9000.0)], 1.2); assert t.pet is None  # a monster far away is not your pet
t.feed([skill], 1.3); t.feed([spawn(4444, 1100.0, 2100.0, head=b'\x04\x20\x00')], 1.35); assert t.pet is None  # a monster beside you
t.feed([spawn(PET, 1200.0, 2150.0)], 1.4)  # your pet, 2.5 m beside you
s = t.snapshot(now=1.5)
assert s['pet'] and s['pet']['id'] == PET and all(m['id'] != PET for m in s['monsters'])
t.feed([pet_move], 2.0)
assert t.snapshot(now=2.0)['pet']['xyz'] == [1500.0, 2500.0, 300.0] and t.pet['velocity'][0] == 600.0
t.feed([b'B6' + venc(PET) + b'\x00\x07'], 3.0); assert t.pet is None
print('pet: recognised, moved, dismissed')

# 9) character ID from the login message (bit 7 of its first byte is a per-login flag)
for first in (0x5e, 0xde):
    login = b'36' + venc(5898) + bytes([first, 0x81, 0xc9, 0x28, 0x37, 8]) + b'Noragiri' + bytes(20)
    assert cf.login_name(login) == (5898, 'Noragiri', 684294494), cf.login_name(login)
t = cf.Tracker(); t.feed([login], 1.0); assert t.snapshot(now=1)['own_char_no'] == 684294494
print('character ID: 684294494 from both flag variants')

# 8) gathering groups from the item ids in the map data
md = cf.MapData()
assert md.group_of('Orichalcum Ore') == 'Ore' and md.group_of('Odyle') == 'Odyle' and md.group_of('Ruby Gemstone') == 'Gem'
assert md.group_of('Yggdrasil Log') == 'Log' and md.group_of('Aria') == 'Herb' and md.group_of('Coriolus') == 'Ingredient'
assert all(md.group_of(n) for n in md.names()), [n for n in md.names() if not md.group_of(n)]
print('groups:', {g: sum(1 for n in md.names() if md.group_of(n) == g) for g, _ in cf.GATHER_GROUPS})
# 9) hidden-cube spawn points from the map data
for key, spawns in md.cubes.items():
    at, at2 = md.to_world(*spawns[0], 500.0), md.to_world(*spawns[1], 500.0)
    assert md.cube_map([at]) is None and md.cube_map([at, at2]) == key and at in md.cubes_nearby(key, at, 10), key
# 10) feathers: a '46' object on an Empyrean Trace spot, then picked up -> a used-up event with its type
spot = md.traces['verteron'][0]
fx, fy, _ = md.to_world(*spot, 0.0)
here = b'*8' + venc(OWN) + b'\x00' * 28 + struct.pack('<fff', fx + 500.0, fy, 0.0)
feather = b'46' + venc(555) + b'\x04\x00' + struct.pack('<I', 424242) + struct.pack('<fff', fx + 30.0, fy, 80.0)
t = cf.Tracker(); t.feed([me_id, here, feather], 1.0)
n = t.snapshot(now=1.0)['nodes']; assert n and n[0]['type'] == 424242 and md.trace_at('verteron', n[0]['xyz'], 2) == spot
t.feed([b'56' + venc(555) + b'\x04\x03\x00'], 2.0)
s = t.snapshot(now=2.0); assert s['nodes'] == [] and s['used_count'] == 1 and s['used'][0]['type'] == 424242
t.feed([feather], 3.0); t.feed([b'56' + venc(555) + b'\x01\x01\x00'], 3.5)  # only left range: not used up
assert t.snapshot(now=4.0)['used_count'] == 1
print('feathers: spot matched, pick-up reported;', {k: len(v) for k, v in md.traces.items()})
# 11) a real feather pickup (recorded 2026-10-15, Verteron): '46' with flags 0c 00, then ':6' feather <- you,
# ':\xe3' (to you only), then ':6' feather <- another player (not yours)
ME = 10646
real = [bytes.fromhex(h) for h in (
    '3436a8d9010c00e1521200edea89c7d1288bc788ca1e460000000000000000010101f4715f39a101000000000000000000006c9f854d080015352707',
    '3a36a8d90196530000000000000000',
    '3ae3a86c0000edea89c7d1288bc788ca1e46016107aa2503000000',
    '3a36a8d901a4410000000000000000')]
assert cf.object_intro(real[0])[:2] == (27816, 1200865)
near = b'*8' + venc(ME) + b'\x00' * 28 + struct.pack('<fff', -71597.0, -71663.0, 9097.0)  # where you stood
t = cf.Tracker(); t.feed([b'36' + venc(ME) + b'\x00' * 5, near], 1.0); t.feed([real[0]], 2.0)
n = t.snapshot(now=2.0)['nodes']; assert n[0]['type'] == 1200865 and md.trace_at('verteron', n[0]['xyz'], 1) == (1695, 1692)
t.feed([real[1]], 3.0); t.feed([real[2]], 3.1); t.feed([real[3]], 5.0)
s = t.snapshot(now=5.0); assert s['used_count'] == 2 and {e['type'] for e in s['used']} == {1200865}, s['used']
print('real feather: 0c-flag spawn parsed, your pickup reported (twice), the other player\'s not')
print('cube spawns:', {k: len(v) for k, v in md.cubes.items()})
# 12) pet collection: the full list on entering the world ('00 90'), then live soul drops and a level up
# (recorded 2026-10-15: Mutated Cadaver Insectoid 1130 went 21 -> 23 -> 25 = Lv2; one drop carried two pets)
sync = (b'\x00\x90' + struct.pack('<I', 1005) + venc(2) + struct.pack('<III', 1130, 1130, 1) + struct.pack('<III', 1033, 1033, 2)
        + venc(2) + struct.pack('<II', 1130, 21) + struct.pack('<II', 1033, 0) + b'\x06\x01')
assert cf.pet_collection(sync) == ({1130: 1, 1033: 2}, {1130: 21, 1033: 0})
assert cf.pet_collection(b'\x00\x90' + bytes(4) + venc(1) + struct.pack('<III', 1130, 1131, 1) + venc(0)) is None  # ids differ
live = [bytes.fromhex(h) for h in (
    '0d90016a04000002000000',                                          # +2 souls, pet 1130
    '0d900209040000010000006a04000002000000',                          # two pets in one drop
    '1490' + '0000' + '6a040000' + '02000000' + '0306000000362900000000000000',  # pet 1130 -> Lv2
    '0e90016a04000019000000')]                                         # 25 souls spent
assert cf.pet_souls(live[0]) == ('gain', [(1130, 2)])
assert cf.pet_souls(live[1]) == ('gain', [(1033, 1), (1130, 2)])
assert cf.pet_souls(live[2]) == ('level', [(1130, 2)])
assert cf.pet_souls(live[3]) == ('spent', [(1130, 25)])
assert cf.pet_souls(b'\r\x90\x01' + bytes(7)) is None  # pet id 0
t = cf.Tracker(); t.feed([sync] + live, 1.0)
s = t.snapshot(now=1.0)
assert s['soul_sync']['levels'] == {1130: 1, 1033: 2} and s['soul_count'] == 4 and s['soul_events'][-1]['kind'] == 'spent'
from daevaradar.features.pets import PetData, progress, SOUL_CAP
pd = PetData()
assert len(pd.collection) == 200 and pd.pets[1130]['name'] == 'Mutated Cadaver Insectoid', len(pd.collection)
assert progress(2, 12) == 42 and progress(3, 0) == SOUL_CAP == 105
fossa = pd.spawn_points('verteron', [1004])
assert fossa and all(pid == 1004 for pid, *_ in fossa), len(fossa)
assert pd.which_map({p[1] for p in fossa + pd.spawn_points('verteron', [1015])}) == 'verteron'
print('pets: sync + live drops + level up parsed;', len(pd.collection), 'pets, spawn maps', pd.keys())
print('ALL OK')
