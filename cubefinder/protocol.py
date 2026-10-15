"""AION 2 network stream: TCP reassembly into frames, varint-framed messages, LZ4-compressed blocks."""
import collections
import struct


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
