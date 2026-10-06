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


Item = Union[Rect, Line, Poly, Dot, Text]


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


def _box(a: Activity) -> List[Item]:
    ink = a.color or INK
    items: List[Item] = [Rect(a.x, a.y, a.width, a.height, a.fill or "#ffffff", ink, BOX_STROKE)]
    size = a.font_size
    line_h = size * 1.2
    lines = wrap(a.name, max(a.width - 10.0, size), size)
    # Keep the strip along the bottom for the node number, so a long name does not run
    # into it; the name is centred in what is left.
    reserved = NUMBER_SIZE if a.number else 0.0
    top = a.y + (a.height - reserved - len(lines) * line_h) / 2.0 + 1.0
    for i, line in enumerate(lines):
        items.append(
            Text(a.x + a.width / 2.0, top + i * line_h + BASELINE * size + 0.1 * size,
                 line, size, ink)
        )
    if a.number:
        items.append(
            Text(a.x + a.width - 3.0, a.y + a.height - 3.0, a.number, NUMBER_SIZE, NUMBER_COLOR, "end")
        )
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


def _label(arrow: Arrow) -> List[Item]:
    """The arrow's name, centred in the box the file gives it, and the zig-zag that ties it
    to the line."""
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
    if not lb.transparent:
        items.append(Rect(lb.x, lb.y, lb.width, lb.height, "#ffffff"))
    top = lb.y + lb.height / 2.0 - len(lines) * line_h / 2.0
    for i, line in enumerate(lines):
        items.append(
            Text(lb.x + lb.width / 2.0, top + i * line_h + BASELINE * size, line, size, color)
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


def build_scene(diagram: Diagram, title: str = "") -> Scene:
    """Everything on one diagram, ready for a backend to draw.

    The canvas is the whole page - the frame the model's external arrows attach to - plus a
    strip above it for the heading, grown if anything (a long label, a stray text) falls
    outside.
    """
    left, top, right, bottom = diagram.frame
    items: List[Item] = [Rect(left, top, right - left, bottom - top, None, FRAME_COLOR, 0.8, (4.0, 3.0))]
    for a in diagram.activities:
        items.extend(_box(a))
    for arrow in diagram.arrows:
        items.extend(_arrow(arrow))
    for arrow in diagram.arrows:
        items.extend(_label(arrow))
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
