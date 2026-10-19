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

# 7) summoned pets (anyone's) aren't monsters: their 'A6' spawn starts with 1f 10 after the id
ME, PET, MOB = 77, 4242, 4444
t = cf.Tracker()
me_pos = b'*8' + venc(ME) + bytes(28) + struct.pack('<fff', 1000.0, 2000.0, 300.0)
def spawn(e, x, y, head=b'\x1f\x10\x00'):  # pets' spawns start with 1f 10 after the id, monsters' don't
    return b'A6' + venc(e) + head + struct.pack('<I', 2920114) + bytes(2) + struct.pack('<fff', x, y, 300.0) + bytes(4)
t.feed([b'36' + venc(ME)], 1.0); t.feed([me_pos], 1.1)
t.feed([spawn(PET, 1200.0, 2150.0), spawn(MOB, 1100.0, 2100.0, head=b'\x04\x20\x00')], 1.4)
ids = [m['id'] for m in t.snapshot(now=1.5)['monsters']]
assert MOB in ids and PET not in ids, ids
print('pet: summoned pet left off the monsters')

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
tiles = Path(__file__).resolve().parent.parent / 'data' / 'map_tiles'
for key in ('verteron', 'altgard', 'reshanta-lower'):  # zoom 4 = the 4096 px map: 16 x 16 tiles of 256 px
    assert (tiles / key / '0' / '0_0.webp').exists() and len(list((tiles / key / '4').glob('*.webp'))) == 256, key
print('pets: sync + live drops + level up parsed;', len(pd.collection), 'pets, spawn maps', pd.keys())
# 13) wardrobe ('5b 8d', recorded 2026-10-15: 219 pieces incl. the 8 equipped skins): count | (skin u32, n u32) | slots
ward = (b'[\x8d' + venc(3) + struct.pack('<II', 30063103, 1) + struct.pack('<II', 30063101, 2)
        + struct.pack('<II', 10014106, 1) + venc(1) + b'\x01' + struct.pack('<I', 31))
assert cf.wardrobe(ward) == {30063103: 1, 30063101: 2, 10014106: 1}
assert cf.wardrobe(b'[\x8d' + venc(1) + struct.pack('<II', 5, 1)) is None  # not an item id
t = cf.Tracker(); t.feed([ward], 1.0)
assert len(t.snapshot(now=1.0)['wardrobe']['skins']) == 3
from daevaradar.features.wardrobe import SkinData
sd = SkinData()
assert sd.describe(300631)[0] == 'Light of Genesis' and sd.describe(300631)[2][3] == 'Helm', sd.describe(300631)
assert sd.describe(100141)[0] == 'Black Moonlight' and sd.describe(100141)[1] == 'Weapon'
assert sd.label(50006101) == 'Earrings' and sd.label(50006103) == 'Necklace' and sd.label(70002103) == 'Dagger'
assert sd.describe(400481)[1] == 'Special' and sd.label(10014106) == 'Orb'
print('wardrobe: parsed;', len(sd.sets), 'sets in data/skins.json')
# 14) the rotating shop ('91 56', recorded 2026-10-15): tab | count | records (one with an extra byte in front)
def offer(product, item, code, original, price):
    return struct.pack('<IIIIB', product, item, 1, 0, code) + b'\x5d' + struct.pack('<QQ', original, price)
shop = b'\x91V\x68\x02' + offer(120054, 534760058, 2, 100000, 70000) + b'\x55' + offer(120179, 534760190, 0, 100000, 90000)
tab = cf.shop_tab(shop)
assert tab[0] == 'Pets' and [(o['item'], o['discount'], o['price']) for o in tab[1]] == \
    [(534760058, 30, 70000), (534760190, 10, 90000)], tab
assert cf.shop_tab(b'\x91V\x68\x03' + shop[4:]) is None  # says 3 items, has 2
t = cf.Tracker(); t.feed([shop], 1.0)
assert t.snapshot(now=1.0)['shop']['Pets']['items'][0]['price'] == 70000
print('shop: tab parsed, discounts', [o['discount'] for o in tab[1]])
# 15) your sheet (recorded 2026-10-19): the counters ('0b 61') and inventory ('11 56') come at login BEFORE the
# message with your name, which names them; '0c 61' updates one counter (Nightmare 2 -> 1 after a run)
import struct
t = cf.Tracker()
listing = b'\x0ba' + bytes([3]) + b'\x04' + struct.pack('<I', 4) + b'\x02' + b'\x01' + struct.pack('<Iq', 3, 25200000) + \
    b'\x00' + struct.pack('<I', 80000001)
assert cf.counters(listing) == {4: 2, 3: 25200000, 80000001: 0}
inventory = b'\x11V' + bytes(5) + b'\x2f\xa4\x13\x00\x9c\x01\x00' + struct.pack('<IQ', 930100031, 86997) + b'\x01' + bytes(4)
assert cf.currencies(inventory) == {930100031: 86997}
t.feed([listing, inventory], 1.0)
assert t.snapshot(now=1)['sheet']['owner'] is None  # not named until the login message
t.feed([b'36' + venc(5898) + bytes([0x5e, 0x81, 0xc9, 0x28, 0x37, 8]) + b'Noragiri' + bytes(20)], 2.0)
t.feed([b'\x0ca' + b'\x00\x04' + struct.pack('<I', 4) + b'\x01\x03'], 3.0)
s = t.snapshot(now=3)['sheet']
assert s['owner'] == 'Noragiri' and s['counters'][4] == 1 and s['currencies'][930100031] == 86997, s
print('sheet: counters + currencies named by the login; Nightmare 2 -> 1')

# 16) Lovense haptics: the level rises as the cube gets closer; none beyond the range or without a position
from daevaradar import haptics
assert [haptics.level_for(d, 60, 10) for d in (0, 30, 59, 60, 90, None)] == [10, 5, 1, 0, 0, 0]
assert haptics.level_for(10, 60, 0) == 0
print('haptics: level by distance')

print('ALL OK')
