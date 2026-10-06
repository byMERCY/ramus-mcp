# ramus-mcp

An MCP server that gives Claude — or any MCP client — **eyes and hands for Ramus**, the IDEF0
business-process modelling tool. It opens a model (`.rsf`), reads its activity tree and every
diagram's arrows, and **sees** a diagram rendered as a picture — boxes, arrows with their heads,
names, colours, dashes and tunnels, laid out as Ramus stores them. It **checks** the model
against the rules of IDEF0, **changes** it — boxes and arrows placed and routed the IDEF0 way —
or **builds one from nothing**, and saves a `.rsf` that Ramus opens.

*По-русски:* коннектор, через который Клод читает, рисует, проверяет по правилам IDEF0, правит и
создаёт с нуля модели Ramus (`.rsf`).

Original work: it reads and writes the `.rsf` **file format** directly (a ZIP of XML tables) and
contains no Ramus code, so it needs neither Ramus nor Java to run. It reads files of Ramus 2.x and
3.x, and writes them the way Ramus does — a table it rewrites is byte for byte what Ramus itself
would write, and every kind of change was checked by opening the result in the Ramus engine.

## Install in Claude Desktop

1. Get `ramus-mcp-<version>.mcpb` (from the releases, or build it — see below).
2. Open it with Claude Desktop (double-click, or *Settings → Extensions → Install extension*).
3. Pick the folder you keep your models in. That is all: Claude Desktop sets up Python and the
   two dependencies itself.

Then ask, for example:

- «Открой модель «поход» и покажи диаграмму A0.»
- «Проверь модель по правилам IDEF0 и исправь, что можно.»
- «Создай модель «Провести олимпиаду»: контекстная диаграмма и декомпозиция на 4 работы.»
- "Tidy up the arrows on A2 and save the model as a copy."

Close a model in Ramus before Claude saves over it. Saving over the opened file first copies it
to `<name>.backup.rsf`.

## Use it from another MCP client

The server runs over stdio. With [uv](https://docs.astral.sh/uv/):

```bash
uv run --directory /path/to/ramus-mcp src/server.py
```

or with a virtual environment:

```bash
py -3 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python src\server.py
```

For Claude Code: `claude mcp add ramus -- uv run --directory /path/to/ramus-mcp src/server.py`.
A client configured by JSON takes the same command:

```json
{
  "mcpServers": {
    "ramus": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/ramus-mcp", "src/server.py"],
      "env": { "RAMUS_MODELS_DIR": "/path/to/your/models" }
    }
  }
}
```

`RAMUS_MODELS_DIR` (optional) is where `list_models` looks and where relative paths start.

## Tools

A sheet is named by the activity it decomposes — `"A-0"` is the context diagram, `"A0"` its
decomposition, then `"A1"`, `"A12"` … — and an activity by its number or id.

**Eyes**

| Tool | What it does |
|---|---|
| `list_models(folder?)` | The `.rsf` files in the models folder (or another), newest first |
| `open_model(path)` / `current_model()` | Open a model; say which one is open |
| `list_diagrams()` | The sheets, with node numbers and sizes |
| `get_function_tree()` | The whole activity tree with IDEF0 numbers (A0, A1, A11 …) |
| `get_diagram(node, include_routes?)` | One sheet as data: boxes, the flows between them (each end with its ICOM role, tunnels marked), free text, ids for the hands; `include_routes` adds every segment's polyline |
| `render_diagram(node)` | One sheet drawn as a PNG — the picture Claude looks at |
| `render_diagram_svg_text(node)` | The same sheet as SVG |

**Rules.** `check_model(sheet?)` checks the model, or one sheet, against IDEF0 and lists what it
finds, each with the call that mends it:

- *errors* — the method is broken: an activity with no control or no output, an arrow leaving a
  box other than by its right side or entering by it, an end attached to nothing, a box or an
  arrow with no name;
- *warnings* — worth a look: ICOM balance between levels (an arrow with no continuation on the
  other level — right only for a tunnel that was meant; one drawn on both levels but not joined;
  one come down from above but not drawn on to a box), fewer than 3 or more than 6 boxes on a
  sheet, an activity not named by a verb or a flow named by one, an arrow from frame to frame.

Every editing tool also answers with `idef0`: the findings its change brought in and how many it
settled, so a sheet is put right while it is being drawn.

**Hands.** Every change shows at once in the reading tools; nothing is written until `save_model`.

| Tool | What it does |
|---|---|
| `create_model(path, activity, author?, project?)` | A new model from nothing: a `.rsf` whose context diagram holds the top activity A0, written as the Ramus 3 desktop application writes a new file |
| `rename_activity(activity, name)` | Rename a box |
| `rename_flow(flow, name)` | Rename a flow — what an arrow carries, on every level |
| `add_activity(parent, name, x?, y?, width?, height?)` | Add a box to a decomposition: it takes the next number, copies its neighbours' look and, without a position, goes down the IDEF0 diagonal clear of boxes, arrows and names |
| `add_arrow(sheet, source, target, name? / flow?)` | Draw an arrow: box output → box input, control or mechanism; frame → box; box → frame; a branch off an arrow already there (`source={"arrow": segment}`) or a join into one (`target={"arrow": segment}`), cut in the way Ramus cuts them. The route is orthogonal, clear of boxes, and keeps off the other arrows — down the middle of the gap between them; where each end sits on its side is chosen by trying several. An end on the frame or on a decomposed box is joined to the same flow on the other level when that arrow is there — keeping the levels balanced — and is otherwise left a tunnel |
| `move_activity(activity, x?, y?, width?, height?)` | Move or resize a box; its arrows are routed again, an arrow it lands on goes round it, a name it covers moves off |
| `delete_activity(activity, with_decomposition?)` | Delete a box with the arrows that ended on it (and, if asked, everything under it); the boxes after it move up a number |
| `delete_arrow(segment)` | Delete an arrow segment; as in Ramus, a piece left leading nowhere goes with it |
| `join_levels(first, second)` | Tie an arrow on a box to the same arrow on the frame of that box's decomposition when both are drawn but not joined |
| `tidy_sheet(sheet)` / `tidy_labels(sheet)` | Lay a sheet's arrows out again — a new route is kept only if clearly better, crossing pairs are tried the other way round — then move the names in the way; or only the names |
| `save_model(path?, overwrite?)` | Write the changes: to a new file (the original stays untouched), or over the opened one after copying it once to `<name>.backup.rsf` |

## Limits

- Arrows are drawn, rerouted and deleted in files saved by Ramus 3.x. A Ramus 2.x file keeps its
  routes in a binary form this does not write yet: open and save it once in Ramus 3.
- DFD shapes are drawn as IDEF0 boxes, and a sheet is one page.
- A tunnel drawn in square brackets cannot be told from a forgotten arrow; `check_model` reports
  it as a warning that is right only if the tunnel was meant.

## Development

```bash
.venv\Scripts\python -m unittest discover -s tests
```

Most tests build a small model on the fly; the ones that read Ramus's sample models skip
themselves when Ramus is not installed (`RAMUS_SAMPLES` points them at another copy of its `doc`
folder). `tools/ramus_validate.py` opens a file in a Ramus engine to check what was written, and
`tools/build_mcpb.py` builds the Claude Desktop extension. From the command line:
`.venv\Scripts\python src\render_svg.py model.rsf out.svg [A1]`.

## License

MIT — see [LICENSE](LICENSE).
