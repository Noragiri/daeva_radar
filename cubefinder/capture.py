"""Packet capture: WinDivert on Windows, a raw socket (or a root helper) on Linux; finding the game's
connections; the engine that feeds captured packets to the tracker."""
from pathlib import Path
import ctypes
import os
import subprocess
import sys
import threading
import time

from .common import ENTRY, LINUX, NO_WINDOW, ROOT, WINDOWS
from .protocol import StreamReader


SNIFF, RECV_ONLY = 1, 4
DEFAULT_PORTS = {13328, 1111}
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
    here = ROOT
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
        # both directions: from the server (what the game tells your client) and to it (what your client sends,
        # e.g. your own movement, which some characters barely get echoed back)
        expression = " or ".join("tcp.SrcPort == %d or tcp.DstPort == %d" % (p, p) for p in sorted(self.ports))
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
        self.proc = subprocess.Popen(launcher + [sys.executable, str(ENTRY), "--capture-helper"],
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
        self.client_readers = {}  # your client -> server streams
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
                    back = (route[2], route[3], route[0], route[1])  # the same connection, the other way
                    routes = self.routes
                    if routes and route not in routes and back not in routes:
                        now = time.monotonic()
                        if now >= next_discovery:  # a brand-new socket (map change, reconnect)
                            next_discovery = now + 0.5
                            try:
                                pending = discover_routes()
                            except (OSError, subprocess.TimeoutExpired):
                                pending = set()
                        if route not in pending and back not in pending:
                            continue
                        outgoing = back in pending
                    elif routes:
                        outgoing = route not in routes
                    elif route[1] in self.ports:
                        outgoing = False
                    elif route[3] in self.ports:
                        outgoing = True
                    else:
                        continue
                    if outgoing:  # your client -> the server: its own reader, never touching the server stream
                        reader = self.client_readers.get(route)
                        if reader is None:
                            reader = self.client_readers[route] = StreamReader(
                                self.tracker.feed_client, lambda reason: None, lambda text: None)
                    else:
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
