# ramus-mcp

An MCP server that gives an AI agent **eyes and hands for Ramus** — the IDEF0/DFD business-process
models stored in `.rsf` files. Point Claude (or any MCP client) at it and it can open a model, read
its activity tree, and **see** a diagram rendered as an image. Editing and writing models back is
next.

Original work — it reads the `.rsf` **file format** directly (a ZIP of XML tables) and contains no
Ramus code, so it needs neither the Ramus application nor Java to run.

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

| Tool | What it does |
|---|---|
| `open_model(path)` | Open a `.rsf` file and make it the active model |
| `current_model()` | Which model is open |
| `list_diagrams()` | The decomposition sheets, largest first |
| `get_function_tree()` | The whole activity tree, nested, with IDEF0 numbers |
| `get_diagram(index)` | One sheet's activity boxes as data |
| `render_diagram(index)` | One sheet drawn as a PNG image (the "eyes") |
| `render_diagram_svg_text(index)` | The same sheet as an SVG document |

## Status

Eyes work: reading and rendering a model's activities. Arrows, and the "hands" (creating and editing
activities, then saving a `.rsf` that reopens in Ramus) are on the roadmap in `CLAUDE.md`.

## License

MIT — see [LICENSE](LICENSE).
