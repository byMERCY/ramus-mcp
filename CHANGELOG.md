# Changelog

**English** · [Русский](CHANGELOG.ru.md)

## 1.1.0 — 2026-10-08

28 tools (21 in 1.0.0).

### Added

- **Data flow diagrams.** Models in DFD and DFDS notation are read, drawn the way Ramus draws
  them (processes, external entities, data stores, roles), created
  (`create_model(notation="dfd" | "dfds")`), edited (`add_activity(kind="external" | "store" |
  "role")`) and checked: every process takes something in and gives something out, and data
  moves only through processes.
- **The IDEF0 diagram form.** `render_diagram(form=true)` draws a sheet with its header (author,
  project, dates, status, context) and footer (node, title, number); `export_diagram` writes one
  sheet or all of them to PNG or SVG files, ready for a report.
- **Look.** `style_activity` and `style_arrow` set colours (by name, `#rrggbb` or `r,g,b`),
  line width, dashes, fonts and block types; `add_activity` and `add_arrow` take a `style` too.
- **Notes.** `add_text` writes a note or a legend on a sheet; `set_purpose` states the model's
  purpose and viewpoint on its context diagram, as IDEF0 asks.
- **`check_layout`** says how well a sheet is drawn: crossings, detours, arrow ends in corners,
  feedback the wrong way round, names astray — a score and the faults behind it.
- **`layout_sheet`** lays a sheet out afresh: boxes down the diagonal sized to the page, every
  arrow drawn again — kept only if it reads better.

### Changed

- `tidy_sheet` keeps a new route only if the sheet reads better by `check_layout`, and redraws
  forks and joins as one line with branches.
- Arrow ends take the middle of a box's side and keep off its corners and its number.
- `add_activity` spreads the boxes down the diagonal to fit the page while no arrow touches them.
- Arrows are routed 5–8 times faster: one search over every place an arrow may start and end.
- Bends are rounded and free text is centred, as in Ramus.

### Fixed

- After `save_model` the model no longer counts as having unsaved changes, so `open_model` and
  `create_model` no longer ask for `discard_unsaved`.
- `save_model` with a relative path writes into the models folder, like `open_model` and
  `create_model`, not into the server's own folder.
- `set_purpose("Цель: …")` no longer writes the word twice.

## 1.0.0 — 2026-10-06

The first release, as a Claude Desktop extension (`.mcpb`).

- **Eyes:** reads Ramus 2.x and 3.x files — the activity tree, every arrow with its ICOM role,
  tunnels, labels, free text and colours — and renders any sheet as PNG or SVG.
- **Rules:** `check_model` checks IDEF0 — a control and an output on every activity, arrows by
  the right sides, ICOM balance between levels, 3–6 boxes on a sheet, verbs and nouns in names —
  each finding with the call that fixes it.
- **Hands:** creates a model from nothing, adds, moves, renames and deletes boxes, draws arrows
  with an orthogonal router (box to box, to and from the frame, forks and joins), joins arrows
  across levels, tidies a sheet, and saves — over the file with a backup, or to a copy.
