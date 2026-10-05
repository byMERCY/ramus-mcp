"""Rasterise a Ramus diagram to PNG.

The SVG renderer is for a person to look at or save; this one exists so the agent can *see* a
diagram, because MCP shows the model raster images, not SVG. Same layout, drawn with Pillow.

Boxes only for now (milestone M1); arrows arrive with M2.
"""

from __future__ import annotations

import io
from typing import List, Optional

from PIL import Image, ImageDraw, ImageFont

try:
    from .ramus_rsf import Diagram, Activity
except ImportError:  # pragma: no cover - script execution
    from ramus_rsf import Diagram, Activity


MARGIN = 40.0
SCALE = 2.0  # model units -> pixels; 2x keeps text crisp without a huge image
FONT_SIZE = 11
NUMBER_FONT_SIZE = 10
TITLE_FONT_SIZE = 12
LINE_GAP = 2  # extra pixels between wrapped lines


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    """A real TrueType face, so Cyrillic renders and text is not the PIL bitmap default."""
    for name in ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _text_width(draw: ImageDraw.ImageDraw, text: str, font) -> float:
    return draw.textlength(text, font=font)


def _wrap(draw, name: str, font, box_px_width: float) -> List[str]:
    usable = max(box_px_width - 10 * SCALE, 1)
    words = name.split()
    if not words:
        return [""]
    lines: List[str] = []
    line = words[0]
    for word in words[1:]:
        trial = line + " " + word
        if _text_width(draw, trial, font) <= usable:
            line = trial
        else:
            lines.append(line)
            line = word
    lines.append(line)
    return lines


def render_diagram_png(diagram: Diagram, title: str = "") -> bytes:
    boxes = diagram.activities
    if boxes:
        min_x = min(a.x for a in boxes)
        min_y = min(a.y for a in boxes)
        max_x = max(a.x + a.width for a in boxes)
        max_y = max(a.y + a.height for a in boxes)
    else:
        min_x = min_y = 0.0
        max_x = max_y = 100.0

    width_px = int(((max_x - min_x) + 2 * MARGIN) * SCALE)
    height_px = int(((max_y - min_y) + 2 * MARGIN) * SCALE)

    def px(x: float) -> float:
        return (x - min_x + MARGIN) * SCALE

    def py(y: float) -> float:
        return (y - min_y + MARGIN) * SCALE

    img = Image.new("RGB", (max(width_px, 1), max(height_px, 1)), "#ffffff")
    draw = ImageDraw.Draw(img)

    name_font = _load_font(int(FONT_SIZE * SCALE))
    num_font = _load_font(int(NUMBER_FONT_SIZE * SCALE))
    title_font = _load_font(int(TITLE_FONT_SIZE * SCALE))

    heading = title or (
        f"Decomposition of: {diagram.parent_name}" if diagram.parent_name else "Diagram"
    )
    draw.text((8 * SCALE, 8 * SCALE), heading, fill="#888888", font=title_font)

    line_h = (FONT_SIZE * SCALE) + LINE_GAP

    for a in boxes:
        x0, y0 = px(a.x), py(a.y)
        x1, y1 = px(a.x + a.width), py(a.y + a.height)
        draw.rectangle([x0, y0, x1, y1], fill="#ffffff", outline="#1a1a1a", width=2)

        lines = _wrap(draw, a.name, name_font, x1 - x0)
        block_h = len(lines) * line_h
        cy = (y0 + y1) / 2 - block_h / 2
        cx = (x0 + x1) / 2
        for i, line in enumerate(lines):
            w = _text_width(draw, line, name_font)
            draw.text((cx - w / 2, cy + i * line_h), line, fill="#111111", font=name_font)

        if a.number:
            nw = _text_width(draw, a.number, num_font)
            draw.text(
                (x1 - nw - 5 * SCALE, y1 - (NUMBER_FONT_SIZE * SCALE) - 4 * SCALE),
                a.number,
                fill="#555555",
                font=num_font,
            )

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
