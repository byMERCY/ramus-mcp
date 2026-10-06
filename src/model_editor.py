"""The "hands": changing a Ramus model and writing it back.

Everything here works on an :class:`~rsf_document.RsfDocument` and finds what it needs the way
the reader does - attributes and qualifiers by name, never by the numbers one particular file
happens to use. Each change is applied to the tables in memory; :meth:`ModelEditor.snapshot`
gives a reader over the unsaved state, and :meth:`ModelEditor.save` writes the file.

Edits follow Ramus's own bookkeeping. A value that belongs to an older branch than the one the
file was saved on is not overwritten - a new version of it is added on the current branch, the
way Ramus records a change - so the history a Ramus 3 file carries stays intact.
"""

from __future__ import annotations

import io
import os
import shutil
import struct
from typing import Dict, List, Optional

try:
    from .rsf_document import RsfDocument, Row
    from .ramus_rsf import RsfModel
except ImportError:  # pragma: no cover - script execution
    from rsf_document import RsfDocument, Row
    from ramus_rsf import RsfModel

TEXTS = "Core/attribute_texts"


class EditError(ValueError):
    """A change that cannot be made as asked - the message says why and what to do instead."""


class ModelEditor:
    """One .rsf file open for changing."""

    def __init__(self, path: str):
        self.path = os.path.abspath(path)
        self.doc = RsfDocument(path)
        self._backed_up = False

    # ------------------------------------------------------------- reading back

    @property
    def changed(self) -> bool:
        return self.doc.changed

    def snapshot(self) -> RsfModel:
        """A reader over the model as it is now, unsaved changes included."""
        return RsfModel(io.BytesIO(self.doc.to_bytes()))

    # ------------------------------------------------------------------ lookups

    def _element_row(self, element_id: int) -> Row:
        branch = self.doc.current_branch()
        for r in self.doc.table("elements").where(ELEMENT_ID=element_id):
            if self.doc.alive(r, branch):
                return r
        raise EditError(f"There is no element with id {element_id} in this model. Ids come "
                        f"from get_function_tree and get_diagram.")

    def _name_attribute(self, element_id: int) -> int:
        """The text attribute that names this element: the one its qualifier designates."""
        qid = int(self._element_row(element_id)["QUALIFIER_ID"])
        for r in self.doc.table("qualifiers").where(QUALIFIER_ID=qid):
            attr = r.get("ATTRIBUTE_FOR_NAME")
            if attr not in (None, "", "-1"):
                return int(attr)
        raise EditError(f"Element {element_id} belongs to a catalog with no name attribute, "
                        f"so it has no name to change.")

    def _require_attribute(self, name: str) -> int:
        attr = self.doc.attribute_id(name)
        if attr is None:
            raise EditError(f"This file has no {name} attribute - it does not look like an "
                            f"IDEF0 model.")
        return attr

    def _is_activity(self, element_id: int) -> bool:
        bounds = self.doc.attribute_id("F_BOUNDS")
        return bool(self.doc.table("IDEF0/attribute_rectangles").where(
            ATTRIBUTE_ID=bounds, ELEMENT_ID=element_id))

    # ------------------------------------------------------------ text values

    def _live_value_row(self, table: str, attribute_id: int, element_id: int) -> Optional[Row]:
        """The row holding this element's value as of the current branch, if any: the newest
        version that is not newer than the branch the file is on."""
        branch = self.doc.current_branch()
        best: Optional[Row] = None
        best_branch = -1
        for r in self.doc.table(table).where(ATTRIBUTE_ID=attribute_id, ELEMENT_ID=element_id):
            b = int(r.get("VALUE_BRANCH_ID", "0") or 0)
            if b <= branch and b >= best_branch:
                best, best_branch = r, b
        return best

    def _set_text(self, element_id: int, attribute_id: int, value: str) -> None:
        t = self.doc.table(TEXTS)
        row = self._live_value_row(TEXTS, attribute_id, element_id)
        branch = self.doc.current_branch()
        if row is not None and (not t.has("VALUE_BRANCH_ID")
                                or int(row.get("VALUE_BRANCH_ID", "0") or 0) == branch):
            t.set(row, VALUE=value)
        else:
            self.doc.add_row(TEXTS, {"ATTRIBUTE_ID": attribute_id, "ELEMENT_ID": element_id,
                                     "VALUE": value})

    # ------------------------------------------------------------------ edits

    def rename_activity(self, element_id: int, name: str) -> Dict[str, object]:
        """Give an activity (a box) a new name."""
        name = _clean_name(name, "activity")
        if not self._is_activity(element_id):
            raise EditError(f"Element {element_id} is not an activity box. Use the ids "
                            f"get_diagram reports for boxes.")
        old = self._current_name(element_id)
        self._set_text(element_id, self._name_attribute(element_id), name)
        self._sync_element_name(element_id, name)
        return {"id": element_id, "old_name": old, "name": name}

    def rename_flow(self, stream_id: int, name: str) -> Dict[str, object]:
        """Give a flow (the thing an arrow carries, shared by all its segments) a new name."""
        name = _clean_name(name, "flow")
        streams = self.doc.qualifier_id("F_STREAMS")
        row = self._element_row(stream_id)
        if streams is None or int(row["QUALIFIER_ID"]) != streams:
            raise EditError(f"Element {stream_id} is not a flow. get_diagram reports each "
                            f"flow's stream id.")
        attr = self._require_attribute("F_STREAM_NAME")
        old_row = self._live_value_row(TEXTS, attr, stream_id)
        old = (old_row or {}).get("VALUE", "")
        self._set_text(stream_id, attr, name)
        return {"stream": stream_id, "old_name": old, "name": name}

    def _current_name(self, element_id: int) -> str:
        row = self._live_value_row(TEXTS, self._name_attribute(element_id), element_id)
        if row is not None and row.get("VALUE"):
            return row["VALUE"]
        return self._element_row(element_id).get("ELEMENT_NAME", "")

    def _sync_element_name(self, element_id: int, name: str) -> None:
        """Files from Ramus 2 also keep a copy of the name in the element row; keep it in step
        so an old reader agrees. Ramus 3 leaves that cell empty, and so does this."""
        row = self._element_row(element_id)
        if row.get("ELEMENT_NAME"):
            self.doc.table("elements").set(row, ELEMENT_NAME=name)

    # --------------------------------------------------------- adding a box

    def add_activity(self, parent_id: int, name: str, x: Optional[float] = None,
                     y: Optional[float] = None, width: Optional[float] = None,
                     height: Optional[float] = None) -> Dict[str, object]:
        """Add an activity box to the decomposition of ``parent_id``.

        The box goes last in its sheet's order, so it gets the next number (A3 after A1, A2).
        Without a position it is placed the way IDEF0 lays a sheet out - down the diagonal
        from the box before it - in the first spot that overlaps nothing. Its look (font,
        colours, kind of box) is taken from a box already on the sheet, so it matches. Adding
        the first box under an activity gives that activity its decomposition.
        """
        name = _clean_name(name, "activity")
        model = self.snapshot()
        acts = model.activities()
        if parent_id not in acts:
            if any(d.parent_id == parent_id and d.node == "A-0" for d in model.diagrams()):
                raise EditError("The context diagram (A-0) holds exactly one activity, the top "
                                "of the model. Add the box under A0 - or any other activity - "
                                "to put it on that activity's decomposition.")
            raise EditError(f"There is no activity with id {parent_id} to decompose. Use the "
                            f"ids get_function_tree reports.")
        parent = acts[parent_id]
        sheet = next((d for d in model.diagrams() if d.parent_id == parent_id), None)
        siblings = list(sheet.activities) if sheet else []
        frame = sheet.frame if sheet else model.frame()
        template = siblings[-1] if siblings else parent

        w = float(width) if width else _typical(siblings, "width", 120.0)
        h = float(height) if height else _typical(siblings, "height", 60.0)
        if x is None or y is None:
            x, y = _free_spot(sheet, frame, w, h)
        _inside_frame(float(x), float(y), w, h, frame)

        doc = self.doc
        parent_row = self._element_row(parent_id)
        new_id = doc.new_element_id()
        doc.add_row("elements", {
            "ELEMENT_ID": new_id,
            "QUALIFIER_ID": int(parent_row["QUALIFIER_ID"]),
            # Ramus 2 kept a copy of the name in the element row; Ramus 3 leaves it empty.
            "ELEMENT_NAME": name if parent_row.get("ELEMENT_NAME") else "",
        }, lenient=True)

        hier = doc.table("Core/attribute_hierarchicals")
        parent_hier = hier.where(ELEMENT_ID=parent_id)
        template_hier = parent_hier[0] if parent_hier else (hier.rows[0] if hier.rows else {})
        doc.add_row("Core/attribute_hierarchicals", {
            "ATTRIBUTE_ID": int(template_hier.get("ATTRIBUTE_ID", "1")),
            "ELEMENT_ID": new_id,
            "ICON_ID": int(template_hier.get("ICON_ID", "-1") or -1),
            "PARENT_ELEMENT_ID": parent_id,
            "PREVIOUS_ELEMENT_ID": siblings[-1].element_id if siblings else -1,
        }, lenient=True)

        self._set_text(new_id, self._name_attribute(parent_id), name)
        self._add_value("IDEF0/attribute_rectangles", "F_BOUNDS", new_id,
                        {"X": float(x), "Y": float(y), "WIDTH": w, "HEIGHT": h})
        self._add_value("IDEF0/attribute_statuses", "F_STATUS", new_id,
                        {"TYPE": 0, "OTHER_NAME": ""})
        self._copy_value("IDEF0/attribute_fonts", "F_FONT", template.element_id, new_id,
                         ("NAME", "SIZE", "STYLE"), {"NAME": "Dialog", "SIZE": 10, "STYLE": 0})
        self._copy_value("IDEF0/attribute_function_types", "F_TYPE", template.element_id, new_id,
                         ("TYPE",), {"TYPE": 1})
        self._copy_value("IDEF0/attribute_colors", "F_BACKGROUND", template.element_id, new_id,
                         ("COLOR",), {"COLOR": -1})
        self._copy_value("IDEF0/attribute_colors", "F_FOREGROUND", template.element_id, new_id,
                         ("COLOR",), {"COLOR": -16777216})
        if doc.has_table("IDEF0/attribute_decomposition_types"):
            self._copy_value("IDEF0/attribute_decomposition_types", "F_DECOMPOSITION_TYPE",
                             template.element_id, new_id, ("TYPE",), {"TYPE": -1})
        # A box with no decomposition: Ramus 3 stores no diagram data for it, Ramus 2 an
        # empty diagram.
        self._add_value("IDEF0/attribute_visual_datas", "F_VISUAL_DATA", new_id,
                        {"DATA": b"" if self._version() == 2 else _EMPTY_V1})
        if not siblings:
            self._give_a_decomposition(parent_id)

        number = (parent.number + str(len(siblings) + 1)) if parent.number != "A0" \
            else "A" + str(len(siblings) + 1)
        return {"id": new_id, "number": number, "name": name, "parent": parent_id,
                "x": float(x), "y": float(y), "width": w, "height": h}

    def _version(self) -> int:
        """2 for a Ramus 3 file (arrow routes in tables), 1 for Ramus 2 (routes in blobs)."""
        return 2 if self.doc.has_table("IDEF0/attribute_sector_points") else 1

    def _give_a_decomposition(self, element_id: int) -> None:
        """An activity getting its first child needs diagram data of its own: an empty diagram
        in the file's format, where before it had none."""
        attr = self._require_attribute("F_VISUAL_DATA")
        table = self.doc.table("IDEF0/attribute_visual_datas")
        empty = _EMPTY_V2 if self._version() == 2 else _EMPTY_V1
        row = self._live_value_row("IDEF0/attribute_visual_datas", attr, element_id)
        if row is None:
            self.doc.add_row("IDEF0/attribute_visual_datas",
                             {"ATTRIBUTE_ID": attr, "ELEMENT_ID": element_id, "DATA": empty},
                             lenient=True)
        elif not row.get("DATA"):
            table.set(row, DATA=empty)

    def _add_value(self, table: str, attribute: str, element_id: int,
                   values: Dict[str, object]) -> None:
        if not self.doc.has_table(table):
            return
        row = {"ATTRIBUTE_ID": self._require_attribute(attribute), "ELEMENT_ID": element_id}
        row.update(values)
        self.doc.add_row(table, row, lenient=True)

    def _copy_value(self, table: str, attribute: str, source_id: int, element_id: int,
                    columns, default: Dict[str, object]) -> None:
        """Give ``element_id`` the same value ``source_id`` has, or the default if it has none."""
        if not self.doc.has_table(table):
            return
        attr = self.doc.attribute_id(attribute)
        if attr is None:
            return
        source = self._live_value_row(table, attr, source_id)
        t = self.doc.table(table)
        values: Dict[str, object] = {}
        for c in columns:
            if source is not None and c in source:
                text = source[c]
                kind = t.type_of(c).upper()
                values[c] = int(text) if kind in ("BIGINT", "INTEGER") else text
            else:
                values[c] = default.get(c)
        self._add_value(table, attribute, element_id, values)

    # ------------------------------------------------------------------- save

    def save(self, path: Optional[str] = None, overwrite: bool = False) -> Dict[str, object]:
        """Write the model.

        With a path: write there (an existing file is replaced only with ``overwrite``). With
        none: write back over the file that was opened - but the first time, the original is
        copied to ``<name>.backup.rsf`` beside it, so a mistaken edit can always be undone.
        """
        result: Dict[str, object] = {}
        if path is None or os.path.abspath(path) == self.path:
            target = self.path
            if not self._backed_up:
                backup = _backup_path(self.path)
                shutil.copy2(self.path, backup)
                self._backed_up = True
                result["backup"] = backup
            self.doc.save(target, overwrite=True)
        else:
            target = self.doc.save(path, overwrite=overwrite)
        result["saved"] = target
        return result


# An empty diagram's data, per format: Ramus 3 keeps only free texts in the blob (version 2,
# none of them); Ramus 2 keeps the arrow routes there too (version 1, no sectors, no texts).
_EMPTY_V2 = struct.pack("<ii", 2, 0)
_EMPTY_V1 = struct.pack("<iii", 1, 0, 0)

_GAP = 24.0  # room kept between boxes, and between a box and the frame


def _typical(boxes, attribute: str, default: float) -> float:
    """The median size of the boxes already on a sheet, so a new one matches them."""
    values = sorted(getattr(b, attribute) for b in boxes if getattr(b, attribute) > 0)
    return values[len(values) // 2] if values else default


def _overlaps(a, b, gap: float) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return not (ax + aw + gap <= bx or bx + bw + gap <= ax or ay + ah + gap <= by or by + bh + gap <= ay)


_LINE_CLEARANCE = 10.0  # how close a new box may come to an arrow's line
_LABEL_CLEARANCE = 6.0  # ... and to an arrow's name or a free text


def _obstacles(sheet):
    """What a new box must not land on: the boxes, every arrow segment (as the thin rectangle
    it sweeps) and every label and free text on the sheet."""
    boxes, lines, labels = [], [], []
    if sheet is None:
        return boxes, lines, labels
    boxes = [(b.x, b.y, b.width, b.height) for b in sheet.activities]
    for a in sheet.arrows:
        for p, q in zip(a.points, a.points[1:]):
            lines.append((min(p[0], q[0]), min(p[1], q[1]), abs(p[0] - q[0]), abs(p[1] - q[1])))
        if a.label is not None and a.label.width > 0 and a.label.height > 0:
            labels.append((a.label.x, a.label.y, a.label.width, a.label.height))
    for t in sheet.texts:
        labels.append((t.x, t.y, t.width, t.height))
    return boxes, lines, labels


def _free_spot(sheet, frame, w: float, h: float):
    """Where a new box goes when no position is given.

    IDEF0 lays a sheet out down the diagonal, so the ideal spot is just right of and below the
    last box (top left for the first). Of all the places the box fits without touching another
    box, an arrow or a label, the one nearest that ideal wins. A sheet too crowded for that
    first lets the box cross arrow lines, then labels - but never another box.
    """
    left, top, right, bottom = frame
    boxes, lines, labels = _obstacles(sheet)
    ordered = list(sheet.activities) if sheet else []
    if ordered:
        last = ordered[-1]
        ideal = (last.x + last.width + _GAP, last.y + last.height + _GAP)
    else:
        ideal = (left + 4 * _GAP, top + 3 * _GAP)

    tiers = (
        ((boxes, _GAP), (lines, _LINE_CLEARANCE), (labels, _LABEL_CLEARANCE)),
        ((boxes, _GAP), (labels, _LABEL_CLEARANCE)),
        ((boxes, _GAP),),
    )
    step = _GAP / 2
    xs, ys = [], []
    x = left + _GAP
    while x + w <= right - _GAP:
        xs.append(x)
        x += step
    y = top + _GAP
    while y + h <= bottom - _GAP:
        ys.append(y)
        y += step
    candidates = [(cx, cy) for cy in ys for cx in xs]
    candidates.sort(key=lambda c: (c[0] - ideal[0]) ** 2 + (c[1] - ideal[1]) ** 2)

    for tier in tiers:
        for cx, cy in candidates:
            rect = (cx, cy, w, h)
            if not any(_overlaps(rect, o, gap) for group, gap in tier for o in group):
                return cx, cy
    raise EditError("There is no free room on this sheet for another box of that size. Give "
                    "x and y explicitly, make the box smaller, or move the others first.")


def _inside_frame(x: float, y: float, w: float, h: float, frame) -> None:
    left, top, right, bottom = frame
    if w <= 0 or h <= 0:
        raise EditError("A box needs a positive width and height.")
    if x < left or y < top or x + w > right or y + h > bottom:
        raise EditError(f"That box would stick out of the sheet. The drawable area runs "
                        f"{left:g}..{right:g} across and {top:g}..{bottom:g} down.")


def _clean_name(name: str, what: str) -> str:
    if name is None or not str(name).strip():
        raise EditError(f"A {what} needs a name; got an empty one.")
    return str(name).strip()


def _backup_path(path: str) -> str:
    """``model.rsf`` -> ``model.backup.rsf``; a second backup never replaces the first."""
    root, ext = os.path.splitext(path)
    candidate = f"{root}.backup{ext}"
    n = 2
    while os.path.exists(candidate):
        candidate = f"{root}.backup{n}{ext}"
        n += 1
    return candidate
