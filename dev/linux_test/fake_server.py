import socket, struct, time
def venc(n):
    out = bytearray()
    while True:
        b = n & 127; n >>= 7
        out.append(b | (128 if n else 0))
        if not n: return bytes(out)
def frame(body): return venc(len(body) + 4) + body
def lit(raw):
    L = len(raw); ext = b''
    if L >= 15:
        r = L - 15
        while r >= 255: ext += b'\xff'; r -= 255
        ext += bytes([r])
    return bytes([min(L, 15) << 4]) + ext + raw
def compressed(frames):
    raw = b''.join(frames)
    return frame(b'\xff\xff' + struct.pack('<I', len(raw)) + lit(raw))
OWN = 6190
me = b'36' + venc(OWN) + bytes(5)
pos = b'*8' + venc(OWN) + bytes(28) + struct.pack('<fff', 10000., 20000., 500.)
cube = b'46' + venc(40748) + b'\x04\x00' + struct.pack('<I', 1200039) + struct.pack('<fff', 13000., 16000., 1500.)
srv = socket.socket(); srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv.bind(('127.0.0.1', 13328)); srv.listen(1)
conn, _ = srv.accept()
for i in range(25):
    conn.sendall(compressed([frame(me), frame(pos)]) + compressed([frame(cube)]) + frame(b'zz' + bytes(20)))
    time.sleep(1)
