"""Render a Ramus diagram to SVG.

This is the "eyes" in a form a person can open, save or embed: the page frame, a box per
activity with its name and node number, and every arrow drawn along the route the model stores
- with its head, its colour, its name and the zig-zag that ties the name to the line. The
layout is the model's own; nothing is moved. What to draw comes from :mod:`scene`.
"""

from __future__ import annotations

import sys
from html import escape
from typing import List

try:
    from .ramus_rsf import RsfModel, Diagram
    from .scene import Dot, Item, Line, Poly, Rect, Scene, build_scene
except ImportError:  # pragma: no cover - script execution
    from ramus_rsf import RsfModel, Diagram
    from scene import Dot, Item, Line, Poly, Rect, Scene, build_scene


FONT_FAMILY = "Segoe UI, Arial, DejaVu Sans, sans-serif"


def _num(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _points(points) -> str:
    return " ".join(f"{_num(x)},{_num(y)}" for x, y in points)


def _dash(dash) -> str:
    return f' stroke-dasharray="{" ".join(_num(d) for d in dash)}"' if dash else ""


def _element(item: Item) -> str:
    if isinstance(item, Rect):
        rounded = f' rx="{_num(item.radius)}" ry="{_num(item.radius)}"' if item.radius else ""
        return (
            f'<rect x="{_num(item.x)}" y="{_num(item.y)}" width="{_num(item.w)}" '
            f'height="{_num(item.h)}"{rounded} fill="{item.fill or "none"}" '
            f'stroke="{item.stroke or "none"}" stroke-width="{_num(item.stroke_width)}"'
            f'{_dash(item.dash)}/>'
        )
    if isinstance(item, Line):
        return (
            f'<polyline points="{_points(item.points)}" fill="none" stroke="{item.color}" '
            f'stroke-width="{_num(item.width)}" stroke-linejoin="round" '
            f'stroke-linecap="butt"{_dash(item.dash)}/>'
        )
    if isinstance(item, Poly):
        return f'<polygon points="{_points(item.points)}" fill="{item.fill}"/>'
    if isinstance(item, Dot):
        return f'<circle cx="{_num(item.x)}" cy="{_num(item.y)}" r="{_num(item.r)}" fill="{item.fill}"/>'
    weight = ' font-weight="bold"' if item.bold else ""
    return (
        f'<text x="{_num(item.x)}" y="{_num(item.y)}" text-anchor="{item.anchor}" '
        f'font-size="{_num(item.size)}"{weight} fill="{item.color}">{escape(item.text)}</text>'
    )


def render_scene(scene: Scene) -> str:
    body = "\n".join("  " + _element(item) for item in scene.items)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="{_num(scene.x)} {_num(scene.y)} {_num(scene.width)} {_num(scene.height)}" '
        f'font-family="{FONT_FAMILY}">\n'
        f'  <rect x="{_num(scene.x)}" y="{_num(scene.y)}" width="{_num(scene.width)}" '
        f'height="{_num(scene.height)}" fill="#ffffff"/>\n'
        f"{body}\n"
        "</svg>\n"
    )


def render_diagram(diagram: Diagram, title: str = "", form=None) -> str:
    """One diagram -> a complete SVG document string (in the IDEF0 form, given one)."""
    return render_scene(build_scene(diagram, title, form))


def main(argv: List[str]) -> int:
    if len(argv) < 2:
        print("usage: render_svg.py <model.rsf> [out.svg] [node, e.g. A1]", file=sys.stderr)
        return 2
    path = argv[1]
    out = argv[2] if len(argv) > 2 else "diagram.svg"
    node = argv[3] if len(argv) > 3 else None

    with RsfModel(path) as model:
        diagrams = model.diagrams()
        if not diagrams:
            print("no diagrams with boxes found in this model", file=sys.stderr)
            return 1
        chosen = diagrams[0]
        if node:
            matches = [d for d in diagrams if d.node.lower() == node.lower()]
            if not matches:
                print(f"no diagram {node!r}; the model has: "
                      f"{', '.join(sorted(d.node for d in diagrams))}", file=sys.stderr)
                return 1
            chosen = matches[0]
        svg = render_diagram(chosen)

    with open(out, "w", encoding="utf-8") as fh:
        fh.write(svg)

    print(
        f"rendered {chosen.node} '{chosen.parent_name or '(root)'}' "
        f"({len(chosen.activities)} activities, {len(chosen.arrows)} arrow segments) -> {out}"
    )
    print(f"diagrams in model: {', '.join(d.node for d in diagrams)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
