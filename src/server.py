"""ramus-mcp — an MCP server that gives an agent eyes and hands for Ramus .rsf models.

Eyes: open a model, list its diagrams, read the activity tree and each diagram's arrows as data,
and render a diagram to PNG so the agent can look at it. Rules: check the model against IDEF0.
Hands: rename activities and flows, add, move and delete boxes, draw, fork, join and delete
arrows - laid out and routed the IDEF0 way - and save a .rsf that Ramus reopens.

Every reading tool shows the model as it stands, unsaved changes included, so a change can be
looked at before it is written. Nothing is written until save_model.

Run it over stdio:  python src/server.py   (or: uv run src/server.py)
Point an MCP client at that command. It keeps one model open at a time; open_model switches.
RAMUS_MODELS_DIR, if set, is the folder list_models looks in and relative paths start from.
"""

from __future__ import annotations

import datetime as _dt
import os
import sys
from typing import Any, Dict, List, Optional

# Make the sibling modules importable whether this is run as a script or a module.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp.server.fastmcp import FastMCP, Image  # noqa: E402
from mcp.types import ToolAnnotations  # noqa: E402

import idef0_rules as rules  # noqa: E402
import layout_quality as layout  # noqa: E402
from model_editor import EditError, ModelEditor  # noqa: E402
from ramus_rsf import Activity, Arrow, Diagram, End, RsfModel  # noqa: E402
from render_png import render_diagram_png  # noqa: E402
from scene import Form  # noqa: E402
from render_svg import render_diagram as render_diagram_svg  # noqa: E402

INSTRUCTIONS = """\
Ramus business-process models (.rsf files, IDEF0): read them, see them, check them against the \
rules of IDEF0, change them, or build one from nothing. One model is open at a time.

Start: list_models finds the .rsf files (in the models folder, or a folder you name); \
open_model opens one; create_model starts a new one.

Look before and after a change: render_diagram shows a sheet as a picture; get_diagram gives \
its boxes and arrows as data, with the ids the editing tools take; get_function_tree the whole \
hierarchy. A sheet is named by the activity it decomposes: "A-0" is the context diagram, "A0" \
its decomposition, then "A1", "A12" and so on.

Build the IDEF0 way: the context diagram holds one box, A0, with its inputs (left), controls \
(top), mechanisms (bottom) and outputs (right); decompose it into 3 to 6 boxes (add_activity), \
connect them (add_arrow: an output into another box's input, control or mechanism), and go a \
level down where there is more to say. Name activities by verbs ("Проверить заявку") and arrows \
by nouns ("заявка"), in the user's language. An arrow on a decomposed box continues on the \
frame of its sheet: draw it there from (or to) the frame and the two are joined.

Every edit answers with "idef0": the rule findings it brought in and how many it settled; \
check_model lists them all, each with the call that mends it. check_layout says how well a \
sheet is drawn (crossings, detours, arrow ends in corners, feedback the wrong way round, names \
astray); tidy_sheet straightens a tangled one and layout_sheet lays a sheet out afresh, \
boxes and all - each keeps only what reads better. Add a sheet's boxes before its arrows: \
while no arrow touches them, add_activity spreads them down the diagonal to fit the page. \
Nothing is written until save_model (create_model writes its new file at once); saving over \
the opened file first copies it to <name>.backup.rsf. Ask the user to close the model in \
Ramus before writing over it.
"""

mcp = FastMCP("ramus", instructions=INSTRUCTIONS)

# How each tool touches things, for clients that ask before acting: looking changes nothing;
# editing changes the open model in memory only; removing takes something out of it; writing
# puts a file on disk.
_LOOK = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
_EDIT = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)
_REMOVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=False)
_WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=False)


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
        if a.number and a.number.lower() == text.lower():
            return a.element_id
    # A data flow diagram's external entities and stores have no number: by name, then.
    named = [a for a in _Open.activities.values() if a.name.strip().lower() == text.lower()]
    if len(named) == 1:
        return named[0].element_id
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


def _models_dir() -> Optional[str]:
    """The folder the user keeps models in, if one was set up (RAMUS_MODELS_DIR)."""
    folder = os.environ.get("RAMUS_MODELS_DIR", "").strip()
    if not folder or "${" in folder:  # unset, or a placeholder the client left unexpanded
        return None
    return os.path.abspath(os.path.expanduser(os.path.expandvars(folder)))


def _model_path(path: str, must_exist: bool) -> str:
    """A model path as the user may give it: relative to the models folder, ~ for home, and
    with or without the .rsf."""
    p = os.path.expanduser(os.path.expandvars(str(path).strip().strip('"')))
    if not os.path.isabs(p) and _models_dir():
        p = os.path.join(_models_dir(), p)
    p = os.path.abspath(p)
    if not p.lower().endswith(".rsf") and (not must_exist or not os.path.exists(p)):
        p += ".rsf"
    return p


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
    row = {
        "id": a.element_id,
        "number": a.number,
        "name": a.name,
        "x": round(a.x, 1),
        "y": round(a.y, 1),
        "width": round(a.width, 1),
        "height": round(a.height, 1),
    }
    if a.kind != "process":
        row["kind"] = a.kind
        if a.owner_id is not None:
            row["owner"] = a.owner_id
    return row


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


@mcp.tool(title="Open a model", annotations=_LOOK)
def open_model(path: str, discard_unsaved: bool = False) -> Dict[str, Any]:
    """Open a Ramus .rsf model file and make it the one every other tool reads and edits.

    Returns a short summary: how many diagrams (decomposition sheets) it has, their IDEF0 node
    numbers ("A-0" is the context diagram, "A0" the top decomposition), and the largest few, so
    you know what there is to look at. If the model open now has unsaved changes this refuses,
    so nothing is lost or written behind your back: save_model first, or pass discard_unsaved.
    """
    path = _model_path(path, must_exist=True)
    if not os.path.isfile(path):
        raise ValueError(f"{path} is not a file. list_models shows the models there are.")
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


@mcp.tool(title="Create a new model", annotations=_WRITE)
def create_model(path: str, activity: str, author: str = "", project: Optional[str] = None,
                 model_name: str = "Работы", overwrite: bool = False,
                 discard_unsaved: bool = False, notation: str = "idef0") -> Dict[str, Any]:
    """Create a new Ramus model file and open it - the way to start a model from nothing.

    ``path`` is the .rsf file to write; ``activity`` names the whole process as one activity, a
    verb phrase ("Провести олимпиаду"): it is the single box A0 on the context diagram A-0.
    ``author`` and ``project`` (by default the file's name) are what the diagram frame shows;
    ``model_name`` is the model's name in Ramus's list of models. An existing file is
    replaced only with overwrite (and copied to <name>.backup.rsf first). ``notation`` is how
    the model is drawn: "idef0" (the default), "dfd" - a data flow diagram: processes with
    rounded corners, external entities and data stores (add_activity kind), flows meeting
    boxes on any side - or "dfds", a data flow diagram whose activities show their roles.

    Then: draw A0's inputs, controls, mechanisms and outputs on the context diagram (add_arrow
    with sheet "A-0"), and decompose it - add_activity under "A0", 3 to 6 boxes - and so on
    down. The file is written at once; later changes wait for save_model.
    """
    if _Open.editor is not None and _Open.editor.changed and not discard_unsaved:
        raise ValueError(f"{_Open.path} has unsaved changes. Call save_model to keep them, "
                         f"or create_model again with discard_unsaved=true to drop them.")
    try:
        editor = ModelEditor.create(_model_path(path, must_exist=False), activity, model_name,
                                    author, project, overwrite, notation)
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


_SKIP_DIRS = {"node_modules", "__pycache__", "AppData", "site-packages"}


@mcp.tool(title="Find model files", annotations=_LOOK)
def list_models(folder: Optional[str] = None, recursive: bool = True) -> Dict[str, Any]:
    """Find Ramus model files (.rsf): in ``folder``, or else in the models folder set up for
    this connector (else the user's Documents), and the folders below it when ``recursive``.
    Newest first, with size and date; backups (<name>.backup.rsf) are left out. Give a path from
    here to open_model."""
    if folder:
        base = os.path.abspath(os.path.expanduser(os.path.expandvars(folder.strip().strip('"'))))
    else:
        base = _models_dir() or os.path.join(os.path.expanduser("~"), "Documents")
    if not os.path.isdir(base):
        raise ValueError(f"{base} is not a folder.")
    found = []
    for root, dirs, files in os.walk(base):
        depth = os.path.relpath(root, base).count(os.sep) + (root != base)
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in _SKIP_DIRS] \
            if recursive and depth < 3 else []
        for f in files:
            if f.lower().endswith(".rsf") and ".backup" not in f.lower():
                p = os.path.join(root, f)
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                found.append((st.st_mtime, p, st.st_size))
    found.sort(reverse=True)
    shown = found[:200]
    result: Dict[str, Any] = {
        "folder": base,
        "models": [{"path": p, "name": os.path.splitext(os.path.basename(p))[0],
                    "size_kb": round(size / 1024, 1),
                    "modified": _dt.datetime.fromtimestamp(m).strftime("%Y-%m-%d %H:%M")}
                   for m, p, size in shown],
    }
    if len(found) > len(shown):
        result["more"] = len(found) - len(shown)
    if not found:
        result["note"] = "No .rsf files here. create_model starts a new one."
    return result


@mcp.tool(title="Which model is open", annotations=_LOOK)
def current_model() -> Dict[str, Any]:
    """Say which model is open, if any."""
    if _Open.model is None:
        return {"open": False}
    return {"open": True, "path": _Open.path, "diagrams": len(_Open.diagrams),
            "unsaved_changes": bool(_Open.editor and _Open.editor.changed)}


@mcp.tool(title="List the diagrams", annotations=_LOOK)
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


@mcp.tool(title="Activity tree", annotations=_LOOK)
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


@mcp.tool(title="Read a diagram", annotations=_LOOK)
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
        "notation": d.notation,
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


@mcp.tool(title="Look at a diagram", annotations=_LOOK)
def render_diagram(index: int = 0, node: str = "", form: bool = False) -> Image:
    """Draw one diagram and return it as a PNG image, laid out as the model stores it.

    Boxes with their node numbers, every arrow along its stored route with head and name, the
    page frame the outside arrows attach to. Pick the sheet by node number (node="A1") or by
    index from list_diagrams. With ``form`` the sheet is drawn in the IDEF0 diagram form, as
    printed: a header (author, project, dates, status, the context - the sheet above with this
    one's box filled in) and a footer (node, title, number).
    """
    d = _diagram(index, node)
    return Image(data=render_diagram_png(d, form=_form(d) if form else None), format="png")


@mcp.tool(title="Diagram as SVG", annotations=_LOOK)
def render_diagram_svg_text(index: int = 0, node: str = "", form: bool = False) -> str:
    """The same picture as render_diagram, as an SVG document (text) - for a person to save or
    embed; ``form`` draws it in the IDEF0 diagram form."""
    d = _diagram(index, node)
    return render_diagram_svg(d, form=_form(d) if form else None)


@mcp.tool(title="Export diagrams to files", annotations=_WRITE)
def export_diagram(path: str, node: str = "", form: bool = True,
                   scale: float = 3.0) -> Dict[str, Any]:
    """Write one diagram - or every diagram of the model - to picture files, for a report or
    a slide. ``path`` ends in .png or .svg: the file to write for one sheet (``node``, e.g.
    "A0"), or, with node "all" (or left out), a folder - or a name pattern like
    "out/{node}.png" - getting one file per sheet, named by its node. ``form`` (on by
    default) frames each sheet in the IDEF0 diagram form; ``scale`` is pixels per unit of
    the page (800 units across) for PNG - 3 gives a picture 2400 wide. A relative path starts
    from the models folder. Existing files are replaced."""
    _require()
    base = _models_dir() or os.getcwd()
    target = path if os.path.isabs(path) else os.path.join(base, path)
    every = not node or node.strip().lower() == "all"
    sheets = _Open.diagrams if every else [_diagram(0, node)]
    ext = os.path.splitext(target)[1].lower()
    if every and "{node}" not in target:
        if ext in (".png", ".svg"):
            raise ValueError('To write every sheet, give a folder or a pattern with {node} '
                             'in it, e.g. "export/{node}.png".')
        target = os.path.join(target, "{node}.png")
        ext = ".png"
    if ext not in (".png", ".svg"):
        raise ValueError("The file is a .png or an .svg.")
    written = []
    for d in sorted(sheets, key=lambda x: (x.node != "A-0", len(x.node), x.node)):
        out = target.replace("{node}", d.node)
        os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
        frame = _form(d) if form else None
        if ext == ".svg":
            with open(out, "w", encoding="utf-8") as fh:
                fh.write(render_diagram_svg(d, form=frame))
        else:
            with open(out, "wb") as fh:
                fh.write(render_diagram_png(d, form=frame, scale=max(0.5, min(float(scale), 8.0))))
        written.append({"node": d.node, "file": os.path.abspath(out)})
    return {"written": written}


def _form(d: Diagram) -> Form:
    return Form(**_Open.model.form_of(d))


@mcp.tool(title="Check against IDEF0", annotations=_LOOK)
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


@mcp.tool(title="Check how a sheet is drawn", annotations=_LOOK)
def check_layout(sheet: str) -> Dict[str, Any]:
    """How well a sheet is drawn - ``sheet`` is the number of the activity it decomposes
    ("A0"; "A-0" for the context diagram) or its id. Where check_model says whether the model
    is right, this says whether the sheet reads well: arrows crossing or running on top of one
    another, routes going a long way round, arrow ends pushed into a box's corner or onto its
    number, ends crowded on one side, feedback drawn against the convention (into a control
    over the top, into an input or a mechanism under the bottom), names far from their arrows
    or lying on boxes, lines and other names, routes through boxes, boxes on top of each other.

    Returns a score - penalty points, lower is better; it compares two layouts of one sheet,
    not one sheet with another - the faults counted by kind, and the worst of them, each with
    the segments and boxes it concerns and the tool that mends it (tidy_sheet, tidy_labels,
    move_activity).
    """
    _require()
    return _Open.editor.layout_report(_sheet_id(sheet))


# ------------------------------------------------------------------------------- the hands
#
# Every change is made in memory; the reading tools show it straight away, and nothing is
# written until save_model. Activities and sheets may be named by IDEF0 number ("A12") or id.


@mcp.tool(title="Rename an activity", annotations=_EDIT)
def rename_activity(activity: str, name: str) -> Dict[str, Any]:
    """Give an activity box a new name. ``activity`` is its number ("A12") or id. IDEF0 names
    an activity with a verb phrase: "Проверить заявку", not "Проверка"."""
    return _edited(_Open.editor.rename_activity(_activity_id(activity), name))


@mcp.tool(title="Rename a flow", annotations=_EDIT)
def rename_flow(flow: int, name: str) -> Dict[str, Any]:
    """Give a flow - what an arrow carries, shared by all its segments and levels - a new name.
    ``flow`` is the stream id get_diagram reports. IDEF0 names a flow with a noun phrase."""
    _require()
    return _edited(_Open.editor.rename_flow(int(flow), name))


@mcp.tool(title="Add an activity", annotations=_EDIT)
def add_activity(parent: str, name: str, x: Optional[float] = None, y: Optional[float] = None,
                 width: Optional[float] = None, height: Optional[float] = None,
                 kind: str = "process", owner: Optional[str] = None,
                 style: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Add an activity box to the decomposition of ``parent`` (its number, "A0", or id).

    The box goes last on that sheet and takes the next number (A3 after A1, A2); adding the
    first box under an activity gives it a decomposition. Without x/y it is placed the IDEF0
    way, down the diagonal from the box before it, clear of boxes, arrows and labels; give them
    (with width/height) to place it yourself, in the units get_diagram reports. Its look is
    copied from the boxes already there. IDEF0 asks for 3 to 6 boxes on a sheet.

    In a DFD or DFDS model (create_model notation) ``kind`` may also be "external" - an
    external entity, where data comes from or goes to - or "store", a data store; they take
    no number and are placed apart from the processes (name them by name in add_arrow). In a
    DFDS model "role" adds a performer as a tag inside the activity ``owner`` (its number).

    ``style`` gives it a look at once - the keys of style_activity: {"fill": "green",
    "color": "black", "font_size": 12, "bold": true, "block_type": "operation"}.
    """
    _require()
    result = _Open.editor.add_activity(_activity_id(parent), name, x, y, width, height,
                                       kind, None if owner is None else _activity_id(owner),
                                       style)
    sheet = next((d for d in _Open.editor.snapshot().diagrams() if d.parent_id == result["parent"]), None)
    if sheet is not None and len(sheet.activities) > 6:
        result["note"] = (f"This sheet now has {len(sheet.activities)} boxes; IDEF0 recommends "
                          f"at most 6 - consider decomposing one of them instead.")
    return _edited(result)


@mcp.tool(title="Draw an arrow", annotations=_EDIT)
def add_arrow(sheet: str, source: Dict[str, Any], target: Dict[str, Any],
              name: Optional[str] = None, flow: Optional[int] = None,
              style: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
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

    On a data flow diagram (DFD, DFDS) there is no ICOM: an end is {"activity": "A1" or
    "Клиент", "side": "left" | "top" | "right" | "bottom"} - a flow leaves by the right and
    arrives by the left unless told otherwise - or {"frame": "left" | "top" | "right" |
    "bottom"}; boxes are external entities and data stores as well as processes.

    ``style`` gives the arrow a look at once - the keys of style_arrow: {"color": "red",
    "width": 2, "line": "dashed", "font_size": 9, "bold": false}. A branch or a join takes
    the look of the arrow it comes off unless given one.
    """
    _require()
    return _edited(_Open.editor.add_arrow(_sheet_id(sheet), _end_ref(source), _end_ref(target),
                                          name, None if flow is None else int(flow), style))


@mcp.tool(title="Write a text on a sheet", annotations=_EDIT)
def add_text(sheet: str, text: str, x: Optional[float] = None, y: Optional[float] = None,
             width: Optional[float] = None, font_size: float = 10.0,
             color: Optional[str] = None) -> Dict[str, Any]:
    """Write a free text on a sheet - ``sheet`` as for add_arrow ("A0"; "A-0" the context
    diagram): a note, a legend, a remark. Without x/y it goes in the lower left corner, clear
    of what is there; ``width`` is how wide it may run before it wraps ("\\n" breaks a line).
    ``color`` as for style_activity. Ramus 3 files only."""
    _require()
    return _edited(_Open.editor.add_text(_sheet_id(sheet), text, x, y, width, font_size, color))


@mcp.tool(title="State the purpose and viewpoint", annotations=_EDIT)
def set_purpose(purpose: str, viewpoint: Optional[str] = None) -> Dict[str, Any]:
    """Write the model's purpose and viewpoint on its context diagram A-0, as IDEF0 asks of
    every model: why it is made (purpose) and from whose position it is seen (viewpoint) -
    "Цель: ...", "Точка зрения: ..." in a Russian model. Called again, it replaces them."""
    _require()
    return _edited(_Open.editor.set_purpose(purpose, viewpoint))


@mcp.tool(title="Colour and style a box", annotations=_EDIT)
def style_activity(activity: str, fill: Optional[str] = None, color: Optional[str] = None,
                   font_size: Optional[float] = None, bold: Optional[bool] = None,
                   block_type: Optional[str] = None) -> Dict[str, Any]:
    """Give a box a look - ``activity`` is its number ("A2"), its name (an external entity,
    a store) or id. ``fill`` is its background and ``color`` its outline and name: a colour
    name (red, green, blue, yellow, orange, teal, gray ... or Russian: зелёный, красный ...),
    #rrggbb or r,g,b; "white" and "black" bring back the usual look. ``font_size`` and
    ``bold`` set its name's type. ``block_type`` is what Ramus calls an activity -
    complex_process, process, process_part, operation, action: a label, not a shape. Only
    what is given changes."""
    _require()
    return _edited(_Open.editor.style_activity(_activity_id(activity), fill, color, font_size,
                                               bold, block_type))


@mcp.tool(title="Colour and style an arrow", annotations=_EDIT)
def style_arrow(segment: int, color: Optional[str] = None, width: Optional[float] = None,
                line: Optional[str] = None, font_size: Optional[float] = None,
                bold: Optional[bool] = None, whole_arrow: bool = True) -> Dict[str, Any]:
    """Give an arrow a look - ``segment`` is any of its segment ids (get_diagram with
    include_routes, or what add_arrow answered). ``color`` as for boxes; ``width`` 0.5 to 6
    (1 is the usual, 2-3 a main flow); ``line`` solid, dashed, dotted or dash_dot;
    ``font_size`` and ``bold`` for its name (a name in a new size is set beside its line
    again). With whole_arrow (the default) the whole arrow on its sheet - trunk and branches -
    takes the look. Only what is given changes."""
    _require()
    return _edited(_Open.editor.style_arrow(int(segment), color, width, line, font_size, bold,
                                            whole_arrow))


@mcp.tool(title="Move or resize an activity", annotations=_EDIT)
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


@mcp.tool(title="Delete an activity", annotations=_REMOVE)
def delete_activity(activity: str, with_decomposition: bool = False) -> Dict[str, Any]:
    """Delete an activity box - ``activity`` is its number ("A12") or id - with every arrow
    that ended on it; a piece of arrow left leading nowhere goes too. The boxes after it move
    up a number. A box with a decomposition of its own is deleted only with
    ``with_decomposition``, and then everything under it goes as well. Arrows on another
    level that continued a deleted one are listed: they are tunnels now (unbalanced).
    """
    _require()
    return _edited(_Open.editor.delete_activity(_activity_id(activity), with_decomposition))


@mcp.tool(title="Delete an arrow segment", annotations=_REMOVE)
def delete_arrow(segment: int) -> Dict[str, Any]:
    """Delete one arrow segment - its id is in get_diagram's ``segments`` for each flow (or
    ``id`` with include_routes). As in Ramus, a piece left leading nowhere goes with it: the
    trunk of a fork whose last branch this was, the branches of a fork whose trunk this was.
    The flow stays in the model, so it can be drawn again by its stream id.
    """
    _require()
    return _edited(_Open.editor.delete_arrow(int(segment)))


@mcp.tool(title="Join an arrow across levels", annotations=_EDIT)
def join_levels(first: int, second: int) -> Dict[str, Any]:
    """Join an arrow on a box to the same arrow on the frame of that box's decomposition, when
    both are drawn but are not tied together - each shows as a tunnel and the levels do not
    balance (check_model reports it as not_joined, with the two segment ids). Give the two
    segment ids in either order; they must carry the same flow and meet the same side."""
    _require()
    return _edited(_Open.editor.join_levels(int(first), int(second)))


@mcp.tool(title="Tidy a sheet", annotations=_EDIT)
def tidy_sheet(sheet: str) -> Dict[str, Any]:
    """Lay out a sheet's arrows again - ``sheet`` is the number of the activity it decomposes
    ("A0"; "A-0" for the context diagram) or its id. Each arrow is rerouted with the others
    where they are, its ends free to slide along their sides, and the new route is kept only
    if the sheet reads better for it (the measure check_layout reports); the ends on each side
    of a box are spread out evenly, in the order that keeps them from crossing; pairs of arrows
    that cross are tried the other way round; feedback goes over the top into a control and
    under the bottom into an input or mechanism. Then names in the way of something are put
    back beside their arrows. If the sheet does not come out better as a whole it is left as
    it was. Boxes stay where they are. Answers with the score before and after (``layout``).
    Use it after a run of edits, or on a sheet drawn by hand that looks tangled;
    render_diagram shows the result.
    """
    _require()
    return _edited(_Open.editor.tidy_sheet(_sheet_id(sheet)))


@mcp.tool(title="Lay a sheet out again", annotations=_EDIT)
def layout_sheet(sheet: str) -> Dict[str, Any]:
    """Lay a whole sheet out again - ``sheet`` is the number of the activity it decomposes
    ("A0") or its id. The boxes go down the IDEF0 diagonal, in their numbered order, sized and
    spaced to fill the page for as many as there are; then every arrow is drawn again among
    them - from box to box first, then those from and to the frame, then forks and joins (one
    line along the bottom with a branch up into each box, say), feedback last - and tidied as
    tidy_sheet does. Arrows keep their segment ids, names and ties to the other levels. If the
    sheet does not come out reading better (the measure check_layout reports), it is left as
    it was. Answers with the boxes moved and the score before and after (``layout``).

    Use it on a sheet whose boxes are badly placed - squeezed into a corner, off the diagonal,
    too many for the place they were put in - or after adding boxes to a sheet already drawn;
    tidy_sheet is enough when only the arrows are tangled. render_diagram shows the result.
    """
    _require()
    return _edited(_Open.editor.layout_sheet(_sheet_id(sheet)))


@mcp.tool(title="Tidy arrow names", annotations=_EDIT)
def tidy_labels(sheet: str) -> Dict[str, Any]:
    """Move only the arrow names on a sheet that are in the way - on a box, a line or another
    name, off the sheet, or lost far from their arrow - back beside their arrows. Routes and
    boxes stay as they are."""
    _require()
    return _edited(_Open.editor.tidy_labels(_sheet_id(sheet)))


@mcp.tool(title="Save the model", annotations=_WRITE)
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


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
