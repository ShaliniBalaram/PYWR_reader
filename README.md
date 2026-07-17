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

```bash
cd PYWR_reader
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt   # just Flask
./.venv/bin/python app.py
# → open http://127.0.0.1:5321 in a browser
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

**97 tests**, using only Python's stdlib `unittest`. On a bare checkout all of
them pass in under a second — the two groups that need an extra skip
themselves rather than fail:

| Group | Needs | Covers |
|---|---|---|
| unit + API | just Flask | loaders, layout, graph ops, every route |
| frontend contract | just Flask | that `app.js` still agrees with `index.html` and `app.py` — every `$("id")` it looks up exists, every `/api/…` it calls is served |
| pywr integration | the pywr environment | really executing a model, exact per-edge flows, scenarios |
| browser smoke | `requirements-dev.txt` + chromium | the real UI in a headless browser: the network draws, path tracing, each layout, Undo, the Add menu, JSON editing |

The frontend has no build step and no test framework, so the contract tests
exist to catch what silently breaks otherwise: rename an element id in one
file and not the other, or call a route that no longer exists, and they fail
with the name of the culprit. To enable the browser tests too:

```bash
./.venv/bin/pip install -r requirements-dev.txt
./.venv/bin/playwright install chromium      # ~90 MB, one time
./run_tests.sh
```

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

**External data files:** real models point `tables`/`parameters` at data files
by absolute paths from another machine (`C:\Data\...\SEW_RZ5.xlsx`). On open,
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
- Prefer raw JSON? See [Editing the JSON directly](#editing-the-json-directly)
  — the whole model, a whole `parameters` block, or one entry at a time.
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
5. **Scenarios:** if the model defines pywr scenarios, a **Scenario** picker
   appears at the top of the Runs tab — one dropdown per scenario dimension.
   pywr solves the whole ensemble in a single run; the picker chooses which
   combination is drawn on the canvas and in the charts. Run different members
   and tick them in the Runs list to overlay them. Try it with
   `examples/scenario_network/pywr_model.json` (a `demand` scenario with
   low / mid / high members).
6. **Warnings:** if pywr emits non-fatal notes during a run (for example a
   model authored for a newer pywr than the bootstrapped one), the run still
   completes and a **⚠** badge appears on it in the Runs tab — click it to read
   the messages. Real failures show as *failed* with the full traceback.

---

## Worked example — a real 80-year model (SEW WRZ5)

This model was tested end to end from
`/Volumes/Shalini B/Research work/Generic PYWR/zone 5/zone 5/`:

1. **Open** `SEW WRZ5.json` (162 nodes, 145 edges). Its stored positions are
   all `[1000, 1000]`, so auto-layout draws the network.
2. **Model → Data files** shows both referenced files resolved: `SEW_RZ5.xlsx`
   (in the model folder) and `SEW_WRZ5_Historic_timeseries.h5` (found a couple
   of levels up under `Generic PYWR/`).
3. **▶ Run** solves **29,586 daily timesteps (1920–2000)** with the `glpk-edge`
   solver in ~25 s; the time bar and per-node charts populate. The run carries
   one **⚠** note — the model declares `minimum_version 1.31.1` while the
   bootstrapped pywr is 1.29.0 — but it completes and produces full results.
4. To use the model's *real* schematic instead of auto-layout, **Open** the
   `Zone5.tcm` viewer file with the model already loaded — its positions land
   on 158 of the 162 nodes. **Save** to bake them into the JSON.

Nothing in that folder was modified; the `.xlsx`/`.h5` data stays where it is.

## Project layout

```
PYWR_reader/
├── app.py                    Flask app + API
├── pywr_reader/
│   ├── model_io.py           pywr JSON / .tcm / CSV readers, writers
│   ├── layout.py             layered / force / grouped / radial layouts (no deps)
│   ├── graphops.py           trace, add/delete/rename, reference rewriting
│   ├── dataresolve.py        locate external data files by basename
│   ├── envsetup.py           one-click pywr environment bootstrap
│   └── runner.py             executed inside .pywr-env — runs pywr, dumps series
├── static/                   frontend (vanilla JS + SVG, no build step)
├── tests/                    97 unittest tests
│   ├── test_pywr_reader.py       unit: loaders, layouts, graph ops
│   ├── test_app_api.py           every route via Flask's test client
│   ├── test_frontend_contract.py app.js vs index.html vs app.py (no deps)
│   ├── test_frontend_smoke.py    the real UI in a browser (needs playwright)
│   └── test_run_integration.py   really runs pywr (needs .pywr-env)
├── examples/gw_network/      small self-contained runnable demo
├── examples/scenario_network/  runnable demo with a pywr scenario ensemble
├── examples/split_network/   runnable demo with an ambiguous split/junction edge
├── requirements.txt          flask (that's the lot)
├── requirements-dev.txt      playwright, for the optional browser tests
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
- [x] Verified on a real 80-year, 29,586-timestep model (SEW WRZ5)
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
- [ ] GeoJSON/Shapefile import for geographic networks
- [ ] Open a submodel together with its inputs file (compose a
      `wrse_simulator`-style fragment into a runnable model)

## Licence

MIT — see [LICENSE](LICENSE). Use it, change it, share it; just keep the
copyright notice.

Author: Shalini B
