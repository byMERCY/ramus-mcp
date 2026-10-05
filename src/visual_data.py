"""Decoders for the binary payloads inside a Ramus .rsf file.

Two table columns hold small binary records instead of plain values:

* ``VISUAL_ATTRIBUTES`` of a sector (an arrow segment) - how the line is drawn: stroke, font,
  colour.
* ``DATA`` of a diagram's visual-data row - the diagram's arrow geometry and its free text
  labels.

Both are written the same way, and this module reads that way and nothing else:

* The table dump spells a byte array as hex text, and each byte is stored as ``byte + 128``
  (mod 256) - i.e. XOR 0x80 - so a zero byte is ``80`` and an ``FF`` is ``7F``. :func:`unmask`
  undoes that.
* After that the bytes are little-endian: a 32-bit int, a 64-bit IEEE double, a one-byte
  boolean, and a string as an int length (-1 = null) followed by UTF-8.
* Fonts, colours, strokes and the two coordinates of a point are *interned*: the first time
  one appears it is written out in full and numbered; afterwards only its number is written.
  The numbering lives for one blob (:class:`_Memory`).

The diagram blob comes in two versions (its first int). Version 1 - Ramus 2.x - carries every
arrow's route inline. Version 2 - Ramus 3.x - moved the routes into the ``sector_points`` and
``sector_properties`` tables and left only the free text labels in the blob, so for it
:func:`decode_diagram_blob` returns no sectors and the caller reads the tables.

It is written against the file format only; no Ramus code is used.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import List, Optional, Tuple


class BlobError(ValueError):
    """The bytes do not follow the layout - truncated, or not what was expected."""


_UNMASK = bytes(b ^ 0x80 for b in range(256))


def unmask(text: str) -> bytes:
    """A table-dump hex string -> the real bytes (see the module note on the +128 offset)."""
    try:
        return bytes.fromhex(text.strip()).translate(_UNMASK)
    except ValueError as exc:
        raise BlobError(f"not a hex byte string: {exc}") from None


# ------------------------------------------------------------------------ reading primitives


@dataclass(frozen=True)
class Font:
    name: str
    size: int
    style: int  # java.awt.Font style flags: 0 plain, 1 bold, 2 italic


@dataclass(frozen=True)
class Color:
    r: int
    g: int
    b: int

    def hex(self) -> str:
        return f"#{self.r & 255:02x}{self.g & 255:02x}{self.b & 255:02x}"


@dataclass(frozen=True)
class Stroke:
    """How a line is drawn. ``kind`` is "basic" for an explicit width, or "way"/"arrowed" for
    the two decorated styles Ramus offers (``level`` says which of its three sizes)."""

    width: float = 1.0
    cap: int = 0  # 0 butt, 1 round, 2 square
    join: int = 0  # 0 miter, 1 round, 2 bevel
    dash_phase: float = 0.0
    miter_limit: float = 10.0
    dash: Optional[Tuple[float, ...]] = None
    kind: str = "basic"
    level: int = 0


@dataclass
class _Memory:
    """What a blob has defined so far, for the back-references that follow."""

    fonts: List[Optional[Font]] = field(default_factory=list)
    colors: List[Color] = field(default_factory=list)
    strokes: List[Stroke] = field(default_factory=list)
    ordinates: List[Tuple[int, float]] = field(default_factory=list)  # (axis, position)


class _Cursor:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    @property
    def remaining(self) -> int:
        return len(self.data) - self.pos

    def take(self, n: int) -> bytes:
        if n < 0 or n > self.remaining:
            raise BlobError(f"wanted {n} bytes at offset {self.pos}, {self.remaining} left")
        chunk = self.data[self.pos : self.pos + n]
        self.pos += n
        return chunk

    def i32(self) -> int:
        return struct.unpack("<i", self.take(4))[0]

    def i64(self) -> int:
        return struct.unpack("<q", self.take(8))[0]

    def f64(self) -> float:
        return struct.unpack("<d", self.take(8))[0]

    def flag(self) -> bool:
        return self.take(1)[0] != 0

    def string(self) -> Optional[str]:
        n = self.i32()
        if n == -1:
            return None
        if n < 0:
            raise BlobError(f"negative string length {n} at offset {self.pos - 4}")
        return self.take(n).decode("utf-8", errors="replace")


def _read_font(c: _Cursor, mem: _Memory) -> Optional[Font]:
    if c.flag():  # "is null"
        return None
    if c.flag():  # "is new"
        font = Font(name=c.string() or "", size=c.i32(), style=c.i32())
        mem.fonts.append(font)
        return font
    return _recall(mem.fonts, c.i32(), "font")


def _read_color(c: _Cursor, mem: _Memory) -> Color:
    if c.flag():  # "is new"
        color = Color(c.i32(), c.i32(), c.i32())
        mem.colors.append(color)
        return color
    return _recall(mem.colors, c.i32(), "colour")


# The decorated strokes are stored as a negative code instead of a width.
_DECORATED = {-10: ("way", 0), -11: ("way", 1), -12: ("way", 2),
              -20: ("arrowed", 0), -21: ("arrowed", 1), -22: ("arrowed", 2)}


def _read_stroke(c: _Cursor, mem: _Memory) -> Stroke:
    if c.flag():  # "is new": an explicit java.awt.BasicStroke
        width = c.f64()
        cap = c.i32()
        join = c.i32()
        phase = c.f64()
        miter = c.f64()
        n = c.i32()
        dash = tuple(c.f64() for _ in range(n)) if n > 0 else None
        stroke = Stroke(width, cap, join, phase, miter, dash)
        mem.strokes.append(stroke)
        return stroke
    code = c.i32()
    if code in _DECORATED:
        kind, level = _DECORATED[code]
        return Stroke(kind=kind, level=level)
    if 0 <= code < len(mem.strokes):
        return mem.strokes[code]
    return Stroke()  # an index nothing defined: Ramus falls back to a default stroke


def _read_ordinate(c: _Cursor, mem: _Memory) -> Tuple[int, float]:
    if c.flag():  # "is new": axis (0 = x, 1 = y) then the position
        ordinate = (c.i32(), c.f64())
        mem.ordinates.append(ordinate)
        return ordinate
    return _recall(mem.ordinates, c.i32(), "coordinate")


def _read_rect(c: _Cursor) -> Tuple[float, float, float, float]:
    return c.f64(), c.f64(), c.f64(), c.f64()


def _read_global_id(c: _Cursor) -> Optional[int]:
    """An element reference: a model number then the id, or nothing."""
    model = c.i32()
    if model < 0:
        return None
    if model == 0:
        return c.i32()
    if model == 1:
        return c.i64()
    raise BlobError(f"unknown element-reference kind {model} at offset {c.pos - 4}")


def _recall(items: list, index: int, what: str):
    if not 0 <= index < len(items):
        raise BlobError(f"back-reference to {what} #{index}, only {len(items)} defined")
    return items[index]


# ------------------------------------------------------------------------------ sector style


@dataclass(frozen=True)
class SectorStyle:
    """How one arrow segment is drawn. Any part the file leaves out stays None."""

    stroke: Optional[Stroke] = None
    font: Optional[Font] = None
    color: Optional[Color] = None


def decode_sector_style(data: bytes) -> SectorStyle:
    """The ``VISUAL_ATTRIBUTES`` of a sector: a stroke, a font, a colour, in that order.

    An empty payload means "all defaults". A payload that does not parse is treated the same
    way rather than failing a whole diagram over one line's colour.
    """
    if not data:
        return SectorStyle()
    c = _Cursor(data)
    mem = _Memory()
    try:
        stroke = _read_stroke(c, mem)
        font = _read_font(c, mem)
        color = _read_color(c, mem)
    except (BlobError, struct.error):
        return SectorStyle()
    return SectorStyle(stroke, font, color)


# ----------------------------------------------------------------------------- diagram blob


@dataclass
class PointRecord:
    x: float
    y: float
    kind: int  # which axis the line leaving this point runs along; -1 when not recorded


@dataclass
class LabelRecord:
    """Where an arrow's name is written."""

    x: float
    y: float
    width: float
    height: float
    transparent: bool
    tilde_pos: Optional[float]  # 0..1 along the route where the zig-zag joins; None if none


@dataclass
class SectorRecord:
    sector_id: Optional[int]
    show_tilda: bool
    points: List[PointRecord]
    label: Optional[LabelRecord]


@dataclass
class TextRecord:
    """A free text annotation placed on the diagram."""

    font: Optional[Font]
    color: Color
    x: float
    y: float
    width: float
    height: float
    text: Optional[str]


@dataclass
class DiagramBlob:
    version: int
    sectors: List[SectorRecord] = field(default_factory=list)
    texts: List[TextRecord] = field(default_factory=list)


def decode_diagram_blob(data: bytes, strict: bool = False) -> DiagramBlob:
    """The ``DATA`` of a diagram's visual-data row.

    A payload too short to hold a version is "no data" (a diagram never opened and saved in
    Ramus has none). With ``strict`` any leftover byte after the layout is an error - useful
    in tests, where it proves the layout was understood to the last byte.
    """
    if len(data) < 4:
        return DiagramBlob(version=0)
    c = _Cursor(data)
    mem = _Memory()
    version = c.i32()
    blob = DiagramBlob(version=version)
    if version <= 0:
        return blob

    if version == 1:
        count = c.i32()
        if count < 0 or count > c.remaining:
            raise BlobError(f"implausible sector count {count}")
        for _ in range(count):
            blob.sectors.append(_read_sector(c, mem))
    # Version 2 and later keep the routes in tables; only the text labels follow the version.

    count = c.i32()
    if count < 0 or count > c.remaining:
        raise BlobError(f"implausible text count {count}")
    for _ in range(count):
        font = _read_font(c, mem)
        color = _read_color(c, mem)
        x, y, w, h = _read_rect(c)
        blob.texts.append(TextRecord(font, color, x, y, w, h, c.string()))

    if strict and c.remaining:
        raise BlobError(f"{c.remaining} unread bytes after the layout")
    return blob


def _read_sector(c: _Cursor, mem: _Memory) -> SectorRecord:
    c.string()  # two strings older versions kept here; both are empty or null in practice
    c.string()
    show_tilda = c.flag()
    n_points = c.i32()
    if n_points < 0 or n_points > c.remaining:
        raise BlobError(f"implausible point count {n_points} at offset {c.pos - 4}")
    points: List[PointRecord] = []
    for _ in range(n_points):
        axis_x, x = _read_ordinate(c, mem)
        axis_y, y = _read_ordinate(c, mem)
        if (axis_x, axis_y) != (0, 1):
            raise BlobError(f"point coordinates are not (x, y) at offset {c.pos}")
        kind = c.i32()
        c.i32()  # an old border-type field, always -1
        if _read_global_id(c) is not None:  # an old function reference, then its side
            c.i32()
        points.append(PointRecord(x, y, kind))
    sector_id = _read_global_id(c)
    label: Optional[LabelRecord] = None
    if not c.flag():  # "no label" is the flag; false means there is one
        x, y, w, h = _read_rect(c)
        transparent = c.flag()
        tilde = c.f64() if show_tilda else None
        label = LabelRecord(x, y, w, h, transparent, tilde)
    return SectorRecord(sector_id, show_tilda, points, label)
