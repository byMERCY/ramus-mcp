"""ramus-mcp — an MCP server that gives an agent eyes and hands for Ramus .rsf models.

Eyes are here: open a model, list its diagrams, read the activity tree and each diagram's arrows
as data, and render a diagram to PNG so the agent can look at it. Hands (creating and editing,
then writing a .rsf that Ramus reopens) come next.

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

from ramus_rsf import Activity, Arrow, Diagram, End, RsfModel  # noqa: E402
from render_png import render_diagram_png  # noqa: E402
from render_svg import render_diagram as render_diagram_svg  # noqa: E402


mcp = FastMCP("ramus")


class _Open:
    """The one model the server is working on, reloaded whenever the file is opened."""

    path: Optional[str] = None
    model: Optional[RsfModel] = None
    diagrams: List[Diagram] = []
    activities: Dict[int, Activity] = {}


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
        rows.append(
            {
                "flow": flow,
                "name": " | ".join(names),
                "from": [_end_row(a.start) for a in arrows if a.start.kind != "junction"],
                "to": [_end_row(a.end) for a in arrows if a.end.kind != "junction"],
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
    return row


@mcp.tool()
def open_model(path: str) -> Dict[str, Any]:
    """Open a Ramus .rsf model file and make it the one every other tool reads.

    Returns a short summary: how many diagrams (decomposition sheets) it has, their IDEF0 node
    numbers ("A-0" is the context diagram, "A0" the top decomposition), and the largest few, so
    you know what there is to look at.
    """
    if not os.path.isfile(path):
        raise ValueError(f"{path} is not a file.")
    if _Open.model is not None:
        _Open.model.close()
    _Open.model = RsfModel(path)
    _Open.path = path
    _Open.diagrams = _Open.model.diagrams()
    _Open.activities = _Open.model.activities()
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
def current_model() -> Dict[str, Any]:
    """Say which model is open, if any."""
    if _Open.model is None:
        return {"open": False}
    return {"open": True, "path": _Open.path, "diagrams": len(_Open.diagrams)}


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


if __name__ == "__main__":
    mcp.run()
