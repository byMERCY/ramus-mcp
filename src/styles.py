"""The words a style is given in - colours, line kinds, block types - and the numbers Ramus keeps.

Colours are named the way Java names them (Ramus is a Java program, and its palette is Java's):
``green`` is pure green, as Ramus fills a box. Russian names mean the same colours. A colour
may also be given as ``#rrggbb``, ``#rgb`` or ``r,g,b``.
"""

from __future__ import annotations

from typing import Optional, Tuple

RGB = Tuple[int, int, int]

_NAMED = {
    "black": (0, 0, 0), "white": (255, 255, 255), "red": (255, 0, 0), "green": (0, 255, 0),
    "blue": (0, 0, 255), "yellow": (255, 255, 0), "cyan": (0, 255, 255),
    "magenta": (255, 0, 255), "orange": (255, 200, 0), "pink": (255, 175, 175),
    "gray": (128, 128, 128), "grey": (128, 128, 128), "lightgray": (192, 192, 192),
    "lightgrey": (192, 192, 192), "darkgray": (64, 64, 64), "darkgrey": (64, 64, 64),
    "teal": (0, 128, 128), "navy": (0, 0, 128), "purple": (128, 0, 128),
    "brown": (139, 69, 19), "darkgreen": (0, 128, 0), "darkred": (139, 0, 0),
    "lightblue": (173, 216, 230), "lightgreen": (144, 238, 144), "lightyellow": (255, 255, 224),
    "чёрный": (0, 0, 0), "черный": (0, 0, 0), "белый": (255, 255, 255),
    "красный": (255, 0, 0), "зелёный": (0, 255, 0), "зеленый": (0, 255, 0),
    "синий": (0, 0, 255), "жёлтый": (255, 255, 0), "желтый": (255, 255, 0),
    "голубой": (0, 255, 255), "бирюзовый": (0, 128, 128), "розовый": (255, 175, 175),
    "оранжевый": (255, 200, 0), "серый": (128, 128, 128), "фиолетовый": (128, 0, 128),
    "коричневый": (139, 69, 19), "тёмно-зелёный": (0, 128, 0), "темно-зеленый": (0, 128, 0),
    "светло-серый": (192, 192, 192), "тёмно-серый": (64, 64, 64), "темно-серый": (64, 64, 64),
}

# How a line is drawn: a dash pattern (dash, gap, ...) in units of the page, or None for solid.
LINES = {"solid": None, "dashed": (6.0, 4.0), "dotted": (1.5, 3.0),
         "dash_dot": (8.0, 3.0, 2.0, 3.0)}

# What Ramus calls an IDEF0 box's type (F_TYPE); it is a label, not a shape.
BLOCK_TYPES = {"complex_process": 0, "process": 1, "process_part": 2, "operation": 3,
               "action": 4}


class StyleError(ValueError):
    pass


def parse_color(value: object) -> RGB:
    """A colour from a name, ``#rrggbb``, ``#rgb`` or ``r,g,b``."""
    text = str(value or "").strip().lower().replace(" ", "")
    if text in _NAMED:
        return _NAMED[text]
    if text.startswith("rgb(") and text.endswith(")"):
        text = text[4:-1]
    if text.startswith("#"):
        digits = text[1:]
        if len(digits) == 3:
            digits = "".join(c * 2 for c in digits)
        if len(digits) == 6:
            try:
                return (int(digits[0:2], 16), int(digits[2:4], 16), int(digits[4:6], 16))
            except ValueError:
                pass
    parts = text.split(",")
    if len(parts) == 3:
        try:
            rgb = tuple(int(p) for p in parts)
            if all(0 <= c <= 255 for c in rgb):
                return rgb  # type: ignore[return-value]
        except ValueError:
            pass
    raise StyleError(f"{value!r} is not a colour: give a name (red, green, blue, black, "
                     f"teal, orange ... or in Russian), #rrggbb, or r,g,b.")


def argb(rgb: RGB) -> int:
    """A colour as Ramus stores a box's: java.awt.Color.getRGB, a signed 32-bit int."""
    value = (0xFF << 24) | (rgb[0] << 16) | (rgb[1] << 8) | rgb[2]
    return value - (1 << 32) if value >= 1 << 31 else value


def hex_of(rgb: RGB) -> str:
    return "#%02x%02x%02x" % rgb


def line_of(value: Optional[str]):
    """A line kind's dash pattern."""
    key = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if key not in LINES:
        raise StyleError(f"A line is {', '.join(LINES)}, not {value!r}.")
    return LINES[key]


def line_name(dash) -> str:
    """The name of a dash pattern, as near as one goes."""
    if not dash:
        return "solid"
    for name, pattern in LINES.items():
        if pattern is not None and tuple(pattern) == tuple(dash):
            return name
    return "dashed"
