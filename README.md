# PyWR Reader

**Open a PyWR water resource model and actually see it.** The network draws
itself, clicking a node lights up everywhere its water comes from and goes to,
and one button runs the model and plays the flows back through every pipe.

![Python](https://img.shields.io/badge/Python-3.9+-blue)
![Platform](https://img.shields.io/badge/Platform-macOS%20%7C%20Windows%20%7C%20Linux-green)
![Licence](https://img.shields.io/badge/Licence-MIT-blue)

---

## What it does

A PyWR model is a large JSON file. Reading one tells you very little about the
network it describes — which sources feed which demand centre, what a licence
constrains, where the water actually goes. This app answers those questions.

| | |
|---|---|
| **See the network** | Opens the model and lays it out automatically — sources at the top, demands at the bottom. Four layouts to choose from; drag any node; positions save back into the file. |
| **Follow the water** | Click a node: blue for everything upstream that can feed it, orange for everything downstream it can reach. |
| **Read it properly** | A filterable browser over nodes, edges, parameters, tables and recorders, each summarised in one line and expandable to full JSON. |
| **Edit it** | Add and delete nodes and edges, rename things safely, add recorders and whole parameter chains from templates — or edit the raw JSON in a panel that follows your selection. |
| **Run it** | Executes the model with pywr, then a time slider plays the flows through the network. Per-node charts, what-if comparisons, CSV export. |
| **Look at the data** | Opens the `.h5`, `.xlsx` and `.csv` files the model reads — as a table or a zoomable time-series plot. |
| **Trace from a map** | Drop a map or schematic behind the canvas and build a model by clicking along the flow lines. |

It reads **PyWR model JSON**, **`.tcm`** viewer files (for node positions), and
Graph Overlay **`nodes.csv`** pairs.

Verified on a real 80-year, 29,586-timestep zone model of 162 nodes.

---

## Install

You need **Python 3.9 or newer**. Everything else is one dependency (Flask) —
so the install is three commands on every platform, and the app is a local web
page at `http://127.0.0.1:5321`.

> Running *simulations* additionally needs pywr, but **you never install that
> yourself** — see [Running simulations](#running-simulations) below.

### macOS

Python 3 is already present on macOS 12+. Open **Terminal**:

```bash
cd PYWR_reader
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
./.venv/bin/python app.py
```

Then open <http://127.0.0.1:5321>. If macOS says `python3` isn't found, it will
offer to install the developer tools — accept, then run the commands again.

### Windows

Install Python from [python.org](https://www.python.org/downloads/windows/) if
you don't have it, **ticking "Add Python to PATH"** on the first screen of the
installer. Then open **PowerShell** or **Command Prompt**:

```bat
cd PYWR_reader
py -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python app.py
```

Then open <http://127.0.0.1:5321>.

Notes for Windows:

- Use `py` rather than `python3` — that's the launcher the installer sets up.
- The paths use **backslashes** and there is no `./` prefix.
- If PowerShell blocks the activation script, you don't need to activate
  anything — calling `.venv\Scripts\python` directly (as above) always works.
- The Open dialog lists your real drive letters, so a model on `D:` or a
  network drive is reachable.

### Linux

Most distributions need the `venv` package installed separately:

```bash
sudo apt install python3-venv        # Debian/Ubuntu; use dnf/pacman elsewhere
cd PYWR_reader
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
./.venv/bin/python app.py
```

Then open <http://127.0.0.1:5321>.

### First run

**Open** → `examples/gw_network/pywr_model.json` — a small runnable
groundwater/river demo with daily data, included in the repository.

Reading, layout, path highlighting, editing, save, and CSV/`.tcm` import all
work with nothing installed but Flask. Everything else in the codebase is the
Python standard library.

---

## Running simulations

Running a model needs pywr, which is awkward to install by hand — so the app
does it for you. Click the red **pywr not set up** chip → **Set up PyWR now**.
It builds a private environment in `.pywr-env/` and reports progress live.

What it does per platform, in order:

| Platform | How it gets pywr |
|---|---|
| **macOS (Apple Silicon)** | micromamba fetches a self-contained conda-forge build and runs it under Rosetta 2 (pywr ships Intel binaries only), or builds from source against conda-forge's GLPK. |
| **Windows, Linux, Intel Mac** | `pip install pywr`, else uv-managed Python 3.11, else micromamba. |

micromamba is a single static binary fetched into the project folder.
**Nothing is installed outside this folder** and no admin rights are needed.
The only requirement is internet access the first time.

Then:

1. **▶ Run** executes the model; recorders are attached to every node
   automatically.
2. A **time slider** appears — edges are coloured and thickened by flow. Press
   **space** or ▶ to animate. Click a node and the flow value is drawn on each
   pipe along its path.
3. The selected node's panel charts its flow or volume over time.
4. **What-if:** press **Δ** beside any numeric parameter to stage a change, then
   **▶ Run what-if** — the model file is *not* modified. Tick several runs to
   overlay and compare them.
5. **Scenarios:** if the model defines pywr scenarios, a picker chooses which
   ensemble member is drawn.

**Getting results out.** Runs live in memory, so save what you need:

| Button | What you get |
|---|---|
| **csv** on a run | The whole run — a row per timestep, a column per node *and* per edge. |
| **csv** on a node chart | Just that node, a column per run plotted — so a what-if comparison downloads as you see it. |
| **save** on a run | Writes `<model>.<run>.pywrrun.json` beside the model. **Open run…** loads it back later, on any machine. |

CSVs are written utf-8-sig, so Excel reads accented node names correctly.

**Per-edge flows.** pywr records node totals, so at a split feeding a junction
the per-edge flow isn't directly available. The app splices a *transparent proxy
link* onto those edges to recover the **exact** flow without changing the run.
The rare edge it still can't pin stays **dashed = estimated**, and exports label
it `[estimated]` so an estimate is never mistaken for a recorded flow.

---

## Working with a model

### Layout

If a model has no positions — or junk ones (every node stacked on one
coordinate, as pywr-editor leaves them) — a layout is computed automatically.
**Layout ▾** offers four, applied instantly, with **Undo**:

| Layout | Best for |
|---|---|
| **Layered (flow)** | The default. Sources top, demands bottom — follows the water. |
| **Force-directed** | Meshy networks, and zone models where layered funnels 40 sources into one unreadable row. |
| **Grouped by function** | A structural overview: source / river / storage / link / demand / virtual, left to right. |
| **Radial** | Wide, shallow networks — rings by distance from the sources. |

These are the algorithms networkx would give you, implemented against the
standard library so layout works on a bare checkout. All are deterministic.

**Save** writes positions into each node's `position.schematic`, so the file
stays a valid pywr model.

### External data files

Real models point at data files by absolute paths from another machine
(`C:\Data\...\inflows.xlsx`). On open, the app finds each one by basename — in
the model's folder, its parent and grandparent, and any folder you add — and
shows a ✓/✗ report in **Model → Data files**. If something is missing it says
so and blocks a run with a clear message rather than failing deep inside pywr.
**The model file is never rewritten**; paths are remapped only for the run.

Every located file gets a **view** button:

- **Table** — the keys or sheets with row counts, and the first 200 rows of
  whichever you pick. Only the head is read, so a 35,000-row file opens instantly.
- **Plot** — a column over time. Tick columns to overlay, hover for values,
  scroll or drag to zoom and pan. The whole column is thinned to an overview so
  80 years draws at once, but **zoom in and it re-reads that window at full
  daily resolution**. Tick **lock Y** to stop a zoom rescaling the axis.

This needs the pywr environment: h5 and xlsx are read with pandas, which lives
there rather than in the app.

### Editing

- **New** — start an empty model to build from scratch or trace over an image.
- **+ Add ▾** — **Node** (pick the type, then click the canvas) or **Edge**
  (click source, then destination).
- Rename, change type, edit parameters, delete nodes and edges — with warnings
  when something still references what you're removing.
- **Export CSV pair** writes Graph-Overlay-compatible `nodes.csv` +
  `nodes_edges.csv`.

### Tracing a network from a map

1. **New** (or open a model) → **Trace image** → pick a map, scan or screenshot.
   It sits behind the network and pans and zooms with it.
2. Drag to place, drag the corner to resize, set **Opacity**, or **Fit to view**.
   **Lock** when the scale looks right.
3. **+ Node** on top of the map's features (tick **Quick place** to skip the
   name dialog), **+ Edge** along the flow lines.
4. **Save** writes a normal pywr JSON — the image is never stored inside it. By
   default it's kept in the browser; **💾 Save beside model** writes it as
   `<model>.pywrtrace.png` plus a tiny sidecar holding its position, so the
   trace travels with the project.

---

## Editing as JSON

### The live JSON dock

**{ } JSON** opens a panel under the canvas that *stays* open and follows your
selection. Click a node and its JSON is there; change something on the canvas
and you watch the JSON change with it — two views of one model.

| Scope | What you see |
|---|---|
| **node** | The selected node on its own. |
| **node + related** | The node, **the parameters it uses** (following `Aggregated` → base × factor chains to the end), **the recorders watching it**, any parameter reading those recorders, and the tables they all read. |
| **whole model** | Every section at once. |

**node + related** answers "what is actually attached to this demand centre?".
Select `DC_Boyneswood` in a real zone model and you get its node JSON, the
base/factor parameters behind its `max_flow`, the seven recorders on it, the
`EDO_threshold_param` reading one of them, and the zone totals it feeds —
assembled by following references, not by matching names. References are
followed **down** without limit and **up** by association only, so an aggregate
recorder over every demand centre is listed without dragging in the other forty.

**Apply** (or **⌘/Ctrl+Enter**) pushes your edit through the same validation as
every other editor and redraws the canvas. Deleting an entry from a
**node + related** slice deletes it from the model.

Apply is deliberately a button, not live-as-you-type: half-typed JSON is invalid
by definition and a canvas redrawing through those states is unusable. Your
typing *is* checked live — **in sync**, **edited** or **invalid JSON**.

**Nothing overwrites your typing.** If the model changes while you have
unapplied edits, the dock keeps your text and offers **Reload** or **Keep
editing**. Keep editing is safe: Apply merges onto the model as it is *then*.

**Renaming a key is offered, never guessed** — one key gone and one arrived
looks identical whether you renamed something or swapped it, so Apply asks
whether references should follow.

### The modal editors

The explorer is editable at three levels: **{ } edit JSON** (the whole model),
**{ } edit all parameters** (one block — add, rename or remove freely), and
**{ } edit** on any row (a single parameter, table, recorder, or a node).

Bad edits never reach the canvas. A syntax slip reports line and column;
structural mistakes are named — a duplicate node name, an edge pointing at a
node that isn't there, a `parameters` block that isn't an object. The message
shows under the editor and your text stays put.

> Renaming in the **whole-model** editor is not tracked — a name that changes
> there reads as one node removed and another added. Rename from a node's own
> `{ } edit`, or from the dock, and every reference is rewritten for you.

---

## Adding and renaming safely

### Adding recorders and parameters

Recorders are what a run writes down, and real models put the same handful on
every node — so they're buttons. The node panel lists what watches the selected
node and offers one-click adds filtered by node type: a demand node can run a
*deficit*, a storage node has a *volume*, everything gets *flow*. **Record the
usual things** adds the standard set in one edit, offering only what's missing.

**Common set-ups** builds a whole parameter chain the way the rest of the model
spells it:

| Template | For | What it creates |
|---|---|---|
| **seasonal demand cap** | demand nodes | `_max_flow_base` (from a table) × `_max_flow_factor` (monthly profile) → `_max_flow`, wired to the node |
| **annual licence volume** | storage / virtual storage | `_max_volume` read from a table, wired to the node |
| **base + top-up abstraction** | sources | average and peak output from a table, the top-up above average shaped by a monthly profile |
| **deficit alarm** | demand nodes | the deficit recorder, a `RecorderThresholdParameter` over it, and an `EventRecorder` counting sustained spells |

It asks only for what differs between one node and the next — in practice the
table row, so the rest comes pre-filled — and **shows the JSON before it lands**,
updating as you type. An entry that already exists is left alone rather than
overwritten, so running a template on a half-set-up node finishes the job; if
the entry the node would point at is one of those, it says so, because the new
parameters would otherwise be built and joined to nothing.

**Browse model** also has **+ add** on Parameters, Recorders and Tables, with
forms for the common types. Fields holding the name of something else suggest
what your model already defines. **Write it as JSON myself** is always in the
dropdown — the forms are a shortcut, not a ceiling.

### Keeping references honest

A pywr model is held together by names: a node's `max_flow` is the *name* of a
parameter, an `Aggregated` parameter lists the *names* of others. Rename or
delete one carelessly and the model breaks somewhere you weren't looking.

- **Rename carries every reference.** The **rename** button on any row says
  what's at stake first — *"3 places refer to it"* — then rewrites them all:
  node attributes, operand lists, the parameter reading a recorder, the
  parameters reading a table. The entry keeps its position in the file, so a
  rename stays a one-line diff.
- **Delete tells you what you're breaking**, then names the exact paths. It's a
  warning, not a veto.
- **Dangling references are visible.** Every change re-checks for names referred
  to but defined nowhere, shown in an amber strip in the dock. Warnings, never
  errors — a half-finished edit legitimately has them.

Two deliberate limits: pywr keeps parameters and recorders in **separate
namespaces**, so when a name means both, those references are left alone and the
rename says so rather than guessing. And the dangling check only follows keys
that certainly hold a name, because a false "undefined" on every load of a valid
model would be worse than missing one.

---

## Tests

```bash
./run_tests.sh          # or: ./.venv/bin/python -m unittest discover -s tests -v
```

**195 tests**, using only Python's stdlib `unittest`. On a bare checkout they
pass in under a second — the two groups needing extras skip themselves rather
than fail:

| Group | Needs | Covers |
|---|---|---|
| unit + API | just Flask | loaders, layout, graph ops, every route |
| frontend contract | just Flask | that the JS modules still agree with `index.html` and the API — every `$("id")` exists, every `/api/…` is served |
| pywr integration | the pywr environment | really executing a model, what-if overrides, per-edge flow recording, reading h5/csv |
| browser smoke | `requirements-dev.txt` + chromium | the real UI in a browser: the network draws, path tracing, layouts, Undo, JSON editing, the dock both ways, adding/renaming/deleting entries, the templates |
| performance | just Flask | a 1,200-node model lays out, opens and saves within a time budget |

The frontend has no build step and no test framework, so the contract tests
catch what silently breaks otherwise: rename an element id in one file and not
the other, or call a route that no longer exists, and they fail with the name of
the culprit. To enable the browser tests:

```bash
./.venv/bin/pip install -r requirements-dev.txt
./.venv/bin/playwright install chromium      # ~90 MB, one time
```

`run_tests.sh` also runs **ruff** when it's installed; a bare checkout skips it.

---

## Project layout

```
PYWR_reader/
├── app.py                    thin entry point — builds Flask, registers blueprints
├── pywr_reader/
│   ├── session.py            Workspace + RunStore — the open model and its runs
│   ├── api/                  route blueprints, registered by app.py
│   │   ├── files.py              open / save / browse / graph / edit-as-JSON
│   │   ├── edit.py               layouts, node/edge CRUD, definition add/rename/delete
│   │   ├── datafiles.py          locate, preview and plot external data files
│   │   ├── traceimg.py           the trace-over-image sidecar
│   │   ├── env.py                pywr environment status + one-click setup
│   │   └── runs.py               run a model; CSV export; save / open a run
│   ├── model_io.py           pywr JSON / .tcm / CSV readers, writers
│   ├── layout.py             layered / force / grouped / radial layouts (no deps)
│   ├── graphops.py           trace, add/delete/rename, reference rewriting + checks
│   ├── dataresolve.py        locate external data files by basename
│   ├── dataview.py           read h5/xlsx/csv (runs inside .pywr-env)
│   ├── envsetup.py           one-click pywr environment bootstrap
│   └── runner.py             executed inside .pywr-env — runs pywr, dumps series
├── static/                   frontend — native ES modules (no build step)
│   ├── app.js                    the network canvas, editing, runs + wiring
│   ├── state / palette / dom / api.js   shared state, colours, DOM + API helpers
│   ├── dataviewer.js             the h5/xlsx/csv table + plot modal
│   ├── explorer.js               Browse model, edit / rename / delete entries
│   ├── jsondock.js               the live JSON dock that follows the selection
│   ├── catalog.js                recorder / parameter / chain templates
│   └── bundles.js                the "common set-ups" dialog with its live preview
├── tests/                    195 unittest tests
├── examples/gw_network/      small self-contained runnable demo
├── requirements.txt          flask (that's the lot)
├── requirements-dev.txt      ruff + playwright, for dev/tests
└── .pywr-env/                private pywr environment (created on demand)
```

---

## Roadmap

- [x] Read pywr JSON / .tcm / CSV; auto-layout; positions merge
- [x] Node/edge editing, rename with reference rewriting, save/export
- [x] External data-file resolution (finds `.xlsx`/`.h5`/`.csv` by basename)
- [x] One-click pywr environment; run with recorders on every node
- [x] Flow explorer: path highlighting, flow-scaled edges, time slider,
      animation, per-node charts, what-if runs with comparison
- [x] Verified on a real 80-year, 29,586-timestep zone model (162 nodes)
- [x] Image tracing mode — trace a network over a map or schematic
- [x] Model explorer — filterable, readable browse of nodes/params/tables
- [x] Scenario picker — choose and overlay ensemble members
- [x] Per-edge exact flows at splits/junctions via spliced proxy links
- [x] Layout picker — layered / force-directed / grouped / radial, with Undo
- [x] Editable JSON — whole model, a section, or one entry, validated before
      it lands; renaming a node rewrites every reference to it
- [x] Live JSON dock — a panel that stays open and follows the selection,
      showing a node with everything that hangs off it; edits flow both ways
- [x] Reference safety for parameters, recorders and tables — rename rewrites
      every reference, delete says what it leaves dangling, and names referred
      to but defined nowhere show as warnings
- [x] Guided add for recorders and parameters, and parameter-chain templates
      previewed as JSON before they land
- [ ] GeoJSON/Shapefile import for geographic networks
- [ ] Open a submodel together with its inputs file, for model suites that
      split the network and its parameters across separate files

---

## Licence

MIT — see [LICENSE](LICENSE). Use it, change it, share it; just keep the
copyright notice.

Author: Shalini B
