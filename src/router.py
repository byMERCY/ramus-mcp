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
JOG = 30.0  # a short step between two bends - a dip round a corner - on top of the bends
JOG_UNDER = 14.0  # ... a step shorter than this
OFF_CENTRE = 0.05  # per unit a turn sits away from halfway between the ends - a tie-breaker
CROSSING = 30.0  # crossing an existing arrow
OVERLAP = 90.0  # running along one: this much for each piece that does ...
OVERLAP_PER_UNIT = 3.0  # ... and this much per unit of length drawn on top of it
THROUGH_LABEL = 12.0  # running through another arrow's name - which can be moved off it
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
    if not labels:
        return 0.0
    if a[1] == b[1]:  # a horizontal piece meets only the names its height runs through
        y, x0, x1 = a[1], min(a[0], b[0]), max(a[0], b[0])
        hits = sum(1 for rx, ry, rw, rh in labels
                   if ry < y < ry + rh and max(x0, rx) < min(x1, rx + rw))
    else:
        x, y0, y1 = a[0], min(a[1], b[1]), max(a[1], b[1])
        hits = sum(1 for rx, ry, rw, rh in labels
                   if rx < x < rx + rw and max(y0, ry) < min(y1, ry + rh))
    return THROUGH_LABEL * hits


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


End = Tuple[Point, Point, float]  # a place a route may start or end: point, way, price


class Found:
    """What :func:`route_many` found: the route, and the start and end it took."""

    def __init__(self, points: List[Point], start: End, end: End):
        self.points = points
        self.start = start
        self.end = end

    @property
    def price(self) -> float:
        """What the two ends it took cost, on top of the route itself."""
        return self.start[2] + self.end[2]


def route(start: Point, start_dir: Point, end: Point, end_dir: Point,
          obstacles: Sequence[Rect], bounds: Rect, lines: Sequence[Segment] = (),
          margin: float = MARGIN, labels: Sequence[Rect] = (),
          costs: Optional[Costs] = None,
          extra: Optional[Callable[[Point, Point], float]] = None) -> List[Point]:
    """An orthogonal route from ``start`` to ``end``.

    ``start_dir`` is the way the route leaves ``start`` (out of the side of a box, or into the
    sheet from the frame); ``end_dir`` the way it moves as it arrives at ``end``. ``obstacles``
    are the boxes to keep clear of - the two the arrow joins included - and ``lines`` the
    arrows already drawn. ``bounds`` is the drawable area. ``costs``, if given, is a Costs
    over those same lines and labels, kept by a caller routing many times on one sheet.
    ``extra(a, b)``, if given, is what this arrow in particular pays for the piece a-b on top
    of the usual - never less than nothing (see :func:`feedback_price`).

    The first and last pieces run straight out of ``start`` and into ``end`` and may be any
    length from MIN_STUB up, so a route can turn in a gap between two boxes narrower than two
    margins; where nothing else decides it, the turn is made halfway between the ends.
    """
    return route_many([(start, start_dir, 0.0)], [(end, end_dir, 0.0)], obstacles, bounds,
                      lines, margin, labels, costs, extra).points


def route_many(starts: Sequence[End], ends: Sequence[End], obstacles: Sequence[Rect],
               bounds: Rect, lines: Sequence[Segment] = (), margin: float = MARGIN,
               labels: Sequence[Rect] = (), costs: Optional[Costs] = None,
               extra: Optional[Callable[[Point, Point], float]] = None) -> Found:
    """The cheapest route from any of ``starts`` to any of ``ends`` - each a point, the way
    a route leaves it (or arrives at it), and what taking it costs - in one search: the same
    route :func:`route` finds for the best pair, without routing every pair on its own. The
    graph carries the lines of every start and end, so no pair's route is missed.
    """
    clearance = margin * 0.75
    blocked = [_blocked(r, clearance) for r in obstacles]
    bx, by, bw, bh = bounds
    start_points = {p for p, _, _ in starts}
    ends = [e for e in ends if e[0] not in start_points]
    if not starts or not ends:
        raise ValueError("a route needs somewhere to start and somewhere else to end")

    xs = {bx + margin, bx + bw - margin}
    ys = {by + margin, by + bh - margin}
    for p, d, _ in starts:
        xs.update((p[0], p[0] + d[0] * margin))
        ys.update((p[1], p[1] + d[1] * margin))
    for p, d, _ in ends:
        xs.update((p[0], p[0] - d[0] * margin))
        ys.update((p[1], p[1] - d[1] * margin))

    def nearest(p: Point, others: Sequence[End]) -> Point:
        return min((o[0] for o in others), key=lambda q: abs(q[0] - p[0]) + abs(q[1] - p[1]))

    # Halfway between the ends: where a turn goes when nothing else decides it - for every
    # pair, so that whichever pair wins has its middle to turn in.
    for p, _, _ in starts:
        for q, _, _ in ends:
            xs.add((p[0] + q[0]) / 2)
            ys.add((p[1] + q[1]) / 2)
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
    # An end on a box or the frame is a free point too, but joins only by its ray.
    terminals = start_points | {p for p, _, _ in ends}

    def free(p: Point) -> bool:
        return not any(lo < p[0] < hi for lo, hi in row_spans[p[1]])

    def step(coords: List[float], k: int, way: int, spans, at) -> Optional[Point]:
        """The next node from coords[k] one way along a line, if no box is in between."""
        here = coords[k]
        k += way
        while 0 <= k < len(coords):
            p = at(coords[k])
            if p in terminals:
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
    # pieces along their own direction: out of a start, into an end. Such a piece may cross
    # the clearance of the box it starts or ends on, and no other.
    stub_cost: Dict[Tuple[Point, Point], float] = {}

    def ray(origin: Point, direction: Point, toward: Point, outward: bool) -> List[Point]:
        others = [r for r in blocked if not _inside(origin, r)]
        if direction in (LEFT, RIGHT):
            line = [(x, origin[1]) for x in xs] if origin[1] in yi else []
        else:
            line = [(origin[0], y) for y in ys] if origin[0] in xi else []
        mid_along = ((toward[0] - origin[0]) * direction[0]
                     + (toward[1] - origin[1]) * direction[1]) / 2
        ideal = mid_along if mid_along >= MIN_STUB else margin
        out = []
        for n in line:
            t = (n[0] - origin[0]) * direction[0] + (n[1] - origin[1]) * direction[1]
            if t >= MIN_STUB and n not in terminals and free(n) and not any(
                    _crosses_rect(origin, n, r) for r in others):
                out.append(n)
                stub_cost[(origin, n) if outward else (n, origin)] = OFF_CENTRE * abs(t - ideal)
        return out

    known: Dict[Point, List[Point]] = {}
    for p, d, _ in starts:
        known.setdefault(p, []).extend(ray(p, d, nearest(p, ends), True))
    into: Dict[Point, List[Point]] = {}
    end_price: Dict[Point, float] = {}
    end_of: Dict[Point, End] = {}
    for e in ends:
        p, d, price = e
        if p in end_price and end_price[p] <= price:
            continue
        end_price[p], end_of[p] = price, e
        for n in ray(p, (-d[0], -d[1]), nearest(p, starts), False):
            into.setdefault(n, []).append(p)
    for p, d, _ in starts:  # straight across, where a start and an end face each other
        for q, qd, _ in ends:
            if d == qd and p != q and (p[0] == q[0] or p[1] == q[1]) and \
                    _direction(p, q) == d and not any(
                        _crosses_rect(p, q, r) for r in blocked
                        if not _inside(p, r) and not _inside(q, r)):
                known[p].append(q)

    def neighbours(n: Point) -> List[Point]:
        out = known.get(n)
        if out is None:
            x, y = n
            row, col = (lambda c: (c, y)), (lambda c: (x, c))
            out = [p for p in (step(xs, xi[x], -1, row_spans[y], row),
                               step(xs, xi[x], 1, row_spans[y], row),
                               step(ys, yi[y], -1, col_spans[x], col),
                               step(ys, yi[y], 1, col_spans[x], col)) if p is not None]
            out.extend(into.get(n, ()))
            known[n] = out
        return out

    goals = [(p, d, end_price[p]) for p, (_, d, _) in end_of.items()]
    best = _cheapest(starts, goals, neighbours, stub_cost, costs, extra)
    if best is None:
        # Nowhere clear to go: an elbow between the likeliest pair, the honest fallback.
        s, e = min(((s, e) for s in starts for e in ends),
                   key=lambda se: least_cost(se[0][0], se[0][1], se[1][0], se[1][1])
                   + se[0][2] + se[1][2])
        (sp, sd, _), (ep, ed, _) = s, e
        s1 = (sp[0] + sd[0] * margin, sp[1] + sd[1] * margin)
        e1 = (ep[0] - ed[0] * margin, ep[1] - ed[1] * margin)
        middle = [(e1[0], s1[1])] if sd in (LEFT, RIGHT) else [(s1[0], e1[1])]
        return Found(simplify([sp, s1] + middle + [e1, ep]), s, e)
    path, first = best
    return Found(simplify(path), first, end_of[path[-1]])


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


def _cheapest(starts: Sequence[End], goals: Sequence[End],
              neighbours: Callable[[Point], List[Point]],
              stub_cost: Dict[Tuple[Point, Point], float],
              costs: Costs,
              extra: Optional[Callable[[Point, Point], float]] = None):
    """A* over (node, heading), so bends can be priced, from every start at once (each at its
    own price) to whichever goal is cheapest (its price paid on arrival). The estimate of what
    is left - for the cheapest goal, the Manhattan distance to it, plus a bend for each turn
    there is no arriving without, plus its price - never exceeds the true price (every piece
    costs at least its length, every turn a BEND), so the route found is still the cheapest.
    Returns the route's points and the start it left from, or None."""
    goal_price = {p: price for p, _, price in goals}

    if len(goals) == 1:
        (gp, gd, gprice), = goals

        def estimate(node: Point, heading: Point) -> float:
            return least_cost(node, heading, gp, gd) + gprice
    else:
        seen: Dict[Tuple[Point, Point], float] = {}  # a state is reached again and again
        flat = [(p[0], p[1], d[0], d[1], price) for p, d, price in goals]
        two_bends = 2 * BEND

        def estimate(node: Point, heading: Point) -> float:
            # least_cost to each goal, plus its price - written out, as this runs for every
            # state the search reaches
            key = (node, heading)
            value = seen.get(key)
            if value is not None:
                return value
            nx, ny = node
            hx, hy = heading
            value = float("inf")
            for gx, gy, ex, ey, price in flat:
                dx, dy = gx - nx, gy - ny
                left = (dx if dx >= 0 else -dx) + (dy if dy >= 0 else -dy)
                if left == 0.0:
                    cost = 0.0
                elif hx == ex and hy == ey:
                    on_line = dy == 0.0 if ey == 0.0 else dx == 0.0
                    cost = left if on_line and dx * ex + dy * ey > 0 else left + two_bends
                elif hx == -ex and hy == -ey:
                    cost = left + two_bends
                else:
                    cost = left + BEND
                cost += price
                if cost < value:
                    value = cost
            seen[key] = value
            return value

    dist: Dict[Tuple[Point, Point], float] = {}
    came: Dict[Tuple[Point, Point], Tuple[Point, Point]] = {}
    origin: Dict[Tuple[Point, Point], End] = {}
    heap = []
    counter = 0
    for s in starts:
        p, d, price = s
        state = (p, d)
        if price < dist.get(state, float("inf")):
            dist[state] = price
            origin[state] = s
            heapq.heappush(heap, (price + estimate(p, d), counter, price, p, d))
            counter += 1
    goal_state = None
    while heap:
        _, _, cost, node, heading = heapq.heappop(heap)
        if cost > dist.get((node, heading), float("inf")):
            continue
        if node in goal_price and (node, heading) not in origin:
            goal_state = (node, heading)
            break
        # Turning here after a short step that itself began with a turn makes a jog.
        jogged = False
        before = came.get((node, heading))
        if before is not None and before[1] != heading and \
                abs(node[0] - before[0][0]) + abs(node[1] - before[0][1]) < JOG_UNDER:
            jogged = True
        for nxt in neighbours(node):
            d = _direction(node, nxt)
            if d == (-heading[0], -heading[1]):
                continue  # no doubling back on itself
            step = abs(nxt[0] - node[0]) + abs(nxt[1] - node[1])
            extra_cost = BEND if d != heading else 0.0
            if jogged and d != heading:
                extra_cost += JOG
            extra_cost += stub_cost.get((node, nxt), 0.0)
            if extra is not None:
                extra_cost += extra(node, nxt)
            if nxt in goal_price:
                extra_cost += goal_price[nxt]
            new = cost + step + extra_cost + costs.piece(node, nxt)
            if new < dist.get((nxt, d), float("inf")):
                dist[(nxt, d)] = new
                came[(nxt, d)] = (node, heading)
                heapq.heappush(heap, (new + estimate(nxt, d), counter, new, nxt, d))
                counter += 1
    if goal_state is None:
        return None
    path = [goal_state[0]]
    state = goal_state
    while state in came:
        state = came[state]
        path.append(state[0])
    return list(reversed(path)), origin[state]


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
CENTRE = 60.0  # what an end on a box pays at a corner of its side, for being off the middle -
#                growing with the square of the distance, so the middle half is cheap and the
#                corners are not worth a shorter route; lining up for a straight run (two bends
#                saved) still is
OUT_OF_PLACE = 40.0  # an end in a corner, or on the box's number, where nothing else is free
_EVEN = (1 / 2, 1 / 3, 2 / 3, 1 / 4, 3 / 4, 1 / 5, 2 / 5, 3 / 5, 4 / 5, 1 / 6, 5 / 6)


def corner_zone(length: float) -> float:
    """How near a corner an arrow's end may come on a side of a box this long before it looks
    pushed into the corner: a fifth of the side, at least 8 and at most 20."""
    return min(20.0, max(8.0, 0.2 * length))


def attach_options(box: Rect, side: int, taken: Sequence[float],
                   prefer: Sequence[float] = (), spacing: float = 12.0,
                   corner: Optional[float] = 8.0, centre: float = 0.0,
                   avoid: Sequence[Tuple[float, float]] = ()) -> List[Tuple[float, float]]:
    """The places on a side of a box worth trying for an arrow's end, each with a price.

    Free preferred coordinates first, then the free even divisions of the side (a little
    dearer the further down the list, which keeps ends near the middle when nothing else
    matters), and - when the side is crowded - the spots farthest from the ends already there,
    priced by how much closer than ``spacing`` they come. Whoever routes the arrow tries them
    and adds the price to the route's own cost.

    ``corner`` is how near either corner a place may be (None: :func:`corner_zone`). With a
    ``centre`` price every place also pays for its distance from the middle of the side -
    ``centre`` at a corner, a quarter of it halfway there. ``avoid`` are stretches of the side
    (from, to) to keep off, like the corner where the box's number is written: a place there is
    offered only when the side is crowded, and dearly.
    """
    x, y, w, h = box
    a0, a1 = (y, y + h) if side in (SIDE_LEFT, SIDE_RIGHT) else (x, x + w)
    zone = corner_zone(a1 - a0) if corner is None else corner
    lo, hi = a0 + zone, a1 - zone
    if hi <= lo:
        return [((a0 + a1) / 2, 0.0)]
    near = [t for t in taken if lo - spacing < t < hi + spacing]
    mid, half = (a0 + a1) / 2, (a1 - a0) / 2

    def gap(c: float) -> float:
        return min((abs(c - t) for t in near), default=float("inf"))

    def placed(c: float) -> float:
        price = centre * ((c - mid) / half) ** 2
        if any(f <= c <= t for f, t in avoid):
            price += OUT_OF_PLACE
        return price

    out: List[Tuple[float, float]] = []
    seen = set()

    def offer(c: float, price: float, first: float = lo, last: float = hi) -> None:
        key = round(c, 3)
        if first <= c <= last and key not in seen:
            seen.add(key)
            out.append((c, price + placed(c)))

    def fits(c: float) -> bool:
        return gap(c) >= spacing and not any(f <= c <= t for f, t in avoid)

    for c in prefer:
        if c is not None and fits(c):
            offer(c, 0.0)
    for i, f in enumerate(_EVEN[:3]):  # the middle and the thirds
        c = lo + (hi - lo) * f
        if fits(c):
            offer(c, 1.0 + 0.5 * i)
    edges = [lo, hi]
    for f, t in avoid:  # the edge of a stretch kept off is as good as a corner
        edges += [f - 0.5, t + 0.5]
    for c in edges:  # the ends of the free stretch: often what keeps arrows apart
        if fits(c):
            offer(c, 2.5)
    for i, f in enumerate(_EVEN[3:], start=3):
        c = lo + (hi - lo) * f
        if fits(c):
            offer(c, 1.0 + 0.5 * i)
    inside = sorted(t for t in near if lo <= t <= hi)
    for c in [lo, hi] + [(a + b) / 2 for a, b in zip(inside, inside[1:])] + list(prefer):
        if c is not None:
            offer(c, 10.0 + CROWDED * max(0.0, spacing - gap(c)))
    # A side too crowded for its middle stretch: into the corners, at a price.
    if not any(gap(c) >= spacing for c, _ in out):
        for c in (a0 + spacing / 2, a1 - spacing / 2):
            offer(c, OUT_OF_PLACE + 10.0 + CROWDED * max(0.0, spacing - gap(c)), a0, a1)
    return out


def route_cost(points: Sequence[Point], lines: Sequence[Segment] = (),
               labels: Sequence[Rect] = (), costs: Optional[Costs] = None,
               extra: Optional[Callable[[Point, Point], float]] = None) -> float:
    """What a finished route costs by the router's own measure: its length, a price per bend,
    and its crossings of and runs along the arrows in ``lines`` (or those ``costs`` holds) -
    plus what ``extra`` asks for its pieces, if given."""
    pieces = list(zip(points, points[1:]))
    cost = sum(abs(b[0] - a[0]) + abs(b[1] - a[1]) for a, b in pieces)
    cost += BEND * max(0, len(points) - 2)
    if costs is None:
        costs = Costs(lines, labels)
    cost += sum(costs.piece(a, b) for a, b in pieces if a != b)
    if extra is not None:
        cost += sum(extra(a, b) for a, b in pieces if a != b)
    return cost


THROUGH_BOX = 1000.0  # a route that runs through a box: only the fallback elbow ever does


def through_boxes(points: Sequence[Point], boxes: Sequence[Rect]) -> int:
    """How many of ``boxes`` a route runs through - the way a planner tells the elbow that
    :func:`route` falls back on, when there is no way round, from a real route."""
    return sum(1 for r in boxes
               if any(a != b and _crosses_rect(a, b, r) for a, b in zip(points, points[1:])))


WRONG_WAY_ROUND = 120.0  # feedback drawn round the wrong side of the box it leaves


def feedback_price(source: Rect, over: bool) -> Callable[[Point, Point], float]:
    """The ``extra`` for an output fed back to a box further left: IDEF0 draws feedback into a
    control up and over, into an input or a mechanism down and under - so a piece running back
    to the left beside or below the box it leaves (over), or beside or above it (under), pays
    WRONG_WAY_ROUND."""
    top, bottom = source[1], source[1] + source[3]

    def price(a: Point, b: Point) -> float:
        if a[1] != b[1] or b[0] >= a[0]:
            return 0.0
        wrong = a[1] > top if over else a[1] < bottom
        return WRONG_WAY_ROUND if wrong else 0.0

    return price


def point_on(box: Rect, side: int, along: float) -> Point:
    """The point at coordinate ``along`` on a side of a box."""
    x, y, w, h = box
    return {SIDE_RIGHT: (x + w, along), SIDE_LEFT: (x, along),
            SIDE_TOP: (along, y), SIDE_BOTTOM: (along, y + h)}[side]


def length(points: Sequence[Point]) -> float:
    return sum(abs(b[0] - a[0]) + abs(b[1] - a[1]) for a, b in zip(points, points[1:]))
