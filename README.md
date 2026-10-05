# ramus-mcp

An MCP server that gives an AI agent **eyes and hands for Ramus** — the IDEF0/DFD business-process
models stored in `.rsf` files. Point Claude (or any MCP client) at it and it can open a model, read
its activity tree and every diagram's arrows, and **see** a diagram rendered as an image — boxes,
arrows with their heads, names, colours and tunnels, laid out exactly as Ramus stores them. Editing
and writing models back is next.

Original work — it reads the `.rsf` **file format** directly (a ZIP of XML tables) and contains no
Ramus code, so it needs neither the Ramus application nor Java to run. It reads files saved by both
Ramus 2.x and Ramus 3.x.

## Install

```bash
py -3 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

## Use it from an MCP client

The repo ships a project `.mcp.json`; a client opened in this folder picks it up. Or point any
client at the command:

```
command: .venv\Scripts\python.exe
args:    src\server.py
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

From the command line: `.venv\Scripts\python src\render_svg.py model.rsf out.svg [A1]`.

## Tests

```bash
.venv\Scripts\python -m unittest discover -s tests
```

Most tests build a small model on the fly. The ones that read Ramus's own sample models skip
themselves when Ramus is not installed (`RAMUS_SAMPLES` points them at another copy of its `doc`
folder).

## Status

Eyes work: reading models and rendering diagrams with their arrows. The "hands" (creating and
editing activities and arrows, then saving a `.rsf` that reopens in Ramus) are on the roadmap in
`CLAUDE.md`, together with the notes on the file format.

## License

MIT — see [LICENSE](LICENSE).
