"""Wardrobe: your appearance collection (armour, cloak and weapon skins) by set, from the game's login message."""
import json
import time

from ..common import ROOT, load_settings, save_settings
from ..ui import BG, BUTTON, BUTTON_HOVER, FONT, MONO, MUTED, PANEL, TEXT, Toast, heading
from ..feature import Feature


WARDROBE_DEFAULT = dict(kind="All", show="owned")
KINDS = ("Armour", "Weapon", "Accessory", "Special", "Other")
KIND_COLOR = {"Armour": "#9fe3ff", "Weapon": "#ffb347", "Accessory": "#ff9ad9", "Special": "#c58cff", "Other": "#9aa0aa"}
WEAPON_PIECES = {1: "Greatsword", 2: "Longsword", 3: "Dagger", 4: "Bow", 5: "Spellbook", 6: "Orb", 7: "Mace", 8: "Staff"}
ACCESSORY_PIECES = {1: "Earrings", 3: "Necklace"}
# The in-game Closet's totals ("Collection Effect", 2026-10-15): per weapon type, all armour, all accessories.
# The wardrobe message only lists what you own, so these are kept here; update them after a patch adds skins.
CLOSET_TOTALS = {"Weapon": 56, "Armour": 766, "Accessory": 117}


class SkinData:
    """data/skins.json (written by dev/fetch_skin_data.py; credit metabot.gg): set id -> name, kind, pieces.
    A skin's item id is set id x 100 + piece."""

    def __init__(self, path=None):
        try:
            self.sets = {int(k): v for k, v in json.loads(
                (path or ROOT / "data" / "skins.json").read_text(encoding="utf-8"))["sets"].items()}
        except (OSError, ValueError, KeyError):
            self.sets = {}

    def describe(self, set_id):
        """-> (name, kind, {piece: label}, total) for any set id. Sets not in skins.json go by their id range:
        500... accessories (01 Earrings, 03 Necklace: checked against the Closet's counts), 700... weapon skins
        (pieces numbered like the weapon series: 70002103 is a dagger)."""
        s = self.sets.get(set_id)
        if s:
            pieces = {int(k): v for k, v in s["pieces"].items()}
            return s["name"], s["kind"], pieces, s.get("total", len(pieces))
        if set_id // 1000 == 500:
            return "Accessory set %d" % set_id, "Accessory", dict(ACCESSORY_PIECES), len(ACCESSORY_PIECES)
        if set_id // 1000 == 700:
            return "Weapon skin %d" % set_id, "Weapon", {}, None
        return "Unknown set %d" % set_id, "Other", {}, None

    def label(self, skin):
        """A piece's name ("Helm", "Orb", "Earrings"...), also for sets not in skins.json."""
        name, kind, pieces, total = self.describe(skin // 100)
        return pieces.get(skin % 100) or (WEAPON_PIECES if kind == "Weapon" else {}).get(skin % 100) or \
            "piece %02d" % (skin % 100)


class WardrobeFeature(Feature):
    """Your wardrobe (the Closet, account-wide: one collection whichever character logs in). The game sends every
    appearance piece you own when you enter the world ('5b 8d'); this
    groups them into sets (names and pieces: data/skins.json) and shows what each set is missing."""
    title, icon, TABS = "Wardrobe", "❖", ("Sets",)
    off_text = "The wardrobe isn't read while it's off."

    def __init__(self, app, parent):
        settings = load_settings()
        self.cfg = dict(WARDROBE_DEFAULT, **settings.get("wardrobe_view", {}))
        saved = settings.get("wardrobe", {})
        self.skins = {int(k): v for k, v in saved.get("skins", {}).items()}
        self.synced = saved.get("synced")
        self.data = SkinData()
        self.seen = None
        self.rows = []
        self.selected = None
        super().__init__(app, parent)

    def save(self):
        save_settings(dict(load_settings(), wardrobe_view=self.cfg))

    def owned_sets(self):
        """set id -> owned piece numbers."""
        out = {}
        for skin in self.skins:
            out.setdefault(skin // 100, set()).add(skin % 100)
        return out

    def sync(self, snap):
        if not snap["wardrobe"] or snap["wardrobe_count"] == self.seen:
            return
        first = self.seen is None and not self.skins
        self.seen = snap["wardrobe_count"]
        new = sorted(set(snap["wardrobe"]["skins"]) - set(self.skins))
        self.skins = dict(snap["wardrobe"]["skins"])
        self.synced = time.strftime("%Y-%m-%d %H:%M")
        save_settings(dict(load_settings(), wardrobe=dict(skins=self.skins, synced=self.synced)))
        self.app.engine.say("Wardrobe: synced (%d pieces)" % len(self.skins))
        if new and not first and self.synced:
            names = ["%s (%s)" % (self.data.describe(s // 100)[0], self.data.label(s)) for s in new[:3]]
            Toast(self.root, "❖ Wardrobe: %d new piece%s\n%s" % (len(new), "" if len(new) == 1 else "s",
                                                               "\n".join(names) + ("\n…" if len(new) > 3 else "")))
        self.refresh()

    # ---- Sets tab
    def build_sets(self, f):
        tk = self.tk
        heading(tk, f, "Wardrobe", None)
        self.summary = tk.Label(f, text="", bg=BG, fg=TEXT, font=(FONT, 10, "bold"), anchor="w", justify="left")
        self.summary.pack(fill="x")
        self.sub = tk.Label(f, text="", bg=BG, fg=MUTED, font=(FONT, 9), anchor="w", justify="left")
        self.sub.pack(fill="x", pady=(0, 6))
        row = tk.Frame(f, bg=BG)
        row.pack(fill="x")
        self.search = tk.StringVar()
        tk.Entry(row, textvariable=self.search, bg=BUTTON, fg=TEXT, insertbackground=TEXT, relief="flat",
                 font=(FONT, 10), width=14).pack(side="left", ipady=3, padx=(0, 6))
        self.search.trace_add("write", lambda *a: self.refresh())
        self.kind_var = tk.StringVar(value=self.cfg["kind"])
        for value in ("All",) + KINDS:
            self.radio(row, value, value, self.kind_var)
        row = tk.Frame(f, bg=BG)
        row.pack(fill="x", pady=(4, 0))
        self.show_var = tk.StringVar(value=self.cfg["show"])
        for value, text in (("owned", "Owned"), ("incomplete", "Incomplete"), ("complete", "Complete"),
                            ("missing", "Not started"), ("all", "All sets"), ("pieces", "Missing pieces")):
            self.radio(row, value, text, self.show_var)
        self.tk.Button(row, text="Copy list", command=self.copy_list, bg=BUTTON, fg=TEXT, relief="flat", bd=0,
                       activebackground=BUTTON_HOVER, activeforeground=TEXT, font=(FONT, 9), padx=8,
                       cursor="hand2").pack(side="right")
        self.listbox = tk.Listbox(f, height=14, bg=PANEL, fg=TEXT, selectbackground=BUTTON_HOVER, relief="flat",
                                  highlightthickness=0, font=(MONO, 9), activestyle="none")
        self.listbox.pack(fill="x", pady=(6, 0))
        self.listbox.bind("<<ListboxSelect>>", lambda e: self.select_row())
        self.detail = tk.Label(f, text="", bg=BG, fg=MUTED, font=(MONO, 9), anchor="nw", justify="left", height=9)
        self.detail.pack(fill="x", pady=(6, 0))
        self.refresh()

    def radio(self, parent, value, text, var):
        self.tk.Radiobutton(parent, text=text, value=value, variable=var, command=self.set_filters, bg=BG, fg=TEXT,
                            selectcolor=BUTTON, activebackground=BG, activeforeground=TEXT, font=(FONT, 9),
                            indicatoron=False, padx=6, pady=2, relief="flat", bd=0, offrelief="flat",
                            cursor="hand2").pack(side="left", padx=(0, 2))

    def set_filters(self):
        self.cfg.update(kind=self.kind_var.get(), show=self.show_var.get())
        self.save()
        self.refresh()

    def refresh(self):
        if not hasattr(self, "listbox"):
            return
        owned = self.owned_sets()
        ids = set(owned) | set(self.data.sets)
        complete = sum(1 for k in ids if owned.get(k) and self.data.describe(k)[3] and
                       len(owned[k]) >= self.data.describe(k)[3])
        self.summary.config(text="%d pieces · %d sets started · %d complete · %s" % (
            len(self.skins), len(owned), complete, self.closet_line()))
        weapons = self.weapon_counts()
        self.sub.config(text=("Weapons /%d: %s  ·  " % (CLOSET_TOTALS["Weapon"], ", ".join(
            "%s %d" % (w, weapons[w]) for w in sorted(weapons, key=lambda w: -weapons[w])) or "none") if self.skins else "") +
            (("synced %s" % self.synced) if self.synced else "Not synced yet: enter the world with the radar running."))
        text, kind, show = self.search.get().strip().lower(), self.cfg["kind"], self.cfg["show"]
        if show == "pieces":
            return self.refresh_pieces(ids, owned, text, kind)
        rows = []
        for k in ids:
            name, k_kind, pieces, total = self.data.describe(k)
            have = owned.get(k, set())
            done = total is not None and len(have) >= total
            if kind != "All" and k_kind != kind or text and text not in name.lower():
                continue
            if (show == "owned" and not have or show == "incomplete" and (not have or done) or
                    show == "complete" and not done or show == "missing" and have):
                continue
            rows.append((k_kind, name, k, len(have), total))
        rows.sort(key=lambda r: (r[0], r[1]))
        self.rows = [r[2] for r in rows]
        self.listbox.delete(0, "end")
        for i, (k_kind, name, k, have, total) in enumerate(rows):
            mark = "✓" if total and have >= total else ("·" if not have else " ")
            self.listbox.insert("end", "%s %-28.28s %-6s %2d/%s" % (mark, name, k_kind, have, total if total else "?"))
            self.listbox.itemconfig(i, fg=KIND_COLOR.get(k_kind, TEXT) if have else MUTED)
        if not rows:
            self.listbox.insert("end", "  Nothing matches." if self.skins else "  Waiting for the wardrobe (enter the world).")
        self.show_detail()

    def refresh_pieces(self, ids, owned, text, kind):
        """Missing pieces view: one row per piece you don't own (of the sets with known pieces)."""
        rows = []
        for k in ids:
            name, k_kind, pieces, total = self.data.describe(k)
            if kind != "All" and k_kind != kind:
                continue
            have = owned.get(k, set())
            for n, label in sorted(pieces.items()):
                if n not in have and (not text or text in name.lower() or text in label.lower()):
                    rows.append((k_kind, name, label, k, bool(have)))
        rows.sort(key=lambda r: (not r[4], r[0], r[1], r[2]))  # sets you've started first
        self.rows = [r[3] for r in rows]
        self.listbox.delete(0, "end")
        for i, (k_kind, name, label, k, started) in enumerate(rows):
            self.listbox.insert("end", "· %-28.28s %-12.12s %s" % (name, label, k_kind))
            self.listbox.itemconfig(i, fg=KIND_COLOR.get(k_kind, TEXT) if started else MUTED)
        if not rows:
            self.listbox.insert("end", "  Nothing missing here." if self.skins else "  Waiting for the wardrobe (enter the world).")
        self.summary.config(text="%d missing pieces%s (%d in sets you've started)" % (
            len(rows), "" if kind == "All" else " · " + kind, sum(1 for r in rows if r[4])))
        self.show_detail()

    def copy_list(self):
        """The rows shown, to the clipboard (paste into a note or a message)."""
        lines = [self.listbox.get(i).strip() for i in range(self.listbox.size())]
        self.root.clipboard_clear()
        self.root.clipboard_append("\n".join(lines))
        self.app.engine.say("Wardrobe: copied %d lines" % len(lines))

    def kind_counts(self):
        kinds = {}
        for skin in self.skins:
            kind = self.data.describe(skin // 100)[1]
            kinds[kind] = kinds.get(kind, 0) + 1
        return kinds

    def weapon_counts(self):
        out = {}
        for skin in self.skins:
            if self.data.describe(skin // 100)[1] == "Weapon":
                w = self.data.label(skin)
                out[w] = out.get(w, 0) + 1
        return out

    def closet_line(self):
        """Armour and accessories against the Closet's totals (its 'Collection Effect' numbers)."""
        kinds = self.kind_counts()
        return " · ".join("%s %d/%d (%d%%)" % (k, kinds.get(k, 0), CLOSET_TOTALS[k], 100 * kinds.get(k, 0) // CLOSET_TOTALS[k])
                          for k in ("Armour", "Accessory"))

    def select_row(self):
        sel = self.listbox.curselection()
        if sel and sel[0] < len(self.rows):
            self.selected = self.rows[sel[0]]
            self.show_detail()

    def show_detail(self):
        if self.selected is None:
            self.detail.config(text="Select a set for its pieces.")
            return
        name, kind, pieces, total = self.data.describe(self.selected)
        have = self.owned_sets().get(self.selected, set())
        lines = ["%s (%s) · %d/%s" % (name, kind, len(have), total if total else "?")]
        numbers = sorted(set(pieces) | have)
        for n in numbers:
            lines.append("  %s %s" % ("✓" if n in have else "·", self.data.label(self.selected * 100 + n)))
        self.detail.config(text="\n".join(lines[:10]))

    # ---- loop hooks
    def background(self, snap):
        self.sync(snap)
        super().background(snap)

    def status(self, snap):
        return "%d skins" % len(self.skins) if self.skins else None
