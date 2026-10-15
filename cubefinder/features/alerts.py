"""Timed alarms: the game's recurring events and your own."""
from pathlib import Path
import datetime as _dt
import os
import time

from ..common import SOUNDS, load_settings, save_settings
from ..sound import play, voices
from ..ui import ACCENT, BG, BUTTON, BUTTON_HOVER, FONT, MONO, MUTED, TEXT, Toast, button, checkbox, heading, section
from ..feature import Feature


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
