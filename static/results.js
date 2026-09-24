/* The results dock — a plot of model output across the bottom of the window,
   so you can watch the numbers while you move around the network.

   This complements the chart in the node panel rather than replacing it. That
   one shows ONE node across SEVERAL runs (how did this change?); this one shows
   SEVERAL nodes from ONE run (what is happening across the network?). Keeping
   the two questions on separate axes is what stops the legend becoming a
   node x run grid nobody can read.

   Nothing here changes existing behaviour: the dock is hidden until asked for,
   and it reads the same /api/run/<id>/series the node panel already caches. */

import { S } from "./state.js";
import { RUN_COLORS } from "./palette.js";
import { $, el, toast, setChildren } from "./dom.js";
// the canvas core owns selection, the series cache and the chart builder; it
// imports initResults from here, the same deliberate cycle the other docks use
import { getSeries, buildChart, selectNode } from "./app.js";

const R = { open: false, pinned: [], drawing: false, again: false,
            compareRuns: false };

/* Dash patterns separate runs while colour keeps identifying the node. Going
   the other way — a colour per run — would have needed a node x run colour
   grid, which is exactly the legend this dock was built to avoid. */
const RUN_DASHES = ["", "5 3", "2 3", "8 3 2 3"];

const body = () => $("results-body");

/** Which nodes to plot: the ones you pinned, plus whatever is selected — so
 *  the dock is useful the moment you open it, with nothing to set up. */
function plotted() {
  const names = [...R.pinned];
  if (S.sel && S.sel.kind === "node" && !names.includes(S.sel.name)) {
    names.push(S.sel.name);
  }
  return names;
}

function activeRun() {
  return S.activeRun && S.activeRun.status === "done" ? S.activeRun : null;
}

/** The runs to overlay: the active one, or every ticked one when asked.
 *
 *  The dock read S.activeRun and nothing else, so the small sidebar chart
 *  could compare two runs and the large docked one — the surface you would
 *  actually want for it — could not. */
function runsToPlot() {
  const active = activeRun();
  if (!active) return [];
  if (!R.compareRuns) return [{ id: active.id, label: active.label }];
  const ticked = S.runs.filter(r => r.status === "done" && S.compare.has(r.id));
  if (!ticked.some(r => r.id === active.id)) {
    ticked.push({ id: active.id, label: active.label });
  }
  return ticked.map(r => ({ id: r.id, label: r.label }));
}

/** Show the compare control only when there is something to compare with. */
function syncCompareControl() {
  const doneRuns = S.runs.filter(r => r.status === "done").length;
  $("results-compare-wrap").classList.toggle("hidden", doneRuns < 2);
  $("results-compare").checked = R.compareRuns;
}

export function togglePin(name) {
  const at = R.pinned.indexOf(name);
  if (at >= 0) R.pinned.splice(at, 1);
  else R.pinned.push(name);
  render();
}

/** A short line in the run's dash pattern, for the legend chip. */
function dashSwatch(dash) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 22 6");
  svg.setAttribute("width", "22");
  svg.setAttribute("height", "6");
  const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
  line.setAttribute("x1", "0"); line.setAttribute("x2", "22");
  line.setAttribute("y1", "3"); line.setAttribute("y2", "3");
  line.setAttribute("stroke", "var(--ink)");
  line.setAttribute("stroke-width", "2");
  if (dash) line.setAttribute("stroke-dasharray", dash);
  svg.append(line);
  return svg;
}

function chip(name, colour, isPinned, kind) {
  return el("span", { class: "res-chip" + (isPinned ? " pinned" : "") },
    el("span", { class: "res-dot", style: `background:${colour}` }),
    el("button", { class: "res-name", title: "Show this node on the canvas",
      onclick: () => selectNode(name) }, name),
    kind ? el("span", { class: "res-kind" }, kind) : null,
    el("button", {
      class: "res-pin",
      title: isPinned ? "Stop plotting this node"
                      : "Keep plotting this node when you select another",
      onclick: () => togglePin(name),
    }, isPinned ? "✕" : "pin"));
}

async function render() {
  if (!R.open) return;
  syncCompareControl();
  const run = activeRun();
  $("results-run").textContent = run ? run.label : "";
  if (!run) {
    body().replaceChildren(el("p", { class: "muted small" },
      "No results yet. Press ▶ Run to solve the model, and the output "
      + "appears here."));
    return;
  }
  const names = plotted();
  if (!names.length) {
    body().replaceChildren(el("p", { class: "muted small" },
      "Select a node on the canvas to plot what it did, and pin it to keep it "
      + "while you look at others."));
    return;
  }

  const runs = runsToPlot();
  // A slow fetch shouldn't queue up redraws — but dropping one outright left
  // the dock showing what it was drawing when the change arrived. Remember
  // that one is owed and run it once, after this one lands.
  if (R.drawing) { R.again = true; return; }
  R.drawing = true;
  let seriesList = [];
  const kindByNode = {};
  try {
    for (const [i, name] of names.entries()) {
      for (const [j, r] of runs.entries()) {
        try {
          const data = await getSeries(r.id, name);
          const values = data.flow || data.volume;
          if (!values) continue;
          kindByNode[name] = data.flow ? "flow" : "volume";
          seriesList.push({
            // one colour per node, one dash per run: the legend stays readable
            label: runs.length > 1 ? `${name} · ${r.label}` : name,
            color: RUN_COLORS[i % RUN_COLORS.length],
            dash: runs.length > 1 ? RUN_DASHES[j % RUN_DASHES.length] : "",
            dates: data.dates, values, kind: kindByNode[name],
          });
        } catch { /* that node has no recorded series in this run */ }
      }
    }
  } finally { R.drawing = false; }

  if (R.again) { R.again = false; return render(); }
  if (!R.open) return;              // closed while we were fetching
  const chips = el("div", { class: "res-chips" },
    ...names.map((name, i) => chip(name, RUN_COLORS[i % RUN_COLORS.length],
      R.pinned.includes(name), kindByNode[name] || "no series")),
    ...(runs.length > 1 ? runs.map((r, j) => el("span", { class: "res-chip run" },
      el("span", { class: "res-dash" }, dashSwatch(RUN_DASHES[j % RUN_DASHES.length])),
      el("span", { class: "res-name run" }, r.label))) : []));

  if (!seriesList.length) {
    body().replaceChildren(chips, el("p", { class: "muted small" },
      "Nothing was recorded for these nodes in this run."));
    return;
  }

  // flow is a rate and volume is a quantity; sharing one axis is fine to look
  // at but would be a lie to leave unlabelled
  const kinds = [...new Set(seriesList.map(s => s.kind))];
  const width = Math.max(420, Math.floor(body().clientWidth || 900) - 24);
  const height = Math.max(120, (body().clientHeight || 220) - 88);
  setChildren(body(),
    chips,
    buildChart(seriesList, { width, height }),
    kinds.length > 1
      ? el("p", { class: "muted small" },
          "Flow and volume are drawn on the same axis here — compare "
          + "shapes, not heights.")
      : null,
    el("p", { class: "muted small" },
      "Scroll to zoom, drag to pan, double-click to reset."));
}

export function resultsChanged() { render(); }

/** Let go of the previous model. The pins name nodes that may not exist in
 *  the next one, and an empty model was still showing the last one's charts. */
export function resultsReset() {
  R.pinned = [];
  R.compareRuns = false;
  $("results-run").textContent = "";
  $("results-compare-wrap").classList.add("hidden");
  if (R.open) render();
}

export function toggleResults(force) {
  R.open = force == null ? !R.open : !!force;
  $("resultsdock").classList.toggle("hidden", !R.open);
  $("btn-results").classList.toggle("active", R.open);
  if (R.open) render();
}

function initGrip() {
  const grip = $("results-grip"), dock = $("resultsdock");
  grip.addEventListener("mousedown", down => {
    down.preventDefault();
    const startY = down.clientY, startH = dock.getBoundingClientRect().height;
    const move = e => {
      dock.style.height = Math.max(140, Math.min(window.innerHeight - 160,
        startH + (startY - e.clientY))) + "px";
      render();
    };
    const up = () => {
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
    };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
  });
}

export function initResults() {
  $("btn-results").addEventListener("click", () => toggleResults());
  $("results-close").addEventListener("click", () => toggleResults(false));
  $("results-clear").addEventListener("click", () => { R.pinned = []; render(); });
  $("results-compare").addEventListener("change", e => {
    R.compareRuns = e.target.checked;
    render();
  });
  $("results-pin").addEventListener("click", () => {
    if (!S.sel || S.sel.kind !== "node") return toast("Select a node first", true);
    togglePin(S.sel.name);
  });
  initGrip();
  window.addEventListener("resize", () => { if (R.open) render(); });
}
