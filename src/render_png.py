"""Rasterise a Ramus diagram to PNG.

The SVG renderer is for a person to look at or save; this one exists so the agent can *see* a
diagram, because MCP shows the model raster images, not SVG. It draws the same scene
(:mod:`scene`) with Pillow. Pillow does not anti-alias its shapes, so the picture is drawn
larger than it is delivered and scaled down - lines and arrowheads come out smooth.
"""

from __future__ import annotations

import io
import math
from functools import lru_cache
from typing import List, Tuple

from PIL import Image, ImageDraw, ImageFont

try:
    from .ramus_rsf import Diagram
    from .scene import Dot, Line, Poly, Rect, Scene, build_scene, font_path
except ImportError:  # pragma: no cover - script execution
    from ramus_rsf import Diagram
    from scene import Dot, Line, Poly, Rect, Scene, build_scene, font_path


SCALE = 2.0  # model units -> pixels in the delivered image
SUPERSAMPLE = 2  # drawn this many times larger, then scaled down
_ANCHORS = {"start": "ls", "middle": "ms", "end": "rs"}  # PIL anchors: horizontal + baseline


@lru_cache(maxsize=32)
def _font(pixels: int):
    """A TrueType face at this pixel size, or Pillow's bitmap default if none is installed."""
    path = font_path()
    if path is None:
        return ImageFont.load_default()
    return ImageFont.truetype(path, pixels)


def _dashes(points: List[Tuple[float, float]], on: float, off: float):
    """Cut a polyline into the visible pieces of a dashed line."""
    pieces: List[List[Tuple[float, float]]] = []
    drawing, remaining = True, on
    current = [points[0]]
    for a, b in zip(points, points[1:]):
        length = math.dist(a, b)
        if length == 0:
            continue
        pos = 0.0
        while pos < length:
            step = min(remaining, length - pos)
            t = (pos + step) / length
            point = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
            if drawing:
                current.append(point)
            pos += step
            remaining -= step
            if remaining <= 1e-9:
                if drawing:
                    pieces.append(current)
                    current = []
                else:
                    current = [point]
                drawing = not drawing
                remaining = on if drawing else off
    if drawing and len(current) > 1:
        pieces.append(current)
    return pieces


def render_scene_png(scene: Scene) -> bytes:
    k = SCALE * SUPERSAMPLE
    size = (max(int(math.ceil(scene.width * k)), 1), max(int(math.ceil(scene.height * k)), 1))
    img = Image.new("RGB", size, "#ffffff")
    draw = ImageDraw.Draw(img)

    def px(p: Tuple[float, float]) -> Tuple[float, float]:
        return (p[0] - scene.x) * k, (p[1] - scene.y) * k

    def stroke(points, color, width, dash) -> None:
        pts = [px(p) for p in points]
        w = max(int(round(width * k)), 1)
        runs = _dashes(pts, dash[0] * k, dash[1] * k) if dash else [pts]
        for run in runs:
            if len(run) >= 2:
                draw.line(run, fill=color, width=w, joint="curve")

    for item in scene.items:
        if isinstance(item, Rect):
            x0, y0 = px((item.x, item.y))
            x1, y1 = px((item.x + item.w, item.y + item.h))
            if item.fill:
                draw.rectangle([x0, y0, x1, y1], fill=item.fill)
            if item.stroke:
                outline = [(item.x, item.y), (item.x + item.w, item.y),
                           (item.x + item.w, item.y + item.h), (item.x, item.y + item.h),
                           (item.x, item.y)]
                stroke(outline, item.stroke, item.stroke_width, item.dash)
        elif isinstance(item, Line):
            stroke(item.points, item.color, item.width, item.dash)
        elif isinstance(item, Poly):
            draw.polygon([px(p) for p in item.points], fill=item.fill)
        elif isinstance(item, Dot):
            cx, cy = px((item.x, item.y))
            r = item.r * k
            draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=item.fill)
        else:
            x, y = px((item.x, item.y))
            draw.text(
                (x, y), item.text, fill=item.color,
                font=_font(max(int(round(item.size * k)), 1)), anchor=_ANCHORS[item.anchor],
            )

    final = (max(int(round(scene.width * SCALE)), 1), max(int(round(scene.height * SCALE)), 1))
    img = img.resize(final, Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def render_diagram_png(diagram: Diagram, title: str = "") -> bytes:
    return render_scene_png(build_scene(diagram, title))
