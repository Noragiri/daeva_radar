"""Timed alarms: the game's recurring events and your own."""
from pathlib import Path
import datetime as _dt
import os
import time

from ..common import SOUNDS, berlin_offset, load_settings, save_settings
from ..sound import play, voices
from ..ui import ACCENT, MUTED, Toast
from ..feature import Feature
from .. import page as P


BUILTIN_ALARMS = [
    dict(id="shugo", name="Shugo Festival", rule=dict(type="hourly", minute=0), lead=2, builtin=True),
    dict(id="invasion", name="Dimensional Invasion", rule=dict(type="hourly", minute=30), lead=2, builtin=True),
    dict(id="rift", name="Spacetime Rift", rule=dict(type="every", hours=3, start="02:00"), lead=5, builtin=True),
]
ALERTS_DEFAULT = dict(voice="system beep", custom=None, volume=100, popup=True, at_start=False, offset=0, berlin=True,
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
class AlertsFeature(Feature):
    """Timed alarms: the game's recurring events (Shugo Festival, Dimensional Invasion, Spacetime Rift) and your
    own. Each warns a set number of minutes before (sound + popup on top of the game), whatever page is shown."""
    title, icon, TABS = "Alerts", "⏰", ("Upcoming", "Add alarm", "Settings")
    off_text = "No alarms ring while it's off."

    def __init__(self, app):
        self.cfg = dict(ALERTS_DEFAULT, **load_settings().get("alerts", {}))
        self.fired = set()
        self.new = dict(name="", kind="daily", time=(_dt.datetime.now() + _dt.timedelta(minutes=10)).strftime("%H:%M"),
                        hours="3", minute="0", lead="2")  # the Add alarm form
        self.add_message = ("", MUTED)
        super().__init__(app)

    def save(self):
        save_settings(dict(load_settings(), alerts=self.cfg))

    def now(self):
        if self.cfg["berlin"]:  # the EU servers' clock, summer / winter time included (CEST -> CET on 2026-10-25)
            return _dt.datetime.fromtimestamp(time.time() + berlin_offset(time.time()), _dt.timezone.utc).replace(tzinfo=None)
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
    def page_upcoming(self, snap):
        now = self.now()
        blocks = [P.heading("Upcoming", "Click an alarm to switch it. Times are server time (EU: Berlin)."),
                  P.text("Server time %s%s" % (now.strftime("%H:%M:%S"),
                                               "  (Berlin)" if self.cfg["berlin"] else "" if not self.cfg["offset"] else "  (PC %+dh)" % self.cfg["offset"]),
                         mono=True, small=True)]
        for when, alarm in self.upcoming():
            left = int((when - now).total_seconds())
            on = self.enabled_alarm(alarm)
            countdown = ("%d:%02d:%02d" % (left // 3600, left // 60 % 60, left % 60) if left >= 3600
                         else "%d:%02d" % (left // 60, left % 60))
            line = P.lines([("%s %-22.22s %s  in %8s   %s" % ("●" if on else "○", alarm["name"], when.strftime("%H:%M"),
                                                               countdown, describe(alarm["rule"])),
                             (ACCENT if left <= alarm["lead"] * 60 else None) if on else "#5a606a", alarm["id"])],
                           click="toggle_alarm")
            blocks.append(P.row(line, None if alarm.get("builtin") else
                                P.button("delete_alarm", "×", alarm["id"], confirm="Delete %s?" % alarm["name"])))
        return blocks

    def alarm(self, alarm_id):
        return next((a for a in self.alarms() if a["id"] == alarm_id), None)

    def on_toggle_alarm(self, alarm_id):
        if self.alarm(alarm_id):
            self.toggle_alarm(self.alarm(alarm_id))

    def on_delete_alarm(self, alarm_id):
        if self.alarm(alarm_id):
            self.delete_alarm(self.alarm(alarm_id))

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
    KINDS = (("daily", "Every day at"), ("every", "Every … hours, starting at"), ("hourly", "Every hour at minute"),
             ("once", "Once, next time it's"))

    def page_add_alarm(self, snap):
        n, kind = self.new, self.new["kind"]
        when = []
        if kind in ("daily", "every", "once"):
            when.append(P.field("new_time", n["time"], "HH:MM", label="Time", width=90))
        if kind == "every":
            when.append(P.field("new_hours", n["hours"], "3", label="every hours", width=60))
        if kind == "hourly":
            when.append(P.field("new_minute", n["minute"], "0", label="minute", width=60))
        text, color = self.add_message
        return [P.heading("Add your own alarm"),
                P.section("Name"), P.field("new_name", n["name"], "e.g. Guild raid", width=320),
                P.section("When"), P.choice("new_kind", self.KINDS, kind), P.row(*when),
                P.section("Warn me"),
                P.row(P.field("new_lead", n["lead"], "2", width=60), P.text("minutes before (0 = when it starts)", small=True)),
                P.row(P.button("add", "+ Add alarm", primary=True), P.text(text, color=color, small=True) if text else None)]

    def act(self, id, value):
        if id.startswith("new_"):  # the form's fields
            self.new[id[4:]] = value if isinstance(value, str) else str(value)
            return None
        return super().act(id, value)

    def on_add(self, _):
        self.add_alarm()

    def add_alarm(self):
        name = self.new["name"].strip()
        kind = self.new["kind"]
        try:
            if not name:
                raise ValueError("give it a name")
            lead = int(self.new["lead"])
            if not 0 <= lead <= 180:
                raise ValueError("warn 0-180 minutes before")
            if kind in ("daily", "every", "once"):
                hh, mm = map(int, self.new["time"].strip().split(":"))
                if not (0 <= hh < 24 and 0 <= mm < 60):
                    raise ValueError
                hhmm = "%02d:%02d" % (hh, mm)
            if kind == "daily":
                rule = dict(type="daily", time=hhmm)
            elif kind == "every":
                hours = int(self.new["hours"])
                if not 1 <= hours <= 24:
                    raise ValueError("every 1-24 hours")
                rule = dict(type="every", hours=hours, start=hhmm)
            elif kind == "hourly":
                minute = int(self.new["minute"])
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
            self.add_message = ("Check the fields: " + (str(e) or "time as HH:MM"), "#ff6ad5")
            return
        alarm = dict(id="custom-%d" % int(time.time() * 1000), name=name, rule=rule, lead=lead)
        self.cfg["custom_alarms"].append(alarm)
        self.save()
        when = next_occurrence(rule, self.now())
        self.add_message = ("Added: %s, next at %s" % (name, when.strftime("%H:%M") if when else "?"), "#7cf08a")
        self.new["name"] = ""

    # ---- Settings tab
    @staticmethod
    def custom_files():
        folder = SOUNDS / "custom"
        return sorted(str(p) for p in folder.glob("*") if p.suffix.lower() in (".wav", ".mp3")) if folder.exists() else []

    def page_settings(self, snap):
        voices_ = [(v, v) for v in voices()] + ([("custom", "Your own file")] if self.custom_files() else [])
        blocks = [P.heading("Alert settings"), P.section("Sound"),
                  P.row(P.choice("voice", voices_, self.cfg["voice"], style="select"), P.button("test", "▶ Test"))]
        if self.cfg["voice"] == "custom":
            blocks.append(P.row(P.choice("custom", [(f, Path(f).name) for f in self.custom_files()], self.cfg["custom"],
                                         label="File", style="select"),
                                P.button("open_custom", "Open folder")))
        return blocks + [
            P.slider("volume", self.cfg["volume"], 0, 200, 5, " %", label="Volume"),
            P.text("Your own WAV or MP3: put it in sounds/custom/ (Open folder), then pick it here.", small=True),
            P.section("Show"),
            P.toggle("flag_popup", "Popup on top of the game", self.cfg["popup"]),
            P.toggle("flag_at_start", "Also alert when it starts", self.cfg["at_start"]),
            P.section("Server clock"),
            P.toggle("flag_berlin", "Berlin time, with summer / winter time (EU servers)", self.cfg["berlin"]),
            P.row(P.number("offset", self.cfg["offset"], -23, 23),
                  P.text("else: hours from this PC's clock", small=True))]

    def on_voice(self, voice):
        if voice == "custom" and not (self.cfg["custom"] and os.path.exists(self.cfg["custom"])):
            files = self.custom_files()
            if not files:
                return
            self.cfg["custom"] = files[0]
        self.cfg["voice"] = voice
        self.save()
        self.test_alert()

    def on_custom(self, path):
        if path in self.custom_files():
            self.cfg.update(custom=os.path.normpath(path), voice="custom")
            self.save()
            self.test_alert()

    def on_open_custom(self, _):
        folder = SOUNDS / "custom"
        folder.mkdir(parents=True, exist_ok=True)
        if hasattr(os, "startfile"):
            os.startfile(str(folder))

    def on_test(self, _):
        self.test_alert()

    def on_volume(self, value):
        self.cfg["volume"] = int(value)
        self.save()
        self.test_alert()

    def on_offset(self, value):
        try:
            self.cfg["offset"] = max(-23, min(23, int(value)))
        except (TypeError, ValueError):
            return
        self.save()

    def test_alert(self):
        self.ring("Test alarm", "This is how alarms look and sound.")

    # ---- ringing
    def ring(self, title, detail):
        self.app.engine.say("Alarm: %s - %s" % (title, detail))
        play(self.cfg["voice"], self.cfg["custom"], self.cfg["volume"])
        if self.cfg["popup"]:
            Toast(self.root, "%s\n%s" % (title, detail), label="Alarm")

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

    def status(self, snap):
        items = [(w, a) for w, a in self.upcoming() if self.enabled_alarm(a)]
        if not items:
            return None
        when, alarm = items[0]
        left = int((when - self.now()).total_seconds())
        return "%s in %d:%02d" % (alarm["name"].split()[0], left // 60, left % 60) if left < 3600 else \
            "%s at %s" % (alarm["name"].split()[0], when.strftime("%H:%M"))
