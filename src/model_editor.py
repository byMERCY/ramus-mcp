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
        side of a box), ``{"frame": "input" | "control" | "mechanism"}`` - something coming
        in from outside the decomposed activity - or ``{"arrow": segment}``: a branch off an
        arrow already on the sheet, forking from a point on it. ``target`` is where it goes:
        ``{"activity": id, "role": "input" | "control" | "mechanism"}``, or
        ``{"frame": "output"}`` - leaving the decomposed activity.

        The arrow carries a new flow called ``name``, or the existing flow ``flow``; a branch
        carries the flow of the arrow it forks from unless told otherwise, and then shows no
        name of its own (as in Ramus). Where it meets the frame, or a box that has a
        decomposition of its own, it is joined to the same flow on the other level if that
        arrow is there and not yet continued - the same flow and the same node, which is what
        keeps IDEF0's arrows balanced between levels. With nothing to join it is left as a
        tunnel, as Ramus shows it.

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
        src = _end_spec(source, "source", on_sheet, {a.sector_id: a for a in sheet.arrows})
        dst = _end_spec(target, "target", on_sheet)
        trunk = src.arrow
        if src.kind == "frame" and dst.kind == "frame":
            raise EditError("An arrow from the frame straight back to the frame touches no box. "
                            "One end has to be on an activity.")
        if src.activity is not None and dst.activity is not None \
                and src.activity.element_id == dst.activity.element_id:
            raise EditError("An arrow from a box back into the same box is not drawn this way in "
                            "IDEF0; feed the output to another activity.")
        if trunk is not None and dst.kind == "activity" and any(
                e.kind == "activity" and e.activity_id == dst.activity.element_id
                for e in (trunk.start, trunk.end)):
            raise EditError("That branch would lead back into a box the arrow it forks from "
                            "already starts or ends at.")

        # ---- the flow, and the arrows on the other level it may be joined to
        names = self._stream_names()
        if trunk is not None and flow is None and not (name and name.strip()):
            if trunk.stream_id is None:
                raise EditError("The arrow to branch from carries no flow; give the branch a name.")
            flow = trunk.stream_id
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
        stubs = []  # inherited stubs this arrow takes over
        for which, spec in (("source", src), ("target", dst)):
            if spec.kind == "arrow":
                continue  # a fork joins nothing on another level
            partner = self._partner(spec, which, sheet, sheets, acts, stream, label)
            if partner is not None:
                node, partner_stream, stub = partner
                if stream is None:
                    stream = partner_stream
                if partner_stream == stream:
                    links[which] = (node, partner_stream)
                    if stub is not None:
                        stubs.append(stub)
        if stubs:  # gone before routing, so the new arrow does not avoid what it replaces
            self._remove_elements(set(stubs))
            sheet = next(d for d in self.snapshot().diagrams() if d.parent_id == sheet_id)

        # ---- geometry
        layout = _Layout(sheet)
        if trunk is not None:
            # Any piece of the same flow on the sheet may carry the fork: the arrow named, the
            # rest of it past a fork, its other branches.
            kin = [a for a in sheet.arrows if a.has_route and a.flow == trunk.flow
                   and a.stream_id == trunk.stream_id]
            trunk, fork, piece, points = layout.plan_branch(kin or [trunk],
                                                            layout.anchor(dst, "end"))
        else:
            points = layout.plan(layout.anchor(src, "start"), layout.anchor(dst, "end"))
        style_hex = self._sheet_style(sheet_id)
        named = trunk is None or stream != trunk.stream_id
        text_box, tilde = layout.place_label(label, _font_size_of(style_hex), points) \
            if named else (None, False)

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
        first = None
        if trunk is not None:
            x_ord, y_ord, continuation = self._split(trunk, piece, fork, nodes["source"])
            first = (x_ord, y_ord, _point_type(points[0], points[1]))
        self._write_points(sector, points, first)
        if text_box is not None:
            tx, ty, tw, th = text_box
            self._add_value("IDEF0/attribute_sector_properties", "F_SECTOR_PROPERTIES", sector, {
                "SHOW_TEXT": 1, "SHOW_TILDA": 1 if tilde else 0, "TEXT_X": tx, "TEXT_Y": ty,
                "TEXT_WIDTH": tw, "TEXT_HIEGHT": th,
                "TILDA_POS": _share_nearest(points, text_box) if tilde else 0.0,
                "TRANSPARENT": 1,
            })
        else:
            self._add_value("IDEF0/attribute_sector_properties", "F_SECTOR_PROPERTIES", sector,
                            _NO_LABEL)
        if trunk is not None:  # the trunk is shorter now: keep its name in touch with it
            fresh = next(d for d in self.snapshot().diagrams() if d.parent_id == sheet_id)
            cut = next(a for a in fresh.arrows if a.sector_id == trunk.sector_id)
            self._relabel(_Layout(fresh), cut, cut.points)
        # A box with no decomposition gets the arrow-to-be of one, as Ramus 3 gives it.
        frame = self.snapshot().frame()
        for which, spec, p in (("source", src, points[0]), ("target", dst, points[-1])):
            if spec.kind == "activity" and spec.activity.element_id not in sheets:
                self._add_inherited_stub(spec, which, p, nodes[which], stream, style_hex, frame)
        result = {
            "sector": sector, "stream": stream, "name": label,
            "from": _describe_end(src), "to": _describe_end(dst),
            "joined_to_other_level": sorted(links),
            "route": [(round(x, 2), round(y, 2)) for x, y in points],
        }
        moved = self._free_labels(sheet_id, {sector: points})
        if moved:
            result["labels_moved"] = moved
        if trunk is not None:
            result["fork"] = {"at": (round(fork[0], 2), round(fork[1], 2)),
                              "trunk": trunk.sector_id, "continuation": continuation}
        return result

    def _split(self, arrow, piece: int, at, node: int) -> Tuple[int, int, int]:
        """Cut a segment in two at point ``at`` on its piece number ``piece``, the way Ramus
        makes a fork: the segment ends there on a new node, and a new segment of the same flow
        carries on from that node along the rest of the route to where the old one ended (its
        end, node and all, moves over). The cut point's ordinates are shared by every segment
        meeting there; they are returned with the new segment's id."""
        doc = self.doc
        sid = arrow.sector_id
        points_table = doc.table("IDEF0/attribute_sector_points")
        attr = self._require_attribute("F_SECTOR_POINTS")
        rows = sorted(points_table.where(ATTRIBUTE_ID=attr, ELEMENT_ID=sid),
                      key=lambda r: int(r.get("POSITION", "0") or 0))
        if len(rows) < piece + 2:
            raise EditError(f"Arrow segment {sid} has no route to fork from.")
        branch = doc.current_branch()
        if points_table.has("VALUE_BRANCH_ID") and any(
                int(r.get("VALUE_BRANCH_ID", "0") or 0) != branch for r in rows):
            raise EditError("This arrow's route was drawn on an earlier branch of the model's "
                            "history; forking it there is not supported yet.")
        a, b = rows[piece], rows[piece + 1]
        horizontal = a["Y_POSITION"] == b["Y_POSITION"]
        x_ord = doc.new_ordinate() if horizontal else int(a["X_ORDINATE_ID"])
        y_ord = int(a["Y_ORDINATE_ID"]) if horizontal else doc.new_ordinate()
        kind = 0 if horizontal else 1

        # The new segment: same flow, same sheet, same look; no name of its own.
        rest = doc.new_element_id()
        doc.add_row("elements", {"ELEMENT_ID": rest, "ELEMENT_NAME": "",
                                 "QUALIFIER_ID": self._require_qualifier("F_SECTORS")},
                    lenient=True)
        for link in ("F_FUNCTION_SECTOR", "F_SECTOR_STREAM"):
            row = self._live_value_row("Core/attribute_other_elements",
                                       self._require_attribute(link), sid)
            if row is not None:
                self._add_link(rest, link, int(row["OTHER_ELEMENT"]))
        look = self._live_value_row("IDEF0/attribute_sectors",
                                    self._require_attribute("F_SECTOR_ATTRIBUTE"), sid)
        values = {k: v for k, v in (look or {}).items()
                  if k not in ("ELEMENT_ID", "ATTRIBUTE_ID", "VALUE_BRANCH_ID")}
        values.pop("ALTERNATIVE_TEXT", None)
        self._add_value("IDEF0/attribute_sectors", "F_SECTOR_ATTRIBUTE", rest, values)
        self._add_value("IDEF0/attribute_sector_properties", "F_SECTOR_PROPERTIES", rest,
                        _NO_LABEL)

        # Its route: the cut point, then the old points after it, ordinates and all.
        self._add_value("IDEF0/attribute_sector_points", "F_SECTOR_POINTS", rest, {
            "POSITION": 0, "POINT_TYPE": kind, "X_POSITION": float(at[0]),
            "Y_POSITION": float(at[1]), "X_ORDINATE_ID": x_ord, "Y_ORDINATE_ID": y_ord})
        for i, r in enumerate(rows[piece + 1:], start=1):
            points_table.set(r, POSITION=i, ELEMENT_ID=rest)
        # ... and the old segment now stops at the cut.
        self._add_value("IDEF0/attribute_sector_points", "F_SECTOR_POINTS", sid, {
            "POSITION": piece + 1, "POINT_TYPE": kind, "X_POSITION": float(at[0]),
            "Y_POSITION": float(at[1]), "X_ORDINATE_ID": x_ord, "Y_ORDINATE_ID": y_ord})

        borders = doc.table("IDEF0/attribute_sector_borders")
        end_attr = self._require_attribute("F_SECTOR_BORDER_END")
        old_end = self._live_value_row("IDEF0/attribute_sector_borders", end_attr, sid)
        if old_end is not None:
            borders.set(old_end, ELEMENT_ID=rest)
        junction = {"BORDER_TYPE": -1, "FUNCTION": -1, "FUNCTION_TYPE": -1, "CROSSPOINT": node,
                    "TUNNEL_SOFT": 0}
        self._add_value("IDEF0/attribute_sector_borders", "F_SECTOR_BORDER_END", sid, junction)
        self._add_value("IDEF0/attribute_sector_borders", "F_SECTOR_BORDER_START", rest, junction)
        return x_ord, y_ord, rest

    def _add_inherited_stub(self, spec: "_EndSpec", which: str, point, node: int, stream: int,
                            style_hex, frame) -> int:
        """The hidden segment Ramus 3 keeps on the sheet of a box that has no decomposition yet,
        one per arrow on the box: the same flow, on the same node, from (or to) the frame side
        that matches the box's side, 30 long, the other end open and no name shown. When the
        box is decomposed it is the arrow that comes down from the level above."""
        box = spec.activity
        left, top, right, bottom = frame
        horizontal = spec.side in (rt.SIDE_LEFT, rt.SIDE_RIGHT)
        share = ((point[1] - box.y) / box.height) if horizontal else ((point[0] - box.x) / box.width)
        share = min(1.0, max(0.0, share))
        along = (top + (bottom - top) * share) if horizontal else (left + (right - left) * share)
        on_frame = rt.point_on((left, top, right - left, bottom - top), spec.side, along)
        inward = _back(rt.OUTWARD[spec.side])
        inside = (on_frame[0] + inward[0] * _STUB_LENGTH, on_frame[1] + inward[1] * _STUB_LENGTH)
        # The box's output leaves the sheet at the frame; everything else comes in from it.
        points = [inside, on_frame] if which == "source" else [on_frame, inside]

        doc = self.doc
        sector = doc.new_element_id()
        doc.add_row("elements", {"ELEMENT_ID": sector, "ELEMENT_NAME": "",
                                 "QUALIFIER_ID": self._require_qualifier("F_SECTORS")},
                    lenient=True)
        self._add_link(sector, "F_FUNCTION_SECTOR", box.element_id)
        self._add_link(sector, "F_SECTOR_STREAM", stream)
        self._add_value("IDEF0/attribute_sectors", "F_SECTOR_ATTRIBUTE", sector, {
            "ALTERNATIVE_TEXT": "", "CREATE_POS": 0.0, "CREATE_STATE": -1, "SHOW_TEXT": 1,
            "TEXT_ALIGMENT": 0, "VISUAL_ATTRIBUTES": style_hex,
        })
        self._add_value("IDEF0/attribute_sector_borders",
                        "F_SECTOR_BORDER_END" if which == "source" else "F_SECTOR_BORDER_START",
                        sector, {"BORDER_TYPE": spec.side, "FUNCTION": -1, "FUNCTION_TYPE": -1,
                                 "CROSSPOINT": node, "TUNNEL_SOFT": 0})
        self._write_points(sector, points)
        self._add_value("IDEF0/attribute_sector_properties", "F_SECTOR_PROPERTIES", sector, {
            "SHOW_TEXT": 0, "SHOW_TILDA": 0, "TEXT_X": 0.0, "TEXT_Y": 0.0, "TEXT_WIDTH": 0.0,
            "TEXT_HIEGHT": 0.0, "TILDA_POS": 0.0, "TRANSPARENT": 0,
        })
        return sector

    # ------------------------------------------------------------ joining levels

    def join_levels(self, first: int, second: int) -> Dict[str, object]:
        """Join an arrow on a box to the arrow on the frame of that box's decomposition when
        both are drawn but neither continues the other - each a tunnel, the levels out of
        balance. The end on the box and the end on the frame are given one node: that is how
        Ramus carries an arrow from one level to the next. The two segments may be named in
        either order; they must carry the same flow and meet the same side (the box's above,
        the frame's below)."""
        model = self.snapshot()
        sheets = {d.parent_id: d for d in model.diagrams()}
        where = {a.sector_id: (a, d) for d in sheets.values() for a in d.arrows}
        for s in (first, second):
            if s not in where:
                raise EditError(f"There is no arrow segment {s} on any sheet. get_diagram lists "
                                f"each flow's segments.")
        acts = model.activities()

        def pairing(up: int, down: int):
            (ua, us), (da, ds) = where[up], where[down]
            box = acts.get(ds.parent_id)  # the box the lower sheet decomposes
            if box is None or box.parent_id != us.parent_id:
                return None
            for ue, de in ((ua.end, da.start), (ua.start, da.end)):
                if ue.kind == "activity" and ue.activity_id == box.element_id \
                        and de.kind == "frame":
                    return ua, us, ue, da, ds, de, box
            return None

        found = pairing(first, second) or pairing(second, first)
        if found is None:
            raise EditError(f"Segments {first} and {second} are not an arrow on a box and an "
                            f"arrow on the frame of that box's own decomposition, meeting the "
                            f"same way (into the box and in from the frame, or out of the box "
                            f"and out through the frame).")
        ua, us, ue, da, ds, de, box = found
        if ue.side != de.side:
            raise EditError(f"«{ua.name}» is {_a(ue.role)} of {box.number} on {us.node} but "
                            f"«{da.name}» comes onto {ds.node} as {_a(de.role)}; an arrow keeps "
                            f"its role from one level to the next.")
        if ua.stream_id != da.stream_id:
            raise EditError(f"The two carry different flows - «{ua.name}» ({ua.stream_id}) and "
                            f"«{da.name}» ({da.stream_id}). Redraw the lower one with "
                            f"add_arrow(flow={ua.stream_id}) instead.")
        if ue.node is not None and ue.node == de.node:
            raise EditError(f"Segments {ua.sector_id} and {da.sector_id} are already joined.")
        nodes = model.nodes()
        for end, other_sheet in ((ue, ds), (de, us)):
            for sid, _ in nodes.get(end.node, []) if end.node is not None else []:
                hit = where.get(sid)
                if hit is not None and hit[1] is other_sheet:
                    raise EditError(f"Segment {ua.sector_id if end is ue else da.sector_id} is "
                                    f"already continued on {other_sheet.node} by segment {sid}.")

        node = ue.node
        if node is None:
            node = self.doc.new_crosspoint()
            self._set_value("IDEF0/attribute_sector_borders",
                            "F_SECTOR_BORDER_END" if ue is ua.end else "F_SECTOR_BORDER_START",
                            ua.sector_id, {"CROSSPOINT": node})
        self._set_value("IDEF0/attribute_sector_borders",
                        "F_SECTOR_BORDER_START" if de is da.start else "F_SECTOR_BORDER_END",
                        da.sector_id, {"CROSSPOINT": node})
        return {"joined": [ua.sector_id, da.sector_id], "node": node, "stream": ua.stream_id,
                "name": ua.name, "role": ue.role,
                "above": {"sheet": us.node, "activity": box.number, "segment": ua.sector_id},
                "below": {"sheet": ds.node, "segment": da.sector_id}}

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
                 label: str) -> Optional[Tuple[int, int, Optional[int]]]:
        """The arrow on the other level this end continues, if it is there and not yet
        continued: (its node, its flow, and the inherited stub on this sheet that held the node,
        if one did - the new arrow replaces it). Matched by flow, or by name when the flow is
        new."""
        side = rt_side_name(spec.side)
        stubs: Dict[int, int] = {}
        if spec.kind == "frame":
            owner = acts.get(sheet.parent_id)
            if owner is None:
                return None  # the context diagram: nothing above it
            other = sheets.get(owner.parent_id)
            box_id = owner.element_id
            want = "end" if which == "source" else "start"
            here = set()
            for a in sheet.arrows:
                for e, far in ((a.start, a.end), (a.end, a.start)):
                    if e.kind == "frame" and e.side == side:
                        if far.kind == "open":
                            stubs[e.node] = a.sector_id  # an arrow come down, not drawn on yet
                        else:
                            here.add(e.node)
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
                return e.node, a.stream_id, stubs.get(e.node)
        return None

    def _write_points(self, sector: int, points, first=None, last=None) -> None:
        """The route, point by point. Points on one horizontal piece share a y ordinate and on
        a vertical piece an x ordinate - the links Ramus keeps a route orthogonal by.

        ``first`` / ``last`` keep an end's own ordinates and point type - (x ordinate,
        y ordinate, type) - for an end that stays where it was on a node it shares with other
        segments; the piece leading off it then shares that ordinate too."""
        n = len(points)
        x_group, y_group = list(range(n)), list(range(n))
        for i in range(1, n):
            (px, py), (x, y) = points[i - 1], points[i]
            if py == y:
                y_group[i] = y_group[i - 1]
            if px == x:
                x_group[i] = x_group[i - 1]
        ords: Dict[Tuple[str, int], int] = {}
        for end, i in ((first, 0), (last, n - 1)):
            if end is not None:
                if end[0] is not None:
                    ords.setdefault(("x", x_group[i]), end[0])
                if end[1] is not None:
                    ords.setdefault(("y", y_group[i]), end[1])
        for i, (x, y) in enumerate(points):
            for key in (("x", x_group[i]), ("y", y_group[i])):
                if key not in ords:
                    ords[key] = self.doc.new_ordinate()
            kind = -1
            if i == 0 and first is not None:
                kind = first[2]
            elif i == n - 1 and last is not None:
                kind = last[2]
            self._add_value("IDEF0/attribute_sector_points", "F_SECTOR_POINTS", sector, {
                "POSITION": i, "POINT_TYPE": kind, "X_POSITION": float(x), "Y_POSITION": float(y),
                "X_ORDINATE_ID": ords[("x", x_group[i])], "Y_ORDINATE_ID": ords[("y", y_group[i])],
            })

    # ------------------------------------------------------------ moving a box

    def move_activity(self, element_id: int, x: Optional[float] = None,
                      y: Optional[float] = None, width: Optional[float] = None,
                      height: Optional[float] = None) -> Dict[str, object]:
        """Move a box on its sheet, resize it, or both; what is not given stays as it was.

        Its arrows go with it: each one attached to the box is routed again, its end keeping
        its place along the side (scaled with the box), or lining up with the other end where
        that makes a straight line. An arrow the box now sits on is routed round it, and a name
        it now covers is moved off. Other arrows are left alone.
        """
        model = self.snapshot()
        act = model.activities().get(element_id)
        if act is None or not act.has_box:
            raise EditError(f"There is no activity box with id {element_id}. Use the ids "
                            f"get_diagram and get_function_tree report.")
        sheet = next(d for d in model.diagrams()
                     if any(a.element_id == element_id for a in d.activities))
        old = (act.x, act.y, act.width, act.height)
        new = tuple(float(v) if v is not None else o
                    for v, o in zip((x, y, width, height), old))
        _inside_frame(*new, sheet.frame)
        for other in sheet.activities:
            if other.element_id != element_id and _overlaps(new, _box(other), 0.0):
                raise EditError(f"There the box would overlap {other.number} "
                                f"({other.name.strip()}). Pick a free spot - get_diagram "
                                f"gives every box's place.")

        def ends_on_box(a) -> bool:
            return any(e.kind == "activity" and e.activity_id == element_id
                       for e in (a.start, a.end))

        grown = _grow(new, 2.0)
        attached = [a for a in sheet.arrows if a.has_route and ends_on_box(a)]
        crossing = [a for a in sheet.arrows if a.has_route and not ends_on_box(a)
                    and any(rt._crosses_rect(p, q, grown) for p, q in zip(a.points, a.points[1:]))]
        covered = [a for a in sheet.arrows if a not in attached and a not in crossing
                   and a.label is not None and _overlaps(_label_rect(a.label), new, 2.0)]
        if (attached or crossing or covered) and self._version() != 2:
            raise EditError("This is a Ramus 2 file: its arrow routes live in a binary record "
                            "this cannot rewrite yet, so a box with arrows on or near it cannot "
                            "be moved here. Open the file in Ramus 3 and save it once first.")

        self._set_value("IDEF0/attribute_rectangles", "F_BOUNDS", element_id,
                        {"X": new[0], "Y": new[1], "WIDTH": new[2], "HEIGHT": new[3]})
        layout = _Layout(sheet)
        layout.boxes[element_id] = new
        rerouted = []
        for a in attached + crossing:
            start = layout.anchor_from(a.start, "start", a.points, element_id, old)
            end = layout.anchor_from(a.end, "end", a.points, element_id, old)
            points = layout.plan(start, end, exclude=a.sector_id)
            self._replace_route(a.sector_id, points, start, end)
            layout.update(a.sector_id, points)
            rerouted.append({"sector": a.sector_id, "name": a.name,
                             "route": [(round(px, 2), round(py, 2)) for px, py in points]})
        # Names last, once every route is where it will stay.
        moved_labels = [a.sector_id for a in attached + crossing + covered
                        if self._relabel(layout, a, layout.routes[a.sector_id])]
        moved_labels += self._free_labels(sheet.parent_id, {
            r["sector"]: layout.routes[r["sector"]] for r in rerouted})
        return {"id": element_id, "number": act.number, "x": new[0], "y": new[1],
                "width": new[2], "height": new[3], "rerouted": rerouted,
                "labels_moved": moved_labels}

    def _set_value(self, table: str, attribute: str, element_id: int,
                   values: Dict[str, object]) -> bool:
        """Change an element's single value in place - or, if the value belongs to an older
        branch, add a new version of it on the current one. False if it has no such value."""
        row = self._live_value_row(table, self._require_attribute(attribute), element_id)
        if row is None:
            return False
        self._set_row(table, row, values)
        return True

    def _set_row(self, table: str, row: Row, values: Dict[str, object]) -> None:
        """Change a value row - in place if it is the current branch's, else as a new version."""
        t = self.doc.table(table)
        if not t.has("VALUE_BRANCH_ID") \
                or int(row.get("VALUE_BRANCH_ID", "0") or 0) == self.doc.current_branch():
            t.set(row, **values)
        else:
            version = {k: v for k, v in row.items() if k != "VALUE_BRANCH_ID"}
            version.update(values)
            self.doc.add_row(table, version)

    def _replace_route(self, sector: int, points, start: "_Anchor", end: "_Anchor") -> None:
        """Give a segment a new route. An end that stays put on a node keeps its ordinates and
        point type, so it stays tied to the segments it meets there."""
        table = "IDEF0/attribute_sector_points"
        t = self.doc.table(table)
        attr = self._require_attribute("F_SECTOR_POINTS")
        rows = sorted(t.where(ATTRIBUTE_ID=attr, ELEMENT_ID=sector),
                      key=lambda r: int(r.get("POSITION", "0") or 0))
        branch = self.doc.current_branch()
        if t.has("VALUE_BRANCH_ID") and any(int(r.get("VALUE_BRANCH_ID", "0") or 0) != branch
                                            for r in rows):
            raise EditError("This arrow's route was drawn on an earlier branch of the model's "
                            "history; rerouting it there is not supported yet.")

        def kept(anchor, row):
            if anchor.point is None or row is None:
                return None
            ordinate = (lambda c: int(row[c]) if row.get(c) not in (None, "") else None)
            return (ordinate("X_ORDINATE_ID"), ordinate("Y_ORDINATE_ID"),
                    int(row.get("POINT_TYPE", "-1") or -1))

        first = kept(start, rows[0] if rows else None)
        last = kept(end, rows[-1] if rows else None)
        for r in rows:
            t.remove(r)
        self._write_points(sector, points, first, last)

    def tidy_sheet(self, sheet_id: int, passes: int = 3) -> Dict[str, object]:
        """Lay a sheet's arrows out again, one at a time with the others where they are: each
        is rerouted - its ends free to slide along their sides, an end on a node kept - and the
        new route is kept only if it is clearly better (fewer crossings, bends, crowding). A few
        passes let arrows drawn early make way for ones drawn after them. Then the names are
        tidied (see tidy_labels). Boxes do not move."""
        if self._version() != 2:
            raise EditError("Arrows can be rerouted in files in the Ramus 3 format only.")
        rerouted: List[int] = []
        for _ in range(passes):
            sheet = next((d for d in self.snapshot().diagrams() if d.parent_id == sheet_id), None)
            if sheet is None:
                raise EditError(f"Activity {sheet_id} has no decomposition sheet.")
            layout = _Layout(sheet)
            changed = False
            for a in sheet.arrows:
                if not a.has_route:
                    continue
                start = layout.anchor_from(a.start, "start", a.points)
                end = layout.anchor_from(a.end, "end", a.points)
                if start.point is not None and end.point is not None and len(a.points) <= 2:
                    continue
                now = layout.cost_of(layout.routes[a.sector_id], start, end, a.sector_id)
                points, _ = layout.plan_with_cost(start, end, exclude=a.sector_id)
                if layout.cost_of(points, start, end, a.sector_id) < now - _WORTH_IT:
                    self._replace_route(a.sector_id, points, start, end)
                    layout.update(a.sector_id, points)
                    if a.sector_id not in rerouted:
                        rerouted.append(a.sector_id)
                    changed = True
            if not changed:
                break
        for s in self._uncross(sheet_id):
            if s not in rerouted:
                rerouted.append(s)
        result = self.tidy_labels(sheet_id)
        result["rerouted"] = rerouted
        return result

    def _uncross(self, sheet_id: int) -> List[int]:
        """Two arrows that cross often cannot be helped one at a time - each is the best it can
        be with the other where it is - while drawn the other way round neither crosses. For
        each crossing pair: take both up, route them again in either order, and keep the better
        result if it beats what is there."""
        sheet = next(d for d in self.snapshot().diagrams() if d.parent_id == sheet_id)
        layout = _Layout(sheet)
        arrows = {a.sector_id: a for a in sheet.arrows if a.has_route}
        anchors = {s: (layout.anchor_from(a.start, "start", a.points),
                       layout.anchor_from(a.end, "end", a.points)) for s, a in arrows.items()}
        changed: List[int] = []
        for first, second in _crossing_pairs(layout.routes, arrows):
            routes = {s: layout.routes[s] for s in (first, second)}
            now = sum(layout.cost_of(routes[s], *anchors[s], s) for s in routes)
            best = None
            for ends in _swaps(first, second, anchors, routes):
                for order in ((first, second), (second, first)):
                    for s in order:
                        layout.routes.pop(s)
                    trial = {}
                    for s in order + order[:1]:  # the first again, now the second is there
                        layout.routes.pop(s, None)
                        trial[s] = layout.plan(*ends[s], exclude=s)
                        layout.routes[s] = trial[s]
                    total = sum(layout.cost_of(trial[s], *anchors[s], s) for s in trial)
                    if best is None or total < best[0]:
                        best = (total, dict(trial))
                    layout.routes.update(routes)
            if best[0] < now - _WORTH_IT:
                for s, pts in best[1].items():
                    self._replace_route(s, pts, *anchors[s])
                    layout.routes[s] = pts
                    if s not in changed:
                        changed.append(s)
        return changed

    def tidy_labels(self, sheet_id: int) -> Dict[str, object]:
        """Move every arrow name on a sheet that is in the way of something - on a box, another
        name or a line, off the sheet, or lost far from its arrow - back beside its arrow."""
        sheet = next((d for d in self.snapshot().diagrams() if d.parent_id == sheet_id), None)
        if sheet is None:
            raise EditError(f"Activity {sheet_id} has no decomposition sheet.")
        if self._version() != 2:
            raise EditError("Arrow names can be moved in files in the Ramus 3 format only.")
        layout = _Layout(sheet)
        moved = [a.sector_id for a in sheet.arrows
                 if a.has_route and self._relabel(layout, a, a.points)]
        return {"sheet": sheet.node, "labels_moved": moved}

    def _free_labels(self, sheet_id: int, routes) -> List[int]:
        """Move the names a new or rerouted route now runs through."""
        sheet = next(d for d in self.snapshot().diagrams() if d.parent_id == sheet_id)
        layout = _Layout(sheet)
        moved = []
        for a in sheet.arrows:
            box = layout.labels.get(a.sector_id)
            if box is None or a.sector_id in routes or not a.has_route:
                continue
            if any(rt._crosses_rect(p, q, box) for pts in routes.values()
                   for p, q in zip(pts, pts[1:])):
                if self._relabel(layout, a, a.points):
                    moved.append(a.sector_id)
        return moved

    def _relabel(self, layout: "_Layout", arrow, points) -> bool:
        """Keep an arrow's name where it is if it is still clear and close to the route, or
        put it beside the route again; a zig-zag tying it to the line is pointed at the route's
        nearest point either way. True if the name moved."""
        if arrow.label is None:
            return False
        values: Dict[str, object] = {}
        box = layout.labels.get(arrow.sector_id)
        tilde = arrow.label.tilde_pos is not None
        moved = box is None or not layout.label_fits(box, points, arrow.sector_id, tilde)
        if moved:
            box, far = layout.place_label(arrow.name, arrow.font_size, points,
                                          exclude=arrow.sector_id)
            layout.labels[arrow.sector_id] = box
            values.update(TEXT_X=box[0], TEXT_Y=box[1], TEXT_WIDTH=box[2], TEXT_HIEGHT=box[3])
            if far and not tilde:
                tilde = True
                values["SHOW_TILDA"] = 1
        if tilde:
            values["TILDA_POS"] = _share_nearest(points, box)
        if values:
            self._set_value("IDEF0/attribute_sector_properties", "F_SECTOR_PROPERTIES",
                            arrow.sector_id, values)
        return moved

    # ------------------------------------------------------- deleting things

    def delete_arrow(self, sector_id: int) -> Dict[str, object]:
        """Delete one arrow segment, the way Ramus does: a piece left leading nowhere goes
        with it - the trunk of a fork whose last branch this was, the branches of a fork whose
        trunk this was. The flow itself stays in the model's list of flows."""
        sectors = self._live_sectors()
        if sector_id not in sectors:
            raise EditError(f"There is no arrow segment {sector_id}. get_diagram with "
                            f"include_routes lists each segment's sector id.")
        if self._version() != 2:
            raise EditError("This is a Ramus 2 file: its arrows live in a binary record this "
                            "cannot rewrite yet. Open it in Ramus 3 and save it once first.")
        shown = self._shown_arrows()
        removed = self._cascade([sector_id], sectors)
        removed |= self._stranded(removed, sectors)
        unpaired = self._unpaired_after(removed, sectors, shown)
        self._remove_elements(removed)
        return {"removed_arrows": _describe_arrows(removed, shown),
                "left_unpaired_on_other_level": unpaired}

    def delete_activity(self, element_id: int,
                        with_decomposition: bool = False) -> Dict[str, object]:
        """Delete a box from its sheet, with the arrows that ended on it.

        An arrow piece left leading nowhere goes too (as delete_arrow does). The boxes after it
        move up a number. A box that has a decomposition of its own is deleted only with
        ``with_decomposition`` - then everything under it goes, every sheet and every arrow.
        """
        model = self.snapshot()
        acts = model.activities()
        act = acts.get(element_id)
        if act is None:
            raise EditError(f"There is no activity with id {element_id}. Use the ids "
                            f"get_function_tree reports.")
        if act.number == "A0" or act.parent_id not in acts:
            raise EditError("This is the top activity of the model (A0, the box on the context "
                            "diagram); a model cannot be left without it. Rename it instead.")
        subtree = [element_id]
        for parent in subtree:
            subtree += [a.element_id for a in acts.values() if a.parent_id == parent]
        if len(subtree) > 1 and not with_decomposition:
            raise EditError(f"{act.number} has a decomposition of its own ({len(subtree) - 1} "
                            f"activities under it). Pass with_decomposition to delete it with "
                            f"everything under it.")

        sectors = self._live_sectors()
        inside = set(subtree)
        under = {s for s, info in sectors.items() if info["sheet"] in inside}
        seeds = [s for s, info in sectors.items()
                 if s not in under and element_id in (info["start_box"], info["end_box"])]
        if (seeds or under) and self._version() != 2:
            raise EditError("This is a Ramus 2 file: its arrows live in a binary record this "
                            "cannot rewrite yet, so a box with arrows cannot be deleted here. "
                            "Open it in Ramus 3 and save it once first.")
        shown = self._shown_arrows()
        removed = self._cascade(seeds, sectors) | under
        removed |= self._stranded(removed, sectors)
        unpaired = self._unpaired_after(removed, sectors, shown)

        # The box after it in the sheet's order now follows the one before it.
        places = self._live_rows("Core/attribute_hierarchicals")
        mine = places.get(element_id)
        before = int((mine or {}).get("PREVIOUS_ELEMENT_ID", "-1") or -1)
        for eid, r in places.items():
            if eid not in inside and r.get("PREVIOUS_ELEMENT_ID") == str(element_id):
                self._set_row("Core/attribute_hierarchicals", r, {"PREVIOUS_ELEMENT_ID": before})
        self._remove_elements(set(subtree) | removed)
        return {
            "removed_activities": [{"id": i, "number": acts[i].number, "name": acts[i].name}
                                   for i in subtree],
            "removed_arrows": _describe_arrows(removed - under, shown),
            "removed_arrows_under_it": len(under & set(shown)),
            "left_unpaired_on_other_level": unpaired,
        }

    def _live_rows(self, table: str) -> Dict[int, Row]:
        """element -> its row in a one-row-per-element table, as of the current branch."""
        branch = self.doc.current_branch()
        best: Dict[int, Tuple[int, Row]] = {}
        for r in self.doc.table(table).rows:
            if not r.get("ELEMENT_ID"):
                continue
            b = int(r.get("VALUE_BRANCH_ID", "0") or 0)
            eid = int(r["ELEMENT_ID"])
            if b <= branch and (eid not in best or b >= best[eid][0]):
                best[eid] = (b, r)
        return {eid: r for eid, (_, r) in best.items()}

    def _live_sectors(self) -> Dict[int, Dict[str, Optional[int]]]:
        """Every live arrow segment: the sheet it is on, and the node and box at each end."""
        doc = self.doc
        branch = doc.current_branch()
        q = doc.qualifier_id("F_SECTORS")
        live = {int(r["ELEMENT_ID"]) for r in doc.table("elements").rows
                if q is not None and r.get("QUALIFIER_ID") == str(q) and doc.alive(r, branch)}
        owner_attr = str(doc.attribute_id("F_FUNCTION_SECTOR"))
        out: Dict[int, Dict[str, Optional[int]]] = {
            s: {"sheet": None, "start": None, "end": None, "start_box": None, "end_box": None,
                "start_frame": False, "end_frame": False}
            for s in live}
        for r in doc.table("Core/attribute_other_elements").rows:
            s = int(r.get("ELEMENT_ID") or -1)
            if s in out and r.get("ATTRIBUTE_ID") == owner_attr:
                out[s]["sheet"] = int(r["OTHER_ELEMENT"])
        if doc.has_table("IDEF0/attribute_sector_borders"):
            which = {str(doc.attribute_id("F_SECTOR_BORDER_START")): "start",
                     str(doc.attribute_id("F_SECTOR_BORDER_END")): "end"}
            for r in doc.table("IDEF0/attribute_sector_borders").rows:
                s = int(r.get("ELEMENT_ID") or -1)
                end = which.get(r.get("ATTRIBUTE_ID"))
                if s not in out or end is None:
                    continue
                cp = int(r.get("CROSSPOINT") or -1)
                box = int(r.get("FUNCTION") or -1)
                frame = int(r.get("BORDER_TYPE") or -1) >= 0
                out[s][end] = cp if cp >= 0 else None
                out[s][end + "_box"] = box if box >= 0 and not frame else None
                out[s][end + "_frame"] = frame
        return out

    @staticmethod
    def _stranded(removed: set, sectors) -> set:
        """Segments on another level that only continued ones being deleted: an end on the
        frame at a node shared with a deleted segment, the other end open. Ramus 3 keeps such a
        segment on the sheet of every box that has no decomposition yet, one per arrow on the
        box - the arrow-to-be of a future decomposition - and drops it with its partner."""
        nodes = {(sectors[s][end], sectors[s]["sheet"]) for s in removed
                 for end in ("start", "end") if sectors[s][end] is not None}
        out = set()
        for s, info in sectors.items():
            if s in removed:
                continue
            for end, other in (("start", "end"), ("end", "start")):
                cp = info[end]
                if cp is not None and info[end + "_frame"] and info[other] is None and any(
                        cp == n and sheet != info["sheet"] for n, sheet in nodes):
                    out.add(s)
        return out

    @staticmethod
    def _cascade(seeds, sectors) -> set:
        """The segments to delete with ``seeds``: whatever would be left leading nowhere - a
        piece into a node with nothing going on from it, or out of one with nothing coming
        in. Only pieces on the same sheet count; a node shared with another level is not a
        dead end (the other level's arrow becomes a tunnel instead)."""
        ins: Dict[tuple, List[int]] = {}
        outs: Dict[tuple, List[int]] = {}
        for s, info in sectors.items():
            if info["end"] is not None:
                ins.setdefault((info["sheet"], info["end"]), []).append(s)
            if info["start"] is not None:
                outs.setdefault((info["sheet"], info["start"]), []).append(s)
        removed: set = set()
        queue = list(seeds)
        while queue:
            s = queue.pop()
            if s in removed or s not in sectors:
                continue
            removed.add(s)
            info = sectors[s]
            if info["start"] is not None:
                key = (info["sheet"], info["start"])
                if all(o in removed for o in outs.get(key, [])):
                    queue.extend(ins.get(key, []))
            if info["end"] is not None:
                key = (info["sheet"], info["end"])
                if all(i in removed for i in ins.get(key, [])):
                    queue.extend(outs.get(key, []))
        return removed

    def _shown_arrows(self) -> Dict[int, Tuple[str, str]]:
        """sector -> (its name, the node of the sheet it is drawn on), for every arrow segment
        on a diagram - not the hidden ones Ramus keeps under boxes with no decomposition."""
        model = self.snapshot()
        return {a.sector_id: (a.name, d.node) for d in model.diagrams() for a in d.arrows}

    def _unpaired_after(self, removed: set, sectors, shown) -> List[Dict[str, object]]:
        """Arrows on another sheet that continued one being deleted - left as tunnels now."""
        nodes = {(sectors[s][end], sectors[s]["sheet"]) for s in removed
                 for end in ("start", "end") if sectors[s][end] is not None}
        out = []
        for s, info in sectors.items():
            if s in removed or s not in shown:
                continue
            if any(info[end] == n and info["sheet"] != sheet
                   for end in ("start", "end") for n, sheet in nodes if info[end] is not None):
                out.append({"sector": s, "name": shown[s][0], "sheet": shown[s][1]})
        return out

    def _remove_elements(self, ids) -> None:
        """Delete elements the way Ramus 3 does: the element row stays, marked removed on the
        current branch, and its values on that branch go (older branches keep theirs - that
        is history). A Ramus 2 file has no branches: its rows simply go. Links from other
        elements to the deleted ones go too."""
        if not ids:
            return
        doc = self.doc
        branch = doc.current_branch()
        keys = {str(i) for i in ids}
        elements = doc.table("elements")
        for r in list(elements.rows):
            if r.get("ELEMENT_ID") in keys and doc.alive(r, branch):
                if elements.has("REMOVED_BRANCH_ID"):
                    elements.set(r, REMOVED_BRANCH_ID=branch)
                else:
                    elements.remove(r)
        for name in doc.table_names():
            if "/attribute_" not in name:
                continue
            t = doc.table(name)
            if not t.has("ELEMENT_ID"):
                continue
            versioned = t.has("VALUE_BRANCH_ID")
            for r in list(t.rows):
                if versioned and int(r.get("VALUE_BRANCH_ID", "0") or 0) != branch:
                    continue
                if r.get("ELEMENT_ID") in keys or r.get("OTHER_ELEMENT") in keys:
                    t.remove(r)

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
_STUB_LENGTH = 30.0  # an inherited stub on an undecomposed box's sheet, as Ramus 3 draws it


def rt_side_name(side: int) -> str:
    return _SIDE_NAMES[side]


def _a(role: Optional[str]) -> str:
    """"an input", "a control" ..."""
    role = role or "end"
    return f"an {role}" if role[0] in "aeiou" else f"a {role}"


@dataclass
class _EndSpec:
    kind: str  # "activity", "frame", or "arrow" (a fork off an arrow on the sheet)
    side: int
    activity: Optional[object] = None  # ramus_rsf.Activity
    role: str = ""
    arrow: Optional[object] = None  # ramus_rsf.Arrow, for a fork


def _end_spec(spec: Dict[str, object], which: str, on_sheet, arrows=None) -> _EndSpec:
    """Read one end of a requested arrow, refusing what IDEF0 does not allow."""
    if not isinstance(spec, dict):
        raise EditError(f'The {which} must be an object such as {{"activity": 12, "role": '
                        f'"input"}} or {{"frame": "input"}}.')
    if "arrow" in spec:
        if which != "source":
            raise EditError("An arrow can fork off another one, but not end on one (a join) - "
                            "not yet. Give the target as a box or the frame.")
        try:
            sid = int(spec["arrow"])
        except (TypeError, ValueError):
            raise EditError(f"The arrow to branch from must be a segment id, not "
                            f"{spec['arrow']!r}.")
        arrow = (arrows or {}).get(sid)
        if arrow is None or not arrow.has_route:
            raise EditError(f"There is no arrow segment {sid} drawn on this sheet to branch "
                            f"from. get_diagram lists each flow's segments.")
        return _EndSpec("arrow", -1, arrow=arrow)
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
    if spec.kind == "arrow":
        return {"branch_of": spec.arrow.sector_id}
    if spec.kind == "frame":
        return {"frame": spec.role, "side": rt_side_name(spec.side)}
    return {"activity": spec.activity.element_id, "number": spec.activity.number,
            "role": spec.role, "side": rt_side_name(spec.side)}


def _back(direction):
    return (-direction[0], -direction[1])


def _swaps(first: int, second: int, anchors, routes):
    """The ends to try two crossing arrows with: as they are (free to slide), and - where both
    start, or both end, on the same side of the same box or frame - with those two ends
    swapped and pinned, which is what usually undoes a crossing."""
    yield {s: anchors[s] for s in (first, second)}
    for which, at in ((0, 0), (1, -1)):  # starts, ends
        a, b = anchors[first][which], anchors[second][which]
        if a.point is not None or b.point is not None or a.kind != b.kind \
                or a.side != b.side or a.owner != b.owner:
            continue
        pa, pb = routes[first][at], routes[second][at]

        def pinned(anchor, p):
            return _Anchor((p[0], p[1], 0.0, 0.0), anchor.side, anchor.direction, "fixed",
                           point=p)

        swapped = {first: list(anchors[first]), second: list(anchors[second])}
        swapped[first][which] = pinned(a, pb)
        swapped[second][which] = pinned(b, pa)
        yield {s: tuple(v) for s, v in swapped.items()}


def _crossing_pairs(routes, arrows) -> List[Tuple[int, int]]:
    """Pairs of segments whose routes cross, either of which can be rerouted (not both ends
    fixed on nodes)."""
    def movable(s) -> bool:
        a = arrows[s]
        return any(e.kind in ("activity", "frame") for e in (a.start, a.end))

    ids = [s for s in routes if s in arrows and movable(s)]
    out = []
    for i, s in enumerate(ids):
        for t in ids[i + 1:]:
            if any(rt._line_cost(p, q, [(u, v)]) >= rt.CROSSING
                   for p, q in zip(routes[s], routes[s][1:]) if p != q
                   for u, v in zip(routes[t], routes[t][1:]) if u != v):
                out.append((s, t))
    return out


def _point_type(p, q) -> int:
    """POINT_TYPE of a route's point on a node: 0 where the piece off it is horizontal, 1 where
    it is vertical (so Ramus reads it from the files it writes)."""
    return 0 if p[1] == q[1] else 1


# The label row of a segment that shows no name: a fork's continuation, a same-flow branch.
_NO_LABEL = {"SHOW_TEXT": 0, "SHOW_TILDA": 0, "TEXT_X": 0.0, "TEXT_Y": 0.0, "TEXT_WIDTH": 0.0,
             "TEXT_HIEGHT": 0.0, "TILDA_POS": 0.0, "TRANSPARENT": 0}


def _describe_arrows(sectors, shown) -> List[Dict[str, object]]:
    """The deleted segments a person would know: those drawn on a diagram."""
    return [{"sector": s, "name": shown[s][0], "sheet": shown[s][1]}
            for s in sorted(sectors) if s in shown]


def _box(a) -> Tuple[float, float, float, float]:
    return (a.x, a.y, a.width, a.height)


def _grow(r, by: float):
    return (r[0] - by, r[1] - by, r[2] + 2 * by, r[3] + 2 * by)


def _label_rect(label) -> Tuple[float, float, float, float]:
    return (label.x, label.y, label.width, label.height)


# --------------------------------------------------------------- laying arrows out

_SIDE_NUMBERS = {name: side for side, name in _SIDE_NAMES.items()}
_APPROACH = 3 * rt.MARGIN  # how far before a perpendicular end a frame arrow turns towards it
_TRIES = 8  # places tried at each end of an arrow
_FORK_CLEAR = 14.0  # a fork keeps this far from either end of the piece it is on
_WORTH_IT = 5.0  # tidy_sheet keeps a new route only if it is cheaper by more than this


@dataclass
class _Anchor:
    """One end of an arrow about to be routed: the side it may sit anywhere along, or - for an
    end on a node or left open - the exact point it must stay at, and which way the route
    moves there (out of a start, into an end)."""

    rect: Tuple[float, float, float, float]
    side: int
    direction: Tuple[float, float]
    kind: str  # "activity", "frame" or "fixed"
    owner: Optional[int] = None  # the box, for an end on one
    corner: float = 8.0
    point: Optional[Tuple[float, float]] = None  # a fixed end
    keep: Optional[float] = None  # where it was along the side: tried along with the rest


class _Layout:
    """A sheet's geometry while arrows on it are being drawn or redrawn: its boxes, its frame,
    and every arrow's route, ends and name - kept up to date as routes change, so each new
    route and name keeps clear of the ones placed before it."""

    def __init__(self, sheet):
        left, top, right, bottom = sheet.frame
        self.frame = (left, top, right - left, bottom - top)
        self.boxes = {a.element_id: _box(a) for a in sheet.activities}
        self.routes = {a.sector_id: list(a.points) for a in sheet.arrows if a.has_route}
        self.ends = {a.sector_id: (a.start, a.end) for a in sheet.arrows}
        self.labels = {a.sector_id: _label_rect(a.label) for a in sheet.arrows
                       if a.label is not None and a.label.width > 0 and a.label.height > 0}
        self.texts = [(t.x, t.y, t.width, t.height) for t in sheet.texts]

    def lines(self, exclude: Optional[int] = None):
        return [(p, q) for s, pts in self.routes.items() if s != exclude
                for p, q in zip(pts, pts[1:])]

    def names(self, exclude: Optional[int] = None):
        """The rectangles of every name and free text on the sheet but ``exclude``'s."""
        return [r for s, r in self.labels.items() if s != exclude] + self.texts

    def update(self, sector: int, points, label=None) -> None:
        self.routes[sector] = list(points)
        if label is not None:
            self.labels[sector] = label

    def taken(self, anchor: _Anchor, exclude: Optional[int] = None) -> List[float]:
        """Where the other arrows' ends already are on the side this anchor is on."""
        name = _SIDE_NAMES[anchor.side]
        axis = 1 if anchor.side in (rt.SIDE_LEFT, rt.SIDE_RIGHT) else 0
        out = []
        for s, (start, end) in self.ends.items():
            pts = self.routes.get(s)
            if s == exclude or not pts:
                continue
            for e, p in ((start, pts[0]), (end, pts[-1])):
                if e.kind == anchor.kind and e.side == name and \
                        (anchor.kind != "activity" or e.activity_id == anchor.owner):
                    out.append(p[axis])
        return out

    def anchor(self, spec: _EndSpec, which: str) -> _Anchor:
        """The anchor for one end of a new arrow."""
        out = rt.OUTWARD[spec.side]
        if spec.kind == "activity":
            return _Anchor(self.boxes[spec.activity.element_id], spec.side,
                           out if which == "start" else _back(out), "activity",
                           owner=spec.activity.element_id)
        return _Anchor(self.frame, spec.side, _back(out) if which == "start" else out,
                       "frame", corner=2 * rt.MARGIN)

    def anchor_from(self, end, which: str, points, moved: Optional[int] = None,
                    old_rect=None) -> _Anchor:
        """The anchor for one end of an arrow being redrawn. An end on a box or the frame may
        slide along its side (where it was is one of the places tried - scaled to the new size
        if it is on the box that ``moved``); an end on a node, or one left open, stays put."""
        p = points[0] if which == "start" else points[-1]
        if end.kind in ("activity", "frame") and end.side in _SIDE_NUMBERS and \
                (end.kind == "frame" or end.activity_id in self.boxes):
            side = _SIDE_NUMBERS[end.side]
            axis = 1 if side in (rt.SIDE_LEFT, rt.SIDE_RIGHT) else 0
            keep = p[axis]
            out = rt.OUTWARD[side]
            if end.kind == "frame":
                return _Anchor(self.frame, side, _back(out) if which == "start" else out,
                               "frame", corner=2 * rt.MARGIN, keep=keep)
            rect = self.boxes[end.activity_id]
            if end.activity_id == moved and old_rect is not None and old_rect[axis + 2] > 0:
                share = (keep - old_rect[axis]) / old_rect[axis + 2]
                keep = rect[axis] + rect[axis + 2] * min(1.0, max(0.0, share))
            return _Anchor(rect, side, out if which == "start" else _back(out), "activity",
                           owner=end.activity_id, keep=keep)
        rest = points[1:] if which == "start" else points[-2::-1]
        q = next((r for r in rest if r != p), (p[0] + 1.0, p[1]))  # a route of one point
        direction = rt._direction(p, q) if which == "start" else rt._direction(q, p)
        return _Anchor((p[0], p[1], 0.0, 0.0), rt.SIDE_RIGHT, direction, "fixed", point=p)

    def _options(self, anchor: _Anchor, other: _Anchor, other_is_start: bool, other_points,
                 exclude: Optional[int]):
        """Points worth trying for one end: where it was, lined up with each candidate of the
        other end (straight across, or a turn's length before a perpendicular end), and the
        usual even divisions of the side - each with its price."""
        if anchor.point is not None:
            return [(anchor.point, 0.0)]
        axis = 1 if anchor.side in (rt.SIDE_LEFT, rt.SIDE_RIGHT) else 0
        prefs = [anchor.keep] if anchor.keep is not None else []
        sign = 1.0 if other_is_start else -1.0
        for q in other_points:
            prefs.append(q[axis] + sign * other.direction[axis] * _APPROACH)
        found = rt.attach_options(anchor.rect, anchor.side, self.taken(anchor, exclude), prefs,
                                  corner=anchor.corner)
        found.sort(key=lambda o: o[1])
        return [(rt.point_on(anchor.rect, anchor.side, c), price) for c, price in found[:_TRIES]]

    def plan(self, start: _Anchor, end: _Anchor, exclude: Optional[int] = None):
        """The best route between two anchors (see plan_with_cost)."""
        return self.plan_with_cost(start, end, exclude)[0]

    def cost_of(self, points, start: _Anchor, end: _Anchor, exclude: Optional[int] = None):
        """What a route costs as it stands, by the measure plan_with_cost uses."""
        return rt.route_cost(points, self.lines(exclude), self.names(exclude)) + \
            self.crowding(start, points[0], exclude) + self.crowding(end, points[-1], exclude)

    def plan_with_cost(self, start: _Anchor, end: _Anchor, exclude: Optional[int] = None):
        """The best route between two anchors, and its cost: every pair of the places worth
        trying at the two ends is routed, and the cheapest - its length, bends, crossings and
        crowding, plus the price of crowding an end in among others - wins."""
        lines = self.lines(exclude)
        names = self.names(exclude)
        costs = rt.Costs(lines, names)
        boxes = list(self.boxes.values())
        s_base = [p for p, _ in self._options(start, end, False, [], exclude)]
        e_base = [p for p, _ in self._options(end, start, True, [], exclude)]
        # Cheapest-looking pairs first; once even the least a pair could cost is no better than
        # the best route found, neither it nor any after it can win.
        pairs = sorted(
            ((rt.least_cost(sp, start.direction, ep, end.direction) + s_price + e_price, n, sp, ep,
              s_price + e_price)
             for n, ((sp, s_price), (ep, e_price)) in enumerate(
                 (s, e) for s in self._options(start, end, False, e_base, exclude)
                 for e in self._options(end, start, True, s_base, exclude))
             if sp != ep))
        best = None
        for least, _, sp, ep, price in pairs:
            if best is not None and least >= best[0]:
                break
            pts = rt.route(sp, start.direction, ep, end.direction, boxes, self.frame, lines,
                           labels=names, costs=costs)
            cost = rt.route_cost(pts, costs=costs) + price
            if best is None or cost < best[0]:
                best = (cost, pts)
        if best is None:
            raise EditError("There is no way to draw that arrow: its two ends meet.")
        return best[1], best[0]

    def plan_branch(self, trunks, end: _Anchor):
        """Where to fork a branch off one of ``trunks`` (segments of one flow) and how to route
        it to ``end``: every straight piece long enough is tried at its middle, near either
        end, and level with (or a turn's length before) each place worth trying at the target,
        leaving to either side; the cheapest route wins. Returns the segment forked, the fork
        point, the number of the piece it is on, and the branch's route."""
        lines = self.lines()  # the trunk included: a branch must not run back along it
        names = self.names()
        costs = rt.Costs(lines, names)
        boxes = list(self.boxes.values())
        targets = self._options(end, None, True, [], None)[:4]
        best = None
        pieces = [(trunk, k, p, q) for trunk in trunks
                  for route in [self.routes.get(trunk.sector_id) or list(trunk.points)]
                  for k, (p, q) in enumerate(zip(route, route[1:]))]
        for trunk, k, p, q in pieces:
            horizontal = p[1] == q[1]
            along = 0 if horizontal else 1
            lo, hi = sorted((p[along], q[along]))
            if hi - lo < 2 * _FORK_CLEAR:
                continue
            spots = {(lo + hi) / 2, lo + _FORK_CLEAR, hi - _FORK_CLEAR}
            for e, _ in targets:
                spots.add(e[along])
                spots.add(e[along] - end.direction[along] * _APPROACH)
            spots = {min(hi - _FORK_CLEAR, max(lo + _FORK_CLEAR, c)) for c in spots}
            ways = (rt.UP, rt.DOWN) if horizontal else (rt.LEFT, rt.RIGHT)
            for c in sorted(spots):
                fork = (c, p[1]) if horizontal else (p[0], c)
                for way in ways:
                    for e, price in targets:
                        pts = rt.route(fork, way, e, end.direction, boxes, self.frame,
                                       lines, labels=names, costs=costs)
                        cost = rt.route_cost(pts, costs=costs) + price
                        if best is None or cost < best[0]:
                            best = (cost, trunk, fork, k, pts)
        if best is None:
            raise EditError(f"Arrow segment {trunks[0].sector_id} has no straight piece long "
                            f"enough to fork from.")
        return best[1:]

    def place_label(self, text: str, size: float, points, exclude: Optional[int] = None):
        labels = [r for s, r in self.labels.items() if s != exclude] + self.texts
        return _place_label(text, size, points, list(self.boxes.values()), labels, self.frame,
                            self.lines(exclude))

    def label_fits(self, box, points, sector: int, tilde: bool = False) -> bool:
        """Is a name still fine where it is: near its route (a name with a zig-zag may be
        further off), clear of boxes, other names and every line (its own included), and off
        the ends where heads and brackets go?"""
        own = _distance_to_route(box, points)
        if own > (_TILDE_NEAR if tilde else _LABEL_NEAR):
            return False
        if not tilde and any(_distance_to_route(box, [p, q]) < own - 1.0
                             for p, q in self.lines(sector)):
            return False  # nearer another arrow's line than its own: it reads as that one's
        keep_off = list(self.boxes.values()) + self.texts + \
            [r for s, r in self.labels.items() if s != sector] + \
            [_seg_box(p, q) for p, q in self.lines()] + \
            [(points[-1][0] - 9.0, points[-1][1] - 9.0, 18.0, 18.0)]
        return _label_clear(box, keep_off, self.frame)

    def crowding(self, anchor: _Anchor, point, exclude: Optional[int] = None) -> float:
        """What an end at ``point`` pays for sitting closer than the usual spacing to the other
        ends on its side - the same price attach_options puts on a crowded spot."""
        if anchor.point is not None:
            return 0.0
        axis = 1 if anchor.side in (rt.SIDE_LEFT, rt.SIDE_RIGHT) else 0
        gap = min((abs(point[axis] - t) for t in self.taken(anchor, exclude)), default=1e9)
        return 0.0 if gap >= 12.0 else 10.0 + rt.CROWDED * (12.0 - gap)


def _font_size_of(style) -> float:
    try:
        data = style if isinstance(style, (bytes, bytearray)) else vd.unmask(str(style))
        font = vd.decode_sector_style(bytes(data)).font
        return float(font.size) if font and font.size else 10.0
    except Exception:  # an unreadable style is drawn with the default; so is its label
        return 10.0


def _place_label(text: str, size: float, points, boxes, labels, frame_rect, lines=()):
    """The rectangle an arrow's name goes in, and whether it needs a zig-zag back to the line:
    beside the longest straight piece of its route
    that has room - above a horizontal piece or right of a vertical one, else the other side,
    centred on it or flush with either end - clear of boxes, other names, other arrows' lines
    and the ends of its own (where the head or a tunnel bracket goes), inside the sheet. A
    name that fits nowhere on one or two lines is tried again wrapped narrower, and then at
    more places along each piece. Where no place is clear, the one in the way of least - a box
    worst, another name next, a line least - is taken; never one off the sheet."""
    head = points[-1]  # the arrowhead (or a tunnel bracket) is drawn at the end
    weighted = [(r, 100.0) for r in boxes] + [(r, 60.0) for r in labels] + \
        [(_seg_box(p, q), 8.0) for p, q in lines] + \
        [(_seg_box(p, q), 8.0) for p, q in zip(points, points[1:])] + \
        [((head[0] - 9.0, head[1] - 9.0, 18.0, 18.0), 20.0)]
    fx, fy, fw, fh = frame_rect

    def inside(r) -> bool:
        return fx + 2 <= r[0] and fy + 2 <= r[1] and r[0] + r[2] <= fx + fw - 2 \
            and r[1] + r[3] <= fy + fh - 2

    def trouble(r) -> float:
        score = sum(weight for o, weight in weighted if _overlaps(r, o, 1.5))
        # A name nearer another arrow's line than its own reads as that arrow's.
        own = _distance_to_route(r, points)
        if any(_distance_to_route(r, [p, q]) < own - 1.0 for p, q in lines):
            score += 6.0
        return score

    pieces = sorted(zip(points, points[1:]),
                    key=lambda ab: -(abs(ab[1][0] - ab[0][0]) + abs(ab[1][1] - ab[0][1])))
    best = None
    # Beside the line first (gap 3 or 4); then, where that is crowded, further off - a name
    # tied back to its line by a zig-zag, as Ramus draws one.
    for gap, dense in ((None, False), (None, True), (16.0, True), (28.0, True), (44.0, True)):
        tilde = gap is not None
        for wrap_width in (_LABEL_WRAP, 75.0, 50.0):
            rows = wrap(text, wrap_width, size) or [text]
            w = max(text_width(row, size) for row in rows) + 4.0
            h = len(rows) * _LINE_HEIGHT * size
            for a, b in pieces:
                mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
                if a[1] == b[1]:
                    x0, x1 = sorted((a[0], b[0]))
                    xs = _spread(x0, x1, w) if dense else [mx - w / 2, x0 + 12.0, x1 - w - 12.0]
                    off = gap if tilde else 3.0
                    options = [(x, y, w, h) for y in (my - h - off, my + off) for x in xs]
                else:
                    y0, y1 = sorted((a[1], b[1]))
                    ys = _spread(y0, y1, h) if dense else [my - h / 2, y0 + 12.0, y1 - h - 12.0]
                    off = gap if tilde else 4.0
                    options = [(x, y, w, h) for x in (mx + off, mx - w - off) for y in ys]
                for r in options:
                    if not inside(r):
                        continue
                    score = trouble(r)
                    if score == 0:
                        return r, tilde
                    score += 0.5 if tilde else 0.0
                    if best is None or score < best[0]:
                        best = (score, r, tilde)
    if best is not None:
        return best[1], best[2]
    x = min(max(points[0][0], fx + 2), fx + fw - 42)
    y = min(max(points[0][1] - 13.0, fy + 2), fy + fh - 12)
    return (x, y, 40.0, 10.0), False


def _spread(lo: float, hi: float, size: float, step: float = 8.0) -> List[float]:
    """Starts for a name ``size`` long sliding beside a piece from ``lo`` to ``hi`` - from
    overhanging one end by half to overhanging the other, middle first."""
    first, last = lo - size / 2, hi - size / 2
    out = [(first + last) / 2]
    t = first
    while t <= last:
        out.append(t)
        t += step
    return out


_LABEL_NEAR = 24.0  # a name farther than this from its route has lost touch with it ...
_TILDE_NEAR = 64.0  # ... unless a zig-zag ties it back


def _seg_box(p, q):
    """The thin rectangle a straight piece of a route sweeps."""
    return (min(p[0], q[0]), min(p[1], q[1]), abs(p[0] - q[0]), abs(p[1] - q[1]))


def _label_clear(r, keep_off, frame_rect) -> bool:
    fx, fy, fw, fh = frame_rect
    x, y, rw, rh = r
    if x < fx + 2 or y < fy + 2 or x + rw > fx + fw - 2 or y + rh > fy + fh - 2:
        return False
    return not any(_overlaps(r, o, 1.5) for o in keep_off)


def _distance_to_route(box, points) -> float:
    """How far a rectangle is from the nearest piece of a route (0 if they touch)."""
    x, y, w, h = box
    best = float("inf")
    for p, q in zip(points, points[1:]):
        sx, sy, sw, sh = _seg_box(p, q)
        dx = max(0.0, sx - (x + w), x - (sx + sw))
        dy = max(0.0, sy - (y + h), y - (sy + sh))
        best = min(best, (dx * dx + dy * dy) ** 0.5)
    return best


def _share_nearest(points, box) -> float:
    """The share of a route's length at which it comes nearest the middle of ``box`` - where
    Ramus starts the zig-zag that ties a name to its arrow."""
    cx, cy = box[0] + box[2] / 2, box[1] + box[3] / 2
    total = rt.length(points)
    if total <= 0:
        return 0.5
    walked, best, best_at = 0.0, float("inf"), 0.5
    for p, q in zip(points, points[1:]):
        piece = abs(q[0] - p[0]) + abs(q[1] - p[1])
        t = 0.0
        if piece > 0:
            t = ((cx - p[0]) * (q[0] - p[0]) + (cy - p[1]) * (q[1] - p[1])) / (piece * piece)
            t = min(1.0, max(0.0, t))
        nx, ny = p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t
        d = (nx - cx) ** 2 + (ny - cy) ** 2
        if d < best:
            best, best_at = d, (walked + piece * t) / total
        walked += piece
    return best_at


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
