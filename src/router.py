"""Orthogonal arrow routing for IDEF0 sheets.

An IDEF0 arrow runs in horizontal and vertical pieces, leaves a box from the side its role
says (outputs right) and enters another the same way (inputs from the left, controls from the
top, mechanisms from below). This finds such a route between two points on a sheet that keeps
clear of the boxes, bends as little as it can, and avoids crossing or running along the arrows
already there - the things that make a diagram hard to read.

The method is the standard one for this: an orthogonal visibility graph. Candidate horizontal
and vertical lines are taken along the edges of every box (kept a margin away), through the
two end points and halfway between them, and halfway between neighbouring arrows already
there; their crossings are the graph's nodes, joined where a straight piece passes no box. The
cheapest path through it - length, plus a price for every bend, every crossing of an existing
arrow and every stretch drawn on top of or close beside one - is the route, found by A* over
only the part of the graph it needs.

Pure geometry: nothing here knows about files.
"""

from __future__ import annotations

import bisect
import heapq
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple, Union

Point = Tuple[float, float]
Rect = Tuple[float, float, float, float]  # x, y, width, height
Segment = Tuple[Point, Point]

MARGIN = 14.0  # how far a route keeps from a box; the usual length of the first and last pieces
MIN_STUB = 8.0  # the first and last pieces are never shorter (the head is 8 long)
BEND = 40.0  # a bend costs as much as this much extra length
OFF_CENTRE = 0.05  # per unit a turn sits away from halfway between the ends - a tie-breaker
CROSSING = 30.0  # crossing an existing arrow
OVERLAP = 90.0  # running along one: this much for each piece that does ...
OVERLAP_PER_UNIT = 3.0  # ... and this much per unit of length drawn on top of it
THROUGH_LABEL = 45.0  # running through another arrow's name
NEAR = 16.0  # closer than this alongside another arrow reads as crowding, priced per unit of
#              length from OVERLAP_PER_UNIT (as good as on top of it) down to nothing at NEAR
ON_TOP = 2.0  # closer than this is drawn on top of it

RIGHT, DOWN, LEFT, UP = (1.0, 0.0), (0.0, 1.0), (-1.0, 0.0), (0.0, -1.0)

# The side of a box an end attaches to, by Ramus's numbering, and the way a route moves there.
SIDE_RIGHT, SIDE_BOTTOM, SIDE_LEFT, SIDE_TOP = 0, 1, 2, 3
OUTWARD = {SIDE_RIGHT: RIGHT, SIDE_BOTTOM: DOWN, SIDE_LEFT: LEFT, SIDE_TOP: UP}


def _blocked(rect: Rect, clearance: float) -> Rect:
    x, y, w, h = rect
    return (x - clearance, y - clearance, w + 2 * clearance, h + 2 * clearance)


def _crosses_rect(a: Point, b: Point, r: Rect) -> bool:
    """Does the straight axis-parallel piece a-b pass through the inside of r?"""
    rx, ry, rw, rh = r
    if a[1] == b[1]:  # horizontal
        y = a[1]
        x0, x1 = sorted((a[0], b[0]))
        return ry < y < ry + rh and max(x0, rx) < min(x1, rx + rw)
    x = a[0]
    y0, y1 = sorted((a[1], b[1]))
    return rx < x < rx + rw and max(y0, ry) < min(y1, ry + rh)


def _inside(p: Point, r: Rect) -> bool:
    rx, ry, rw, rh = r
    return rx < p[0] < rx + rw and ry < p[1] < ry + rh


def _alongside(gap: float, run: float) -> float:
    """What running ``run`` units beside another arrow ``gap`` away costs: on top of it, a
    price for doing it at all and more per unit; near it, less the further off."""
    if gap < ON_TOP:
        return OVERLAP + OVERLAP_PER_UNIT * run
    return OVERLAP_PER_UNIT * (NEAR - gap) / (NEAR - ON_TOP) * run


def _line_cost(a: Point, b: Point, lines: Sequence[Segment]) -> float:
    """What drawing a-b costs in crossings of, and overlaps with, the arrows already there."""
    cost = 0.0
    horizontal = a[1] == b[1]
    for p, q in lines:
        if p == q:
            continue
        if horizontal:
            x0, x1 = sorted((a[0], b[0]))
            y = a[1]
            if p[0] == q[0]:  # a vertical line: crossing?
                y0, y1 = sorted((p[1], q[1]))
                if x0 < p[0] < x1 and y0 < y < y1:
                    cost += CROSSING
            elif abs(p[1] - y) < NEAR:  # a horizontal one on, or close to, the same line
                run = min(x1, max(p[0], q[0])) - max(x0, min(p[0], q[0]))
                if run > 0:
                    cost += _alongside(abs(p[1] - y), run)
        else:
            y0, y1 = sorted((a[1], b[1]))
            x = a[0]
            if p[1] == q[1]:
                x0, x1 = sorted((p[0], q[0]))
                if y0 < p[1] < y1 and x0 < x < x1:
                    cost += CROSSING
            elif abs(p[0] - x) < NEAR:
                run = min(y1, max(p[1], q[1])) - max(y0, min(p[1], q[1]))
                if run > 0:
                    cost += _alongside(abs(p[0] - x), run)
    return cost


def _label_cost(a: Point, b: Point, labels: Sequence[Rect]) -> float:
    """What drawing a-b costs in names it runs through."""
    return THROUGH_LABEL * sum(1 for r in labels if _crosses_rect(a, b, r))


class Costs:
    """What drawing a straight piece costs among the arrows (``lines``) and names (``labels``)
    already on a sheet - the same as _line_cost plus _label_cost, but with the lines sorted by
    position so a piece looks only at the ones it can meet, and each piece's price remembered:
    one plan routes many candidate pairs over the same sheet and asks about the same pieces
    again and again."""

    def __init__(self, lines: Sequence[Segment] = (), labels: Sequence[Rect] = ()):
        self.labels = list(labels)
        self._h = sorted((p[1], min(p[0], q[0]), max(p[0], q[0]))
                         for p, q in lines if p[1] == q[1] and p[0] != q[0])
        self._v = sorted((p[0], min(p[1], q[1]), max(p[1], q[1]))
                         for p, q in lines if p[0] == q[0] and p[1] != q[1])
        self._odd = [(p, q) for p, q in lines if p[0] != q[0] and p[1] != q[1]]
        self._hy = [h[0] for h in self._h]
        self._vx = [v[0] for v in self._v]
        self._memo: Dict[Tuple[Point, Point], float] = {}

    def levels(self) -> Tuple[List[float], List[float]]:
        """Where the arrows already run: the x of each vertical piece, the y of each
        horizontal one."""
        return self._vx, self._hy

    def piece(self, a: Point, b: Point) -> float:
        key = (a, b) if a <= b else (b, a)
        cost = self._memo.get(key)
        if cost is None:
            cost = self._lines(a, b) + _label_cost(a, b, self.labels)
            self._memo[key] = cost
        return cost

    def _lines(self, a: Point, b: Point) -> float:
        if a[1] == b[1]:
            fixed, lo, hi = a[1], min(a[0], b[0]), max(a[0], b[0])
            across, across_at, along, along_at = self._v, self._vx, self._h, self._hy
        elif a[0] == b[0]:
            fixed, lo, hi = a[0], min(a[1], b[1]), max(a[1], b[1])
            across, across_at, along, along_at = self._h, self._hy, self._v, self._vx
        else:
            return _line_cost(a, b, self._as_segments())
        cost = 0.0
        for i in range(bisect.bisect_right(across_at, lo), bisect.bisect_left(across_at, hi)):
            at, l0, l1 = across[i]
            if lo < at < hi and l0 < fixed < l1:
                cost += CROSSING
        # A little wider than NEAR either way, then the exact test, so rounding cannot drop one.
        for i in range(bisect.bisect_left(along_at, fixed - NEAR - 1.0),
                       bisect.bisect_right(along_at, fixed + NEAR + 1.0)):
            at, l0, l1 = along[i]
            gap = abs(at - fixed)
            if gap < NEAR:
                run = min(hi, l1) - max(lo, l0)
                if run > 0:
                    cost += _alongside(gap, run)
        if self._odd:
            cost += _line_cost(a, b, self._odd)
        return cost

    def _as_segments(self) -> List[Segment]:
        return ([((l0, at), (l1, at)) for at, l0, l1 in self._h]
                + [((at, l0), (at, l1)) for at, l0, l1 in self._v] + self._odd)


def _direction(a: Point, b: Point) -> Point:
    if a[1] == b[1]:
        return RIGHT if b[0] > a[0] else LEFT
    return DOWN if b[1] > a[1] else UP


def simplify(points: Iterable[Point]) -> List[Point]:
    """Drop repeated points and the middle of any three in a straight line."""
    out: List[Point] = []
    for p in points:
        p = (round(p[0], 6), round(p[1], 6))
        if out and p == out[-1]:
            continue
        if len(out) >= 2:
            a, b = out[-2], out[-1]
            if (a[0] == b[0] == p[0]) or (a[1] == b[1] == p[1]):
                out[-1] = p
                continue
        out.append(p)
    return out


def route(start: Point, start_dir: Point, end: Point, end_dir: Point,
          obstacles: Sequence[Rect], bounds: Rect, lines: Sequence[Segment] = (),
          margin: float = MARGIN, labels: Sequence[Rect] = (),
          costs: Optional[Costs] = None) -> List[Point]:
    """An orthogonal route from ``start`` to ``end``.

    ``start_dir`` is the way the route leaves ``start`` (out of the side of a box, or into the
    sheet from the frame); ``end_dir`` the way it moves as it arrives at ``end``. ``obstacles``
    are the boxes to keep clear of - the two the arrow joins included - and ``lines`` the
    arrows already drawn. ``bounds`` is the drawable area. ``costs``, if given, is a Costs
    over those same lines and labels, kept by a caller routing many times on one sheet.

    The first and last pieces run straight out of ``start`` and into ``end`` and may be any
    length from MIN_STUB up, so a route can turn in a gap between two boxes narrower than two
    margins; where nothing else decides it, the turn is made halfway between the ends.
    """
    clearance = margin * 0.75
    blocked = [_blocked(r, clearance) for r in obstacles]
    bx, by, bw, bh = bounds
    mid = ((start[0] + end[0]) / 2, (start[1] + end[1]) / 2)

    xs = {start[0], end[0], mid[0], start[0] + start_dir[0] * margin,
          end[0] - end_dir[0] * margin, bx + margin, bx + bw - margin}
    ys = {start[1], end[1], mid[1], start[1] + start_dir[1] * margin,
          end[1] - end_dir[1] * margin, by + margin, by + bh - margin}
    for r in obstacles:
        xs.update((r[0] - margin, r[0] + r[2] + margin))
        ys.update((r[1] - margin, r[1] + r[3] + margin))
        # ... and just outside the clearance, so a narrow gap can take two lines side by side
        xs.update((r[0] - clearance - 1, r[0] + r[2] + clearance + 1))
        ys.update((r[1] - clearance - 1, r[1] + r[3] + clearance + 1))
    for a in obstacles:  # the middle of each gap between two boxes: a corridor's centre line
        for b in obstacles:
            if a[0] + a[2] < b[0]:
                xs.add((a[0] + a[2] + b[0]) / 2)
            if a[1] + a[3] < b[1]:
                ys.add((a[1] + a[3] + b[1]) / 2)
    if costs is None:
        costs = Costs(lines, labels)
    line_xs, line_ys = costs.levels()
    xs |= _corridors(line_xs, [e for r in obstacles for e in (r[0], r[0] + r[2])], bx, bx + bw)
    ys |= _corridors(line_ys, [e for r in obstacles for e in (r[1], r[1] + r[3])], by, by + bh)
    xs = sorted(x for x in xs if bx <= x <= bx + bw)
    ys = sorted(y for y in ys if by <= y <= by + bh)

    # The graph's nodes are where the lines cross, outside every box's clearance; each joins
    # the next node along its row and its column unless a box is in between. Only the part the
    # search reaches is ever worked out. Each line meets only the boxes whose (grown) span it
    # runs through.
    row_spans = {y: [(r[0], r[0] + r[2]) for r in blocked if r[1] < y < r[1] + r[3]] for y in ys}
    col_spans = {x: [(r[1], r[1] + r[3]) for r in blocked if r[0] < x < r[0] + r[2]] for x in xs}
    xi = {x: i for i, x in enumerate(xs)}
    yi = {y: i for i, y in enumerate(ys)}
    ends = {start, end}  # an end on the frame is a free point too, but joins only by its ray

    def free(p: Point) -> bool:
        return not any(lo < p[0] < hi for lo, hi in row_spans[p[1]])

    def step(coords: List[float], k: int, way: int, spans, at) -> Optional[Point]:
        """The next node from coords[k] one way along a line, if no box is in between."""
        here = coords[k]
        k += way
        while 0 <= k < len(coords):
            p = at(coords[k])
            if p in ends:
                k += way
                continue
            if not free(p):
                return None
            lo_, hi_ = (here, coords[k]) if way > 0 else (coords[k], here)
            if any(max(lo_, lo) < min(hi_, hi) for lo, hi in spans):
                return None
            return p
        return None

    # The ends sit on a box (inside its clearance), so they join the graph only by straight
    # pieces along their own direction: out of the start, into the end. Such a piece may cross
    # the clearance of the box it starts or ends on, and no other.
    stub_cost: Dict[Tuple[Point, Point], float] = {}

    def ray(origin: Point, direction: Point, mid_along: float) -> List[Point]:
        others = [r for r in blocked if not _inside(origin, r)]
        if direction in (LEFT, RIGHT):
            line = [(x, origin[1]) for x in xs] if origin[1] in yi else []
        else:
            line = [(origin[0], y) for y in ys] if origin[0] in xi else []
        out = []
        for n in line:
            t = (n[0] - origin[0]) * direction[0] + (n[1] - origin[1]) * direction[1]
            if t >= MIN_STUB and n not in ends and free(n) and not any(
                    _crosses_rect(origin, n, r) for r in others):
                out.append(n)
                ideal = mid_along if mid_along >= MIN_STUB else margin
                stub_cost[(origin, n) if origin == start else (n, origin)] = \
                    OFF_CENTRE * abs(t - ideal)
        return out

    along_s = (mid[0] - start[0]) * start_dir[0] + (mid[1] - start[1]) * start_dir[1]
    along_e = (end[0] - mid[0]) * end_dir[0] + (end[1] - mid[1]) * end_dir[1]
    known: Dict[Point, List[Point]] = {start: ray(start, start_dir, along_s)}
    into_end = set(ray(end, (-end_dir[0], -end_dir[1]), along_e))
    straight = (start_dir == end_dir and _direction(start, end) == start_dir
                if start != end and (start[0] == end[0] or start[1] == end[1]) else False)
    if straight and not any(_crosses_rect(start, end, r) for r in blocked
                            if not _inside(start, r) and not _inside(end, r)):
        known[start].append(end)

    def neighbours(n: Point) -> List[Point]:
        out = known.get(n)
        if out is None:
            x, y = n
            row, col = (lambda c: (c, y)), (lambda c: (x, c))
            out = [p for p in (step(xs, xi[x], -1, row_spans[y], row),
                               step(xs, xi[x], 1, row_spans[y], row),
                               step(ys, yi[y], -1, col_spans[x], col),
                               step(ys, yi[y], 1, col_spans[x], col)) if p is not None]
            if n in into_end:
                out.append(end)
            known[n] = out
        return out

    best = _cheapest(start, start_dir, end, end_dir, neighbours, stub_cost, costs)
    if best is None:
        # Nowhere clear to go: an elbow, the honest fallback.
        s1 = (start[0] + start_dir[0] * margin, start[1] + start_dir[1] * margin)
        e1 = (end[0] - end_dir[0] * margin, end[1] - end_dir[1] * margin)
        middle = [(e1[0], s1[1])] if start_dir in (LEFT, RIGHT) else [(s1[0], e1[1])]
        best = [start, s1] + middle + [e1, end]
    return simplify(best)


def least_cost(node: Point, heading: Point, end: Point, end_dir: Point) -> float:
    """The least any route from ``node``, moving along ``heading``, to ``end``, arriving along
    ``end_dir``, can cost: the Manhattan distance, plus a bend for each turn there is no
    arriving without. Every piece costs at least its length and every turn a BEND, so no route
    costs less - which makes this both the router's estimate of what is left and a bound below
    which a candidate pair of ends is not worth routing."""
    dx, dy = end[0] - node[0], end[1] - node[1]
    left = abs(dx) + abs(dy)
    if left == 0.0:
        return 0.0
    if heading == end_dir:  # straight in only if already on the line, with the end ahead
        ahead = dx * end_dir[0] + dy * end_dir[1]
        on_line = (dy == 0.0) if end_dir[1] == 0.0 else (dx == 0.0)
        return left if on_line and ahead > 0 else left + 2 * BEND
    return left + (2 * BEND if heading == (-end_dir[0], -end_dir[1]) else BEND)


def _corridors(levels: Sequence[float], edges: Sequence[float], lo: float,
               hi: float) -> set:
    """Halfway between each arrow already running one way and its neighbour - the next such
    arrow, a box's edge, or the frame: where a new line keeps furthest from both."""
    marks = sorted(set(levels) | set(edges) | {lo, hi})
    lines = set(levels)
    return {(u + v) / 2 for u, v in zip(marks, marks[1:])
            if v - u > 2 * ON_TOP and (u in lines or v in lines)}


def _cheapest(start: Point, start_dir: Point, end: Point, end_dir: Point,
              neighbours: Callable[[Point], List[Point]],
              stub_cost: Dict[Tuple[Point, Point], float],
              costs: Costs) -> Optional[List[Point]]:
    """A* over (node, heading), so bends can be priced. The estimate of what is left - the
    Manhattan distance to the end, plus a bend for each turn there is no arriving without -
    never exceeds the true price (every piece costs at least its length, every turn a BEND),
    so the route found is still the cheapest."""
    start_state = (start, start_dir)
    dist = {start_state: 0.0}
    came: Dict[Tuple[Point, Point], Tuple[Point, Point]] = {}
    heap = [(least_cost(start, start_dir, end, end_dir), 0, 0.0, start, start_dir)]
    counter = 1
    goal_state = None
    while heap:
        _, _, cost, node, heading = heapq.heappop(heap)
        if cost > dist.get((node, heading), float("inf")):
            continue
        if node == end:
            goal_state = (node, heading)
            break
        for nxt in neighbours(node):
            d = _direction(node, nxt)
            if d == (-heading[0], -heading[1]):
                continue  # no doubling back on itself
            step = abs(nxt[0] - node[0]) + abs(nxt[1] - node[1])
            extra = BEND if d != heading else 0.0
            extra += stub_cost.get((node, nxt), 0.0)
            new = cost + step + extra + costs.piece(node, nxt)
            if new < dist.get((nxt, d), float("inf")):
                dist[(nxt, d)] = new
                came[(nxt, d)] = (node, heading)
                heapq.heappush(heap, (new + least_cost(nxt, d, end, end_dir), counter,
                                      new, nxt, d))
                counter += 1
    if goal_state is None:
        return None
    path = [goal_state[0]]
    state = goal_state
    while state in came:
        state = came[state]
        path.append(state[0])
    return list(reversed(path))


def attach(box: Rect, side: int, taken: Sequence[float],
           prefer: Union[None, float, Sequence[float]] = None,
           spacing: float = 12.0, corner: float = 8.0) -> float:
    """Where on a side of a box a new arrow attaches - the coordinate along that side.

    ``taken`` are the coordinates other arrows already use there. A preferred coordinate (one
    that would let the arrow run straight, or where it was before) is used if it is free - or
    the first free one of several; otherwise the first free one of the usual even divisions -
    half, thirds, quarters, fifths. A side too crowded for any of those gets the spot farthest
    from the arrows already on it.
    """
    prefs = [] if prefer is None else [prefer] if isinstance(prefer, (int, float)) else prefer
    options = attach_options(box, side, taken, prefs, spacing, corner)
    return min(options, key=lambda o: o[1])[0]


CROWDED = 6.0  # per unit an end comes closer than the spacing to another on its side
_EVEN = (1 / 2, 1 / 3, 2 / 3, 1 / 4, 3 / 4, 1 / 5, 2 / 5, 3 / 5, 4 / 5, 1 / 6, 5 / 6)


def attach_options(box: Rect, side: int, taken: Sequence[float],
                   prefer: Sequence[float] = (), spacing: float = 12.0,
                   corner: float = 8.0) -> List[Tuple[float, float]]:
    """The places on a side of a box worth trying for an arrow's end, each with a price.

    Free preferred coordinates first (free of charge), then the free even divisions of the side
    (a little dearer the further down the list, which keeps ends near the middle when nothing
    else matters), and - when the side is crowded - the spots farthest from the ends already
    there, priced by how much closer than ``spacing`` they come. Whoever routes the arrow tries
    them and adds the price to the route's own cost.
    """
    x, y, w, h = box
    lo, hi = (y + corner, y + h - corner) if side in (SIDE_LEFT, SIDE_RIGHT) else \
        (x + corner, x + w - corner)
    if hi <= lo:
        return [((lo + hi) / 2, 0.0)]
    near = [t for t in taken if lo - spacing < t < hi + spacing]

    def gap(c: float) -> float:
        return min((abs(c - t) for t in near), default=float("inf"))

    out: List[Tuple[float, float]] = []
    seen = set()

    def offer(c: float, price: float) -> None:
        key = round(c, 3)
        if lo <= c <= hi and key not in seen:
            seen.add(key)
            out.append((c, price))

    for c in prefer:
        if c is not None and gap(c) >= spacing:
            offer(c, 0.0)
    for i, f in enumerate(_EVEN[:3]):  # the middle and the thirds
        c = lo + (hi - lo) * f
        if gap(c) >= spacing:
            offer(c, 1.0 + 0.5 * i)
    for c in (lo, hi):  # the two ends of the side, if free: often what keeps arrows apart
        if gap(c) >= spacing:
            offer(c, 2.5)
    for i, f in enumerate(_EVEN[3:], start=3):
        c = lo + (hi - lo) * f
        if gap(c) >= spacing:
            offer(c, 1.0 + 0.5 * i)
    inside = sorted(t for t in near if lo <= t <= hi)
    for c in [lo, hi] + [(a + b) / 2 for a, b in zip(inside, inside[1:])] + list(prefer):
        if c is not None:
            offer(c, 10.0 + CROWDED * max(0.0, spacing - gap(c)))
    return out


def route_cost(points: Sequence[Point], lines: Sequence[Segment] = (),
               labels: Sequence[Rect] = (), costs: Optional[Costs] = None) -> float:
    """What a finished route costs by the router's own measure: its length, a price per bend,
    and its crossings of and runs along the arrows in ``lines`` (or those ``costs`` holds)."""
    pieces = list(zip(points, points[1:]))
    cost = sum(abs(b[0] - a[0]) + abs(b[1] - a[1]) for a, b in pieces)
    cost += BEND * max(0, len(points) - 2)
    if costs is None:
        costs = Costs(lines, labels)
    return cost + sum(costs.piece(a, b) for a, b in pieces if a != b)


def point_on(box: Rect, side: int, along: float) -> Point:
    """The point at coordinate ``along`` on a side of a box."""
    x, y, w, h = box
    return {SIDE_RIGHT: (x + w, along), SIDE_LEFT: (x, along),
            SIDE_TOP: (along, y), SIDE_BOTTOM: (along, y + h)}[side]


def length(points: Sequence[Point]) -> float:
    return sum(abs(b[0] - a[0]) + abs(b[1] - a[1]) for a, b in zip(points, points[1:]))
