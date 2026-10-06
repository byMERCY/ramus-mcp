"""ramus-mcp — an MCP server that gives an agent eyes and hands for Ramus .rsf models.

Eyes: open a model, list its diagrams, read the activity tree and each diagram's arrows as data,
and render a diagram to PNG so the agent can look at it. Rules: check the model against IDEF0.
Hands: rename activities and flows, add, move and delete boxes, draw, fork, join and delete
arrows - laid out and routed the IDEF0 way - and save a .rsf that Ramus reopens.

Every reading tool shows the model as it stands, unsaved changes included, so a change can be
looked at before it is written. Nothing is written until save_model.

Run it over stdio:  python src/server.py
Point an MCP client at that command. It keeps one model open at a time; open_model switches.
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional

# Make the sibling modules importable whether this is run as a script or a module.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp.server.fastmcp import FastMCP, Image  # noqa: E402

import idef0_rules as rules  # noqa: E402
from model_editor import EditError, ModelEditor  # noqa: E402
from ramus_rsf import Activity, Arrow, Diagram, End, RsfModel  # noqa: E402
from render_png import render_diagram_png  # noqa: E402
from render_svg import render_diagram as render_diagram_svg  # noqa: E402


mcp = FastMCP("ramus")


class _Open:
    """The one model the server is working on: the editor holding the file, and the reader's
    view of it as it now stands."""

    path: Optional[str] = None
    editor: Optional[ModelEditor] = None
    model: Optional[RsfModel] = None
    diagrams: List[Diagram] = []
    activities: Dict[int, Activity] = {}
    findings: List[rules.Finding] = []


def _refresh() -> None:
    """Re-read the model from the editor, so every tool sees the latest change."""
    if _Open.model is not None:
        _Open.model.close()
    _Open.model = _Open.editor.snapshot()
    _Open.diagrams = _Open.model.diagrams()
    _Open.activities = _Open.model.activities()
    _Open.findings = rules.check(_Open.model)


def _activity_id(ref: Any) -> int:
    """An activity by id, or by its IDEF0 number ("A12")."""
    _require()
    if isinstance(ref, int):
        return ref
    text = str(ref).strip()
    if text.lstrip("-").isdigit():
        return int(text)
    for a in _Open.activities.values():
        if a.number.lower() == text.lower():
            return a.element_id
    raise ValueError(f"No activity numbered {text!r}. get_function_tree lists them.")


def _sheet_id(ref: Any) -> int:
    """A sheet by the number of the activity it decomposes ("A0", "A12"), "A-0" for the
    context diagram, or an activity id."""
    _require()
    if str(ref).strip().upper() == "A-0":
        for d in _Open.diagrams:
            if d.node == "A-0":
                return d.parent_id
    return _activity_id(ref)


def _end_ref(spec: Any) -> Dict[str, Any]:
    if not isinstance(spec, dict):
        raise ValueError('An arrow end is an object: {"activity": "A1", "role": "input"} or '
                         '{"frame": "input"}.')
    out = dict(spec)
    if "activity" in out:
        out["activity"] = _activity_id(out["activity"])
    return out


def _finding_key(f: rules.Finding):
    return (f.rule, f.sheet, f.activity, f.flow, f.segment)


def _edited(result: Dict[str, Any]) -> Dict[str, Any]:
    """After a change: the model read again, and what the change did to its IDEF0 standing -
    the findings it brought in (often a to-do: a new box has no arrows yet) and how many it
    settled, with the model's totals."""
    before = {_finding_key(f) for f in _Open.findings}
    _refresh()
    after = {_finding_key(f) for f in _Open.findings}
    result["unsaved_changes"] = True
    result["idef0"] = dict(rules.summary(_Open.findings),
                           new=[f.as_dict() for f in _Open.findings
                                if _finding_key(f) not in before],
                           resolved=len(before - after))
    return result


def _require() -> None:
    if _Open.model is None:
        raise ValueError(
            "No model is open. Call open_model with the path to a .rsf file first."
        )


def _diagram(index: int = 0, node: str = "") -> Diagram:
    """A diagram by node number ("A0", "A12", "A-0" for the context diagram) or, failing
    that, by its position in list_diagrams."""
    _require()
    if node and node.strip():
        wanted = node.strip().lower()
        for d in _Open.diagrams:
            if d.node.lower() == wanted:
                return d
        raise ValueError(
            f"No diagram with node {node!r}. This model has: "
            f"{', '.join(sorted(d.node for d in _Open.diagrams))}."
        )
    if index < 0 or index >= len(_Open.diagrams):
        raise ValueError(
            f"No diagram #{index}. This model has {len(_Open.diagrams)}; "
            f"list_diagrams shows them."
        )
    return _Open.diagrams[index]


def _activity_row(a: Activity) -> Dict[str, Any]:
    return {
        "id": a.element_id,
        "number": a.number,
        "name": a.name,
        "x": round(a.x, 1),
        "y": round(a.y, 1),
        "width": round(a.width, 1),
        "height": round(a.height, 1),
    }


def _end_row(e: End) -> Dict[str, Any]:
    """One end of an arrow: what it is attached to, in the terms an analyst would use."""
    row: Dict[str, Any] = {"kind": e.kind}
    if e.kind == "activity":
        a = _Open.activities.get(e.activity_id)
        row["activity"] = e.activity_id
        row["number"] = a.number if a else ""
        row["name"] = a.name if a else ""
    if e.side:
        row["side"] = e.side
    if e.role:
        row["role"] = e.role
    if e.kind == "junction":
        row["node"] = e.node
    if e.tunnel:
        row["tunnel"] = e.tunnel
    return row


def _flow_rows(d: Diagram) -> List[Dict[str, Any]]:
    """The diagram's arrows as flows: each branching arrow once, with where it starts and
    where it ends. This is the semantic view - what is connected to what, in which role."""
    groups: Dict[int, List[Arrow]] = {}
    for a in d.arrows:
        groups.setdefault(a.flow, []).append(a)
    rows = []
    for flow, arrows in sorted(groups.items()):
        names: List[str] = []
        for a in arrows:
            if a.name and a.name not in names:
                names.append(a.name)
        streams = sorted({a.stream_id for a in arrows if a.stream_id is not None})
        rows.append(
            {
                "flow": flow,
                "stream": streams[0] if len(streams) == 1 else streams,
                "name": " | ".join(names),
                "from": [_end_row(a.start) for a in arrows if a.start.kind != "junction"],
                "to": [_end_row(a.end) for a in arrows if a.end.kind != "junction"],
                "segments": [a.sector_id for a in arrows],
            }
        )
    return rows


def _arrow_row(a: Arrow) -> Dict[str, Any]:
    row: Dict[str, Any] = {
        "id": a.sector_id,
        "flow": a.flow,
        "name": a.name,
        "from": _end_row(a.start),
        "to": _end_row(a.end),
        "route": [[round(x, 1), round(y, 1)] for x, y in a.points],
        "geometry": a.geometry,
    }
    if a.label is not None:
        lb = a.label
        row["label"] = {
            "x": round(lb.x, 1), "y": round(lb.y, 1),
            "width": round(lb.width, 1), "height": round(lb.height, 1),
        }
    if a.color:
        row["color"] = a.color
    if a.stream_id is not None:
        row["stream"] = a.stream_id
    return row


@mcp.tool()
def open_model(path: str, discard_unsaved: bool = False) -> Dict[str, Any]:
    """Open a Ramus .rsf model file and make it the one every other tool reads and edits.

    Returns a short summary: how many diagrams (decomposition sheets) it has, their IDEF0 node
    numbers ("A-0" is the context diagram, "A0" the top decomposition), and the largest few, so
    you know what there is to look at. If the model open now has unsaved changes this refuses,
    so nothing is lost or written behind your back: save_model first, or pass discard_unsaved.
    """
    if not os.path.isfile(path):
        raise ValueError(f"{path} is not a file.")
    if _Open.editor is not None and _Open.editor.changed and not discard_unsaved:
        raise ValueError(f"{_Open.path} has unsaved changes. Call save_model to keep them, "
                         f"or open_model again with discard_unsaved=true to drop them.")
    _Open.editor = ModelEditor(path)
    _Open.path = path
    _refresh()
    return {
        "opened": path,
        "diagrams": len(_Open.diagrams),
        "nodes": sorted((d.node for d in _Open.diagrams), key=lambda n: (len(n), n)),
        "largest": [
            {"index": i, "node": d.node, "parent": d.parent_name or "(root)",
             "activities": len(d.activities)}
            for i, d in enumerate(_Open.diagrams[:8])
        ],
    }


@mcp.tool()
def create_model(path: str, activity: str, author: str = "", project: Optional[str] = None,
                 model_name: str = "Работы", overwrite: bool = False,
                 discard_unsaved: bool = False) -> Dict[str, Any]:
    """Create a new Ramus model file and open it - the way to start a model from nothing.

    ``path`` is the .rsf file to write; ``activity`` names the whole process as one activity, a
    verb phrase ("Провести олимпиаду"): it is the single box A0 on the context diagram A-0.
    ``author`` and ``project`` (by default the file's name) are what the diagram frame shows;
    ``model_name`` is the model's name in Ramus's list of models. An existing file is
    replaced only with overwrite (and copied to <name>.backup.rsf first).

    Then: draw A0's inputs, controls, mechanisms and outputs on the context diagram (add_arrow
    with sheet "A-0"), and decompose it - add_activity under "A0", 3 to 6 boxes - and so on
    down. The file is written at once; later changes wait for save_model.
    """
    if _Open.editor is not None and _Open.editor.changed and not discard_unsaved:
        raise ValueError(f"{_Open.path} has unsaved changes. Call save_model to keep them, "
                         f"or create_model again with discard_unsaved=true to drop them.")
    try:
        editor = ModelEditor.create(path, activity, model_name, author, project, overwrite)
    except FileExistsError as exc:
        raise ValueError(str(exc)) from None
    _Open.editor = editor
    _Open.path = editor.path
    _refresh()
    return {"created": editor.path, "model": model_name, "top_activity": "A0",
            "sheets": [d.node for d in _Open.diagrams],
            "next": "Draw the context: add_arrow(sheet=\"A-0\", ...) for A0's inputs, controls, "
                    "mechanisms and outputs; then add_activity(parent=\"A0\", ...) to "
                    "decompose it."}


@mcp.tool()
def current_model() -> Dict[str, Any]:
    """Say which model is open, if any."""
    if _Open.model is None:
        return {"open": False}
    return {"open": True, "path": _Open.path, "diagrams": len(_Open.diagrams),
            "unsaved_changes": bool(_Open.editor and _Open.editor.changed)}


@mcp.tool()
def list_diagrams() -> List[Dict[str, Any]]:
    """List the model's diagrams (decomposition sheets), largest first.

    Each entry has an index and a node number - pass either to get_diagram / render_diagram -
    the name of the activity it decomposes, and how many boxes and arrow segments are on it.
    """
    _require()
    return [
        {
            "index": i,
            "node": d.node,
            "parent": d.parent_name or "(root)",
            "activities": len(d.activities),
            "arrows": len(d.arrows),
        }
        for i, d in enumerate(_Open.diagrams)
    ]


@mcp.tool()
def get_function_tree() -> List[Dict[str, Any]]:
    """The whole activity tree: every box in the model, nested by decomposition.

    Each node carries its id, name and IDEF0 node number (A0 at the top, A1..An below it, A11..
    below those), plus its children in sheet order.
    """
    _require()
    by_parent: Dict[int, List[Activity]] = {d.parent_id: d.activities for d in _Open.diagrams}

    def build(parent_id: int) -> List[Dict[str, Any]]:
        return [
            {"id": a.element_id, "number": a.number, "name": a.name,
             "children": build(a.element_id)}
            for a in by_parent.get(parent_id, [])
        ]

    roots = [pid for pid in by_parent if pid not in _Open.activities]
    tree: List[Dict[str, Any]] = []
    for pid in roots:
        tree.extend(build(pid))
    return tree


@mcp.tool()
def get_diagram(index: int = 0, node: str = "", include_routes: bool = False) -> Dict[str, Any]:
    """Read one diagram as data: its boxes, the flows between them, and the free text on it.

    Pick the sheet by node number (node="A1"; "A-0" is the context diagram) or by index from
    list_diagrams. `flows` is the semantic view - each arrow once, with where it starts and
    where it ends, and the ICOM role at each end (output / input / control / mechanism; an end
    on the frame is an arrow entering or leaving the decomposed activity, and a `tunnel` mark
    means it is deliberately not carried to the other level). Set include_routes to also get
    every arrow segment with its polyline and label position, in the same units as the boxes.
    Use render_diagram when you want to *see* the sheet.
    """
    d = _diagram(index, node)
    out: Dict[str, Any] = {
        "index": _Open.diagrams.index(d),
        "node": d.node,
        "parent": d.parent_name or "(root)",
        "frame": dict(zip(("left", "top", "right", "bottom"), d.frame)),
        "activities": [_activity_row(a) for a in d.activities],
        "flows": _flow_rows(d),
        "texts": [
            {"text": t.text, "x": round(t.x, 1), "y": round(t.y, 1)} for t in d.texts
        ],
    }
    if include_routes:
        out["arrows"] = [_arrow_row(a) for a in d.arrows]
    return out


@mcp.tool()
def render_diagram(index: int = 0, node: str = "") -> Image:
    """Draw one diagram and return it as a PNG image, laid out as the model stores it.

    Boxes with their node numbers, every arrow along its stored route with head and name, the
    page frame the outside arrows attach to. Pick the sheet by node number (node="A1") or by
    index from list_diagrams.
    """
    d = _diagram(index, node)
    return Image(data=render_diagram_png(d), format="png")


@mcp.tool()
def render_diagram_svg_text(index: int = 0, node: str = "") -> str:
    """The same diagram as an SVG document (text), for saving or embedding in a page."""
    d = _diagram(index, node)
    return render_diagram_svg(d)


# ------------------------------------------------------------------------------- the rules


@mcp.tool()
def check_model(sheet: Optional[str] = None) -> Dict[str, Any]:
    """Check the model against the rules of IDEF0 - the whole model, or one sheet (``sheet``:
    the number of the activity it decomposes, "A-0" for the context diagram).

    Errors break the method: an activity with no control or no output, an arrow leaving a box
    other than by its right side or entering by it, an end attached to nothing, a box or arrow
    with no name. Warnings are worth a look: an arrow on one level with no continuation on the
    other (ICOM balance - right only for a tunnel that was meant), an arrow drawn on both
    levels but not joined, an arrow come down from above but not taken to any box, fewer than
    3 or more than 6 boxes on a sheet, an activity not named by a verb or a flow named by one,
    an arrow from frame to frame touching no box.

    Each finding names its rule, sheet, box / flow / segment, and - where an editing tool
    mends it - the call to make. Every editing tool also reports, under ``idef0``, the findings
    its change brought in and how many it settled, so a sheet can be put right as it is drawn.
    """
    _require()
    if sheet and not any(d.node.lower() == sheet.strip().lower() for d in _Open.diagrams):
        raise ValueError(f"No sheet {sheet!r}. This model has: "
                         f"{', '.join(sorted(d.node for d in _Open.diagrams))}.")
    found = _Open.findings if not sheet else \
        [f for f in _Open.findings if f.sheet.lower() == sheet.strip().lower()]
    result: Dict[str, Any] = {"checked": sheet.strip() if sheet else "whole model"}
    result.update(rules.summary(found))
    result["findings"] = [f.as_dict() for f in found]
    if not found:
        result["note"] = "No rule broken."
    return result


# ------------------------------------------------------------------------------- the hands
#
# Every change is made in memory; the reading tools show it straight away, and nothing is
# written until save_model. Activities and sheets may be named by IDEF0 number ("A12") or id.


@mcp.tool()
def rename_activity(activity: str, name: str) -> Dict[str, Any]:
    """Give an activity box a new name. ``activity`` is its number ("A12") or id. IDEF0 names
    an activity with a verb phrase: "Проверить заявку", not "Проверка"."""
    return _edited(_Open.editor.rename_activity(_activity_id(activity), name))


@mcp.tool()
def rename_flow(flow: int, name: str) -> Dict[str, Any]:
    """Give a flow - what an arrow carries, shared by all its segments and levels - a new name.
    ``flow`` is the stream id get_diagram reports. IDEF0 names a flow with a noun phrase."""
    _require()
    return _edited(_Open.editor.rename_flow(int(flow), name))


@mcp.tool()
def add_activity(parent: str, name: str, x: Optional[float] = None, y: Optional[float] = None,
                 width: Optional[float] = None, height: Optional[float] = None) -> Dict[str, Any]:
    """Add an activity box to the decomposition of ``parent`` (its number, "A0", or id).

    The box goes last on that sheet and takes the next number (A3 after A1, A2); adding the
    first box under an activity gives it a decomposition. Without x/y it is placed the IDEF0
    way, down the diagonal from the box before it, clear of boxes, arrows and labels; give them
    (with width/height) to place it yourself, in the units get_diagram reports. Its look is
    copied from the boxes already there. IDEF0 asks for 3 to 6 boxes on a sheet.
    """
    _require()
    result = _Open.editor.add_activity(_activity_id(parent), name, x, y, width, height)
    sheet = next((d for d in _Open.editor.snapshot().diagrams() if d.parent_id == result["parent"]), None)
    if sheet is not None and len(sheet.activities) > 6:
        result["note"] = (f"This sheet now has {len(sheet.activities)} boxes; IDEF0 recommends "
                          f"at most 6 - consider decomposing one of them instead.")
    return _edited(result)


@mcp.tool()
def add_arrow(sheet: str, source: Dict[str, Any], target: Dict[str, Any],
              name: Optional[str] = None, flow: Optional[int] = None) -> Dict[str, Any]:
    """Draw an arrow on a sheet - ``sheet`` is the number of the activity it decomposes ("A0",
    "A12"; "A-0" for the context diagram) or its id.

    ``source``: {"activity": "A1"} - the box's output, leaving its right side -
    {"frame": "input" | "control" | "mechanism"} - coming in from outside the decomposed
    activity - or {"arrow": segment} - a branch forking off an arrow already on the sheet
    (a segment id from get_diagram). ``target``: {"activity": "A2", "role": "input" |
    "control" | "mechanism"}, {"frame": "output"} - leaving it - or {"arrow": segment} -
    joining an arrow already on the sheet. Give ``name`` for a new flow (a noun phrase) or
    ``flow`` (a stream id from get_diagram) to draw an existing one. A branch or a join
    carries the flow of the arrow it forks from or joins and shows no name of its own, unless
    it is given a name of its own (then it is a new flow - a part of what that arrow carries).

    An end on the frame, or on a box that has its own decomposition, is joined to the same
    flow on the other level when that arrow is there and not yet continued (matched by flow,
    or by name) - that keeps the levels balanced. Otherwise it is left as a tunnel. The route
    is orthogonal and kept clear of boxes and other arrows; the name goes beside it. Files
    from Ramus 3 only (Ramus 2 keeps routes in a form this cannot yet write).
    """
    _require()
    return _edited(_Open.editor.add_arrow(_sheet_id(sheet), _end_ref(source), _end_ref(target),
                                          name, None if flow is None else int(flow)))


@mcp.tool()
def move_activity(activity: str, x: Optional[float] = None, y: Optional[float] = None,
                  width: Optional[float] = None, height: Optional[float] = None) -> Dict[str, Any]:
    """Move an activity box on its sheet, resize it, or both - ``activity`` is its number
    ("A12") or id; whatever of x, y, width, height is left out stays as it is (units as
    get_diagram reports them).

    Its arrows go with it: each is routed again, its end keeping its place along the box's side
    or lining up with the other end for a straight line. An arrow the box now lands on is
    routed round it and a name it now covers is moved off. Refused if the box would overlap
    another or leave the sheet.
    """
    _require()
    return _edited(_Open.editor.move_activity(_activity_id(activity), x, y, width, height))


@mcp.tool()
def delete_activity(activity: str, with_decomposition: bool = False) -> Dict[str, Any]:
    """Delete an activity box - ``activity`` is its number ("A12") or id - with every arrow
    that ended on it; a piece of arrow left leading nowhere goes too. The boxes after it move
    up a number. A box with a decomposition of its own is deleted only with
    ``with_decomposition``, and then everything under it goes as well. Arrows on another
    level that continued a deleted one are listed: they are tunnels now (unbalanced).
    """
    _require()
    return _edited(_Open.editor.delete_activity(_activity_id(activity), with_decomposition))


@mcp.tool()
def delete_arrow(segment: int) -> Dict[str, Any]:
    """Delete one arrow segment - its id is in get_diagram's ``segments`` for each flow (or
    ``id`` with include_routes). As in Ramus, a piece left leading nowhere goes with it: the
    trunk of a fork whose last branch this was, the branches of a fork whose trunk this was.
    The flow stays in the model, so it can be drawn again by its stream id.
    """
    _require()
    return _edited(_Open.editor.delete_arrow(int(segment)))


@mcp.tool()
def join_levels(first: int, second: int) -> Dict[str, Any]:
    """Join an arrow on a box to the same arrow on the frame of that box's decomposition, when
    both are drawn but are not tied together - each shows as a tunnel and the levels do not
    balance (check_model reports it as not_joined, with the two segment ids). Give the two
    segment ids in either order; they must carry the same flow and meet the same side."""
    _require()
    return _edited(_Open.editor.join_levels(int(first), int(second)))


@mcp.tool()
def tidy_sheet(sheet: str) -> Dict[str, Any]:
    """Lay out a sheet's arrows again - ``sheet`` is the number of the activity it decomposes
    ("A0"; "A-0" for the context diagram) or its id. Each arrow is rerouted with the others
    where they are, its ends free to slide along their sides, and the new route is kept only
    if it is clearly better (fewer crossings, bends, lines on top of each other); pairs of
    arrows that cross are tried the other way round. Then names in the way of something are
    put back beside their arrows. Boxes stay where they are. Use it after a run of edits, or
    on a sheet drawn by hand that looks tangled; render_diagram shows the result.
    """
    _require()
    return _edited(_Open.editor.tidy_sheet(_sheet_id(sheet)))


@mcp.tool()
def tidy_labels(sheet: str) -> Dict[str, Any]:
    """Move only the arrow names on a sheet that are in the way - on a box, a line or another
    name, off the sheet, or lost far from their arrow - back beside their arrows. Routes and
    boxes stay as they are."""
    _require()
    return _edited(_Open.editor.tidy_labels(_sheet_id(sheet)))


@mcp.tool()
def save_model(path: Optional[str] = None, overwrite: bool = False) -> Dict[str, Any]:
    """Write the model with every change made so far.

    With ``path``: write a new file there (an existing file is replaced only with overwrite) -
    the safe way to try changes, leaving the original untouched; work continues on the new
    file. Without it: write over the file that was opened, after copying the original once to
    <name>.backup.rsf beside it. Close the model in Ramus first if it is open there.
    """
    _require()
    try:
        result = _Open.editor.save(path, overwrite=overwrite)
    except FileExistsError as exc:
        raise ValueError(str(exc)) from None
    if path is not None and os.path.abspath(path) != _Open.editor.path:
        # Carry on with the copy, as "save as" does everywhere.
        _Open.editor = ModelEditor(result["saved"])
        _Open.path = result["saved"]
    _refresh()
    result["unsaved_changes"] = False
    return result


if __name__ == "__main__":
    mcp.run()
