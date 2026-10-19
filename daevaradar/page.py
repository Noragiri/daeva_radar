"""Feature pages for the Settings window: each feature describes a tab as a list of blocks (plain dicts, drawn by
assets/web/page.js) and handles the page's actions in on_<id>(value) methods (Feature.act). No windows involved.
Radars draw with the same helpers as the overlays (ui.draw_pin, draw_rings, ...) onto a Canvas here, which records
the shapes as a display list for the page."""
from .ui import FONT, MONO


# ---- blocks
def heading(text, sub=None):
    return dict(t="h", text=text, sub=sub)


def section(text):
    return dict(t="section", text=text)


def text(value, color=None, mono=False, small=False, bold=False):
    return dict(t="text", text=value, color=color, mono=mono, small=small, bold=bold)


def lines(rows, mono=True, click=None):
    """Read-only text lines [(text, colour)] (or [(text, colour, value)] with click: on_<click>(value))."""
    return dict(t="lines", rows=[list(r) for r in rows], mono=mono, click=click)


def toggle(id, label, on, hint=None, disabled=False):
    return dict(t="toggle", id=id, label=label, on=bool(on), hint=hint, off=disabled)


def choice(id, options, value, label=None, style="seg"):
    """options: [(value, label)] or [label]; style "seg" (buttons) or "select" (a drop-down)."""
    opts = [list(o) if isinstance(o, (tuple, list)) else [o, o] for o in options]
    return dict(t="choice", id=id, options=opts, value=value, label=label, style=style)


def button(id, label, value=None, primary=False, disabled=False, confirm=None):
    return dict(t="button", id=id, label=label, value=value, primary=primary, off=disabled, confirm=confirm)


def slider(id, value, lo, hi, step=1, unit="", label=None):
    return dict(t="slider", id=id, value=value, min=lo, max=hi, step=step, unit=unit, label=label)


def number(id, value, lo, hi, step=1, label=None, unit=None):
    return dict(t="number", id=id, value=value, min=lo, max=hi, step=step, label=label, unit=unit)


def field(id, value, placeholder="", label=None, width=None):
    return dict(t="input", id=id, value=value, placeholder=placeholder, label=label, width=width)


def listbox(id, rows, selected=None, height=12, mono=True, dbl=True, empty="Nothing here."):
    """Selectable rows [(text, colour)]: a click is on_<id>(index), a double-click on_<id>_open(index)."""
    return dict(t="list", id=id, rows=[list(r) for r in rows], sel=selected, height=height, mono=mono, dbl=dbl,
                empty=empty)


def image(src, width, height):
    return dict(t="image", src=src, w=width, h=height)


def row(*blocks):
    return dict(t="row", kids=[b for b in blocks if b])


def col(*blocks):
    return dict(t="col", kids=[b for b in blocks if b])


def stats(*cards):
    """Number cards: (big, small)."""
    return dict(t="stats", cards=[list(c) for c in cards])


# ---- a drawing surface with tk.Canvas's create_* calls, recorded for the page
class Canvas:
    def __init__(self, width, height, bg="#1d2027"):
        self.w, self.h, self.bg = width, height, bg
        self.items = []

    def winfo_width(self):
        return self.w

    def winfo_height(self):
        return self.h

    def delete(self, *tags):
        if "all" in tags:
            self.items = []
        else:
            self.items = [i for i in self.items if not set(tags) & set(i[3])]

    def add(self, kind, coords, options, keys):
        flat = []
        for c in coords:
            if isinstance(c, (tuple, list)):
                for v in c:
                    flat.extend(v if isinstance(v, (tuple, list)) else [v])
            else:
                flat.append(c)
        opts = {}
        for key in keys:
            value = options.get(key)
            if value not in (None, ""):
                opts[key] = value
        if "font" in options:
            font = options["font"]
            font = font if isinstance(font, (tuple, list)) else (FONT, 10)
            opts["font"] = [abs(int(font[1])) if len(font) > 1 else 10, "bold" in font[2:], font[0] == MONO]
        if "dash" in opts:
            opts["dash"] = " ".join(str(d) for d in opts["dash"]) if isinstance(opts["dash"], (tuple, list)) else opts["dash"]
        tags = options.get("tags") or ()
        self.items.append([kind, [round(float(v), 1) for v in flat], opts, [tags] if isinstance(tags, str) else list(tags)])
        return len(self.items)

    def create_line(self, *coords, **o):
        return self.add("line", coords, o, ("fill", "width", "dash", "arrow", "capstyle"))

    def create_oval(self, *coords, **o):
        return self.add("oval", coords, dict(dict(outline="black"), **o), ("fill", "outline", "width", "dash"))

    def create_rectangle(self, *coords, **o):
        return self.add("rectangle", coords, dict(dict(outline="black"), **o), ("fill", "outline", "width", "dash"))

    def create_polygon(self, *coords, **o):
        return self.add("polygon", coords, dict(dict(fill="black"), **o), ("fill", "outline", "width", "dash", "smooth"))

    def create_arc(self, *coords, **o):
        return self.add("arc", coords, dict(dict(outline="black", start=0, extent=90), **o),
                        ("fill", "outline", "width", "dash", "start", "extent", "style"))

    def create_text(self, *coords, **o):
        return self.add("text", coords, dict(dict(fill="black", font=(FONT, 10)), **o),
                        ("text", "fill", "anchor", "font", "justify", "width"))

    def tag_lower(self, *args):
        pass

    def tag_raise(self, *args):
        pass

    def block(self):
        return dict(t="canvas", w=self.w, h=self.h, bg=self.bg, items=[i[:3] for i in self.items])
