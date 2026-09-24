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
import { $, el, toast } from "./dom.js";
// the canvas core owns selection, the series cache and the chart builder; it
// imports initResults from here, the same deliberate cycle the other docks use
import { getSeries, buildChart, selectNode } from "./app.js";

const R = { open: false, pinned: [], drawing: false };

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

export function togglePin(name) {
  const at = R.pinned.indexOf(name);
  if (at >= 0) R.pinned.splice(at, 1);
  else R.pinned.push(name);
  render();
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
  const run = activeRun();
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

  if (R.drawing) return;            // a slow fetch shouldn't queue up redraws
  R.drawing = true;
  let seriesList = [];
  try {
    for (const [i, name] of names.entries()) {
      try {
        const data = await getSeries(run.id, name);
        const values = data.flow || data.volume;
        if (values) {
          seriesList.push({
            label: name, color: RUN_COLORS[i % RUN_COLORS.length],
            dates: data.dates, values, kind: data.flow ? "flow" : "volume",
          });
        }
      } catch { /* that node has no recorded series in this run */ }
    }
  } finally { R.drawing = false; }

  if (!R.open) return;              // closed while we were fetching
  const chips = el("div", { class: "res-chips" },
    ...names.map((name, i) => {
      const found = seriesList.find(s => s.label === name);
      return chip(name, RUN_COLORS[i % RUN_COLORS.length],
                  R.pinned.includes(name), found ? found.kind : "no series");
    }));

  if (!seriesList.length) {
    body().replaceChildren(chips, el("p", { class: "muted small" },
      "Nothing was recorded for these nodes in this run."));
    return;
  }

  // flow is a rate and volume is a quantity; sharing one axis is fine to look
  // at but would be a lie to leave unlabelled
  const kinds = [...new Set(seriesList.map(s => s.kind))];
  const width = Math.max(420, Math.floor(body().clientWidth || 900) - 24);
  const height = Math.max(120, (body().clientHeight || 220) - 64);
  body().replaceChildren(
    chips,
    buildChart(seriesList, { width, height }),
    kinds.length > 1
      ? el("p", { class: "muted small" },
          "Flow and volume are drawn on the same axis here — compare "
          + "shapes, not heights.")
      : null);
}

export function resultsChanged() { render(); }

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
  $("results-pin").addEventListener("click", () => {
    if (!S.sel || S.sel.kind !== "node") return toast("Select a node first", true);
    togglePin(S.sel.name);
  });
  initGrip();
  window.addEventListener("resize", () => { if (R.open) render(); });
}
