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
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

try:
    from . import router as rt
    from . import visual_data as vd
    from .rsf_document import RsfDocument, Row
    from .ramus_rsf import RsfModel
    from .scene import text_width, wrap
except ImportError:  # pragma: no cover - script execution
    import router as rt
    import visual_data as vd
    from rsf_document import RsfDocument, Row
    from ramus_rsf import RsfModel
    from scene import text_width, wrap

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

    # ------------------------------------------------------------ adding an arrow

    def add_arrow(self, sheet_id: int, source: Dict[str, object], target: Dict[str, object],
                  name: Optional[str] = None, flow: Optional[int] = None) -> Dict[str, object]:
        """Draw an arrow on the sheet that decomposes ``sheet_id``.

        ``source`` is where it comes from: ``{"activity": id}`` (an output, leaving the right
        side of a box), or ``{"frame": "input" | "control" | "mechanism"}`` - something coming
        in from outside the decomposed activity. ``target`` is where it goes:
        ``{"activity": id, "role": "input" | "control" | "mechanism"}``, or
        ``{"frame": "output"}`` - leaving the decomposed activity.

        The arrow carries a new flow called ``name``, or the existing flow ``flow``. Where it
        meets the frame, or a box that has a decomposition of its own, it is joined to the
        same flow on the other level if that arrow is there and not yet continued - the same
        flow and the same node, which is what keeps IDEF0's arrows balanced between levels.
        With nothing to join it is left as a tunnel, as Ramus shows it.

        The route is orthogonal, leaves and enters by the sides the roles call for, keeps clear
        of the boxes and avoids crossing or running along the other arrows; its name is put
        beside its longest straight piece.
        """
        if self._version() != 2:
            raise EditError("Arrows can be added to files in the Ramus 3 format only. A Ramus 2 "
                            "file keeps its arrow routes in a binary diagram record: open it in "
                            "Ramus 3 and save it once, then draw arrows on it here.")
        model = self.snapshot()
        sheets = {d.parent_id: d for d in model.diagrams()}
        acts = model.activities()
        sheet = sheets.get(sheet_id)
        if sheet is None:
            raise EditError(f"Activity {sheet_id} has no decomposition to draw on. Add a box "
                            f"under it first (add_activity), or pick a sheet from list_diagrams.")
        on_sheet = {a.element_id: a for a in sheet.activities}
        src = _end_spec(source, "source", on_sheet)
        dst = _end_spec(target, "target", on_sheet)
        if src.kind == "frame" and dst.kind == "frame":
            raise EditError("An arrow from the frame straight back to the frame touches no box. "
                            "One end has to be on an activity.")
        if src.activity is not None and dst.activity is not None \
                and src.activity.element_id == dst.activity.element_id:
            raise EditError("An arrow from a box back into the same box is not drawn this way in "
                            "IDEF0; feed the output to another activity.")

        # ---- the flow, and the arrows on the other level it may be joined to
        names = self._stream_names()
        if flow is not None:
            if flow not in names:
                raise EditError(f"There is no flow {flow}. get_diagram reports each arrow's "
                                f"stream id.")
            if name and name.strip() != names[flow]:
                raise EditError(f"Flow {flow} is called {names[flow]!r}. Leave the name out to "
                                f"draw it, or rename it with rename_flow.")
            stream, label = flow, names[flow]
        elif name and name.strip():
            stream, label = None, name.strip()
        else:
            raise EditError("An arrow needs a name (a new flow) or a flow id (an existing one).")

        links: Dict[str, Tuple[int, int]] = {}
        for which, spec in (("source", src), ("target", dst)):
            partner = self._partner(spec, which, sheet, sheets, acts, stream, label)
            if partner is not None:
                node, partner_stream = partner
                if stream is None:
                    stream = partner_stream
                if partner_stream == stream:
                    links[which] = (node, partner_stream)

        # ---- geometry
        left, top, right, bottom = sheet.frame
        frame_rect = (left, top, right - left, bottom - top)
        boxes = [(a.x, a.y, a.width, a.height) for a in sheet.activities]
        lines = [(p, q) for a in sheet.arrows for p, q in zip(a.points, a.points[1:])]
        s_along, d_along = self._attach_both(src, dst, sheet, frame_rect)
        start = rt.point_on(_rect_of(src, frame_rect), src.side, s_along)
        end = rt.point_on(_rect_of(dst, frame_rect), dst.side, d_along)
        out = rt.OUTWARD
        start_dir = out[src.side] if src.kind == "activity" else _back(out[src.side])
        end_dir = _back(out[dst.side]) if dst.kind == "activity" else out[dst.side]
        points = rt.route(start, start_dir, end, end_dir, boxes, frame_rect, lines)

        style_hex = self._sheet_style(sheet_id)
        font_size = _font_size_of(style_hex)
        text_box = _place_label(label, font_size, points, boxes,
                                [(a.label.x, a.label.y, a.label.width, a.label.height)
                                 for a in sheet.arrows if a.label is not None] +
                                [(t.x, t.y, t.width, t.height) for t in sheet.texts],
                                frame_rect, lines)

        # ---- write it
        doc = self.doc
        if stream is None:
            stream = self._new_stream(label)
        sector = doc.new_element_id()
        doc.add_row("elements", {"ELEMENT_ID": sector, "ELEMENT_NAME": "",
                                 "QUALIFIER_ID": self._require_qualifier("F_SECTORS")},
                    lenient=True)
        self._add_link(sector, "F_FUNCTION_SECTOR", sheet_id)
        self._add_link(sector, "F_SECTOR_STREAM", stream)
        self._add_value("IDEF0/attribute_sectors", "F_SECTOR_ATTRIBUTE", sector, {
            "CREATE_POS": 0.0, "CREATE_STATE": -1, "SHOW_TEXT": 1, "TEXT_ALIGMENT": 0,
            "VISUAL_ATTRIBUTES": style_hex,
        })
        nodes = {}
        for which, spec, attr in (("source", src, "F_SECTOR_BORDER_START"),
                                  ("target", dst, "F_SECTOR_BORDER_END")):
            node = links[which][0] if which in links else doc.new_crosspoint()
            nodes[which] = node
            self._add_value("IDEF0/attribute_sector_borders", attr, sector, {
                "BORDER_TYPE": spec.side if spec.kind == "frame" else -1,
                "FUNCTION": spec.activity.element_id if spec.kind == "activity" else -1,
                "FUNCTION_TYPE": spec.side if spec.kind == "activity" else -1,
                "CROSSPOINT": node, "TUNNEL_SOFT": 0,
            })
        self._write_points(sector, points)
        tx, ty, tw, th = text_box
        self._add_value("IDEF0/attribute_sector_properties", "F_SECTOR_PROPERTIES", sector, {
            "SHOW_TEXT": 1, "SHOW_TILDA": 0, "TEXT_X": tx, "TEXT_Y": ty, "TEXT_WIDTH": tw,
            "TEXT_HIEGHT": th, "TILDA_POS": 0.0, "TRANSPARENT": 1,
        })
        return {
            "sector": sector, "stream": stream, "name": label,
            "from": _describe_end(src), "to": _describe_end(dst),
            "joined_to_other_level": sorted(links),
            "route": [(round(x, 2), round(y, 2)) for x, y in points],
        }

    # ---- arrow helpers

    def _require_qualifier(self, name: str) -> int:
        q = self.doc.qualifier_id(name)
        if q is None:
            raise EditError(f"This file has no {name} catalog - it does not look like an "
                            f"IDEF0 model.")
        return q

    def _stream_names(self) -> Dict[int, str]:
        """Every live flow and its name."""
        branch = self.doc.current_branch()
        streams = self.doc.qualifier_id("F_STREAMS")
        attr = self.doc.attribute_id("F_STREAM_NAME")
        names = {}
        for r in self.doc.table("elements").rows:
            if streams is not None and r.get("QUALIFIER_ID") == str(streams) \
                    and self.doc.alive(r, branch):
                eid = int(r["ELEMENT_ID"])
                row = self._live_value_row(TEXTS, attr, eid) if attr is not None else None
                names[eid] = ((row or {}).get("VALUE") or "").strip()
        return names

    def _new_stream(self, name: str) -> int:
        """A new flow, named, and put last in the file's list of flows."""
        doc = self.doc
        stream = doc.new_element_id()
        doc.add_row("elements", {"ELEMENT_ID": stream, "ELEMENT_NAME": "",
                                 "QUALIFIER_ID": self._require_qualifier("F_STREAMS")},
                    lenient=True)
        existing = set(self._stream_names()) - {stream}
        hier = doc.table("Core/attribute_hierarchicals")
        rows = [r for r in hier.rows if r.get("ELEMENT_ID") and int(r["ELEMENT_ID"]) in existing]
        if rows:
            named_as_previous = {r.get("PREVIOUS_ELEMENT_ID") for r in rows}
            last = next((int(r["ELEMENT_ID"]) for r in reversed(rows)
                         if r["ELEMENT_ID"] not in named_as_previous), -1)
            template = rows[0]
            doc.add_row("Core/attribute_hierarchicals", {
                "ATTRIBUTE_ID": int(template.get("ATTRIBUTE_ID", "1")), "ELEMENT_ID": stream,
                "ICON_ID": -1, "PARENT_ELEMENT_ID": -1, "PREVIOUS_ELEMENT_ID": last,
            }, lenient=True)
        self._set_text(stream, self._require_attribute("F_STREAM_NAME"), name)
        return stream

    def _add_link(self, element_id: int, attribute: str, other: int) -> None:
        self.doc.add_row("Core/attribute_other_elements", {
            "ATTRIBUTE_ID": self._require_attribute(attribute), "ELEMENT_ID": element_id,
            "OTHER_ELEMENT": other}, lenient=True)

    def _sheet_style(self, sheet_id: int) -> object:
        """The look most arrows on this sheet have (else in the file), as stored; a plain thin
        black line in Dialog 10 if the file has no arrow to copy."""
        owner_attr = self.doc.attribute_id("F_FUNCTION_SECTOR")
        on_sheet = {r["ELEMENT_ID"] for r in self.doc.table("Core/attribute_other_elements").rows
                    if owner_attr is not None and r.get("ATTRIBUTE_ID") == str(owner_attr)
                    and r.get("OTHER_ELEMENT") == str(sheet_id)}
        counts: Dict[str, int] = {}
        everywhere: Dict[str, int] = {}
        for r in self.doc.table("IDEF0/attribute_sectors").rows:
            v = r.get("VISUAL_ATTRIBUTES")
            if not v:
                continue
            everywhere[v] = everywhere.get(v, 0) + 1
            if r.get("ELEMENT_ID") in on_sheet:
                counts[v] = counts.get(v, 0) + 1
        for pool in (counts, everywhere):
            if pool:
                return max(pool, key=pool.get)  # the masked hex text, copied as is
        return vd.encode_sector_style()

    def _partner(self, spec, which: str, sheet, sheets, acts, stream: Optional[int],
                 label: str) -> Optional[Tuple[int, int]]:
        """The arrow on the other level this end continues, if it is there and not yet
        continued: (its node, its flow). Matched by flow, or by name when the flow is new."""
        side = rt_side_name(spec.side)
        if spec.kind == "frame":
            owner = acts.get(sheet.parent_id)
            if owner is None:
                return None  # the context diagram: nothing above it
            other = sheets.get(owner.parent_id)
            box_id = owner.element_id
            want = "end" if which == "source" else "start"
            here = {e.node for a in sheet.arrows for e in (a.start, a.end)
                    if e.kind == "frame" and e.side == side}
            candidates = [(a, getattr(a, want)) for a in (other.arrows if other else [])]
            candidates = [(a, e) for a, e in candidates if e.kind == "activity"
                          and e.activity_id == box_id and e.side == side]
        else:
            child = sheets.get(spec.activity.element_id)
            if child is None:
                return None  # a box with no decomposition: nothing below it
            want = "start" if which == "target" else "end"
            here = {e.node for a in sheet.arrows for e in (a.start, a.end)
                    if e.kind == "activity" and e.activity_id == spec.activity.element_id
                    and e.side == side}
            candidates = [(a, getattr(a, want)) for a in child.arrows]
            candidates = [(a, e) for a, e in candidates if e.kind == "frame" and e.side == side]
        key = label.strip().casefold()
        for a, e in candidates:
            if e.node is None or e.node in here:
                continue
            if (stream is not None and a.stream_id == stream) or \
                    (stream is None and a.name.strip().casefold() == key):
                return e.node, a.stream_id
        return None

    def _attach_both(self, src, dst, sheet, frame_rect) -> Tuple[float, float]:
        """Where each end attaches along its side, lined up for a straight arrow when the two
        sides face each other and there is room."""
        def taken(spec):
            out = []
            for a in sheet.arrows:
                for e, p in ((a.start, a.points[0] if a.points else None),
                             (a.end, a.points[-1] if a.points else None)):
                    if p is None or e.side != rt_side_name(spec.side) or e.kind != spec.kind:
                        continue
                    if spec.kind == "activity" and e.activity_id != spec.activity.element_id:
                        continue
                    out.append(p[1] if spec.side in (rt.SIDE_LEFT, rt.SIDE_RIGHT) else p[0])
            return out

        def attach(spec, prefer=None):
            corner = 8.0 if spec.kind == "activity" else 2 * rt.MARGIN
            return rt.attach(_rect_of(spec, frame_rect), spec.side, taken(spec), prefer,
                             corner=corner)

        horizontal = {rt.SIDE_LEFT, rt.SIDE_RIGHT}
        parallel = (src.side in horizontal) == (dst.side in horizontal)
        if src.kind == "activity" and dst.kind == "activity":
            if parallel:
                a, b = _rect_of(src, frame_rect), _rect_of(dst, frame_rect)
                axis = 1 if src.side in horizontal else 0
                lo = max(a[axis], b[axis]) + 8.0
                hi = min(a[axis] + a[axis + 2], b[axis] + b[axis + 2]) - 8.0
                if hi > lo:
                    s = attach(src, (lo + hi) / 2)
                    return s, attach(dst, s)
            return attach(src), attach(dst)
        if src.kind == "frame":
            d = attach(dst)
            return attach(src, d if parallel else None), d
        s = attach(src)
        return s, attach(dst, s if parallel else None)

    def _write_points(self, sector: int, points) -> None:
        """The route, point by point. Points on one horizontal piece share a y ordinate and on
        a vertical piece an x ordinate - the links Ramus keeps a route orthogonal by."""
        doc = self.doc
        x_ord = y_ord = None
        for i, (x, y) in enumerate(points):
            if i == 0:
                x_ord, y_ord = doc.new_ordinate(), doc.new_ordinate()
            else:
                px, py = points[i - 1]
                if py == y:
                    x_ord = doc.new_ordinate()
                elif px == x:
                    y_ord = doc.new_ordinate()
                else:
                    x_ord, y_ord = doc.new_ordinate(), doc.new_ordinate()
            self._add_value("IDEF0/attribute_sector_points", "F_SECTOR_POINTS", sector, {
                "POSITION": i, "POINT_TYPE": -1, "X_POSITION": float(x), "Y_POSITION": float(y),
                "X_ORDINATE_ID": x_ord, "Y_ORDINATE_ID": y_ord,
            })

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


# ------------------------------------------------------------------- arrow ends

ROLE_SIDE = {"output": rt.SIDE_RIGHT, "mechanism": rt.SIDE_BOTTOM,
             "input": rt.SIDE_LEFT, "control": rt.SIDE_TOP}
_SIDE_NAMES = {rt.SIDE_RIGHT: "right", rt.SIDE_BOTTOM: "bottom",
               rt.SIDE_LEFT: "left", rt.SIDE_TOP: "top"}
_LINE_HEIGHT = 0.980078125  # of the font size: Java's height of a line of Dialog
_LABEL_WRAP = 110.0  # a name longer than this runs onto more lines


def rt_side_name(side: int) -> str:
    return _SIDE_NAMES[side]


@dataclass
class _EndSpec:
    kind: str  # "activity" or "frame"
    side: int
    activity: Optional[object] = None  # ramus_rsf.Activity
    role: str = ""


def _end_spec(spec: Dict[str, object], which: str, on_sheet) -> _EndSpec:
    """Read one end of a requested arrow, refusing what IDEF0 does not allow."""
    if not isinstance(spec, dict):
        raise EditError(f'The {which} must be an object such as {{"activity": 12, "role": '
                        f'"input"}} or {{"frame": "input"}}.')
    if "frame" in spec:
        role = str(spec["frame"]).lower()
        allowed = ("input", "control", "mechanism") if which == "source" else ("output",)
        if role not in allowed:
            raise EditError(f"From the frame an arrow comes in as an input, control or "
                            f"mechanism, and to the frame it leaves as the output. "
                            f"{role!r} cannot be the {which}'s frame role.")
        return _EndSpec("frame", ROLE_SIDE[role], role=role)
    if "activity" not in spec:
        raise EditError(f'The {which} names neither an activity nor the frame.')
    try:
        aid = int(spec["activity"])
    except (TypeError, ValueError):
        raise EditError(f"The {which}'s activity must be an id, not {spec['activity']!r}.")
    if aid not in on_sheet:
        raise EditError(f"Activity {aid} is not a box on this sheet. An arrow joins boxes on "
                        f"the same sheet; get_diagram lists them.")
    if which == "source":
        role = str(spec.get("role", "output")).lower()
        if role != "output":
            raise EditError("An arrow leaves a box from its output (the right side). Swap the "
                            "ends: what a box takes in is the arrow's target.")
    else:
        role = str(spec.get("role", "")).lower()
        if role not in ("input", "control", "mechanism"):
            raise EditError("Say what the arrow is to the target box: input, control or "
                            "mechanism.")
    return _EndSpec("activity", ROLE_SIDE[role], activity=on_sheet[aid], role=role)


def _describe_end(spec: _EndSpec) -> Dict[str, object]:
    if spec.kind == "frame":
        return {"frame": spec.role, "side": rt_side_name(spec.side)}
    return {"activity": spec.activity.element_id, "number": spec.activity.number,
            "role": spec.role, "side": rt_side_name(spec.side)}


def _rect_of(spec: _EndSpec, frame_rect):
    if spec.kind == "frame":
        return frame_rect
    a = spec.activity
    return (a.x, a.y, a.width, a.height)


def _back(direction):
    return (-direction[0], -direction[1])


def _font_size_of(style) -> float:
    try:
        data = style if isinstance(style, (bytes, bytearray)) else vd.unmask(str(style))
        font = vd.decode_sector_style(bytes(data)).font
        return float(font.size) if font and font.size else 10.0
    except Exception:  # an unreadable style is drawn with the default; so is its label
        return 10.0


def _place_label(text: str, size: float, points, boxes, labels, frame_rect, lines=()):
    """The rectangle an arrow's name goes in: beside the longest straight piece of its route
    that has room - above a horizontal piece or right of a vertical one, else the other side,
    centred on it or flush with either end - clear of boxes, other names, other arrows' lines
    and the ends of its own (where the head or a tunnel bracket goes), inside the sheet. A
    name that fits nowhere on one or two lines is tried again wrapped narrower."""
    fx, fy, fw, fh = frame_rect
    keep_off = list(boxes) + list(labels)
    for p, q in lines:
        keep_off.append((min(p[0], q[0]), min(p[1], q[1]), abs(p[0] - q[0]), abs(p[1] - q[1])))
    for p in (points[0], points[-1]):
        keep_off.append((p[0] - 10.0, p[1] - 10.0, 20.0, 20.0))

    def clear(r) -> bool:
        x, y, rw, rh = r
        if x < fx + 2 or y < fy + 2 or x + rw > fx + fw - 2 or y + rh > fy + fh - 2:
            return False
        return not any(_overlaps(r, o, 1.5) for o in keep_off)

    pieces = sorted(zip(points, points[1:]),
                    key=lambda ab: -(abs(ab[1][0] - ab[0][0]) + abs(ab[1][1] - ab[0][1])))
    first = None
    for wrap_width in (_LABEL_WRAP, 75.0, 50.0):
        rows = wrap(text, wrap_width, size) or [text]
        w = max(text_width(row, size) for row in rows) + 4.0
        h = len(rows) * _LINE_HEIGHT * size
        for a, b in pieces:
            mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
            if a[1] == b[1]:
                x0, x1 = sorted((a[0], b[0]))
                xs = [mx - w / 2, x0 + 12.0, x1 - w - 12.0]
                options = [(x, y, w, h) for y in (my - h - 3.0, my + 3.0) for x in xs]
            else:
                y0, y1 = sorted((a[1], b[1]))
                ys = [my - h / 2, y0 + 12.0, y1 - h - 12.0]
                options = [(x, y, w, h) for x in (mx + 4.0, mx - w - 4.0) for y in ys]
            for r in options:
                if first is None:
                    first = r
                if clear(r):
                    return r
    return first or (points[0][0], points[0][1] - 13.0, 40.0, 10.0)


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
