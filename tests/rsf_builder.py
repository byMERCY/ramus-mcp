"""Builds small .rsf files for tests, so they do not depend on anyone's models.

A .rsf is a zip of table dumps (see ramus_rsf); this writes the handful of tables a diagram
needs, in the newer ("version 2") layout where arrow routes live in ``sector_points`` rather
than inside the diagram blob. Binary cells go through the same byte-plus-128 masking the real
files use. It also has a tiny little-endian writer for hand-made blobs.
"""

from __future__ import annotations

import struct
import zipfile
from typing import Dict, Iterable, List, Optional, Sequence
from xml.sax.saxutils import escape


class Writer:
    """Little-endian primitives, the mirror of visual_data's reader."""

    def __init__(self) -> None:
        self.buf = bytearray()

    def i32(self, v: int) -> "Writer":
        self.buf += struct.pack("<i", v)
        return self

    def i64(self, v: int) -> "Writer":
        self.buf += struct.pack("<q", v)
        return self

    def f64(self, v: float) -> "Writer":
        self.buf += struct.pack("<d", v)
        return self

    def flag(self, v: bool) -> "Writer":
        self.buf.append(1 if v else 0)
        return self

    def string(self, v: Optional[str]) -> "Writer":
        if v is None:
            return self.i32(-1)
        raw = v.encode("utf-8")
        self.i32(len(raw))
        self.buf += raw
        return self

    def raw(self, data: bytes) -> "Writer":
        self.buf += data
        return self

    def bytes(self) -> bytes:
        return bytes(self.buf)


def mask(data: bytes) -> str:
    """Real bytes -> the hex text a table dump holds (each byte stored as byte + 128)."""
    return bytes(b ^ 0x80 for b in data).hex().upper()


def stroke_font_color(width: float = 1.5, rgb=(0, 0, 0), font=("Dialog", 10, 0)) -> bytes:
    """A sector's VISUAL_ATTRIBUTES: an explicit stroke, a new font, a new colour."""
    w = Writer()
    w.flag(True).f64(width).i32(2).i32(0).f64(0.0).f64(10.0).i32(-1)  # stroke, no dashes
    w.flag(False).flag(True).string(font[0]).i32(font[1]).i32(font[2])  # font
    w.flag(True).i32(rgb[0]).i32(rgb[1]).i32(rgb[2])  # colour
    return w.bytes()


class Tables:
    """Collects table dumps and writes them as a zip."""

    def __init__(self) -> None:
        self.tables: Dict[str, tuple] = {}

    def add(self, name: str, columns: Sequence[str], rows: Iterable[Sequence]) -> None:
        self.tables[name] = (list(columns), [list(r) for r in rows])

    def write(self, path: str) -> None:
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            for name, (columns, rows) in self.tables.items():
                z.writestr("data/" + name + ".xml", self._xml(columns, rows))

    @staticmethod
    def _xml(columns: List[str], rows: List[List]) -> str:
        out = ["<?xml version=\"1.0\" encoding=\"UTF-8\"?>", "<table><fields>"]
        for i, c in enumerate(columns):
            out.append(f'<field id="{i}" name="{c}"/>')
        out.append("</fields><data>")
        for row in rows:
            cells = []
            for i, v in enumerate(row):
                cells.append(f'<f id="{i}"/>' if v is None else f'<f id="{i}">{escape(str(v))}</f>')
            out.append("<row>" + "".join(cells) + "</row>")
        out.append("</data></table>")
        return "".join(out)


# The attribute ids are deliberately not the ones real files use: the reader must find
# everything by name.
ATTR = {
    "F_VISUAL_DATA": 20, "F_BACKGROUND": 22, "F_FOREGROUND": 23, "F_BOUNDS": 24, "F_FONT": 25,
    "F_TYPE": 27, "F_FUNCTION_SECTOR": 35, "F_SECTOR_STREAM": 36, "F_SECTOR_POINTS": 37,
    "F_SECTOR_PROPERTIES": 38, "F_STREAM_NAME": 39, "F_SECTOR_ATTRIBUTE": 40,
    "F_SECTOR_BORDER_START": 41, "F_SECTOR_BORDER_END": 42, "F_STREAM_ADDED": 43,
    "F_BASE_FUNCTION_QUALIFIER_ID": 44, "NAME": 55,
}
Q_BASE, Q_SECTORS, Q_STREAMS, Q_MODEL, Q_CATALOG = 8, 6, 7, 14, 15

# Element ids of the fixture model.
BASE, TOP, C1, C2, GONE, G1 = 1, 10, 11, 12, 13, 14
S_DATA, S_RESULT, S_RULES = 20, 21, 22
CAT_NORMS, CAT_RULES = 50, 51

# The fixture's boxes: (id, x, y, w, h)
BOXES = {
    TOP: (324.0, 180.0, 144.0, 80.0),
    C1: (120.0, 70.0, 100.0, 60.0),
    C2: (300.0, 120.0, 100.0, 60.0),
    GONE: (500.0, 300.0, 80.0, 40.0),
    G1: (200.0, 100.0, 90.0, 50.0),
}


def tiny_model(path: str) -> None:
    """A two-level model: A0 = TOP decomposed into A1 = C1 (itself decomposed) and A2 = C2.

    The A0 diagram carries every kind of arrow end: a frame entry, an output that forks at a
    junction to a box and to the frame, a control arrow whose stream has no name of its own
    (so it is labelled by the catalog items attached to it), an arrow left open, a stub Ramus
    never drew, and a sector that was deleted.
    """
    t = Tables()
    t.add("attributes", ["ATTRIBUTE_ID", "ATTRIBUTE_NAME", "CREATED_BRANCH_ID", "REMOVED_BRANCH_ID"],
          [(i, n, 0, 2147483647) for n, i in ATTR.items()])
    t.add("qualifiers", ["QUALIFIER_ID", "QUALIFIER_NAME", "ATTRIBUTE_FOR_NAME", "CREATED_BRANCH_ID", "REMOVED_BRANCH_ID"], [
        (Q_BASE, "F_BASE_FUNCTIONS", -1, 0, 2147483647),
        (Q_SECTORS, "F_SECTORS", -1, 0, 2147483647),
        (Q_STREAMS, "F_STREAMS", ATTR["F_STREAM_NAME"], 0, 2147483647),
        (Q_MODEL, "Работы", ATTR["NAME"], 0, 2147483647),
        (Q_CATALOG, "Справочник", ATTR["NAME"], 0, 2147483647),
    ])

    elements = [
        (BASE, Q_BASE), (TOP, Q_MODEL), (C1, Q_MODEL), (C2, Q_MODEL), (G1, Q_MODEL),
        (S_DATA, Q_STREAMS), (S_RESULT, Q_STREAMS), (S_RULES, Q_STREAMS),
        (CAT_NORMS, Q_CATALOG), (CAT_RULES, Q_CATALOG),
    ]
    rows = [(e, None, q, 0, 2147483647) for e, q in elements]
    rows.append((GONE, None, Q_MODEL, 0, 0))  # a deleted box: still in the tables, marked removed
    sectors = list(range(30, 41))
    rows += [(s, None, Q_SECTORS, 0, 2147483647) for s in sectors if s != 38]
    rows.append((38, None, Q_SECTORS, 0, 0))  # a deleted arrow segment
    t.add("elements", ["ELEMENT_ID", "ELEMENT_NAME", "QUALIFIER_ID", "CREATED_BRANCH_ID", "REMOVED_BRANCH_ID"], rows)

    names = {TOP: "Сделать дело", C1: "Подготовить", C2: "Выполнить", GONE: "Удалённая", G1: "Собрать",
             CAT_NORMS: "Нормы", CAT_RULES: "Правила"}
    texts = [(e, ATTR["NAME"], n) for e, n in names.items()]
    texts += [(S_DATA, ATTR["F_STREAM_NAME"], "данные"), (S_RESULT, ATTR["F_STREAM_NAME"], "результат"),
              (S_RULES, ATTR["F_STREAM_NAME"], None)]
    t.add("Core/attribute_texts", ["ELEMENT_ID", "ATTRIBUTE_ID", "VALUE", "VALUE_BRANCH_ID"],
          [(e, a, v, 0) for e, a, v in texts])

    # Siblings are chained by "previous": C1, then the deleted box, then C2 - so C2 names a
    # sibling that no longer exists and must be placed by following the chain through it.
    t.add("Core/attribute_hierarchicals", ["ELEMENT_ID", "PARENT_ELEMENT_ID", "PREVIOUS_ELEMENT_ID"], [
        (BASE, 0, 0), (TOP, -1, -1), (C1, TOP, -1), (GONE, TOP, C1), (C2, TOP, GONE), (G1, C1, -1),
    ])
    t.add("Core/attribute_longs", ["ELEMENT_ID", "ATTRIBUTE_ID", "VALUE"],
          [(BASE, ATTR["F_BASE_FUNCTION_QUALIFIER_ID"], Q_MODEL)])
    t.add("IDEF0/attribute_rectangles", ["ELEMENT_ID", "ATTRIBUTE_ID", "X", "Y", "WIDTH", "HEIGHT"],
          [(e, ATTR["F_BOUNDS"], x, y, w, h) for e, (x, y, w, h) in BOXES.items()])
    t.add("IDEF0/attribute_function_types", ["ELEMENT_ID", "ATTRIBUTE_ID", "TYPE"],
          [(e, ATTR["F_TYPE"], 1) for e in BOXES])
    t.add("IDEF0/attribute_colors", ["ELEMENT_ID", "ATTRIBUTE_ID", "COLOR"],
          [(C1, ATTR["F_BACKGROUND"], -16711936), (C1, ATTR["F_FOREGROUND"], -16777216)])
    t.add("IDEF0/attribute_fonts", ["ELEMENT_ID", "ATTRIBUTE_ID", "NAME", "SIZE", "STYLE"],
          [(e, ATTR["F_FONT"], "Dialog", 10, 0) for e in BOXES])

    # sector -> (owner diagram, stream)
    links = {
        30: (TOP, S_DATA), 31: (TOP, S_RESULT), 32: (TOP, S_RESULT), 33: (TOP, S_RESULT),
        34: (TOP, S_RULES), 35: (TOP, S_DATA), 36: (TOP, S_DATA), 37: (C1, S_DATA), 38: (TOP, S_DATA),
        39: (BASE, S_DATA), 40: (C1, S_RESULT),
    }
    t.add("Core/attribute_other_elements", ["ELEMENT_ID", "ATTRIBUTE_ID", "OTHER_ELEMENT"],
          [(s, ATTR["F_FUNCTION_SECTOR"], o) for s, (o, _) in links.items()]
          + [(s, ATTR["F_SECTOR_STREAM"], st) for s, (_, st) in links.items()])
    t.add("IDEF0/attribute_any_to_any_elements", ["ELEMENT_ID", "ATTRIBUTE_ID", "OTHER_ELEMENT"],
          [(S_RULES, ATTR["F_STREAM_ADDED"], CAT_NORMS), (S_RULES, ATTR["F_STREAM_ADDED"], CAT_RULES)])

    blue = mask(stroke_font_color(1.5, (0, 0, 255)))
    plain = mask(stroke_font_color(0.5, (0, 0, 0)))
    t.add("IDEF0/attribute_sectors",
          ["ELEMENT_ID", "ATTRIBUTE_ID", "CREATE_STATE", "CREATE_POS", "SHOW_TEXT", "ALTERNATIVE_TEXT",
           "VISUAL_ATTRIBUTES"], [
        (30, ATTR["F_SECTOR_ATTRIBUTE"], -1, 0.0, 1, None, blue),
        (31, ATTR["F_SECTOR_ATTRIBUTE"], -1, 0.0, 1, None, plain),
        (32, ATTR["F_SECTOR_ATTRIBUTE"], -1, 0.0, 1, None, plain),
        (33, ATTR["F_SECTOR_ATTRIBUTE"], -1, 0.0, 1, None, plain),
        (34, ATTR["F_SECTOR_ATTRIBUTE"], -1, 0.0, 1, None, plain),
        (35, ATTR["F_SECTOR_ATTRIBUTE"], -1, 0.0, 1, "другое имя", plain),
        (36, ATTR["F_SECTOR_ATTRIBUTE"], 1, 200.0, 1, None, plain),  # a stub: never drawn
        (37, ATTR["F_SECTOR_ATTRIBUTE"], -1, 0.0, 1, None, plain),
        (38, ATTR["F_SECTOR_ATTRIBUTE"], -1, 0.0, 1, None, plain),
        (39, ATTR["F_SECTOR_ATTRIBUTE"], -1, 0.0, 1, None, plain),
        (40, ATTR["F_SECTOR_ATTRIBUTE"], -1, 0.0, 1, None, plain),
    ])

    # Border helpers. frame sides: 0 right, 1 bottom, 2 left, 3 top.
    def border(sector, attr, border_type=-1, fn=-1, side=-1, cp=-1, tunnel=0):
        return (sector, ATTR[attr], border_type, fn, side, cp, tunnel)

    S, E = "F_SECTOR_BORDER_START", "F_SECTOR_BORDER_END"
    t.add("IDEF0/attribute_sector_borders",
          ["ELEMENT_ID", "ATTRIBUTE_ID", "BORDER_TYPE", "FUNCTION", "FUNCTION_TYPE", "CROSSPOINT", "TUNNEL_SOFT"], [
        border(30, S, border_type=2, cp=100), border(30, E, fn=C1, side=2, cp=101, tunnel=1),         # frame left -> C1 input
        border(31, S, fn=C1, side=0, cp=102), border(31, E, cp=110),                        # C1 output -> junction
        border(32, S, cp=110), border(32, E, fn=C2, side=2, cp=103),                        # junction -> C2 input
        border(33, S, cp=110), border(33, E, border_type=0, cp=104),                        # junction -> frame right
        border(34, S, border_type=3, cp=105), border(34, E, fn=C2, side=3, cp=106),         # frame top -> C2 control
        border(35, S, fn=C2, side=0, cp=107),                                               # C2 output -> nothing
        border(36, S, border_type=2),                                                       # stub from frame left
        border(37, S, border_type=2, cp=108), border(37, E, fn=G1, side=2, cp=109),
        border(38, S, border_type=2, cp=120), border(38, E, border_type=0, cp=121),
        border(39, S, fn=TOP, side=0, cp=130), border(39, E, border_type=0, cp=131),
        border(40, S, fn=G1, side=0, cp=140), border(40, E, border_type=0, cp=102),         # continues 31's start
    ])

    def pts(sector, *points):
        return [(sector, ATTR["F_SECTOR_POINTS"], i, x, y, 1000 * sector + 2 * i, 1000 * sector + 2 * i + 1, -1)
                for i, (x, y) in enumerate(points)]

    points = (
        pts(30, (7.0, 100.0), (120.0, 100.0))
        + pts(31, (220.0, 100.0), (260.0, 100.0))
        + pts(32, (260.0, 100.0), (260.0, 150.0), (300.0, 150.0))
        + pts(33, (260.0, 100.0), (793.0, 100.0))
        + pts(34, (350.0, 7.0), (350.0, 120.0))
        + pts(35, (400.0, 150.0), (450.0, 150.0))
        + pts(37, (7.0, 125.0), (200.0, 125.0))
        + pts(39, (468.0, 220.0), (793.0, 220.0))
        + pts(40, (290.0, 125.0), (793.0, 125.0))
    )
    t.add("IDEF0/attribute_sector_points",
          ["ELEMENT_ID", "ATTRIBUTE_ID", "POSITION", "X_POSITION", "Y_POSITION", "X_ORDINATE_ID",
           "Y_ORDINATE_ID", "POINT_TYPE"], points)
    # Labels: 30 has one with a zig-zag; 31 shows none; the rest use the defaults.
    t.add("IDEF0/attribute_sector_properties",
          ["ELEMENT_ID", "ATTRIBUTE_ID", "SHOW_TEXT", "SHOW_TILDA", "TEXT_X", "TEXT_Y", "TEXT_WIDTH",
           "TEXT_HIEGHT", "TILDA_POS", "TRANSPARENT"], [
        (30, ATTR["F_SECTOR_PROPERTIES"], 1, 1, 30.0, 60.0, 40.0, 9.8, 0.5, 1),
        (31, ATTR["F_SECTOR_PROPERTIES"], 0, 0, 0.0, 0.0, 0.0, 0.0, 0.5, 1),
        (34, ATTR["F_SECTOR_PROPERTIES"], 1, 0, 360.0, 40.0, 60.0, 19.6, 0.5, 1),
    ])

    # Version-2 blobs hold only free texts: none for the base, one for TOP.
    base_blob = Writer().i32(2).i32(0).bytes()
    top_blob = (Writer().i32(2).i32(1)
                .flag(False).flag(True).string("Arial").i32(10).i32(0)  # font, new
                .flag(True).i32(255).i32(0).i32(0)                      # colour, new
                .f64(20.0).f64(300.0).f64(200.0).f64(40.0).string("Цель: проверка").bytes())
    t.add("IDEF0/attribute_visual_datas", ["ELEMENT_ID", "ATTRIBUTE_ID", "DATA"],
          [(BASE, ATTR["F_VISUAL_DATA"], mask(base_blob)), (TOP, ATTR["F_VISUAL_DATA"], mask(top_blob))])
    t.write(path)
