"""Reader for Ramus .rsf business-process model files.

A .rsf file is a ZIP archive whose ``data/**/*.xml`` members are dumps of the tables
Ramus keeps a model in. Each dump is the same shape::

    <table ...>
      <fields><field id="0" name="ELEMENT_ID" .../> ...</fields>
      <data>
        <row><f id="0">79</f><f id="1">...</f> ...</row>
        ...
      </data>
    </table>

This module reads those dumps and assembles what an IDEF0/DFD diagram is made of: the
activities (boxes) and the activity tree, the arrows between them with the geometry of each
route, and the free text labels. It is written against the file format only - it borrows no
code from Ramus - so it can read a model without the application and under any license.

A few facts about the format that the assembly relies on (all checked against real models):

* Attribute and qualifier ids are not fixed - the same attribute has a different id in each
  file - so everything is looked up by *name* (``F_SECTOR_STREAM``, ``F_SECTORS`` ...).
* Ramus 3 keeps deleted elements in the tables, marked by ``REMOVED_BRANCH_ID``; only the
  live ones belong to the model.
* An element's name lives in the text attribute its qualifier designates
  (``ATTRIBUTE_FOR_NAME``); older files also repeat it in ``ELEMENT_NAME``.
* An arrow is a *stream* (a named flow) drawn as one or more *sectors* (segments). A sector
  has two ends; each end meets an activity side, the diagram frame, or a *crosspoint* node it
  shares with other sectors - and a node shared with a sector of another diagram is how an
  arrow continues from a parent diagram into the child that decomposes the box it touches.
* The route of a sector is stored in one of two ways depending on the Ramus version that last
  saved the file - see :mod:`visual_data`.
"""

from __future__ import annotations

import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set, Tuple

try:
    from . import visual_data as vd
except ImportError:  # pragma: no cover - script execution
    import visual_data as vd


# The four sides of a box or of the diagram frame, in the order the file numbers them, and
# the ICOM role an arrow plays when it touches that side.
SIDES = ("right", "bottom", "left", "top")
ROLES = ("output", "mechanism", "input", "control")

# How a decomposition is drawn (F_DECOMPOSITION_TYPE of the decomposed function; nothing
# stored, or anything else, is IDEF0), and what a box on it is (F_TYPE: below 1001 an
# activity - a process on a data flow diagram - else one of the data flow diagram's objects).
NOTATIONS = {1: "dfd", 2: "dfds"}
KINDS = {1001: "external", 1002: "store", 1003: "role"}

# Where the diagram frame sits, in model units. It is not stored anywhere: it is what every
# arrow that lands on the frame is found to use. Left, top and right are the same in every
# model seen (7, 7, 793); the bottom depends on the Ramus version that drew the page - the
# newer one has a taller page. :meth:`RsfModel.frame` prefers what the arrows themselves say.
FRAME_LEFT, FRAME_TOP, FRAME_RIGHT = 7.0, 7.0, 793.0
FRAME_BOTTOM_RAMUS2 = 419.4
FRAME_BOTTOM_RAMUS3 = 437.0
Frame = Tuple[float, float, float, float]  # left, top, right, bottom

_NO_REMOVAL = 2147483647  # REMOVED_BRANCH_ID of an element that was never deleted


def _parse_table(data: bytes) -> List[Dict[str, Optional[str]]]:
    """One table dump -> a list of rows, each a {column name: value} dict.

    A self-closing ``<f/>`` is an empty cell and comes back as ``None``; the caller decides
    whether that means zero, absent, or an empty string. A column a row does not mention at
    all is simply not in its dict.
    """
    root = ET.fromstring(data)
    columns: Dict[str, str] = {}
    fields_el = root.find("fields")
    if fields_el is not None:
        for fe in fields_el.findall("field"):
            columns[fe.get("id")] = fe.get("name")

    rows: List[Dict[str, Optional[str]]] = []
    data_el = root.find("data")
    if data_el is None:
        return rows
    for row in data_el.findall("row"):
        rec: Dict[str, Optional[str]] = {}
        for cell in row.findall("f"):
            rec[columns.get(cell.get("id"), cell.get("id"))] = cell.text
        rows.append(rec)
    return rows


def _num(value: Optional[str], default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _int(value: Optional[str], default: int = 0) -> int:
    return int(_num(value, default))


# --------------------------------------------------------------------------------- the model


@dataclass
class Activity:
    """One IDEF0 activity (a box), with where it sits on its parent's diagram."""

    element_id: int
    name: str
    parent_id: int
    previous_id: int
    x: float = 0.0
    y: float = 0.0
    width: float = 0.0
    height: float = 0.0
    number: str = ""  # IDEF0 node number, e.g. "A0", "A1", "A12"
    type: Optional[int] = None  # Ramus function type: 1 process, 3 operation, ...
    fill: Optional[str] = None  # background colour "#rrggbb"; None = white
    color: Optional[str] = None  # outline and text colour; None = black
    font_size: float = 12.0
    kind: str = "process"  # "process", or on a data flow diagram "external", "store", "role"
    decomposed: bool = False  # has a decomposition of its own
    decomposition: str = "idef0"  # how that decomposition is drawn: "idef0", "dfd", "dfds"
    long_name: str = ""  # the second part of a DFDS name, written small under the first
    owner_id: Optional[int] = None  # a DFDS role: the box it belongs to

    @property
    def has_box(self) -> bool:
        return self.width > 0 and self.height > 0


@dataclass
class End:
    """What one end of an arrow segment is attached to.

    ``kind`` is "activity" (a side of a box), "frame" (a side of the diagram - an arrow
    entering or leaving the parent function), "junction" (a node shared with other segments
    of the same flow) or "open" (attached to nothing). ``role`` is the ICOM role the side
    stands for. ``tunnel`` is "hard" or "soft" when the end is drawn in tunnel brackets - the
    arrow is deliberately not carried to the other level.
    """

    kind: str
    activity_id: Optional[int] = None
    side: Optional[str] = None
    role: Optional[str] = None
    node: Optional[int] = None
    tunnel: Optional[str] = None


@dataclass
class Label:
    """Where an arrow's name is written, and the zig-zag that ties it back to the line."""

    x: float
    y: float
    width: float
    height: float
    transparent: bool = True
    tilde_pos: Optional[float] = None  # 0..1 along the route; None = no zig-zag


@dataclass
class Arrow:
    """One segment of an arrow: a route from one end to the other.

    ``geometry`` says where the route came from: "stored" (the file has it), or "stub" for a
    segment Ramus had not drawn yet - an arrow inherited from the parent diagram that was
    never opened - whose short stand-in route is made up here.
    """

    sector_id: int
    stream_id: Optional[int]
    name: str
    start: End
    end: End
    points: List[Tuple[float, float]]
    geometry: str = "stored"
    label: Optional[Label] = None
    color: Optional[str] = None  # "#rrggbb"; None = the default black
    width: float = 1.0
    dash: Optional[Tuple[float, ...]] = None  # stroke dash pattern; None = solid
    font_size: float = 10.0
    flow: int = 0  # arrows with the same flow number are branches/joins of one flow

    @property
    def has_route(self) -> bool:
        return len(self.points) >= 2


@dataclass
class FreeText:
    """A text annotation placed on a diagram."""

    x: float
    y: float
    width: float
    height: float
    text: str
    color: str = "#000000"
    font_size: float = 10.0


@dataclass
class Diagram:
    """A single sheet: the decomposition of one parent activity.

    ``parent_id`` is the decomposed activity - or, for the context diagram, the model's base
    function, whose sheet holds the one top-level box. ``node`` is the IDEF0 node number of
    the decomposed activity ("A-0" for the context diagram, then "A0", "A1", "A12" ...).
    """

    parent_id: int
    parent_name: str
    activities: List[Activity] = field(default_factory=list)
    arrows: List[Arrow] = field(default_factory=list)
    texts: List[FreeText] = field(default_factory=list)
    node: str = ""
    frame: Frame = (FRAME_LEFT, FRAME_TOP, FRAME_RIGHT, FRAME_BOTTOM_RAMUS2)
    notation: str = "idef0"  # "idef0", "dfd" (data flow) or "dfds" (data flow with roles)


@dataclass
class _Element:
    qualifier_id: int
    name: str


class RsfModel:
    """A loaded .rsf file, read lazily table by table."""

    def __init__(self, path):
        """``path`` is a file name or a binary file object - or a source of tables already in
        memory (anything with ``reader_rows(name)``, as the editor's document has), which is
        how the editor shows a file with unsaved changes without writing it out."""
        self._source = path if hasattr(path, "reader_rows") else None
        self._zip = None if self._source is not None else zipfile.ZipFile(path)
        self._cache: Dict[str, List[Dict[str, Optional[str]]]] = {}
        self._memo: Dict[str, object] = {}

    def close(self) -> None:
        if self._zip is not None:
            self._zip.close()

    def __enter__(self) -> "RsfModel":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---------------------------------------------------------------- raw tables

    def table(self, name: str) -> List[Dict[str, Optional[str]]]:
        """Rows of ``data/<name>.xml``; an empty list if that dump is not in the file."""
        if name not in self._cache:
            if self._source is not None:
                self._cache[name] = self._source.reader_rows(name)
                return self._cache[name]
            member = "data/" + name + ".xml"
            try:
                self._cache[name] = _parse_table(self._zip.read(member))
            except KeyError:
                self._cache[name] = []
        return self._cache[name]

    # ------------------------------------------------------------- lookups by name

    def _current_branch(self) -> int:
        """The branch the file was saved on: the newest one any element mentions."""
        if "branch" not in self._memo:
            seen = {0}
            for r in self.table("elements"):
                seen.add(_int(r.get("CREATED_BRANCH_ID")))
                removed = _int(r.get("REMOVED_BRANCH_ID"), _NO_REMOVAL)
                if removed != _NO_REMOVAL:
                    seen.add(removed)
            self._memo["branch"] = max(seen)
        return self._memo["branch"]  # type: ignore[return-value]

    def _alive(self, row: Dict[str, Optional[str]]) -> bool:
        """Is this element/attribute still in the model? Files without branch columns:
        everything is."""
        created = _int(row.get("CREATED_BRANCH_ID"))
        removed = _int(row.get("REMOVED_BRANCH_ID"), _NO_REMOVAL)
        return created <= self._current_branch() < removed

    def attribute_id(self, name: str) -> Optional[int]:
        """The id this file gives the attribute called ``name``, or None if it has none."""
        if "attrs" not in self._memo:
            ids: Dict[str, int] = {}
            for r in self.table("attributes"):
                if self._alive(r) and r.get("ATTRIBUTE_NAME"):
                    ids[r["ATTRIBUTE_NAME"]] = _int(r["ATTRIBUTE_ID"])
            self._memo["attrs"] = ids
        return self._memo["attrs"].get(name)  # type: ignore[union-attr]

    def _qualifier_ids(self, name: str) -> Set[int]:
        return {
            _int(r["QUALIFIER_ID"])
            for r in self.table("qualifiers")
            if r.get("QUALIFIER_NAME") == name and self._alive(r)
        }

    def _text_values(self) -> Dict[Tuple[int, int], str]:
        """(element id, attribute id) -> text, for every text attribute value."""
        if "texts" not in self._memo:
            self._memo["texts"] = {
                (_int(r["ELEMENT_ID"]), _int(r["ATTRIBUTE_ID"])): (r.get("VALUE") or "")
                for r in self.table("Core/attribute_texts")
            }
        return self._memo["texts"]  # type: ignore[return-value]

    def elements(self) -> Dict[int, _Element]:
        """Every live element: its qualifier and its name.

        The name is the text attribute the element's qualifier designates for naming;
        files that predate that mechanism carry it in the element row itself.
        """
        if "elements" in self._memo:
            return self._memo["elements"]  # type: ignore[return-value]
        name_attr: Dict[int, int] = {}
        for r in self.table("qualifiers"):
            attr = _int(r.get("ATTRIBUTE_FOR_NAME"), -1)
            if attr >= 0:
                name_attr[_int(r["QUALIFIER_ID"])] = attr
        texts = self._text_values()
        dfds = self._dfds_names()

        out: Dict[int, _Element] = {}
        for r in self.table("elements"):
            if not self._alive(r):
                continue
            eid, qid = _int(r["ELEMENT_ID"]), _int(r["QUALIFIER_ID"])
            key = (eid, name_attr.get(qid, -1))
            name = texts.get(key) or dfds.get(key, ("", ""))[0] or r.get("ELEMENT_NAME") or ""
            out[eid] = _Element(qid, name.strip())
        self._memo["elements"] = out
        return out

    def _dfds_names(self) -> Dict[Tuple[int, int], Tuple[str, str]]:
        """(element, attribute) -> (short name, long name), for a model whose activities are
        named by a DFDS name - two parts, the second written smaller beneath the first."""
        if "dfds" not in self._memo:
            self._memo["dfds"] = {
                (_int(r["ELEMENT_ID"]), _int(r["ATTRIBUTE_ID"])):
                    ((r.get("SHORT_NAME") or "").strip(), (r.get("LONG_NAME") or "").strip())
                for r in self.table("IDEF0/attribute_dfds_names")
            }
        return self._memo["dfds"]  # type: ignore[return-value]

    def decomposition_types(self) -> Dict[int, str]:
        """element -> how its decomposition is drawn, for every function that says (the rest
        are IDEF0)."""
        if "notations" not in self._memo:
            types = self._per_element("IDEF0/attribute_decomposition_types",
                                      "F_DECOMPOSITION_TYPE", "TYPE")
            self._memo["notations"] = {eid: NOTATIONS.get(_int(v, -1), "idef0")
                                       for eid, v in types.items()}
        return self._memo["notations"]  # type: ignore[return-value]

    def _links(self, attribute: str) -> Dict[int, int]:
        """element -> the other element it points at, for a Core.OtherElement attribute."""
        attr = self.attribute_id(attribute)
        return {
            _int(r["ELEMENT_ID"]): _int(r["OTHER_ELEMENT"])
            for r in self.table("Core/attribute_other_elements")
            if attr is not None and _int(r["ATTRIBUTE_ID"]) == attr
        }

    # ------------------------------------------------------------- assembled view

    def _hierarchy(self) -> Dict[int, Tuple[int, int]]:
        """element -> (parent, previous sibling); -1/0 mean none."""
        return {
            _int(r["ELEMENT_ID"]): (
                _int(r.get("PARENT_ELEMENT_ID"), -1),
                _int(r.get("PREVIOUS_ELEMENT_ID"), -1),
            )
            for r in self.table("Core/attribute_hierarchicals")
        }

    def _base_function_of_model(self) -> Dict[int, int]:
        """model qualifier id -> the base function element that owns its context diagram."""
        attr = self.attribute_id("F_BASE_FUNCTION_QUALIFIER_ID")
        live = self.elements()
        out: Dict[int, int] = {}
        for r in self.table("Core/attribute_longs"):
            if attr is not None and _int(r["ATTRIBUTE_ID"]) == attr:
                eid = _int(r["ELEMENT_ID"])
                if eid in live:
                    out[_int(r["VALUE"])] = eid
        return out

    def activities(self) -> Dict[int, Activity]:
        """Every live activity in the model, keyed by element id, with its box filled in.

        An activity is an element that carries a rectangle: that is what makes it something
        drawn on a diagram rather than, say, a catalog entry. A top-level activity has no
        parent; its parent here is its model's base function, which owns the context
        diagram it is drawn on. Numbers follow IDEF0: A0 for the top box, A1..An for its
        children, A11.. for theirs.
        """
        if "activities" in self._memo:
            return self._memo["activities"]  # type: ignore[return-value]
        live = self.elements()
        hierarchy = self._hierarchy()
        base_of = self._base_function_of_model()
        rect_attr = self.attribute_id("F_BOUNDS")
        types = self._per_element("IDEF0/attribute_function_types", "F_TYPE", "TYPE")
        sizes = self._per_element("IDEF0/attribute_fonts", "F_FONT", "SIZE")
        fills = self._per_element("IDEF0/attribute_colors", "F_BACKGROUND", "COLOR")
        inks = self._per_element("IDEF0/attribute_colors", "F_FOREGROUND", "COLOR")
        notations = self.decomposition_types()
        owners = self._per_element("IDEF0/attribute_function_ouners", "F_OUNER_ID", "OUNER_ID")
        links = self._longs("F_LINK")
        name_attr = {_int(r["QUALIFIER_ID"]): _int(r.get("ATTRIBUTE_FOR_NAME"), -1)
                     for r in self.table("qualifiers")}
        dfds = self._dfds_names()

        out: Dict[int, Activity] = {}
        for r in self.table("IDEF0/attribute_rectangles"):
            eid = _int(r["ELEMENT_ID"])
            if eid not in live or (rect_attr is not None and _int(r["ATTRIBUTE_ID"]) != rect_attr):
                continue
            parent, previous = hierarchy.get(eid, (-1, -1))
            if parent <= 0:
                parent = base_of.get(live[eid].qualifier_id, -1)
            kind_type = _int(types[eid]) if eid in types else None
            owner = _int(owners.get(eid), -1)
            out[eid] = Activity(
                element_id=eid,
                name=self._shown_name(eid, links.get(eid)),
                parent_id=parent,
                previous_id=previous,
                x=_num(r.get("X")),
                y=_num(r.get("Y")),
                width=_num(r.get("WIDTH")),
                height=_num(r.get("HEIGHT")),
                type=kind_type,
                fill=_argb_hex(fills.get(eid)),
                color=_argb_hex(inks.get(eid)),
                font_size=_num(sizes.get(eid), 12.0),
                kind=KINDS.get(kind_type, "process") if kind_type is not None else "process",
                decomposition=notations.get(eid, "idef0"),
                long_name=dfds.get((eid, name_attr.get(live[eid].qualifier_id, -1)),
                                   ("", ""))[1],
                owner_id=owner if owner > 0 else None,
            )
        for a in out.values():
            parent = out.get(a.parent_id)
            if parent is not None and a.has_box and a.kind == "process":
                parent.decomposed = True
        self._number(out, hierarchy)
        self._memo["activities"] = out
        return out

    def _longs(self, attribute: str) -> Dict[int, int]:
        """element -> its value of a Core.Long attribute."""
        attr = self.attribute_id(attribute)
        return {_int(r["ELEMENT_ID"]): _int(r["VALUE"]) for r in self.table("Core/attribute_longs")
                if attr is not None and _int(r["ATTRIBUTE_ID"]) == attr and r.get("VALUE")}

    def _shown_name(self, eid: int, link: Optional[int]) -> str:
        """The name a box shows: its own - or, for a data flow diagram's object tied to a
        catalog item or a flow (F_LINK), that item's name, or what the flow carries."""
        live = self.elements()
        if link is not None and link in live:
            if live[link].qualifier_id in self._qualifier_ids("F_STREAMS"):
                text = self._stream_labels().get(link, "")
                if text:
                    return text
            elif live[link].name:
                return live[link].name
        return live[eid].name

    def _per_element(self, table: str, attribute: str, column: str) -> Dict[int, Optional[str]]:
        """element -> one column of a single-valued attribute's table, looked up by the
        attribute's name (several attributes can share a table, e.g. the two colours)."""
        attr = self.attribute_id(attribute)
        if attr is None:
            return {}
        return {
            _int(r["ELEMENT_ID"]): r.get(column)
            for r in self.table(table)
            if _int(r["ATTRIBUTE_ID"]) == attr
        }

    def _number(self, acts: Dict[int, Activity], hierarchy: Dict[int, Tuple[int, int]]) -> None:
        """Give every activity its IDEF0 node number, threading the parent's down."""
        by_parent: Dict[int, List[Activity]] = {}
        for a in acts.values():
            if a.has_box:
                by_parent.setdefault(a.parent_id, []).append(a)

        def walk(parent_id: int, prefix: Optional[str]) -> None:
            ordered = _in_sibling_order(by_parent.get(parent_id, []), hierarchy, acts)
            # Only activities count: a data flow diagram's external entities, data stores and
            # roles take no number, as in Ramus.
            i = 0
            for a in ordered:
                if a.kind != "process":
                    continue
                i += 1
                if prefix is None:
                    a.number = "A0"  # the top box on the context diagram
                else:
                    a.number = (prefix if prefix != "A0" else "A") + str(i)
                walk(a.element_id, a.number)

        for root in {a.parent_id for a in acts.values() if a.has_box and a.parent_id not in acts}:
            walk(root, None)

    def diagrams(self) -> List[Diagram]:
        """Every sheet in the model: the boxes grouped by the parent they decompose, with
        the arrows and free text labels drawn on it. Largest sheet first."""
        acts = self.activities()
        hierarchy = self._hierarchy()
        by_parent: Dict[int, List[Activity]] = {}
        for a in acts.values():
            if a.has_box:
                by_parent.setdefault(a.parent_id, []).append(a)

        arrows_by_owner, nodes = self._arrows()
        frame = self.frame()
        blobs = self._blobs()
        live = self.elements()

        diagrams: List[Diagram] = []
        for parent_id, children in by_parent.items():
            ordered = _in_sibling_order(children, hierarchy, acts)
            if parent_id in acts:
                parent_name, node = acts[parent_id].name, acts[parent_id].number
            else:  # a base function: the context diagram
                parent_name = next((a.name for a in ordered if a.kind == "process"),
                                   ordered[0].name if ordered else "")
                node = "A-0"
            diagram = Diagram(
                parent_id=parent_id,
                parent_name=parent_name,
                activities=ordered,
                arrows=arrows_by_owner.get(parent_id, []),
                node=node,
                frame=frame,
                notation=self.decomposition_types().get(parent_id, "idef0"),
            )
            blob = blobs.get(parent_id)
            if blob is not None:
                diagram.texts = [_free_text(t) for t in blob.texts if t.text]
            _finish_arrows(diagram, nodes, live, acts)
            diagrams.append(diagram)
        diagrams.sort(key=lambda d: len(d.activities), reverse=True)
        return diagrams

    def form_of(self, diagram: Diagram) -> Dict[str, object]:
        """What the IDEF0 diagram form round a sheet says (its header and footer, as Ramus and
        FIPS 183 draw them): who made the model and for which project, the sheet's dates and
        status, its node, title and number among the model's sheets, and its context - the
        boxes of the sheet above, with the one this sheet decomposes picked out."""
        acts = self.activities()
        owner = acts.get(diagram.parent_id)
        base = diagram.parent_id
        while base in acts:  # the base function at the root of this sheet's model
            base = acts[base].parent_id
        prefs = next((r for r in self.table("IDEF0/attribute_model_preferences")
                      if _int(r.get("ELEMENT_ID")) == base), {})
        dates = {}
        for name in ("F_CREATE_DATE", "F_REV_DATE"):
            dates[name] = self._per_element("Core/attribute_dates", name, "VALUE")
        when = diagram.parent_id
        created = dates["F_CREATE_DATE"].get(when) or prefs.get("CREATE_DATE")
        revised = dates["F_REV_DATE"].get(when) or prefs.get("CHANGE_DATE") or created
        statuses = self._per_element("IDEF0/attribute_statuses", "F_STATUS", "TYPE")
        status = _int(statuses.get(when if owner is not None else
                                   next((a.element_id for a in diagram.activities), -1)), 0)

        # Sheets numbered as the model is read: the context first, then each decomposition
        # before the decompositions under it.
        sheets = {d.parent_id: d for d in self.diagrams()} if "sheet_numbers" not in \
            self._memo else {}
        if "sheet_numbers" not in self._memo:
            order: Dict[int, int] = {}
            children: Dict[int, List[Activity]] = {}
            for a in acts.values():
                if a.has_box:
                    children.setdefault(a.parent_id, []).append(a)
            hierarchy = self._hierarchy()

            def walk(parent: int) -> None:
                if parent in sheets:
                    order[parent] = len(order) + 1
                for a in _in_sibling_order(children.get(parent, []), hierarchy, acts):
                    walk(a.element_id)

            for root in sorted(p for p in sheets if p not in acts):
                walk(root)
            self._memo["sheet_numbers"] = order
        number = self._memo["sheet_numbers"].get(diagram.parent_id)

        context, mark = [], None
        if owner is not None:
            above = next((d for d in self.diagrams() if d.parent_id == owner.parent_id), None)
            if above is not None:
                for i, a in enumerate(b for b in above.activities if b.has_box):
                    context.append((a.x, a.y, a.width, a.height))
                    if a.element_id == owner.element_id:
                        mark = i
        names = " ".join(a.name for a in acts.values())
        return {
            "author": (prefs.get("PROJECT_AUTOR") or "").strip(),
            "project": (prefs.get("PROJECT_NAME") or "").strip(),
            "used_at": (prefs.get("USED_AT") or "").strip(),
            "date": _day(created), "revision": _day(revised),
            "status": status if 0 <= status <= 3 else 0,
            "node": diagram.node,
            "title": owner.name if owner is not None else diagram.parent_name,
            "number": str(number) if number else "",
            "context": context, "context_mark": mark, "top": owner is None,
            "russian": any("\u0400" <= ch <= "\u04ff" for ch in names),
        }

    def nodes(self) -> Dict[int, List[Tuple[int, str]]]:
        """Every crosspoint node in the model and the segment ends that meet at it: node id ->
        [(sector id, "out" for a segment starting there / "in" for one ending there)]. Inside
        a sheet a node is a fork or a join; shared by sheets, it carries an arrow from a box on
        one level to the frame of that box's decomposition."""
        return self._arrows()[1]

    # ------------------------------------------------------------------------- arrows

    def _blobs(self) -> Dict[int, vd.DiagramBlob]:
        """owner element -> its decoded visual-data blob. A blob that does not parse is
        skipped: a diagram is still worth drawing without its free labels."""
        if "blobs" not in self._memo:
            attr = self.attribute_id("F_VISUAL_DATA")
            blobs: Dict[int, vd.DiagramBlob] = {}
            for r in self.table("IDEF0/attribute_visual_datas"):
                if attr is not None and _int(r["ATTRIBUTE_ID"]) != attr:
                    continue
                try:
                    blobs[_int(r["ELEMENT_ID"])] = vd.decode_diagram_blob(
                        vd.unmask(r["DATA"]) if r.get("DATA") else b""
                    )
                except vd.BlobError:
                    continue
            self._memo["blobs"] = blobs
        return self._memo["blobs"]  # type: ignore[return-value]

    def _routes(self) -> Dict[int, Tuple[List[Tuple[float, float]], Optional[Label]]]:
        """sector element -> (route points, label). Version-2 files keep these in tables;
        version-1 files keep them in the owner's visual-data blob."""
        routes: Dict[int, Tuple[List[Tuple[float, float]], Optional[Label]]] = {}

        for blob in self._blobs().values():
            for s in blob.sectors:
                if s.sector_id is None:
                    continue
                label = None
                if s.label is not None:
                    lb = s.label
                    label = Label(lb.x, lb.y, lb.width, lb.height, lb.transparent, lb.tilde_pos)
                routes[s.sector_id] = ([(p.x, p.y) for p in s.points], label)

        points: Dict[int, List[Tuple[int, float, float]]] = {}
        for r in self.table("IDEF0/attribute_sector_points"):
            points.setdefault(_int(r["ELEMENT_ID"]), []).append(
                (_int(r.get("POSITION")), _num(r.get("X_POSITION")), _num(r.get("Y_POSITION")))
            )
        props = {_int(r["ELEMENT_ID"]): r for r in self.table("IDEF0/attribute_sector_properties")}
        for sid, pts in points.items():
            pts.sort(key=lambda p: p[0])
            label = None
            p = props.get(sid)
            if p is not None and _int(p.get("SHOW_TEXT"), 1) != 0:
                label = Label(
                    _num(p.get("TEXT_X")),
                    _num(p.get("TEXT_Y")),
                    _num(p.get("TEXT_WIDTH")),
                    _num(p.get("TEXT_HIEGHT", p.get("TEXT_HEIGHT"))),
                    _int(p.get("TRANSPARENT"), 1) != 0,
                    _num(p.get("TILDA_POS"), 0.5) if _int(p.get("SHOW_TILDA")) else None,
                )
            routes[sid] = ([(x, y) for _, x, y in pts], label)
        return routes

    def _stream_labels(self) -> Dict[int, str]:
        """stream element -> the text written on its arrows: its name, or, when that is
        empty, the names of the catalog items attached to it."""
        live = self.elements()
        added: Dict[int, List[int]] = {}
        attr = self.attribute_id("F_STREAM_ADDED")
        for r in self.table("IDEF0/attribute_any_to_any_elements"):
            if attr is not None and _int(r["ATTRIBUTE_ID"]) == attr:
                added.setdefault(_int(r["ELEMENT_ID"]), []).append(_int(r["OTHER_ELEMENT"]))
        labels: Dict[int, str] = {}
        for eid, el in live.items():
            if el.name:
                labels[eid] = el.name
            elif eid in added:
                labels[eid] = ";\n".join(live[o].name for o in added[eid] if o in live)
        return labels

    def frame(self) -> Frame:
        """The diagram frame this file was drawn with (see the module constants)."""
        if "frame" not in self._memo:
            self._arrows()
        return self._memo["frame"]  # type: ignore[return-value]

    def _arrows(
        self,
    ) -> Tuple[Dict[int, List[Arrow]], Dict[int, List[Tuple[int, str]]]]:
        """Every live arrow segment grouped by the diagram it is on, and the node table
        (crosspoint id -> [(sector id, "in"/"out"), ...]) across the whole model. Also fixes
        the frame, which the stand-in routes of unplaced segments need."""
        if "arrows" in self._memo:
            return self._memo["arrows"]  # type: ignore[return-value]
        live = self.elements()
        acts = self.activities()
        owners = self._links("F_FUNCTION_SECTOR")
        streams = self._links("F_SECTOR_STREAM")
        stream_text = self._stream_labels()
        routes = self._routes()
        start_attr = self.attribute_id("F_SECTOR_BORDER_START")
        end_attr = self.attribute_id("F_SECTOR_BORDER_END")
        sector_attr = self.attribute_id("F_SECTOR_ATTRIBUTE")

        borders: Dict[Tuple[int, int], Dict[str, Optional[str]]] = {
            (_int(r["ELEMENT_ID"]), _int(r["ATTRIBUTE_ID"])): r
            for r in self.table("IDEF0/attribute_sector_borders")
        }
        sector_rows = {
            _int(r["ELEMENT_ID"]): r
            for r in self.table("IDEF0/attribute_sectors")
            if sector_attr is None or _int(r["ATTRIBUTE_ID"]) == sector_attr
        }
        sector_q = self._qualifier_ids("F_SECTORS")

        out: Dict[int, List[Arrow]] = {}
        nodes: Dict[int, List[Tuple[int, str]]] = {}
        unplaced: List[Tuple[Arrow, Dict[str, Optional[str]]]] = []
        for sid, el in sorted(live.items()):
            if el.qualifier_id not in sector_q or sid not in owners:
                continue
            owner = owners[sid]
            if owner not in live:
                continue
            row = sector_rows.get(sid, {})
            ends = []
            for attr in (start_attr, end_attr):
                b = borders.get((sid, attr)) if attr is not None else None
                ends.append(_end(b or {}, acts))
            start, end = ends
            for e, direction in ((start, "out"), (end, "in")):
                if e.node is not None:
                    nodes.setdefault(e.node, []).append((sid, direction))

            style = _style(row)
            stream = streams.get(sid)
            route, label = routes.get(sid, ([], None))
            arrow = Arrow(
                sector_id=sid,
                stream_id=stream,
                name=stream_text.get(stream, "") if stream is not None else "",
                start=start,
                end=end,
                points=route,
                label=label,
                color=style.color.hex() if style.color else None,
                width=style.stroke.width if style.stroke and style.stroke.kind == "basic" else 1.0,
                dash=_dash_of(style.stroke),
                font_size=float(style.font.size) if style.font else 10.0,
            )
            alt = row.get("ALTERNATIVE_TEXT")
            if alt and alt.strip():
                arrow.name = alt.strip()
            if _int(row.get("SHOW_TEXT"), 1) == 0:
                arrow.label = None
            if not arrow.has_route:
                unplaced.append((arrow, row))
            out.setdefault(owner, []).append(arrow)

        frame = self._frame_from(out)
        for arrow, row in unplaced:
            _make_stub(arrow, row, frame)
        self._memo["frame"] = frame
        self._memo["arrows"] = (out, nodes)
        return out, nodes

    def _frame_from(self, arrows_by_owner: Dict[int, List[Arrow]]) -> Frame:
        """The frame as the arrows that land on it place it; where none does, as the Ramus
        version that saved the file would."""
        right: Dict[float, int] = {}
        bottom: Dict[float, int] = {}
        for arrows in arrows_by_owner.values():
            for a in arrows:
                if not a.has_route:
                    continue
                for e, (x, y) in ((a.start, a.points[0]), (a.end, a.points[-1])):
                    if e.kind == "frame" and e.side == "right":
                        right[round(x, 1)] = right.get(round(x, 1), 0) + 1
                    elif e.kind == "frame" and e.side == "bottom":
                        bottom[round(y, 1)] = bottom.get(round(y, 1), 0) + 1
        newer = any(b.version >= 2 for b in self._blobs().values())
        default_bottom = FRAME_BOTTOM_RAMUS3 if newer else FRAME_BOTTOM_RAMUS2
        return (
            FRAME_LEFT,
            FRAME_TOP,
            max(right, key=right.get) if right else FRAME_RIGHT,
            max(bottom, key=bottom.get) if bottom else default_bottom,
        )


# ------------------------------------------------------------------------- helper functions


def _end(border: Dict[str, Optional[str]], acts: Dict[int, Activity]) -> End:
    """One border row -> what that end of the segment is attached to. A missing row means
    "attached to nothing"."""
    border_type = _int(border.get("BORDER_TYPE"), -1)
    function = _int(border.get("FUNCTION"), -1)
    function_side = _int(border.get("FUNCTION_TYPE"), -1)
    node = _int(border.get("CROSSPOINT"), -1)
    tunnel_flag = _int(border.get("TUNNEL_SOFT"))
    tunnel = {0: "hard", 1: "soft", 3: "soft"}.get(tunnel_flag)
    node_id = node if node >= 0 else None

    if border_type in range(4):
        return End("frame", side=SIDES[border_type], role=ROLES[border_type], node=node_id,
                   tunnel=tunnel)
    if function >= 0 and function_side in range(4):
        return End("activity", activity_id=function, side=SIDES[function_side],
                   role=ROLES[function_side], node=node_id, tunnel=tunnel)
    if node_id is not None:
        return End("junction", node=node_id)
    return End("open")


def _day(value: Optional[str]) -> str:
    """A date as Ramus stores it ("9/22/26 2:07 PM") as a form shows it ("22.09.2026")."""
    if not value:
        return ""
    try:
        month, day, year = value.split()[0].split("/")
        year = int(year)
        return f"{int(day):02d}.{int(month):02d}.{year + 2000 if year < 100 else year}"
    except (ValueError, IndexError):
        return value.strip()


def _argb_hex(value: Optional[str]) -> Optional[str]:
    """A colour stored as a signed 32-bit ARGB int (java.awt.Color.getRGB) -> "#rrggbb"."""
    if value is None or value == "":
        return None
    try:
        argb = int(value) & 0xFFFFFFFF
    except ValueError:
        return None
    return f"#{argb & 0xFFFFFF:06x}"


def _dash_of(stroke: Optional[vd.Stroke]) -> Optional[Tuple[float, ...]]:
    """A stroke's dash pattern, if it has a usable one (all lengths positive, not all zero)."""
    if stroke is None or stroke.kind != "basic" or not stroke.dash:
        return None
    if any(d < 0 for d in stroke.dash) or not any(d > 0 for d in stroke.dash):
        return None
    return tuple(stroke.dash)


def _style(row: Dict[str, Optional[str]]) -> vd.SectorStyle:
    """The stroke, font and colour of a sector row; defaults if it carries none or they do
    not parse."""
    text = row.get("VISUAL_ATTRIBUTES")
    try:
        return vd.decode_sector_style(vd.unmask(text) if text else b"")
    except vd.BlobError:
        return vd.SectorStyle()


def _make_stub(arrow: Arrow, row: Dict[str, Optional[str]], frame: Frame) -> None:
    """Give a segment Ramus never drew a short route from the frame inward.

    Such a segment has exactly one end on the frame and the other attached to nothing; the
    coordinate along the frame side where it should sit is in its ``CREATE_POS``. Anything
    else cannot be placed and is left without a route.
    """
    STUB = 36.0
    left, top, right, bottom = frame
    if arrow.start.kind == "frame" and arrow.end.kind == "open":
        frame_end, flows_in = arrow.start, True
    elif arrow.end.kind == "frame" and arrow.start.kind == "open":
        frame_end, flows_in = arrow.end, False
    else:
        return
    pos = _num(row.get("CREATE_POS"), -1.0)
    if pos < 0:
        return
    side = frame_end.side
    if side == "left":
        edge, inner = (left, pos), (left + STUB, pos)
    elif side == "right":
        edge, inner = (right, pos), (right - STUB, pos)
    elif side == "top":
        edge, inner = (pos, top), (pos, top + STUB)
    else:
        edge, inner = (pos, bottom), (pos, bottom - STUB)
    arrow.points = [edge, inner] if flows_in else [inner, edge]
    arrow.geometry = "stub"


def _finish_arrows(
    diagram: Diagram,
    nodes: Dict[int, List[Tuple[int, str]]],
    live: Dict[int, _Element],
    acts: Dict[int, Activity],
) -> None:
    """Fill in what needs the whole diagram: which segments are one flow, and which
    tunnel marks really are shown."""
    by_id = {a.sector_id: a for a in diagram.arrows}

    # Segments that share a node inside this diagram are branches or joins of one flow.
    parent = {sid: sid for sid in by_id}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for members in nodes.values():
        here = [sid for sid, _ in members if sid in by_id]
        for other in here[1:]:
            parent[find(other)] = find(here[0])
    numbering: Dict[int, int] = {}
    for a in diagram.arrows:
        a.flow = numbering.setdefault(find(a.sector_id), len(numbering) + 1)

    # A tunnel bracket is drawn only where the arrow stops at its node: nothing continues
    # it at the other level, an activity with no decomposition has nowhere to continue it
    # to, and on the two top diagrams the frame has no level above to continue from.
    has_children = {a.parent_id for a in acts.values() if a.has_box}
    for a in diagram.arrows:
        for e in (a.start, a.end):
            if e.tunnel is None:
                continue
            ends = nodes.get(e.node, []) if e.node is not None else []
            continues = any(d == "in" for _, d in ends) and any(d == "out" for _, d in ends)
            leaf = e.kind == "activity" and e.activity_id not in has_children
            top_frame = e.kind == "frame" and diagram.node in ("A-0", "A0")
            if continues or leaf or top_frame:
                e.tunnel = None


def _free_text(t: vd.TextRecord) -> FreeText:
    return FreeText(
        x=t.x,
        y=t.y,
        width=t.width,
        height=t.height,
        text=t.text or "",
        color=t.color.hex(),
        font_size=float(t.font.size) if t.font else 10.0,
    )


def _in_sibling_order(
    activities: Sequence[Activity],
    hierarchy: Dict[int, Tuple[int, int]],
    acts: Dict[int, Activity],
) -> List[Activity]:
    """Order siblings by the previous-element chain, falling back to position.

    Each activity names the sibling before it; the first names none. A sibling that was
    deleted keeps its place in the chain, so a link that points at a deleted sibling is
    followed through it to the one before that. If the chain is still broken, reading order -
    top to bottom, then left to right - is a reasonable stand-in.
    """
    present = {a.element_id for a in activities}

    def effective_previous(a: Activity) -> int:
        prev, seen = a.previous_id, set()
        while prev > 0 and prev not in present and prev not in seen:
            seen.add(prev)
            prev = hierarchy.get(prev, (-1, -1))[1]
        return prev if prev in present else -1

    after: Dict[int, Activity] = {}
    firsts: List[Activity] = []
    for a in activities:
        prev = effective_previous(a)
        if prev == -1:
            firsts.append(a)
        else:
            after.setdefault(prev, a)

    ordered: List[Activity] = []
    seen_ids: Set[int] = set()
    for first in sorted(firsts, key=lambda a: (a.y, a.x)):
        node: Optional[Activity] = first
        while node is not None and node.element_id not in seen_ids:
            ordered.append(node)
            seen_ids.add(node.element_id)
            node = after.get(node.element_id)

    leftover = [a for a in activities if a.element_id not in seen_ids]
    leftover.sort(key=lambda a: (a.y, a.x))
    return ordered + leftover
