# ramus-mcp

An MCP server that gives an AI agent **eyes and hands for Ramus** — the IDEF0/DFD business-process
models stored in `.rsf` files. Point Claude (or any MCP client) at it and it can open a model, read
its activity tree and every diagram's arrows, and **see** a diagram rendered as an image — boxes,
arrows with their heads, names, colours, dashes and tunnels, laid out exactly as Ramus stores them.
And it can **change** the model: rename activities and flows, add boxes and arrows — placed and
routed the IDEF0 way — and save a `.rsf` that Ramus opens.

Original work — it reads the `.rsf` **file format** directly (a ZIP of XML tables) and contains no
Ramus code, so it needs neither the Ramus application nor Java to run. It reads files saved by both
Ramus 2.x and Ramus 3.x.

## Install

```bash
py -3 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

## Use it from an MCP client

Point any MCP client at the command:

```
command: .venv\Scripts\python.exe
args:    src\server.py
```

A client that reads a project-level `.mcp.json` needs a file like this in the project folder
(use the absolute paths of your own checkout; the file is git-ignored, so it stays local):

```json
{
  "mcpServers": {
    "ramus": {
      "command": "<project folder>\\.venv\\Scripts\\python.exe",
      "args": ["<project folder>\\src\\server.py"]
    }
  }
}
```

## Tools

A diagram is picked by its IDEF0 node number (`node="A1"`; `"A-0"` is the context diagram) or by
`index` from `list_diagrams`.

| Tool | What it does |
|---|---|
| `open_model(path)` | Open a `.rsf` file and make it the active model |
| `current_model()` | Which model is open |
| `list_diagrams()` | The decomposition sheets with node numbers and sizes, largest first |
| `get_function_tree()` | The whole activity tree, nested, with IDEF0 numbers (A0, A1, A11 …) |
| `get_diagram(node/index, include_routes)` | One sheet as data: boxes, the flows between them (where each starts and ends, with ICOM roles and tunnels), free text; `include_routes` adds every arrow segment's polyline |
| `render_diagram(node/index)` | One sheet drawn as a PNG image (the "eyes") |
| `render_diagram_svg_text(node/index)` | The same sheet as an SVG document |

**Hands.** Activities and sheets are named by IDEF0 number (`"A12"`; a sheet by the activity it
decomposes, `"A-0"` for the context diagram) or by id. Every change shows up at once in the reading
tools, so it can be looked at before anything is written.

| Tool | What it does |
|---|---|
| `rename_activity(activity, name)` | Rename a box |
| `rename_flow(flow, name)` | Rename a flow — what an arrow carries, on every level |
| `add_activity(parent, name, x?, y?, width?, height?)` | Add a box to a decomposition; it takes the next number, copies its neighbours' look, and without a position is placed down the IDEF0 diagonal clear of boxes, arrows and labels |
| `add_arrow(sheet, source, target, name? / flow?)` | Draw an arrow: box output → box input, control or mechanism; frame → box; box → frame. The route is orthogonal and kept clear of boxes and other arrows. An end on the frame, or on a decomposed box, is joined to the same flow on the other level when that arrow is there — keeping the levels balanced — and is otherwise left a tunnel |
| `save_model(path?, overwrite?)` | Write the changes: to a new file (the original stays untouched), or over the opened one after copying it once to `<name>.backup.rsf` |

Nothing is written until `save_model`, and `open_model` will not switch away from unsaved changes
unless told to drop them. Arrows can be added to files saved by Ramus 3.x; a Ramus 2.x file keeps
its routes in a binary form this does not write yet — open and save it once in Ramus 3.

From the command line: `.venv\Scripts\python src\render_svg.py model.rsf out.svg [A1]`.

## Tests

```bash
.venv\Scripts\python -m unittest discover -s tests
```

Most tests build a small model on the fly. The ones that read Ramus's own sample models skip
themselves when Ramus is not installed (`RAMUS_SAMPLES` points them at another copy of its `doc`
folder).

## Status

Eyes work: reading models and rendering diagrams with their arrows. Hands work for renaming and
for adding boxes and arrows; every change is written the way Ramus writes it — a rewritten table is
byte-for-byte what Ramus itself would produce — and was checked by opening the result in the Ramus
engine. Moving and removing boxes and arrows, and checks of the IDEF0 rules, are next.

## License

MIT — see [LICENSE](LICENSE).
