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

This module reads those dumps and assembles the few that describe an IDEF0/DFD diagram:
the elements and their names, the activity tree, and the rectangle each activity occupies.
It is written against the file format only - it borrows no code from Ramus - so it can read a
model without the application and under any license.

Arrows (the "sectors") are a separate, richer encoding and are added on top of this reader.
"""

from __future__ import annotations

import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Dict, List, Optional


def _parse_table(data: bytes) -> List[Dict[str, Optional[str]]]:
    """One table dump -> a list of rows, each a {column name: value} dict.

    A self-closing ``<f/>`` is an empty cell and comes back as ``None``; the caller decides
    whether that means zero, absent, or an empty string.
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
    number: str = ""  # IDEF0 node number once assigned, e.g. "A1", "A12"

    @property
    def has_box(self) -> bool:
        return self.width > 0 and self.height > 0


@dataclass
class Diagram:
    """A single sheet: the decomposition of one parent activity, as the boxes drawn on it."""

    parent_id: int
    parent_name: str
    activities: List[Activity] = field(default_factory=list)


class RsfModel:
    """A loaded .rsf file, read lazily table by table."""

    def __init__(self, path: str):
        self._zip = zipfile.ZipFile(path)
        self._cache: Dict[str, List[Dict[str, Optional[str]]]] = {}

    def close(self) -> None:
        self._zip.close()

    def __enter__(self) -> "RsfModel":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---------------------------------------------------------------- raw tables

    def table(self, name: str) -> List[Dict[str, Optional[str]]]:
        """Rows of ``data/<name>.xml``; an empty list if that dump is not in the file."""
        if name not in self._cache:
            member = "data/" + name + ".xml"
            try:
                self._cache[name] = _parse_table(self._zip.read(member))
            except KeyError:
                self._cache[name] = []
        return self._cache[name]

    # ------------------------------------------------------------- assembled view

    def activities(self) -> Dict[int, Activity]:
        """Every activity in the model, keyed by element id, with its box filled in.

        An activity is an element that carries a rectangle: that is what makes it something
        drawn on a diagram rather than, say, a catalog entry. Names come from the element
        table, falling back to the Name text attribute when the element row carries none.
        """
        names = {
            _int(r["ELEMENT_ID"]): (r.get("ELEMENT_NAME") or "")
            for r in self.table("elements")
        }
        text_names = self._text_names()

        hierarchy = {
            _int(r["ELEMENT_ID"]): (
                _int(r.get("PARENT_ELEMENT_ID"), -1),
                _int(r.get("PREVIOUS_ELEMENT_ID"), -1),
            )
            for r in self.table("Core/attribute_hierarchicals")
        }

        out: Dict[int, Activity] = {}
        for r in self.table("IDEF0/attribute_rectangles"):
            eid = _int(r["ELEMENT_ID"])
            parent, previous = hierarchy.get(eid, (-1, -1))
            name = names.get(eid) or text_names.get(eid) or ""
            out[eid] = Activity(
                element_id=eid,
                name=name.strip(),
                parent_id=parent,
                previous_id=previous,
                x=_num(r.get("X")),
                y=_num(r.get("Y")),
                width=_num(r.get("WIDTH")),
                height=_num(r.get("HEIGHT")),
            )
        return out

    def _text_names(self) -> Dict[int, str]:
        """element id -> its Name, read from the Core.Text attribute dump.

        The Name attribute does not have a fixed id across models, so it is found by its own
        value: the attribute whose name is "Name" names everything else.
        """
        texts = self.table("Core/attribute_texts")
        name_attr_ids = {
            r["ATTRIBUTE_ID"] for r in texts if (r.get("VALUE") or "") == "Name"
        }
        result: Dict[int, str] = {}
        for r in texts:
            if r.get("ATTRIBUTE_ID") in name_attr_ids:
                result[_int(r["ELEMENT_ID"])] = (r.get("VALUE") or "").strip()
        return result

    def diagrams(self) -> List[Diagram]:
        """Every sheet in the model: the boxes grouped by the parent they decompose.

        Children are ordered the way IDEF0 reads them - by the previous-sibling chain, top
        left to bottom right - and numbered A1, A2, ... within the sheet. (Full A11-style
        numbering needs the parent's own number threaded down; that is a later refinement.)
        """
        acts = self.activities()
        by_parent: Dict[int, List[Activity]] = {}
        for a in acts.values():
            if a.has_box:
                by_parent.setdefault(a.parent_id, []).append(a)

        diagrams: List[Diagram] = []
        for parent_id, children in by_parent.items():
            ordered = _in_sibling_order(children)
            for i, a in enumerate(ordered, start=1):
                a.number = "A" + str(i)
            diagrams.append(
                Diagram(
                    parent_id=parent_id,
                    parent_name=(acts[parent_id].name if parent_id in acts else ""),
                    activities=ordered,
                )
            )
        diagrams.sort(key=lambda d: len(d.activities), reverse=True)
        return diagrams


def _in_sibling_order(activities: List[Activity]) -> List[Activity]:
    """Order siblings by the previous-element chain, falling back to position.

    Each activity names the sibling before it; the first names none (-1 or 0). Following that
    chain gives the author's order. If the chain is broken or missing, reading order - top to
    bottom, then left to right - is a reasonable stand-in.
    """
    present = {a.element_id for a in activities}
    after: Dict[int, Activity] = {}
    first: Optional[Activity] = None
    for a in activities:
        if a.previous_id in (-1, 0) or a.previous_id not in present:
            if first is None:
                first = a
            else:
                after.setdefault(-1, a)  # a second "first" - keep it, chain is imperfect
        else:
            after[a.previous_id] = a

    ordered: List[Activity] = []
    seen = set()
    node = first
    while node is not None and node.element_id not in seen:
        ordered.append(node)
        seen.add(node.element_id)
        node = after.get(node.element_id)

    # Anything the chain did not reach (broken links) goes on the end in reading order.
    leftover = [a for a in activities if a.element_id not in seen]
    leftover.sort(key=lambda a: (a.y, a.x))
    return ordered + leftover
