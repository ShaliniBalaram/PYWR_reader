# PyWR Reader

A local web app to **read, understand, edit and run PyWR water resource
models** — the companion to the Graph Overlay tools (which *build* models,
while this one *explains* them).

Open any PyWR model, see the whole network laid out automatically, click a
node to light up everywhere its water comes from and goes to, run the model
with pywr, then scrub through time watching flows move through every edge.

![Python](https://img.shields.io/badge/Python-3.9+-blue)
![Platform](https://img.shields.io/badge/Platform-macOS%20%7C%20Windows%20%7C%20Linux-green)
![Licence](https://img.shields.io/badge/Licence-MIT-blue)

---

## Quick start

**macOS / Linux**

```bash
cd PYWR_reader
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt   # just Flask
./.venv/bin/python app.py
# → open http://127.0.0.1:5321 in a browser
```

**Windows** (PowerShell or Command Prompt)

```bat
cd PYWR_reader
py -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python app.py
REM → open http://127.0.0.1:5321 in a browser
```

Try it immediately: **Open** → `examples/gw_network/pywr_model.json` — a small
runnable groundwater/river demo with daily data (a runnable copy of the Graph
Overlay PyWR example).

### No prerequisites beyond this release

**The whole app runs on one dependency: Flask** (`requirements.txt`). Reading,
auto-layout, path highlighting, editing, save and CSV/`.tcm` import all work
with nothing else installed — everything else in the codebase is Python
standard library.

Running *simulations* needs pywr, but **you don't install it yourself**: click
the red *pywr not set up* chip → **Set up PyWR now**. The app builds a private
environment in `.pywr-env/` and reports progress live. It has no macOS-wheel
problem to work around for you — the bootstrap tries, in order:

- **macOS (Apple Silicon):** micromamba downloads a self-contained conda-forge
  build and runs it under Rosetta 2 (pywr ships only Intel binaries), or builds
  from source against conda-forge's GLPK.
- **Linux / Windows / Intel Mac:** `pip install pywr`, else uv-managed Python
  3.11, else micromamba.

micromamba is a single static binary fetched into the project; **nothing is
installed outside this folder** and no admin rights are needed. The one thing
the bootstrap needs is internet access the first time.

## Tests

```bash
./run_tests.sh          # or: ./.venv/bin/python -m unittest discover -s tests -v
```

**191 tests**, using only Python's stdlib `unittest`. On a bare checkout all of
them pass in under a second — the two groups that need an extra skip
themselves rather than fail:

| Group | Needs | Covers |
|---|---|---|
| unit + API | just Flask | loaders, layout, graph ops, every route |
| frontend contract | just Flask | that the JS modules still agree with `index.html` and the API blueprints — every `$("id")` looked up exists, every `/api/…` called is served |
| pywr integration | the pywr environment | really executing a model, what-if overrides, per-edge flow recording, reading h5/csv data |
| browser smoke | `requirements-dev.txt` + chromium | the real UI in a headless browser: the network draws, path tracing, each layout, Undo, the Add menu, JSON editing, the live JSON dock both ways, adding/renaming/deleting recorders and parameters, the parameter-chain templates |
| performance | just Flask | a 1,200-node model lays out, opens, and saves within a time budget — the guardrail that keeps real water models responsive |

The frontend has no build step and no test framework, so the contract tests
exist to catch what silently breaks otherwise: rename an element id in one
file and not the other, or call a route that no longer exists, and they fail
with the name of the culprit. To enable the browser tests too:

```bash
./.venv/bin/pip install -r requirements-dev.txt
./.venv/bin/playwright install chromium      # ~90 MB, one time
./run_tests.sh
```

`run_tests.sh` also runs **`ruff`** (config in `ruff.toml`) when it's installed
— a bare checkout skips it.

---

## What it opens

| Input | What happens |
|---|---|
| **PyWR model JSON** | Full model loads — nodes, edges, parameters, tables, recorders, scenarios all preserved. |
| **`.tcm` view file** (PyWR TCM viewer) | Gzipped view files hold node positions. Opened alone, the app finds the source model next to it; opened **while a model is loaded**, its positions are applied to that model by node name. |
| **`nodes.csv`** (Graph Overlay format) | Imports the node table plus the sibling `nodes_edges.csv`. |

**Positions:** if a model has none — or they're junk (many nodes stacked on
one default coordinate, as pywr-editor leaves them) — a layout is computed
automatically: sources at the top, demands at the bottom, licence/virtual
nodes parked beside the nodes they monitor.

No single layout suits every network, so **Layout ▾** offers four and applies
the one you pick instantly (**Undo** puts the positions back):

| Layout | Best for |
|---|---|
| **Layered (flow)** | The default. Sources top, demands bottom — follows the water. |
| **Force-directed** | Meshy networks, and zone models where layered funnels 40 sources into one unreadable row. A spring embedding untangles them. |
| **Grouped by function** | A structural overview: blocks of source / river / storage / link / demand / virtual, left to right. |
| **Radial** | Wide, shallow networks — rings by distance from the sources. |

These are the same algorithms networkx would give you, implemented against the
standard library so layout still works on a bare checkout (networkx's
`spring_layout` needs numpy, `kamada_kawai` needs scipy). All are
deterministic — a model always lays out the same way.

Every node can also be dragged. **Save** writes positions into each node's
`position.schematic`, so the file stays a valid pywr model.

**Looking inside the data:** every located file gets a **view** button in
**Model → Data files** — open the `.h5`, `.xlsx` or `.csv` a model reads and
see what's actually in it. Two ways to look:

- **Table** — the keys (or sheets) with their row counts, and the first 200
  rows of whichever you pick, with real dates and column names. Only the head
  is read, so a 35,000-row timeseries opens instantly.
- **Plot** — a column over time, as a line chart. Tick the columns to overlay
  (a timeseries file often has dozens), hover to read the values on a date.
  **Zoom** in on a period — scroll on the chart, or the −/+/Reset buttons — and
  **drag to pan**; the axis relabels to the window. The whole column is thinned
  to an overview so 80 years draws at once, but **zoom in and it re-reads that
  window at full daily resolution** — down to individual days, each a point. On
  a big file only the zoomed rows are read from disk, so it stays quick. The
  value axis auto-fits what's on screen, or tick **lock Y** to hold the full
  scale so a zoom doesn't rescale it.

This works for `.csv` and `.xlsx` columns too, not just h5. Both flavours of h5
work — the pandas kind, and the plain HDF5 that real pywr timeseries files
often turn out to be.

This one needs the pywr environment: h5 and xlsx are read with pandas, which
lives there rather than in the app (which is still Flask and nothing else).

**External data files:** real models point `tables`/`parameters` at data files
by absolute paths from another machine (`C:\Data\...\inflows.xlsx`). On open,
the app finds each referenced file by basename — in the model's folder, its
parent/grandparent, and any folders you add — and shows a ✓/✗ report in
**Model → Data files**. If something's missing it says so and a run is blocked
with a clear message rather than failing deep inside pywr; add the folder that
holds the data and it re-checks. The model file is never rewritten — paths are
remapped only for the run.

## Understanding the network

- **Click a node** → its properties open, and the water path lights up:
  **blue = upstream** (everything that can feed it), **orange = downstream**
  (everything it can feed). Switch between Both / Upstream / Downstream / Off.
- Node colour = functional group (source, river, storage, demand, link,
  virtual); squares are storages, diamonds are virtual/aggregated nodes.
- The **Model** tab shows metadata, counts, the timestepper and data files.
  **Browse model** opens a readable explorer — filterable tables of nodes,
  edges, parameters, tables and recorders, each row summarised (a parameter
  shows its type and key fields, e.g. `constant · = 150` or
  `MonthlyProfile · table:GW PDO profile`) and expandable to full JSON. Click a
  node row to jump to it on the canvas.

## The live JSON dock

**{ } JSON** in the toolbar opens a JSON panel under the canvas that *stays*
open while you work, and follows whatever you have selected. Click a node and
its JSON is there; change something on the canvas and you watch the JSON change
with it. It is the same model either way round — two views of one thing.

Three scopes, switched at the top of the dock:

| Scope | What you see |
|---|---|
| **node** | The selected node on its own. |
| **node + related** | The node, **the parameters it uses** (following `Aggregated` → base × factor chains to the end), **the recorders watching it**, any parameter reading those recorders, and the tables they all read. |
| **whole model** | Every section at once. |

**node + related** is the one that answers "what is actually attached to this
demand centre?". Select `DC_Boyneswood` in a real zone model and the dock shows
its node JSON plus `DC_Boyneswood_max_flow` and the base/factor parameters
behind it, the seven recorders on the node, the `EDO_threshold_param` reading
one of them, and the zone totals it feeds — assembled by following references,
not by matching names.

References are followed **down** without limit and **up** by association only:
a recorder that aggregates every demand centre is listed (this node feeds it),
but the forty other demand centres it names are not pulled in with it.

**Editing works both ways.** Change the JSON and press **Apply** (or
**⌘/Ctrl+Enter**) — it goes through the same validation as every other JSON
editor, and the canvas redraws. In **node + related**, *deleting an entry from
the slice deletes it from the model*, so removing a recorder is one line of
JSON. Renaming the node rewrites every reference to it, exactly as the node
editor does.

Apply is deliberately a button press, not live-as-you-type: half-typed JSON is
invalid by definition and a canvas redrawing through those states is unusable.
Your typing *is* checked live — the status reads **in sync**, **edited** or
**invalid JSON**, and a syntax error names the line as you make it.

**Nothing overwrites your typing.** If the model changes while you have
unapplied edits — you moved to another node, or edited on the canvas — the dock
keeps your text and says it has fallen behind, offering **Reload (discard my
edits)** or **Keep editing**. Keep editing is safe: Apply merges onto the model
as it is *then*, not as it was when the text was drawn, so a change made
meanwhile is not silently undone.

**Renaming a key is offered, never guessed.** Change `DC_Boyneswood_max_flow`
to something clearer and Apply asks: *rename it and update every reference, or
treat it as removing one entry and adding another?* One key gone and one key
arrived is genuinely ambiguous — it looks the same whether you renamed
something or swapped it for something else — so the dock asks rather than
picking for you. Say yes and the entry keeps its place in the file, so a
rename stays a one-line diff.

If a reference is left pointing at nothing, an amber strip says so with the
exact path (`parameters.DC_Boyneswood_max_flow.parameters[0] references
'…_base', which the model does not define`). See
[Keeping references honest](#keeping-references-honest).

Drag the dock's top edge to resize it; **✕** hides it.

## Adding recorders and parameters

Recorders are what a run actually writes down, and real models put the same
handful on every node. So they are buttons, not JSON.

**On a node.** The node panel has a **Recorders** block listing everything
watching the selected node — name and pywr type, each with a **✕** — and a row
of one-click adds under it. What's offered follows the node: a demand node can
run a *deficit*, a storage node has a *volume*, everything gets *flow* and
*total flow*. Each lands named the way the model already names things
(`DC_Boyneswood_flow`), and a name that would clash gets a suffix rather than
overwriting.

**+ record the usual things** adds the whole standard set in one edit — flow,
total flow, and for a demand node the deficit, total deficit and deficit
frequency. It only offers what's missing, and the count is in the button, so
running it twice is harmless.

**Whole parameter chains.** A demand centre's capacity is never one parameter —
it's a base read from a licence table, a monthly profile, and the product of
the two, with the node's `max_flow` pointing at the result. **Common set-ups**
on the node panel builds the whole chain:

| Template | For | What it creates |
|---|---|---|
| **seasonal demand cap** | demand nodes | `_max_flow_base` (from a table) × `_max_flow_factor` (monthly profile) → `_max_flow`, wired to the node |
| **annual licence volume** | storage / virtual storage | `_max_volume` read from a table, wired to the node |
| **base + top-up abstraction** | sources | average and peak output from a table, the top-up above average shaped by a monthly profile, wired to `max_flow` |
| **deficit alarm** | demand nodes | the deficit recorder, a `RecorderThresholdParameter` over it, and an `EventRecorder` counting sustained spells |

The dialog asks only for what actually differs between one node and the next —
in practice the table row, since the table, the columns and the profile are the
same across a model, so those come pre-filled. Reference fields suggest the
names your model already defines.

**You see the JSON before it lands.** The dialog shows exactly what will be
created, updating as you type, with a line saying how many entries are new and
which node attribute will point at them. Nothing is a special kind of
parameter — it's ordinary JSON afterwards, owned by the same `{ } edit` buttons
as everything else.

Two things it will not do quietly. An entry that already exists is **left
alone** rather than overwritten, so running a template on a half-set-up node
finishes the job instead of clobbering it. And if the entry the node would
point at is one of those, it says so plainly — *"already exists and is left as
it is, so the new parameters would not be connected to anything"* — because
that is the case where you'd otherwise end up with orphans.

**In Browse model.** Each of Parameters, Recorders and Tables has **+ add** with
a short form for the types models are actually built from — a constant (a fixed
number, or a row and column of a table), a monthly profile, an `Aggregated`
combination, a `RecorderThresholdParameter`, an `EventRecorder`, and so on.
Fields that hold the name of something else (a table, a parameter, a node)
suggest the names your model already defines, so a reference is picked rather
than retyped.

Anything the forms don't cover is one dropdown entry away: **Write it as JSON
myself** gives you the raw editor, with the same validation. The forms are a
shortcut for the common cases, never a ceiling — pywr has hundreds of types and
this is deliberately not trying to be a schema for all of them.

## Keeping references honest

A pywr model is held together by names: a node's `max_flow` is the *name* of a
parameter, an `Aggregated` parameter lists the *names* of others, a
`RecorderThresholdParameter` names a *recorder*. Rename or delete one carelessly
and the model breaks somewhere you weren't looking.

**Rename carries every reference.** In **Browse model**, each parameter,
recorder and table row has a **rename** button. It tells you what is at stake
first — *"Every reference follows the new name — 3 places refer to it"* — then
rewrites them all: node attributes, other parameters' operand lists, the
parameter that reads a recorder, the parameters that read a table. The entry
keeps its position in the file rather than jumping to the end.

**Delete tells you what you're breaking.** The **✕** on a row says *"2 other
places refer to it — they will point at a name the model no longer defines"*
before you commit, and names the exact paths afterwards. It is a warning, not a
veto: mid-restructure you may well want it gone.

**Dangling references are visible.** Every model change re-checks for names
referred to but defined nowhere, and the JSON dock shows them in an amber
strip. They are warnings, never errors — a half-finished edit legitimately has
them, and pywr is the final judge of what a given node type accepts.

Two deliberate limits:

- pywr keeps parameters and recorders in **separate namespaces**, so the same
  name can legally mean both. When it does, a bare reference can't be resolved,
  so those are left alone and the rename says so rather than guessing.
- The dangling check only follows keys that certainly hold a name (`parameter`,
  `parameters`, `control_curve`, `recorder`, `table`, the node-reference keys)
  plus a node's own attributes. A false "undefined" on every load of a valid
  model would be worse than missing one, so it errs quiet. Both the bundled
  example and a real 66-node zone model report clean.

## Editing the JSON directly

When the forms get in the way, edit the JSON itself — the explorer is editable
at three levels:

| Where | What it edits |
|---|---|
| **{ } edit JSON** (top right of the explorer) | The **whole model** — every section at once. |
| **{ } edit all parameters** (and tables / recorders) | One whole block: add, rename or remove entries. |
| **{ } edit** on any row | A single **parameter**, table, recorder — or a **node**'s full JSON, from the Nodes list. |

**Apply** parses your JSON and updates the model in memory, redrawing the
canvas immediately; the file on disk changes only when you **Save**. Node
positions are kept, and anything you add without one is placed automatically.

**Renaming is safe.** Change a node's `"name"` in its JSON and every reference
follows it — edges, aggregated/virtual node watch-lists, parameters and
recorders — and the node keeps its place on the canvas. The toast says how many
references were rewritten. (So the JSON editor now matches **Rename** on the
Node tab; either is fine.)

Bad edits never reach the canvas. A syntax slip reports the line and column;
structural mistakes are caught and named — a duplicate node name, an edge
pointing at a node that isn't there (`edges[0] references unknown node 'X'`),
a `parameters` block that isn't an object. The message shows under the editor
and your text stays put, so nothing is lost.

> Renaming in the **whole-model** editor is not tracked — a name that changes
> there reads as one node removed and another added, which is caught as a
> dangling edge. Rename from a node's own `{ } edit` and it's rewritten for you.

## Editing

- **New** — start an empty model to build from scratch or trace over an image.
- **+ Add ▾** — one menu for both: **Node** (pick the type under it, then click
  the canvas) or **Edge** (click source node, then destination). The button
  shows what you're placing until you switch back to **Select**.
- Rename (references in parameters/recorders are updated too), change type,
  edit/add/remove parameters (values are JSON — numbers or `{…}` parameter
  definitions), delete nodes/edges (with warnings if something still
  references them).
- Add recorders to a node in one click, build a whole parameter chain from a
  template, or add parameters/recorders/tables from a form — see
  [Adding recorders and parameters](#adding-recorders-and-parameters).
- Prefer raw JSON? See [the live JSON dock](#the-live-json-dock) for a panel
  that stays open and follows your selection, or
  [Editing the JSON directly](#editing-the-json-directly) for the modal
  editors — the whole model, a whole `parameters` block, or one entry at a time.
- **Export CSV pair** writes Graph-Overlay-compatible `nodes.csv` +
  `nodes_edges.csv`.

## Tracing a network from a map or schematic

Turn a picture of a water network into a pywr model:

1. **New** (or open an existing model) → **Trace image** → pick a map, scanned
   schematic, or screenshot. It drops in behind the network as a background
   layer that pans and zooms with everything else.
2. Position it: drag to move, drag the blue corner to resize, set **Opacity**,
   or **Fit to view**. Click **Lock** when the scale looks right.
3. Trace: **+ Node** to drop nodes on top of the map's features (tick **Quick
   place** to skip the name dialog and place on every click — rename later),
   **+ Edge** to connect them following the flow lines.
4. **Save** writes a normal pywr JSON. The image is *never* stored inside the
   model file. By default it's kept in the browser (per model); click **💾 Save
   beside model** in the trace panel to write the picture as a real image file
   next to the model — `<model>.pywrtrace.png` (or `.jpg`, matching what you
   loaded) plus a tiny `<model>.pywrtrace.json` holding only its position and
   scale. You can open the `.png` in any viewer, and the trace travels with the
   project and reopens on any machine. Either way the pywr output stays clean.

## Running and exploring flows

1. **▶ Run** executes the model in the private pywr environment; recorders
   are attached to every node automatically.
2. When it finishes, a **time slider** appears: edges are coloured and
   thickened by flow (blue ramp); press **space** or ▶ to animate. Click a node
   and the **flow value is drawn on each pipe along its highlighted water
   path** (toggle with the **123** button) — so you see magnitudes and the
   up/downstream path at once, at any timestep. pywr records only node totals,
   so at a split feeding a junction the per-edge flow isn't directly available —
   the app splices a *transparent proxy link* onto those edges to recover the
   **exact** flow (the run is unchanged). The rare edge it still can't pin (an
   explicit destination slot) stays **dashed = estimated**.
3. The selected node's panel shows its **flow/volume time series**; hover for
   values, click the chart to jump the slider to that date.
4. **What-if:** press **Δ** next to any numeric parameter to stage a change
   (e.g. a demand or a max_flow), then **▶ Run what-if** — the model file is
   *not* modified. Tick several runs in the Runs tab to overlay them in the
   chart and compare.
5. **Getting the numbers out.** Runs live in memory, so nothing survives
   stopping the app unless you save it:

   | Button | What you get |
   |---|---|
   | **csv** (on a run) | The whole run as one wide CSV — a row per timestep, a column per node series *and* per edge. Edges pywr couldn't attribute exactly are headed `[estimated]`, so a reader never mistakes an estimate for a recorded flow. |
   | **csv** (on a node's chart) | Just that node, with a column per run currently plotted — so a what-if comparison downloads exactly as you see it. |
   | **save** (on a run) | Writes the run beside the model as `<model>.<run>.pywrrun.json`. **Open run…** loads it back — after a restart, on another machine, whenever. |

   CSVs are written utf-8-sig, so Excel reads accented node names correctly.

6. **Scenarios:** if the model defines pywr scenarios, a **Scenario** picker
   appears at the top of the Runs tab — one dropdown per scenario dimension.
   pywr solves the whole ensemble in a single run; the picker chooses which
   combination is drawn on the canvas and in the charts. Run different members
   and tick them in the Runs list to overlay them.
7. **Warnings:** if pywr emits non-fatal notes during a run (for example a
   model authored for a newer pywr than the bootstrapped one), the run still
   completes and a **⚠** badge appears on it in the Runs tab — click it to read
   the messages. Real failures show as *failed* with the full traceback.

---

## Project layout

```
PYWR_reader/
├── app.py                    thin entry point — builds Flask, registers blueprints
├── pywr_reader/
│   ├── session.py            Workspace + RunStore — the open model and its runs
│   ├── api/                  route blueprints, registered by app.py
│   │   ├── files.py              open / save / browse / graph / edit-as-JSON
│   │   ├── edit.py               layouts, node/edge CRUD, definition rename/delete
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
├── tests/                    191 unittest tests
│   ├── test_pywr_reader.py       unit: loaders, layouts, graph ops
│   ├── test_app_api.py           every route via Flask's test client
│   ├── test_frontend_contract.py app.js vs index.html vs the API (no deps)
│   ├── test_frontend_smoke.py    the real UI in a browser (needs playwright)
│   ├── test_perf.py              a 1,200-node model stays responsive
│   └── test_run_integration.py   really runs pywr (needs .pywr-env)
├── examples/gw_network/      small self-contained runnable demo
├── requirements.txt          flask (that's the lot)
├── requirements-dev.txt      ruff + playwright, for dev/tests
├── LICENSE                   MIT
├── run_tests.sh              test runner
└── .pywr-env/                private pywr environment (created on demand)
```

## Roadmap

- [x] Read pywr JSON / .tcm / CSV; auto-layout; positions merge
- [x] Node/edge editing, rename with reference rewriting, save/export
- [x] External data-file resolution (finds `.xlsx`/`.h5`/`.csv` by basename)
- [x] One-click pywr environment; run with recorders on every node
- [x] Flow explorer: path highlighting, flow-scaled edges, time slider,
      animation, per-node charts, what-if runs with comparison
- [x] Verified on a real 80-year, 29,586-timestep zone model (162 nodes)
- [x] Image tracing mode — trace a network over a map/schematic (New +
      Trace image + Quick place); image kept as a per-model browser overlay or
      a portable `.pywrtrace.json` sidecar beside the model
- [x] Model explorer — filterable, readable browse of nodes/params/tables
- [x] Scenario picker — when a model defines pywr scenarios, choose which
      ensemble member to view; run several and overlay them to compare
- [x] Per-edge exact flows at splits/junctions — a transparent proxy link is
      spliced onto ambiguous edges so pywr records their exact flow
- [x] Layout picker — layered / force-directed / grouped / radial, applied
      instantly with Undo, for models that ship no usable schematic positions
- [x] Editable JSON — the whole model, a section, or a single parameter/node,
      validated before it lands (duplicate names, dangling edges, bad blocks);
      renaming a node rewrites every reference to it
- [x] Live JSON dock — a JSON panel that stays open and follows the selection,
      showing a node with the parameters, recorders and tables that hang off
      it; edits flow both ways and unapplied typing is never overwritten
- [x] Reference safety for parameters, recorders and tables — rename rewrites
      every reference to them, delete says what it leaves dangling, and names
      referred to but defined nowhere show as warnings
- [x] Guided add for recorders and parameters — per-node one-click
      recorders that suit the node type, "record the usual things" for the
      standard set, and add-forms in the explorer whose reference fields
      suggest the names the model already defines
- [x] Parameter-chain templates — build a demand centre's base × profile
      capacity, a licence volume, a base/top-up abstraction or a deficit alarm
      in one edit, previewed as JSON before it lands and wired to the node
- [x] Export results — whole-run and per-node CSV; save a run beside the
      model and reopen it later
- [x] Data file viewer — table or line-plot of the h5/xlsx/csv a model reads
- [ ] GeoJSON/Shapefile import for geographic networks
- [ ] Open a submodel together with its inputs file, for model suites that
      split the network and its parameters across separate files

## Licence

MIT — see [LICENSE](LICENSE). Use it, change it, share it; just keep the
copyright notice.

Author: Shalini B
