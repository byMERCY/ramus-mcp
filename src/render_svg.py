"""Render a Ramus diagram to SVG.

This is the "eyes": it turns the boxes a model stores into a picture you can look at, drawn
the way IDEF0 draws them - a white box per activity, its name centred, its number in the
lower-right corner. The layout is the model's own; nothing is moved.

Arrows come next; this first pass draws the activities and where they sit.
"""

from __future__ import annotations

import sys
from html import escape
from typing import List

# Allow running as a script (python src/render_svg.py ...) as well as importing.
try:
    from .ramus_rsf import RsfModel, Diagram, Activity
except ImportError:  # pragma: no cover - script execution
    from ramus_rsf import RsfModel, Diagram, Activity


MARGIN = 40.0
FONT_SIZE = 11.0
LINE_HEIGHT = 13.0
NUMBER_FONT_SIZE = 10.0
CHAR_WIDTH = FONT_SIZE * 0.55  # rough advance width for a sans-serif glyph


def _wrap(name: str, box_width: float) -> List[str]:
    """Greedily wrap a name to fit the box, so a long label is not clipped."""
    usable = max(box_width - 10.0, CHAR_WIDTH)
    max_chars = max(int(usable / CHAR_WIDTH), 1)
    words = name.split()
    if not words:
        return [""]
    lines: List[str] = []
    line = words[0]
    for word in words[1:]:
        if len(line) + 1 + len(word) <= max_chars:
            line += " " + word
        else:
            lines.append(line)
            line = word
    lines.append(line)
    return lines


def _box_svg(a: Activity) -> str:
    cx = a.x + a.width / 2.0
    lines = _wrap(a.name, a.width)
    block_height = len(lines) * LINE_HEIGHT
    first_baseline = a.y + (a.height - block_height) / 2.0 + FONT_SIZE

    parts = [
        f'<rect x="{a.x:.1f}" y="{a.y:.1f}" width="{a.width:.1f}" '
        f'height="{a.height:.1f}" fill="#ffffff" stroke="#1a1a1a" '
        f'stroke-width="1.4"/>'
    ]
    for i, line in enumerate(lines):
        ty = first_baseline + i * LINE_HEIGHT
        parts.append(
            f'<text x="{cx:.1f}" y="{ty:.1f}" text-anchor="middle" '
            f'font-size="{FONT_SIZE}" font-family="Segoe UI, Arial, sans-serif" '
            f'fill="#111111">{escape(line)}</text>'
        )
    if a.number:
        parts.append(
            f'<text x="{a.x + a.width - 5:.1f}" y="{a.y + a.height - 5:.1f}" '
            f'text-anchor="end" font-size="{NUMBER_FONT_SIZE}" '
            f'font-family="Segoe UI, Arial, sans-serif" fill="#555555">'
            f'{escape(a.number)}</text>'
        )
    return "\n".join(parts)


def render_diagram(diagram: Diagram, title: str = "") -> str:
    """One diagram -> a complete SVG document string."""
    boxes = diagram.activities
    if boxes:
        min_x = min(a.x for a in boxes)
        min_y = min(a.y for a in boxes)
        max_x = max(a.x + a.width for a in boxes)
        max_y = max(a.y + a.height for a in boxes)
    else:
        min_x = min_y = 0.0
        max_x = max_y = 100.0

    vb_x = min_x - MARGIN
    vb_y = min_y - MARGIN
    vb_w = (max_x - min_x) + 2 * MARGIN
    vb_h = (max_y - min_y) + 2 * MARGIN

    heading = title or (
        f"Decomposition of: {diagram.parent_name}"
        if diagram.parent_name
        else "Diagram"
    )

    body = "\n".join(_box_svg(a) for a in boxes)

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="{vb_x:.1f} {vb_y:.1f} {vb_w:.1f} {vb_h:.1f}" font-family="Segoe UI, Arial, sans-serif">
  <rect x="{vb_x:.1f}" y="{vb_y:.1f}" width="{vb_w:.1f}" height="{vb_h:.1f}" fill="#ffffff"/>
  <text x="{vb_x + 8:.1f}" y="{vb_y + 18:.1f}" font-size="12" fill="#888888">{escape(heading)}</text>
{body}
</svg>
"""


def main(argv: List[str]) -> int:
    if len(argv) < 2:
        print("usage: render_svg.py <model.rsf> [out.svg]", file=sys.stderr)
        return 2
    path = argv[1]
    out = argv[2] if len(argv) > 2 else "diagram.svg"

    with RsfModel(path) as model:
        diagrams = model.diagrams()
        if not diagrams:
            print("no diagrams with boxes found in this model", file=sys.stderr)
            return 1
        biggest = diagrams[0]
        svg = render_diagram(biggest)

    with open(out, "w", encoding="utf-8") as fh:
        fh.write(svg)

    print(
        f"rendered '{biggest.parent_name or '(root)'}' "
        f"({len(biggest.activities)} activities) -> {out}"
    )
    print(f"diagrams in model: {len(diagrams)} "
          f"(sizes: {', '.join(str(len(d.activities)) for d in diagrams[:8])})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
