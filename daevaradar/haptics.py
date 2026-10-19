"""Lovense haptics for the Cubes feature: the closer you are to a cube, the stronger the vibration.

Talks to the Lovense Remote app's local Game Mode (a plain HTTP API on your own network, nothing goes to the
internet): in Lovense Remote (phone) -> Discover -> Game Mode, switch it on and note the IP and HTTP port it shows
(20010). Commands are {"command": "Function", "action": "Vibrate:N", "timeSec": T, "apiVer": 1} with N 0-20.

Safety: off by default; every command lasts only a few seconds (FAILSAFE_S) and is re-sent while it's wanted, so if
Daeva Radar or the connection dies the toy stops by itself; it also stops when you leave the game window, the cube
is opened or gone, your position is out of date, the feature is switched off, or the app quits. Requests run on a
background thread, so a slow phone never stalls the radar."""
import json
import queue
import re
import threading
import time
import urllib.request

FAILSAFE_S = 3  # each command's own duration: it stops by itself if no newer one arrives
RESEND_S = 1.0  # a wanted level is sent again this often (before FAILSAFE_S runs out)
MAX_POSITION_AGE_S = 15  # your position only updates on dashes, teleports and aura ticks: older is a guess
HOST_OK = re.compile(r"^[A-Za-z0-9.\-]{1,100}$")


def level_for(distance_m, range_m, strongest):
    """Vibration level 0-20: nothing beyond range_m, 1 at its edge, `strongest` on top of the cube (linear)."""
    if distance_m is None or distance_m >= range_m or strongest <= 0:
        return 0
    return max(1, min(strongest, round(strongest * (1 - distance_m / range_m)) or 1))


class Lovense:
    def __init__(self, say=lambda text: None):
        self.say = say
        self.host, self.port = "", 20010
        self.wanted = 0  # the level the radar wants right now
        self.sent, self.sent_at = 0, 0.0
        self.status = "Off"
        self.test_until = 0.0  # a test buzz is under way: the radar's own level waits
        self.toys = []
        self.jobs = queue.Queue(maxsize=8)
        self.thread = None

    # ---- settings
    def configure(self, host, port):
        host = (host or "").strip()
        self.host = host if HOST_OK.match(host) else ""
        self.port = int(port) if str(port).isdigit() and 0 < int(port) < 65536 else 20010

    @property
    def ready(self):
        return bool(self.host)

    # ---- sending (a worker thread: urlopen can take seconds)
    def post(self, body):
        req = urllib.request.Request("http://%s:%d/command" % (self.host, self.port), data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=2.5) as r:
            return json.loads(r.read() or b"{}")

    def work(self):
        while True:
            job = self.jobs.get()
            if job is None:
                return
            kind, value = job
            try:
                if kind == "vibrate":
                    self.post(dict(command="Function", action="Vibrate:%d" % value, timeSec=FAILSAFE_S, apiVer=1))
                    self.status = "Vibrating at level %d" % value
                elif kind == "stop":
                    self.post(dict(command="Function", action="Stop", timeSec=0, apiVer=1))
                    self.status = "Connected, stopped"
                elif kind == "toys":
                    reply = self.post(dict(command="GetToys"))
                    toys = reply.get("data", {}).get("toys", reply.get("toys", {}))
                    if isinstance(toys, str):
                        toys = json.loads(toys or "{}")
                    self.toys = [t.get("name") or t.get("nickName") or "toy" for t in (toys.values() if isinstance(toys, dict) else toys)]
                    self.status = ("Connected: " + ", ".join(self.toys)) if self.toys else \
                        "Reached the app, but no toy is connected to it"
            except Exception as e:
                self.status = "Can't reach Lovense Remote at %s:%d (%s)" % (self.host, self.port, str(e)[:80])
                self.sent = 0  # try again from scratch next time

    def submit(self, kind, value=0):
        if not self.ready:
            return
        if self.thread is None or not self.thread.is_alive():
            self.thread = threading.Thread(target=self.work, daemon=True)
            self.thread.start()
        try:
            self.jobs.put_nowait((kind, value))
        except queue.Full:
            pass  # the phone is slow: the newest level goes out when it's free

    # ---- called every tick
    def update(self, level):
        """Set the level the radar wants (0 = off). Sends on a change, and every RESEND_S while it's above 0."""
        self.wanted = level
        now = time.time()
        if now < self.test_until:
            return
        if level != self.sent or (level > 0 and now - self.sent_at >= RESEND_S):
            if level > 0:
                self.submit("vibrate", level)
            elif self.sent > 0:
                self.submit("stop")
            self.sent, self.sent_at = level, now

    def stop(self):
        self.wanted = 0
        if self.sent > 0:
            self.submit("stop")
        self.sent = 0

    def test(self, level):
        """A short buzz and a look at which toys the app has: for the Test button."""
        self.status = "Testing…"
        self.submit("toys")
        self.submit("vibrate", level)
        self.test_until = time.time() + FAILSAFE_S
        self.sent = 0

    def close(self):
        self.stop()
        self.jobs.put(None)
