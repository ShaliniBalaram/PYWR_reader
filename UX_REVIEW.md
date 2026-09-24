# PyWR Reader — feature audit and UX review

A pass over every feature, driven through the browser rather than read off the
code. Two models were used: the bundled `examples/gw_network` demo (11 nodes)
and the real SEW WRZ5 zone model (162 nodes, 29,586 daily timesteps, opened
both as `SEW WRZ5.json` and via `Zone5.tcm`).

**Does it run?** Yes. 282 unit tests pass, ruff is clean, an 80-year run of the
real model completes in well under a minute, and the app is free of console
errors in normal use.

**Status:** every finding below has been fixed and re-verified in the browser.
The ratings are the ones from the original pass, with a *now* column where the
fix changed the answer.

---

## Ratings

Out of 5. "Verified" means it was actually exercised.

| Feature | Was | Now | Note |
|---|:--:|:--:|---|
| **JSON dock** (live, follows selection) | 5 | 5 | Three scopes, edit→apply round-trips to the node panel, precise parse errors with line/column, Apply blocked while invalid. The best thing in the app. |
| **Data viewer** (h5/xlsx/csv) | 5 | 5 | Read a real 35,042 × 38 h5. Table + plot, series chips, scroll-zoom and pan. |
| **Rename with reference rewriting** | 5 | 5 | Renaming `Reservoir_A` reported *"updated reference at recorders.Reservoir_volume.node"* — it says exactly what it touched. |
| **Model explorer** (Browse model) | 4.5 | 5 | Filter now autofocuses and says "2 of 11". |
| **Model tab summary** | 4.5 | 5 | Counts, period, data-file resolution, and now the broken-reference list. |
| **Find node** | 4.5 | 4.5 | Name and type matching, keyboard-driven, centres without refitting. |
| **Canvas** (render, zoom, pan, path highlight) | 4.5 | 4.5 | 162 nodes render instantly; upstream-blue / downstream-orange is genuinely clear. |
| **What-if** | 4.5 | 4.5 | Δ offered only on scalars, staged list, model file untouched, runs as a separate labelled run. |
| **Add node / add edge** | 4.5 | 4.5 | Mode banner, sensible default names, edge mode persists for chaining. |
| **Run model** | 4.5 | 5 | 29,586 steps fine; live progress; failures explained before the traceback. |
| **Recorder / parameter catalog + bundles** | 4.5 | 4.5 | ⚠ UI only — well-scoped quick-adds per node type; not applied end to end. |
| **Data-file resolution** | 4.5 | 5 | Clear located/missing states, "+ Add data folder…" recovery, and Save As no longer lies about it. |
| **Layouts + Undo** | 4 | 5 | Four layouts; Undo now covers every edit, 25 deep. |
| **View menu** (labels / filters / .tcm style) | 4 | 4.5 | A label category the node filter is hiding is greyed rather than tickable. |
| **Per-node chart** | 4 | 4.5 | Full 80-year series, CSV export, overlays compared runs, and now zooms. |
| **Save / Save As / Export CSV** | 3.5 | 4.5 | Overwrite is confirmed, the suggestion is a copy's name, data files are re-checked. |
| **Results dock** | 3.5 | 4.5 | Compares nodes *and* runs; zooms; lets go of the previous model. |
| **Open / file browser** | 3.5 | 4 | Starts where you were last time. |
| **Dangling-reference reporting** | 3 | 5 | Model tab, a badge beside Run, named before a delete, and quoted when a run fails. |
| **Time slider / animation** | 3 | 4.5 | Date box and a speed picker that multiplies the stride. |
| **Run progress** | 2.5 | 4.5 | Percentage, step count, elapsed and estimated time. |
| **Session state across model changes** | 2 | 4.5 | Runs, results, what-ifs and search all go with the model; unsaved work is confirmed. |
| **Trace image** (map / PDF) | — | — | Needs a native file picker this harness can't drive. Untested; not rated. |
| **Scenario picker** | — | — | Neither model defines pywr scenarios. Untested; not rated. |

---

## Bugs found and fixed

1. **View menu closed on every checkbox click.** Re-rendering the menu detached
   the element the click started on, so the document-level "clicked outside"
   handler saw no `.menu-wrap` ancestor and dismissed it. Rows now update in
   place.
2. **Literal `null` rendered in the Results dock.** `el()` drops null children;
   the native `replaceChildren` renders them as the text `"null"`.
3. **Literal `null` rendered in the Node panel** for any node type no bundle
   fits (every plain link) — `bundlesBlock` returns null by design.
   2 and 3 are now prevented at source by `setChildren()` in `dom.js`.
4. **Toolbar menus clipped off-screen.** At 1024px the **+ Add** menu hung 122px
   past the window edge and its content was unreachable. Menus now flip to the
   button's right edge when they would overflow.
5. **A render error was swallowed in silence.** `refreshGraph()` caught
   everything to mean "no model open", so a `ReferenceError` mid-render left a
   half-drawn page with an empty console. It now catches only the fetch.
6. **A dropped redraw left the results dock stale.** A change arriving while a
   fetch was in flight was discarded rather than deferred, so the dock kept
   showing what it had been drawing. It now remembers one is owed.
7. **Three raw NUL bytes in `app.js`.** The per-frame edge-key separator was
   written as a literal control character, which made the file binary to grep
   and vulnerable to any editor that normalises it. It is now the `"\0"`
   escape behind a named `edgeKey()` helper — identical at runtime.

---

## A. Fixed — these lost or misrepresented the user's work

**A1. Unsaved changes were discarded silently.**
Opening another model, or starting a new one, took the edits with it without a
word. *Now:* a dialog naming the file, offering **Save first**, **Discard
changes** or **Cancel**. Closing the model asks too.

**A2. Runs and results survived a model change.**
A stale run drove the time slider at **1920-01-01** while the open model ran
2000-01-01 → 2000-12-31, and an empty model still charted the previous one.
*Now:* the server drops its run store when the open model changes, and the
client drops the run list, the results dock's pins, the staged what-ifs and the
search box with it. A `.tcm` applied to the open model keeps them, because that
leaves the model in place — the server says which of the two happened rather
than leaving the UI to guess from the path.

**A3. Deleting a node didn't say what it broke.**
Deleting `River_Gauge_A` reported only "Deleted"; the next run died with
`KeyError: 'River_Gauge_A'`. *Now:* `GET /api/node/refs` is asked first, and the
confirmation reads *"One other part of the model still points at it … •
recorders.Gauge_flow.node"*. The same walker backs rename, so the two cannot
disagree about what a reference is.

**A4. Dangling-reference warnings were only visible inside the JSON dock.**
*Now:* a **Broken references** block in the Model tab, an amber **⚠ n** badge
beside Run that opens the full list, and the dock strip as before.

**A5. Save As silently broke data-file links.**
Saving elsewhere left the Model tab reporting *"All 1 referenced data file(s)
located"*, resolved against the **old** folder. *Now:* the save re-resolves,
says which files no longer sit beside the copy, and adds the original folder to
the search path so the session keeps working — *"params.csv does not sit beside
the saved copy — still being read from …. Copy it next to the model to make it
portable."*

---

## B. Fixed — friction

**B1. The Results dock can now compare runs.** A **compare runs** tick overlays
every ticked run: one colour per node, one dash pattern per run.

**B2. Run progress.** pywr has no progress callback, but it calls every
recorder's `after()` once per timestep — a supported extension point rather
than a reimplementation of `Model.run()`. The runner counts through one and
writes a small file; the status endpoint reads it. An 80-year run now shows
*"31% · 9,099 of 29,586 steps · 14s · ~32s left"*.

**B3. The time slider scales.** A date box jumps straight to a day (verified
landing exactly on 1983-06-01, t=23162, in a 29,586-step run), and a speed
picker multiplies the *stride* rather than the tick rate — 1500× plays the
whole run in about twenty seconds.

**B4. The whole run row activates it**, and a reload picks the last finished
run back up instead of leaving the canvas grey.

**B5. The colour key follows the View menu.** With the `.tcm`'s colours on it
lists that file's categories in its colours, and marks the ones a node filter
is hiding.

**B6. The Open dialog remembers where you were.**

**B7. A `.tcm` can be opened as a model at any time.** When none of its names
match, the app offers to open the model it describes instead; **✕** beside the
file name closes the model outright.

**B8. Save As suggests a copy's name** (`…-copy.json`) and confirms before
overwriting anything that exists.

**B9. Undo covers every edit**, 25 deep, with ⌘/Ctrl+Z. The server snapshots
the model before each edit — a deep copy of a 1,200-node model costs ~3 ms, and
unlike per-operation inverses it cannot drift out of step with what the
operation did.

**B10. A failed run explains itself.** The **why?** button leads with the
matched dangling reference; pywr's traceback is underneath.

**B11. No native `confirm()` left.** All six are the app's own dialog, which
can list the exact references at stake and offer three answers where there are
three ("Save first" as well as "Discard").

**B12. Run charts zoom.** Scroll to zoom about the cursor, drag to pan,
double-click to reset; the y-axis follows the window. A chart already showing
everything lets a downward scroll through to the panel, so it isn't a hole you
can't scroll past.

**B13. Small things.** The explorer's filter autofocuses and counts ("2 of
11"); collapsing the sidebar shifts the view by half the width gained so the
network stays centred; a label category the node filter is hiding is greyed
with an explanation instead of being tickable to no effect.
