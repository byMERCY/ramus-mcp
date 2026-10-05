"""ramus-mcp — an MCP server that gives an agent eyes and hands for Ramus .rsf models.

Eyes are here now: open a model, list its diagrams, read the activity tree, and render a
diagram to PNG so the agent can look at it. Hands (creating and editing, then writing a .rsf
that Ramus reopens) come next.

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

from ramus_rsf import RsfModel, Diagram, Activity  # noqa: E402
from render_png import render_diagram_png  # noqa: E402
from render_svg import render_diagram as render_diagram_svg  # noqa: E402


mcp = FastMCP("ramus")


class _Open:
    """The one model the server is working on, reloaded whenever the file is opened."""

    path: Optional[str] = None
    model: Optional[RsfModel] = None
    diagrams: List[Diagram] = []


def _require() -> None:
    if _Open.model is None:
        raise ValueError(
            "No model is open. Call open_model with the path to a .rsf file first."
        )


def _diagram(index: int) -> Diagram:
    _require()
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


@mcp.tool()
def open_model(path: str) -> Dict[str, Any]:
    """Open a Ramus .rsf model file and make it the one every other tool reads.

    Returns a short summary: how many diagrams (decomposition sheets) it has and the largest
    few, so you know what there is to look at.
    """
    if not os.path.isfile(path):
        raise ValueError(f"{path} is not a file.")
    if _Open.model is not None:
        _Open.model.close()
    _Open.model = RsfModel(path)
    _Open.path = path
    _Open.diagrams = _Open.model.diagrams()
    return {
        "opened": path,
        "diagrams": len(_Open.diagrams),
        "largest": [
            {"index": i, "parent": d.parent_name or "(root)", "activities": len(d.activities)}
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

    Each entry has an index to pass to get_diagram / render_diagram, the name of the activity
    it decomposes, and how many activity boxes are on it.
    """
    _require()
    return [
        {"index": i, "parent": d.parent_name or "(root)", "activities": len(d.activities)}
        for i, d in enumerate(_Open.diagrams)
    ]


@mcp.tool()
def get_function_tree() -> List[Dict[str, Any]]:
    """The whole activity tree: every box in the model, nested by decomposition.

    Each node carries its id, name and the IDEF0 number it has on its sheet, plus its children.
    """
    _require()
    acts = _Open.model.activities()
    children: Dict[int, List[Activity]] = {}
    for a in acts.values():
        children.setdefault(a.parent_id, []).append(a)

    numbers = {a.element_id: a.number for d in _Open.diagrams for a in d.activities}

    def build(parent_id: int) -> List[Dict[str, Any]]:
        nodes = sorted(children.get(parent_id, []), key=lambda a: (a.y, a.x))
        out = []
        for a in nodes:
            out.append(
                {
                    "id": a.element_id,
                    "number": numbers.get(a.element_id, ""),
                    "name": a.name,
                    "children": build(a.element_id),
                }
            )
        return out

    roots = [pid for pid in children if pid not in acts]
    tree: List[Dict[str, Any]] = []
    for pid in roots:
        tree.extend(build(pid))
    return tree


@mcp.tool()
def get_diagram(index: int = 0) -> Dict[str, Any]:
    """Read one diagram as data: the activity boxes on it, with names, numbers and geometry.

    Use render_diagram when you want to *see* it; use this when you need the numbers. Arrows
    are not reported yet (milestone M2).
    """
    d = _diagram(index)
    return {
        "index": index,
        "parent": d.parent_name or "(root)",
        "activities": [_activity_row(a) for a in d.activities],
    }


@mcp.tool()
def render_diagram(index: int = 0) -> Image:
    """Draw one diagram and return it as a PNG image, laid out as the model stores it.

    Boxes only for now (M2 adds arrows). Pick the sheet with list_diagrams.
    """
    d = _diagram(index)
    return Image(data=render_diagram_png(d), format="png")


@mcp.tool()
def render_diagram_svg_text(index: int = 0) -> str:
    """The same diagram as an SVG document (text), for saving or embedding in a page."""
    d = _diagram(index)
    return render_diagram_svg(d)


if __name__ == "__main__":
    mcp.run()
