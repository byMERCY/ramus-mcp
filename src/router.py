"""Orthogonal arrow routing for IDEF0 sheets.

An IDEF0 arrow runs in horizontal and vertical pieces, leaves a box from the side its role
says (outputs right) and enters another the same way (inputs from the left, controls from the
top, mechanisms from below). This finds such a route between two points on a sheet that keeps
clear of the boxes, bends as little as it can, and avoids crossing or running along the arrows
already there - the things that make a diagram hard to read.

The method is the standard one for this: an orthogonal visibility graph. Candidate horizontal
and vertical lines are taken along the edges of every box (kept a margin away), through the
two end points and halfway between them; their crossings are the graph's nodes, joined where a
straight piece passes no box. The cheapest path through it - length, plus a price for every
bend, every crossing of an existing arrow and every stretch drawn on top of one - is the route.

Pure geometry: nothing here knows about files.
"""

from __future__ import annotations

import heapq
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

Point = Tuple[float, float]
Rect = Tuple[float, float, float, float]  # x, y, width, height
Segment = Tuple[Point, Point]

MARGIN = 14.0  # how far a route keeps from a box, and how long the first and last pieces are
BEND = 40.0  # a bend costs as much as this much extra length
CROSSING = 30.0  # crossing an existing arrow
OVERLAP = 90.0  # running along one (per piece that does)
NEAR = 16.0  # closer than this alongside another arrow reads as crowding ...
NEAR_PER_UNIT = 1.5  # ... and costs this much per unit of length it does so

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
                    cost += OVERLAP if abs(p[1] - y) < 2.0 else NEAR_PER_UNIT * run
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
                    cost += OVERLAP if abs(p[0] - x) < 2.0 else NEAR_PER_UNIT * run
    return cost


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
          margin: float = MARGIN) -> List[Point]:
    """An orthogonal route from ``start`` to ``end``.

    ``start_dir`` is the way the route leaves ``start`` (out of the side of a box, or into the
    sheet from the frame); ``end_dir`` the way it moves as it arrives at ``end``. ``obstacles``
    are the boxes to keep clear of - the two the arrow joins included - and ``lines`` the
    arrows already drawn. ``bounds`` is the drawable area.
    """
    s1 = (start[0] + start_dir[0] * margin, start[1] + start_dir[1] * margin)
    e1 = (end[0] - end_dir[0] * margin, end[1] - end_dir[1] * margin)
    blocked = [_blocked(r, margin * 0.75) for r in obstacles]
    bx, by, bw, bh = bounds

    xs = {s1[0], e1[0], (s1[0] + e1[0]) / 2, bx + margin, bx + bw - margin}
    ys = {s1[1], e1[1], (s1[1] + e1[1]) / 2, by + margin, by + bh - margin}
    for r in obstacles:
        xs.update((r[0] - margin, r[0] + r[2] + margin))
        ys.update((r[1] - margin, r[1] + r[3] + margin))
    xs = sorted(x for x in xs if bx <= x <= bx + bw)
    ys = sorted(y for y in ys if by <= y <= by + bh)

    def free(p: Point) -> bool:
        return p in (s1, e1) or not any(_inside(p, r) for r in blocked)

    nodes = {(x, y) for x in xs for y in ys if free((x, y))}
    nodes.update((s1, e1))
    neighbours: Dict[Point, List[Point]] = {n: [] for n in nodes}
    for y in ys:
        row = sorted((n for n in nodes if n[1] == y), key=lambda n: n[0])
        for a, b in zip(row, row[1:]):
            if not any(_crosses_rect(a, b, r) for r in blocked):
                neighbours[a].append(b)
                neighbours[b].append(a)
    for x in xs:
        col = sorted((n for n in nodes if n[0] == x), key=lambda n: n[1])
        for a, b in zip(col, col[1:]):
            if not any(_crosses_rect(a, b, r) for r in blocked):
                neighbours[a].append(b)
                neighbours[b].append(a)

    best = _cheapest(s1, start_dir, e1, end_dir, neighbours, lines)
    if best is None:
        # Nowhere clear to go: an elbow, the honest fallback.
        middle = [(e1[0], s1[1])] if start_dir in (LEFT, RIGHT) else [(s1[0], e1[1])]
        best = [s1] + middle + [e1]
    return simplify([start] + best + [end])


def _cheapest(s1: Point, start_dir: Point, e1: Point, end_dir: Point,
              neighbours: Dict[Point, List[Point]], lines: Sequence[Segment]
              ) -> Optional[List[Point]]:
    """Dijkstra over (node, heading), so bends can be priced."""
    start_state = (s1, start_dir)
    dist = {start_state: 0.0}
    came: Dict[Tuple[Point, Point], Tuple[Point, Point]] = {}
    heap = [(0.0, 0, s1, start_dir)]
    counter = 1
    goal_state = None
    while heap:
        cost, _, node, heading = heapq.heappop(heap)
        if cost > dist.get((node, heading), float("inf")):
            continue
        if node == e1:
            goal_state = (node, heading)
            break
        for nxt in neighbours.get(node, []):
            d = _direction(node, nxt)
            if d == (-heading[0], -heading[1]):
                continue  # no doubling back on itself
            step = abs(nxt[0] - node[0]) + abs(nxt[1] - node[1])
            extra = BEND if d != heading else 0.0
            if nxt == e1 and d != end_dir:
                extra += BEND
            new = cost + step + extra + _line_cost(node, nxt, lines)
            if new < dist.get((nxt, d), float("inf")):
                dist[(nxt, d)] = new
                came[(nxt, d)] = (node, heading)
                heapq.heappush(heap, (new, counter, nxt, d))
                counter += 1
    if goal_state is None:
        return None
    path = [goal_state[0]]
    state = goal_state
    while state in came:
        state = came[state]
        path.append(state[0])
    return list(reversed(path))


def attach(box: Rect, side: int, taken: Sequence[float], prefer: Optional[float] = None,
           spacing: float = 12.0, corner: float = 8.0) -> float:
    """Where on a side of a box a new arrow attaches - the coordinate along that side.

    ``taken`` are the coordinates other arrows already use there. A preferred coordinate (one
    that would let the arrow run straight) is used if it is free; otherwise the first free one
    of the usual even divisions - half, thirds, quarters, fifths.
    """
    x, y, w, h = box
    lo, hi = (y + corner, y + h - corner) if side in (SIDE_LEFT, SIDE_RIGHT) else \
        (x + corner, x + w - corner)
    if hi <= lo:
        return (lo + hi) / 2

    def free(c: float) -> bool:
        return lo <= c <= hi and all(abs(c - t) >= spacing for t in taken)

    if prefer is not None and free(prefer):
        return prefer
    span = hi - lo
    for f in (1 / 2, 1 / 3, 2 / 3, 1 / 4, 3 / 4, 1 / 5, 2 / 5, 3 / 5, 4 / 5, 1 / 6, 5 / 6):
        c = lo + span * f
        if free(c):
            return c
    return lo + span / 2


def point_on(box: Rect, side: int, along: float) -> Point:
    """The point at coordinate ``along`` on a side of a box."""
    x, y, w, h = box
    return {SIDE_RIGHT: (x + w, along), SIDE_LEFT: (x, along),
            SIDE_TOP: (along, y), SIDE_BOTTOM: (along, y + h)}[side]


def length(points: Sequence[Point]) -> float:
    return sum(abs(b[0] - a[0]) + abs(b[1] - a[1]) for a, b in zip(points, points[1:]))
