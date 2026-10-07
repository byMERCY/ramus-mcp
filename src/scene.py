"""Turn a diagram into drawing primitives, once, for every output format.

The SVG and PNG renderers both draw the same picture - the frame, the boxes, the arrows with
their heads and names - and the decisions that make it look like what Ramus shows (where an
arrowhead goes, how a name is tied to its line, where text wraps) should not be written twice.
This module makes them: it walks a :class:`~ramus_rsf.Diagram` and emits a flat list of
primitives in *model units*, the same coordinates the .rsf stores. A backend only has to know
how to draw a rectangle, a polyline, a polygon, a dot and a line of text.

Text is laid out here too (wrapped and positioned line by line), so both outputs break lines in
the same places. Widths are measured with a real TrueType face when one can be found, which
also lets Cyrillic wrap correctly, and estimated otherwise.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from functools import lru_cache
from typing import List, Optional, Tuple, Union

try:
    from .ramus_rsf import Activity, Arrow, Diagram, FreeText
except ImportError:  # pragma: no cover - script execution
    from ramus_rsf import Activity, Arrow, Diagram, FreeText


Point = Tuple[float, float]
# A dash pattern - dash, gap, dash, gap ... lengths, repeated (an odd-length list repeats
# twice over, as in Java's BasicStroke and SVG) - or None for a solid line.
Dash = Optional[Tuple[float, ...]]

# The look. Arrowhead size is Ramus's: a triangle 8 long and 6 wide.
HEAD_LENGTH = 8.0
HEAD_HALF_WIDTH = 3.0
MIN_LINE_WIDTH = 1.0  # Ramus draws some arrows 0.5 wide; that is too thin to read
BOX_STROKE = 1.4
FRAME_COLOR = "#c3c8d0"
INK = "#111111"
NUMBER_COLOR = "#666666"
NUMBER_SIZE = 9.0
STUB_COLOR = "#7a869a"
HEADING_COLOR = "#777777"
HEADING_SIZE = 12.0
HEADER_HEIGHT = 24.0  # room above the frame for the heading
PAGE_MARGIN = 7.0
BASELINE = 0.88  # where the baseline falls in a line, as a fraction of the font size


# ------------------------------------------------------------------------------- primitives


@dataclass
class Rect:
    x: float
    y: float
    w: float
    h: float
    fill: Optional[str] = None
    stroke: Optional[str] = None
    stroke_width: float = 1.0
    dash: Dash = None
    radius: float = 0.0  # rounded corners, as a data flow diagram's process has them


@dataclass
class Line:
    points: List[Point]
    color: str
    width: float = 1.0
    dash: Dash = None


@dataclass
class Poly:
    points: List[Point]
    fill: str


@dataclass
class Dot:
    x: float
    y: float
    r: float
    fill: str


@dataclass
class Text:
    """One line of text; (x, y) is where the baseline meets the anchor."""

    x: float
    y: float
    text: str
    size: float
    color: str
    anchor: str = "middle"  # "start", "middle" or "end"
    bold: bool = False


Item = Union[Rect, Line, Poly, Dot, Text]


@dataclass
class Form:
    """The IDEF0 diagram form round a sheet (see RsfModel.form_of)."""

    author: str = ""
    project: str = ""
    used_at: str = ""
    date: str = ""
    revision: str = ""
    status: int = 0  # 0 working, 1 draft, 2 recommended, 3 publication
    node: str = ""
    title: str = ""
    number: str = ""
    context: List[Tuple[float, float, float, float]] = field(default_factory=list)
    context_mark: Optional[int] = None
    top: bool = False
    russian: bool = False


# The form's words: FIPS 183, and their Russian as GOST R 50.1.028 has them.
_FORM_WORDS = {
    False: {"used_at": "USED AT:", "author": "AUTHOR:", "project": "PROJECT:",
            "notes": "NOTES:  1  2  3  4  5  6  7  8  9  10", "date": "DATE:", "rev": "REV:",
            "status": ("WORKING", "DRAFT", "RECOMMENDED", "PUBLICATION"), "reader": "READER",
            "reader_date": "DATE", "context": "CONTEXT:", "top": "TOP", "node": "NODE:",
            "title": "TITLE:", "number": "NUMBER:"},
    True: {"used_at": "ИСПОЛЬЗУЕТСЯ В:", "author": "АВТОР:", "project": "ПРОЕКТ:",
           "notes": "ЗАМЕЧАНИЯ:  1  2  3  4  5  6  7  8  9  10", "date": "ДАТА:",
           "rev": "РЕВИЗИЯ:",
           "status": ("РАЗРАБАТЫВАЕТСЯ", "ЧЕРНОВИК", "РЕКОМЕНДОВАНО", "ПУБЛИКАЦИЯ"),
           "reader": "ЧИТАТЕЛЬ", "reader_date": "ДАТА", "context": "КОНТЕКСТ:",
           "top": "ВЕРХ", "node": "УЗЕЛ:", "title": "НАЗВАНИЕ:", "number": "НОМЕР:"},
}
FORM_HEADER = 66.0  # the header's height above the page, in model units ...
FORM_FOOTER = 44.0  # ... and the footer's below it
FORM_TEXT = 7.5
FORM_INK = "#000000"


def _form(form: Form, width: float, bottom: float) -> List[Item]:
    """The header above the page (0..``width`` across, the page from 0 down to ``bottom``)
    and the footer below it: ruled cells with their words, the status ticked, the context
    drawn small with this sheet's box filled in."""
    w = _FORM_WORDS[form.russian]
    top = -FORM_HEADER
    row = FORM_HEADER / 4
    size = FORM_TEXT
    items: List[Item] = []

    def text(x, y, s, anchor="start", sz=size):
        items.append(Text(x, y, s, sz, FORM_INK, anchor))

    def rule(points, width_=0.8):
        items.append(Line(points, FORM_INK, width_))

    # The outline: header, page and footer in one; the page's own edges are the frame.
    items.append(Rect(0.0, top, width, bottom + FORM_FOOTER - top, None, FORM_INK, 1.2))
    rule([(0.0, 0.0), (width, 0.0)], 1.2)
    rule([(0.0, bottom), (width, bottom)], 1.2)
    # Header columns: used at | author, project, notes - date, revision | status | reader,
    # date | context.
    c1, c2, c3, c4, c5, c6 = (width * f for f in (0.166, 0.5, 0.52, 0.6875, 0.856, 1.0))
    for x in (c1, c2, c3, c4, c5):
        rule([(x, top), (x, 0.0)])
    for i in range(1, 4):  # the status and reader rows
        rule([(c2, top + i * row), (c5, top + i * row)])
    rule([(c4 + (c5 - c4) * 0.62, top), (c4 + (c5 - c4) * 0.62, 0.0)])
    base = row * 0.72
    text(2.0, top + base, w["used_at"])
    if form.used_at:
        text(2.0, top + row + base, form.used_at)
    text(c1 + 2.0, top + base, f'{w["author"]} {form.author}')
    text(c1 + 2.0, top + row + base, f'{w["project"]} {form.project}')
    text(c1 + 2.0, top + 3 * row + base, w["notes"])
    split = c1 + (c2 - c1) * 0.62
    text(split, top + base, f'{w["date"]} {form.date}')
    text(split, top + row + base, f'{w["rev"]} {form.revision}')
    for i, label in enumerate(w["status"]):
        text(c3 + 2.0, top + i * row + base, label)
        if i == form.status:
            items.append(Rect(c2 + 3.0, top + i * row + 3.0, c3 - c2 - 6.0, row - 6.0,
                              FORM_INK))
    text(c4 + 2.0, top + base, w["reader"])
    text(c5 - 2.0, top + base, w["reader_date"], "end")
    text(c5 + 2.0, top + base, w["context"])
    # The context: the sheet above, drawn small, this sheet's box in black.
    if form.top:
        text((c5 + c6) / 2, top + FORM_HEADER * 0.62, w["top"], "middle", size * 1.2)
    elif form.context:
        # The page above, shrunk whole - its boxes where they stand on it.
        x0, y0, x1, y1 = 0.0, 0.0, width, bottom
        box = (c5 + 6.0, top + row + 2.0, c6 - c5 - 12.0, FORM_HEADER - row - 6.0)
        k = min(box[2] / max(x1 - x0, 1.0), box[3] / max(y1 - y0, 1.0))
        ox = box[0] + (box[2] - (x1 - x0) * k) / 2
        oy = box[1] + (box[3] - (y1 - y0) * k) / 2
        for i, (x, y, rw, rh) in enumerate(form.context):
            marked = i == form.context_mark
            items.append(Rect(ox + (x - x0) * k, oy + (y - y0) * k, max(rw * k, 2.0),
                              max(rh * k, 1.5), FORM_INK if marked else "#ffffff",
                              FORM_INK, 0.5))
    # The footer: node | title | number.
    f1, f2 = width * 0.2, width * 0.8
    for x in (f1, f2):
        rule([(x, bottom), (x, bottom + FORM_FOOTER)])
    text(2.0, bottom + row * 0.8, w["node"])
    text(f1 / 2, bottom + FORM_FOOTER * 0.7, form.node, "middle", size * 1.6)
    text(f1 + 2.0, bottom + row * 0.8, w["title"])
    for i, line in enumerate(wrap(form.title, f2 - f1 - 20.0, size * 1.6)[:2]):
        text((f1 + f2) / 2, bottom + FORM_FOOTER * (0.62 if i == 0 else 0.92) - (
            size * 0.9 if len(wrap(form.title, f2 - f1 - 20.0, size * 1.6)) > 1 else 0.0),
             line, "middle", size * 1.6)
    text(f2 + 2.0, bottom + row * 0.8, w["number"])
    text((f2 + width) / 2, bottom + FORM_FOOTER * 0.7, form.number, "middle", size * 1.6)
    return items


@dataclass
class Scene:
    """What to draw and on what canvas (``x, y, width, height`` in model units)."""

    x: float
    y: float
    width: float
    height: float
    items: List[Item] = field(default_factory=list)


# ---------------------------------------------------------------------------- measuring text

_FONT_NAMES = ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf")
_REFERENCE_SIZE = 100  # measure big, scale down: small sizes round badly


_BOLD_NAMES = ("segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf",
               "LiberationSans-Bold.ttf")


@lru_cache(maxsize=1)
def bold_font_path() -> Optional[str]:
    """Path of a bold TrueType face, or None (bold names are then drawn regular)."""
    try:
        from PIL import ImageFont
    except ImportError:  # pragma: no cover - Pillow is a requirement
        return None
    for name in _BOLD_NAMES:
        try:
            return ImageFont.truetype(name, 20).path
        except OSError:
            continue
    return None


@lru_cache(maxsize=1)
def font_path() -> Optional[str]:
    """Path of a TrueType face that can draw Cyrillic, or None if none is installed."""
    try:
        from PIL import ImageFont
    except ImportError:  # pragma: no cover - Pillow is a requirement
        return None
    for name in _FONT_NAMES:
        try:
            return ImageFont.truetype(name, 20).path
        except OSError:
            continue
    return None


@lru_cache(maxsize=1)
def _reference_font():
    path = font_path()
    if path is None:
        return None
    from PIL import ImageFont

    return ImageFont.truetype(path, _REFERENCE_SIZE)


def text_width(text: str, size: float) -> float:
    font = _reference_font()
    if font is None:
        return len(text) * size * 0.55
    return font.getlength(text) * size / _REFERENCE_SIZE


# Java's "Dialog" font is Arial on Windows. Ramus measures text without fractional metrics, so
# on a screen at 100 % each glyph's advance is rounded to a whole unit - "результаты" at 10 is
# 56 wide there, not the 53.6 its outline says - and a name whose box is narrower is broken
# onto another line.
_JAVA_FACES = ("arial.ttf", "Arial.ttf", "LiberationSans-Regular.ttf")


@lru_cache(maxsize=1)
def _java_font():
    try:
        from PIL import ImageFont
    except ImportError:  # pragma: no cover - Pillow is a requirement
        return None
    for name in _JAVA_FACES:
        try:
            return ImageFont.truetype(name, _REFERENCE_SIZE)
        except OSError:
            continue
    return None


@lru_cache(maxsize=4096)
def _java_advance(char: str, size: float) -> float:
    return _java_font().getlength(char) * size / _REFERENCE_SIZE


def label_width(text: str, size: float) -> float:
    """How wide a box an arrow name needs so that Ramus keeps it on the lines it was given:
    the widest of the measures it may be taken by - our own face, Java's Dialog (Arial) with
    and without its glyphs rounded - and a little room."""
    widths = [text_width(text, size)]
    if _java_font() is not None:
        widths.append(_java_font().getlength(text) * size / _REFERENCE_SIZE)
        widths.append(sum(round(_java_advance(c, size)) for c in text))
    return max(widths) + 4.0


def _pieces(paragraph: str, width: float, size: float) -> List[Tuple[str, str]]:
    """A paragraph as (separator, text) pieces to lay out in order.

    Pieces are words; a word that is wider than a whole line is cut after its hyphens, the way
    a text component breaks "Планово-экономическое", and the cuts join without a space.
    """
    pieces: List[Tuple[str, str]] = []
    for word in paragraph.split():
        sep = " " if pieces else ""
        if text_width(word, size) <= width or "-" not in word.strip("-"):
            pieces.append((sep, word))
            continue
        parts = [p for p in word.replace("-", "-\0").split("\0") if p]
        for i, part in enumerate(parts):
            pieces.append((sep if i == 0 else "", part))
    return pieces


def wrap(text: str, width: float, size: float) -> List[str]:
    """Break ``text`` into lines no wider than ``width``.

    Explicit newlines stay; otherwise lines break at spaces (and after hyphens inside a word
    that is too long on its own). A single unbreakable word wider than the line is left whole
    rather than cut.
    """
    lines: List[str] = []
    for paragraph in text.split("\n"):
        pieces = _pieces(paragraph, width, size)
        if not pieces:
            lines.append("")
            continue
        line = pieces[0][1]
        for sep, piece in pieces[1:]:
            if text_width(line + sep + piece, size) <= width:
                line += sep + piece
            else:
                lines.append(line)
                line = piece
        lines.append(line)
    return lines


# -------------------------------------------------------------------------------- geometry


def _unit(a: Point, b: Point) -> Optional[Point]:
    """Unit vector from a to b, or None if they coincide."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy)
    if length < 1e-9:
        return None
    return dx / length, dy / length


def _outward(points: List[Point], at_start: bool) -> Optional[Tuple[Point, Point]]:
    """The end point of a route and the unit vector leaving the route through it.

    Looks past repeated points so a zero-length last segment does not hide the direction.
    """
    seq = points if not at_start else list(reversed(points))
    end = seq[-1]
    for p in reversed(seq[:-1]):
        u = _unit(p, end)
        if u is not None:
            return end, u
    return None


def _path_length(points: List[Point]) -> float:
    return sum(math.dist(points[i], points[i + 1]) for i in range(len(points) - 1))


def _point_at(points: List[Point], fraction: float) -> Point:
    """The point ``fraction`` (0..1) of the way along a route, by length."""
    target = max(0.0, min(1.0, fraction)) * _path_length(points)
    for i in range(len(points) - 1):
        seg = math.dist(points[i], points[i + 1])
        if seg >= target and seg > 0:
            t = target / seg
            return (
                points[i][0] + (points[i + 1][0] - points[i][0]) * t,
                points[i][1] + (points[i + 1][1] - points[i][1]) * t,
            )
        target -= seg
    return points[-1]


# ----------------------------------------------------------------------------------- parts


# Shapes on a data flow diagram, as Ramus draws them.
DFD_RADIUS = 10.0  # a process's rounded corners
ROLE_RADIUS = 2.0  # a DFDS role's
STORE_LEFT = 20.0  # the compartment at the left of a data store, for its code
LEAF_MARK = 4.0  # the stroke across the top corner of an activity with no decomposition ...
DFD_LEAF_MARK = 15.0  # ... of a process on a data flow diagram (across its rounded corner)
SMALL_TEXT = 0.8  # the second part of a DFDS name is written this much smaller


def _name_lines(items: List[Item], text: str, x: float, y: float, w: float, h: float,
                size: float, ink: str, reserved: float = 0.0) -> None:
    """A name wrapped to a box ``w`` wide and centred in it, above a ``reserved`` strip."""
    line_h = size * 1.2
    lines = wrap(text, max(w - 10.0, size), size)
    top = y + (h - reserved - len(lines) * line_h) / 2.0 + 1.0
    for i, line in enumerate(lines):
        items.append(Text(x + w / 2.0, top + i * line_h + BASELINE * size + 0.1 * size,
                          line, size, ink))


def _box(a: Activity, notation: str = "idef0") -> List[Item]:
    """One box (see _box_shapes), its name in bold if it is set so."""
    items = _box_shapes(a, notation)
    if a.bold:
        for item in items:
            if isinstance(item, Text) and item.size != NUMBER_SIZE:
                item.bold = True
    return items


def _box_shapes(a: Activity, notation: str = "idef0") -> List[Item]:
    """One box in the shape its sheet's notation gives it: an IDEF0 activity a rectangle with
    its node number; on a data flow diagram a process with rounded corners and its number,
    an external entity with a second line along its top and left, a data store open at the
    right with a compartment at the left; on a DFDS diagram an activity with its name over
    the smaller second part of it, and a role as a small rounded tag. An activity with no
    decomposition of its own has a short stroke across a top corner, as Ramus marks it."""
    ink = a.color or INK
    fill = a.fill or "#ffffff"
    x, y, w, h = a.x, a.y, a.width, a.height
    size = a.font_size
    items: List[Item] = []
    if a.kind == "external":
        items.append(Rect(x, y, w, h, fill, ink, BOX_STROKE))
        items.append(Line([(x + 2.0, y + h), (x + 2.0, y + 2.0), (x + w, y + 2.0)], ink, 1.0))
        _name_lines(items, a.name, x + 2.0, y + 2.0, w - 2.0, h - 2.0, size, ink)
        return items
    if a.kind == "store":
        items.append(Rect(x, y, w, h, fill))
        items.append(Line([(x + w, y), (x, y), (x, y + h), (x + w, y + h)], ink, BOX_STROKE))
        items.append(Line([(x + STORE_LEFT, y), (x + STORE_LEFT, y + h)], ink, 1.0))
        _name_lines(items, a.name, x + STORE_LEFT, y, w - STORE_LEFT, h, size, ink)
        return items
    if a.kind == "role":
        items.append(Rect(x, y, w, h, fill, ink, 1.0, radius=ROLE_RADIUS))
        _name_lines(items, a.name, x, y, w, h, size, ink)
        return items
    if notation == "dfd":
        items.append(Rect(x, y, w, h, fill, ink, BOX_STROKE, radius=DFD_RADIUS))
        if not a.decomposed:
            items.append(Line([(x + DFD_LEAF_MARK, y), (x, y + DFD_LEAF_MARK)], ink, 1.0))
        # A process shows its place among the processes on its sheet: the last digits of its
        # number - and the top one, A0, is the first (and only) on the context diagram.
        label = "1" if a.number == "A0" else \
            a.number[len(a.number.rstrip("0123456789")):] if a.number else ""
        _name_lines(items, a.name, x, y, w, h, size, ink, NUMBER_SIZE if label else 0.0)
        if label:
            items.append(Text(x + w - 4.0, y + h - 3.0, label, NUMBER_SIZE, NUMBER_COLOR, "end"))
        return items
    items.append(Rect(x, y, w, h, fill, ink, BOX_STROKE))
    if notation == "dfds":
        if not a.decomposed:
            items.append(Line([(x + w - LEAF_MARK, y), (x + w, y + LEAF_MARK)], ink, 1.0))
        # The name along the top - the bottom of the box is where its roles stand.
        small = size * SMALL_TEXT
        lines = wrap(a.name, max(w - 10.0, size), size)
        below = wrap(a.long_name, max(w - 10.0, small), small) if a.long_name else []
        top = y + 2.0
        for i, line in enumerate(lines):
            items.append(Text(x + w / 2.0, top + i * size * 1.2 + BASELINE * size, line, size,
                              ink))
        if below:
            rule = top + len(lines) * size * 1.2 + 2.0
            items.append(Line([(x, rule), (x + w, rule)], ink, 0.8, (1.0, 2.0)))
            for i, line in enumerate(below):
                items.append(Text(x + 4.0, rule + 2.0 + i * small * 1.2 + BASELINE * small,
                                  line, small, ink, "start"))
        return items
    if not a.decomposed:
        items.append(Line([(x + LEAF_MARK, y), (x, y + LEAF_MARK)], ink, 1.0))
    _name_lines(items, a.name, x, y, w, h, size, ink, NUMBER_SIZE if a.number else 0.0)
    if a.number:
        items.append(Text(x + w - 3.0, y + h - 3.0, a.number, NUMBER_SIZE, NUMBER_COLOR, "end"))
    return items


def _head(points: List[Point], color: str) -> Optional[Poly]:
    found = _outward(points, at_start=False)
    if found is None:
        return None
    tip, (ux, uy) = found
    base = (tip[0] - ux * HEAD_LENGTH, tip[1] - uy * HEAD_LENGTH)
    nx, ny = -uy * HEAD_HALF_WIDTH, ux * HEAD_HALF_WIDTH
    return Poly([tip, (base[0] + nx, base[1] + ny), (base[0] - nx, base[1] - ny)], color)


def _tunnel(points: List[Point], at_start: bool, soft: bool, color: str) -> List[Item]:
    """The two brackets that mark an arrow end as tunnelled, just behind the head.

    ``( )`` for a soft tunnel, ``[ ]`` for a hard one, drawn across the line.
    """
    found = _outward(points, at_start)
    if found is None:
        return []
    (ex, ey), (ux, uy) = found
    nx, ny = -uy, ux
    half = 6.0
    items: List[Item] = []
    for distance, bulge in ((18.0, -1.0), (11.0, 1.0)):
        cx, cy = ex - ux * distance, ey - uy * distance
        pts: List[Point] = []
        if soft:
            for k in range(-4, 5):
                t = k / 4.0
                off = bulge * (1.0 - t * t) * 2.5
                pts.append((cx + nx * t * half + ux * off, cy + ny * t * half + uy * off))
        else:
            off = bulge * 2.5
            for t, o in ((-1.0, 0.0), (-1.0, off), (1.0, off), (1.0, 0.0)):
                pts.append((cx + nx * t * half + ux * o, cy + ny * t * half + uy * o))
        items.append(Line(pts, color, 0.9))
    return items


def _arrow(arrow: Arrow) -> List[Item]:
    """The line of one segment, its head if it has one, and its tunnel brackets."""
    if not arrow.has_route:
        return []
    stub = arrow.geometry == "stub"
    color = arrow.color or (STUB_COLOR if stub else "#000000")
    dash: Dash = (5.0, 3.0) if stub else arrow.dash
    items: List[Item] = [Line(list(arrow.points), color, max(arrow.width, MIN_LINE_WIDTH), dash)]
    # Ramus puts a head wherever the segment ends on something: a box side, the frame, or
    # nothing at all. Where it ends on a junction the flow goes on, so there is no head.
    if arrow.end.kind in ("activity", "frame", "open"):
        head = _head(arrow.points, color)
        if head is not None:
            items.append(head)
    for end, at_start in ((arrow.start, True), (arrow.end, False)):
        if end.tunnel:
            items.extend(_tunnel(arrow.points, at_start, end.tunnel == "soft", color))
    return items


def _label(arrow: Arrow, notation: str = "idef0") -> List[Item]:
    """The arrow's name, centred in the box the file gives it, and the zig-zag that ties it
    to the line. On a DFDS diagram the name is a card: framed, its lower right corner turned
    down, as Ramus draws it there."""
    lb = arrow.label
    if lb is None or not arrow.name or not arrow.has_route:
        return []
    color = arrow.color or "#000000"
    size = arrow.font_size
    # Ramus lays text out with its own font; allow some slack before wrapping a line the
    # box was sized for.
    lines = wrap(arrow.name, max(lb.width * 1.2, size * 2.0), size)
    line_h = size
    items: List[Item] = []
    if notation == "dfds":
        x0, y0, x1, y1 = lb.x, lb.y, lb.x + lb.width + 0.5, lb.y + lb.height
        fold = [(x1, y1 - 2.0), (x1, y0), (x0, y0), (x0, y1), (x1 - 4.0, y1), (x1, y1 - 2.0)]
        items.append(Poly(fold, "#ffffff"))
        items.append(Line(fold, color, 0.6))
    elif not lb.transparent:
        items.append(Rect(lb.x, lb.y, lb.width, lb.height, "#ffffff"))
    top = lb.y + lb.height / 2.0 - len(lines) * line_h / 2.0
    for i, line in enumerate(lines):
        items.append(
            Text(lb.x + lb.width / 2.0, top + i * line_h + BASELINE * size, line, size, color,
                 bold=arrow.bold)
        )
    if lb.tilde_pos is not None:
        items.extend(_tilde(arrow, lb, color))
    return items


def _tilde(arrow: Arrow, lb, color: str) -> List[Item]:
    """A dot on the line, and a short zig-zag from it to the nearest point of the label."""
    tx, ty = _point_at(arrow.points, lb.tilde_pos)
    px = min(max(tx, lb.x), lb.x + lb.width)
    py = min(max(ty, lb.y), lb.y + lb.height)
    items: List[Item] = [Dot(tx, ty, max(arrow.width * 0.75, 1.0), color)]
    u = _unit((tx, ty), (px, py))
    if u is None:
        return items
    bx, by = (tx + px) / 2.0, (ty + py) / 2.0
    kx, ky = u[1] * 3.0, -u[0] * 3.0
    items.append(Line([(tx, ty), (bx + kx, by + ky), (bx - kx, by - ky), (px, py)], color, 0.8))
    return items


def _free_text(t: FreeText) -> List[Item]:
    size = t.font_size
    line_h = size * 1.15
    lines = wrap(t.text, max(t.width, size * 2.0), size)
    return [
        Text(t.x + 2.0, t.y + i * line_h + BASELINE * size + 1.0, line, size, t.color, "start")
        for i, line in enumerate(lines)
    ]


# --------------------------------------------------------------------------------- the scene


def heading_of(diagram: Diagram) -> str:
    if diagram.node and diagram.parent_name:
        return f"{diagram.node}: {diagram.parent_name}"
    return diagram.node or diagram.parent_name or "Diagram"


def build_scene(diagram: Diagram, title: str = "", form: Optional[Form] = None) -> Scene:
    """Everything on one diagram, ready for a backend to draw.

    The canvas is the whole page - the frame the model's external arrows attach to - plus a
    strip above it for the heading, grown if anything (a long label, a stray text) falls
    outside. With a ``form`` the page is framed by the IDEF0 diagram form instead: its header
    above, its footer below, as on a printed sheet.
    """
    left, top, right, bottom = diagram.frame
    if form is not None:
        items: List[Item] = _form(form, right + PAGE_MARGIN, bottom + PAGE_MARGIN)
        for a in diagram.activities:
            items.extend(_box(a, diagram.notation))
        for arrow in diagram.arrows:
            items.extend(_arrow(arrow))
        for arrow in diagram.arrows:
            items.extend(_label(arrow, diagram.notation))
        for t in diagram.texts:
            items.extend(_free_text(t))
        return Scene(0.0, -FORM_HEADER - 1.0, right + PAGE_MARGIN + 1.0,
                     bottom + PAGE_MARGIN + FORM_FOOTER + FORM_HEADER + 2.0, items)
    items = [Rect(left, top, right - left, bottom - top, None, FRAME_COLOR, 0.8, (4.0, 3.0))]
    for a in diagram.activities:
        items.extend(_box(a, diagram.notation))
    for arrow in diagram.arrows:
        items.extend(_arrow(arrow))
    for arrow in diagram.arrows:
        items.extend(_label(arrow, diagram.notation))
    for t in diagram.texts:
        items.extend(_free_text(t))
    items.append(Text(left, -9.0, title or heading_of(diagram), HEADING_SIZE, HEADING_COLOR, "start"))

    min_x, min_y = 0.0, -HEADER_HEIGHT
    max_x, max_y = right + PAGE_MARGIN, bottom + PAGE_MARGIN
    for item in items:
        x0, y0, x1, y1 = _extent(item)
        min_x, min_y = min(min_x, x0 - 4.0), min(min_y, y0 - 4.0)
        max_x, max_y = max(max_x, x1 + 4.0), max(max_y, y1 + 4.0)
    return Scene(min_x, min_y, max_x - min_x, max_y - min_y, items)


def _extent(item: Item) -> Tuple[float, float, float, float]:
    if isinstance(item, Rect):
        return item.x, item.y, item.x + item.w, item.y + item.h
    if isinstance(item, (Line, Poly)):
        xs = [p[0] for p in item.points]
        ys = [p[1] for p in item.points]
        return min(xs), min(ys), max(xs), max(ys)
    if isinstance(item, Dot):
        return item.x - item.r, item.y - item.r, item.x + item.r, item.y + item.r
    w = text_width(item.text, item.size)
    if item.anchor == "middle":
        x0 = item.x - w / 2.0
    elif item.anchor == "end":
        x0 = item.x - w
    else:
        x0 = item.x
    return x0, item.y - item.size, x0 + w, item.y + item.size * 0.25
