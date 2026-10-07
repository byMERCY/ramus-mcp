"""How well a sheet is drawn: a measure of its layout, and the faults behind the number.

The IDEF0 rules (:mod:`idef0_rules`) say whether a model is *right*; this says whether a sheet
*reads well* - the things a person who draws IDEF0 by hand avoids and a reviewer marks:

* arrows that cross, or run on top of or tight beside one another;
* bends, and routes that wander further than their two ends need (an S-loop, a long way round);
* an arrow end hugging a corner of its box, or sitting on the box's number; two ends crowded
  together on one side;
* feedback drawn against the convention - into a control "up and over", into an input or a
  mechanism "down and under" (FIPS 183);
* a name far from its arrow, nearer another arrow than its own, or lying on a box, another name
  or a line;
* a route through a box, boxes on top of one another, boxes off the diagonal - IDEF0 sets a
  sheet's boxes out from top left to bottom right in their numbered order.

Each fault carries penalty points; the sheet's score is their sum plus a little for every bend
and every unit of detour. Lower is better, and 0 is out of reach for any real sheet - the score
is for comparing two layouts of the same sheet (is a tidied sheet really tidier?), and its
faults are what to look at.

Pure geometry over plain data, so it can measure a sheet as the reader sees it
(:func:`assess_diagram`) or one being laid out, before anything is written
(:class:`SheetGeometry`). Model units throughout.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

try:
    from .scene import NUMBER_SIZE, text_width
except ImportError:  # pragma: no cover - script execution
    from scene import NUMBER_SIZE, text_width

Point = Tuple[float, float]
Rect = Tuple[float, float, float, float]  # x, y, width, height

# Penalty points for each fault, and for each bend and unit of detour.
WEIGHTS: Dict[str, float] = {
    "crossing": 10.0,
    "overlap": 40.0,  # running on top of another arrow
    "close": 4.0,  # running tight beside one
    "corner_end": 8.0,
    "number_end": 12.0,
    "crowded_ends": 8.0,
    "detour": 6.0,  # a route going a long way round, on top of DETOUR_PER_UNIT
    "feedback_side": 15.0,
    "label_far": 10.0,
    "label_ambiguous": 6.0,
    "label_on_box": 20.0,
    "label_on_label": 15.0,
    "label_on_line": 4.0,
    "label_off_sheet": 10.0,
    "through_box": 40.0,
    "box_overlap": 50.0,
    "off_diagonal": 8.0,  # a box not right of and below the one numbered before it
}
BEND = 2.0
DETOUR_PER_UNIT = 0.15

ON_TOP = 2.0  # two parallel pieces closer than this are drawn on top of one another
CLOSE = 6.0  # ... closer than this, tight beside each other
CLOSE_RUN = 10.0  # ... for at least this long
STUB = 8.0  # the least a route runs straight out of a box or the frame (the head is 8 long)
DETOUR_NOTED = 40.0  # a detour longer than this is a fault of its own
END_SPACING = 10.0  # two ends on one side nearer than this are crowded
LABEL_NEAR = 24.0  # a name further than this from its route has lost touch with it ...
TILDE_NEAR = 64.0  # ... unless a zig-zag ties it back

# What to do about each fault, for whoever reads the report.
FIXES: Dict[str, str] = {
    "crossing": "tidy_sheet",
    "overlap": "tidy_sheet",
    "close": "tidy_sheet",
    "corner_end": "tidy_sheet",
    "number_end": "tidy_sheet",
    "crowded_ends": "tidy_sheet",
    "detour": "tidy_sheet",
    "feedback_side": "tidy_sheet",
    "label_far": "tidy_labels",
    "label_ambiguous": "tidy_labels",
    "label_on_box": "tidy_labels",
    "label_on_label": "tidy_labels",
    "label_on_line": "tidy_labels",
    "label_off_sheet": "tidy_labels",
    "through_box": "tidy_sheet",
    "box_overlap": "layout_sheet",
    "off_diagonal": "layout_sheet",
}


@dataclass(frozen=True)
class EndInfo:
    """What one end of a segment is on: kind "activity", "frame", "junction" or "open"; the
    box, the side ("right", "bottom", "left", "top") and its ICOM role where there is one."""

    kind: str
    activity_id: Optional[int] = None
    side: Optional[str] = None
    role: Optional[str] = None


@dataclass
class ArrowGeom:
    """One segment as drawn: its route, its ends, its name's rectangle."""

    sector: int
    flow: int  # segments of one flow (a fork, a join) share it and may touch
    points: List[Point]
    start: EndInfo
    end: EndInfo
    label: Optional[Rect] = None
    tilde: bool = False
    name: str = ""


@dataclass
class Fault:
    kind: str
    points: float  # what it costs
    sectors: Tuple[int, ...] = ()
    activities: Tuple[int, ...] = ()
    at: Optional[Point] = None
    detail: str = ""


@dataclass
class Quality:
    score: float
    bends: int
    detour: float
    faults: List[Fault] = field(default_factory=list)

    def counts(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for f in self.faults:
            out[f.kind] = out.get(f.kind, 0) + 1
        return dict(sorted(out.items()))


# ------------------------------------------------------------------------------- geometry


def _pieces(points: Sequence[Point]) -> List[Tuple[Point, Point]]:
    return [(p, q) for p, q in zip(points, points[1:]) if p != q]


def _horizontal(p: Point, q: Point) -> bool:
    return abs(p[1] - q[1]) < 1e-6


def _vertical(p: Point, q: Point) -> bool:
    return abs(p[0] - q[0]) < 1e-6


def _direction(p: Point, q: Point) -> Point:
    dx, dy = q[0] - p[0], q[1] - p[1]
    if abs(dx) >= abs(dy):
        return (1.0 if dx > 0 else -1.0, 0.0)
    return (0.0, 1.0 if dy > 0 else -1.0)


def _crossing(a: Tuple[Point, Point], b: Tuple[Point, Point]) -> Optional[Point]:
    """Where a horizontal and a vertical piece cross, strictly inside both."""
    if _horizontal(*a) and _vertical(*b):
        h, v = a, b
    elif _vertical(*a) and _horizontal(*b):
        h, v = b, a
    else:
        return None
    y, x = h[0][1], v[0][0]
    hx0, hx1 = sorted((h[0][0], h[1][0]))
    vy0, vy1 = sorted((v[0][1], v[1][1]))
    if hx0 + 0.5 < x < hx1 - 0.5 and vy0 + 0.5 < y < vy1 - 0.5:
        return (x, y)
    return None


def _parallel(a: Tuple[Point, Point], b: Tuple[Point, Point]) -> Optional[Tuple[float, float]]:
    """(gap, run) of two parallel pieces that run side by side, else None."""
    if _horizontal(*a) and _horizontal(*b):
        gap = abs(a[0][1] - b[0][1])
        lo = max(min(a[0][0], a[1][0]), min(b[0][0], b[1][0]))
        hi = min(max(a[0][0], a[1][0]), max(b[0][0], b[1][0]))
    elif _vertical(*a) and _vertical(*b):
        gap = abs(a[0][0] - b[0][0])
        lo = max(min(a[0][1], a[1][1]), min(b[0][1], b[1][1]))
        hi = min(max(a[0][1], a[1][1]), max(b[0][1], b[1][1]))
    else:
        return None
    return (gap, hi - lo) if hi - lo > 0 else None


def _seg_rect(p: Point, q: Point) -> Rect:
    return (min(p[0], q[0]), min(p[1], q[1]), abs(p[0] - q[0]), abs(p[1] - q[1]))


def _overlap(a: Rect, b: Rect, gap: float = 0.0) -> bool:
    return (a[0] < b[0] + b[2] + gap and b[0] < a[0] + a[2] + gap
            and a[1] < b[1] + b[3] + gap and b[1] < a[1] + a[3] + gap)


def _through(p: Point, q: Point, r: Rect) -> bool:
    """Does an axis-parallel piece pass through the inside of r (a little in from its sides)?"""
    x, y, w, h = r[0] + 1.0, r[1] + 1.0, r[2] - 2.0, r[3] - 2.0
    if w <= 0 or h <= 0:
        return False
    if _horizontal(p, q):
        x0, x1 = sorted((p[0], q[0]))
        return y < p[1] < y + h and max(x0, x) < min(x1, x + w)
    if _vertical(p, q):
        y0, y1 = sorted((p[1], q[1]))
        return x < p[0] < x + w and max(y0, y) < min(y1, y + h)
    return False


def distance_to_route(box: Rect, points: Sequence[Point]) -> float:
    """How far a rectangle is from the nearest piece of a route (0 if they touch)."""
    x, y, w, h = box
    best = float("inf")
    for p, q in zip(points, points[1:]):
        sx, sy, sw, sh = _seg_rect(p, q)
        dx = max(0.0, sx - (x + w), x - (sx + sw))
        dy = max(0.0, sy - (y + h), y - (sy + sh))
        best = min(best, math.hypot(dx, dy))
    return best


def least_length(start: Point, start_dir: Point, end: Point, end_dir: Point,
                 start_stub: float = STUB, end_stub: float = STUB) -> float:
    """The shortest an orthogonal route can be that leaves ``start`` along ``start_dir`` for
    at least ``start_stub`` and arrives at ``end`` along ``end_dir`` after at least
    ``end_stub`` - with nothing in the way. Along each axis the route has to cover the
    forced runs and the rest of the distance; whatever it does beyond that is detour."""
    total = 0.0
    for axis in (0, 1):
        # Positions along this axis the route must pass through, in order.
        marks = [start[axis]]
        if start_dir[axis] != 0:
            marks.append(start[axis] + start_dir[axis] * start_stub)
        if end_dir[axis] != 0:
            marks.append(end[axis] - end_dir[axis] * end_stub)
        marks.append(end[axis])
        total += sum(abs(b - a) for a, b in zip(marks, marks[1:]))
    return total


def route_length(points: Sequence[Point]) -> float:
    return sum(abs(q[0] - p[0]) + abs(q[1] - p[1]) for p, q in zip(points, points[1:]))


def corner_zone(length: float) -> float:
    """How close to a corner of a side of this length an end may come before it hugs it."""
    return min(20.0, max(8.0, 0.2 * length))


# ------------------------------------------------------------------------------ the sheet


@dataclass
class BoxGeom:
    id: int
    rect: Rect
    number: str = ""


class SheetGeometry:
    """A sheet's boxes, segments and frame, to be measured. Change ``arrows`` (a dict by
    segment id) and measure again; :meth:`contribution` prices just what one segment adds."""

    def __init__(self, boxes: Iterable[BoxGeom], arrows: Iterable[ArrowGeom], frame: Rect,
                 texts: Iterable[Rect] = (), notation: str = "idef0"):
        self.boxes: Dict[int, BoxGeom] = {b.id: b for b in boxes}
        self.arrows: Dict[int, ArrowGeom] = {a.sector: a for a in arrows}
        self.frame = frame
        self.texts = list(texts)
        # IDEF0's conventions - feedback round the outside, boxes down the diagonal - are not
        # a data flow diagram's.
        self.idef0 = notation == "idef0"

    # ---- the measure

    def assess(self) -> Quality:
        faults: List[Fault] = []
        bends, detour = 0, 0.0
        arrows = [a for a in self.arrows.values() if len(a.points) >= 2]
        for a in arrows:
            b, d, own = self._own_faults(a)
            bends += b
            detour += d
            faults.extend(own)
        for i, a in enumerate(arrows):
            for b in arrows[i + 1:]:
                faults.extend(self._pair_faults(a, b))
        for a in arrows:
            faults.extend(self._label_faults(a, arrows))
        faults.extend(self._box_faults())
        score = sum(f.points for f in faults) + BEND * bends + DETOUR_PER_UNIT * detour
        return Quality(round(score, 2), bends, round(detour, 2), faults)

    def contribution(self, sector: int, own_label: bool = True) -> float:
        """The points the sheet's score owes to one segment: its own faults, bends and detour,
        everything between it and another segment, its name's faults and other names lying on
        its line. Comparing this before and after a change to that segment alone says whether
        the sheet got better, without measuring all of it. Without ``own_label`` its own name
        is left out - for a route about to be redrawn, whose name will be put beside it after."""
        a = self.arrows.get(sector)
        if a is None or len(a.points) < 2:
            return 0.0
        bends, detour, faults = self._own_faults(a)
        others = [b for b in self.arrows.values() if b.sector != sector and len(b.points) >= 2]
        for b in others:
            faults.extend(self._pair_faults(a, b))
        everyone = others + [a]
        if own_label:
            faults.extend(self._label_faults(a, everyone))
        for b in others:  # their names, measured against this line only
            faults.extend(f for f in self._label_faults(b, [b, a]) if sector in f.sectors[1:])
        return sum(f.points for f in faults) + BEND * bends + DETOUR_PER_UNIT * detour

    # ---- faults of one segment

    def _own_faults(self, a: ArrowGeom) -> Tuple[int, float, List[Fault]]:
        faults: List[Fault] = []
        pts = _simplified(a.points)
        bends = max(0, len(pts) - 2)
        pieces = _pieces(pts)
        detour = 0.0
        if pieces:
            sdir, edir = _direction(*pieces[0]), _direction(*pieces[-1])
            least = least_length(pts[0], sdir, pts[-1], edir,
                                 STUB if a.start.kind in ("activity", "frame") else 0.0,
                                 STUB if a.end.kind in ("activity", "frame") else 0.0)
            detour = max(0.0, route_length(pts) - least)
            if detour > DETOUR_NOTED:
                faults.append(Fault("detour", WEIGHTS["detour"], (a.sector,), at=pts[0],
                                    detail=f"{round(detour)} longer than it needs to be"))
        for end, p in ((a.start, pts[0]), (a.end, pts[-1])):
            box = self.boxes.get(end.activity_id) if end.kind == "activity" else None
            if box is not None and end.side:
                kind = _end_place(box, end.side, p)
                if kind:
                    faults.append(Fault(kind, WEIGHTS[kind], (a.sector,), (box.id,), p,
                                        f"{end.side} side of {box.number or box.id}"))
        for box in self.boxes.values():
            if any(_through(p, q, box.rect) for p, q in pieces):
                faults.append(Fault("through_box", WEIGHTS["through_box"], (a.sector,),
                                    (box.id,), detail=f"through {box.number or box.id}"))
        fault = self._feedback(a, pieces)
        if fault is not None:
            faults.append(fault)
        return bends, detour, faults

    def _feedback(self, a: ArrowGeom, pieces) -> Optional[Fault]:
        """An output fed back to a box further left should go over the top into a control and
        under the bottom into an input or a mechanism: its leftward run above, or below, the
        box it leaves."""
        if a.start.kind != "activity" or a.end.kind != "activity" or a.start.side != "right" \
                or not self.idef0:
            return None
        src, dst = self.boxes.get(a.start.activity_id), self.boxes.get(a.end.activity_id)
        if src is None or dst is None or a.end.side not in ("top", "left", "bottom"):
            return None
        sx, sy, sw, sh = src.rect
        dx, _, dw, _ = dst.rect
        if dx + dw / 2 >= sx + sw / 2:
            return None  # not back to the left: no feedback
        over = a.end.side == "top"
        for p, q in pieces:
            if _horizontal(p, q) and q[0] < p[0] - 0.5:
                wrong = p[1] > sy + 0.5 if over else p[1] < sy + sh - 0.5
                if wrong:
                    return Fault("feedback_side", WEIGHTS["feedback_side"], (a.sector,),
                                 (src.id, dst.id), p,
                                 "feedback into a control goes over the top" if over else
                                 "feedback into an input or a mechanism goes under the bottom")
        return None

    # ---- faults between two segments

    def _pair_faults(self, a: ArrowGeom, b: ArrowGeom) -> List[Fault]:
        if a.flow == b.flow:
            return []  # a fork or a join: one flow's pieces meet by design
        faults: List[Fault] = []
        pa, pb = _pieces(a.points), _pieces(b.points)
        for p in pa:
            for q in pb:
                at = _crossing(p, q)
                if at is not None:
                    faults.append(Fault("crossing", WEIGHTS["crossing"], (a.sector, b.sector),
                                        at=at))
                    continue
                side = _parallel(p, q)
                if side is None:
                    continue
                gap, run = side
                if gap < ON_TOP:
                    faults.append(Fault("overlap", WEIGHTS["overlap"], (a.sector, b.sector),
                                        at=p[0], detail=f"{round(run)} long"))
                elif gap < CLOSE and run >= CLOSE_RUN:
                    faults.append(Fault("close", WEIGHTS["close"], (a.sector, b.sector),
                                        at=p[0], detail=f"{round(gap, 1)} apart"))
        for ea, xa in ((a.start, a.points[0]), (a.end, a.points[-1])):
            for eb, xb in ((b.start, b.points[0]), (b.end, b.points[-1])):
                if _same_side(ea, eb) and math.dist(xa, xb) < END_SPACING:
                    faults.append(Fault("crowded_ends", WEIGHTS["crowded_ends"],
                                        (a.sector, b.sector), at=xa,
                                        detail=f"{round(math.dist(xa, xb), 1)} apart"))
        return faults

    # ---- faults of a name

    def _label_faults(self, a: ArrowGeom, arrows: Sequence[ArrowGeom]) -> List[Fault]:
        """The faults of ``a``'s name among ``arrows``. A fault that another segment's line or
        name takes part in lists that segment second."""
        r = a.label
        if r is None or r[2] <= 0 or r[3] <= 0 or len(a.points) < 2:
            return []
        faults: List[Fault] = []
        own = distance_to_route(r, a.points)
        if own > (TILDE_NEAR if a.tilde else LABEL_NEAR):
            faults.append(Fault("label_far", WEIGHTS["label_far"], (a.sector,),
                                detail=f"{round(own)} from its arrow"))
        fx, fy, fw, fh = self.frame
        if r[0] < fx or r[1] < fy or r[0] + r[2] > fx + fw or r[1] + r[3] > fy + fh:
            faults.append(Fault("label_off_sheet", WEIGHTS["label_off_sheet"], (a.sector,)))
        for box in self.boxes.values():
            if _overlap(r, box.rect, -0.5):
                faults.append(Fault("label_on_box", WEIGHTS["label_on_box"], (a.sector,),
                                    (box.id,)))
        for t in self.texts:
            if _overlap(r, t, -0.5):
                faults.append(Fault("label_on_label", WEIGHTS["label_on_label"], (a.sector,)))
        nearest_other = float("inf")
        for b in arrows:
            if b is a:
                continue
            if b.label is not None and b.label[2] > 0 and b.label[3] > 0 \
                    and _overlap(r, b.label, -0.5) and a.sector < b.sector:
                faults.append(Fault("label_on_label", WEIGHTS["label_on_label"],
                                    (a.sector, b.sector)))
            if b.flow == a.flow:
                continue
            for p, q in _pieces(b.points):
                if _overlap(r, _seg_rect(p, q), -0.5):
                    faults.append(Fault("label_on_line", WEIGHTS["label_on_line"],
                                        (a.sector, b.sector)))
                    break
            if not a.tilde:
                nearest_other = min(nearest_other, distance_to_route(r, b.points))
        if nearest_other < own - 1.0:
            faults.append(Fault("label_ambiguous", WEIGHTS["label_ambiguous"], (a.sector,),
                                detail="nearer another arrow than its own"))
        return faults

    # ---- faults of the boxes

    def _box_faults(self) -> List[Fault]:
        """Boxes on top of each other, and boxes off the diagonal: each should stand right of
        and below the one before it (``boxes`` is in the sheet's order)."""
        boxes = list(self.boxes.values())
        faults = [Fault("box_overlap", WEIGHTS["box_overlap"], (), (a.id, b.id))
                  for i, a in enumerate(boxes) for b in boxes[i + 1:]
                  if _overlap(a.rect, b.rect)]
        for a, b in zip(boxes, boxes[1:]) if self.idef0 else ():
            ax, ay = a.rect[0] + a.rect[2] / 2, a.rect[1] + a.rect[3] / 2
            bx, by = b.rect[0] + b.rect[2] / 2, b.rect[1] + b.rect[3] / 2
            if bx <= ax or by <= ay:
                faults.append(Fault("off_diagonal", WEIGHTS["off_diagonal"], (), (a.id, b.id),
                                    detail=f"{b.number or b.id} is not right of and below "
                                           f"{a.number or a.id}"))
        return faults


def _simplified(points: Sequence[Point]) -> List[Point]:
    out: List[Point] = []
    for p in points:
        if out and math.dist(p, out[-1]) < 1e-6:
            continue
        if len(out) >= 2:
            a, b = out[-2], out[-1]
            if (abs(a[0] - b[0]) < 1e-6 and abs(b[0] - p[0]) < 1e-6) or \
                    (abs(a[1] - b[1]) < 1e-6 and abs(b[1] - p[1]) < 1e-6):
                out[-1] = p
                continue
        out.append(p)
    return out


def _same_side(a: EndInfo, b: EndInfo) -> bool:
    if a.kind != b.kind or a.side is None or a.side != b.side:
        return False
    if a.kind == "activity":
        return a.activity_id == b.activity_id
    return a.kind == "frame"


def _end_place(box: BoxGeom, side: str, p: Point) -> Optional[str]:
    """"number_end" if an end sits where the box's number is written, "corner_end" if it hugs
    a corner, else None."""
    x, y, w, h = box.rect
    if side in ("left", "right"):
        along, lo, length = p[1], y, h
    else:
        along, lo, length = p[0], x, w
    if box.number:
        nw = text_width(box.number, NUMBER_SIZE) + 4.0
        if side == "right" and along > y + h - NUMBER_SIZE - 4.0:
            return "number_end"
        if side == "bottom" and along > x + w - nw - 3.0:
            return "number_end"
    zone = corner_zone(length)
    if min(along - lo, lo + length - along) < zone - 0.5:
        return "corner_end"
    return None


# --------------------------------------------------------------------- from the reader


def end_info(end) -> EndInfo:
    """The reader's :class:`~ramus_rsf.End` as an EndInfo."""
    return EndInfo(end.kind, end.activity_id, end.side, end.role)


def geometry_of(diagram) -> SheetGeometry:
    """A sheet as the reader gives it (:class:`~ramus_rsf.Diagram`), ready to measure."""
    left, top, right, bottom = diagram.frame
    boxes = [BoxGeom(a.element_id, (a.x, a.y, a.width, a.height), a.number)
             for a in diagram.activities if a.has_box]
    arrows = []
    for a in diagram.arrows:
        if not a.has_route or a.geometry == "stub":
            continue
        label = None
        if a.label is not None and a.name:
            label = (a.label.x, a.label.y, a.label.width, a.label.height)
        arrows.append(ArrowGeom(a.sector_id, a.flow, list(a.points), end_info(a.start),
                                end_info(a.end), label,
                                a.label is not None and a.label.tilde_pos is not None, a.name))
    texts = [(t.x, t.y, t.width, t.height) for t in diagram.texts]
    return SheetGeometry(boxes, arrows, (left, top, right - left, bottom - top), texts,
                         getattr(diagram, "notation", "idef0"))


def assess_diagram(diagram) -> Quality:
    return geometry_of(diagram).assess()


def report(diagram, limit: int = 30) -> Dict[str, object]:
    """The measure of a sheet as a tool answers it: the score, the faults counted by kind, and
    the worst of them with the segments and boxes they concern and the call that mends them."""
    q = assess_diagram(diagram)
    names = {a.sector_id: a.name for a in diagram.arrows}
    numbers = {a.element_id: a.number for a in diagram.activities}
    worst = sorted(q.faults, key=lambda f: -f.points)[:limit]
    return {
        "sheet": diagram.node,
        "score": q.score,
        "bends": q.bends,
        "detour": q.detour,
        "faults": q.counts(),
        "worst": [{
            "kind": f.kind, "points": f.points,
            **({"segments": [{"segment": s, "name": names.get(s, "")} for s in f.sectors]}
               if f.sectors else {}),
            **({"activities": [numbers.get(i, i) for i in f.activities]}
               if f.activities else {}),
            **({"at": [round(f.at[0], 1), round(f.at[1], 1)]} if f.at else {}),
            **({"detail": f.detail} if f.detail else {}),
            "fix": FIXES[f.kind],
        } for f in worst],
    }
