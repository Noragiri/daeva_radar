"""Offline decoder/tracker tests: python dev/test_cube_finder.py (no game, capture or admin)."""
import sys, struct, random
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from types import SimpleNamespace
from cubefinder import protocol, game, ui
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
print('ALL OK')
