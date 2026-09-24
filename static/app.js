/* PyWR Reader frontend — entry module. Imports the leaf modules (state,
   palette, dom, api) and wires the whole app together. Loaded as
   <script type="module">, so it keeps the "no build step" promise. */

import { S, BLOCK, NODE_R } from "./state.js";
import { TYPE_STYLES, OTHER_STYLE, RUN_COLORS, FLOW_RAMP, NODE_TYPES,
         typeStyle, flowColor } from "./palette.js";
import { $, el, svgEl, setChildren, fmt, toast, openModal, closeModal,
         ask, confirmAsk } from "./dom.js";
import { api } from "./api.js";
import { dataViewer } from "./dataviewer.js";
import { openModelExplorer } from "./explorer.js";
import { initDock, toggleDock, dockModelChanged, dockSelectionChanged }
  from "./jsondock.js";
import { recordersFor, recorderDef, suggestName as suggestRecorderName }
  from "./catalog.js";
import { bundlesBlock } from "./bundles.js";
import { isPdf, pdfFirstPageToPng } from "./pdfimport.js";
import { initResults, toggleResults, resultsChanged, resultsReset }
  from "./results.js";
import { initViewPrefs, viewPrefsChanged, nodeHidden, labelVisible,
         nodeStyle, nodeRadius, labelFontSize, restoreSavedView, revealNode,
         legendEntries } from "./viewprefs.js";

$("modal-backdrop").addEventListener("mousedown", e => {
  if (e.target === $("modal-backdrop")) closeModal();
});

/* ------------------------------------------------------ canvas render */
const canvas = $("canvas");
const viewport = $("viewport");
const gBg = $("g-bg");
const gEdges = $("g-edges");
const gNodes = $("g-nodes");
const gLabels = $("g-labels");
const nodeEls = new Map();   // name -> {g, shape, label}
let edgeEls = [];            // idx -> {hit, line}
let edgeLabelEls = [];       // idx -> <text> flow value over the pipe

function applyView() {
  viewport.setAttribute("transform",
    `translate(${S.view.x},${S.view.y}) scale(${S.view.k})`);
  refreshBgHandle();
}

function edgeStubRadius(name) {
  const node = S.nodeIdx.get(name);
  return (node ? nodeRadius(node) : NODE_R) + 2;
}

function edgePath(edge) {
  const a = S.positions[edge.src], b = S.positions[edge.dst];
  if (!a || !b) return null;
  const dx = b[0] - a[0], dy = b[1] - a[1];
  const len = Math.hypot(dx, dy) || 1;
  const ux = dx / len, uy = dy / len;
  const ra = edgeStubRadius(edge.src), rb = edgeStubRadius(edge.dst);
  return `M${a[0] + ux * ra},${a[1] + uy * ra} L${b[0] - ux * (rb + 3)},${b[1] - uy * (rb + 3)}`;
}

function nodeShape(style, radius) {
  const rn = radius || NODE_R;
  const extra = {};
  // a .tcm style sheet also names an outline colour; ours have none
  if (style.stroke) { extra.stroke = style.stroke; extra["stroke-width"] = 1.5; }
  if (typeof style.opacity === "number" && style.opacity < 1) {
    extra["fill-opacity"] = style.opacity;
  }
  if (style.shape === "square") {
    return svgEl("rect", { x: -rn, y: -rn, width: rn * 2,
      height: rn * 2, rx: 4, fill: style.color, ...extra });
  }
  if (style.shape === "diamond") {
    const r = rn + 2;
    return svgEl("polygon", { points: `0,${-r} ${r},0 0,${r} ${-r},0`,
      fill: style.color, ...extra });
  }
  return svgEl("circle", { r: rn, fill: style.color, ...extra });
}

function renderGraph() {
  gEdges.replaceChildren();
  gNodes.replaceChildren();
  gLabels.replaceChildren();
  nodeEls.clear();
  edgeEls = [];
  edgeLabelEls = [];
  if (!S.graph) return;

  // an edge whose endpoint is filtered out has nothing to join, so it goes too
  const dropped = new Set(S.graph.nodes.filter(nodeHidden).map(n => n.name));
  const labelPx = labelFontSize();
  S.graph.edges.forEach((edge, idx) => {
    const d = edgePath(edge);
    const hit = svgEl("path", { class: "edge-hit", d: d || "" });
    const line = svgEl("path", { class: "edge", d: d || "", "marker-end": "url(#arrow)" });
    if (dropped.has(edge.src) || dropped.has(edge.dst)) {
      hit.style.display = "none"; line.style.display = "none";
    }
    hit.addEventListener("mousedown", e => { e.stopPropagation(); selectEdge(idx); });
    gEdges.append(hit, line);
    edgeEls.push({ hit, line });
    const vlabel = svgEl("text", { class: "edge-val" });
    vlabel.style.display = "none";
    gLabels.append(vlabel);
    edgeLabelEls.push(vlabel);
  });

  for (const node of S.graph.nodes) {
    const style = nodeStyle(node);
    const radius = nodeRadius(node);
    const g = svgEl("g", { class: "node" });
    if (nodeHidden(node)) g.style.display = "none";
    const shapeNode = nodeShape(style, radius);
    // circle nodes get their type color; shape carries the group for CVD
    const label = svgEl("text", { y: radius + 13 });
    label.textContent = node.name;
    if (labelPx) label.style.fontSize = `${labelPx}px`;
    if (!labelVisible(node)) label.style.display = "none";
    g.append(shapeNode, label);
    positionNode(node.name, g);
    attachNodeEvents(g, node.name);
    gNodes.append(g);
    nodeEls.set(node.name, { g, shape: shapeNode, label });
  }
  refreshSelection();
  updateFrameVisuals();
  $("empty-state").classList.add("hidden");
}

function positionNode(name, g) {
  const p = S.positions[name];
  if (p) (g || nodeEls.get(name).g).setAttribute("transform",
    `translate(${p[0]},${p[1]})`);
}

function refreshEdgesFor(name) {
  S.graph.edges.forEach((edge, idx) => {
    if (edge.src === name || edge.dst === name) {
      const d = edgePath(edge);
      if (d) { edgeEls[idx].hit.setAttribute("d", d);
               edgeEls[idx].line.setAttribute("d", d); }
    }
  });
}

/* -------------------------------------------------- selection / trace */
function clientTrace(start, dir) {
  const adj = new Map();
  S.graph.edges.forEach((e, i) => {
    const key = dir === "up" ? e.dst : e.src;
    const val = dir === "up" ? e.src : e.dst;
    if (!adj.has(key)) adj.set(key, []);
    adj.get(key).push([val, i]);
  });
  const nodes = new Set([start]), edges = new Set(), queue = [start];
  while (queue.length) {
    const cur = queue.pop();
    for (const [nxt, ei] of adj.get(cur) || []) {
      edges.add(ei);
      if (!nodes.has(nxt)) { nodes.add(nxt); queue.push(nxt); }
    }
  }
  return { nodes, edges };
}

function refreshSelection() {
  const selName = S.sel && S.sel.kind === "node" ? S.sel.name : null;
  const selEdge = S.sel && S.sel.kind === "edge" ? S.sel.idx : null;

  let up = { nodes: new Set(), edges: new Set() };
  let down = { nodes: new Set(), edges: new Set() };
  if (selName && S.traceMode !== "off") {
    if (S.traceMode !== "down") up = clientTrace(selName, "up");
    if (S.traceMode !== "up") down = clientTrace(selName, "down");
  }
  const tracing = selName && S.traceMode !== "off";
  // flow-value labels follow the highlighted path (plus a directly picked edge)
  S.labelEdges = new Set([...up.edges, ...down.edges]);
  if (selEdge != null) S.labelEdges.add(selEdge);

  nodeEls.forEach((els, name) => {
    els.g.classList.toggle("sel", name === selName);
    const inTrace = up.nodes.has(name) || down.nodes.has(name);
    els.g.classList.toggle("dim", tracing && !inTrace);
  });
  edgeEls.forEach((els, idx) => {
    els.line.classList.toggle("sel", idx === selEdge);
    let stroke = "";
    if (up.edges.has(idx)) stroke = "var(--up)";
    if (down.edges.has(idx)) stroke = "var(--down)";
    els.line.style.stroke = stroke;
    els.line.classList.toggle("dim", tracing && !stroke && idx !== selEdge);
    if (tracing && stroke) els.line.style.strokeWidth = "2.5";
    else els.line.style.strokeWidth = "";
  });
  if (!tracing) updateFrameVisuals();
  else updateEdgeLabels();
}

export function selectNode(name) {
  S.sel = { kind: "node", name };
  refreshSelection();
  renderNodePanel();
  setTab("node");
  dockSelectionChanged();
  resultsChanged();
}
function selectEdge(idx) {
  const e = S.graph.edges[idx];
  S.sel = { kind: "edge", idx, src: e.src, dst: e.dst };
  refreshSelection();
  renderNodePanel();
  setTab("node");
  dockSelectionChanged();
}
function deselect() {
  S.sel = null;
  refreshSelection();
  renderNodePanel();
  dockSelectionChanged();
}

/* -------------------------------------------------- canvas interaction */
let drag = null; // {kind:'pan'|'node', ...}

function worldPoint(evt) {
  const rect = canvas.getBoundingClientRect();
  return [(evt.clientX - rect.left - S.view.x) / S.view.k,
          (evt.clientY - rect.top - S.view.y) / S.view.k];
}

canvas.addEventListener("wheel", e => {
  e.preventDefault();
  const rect = canvas.getBoundingClientRect();
  const mx = e.clientX - rect.left, my = e.clientY - rect.top;
  const k2 = Math.max(0.04, Math.min(10, S.view.k * Math.exp(-e.deltaY * 0.0016)));
  S.view.x = mx - (mx - S.view.x) * (k2 / S.view.k);
  S.view.y = my - (my - S.view.y) * (k2 / S.view.k);
  S.view.k = k2;
  applyView();
}, { passive: false });

canvas.addEventListener("mousedown", e => {
  if (e.button !== 0) return;
  if (S.mode === "addnode" && S.graph) {
    if (S.quickPlace) quickPlaceNode(worldPoint(e));
    else openAddNodeModal(worldPoint(e));
    return;
  }
  drag = { kind: "pan", sx: e.clientX, sy: e.clientY,
           ox: S.view.x, oy: S.view.y, moved: false };
});

// place a node instantly at the click, no dialog — for fast tracing.
// name auto-suggested from the toolbar type; rename later in the panel.
async function quickPlaceNode(pos) {
  const type = $("add-node-type").value;
  const name = suggestName(type);
  try {
    updateGraph(await api("/api/node/add", { node: { name, type }, pos }));
    selectNode(name);
  } catch (err) { toast(err.message, true); }
}

function attachNodeEvents(g, name) {
  g.addEventListener("mousedown", e => {
    if (e.button !== 0) return;
    e.stopPropagation();
    if (S.mode === "addedge") {
      handleEdgeClick(name);
      return;
    }
    const p = S.positions[name] || [0, 0];
    drag = { kind: "node", name, sx: e.clientX, sy: e.clientY,
             ox: p[0], oy: p[1], moved: false };
  });
  g.addEventListener("mouseenter", e => showNodeTip(name, e));
  g.addEventListener("mousemove", e => moveTip(e));
  g.addEventListener("mouseleave", hideTip);
}

window.addEventListener("mousemove", e => {
  if (!drag) return;
  const dx = e.clientX - drag.sx, dy = e.clientY - drag.sy;
  if (Math.abs(dx) + Math.abs(dy) > 3) drag.moved = true;
  if (drag.kind === "pan") {
    S.view.x = drag.ox + dx; S.view.y = drag.oy + dy;
    applyView();
  } else if (drag.kind === "node") {
    S.positions[drag.name] = [drag.ox + dx / S.view.k, drag.oy + dy / S.view.k];
    positionNode(drag.name);
    refreshEdgesFor(drag.name);
    updateEdgeLabels();
  } else if (drag.kind === "bgmove") {
    S.bg.x = drag.ox + dx / S.view.k;
    S.bg.y = drag.oy + dy / S.view.k;
    updateBgGeometry();
  } else if (drag.kind === "bgscale") {
    const newW = Math.max(20 / S.view.k, drag.w0 + dx / S.view.k);
    S.bg.scale = newW / S.bg.natW;   // top-left fixed, grow toward the cursor
    updateBgGeometry();
  }
});

window.addEventListener("mouseup", () => {
  if (!drag) return;
  const d = drag; drag = null;
  if (d.kind === "node") {
    if (!d.moved) selectNode(d.name);
    else api("/api/positions", { positions: { [d.name]: S.positions[d.name] } })
      .then(res => { S.graph.undo_label = res.undo_label; renderUndo(); })
      .catch(err => toast(err.message, true));
  } else if (d.kind === "pan" && !d.moved) {
    deselect();
    if (S.mode === "addedge") { S.edgeSrc = null; hint(); }
  } else if (d.kind === "bgmove" || d.kind === "bgscale") {
    persistBg();
  }
});

function handleEdgeClick(name) {
  if (!S.edgeSrc) {
    S.edgeSrc = name;
    hint(`Edge from “${name}” — now click the destination node (Esc to cancel)`);
    return;
  }
  const src = S.edgeSrc;
  S.edgeSrc = null;
  hint("Click the source node of the new edge");
  api("/api/edge/add", { src, dst: name })
    .then(updateGraph)
    .then(() => toast(`Edge ${src} → ${name} added`))
    .catch(err => toast(err.message, true));
}

function hint(text) {
  const box = $("canvas-hint");
  if (!text && S.mode === "addnode") text = "Click on the canvas to place the new node";
  if (!text && S.mode === "addedge") text = "Click the source node of the new edge";
  box.textContent = text || "";
  box.classList.toggle("hidden", !text);
}

function setMode(mode) {
  S.mode = mode;
  S.edgeSrc = null;
  for (const id of ["select", "addnode", "addedge"]) {
    $("btn-mode-" + id).classList.toggle("active", id === mode);
  }
  // the Add button carries what you're placing, so the mode stays visible
  // once its menu is closed
  const add = $("btn-add");
  add.classList.toggle("active", mode === "addnode" || mode === "addedge");
  add.textContent = (mode === "addnode" ? "+ Node"
                   : mode === "addedge" ? "+ Edge" : "+ Add") + " ▾";
  canvas.style.cursor = mode === "select" ? "default" : "crosshair";
  updateBgInteractivity();   // let clicks reach the canvas when tracing
  hint();
}

function fitView() {
  const pts = Object.values(S.positions);
  if (!pts.length) return;
  // fit to the 2nd–98th percentile box so a stray node doesn't shrink
  // everything else to a dot; the outliers are still reachable by panning.
  const q = (arr, p) => {
    const s = arr.slice().sort((a, b) => a - b);
    return s[Math.min(s.length - 1, Math.max(0, Math.floor(s.length * p)))];
  };
  const xs = pts.map(p => p[0]), ys = pts.map(p => p[1]);
  const minX = q(xs, 0.02), maxX = q(xs, 0.98);
  const minY = q(ys, 0.02), maxY = q(ys, 0.98);
  const w = canvas.clientWidth || window.innerWidth - 340;
  const h = canvas.clientHeight || window.innerHeight - 50;
  const pad = 70;
  const k = Math.max(1e-5, Math.min(2,
    (Math.max(pad * 3, w) - pad * 2) / Math.max(50, maxX - minX),
    (Math.max(pad * 3, h) - pad * 2) / Math.max(50, maxY - minY)));
  S.view.k = k;
  S.view.x = (w - (minX + maxX) * k) / 2;
  S.view.y = (h - (minY + maxY) * k) / 2;
  applyView();
}

// Pan so a node sits in the middle of the canvas, keeping the current zoom —
// used by the node search to jump to a hit without a jarring refit.
function centerOnNode(name) {
  const p = S.positions[name];
  if (!p) return;
  const w = canvas.clientWidth, h = canvas.clientHeight;
  // right after a model loads the canvas may not be laid out yet (width 0);
  // wait a frame rather than guess the window size, which would centre on the
  // window instead of the canvas and land the node off to the side
  if (!w || !h) { requestAnimationFrame(() => centerOnNode(name)); return; }
  S.view.x = w / 2 - p[0] * S.view.k;
  S.view.y = h / 2 - p[1] * S.view.k;
  applyView();
}

/* --------------------------------------------------- find a node (search) */
const searchInput = $("node-search-input");
const searchResults = $("node-search-results");
let searchMatches = [], searchActive = -1;

function runNodeSearch() {
  const q = searchInput.value.trim().toLowerCase();
  if (!q || !S.graph) { searchMatches = []; searchResults.classList.add("hidden"); return; }
  // name matches first, then type matches — the name is what you usually want
  const named = [], typed = [];
  for (const n of S.graph.nodes) {
    if (n.name.toLowerCase().includes(q)) named.push(n);
    else if (String(n.type || "").toLowerCase().includes(q)) typed.push(n);
  }
  searchMatches = named.concat(typed).slice(0, 30);
  searchActive = searchMatches.length ? 0 : -1;
  renderSearchResults();
}

function renderSearchResults() {
  if (!searchMatches.length) {
    searchResults.replaceChildren(el("div", { class: "sr-empty" },
      "No matching node"));
  } else {
    searchResults.replaceChildren(...searchMatches.map((n, i) =>
      el("div", { class: "sr-item" + (i === searchActive ? " active" : ""),
        // mousedown, not click: it fires before the input's blur hides this
        onmousedown: e => { e.preventDefault(); pickSearch(i); } },
        el("span", { class: "sr-name" }, n.name),
        el("span", { class: "sr-type" }, String(n.type || "link")))));
  }
  searchResults.classList.remove("hidden");
}

function pickSearch(i) {
  const n = searchMatches[i];
  if (!n) return;
  searchResults.classList.add("hidden");
  searchInput.blur();
  // the search looks through the whole model, so a hit may be one the View
  // filters are hiding — show its category again rather than centring on a
  // node that is not drawn
  const revealed = revealNode(n);
  if (revealed) {
    renderGraph();
    toast(`${revealed} nodes were hidden — switched back on to show ${n.name}`);
  }
  selectNode(n.name);      // highlights it and opens its panel
  centerOnNode(n.name);    // brings it into view at the current zoom
}

searchInput.addEventListener("input", runNodeSearch);
searchInput.addEventListener("focus", () => { if (searchInput.value) runNodeSearch(); });
searchInput.addEventListener("blur", () =>
  setTimeout(() => searchResults.classList.add("hidden"), 120));
searchInput.addEventListener("keydown", e => {
  if (e.key === "ArrowDown") {
    e.preventDefault();
    searchActive = Math.min(searchMatches.length - 1, searchActive + 1);
    renderSearchResults();
  } else if (e.key === "ArrowUp") {
    e.preventDefault();
    searchActive = Math.max(0, searchActive - 1);
    renderSearchResults();
  } else if (e.key === "Enter") {
    e.preventDefault();
    pickSearch(searchActive);
  } else if (e.key === "Escape") {
    searchInput.value = "";
    searchResults.classList.add("hidden");
    searchInput.blur();
  }
});

/* ------------------------------------------------------ trace image */
// The image lives in world coordinates inside #viewport, so it pans and
// zooms with the network. Unlocked, it can be dragged/resized; locked, it
// ignores the mouse so clicks fall through to place nodes and edges.
let bgImgEl = null, bgFrameEl = null, bgHandleEl = null;

function renderBg() {
  gBg.replaceChildren();
  bgImgEl = bgFrameEl = bgHandleEl = null;
  const bg = S.bg;
  $("trace-panel").classList.toggle("hidden", !bg);
  if (!bg) return;

  bgImgEl = svgEl("image", { x: bg.x, y: bg.y,
    width: bg.natW * bg.scale, height: bg.natH * bg.scale,
    opacity: bg.opacity, preserveAspectRatio: "none" });
  bgImgEl.setAttributeNS("http://www.w3.org/1999/xlink", "href", bg.src);
  bgImgEl.setAttribute("href", bg.src);
  gBg.append(bgImgEl);

  bgFrameEl = svgEl("rect", { class: "bg-frame", x: bg.x, y: bg.y,
    width: bg.natW * bg.scale, height: bg.natH * bg.scale });
  bgHandleEl = svgEl("rect", { class: "bg-handle" });
  gBg.append(bgFrameEl, bgHandleEl);

  if (!bg.locked) {
    bgImgEl.classList.add("bg-move");
    bgImgEl.addEventListener("mousedown", e => {
      e.stopPropagation();
      drag = { kind: "bgmove", sx: e.clientX, sy: e.clientY,
               ox: bg.x, oy: bg.y };
    });
    bgHandleEl.addEventListener("mousedown", e => {
      e.stopPropagation();
      drag = { kind: "bgscale", sx: e.clientX, ox0: bg.x,
               scale0: bg.scale, w0: bg.natW * bg.scale };
    });
  }
  updateBgInteractivity();
  refreshBgHandle();
  renderTracePanel();
}

/** The image only grabs the mouse while you're positioning it — unlocked AND
 *  in select mode. In +Node / +Edge mode it must be click-through so you can
 *  trace on top of it; otherwise the click lands on the image (move/resize) or,
 *  once locked, never reaches the add handler. This is what made "click to add
 *  a node over the image" silently pan/move instead. */
function updateBgInteractivity() {
  if (!S.bg || !bgImgEl) return;
  const interactive = !S.bg.locked && S.mode === "select";
  bgImgEl.style.pointerEvents = interactive ? "" : "none";
  const show = interactive ? "" : "none";
  if (bgFrameEl) bgFrameEl.style.display = show;
  if (bgHandleEl) bgHandleEl.style.display = show;
}

function refreshBgHandle() {
  const bg = S.bg;
  if (!bg || !bgHandleEl) return;
  const s = 11 / S.view.k;   // constant screen size regardless of zoom
  const cx = bg.x + bg.natW * bg.scale, cy = bg.y + bg.natH * bg.scale;
  bgHandleEl.setAttribute("x", cx - s / 2);
  bgHandleEl.setAttribute("y", cy - s / 2);
  bgHandleEl.setAttribute("width", s);
  bgHandleEl.setAttribute("height", s);
}

function updateBgGeometry() {
  if (!S.bg || !bgImgEl) return;
  const { x, y, natW, natH, scale, opacity } = S.bg;
  for (const elm of [bgImgEl, bgFrameEl]) {
    elm.setAttribute("x", x); elm.setAttribute("y", y);
    elm.setAttribute("width", natW * scale);
    elm.setAttribute("height", natH * scale);
  }
  bgImgEl.setAttribute("opacity", opacity);
  refreshBgHandle();
}

// Place a raster (data URL + pixel size) as the trace background, centred in
// the current view at ~70% of it. Shared by the image and PDF paths.
function placeTraceImage(src, natW, natH, msg) {
  const w = canvas.clientWidth || 900, h = canvas.clientHeight || 600;
  const scale = (w / S.view.k * 0.7) / natW;
  const cx = (w / 2 - S.view.x) / S.view.k;
  const cy = (h / 2 - S.view.y) / S.view.k;
  S.bg = { src, natW, natH, scale, opacity: 0.55, locked: false,
           x: cx - natW * scale / 2, y: cy - natH * scale / 2 };
  renderBg();
  persistBg();
  toast(msg);
}

const TRACE_HINT = "position it, then Lock (or +Node) and trace with + Node / + Edge";

function loadTraceImage(file) {
  // A PDF can't load into an <img>; its first page is rasterised to a PNG by
  // the lazily-loaded pdf.js, then placed like any image (so save/sidecar,
  // which want a real image, keep working — the .pdf itself is never stored).
  if (isPdf(file)) {
    toast("Rendering the PDF…");
    pdfFirstPageToPng(file)
      .then(({ dataUrl, width, height }) =>
        placeTraceImage(dataUrl, width, height, "PDF loaded — " + TRACE_HINT))
      .catch(err => toast("Could not render that PDF — " + err.message, true));
    return;
  }
  const reader = new FileReader();
  reader.onload = () => {
    const img = new Image();
    img.onload = () => placeTraceImage(reader.result, img.naturalWidth,
      img.naturalHeight, "Image loaded — " + TRACE_HINT);
    img.onerror = () => toast("Could not read that image", true);
    img.src = reader.result;
  };
  reader.onerror = () => toast("Could not read that file", true);
  reader.readAsDataURL(file);
}

function removeTraceImage() {
  const hadSidecar = S.bg && S.bg.sidecar;
  S.bg = null;
  renderBg();
  persistBg();
  if (hadSidecar) api("/api/traceimage", { trace: null }).catch(() => {});
}

function setBgLocked(locked) {
  if (!S.bg) return;
  S.bg.locked = locked;
  renderBg();
  persistBg();
  if (locked) {
    if (S.mode === "select") setMode("addnode");
    toast("Image locked — click the canvas to trace nodes");
  }
}

function scaleBgBy(factor) {
  if (!S.bg) return;
  // keep the image centre fixed while scaling
  const cx = S.bg.x + S.bg.natW * S.bg.scale / 2;
  const cy = S.bg.y + S.bg.natH * S.bg.scale / 2;
  S.bg.scale = Math.max(1e-4, S.bg.scale * factor);
  S.bg.x = cx - S.bg.natW * S.bg.scale / 2;
  S.bg.y = cy - S.bg.natH * S.bg.scale / 2;
  updateBgGeometry();
  persistBg();
}

function fitBgToView() {
  if (!S.bg) return;
  const w = canvas.clientWidth || 900, h = canvas.clientHeight || 600;
  const worldW = w / S.view.k * 0.9, worldH = h / S.view.k * 0.9;
  S.bg.scale = Math.min(worldW / S.bg.natW, worldH / S.bg.natH);
  S.bg.x = (w / 2 - S.view.x) / S.view.k - S.bg.natW * S.bg.scale / 2;
  S.bg.y = (h / 2 - S.view.y) / S.view.k - S.bg.natH * S.bg.scale / 2;
  updateBgGeometry();
  persistBg();
}

function renderTracePanel() {
  const bg = S.bg;
  if (!bg) return;
  const lock = $("tp-lock");
  lock.textContent = bg.locked ? "🔒 Locked — tracing" : "🔓 Unlocked — drag to place";
  lock.classList.toggle("locked", bg.locked);
  $("tp-opacity").value = Math.round(bg.opacity * 100);
  $("tp-hint").textContent = bg.locked
    ? "Use + Node to drop nodes on the map, + Edge to connect them. Unlock to reposition the image."
    : "Drag the image to place it; drag the blue corner to resize. Lock it when the scale looks right.";
  const saved = !!(S.graph && S.graph.path);
  const btn = $("tp-sidecar");
  btn.disabled = !saved;
  btn.title = saved ? "Write the image beside the model file"
                    : "Save the model first, then the trace can be stored beside it";
  $("tp-sidecar-status").textContent = bg.sidecar
    ? "✓ saved beside model (kept in sync)"
    : (saved ? "in browser only — click to store beside the model"
             : "in browser only — save the model to store it beside the file");
}

/* trace image persists per-model in localStorage (never in the pywr file) */
function bgKey() {
  return "pywr_reader_bg::" + ((S.graph && S.graph.path) || "__untitled__");
}
function persistBg() {
  try {
    if (S.bg) localStorage.setItem(bgKey(), JSON.stringify(S.bg));
    else localStorage.removeItem(bgKey());
  } catch (err) {
    // data URLs can exceed the quota — keep working in memory, just warn once
    if (S.bg && !S.bg._warned) {
      S.bg._warned = true;
      toast("Trace image is too large to remember across reloads", true);
    }
  }
  syncTraceSidecar();   // if saved beside the model, keep that file current
}
async function loadBgForModel() {
  if (!S.graph) { S.bg = null; renderBg(); return; }  // no model → no trace
  // Prefer the sidecar file beside the model (portable); fall back to the
  // browser cache for unsaved models or when no sidecar exists.
  let bg = null;
  if (S.graph.path) {
    try {
      const res = await api("/api/traceimage");
      if (res.trace) { bg = res.trace; bg.sidecar = true; }
    } catch { /* fall through to localStorage */ }
  }
  if (!bg) {
    try {
      const raw = localStorage.getItem(bgKey());
      bg = raw ? JSON.parse(raw) : null;
    } catch { bg = null; }
  }
  S.bg = bg;
  renderBg();
}

async function saveTraceSidecar() {
  if (!S.bg) return;
  if (!S.graph || !S.graph.path) {
    toast("Save the model first — the trace image is stored beside it", true);
    return;
  }
  try {
    // full save: the image bytes (src) are decoded to a real .png by the server
    const { src, x, y, scale, opacity, natW, natH, locked } = S.bg;
    const res = await api("/api/traceimage",
      { trace: { src, x, y, scale, opacity, natW, natH, locked } });
    S.bg.sidecar = true;
    renderTracePanel();
    toast("Trace image saved beside model: "
      + (res.image || res.path).split("/").pop());
  } catch (err) { toast(err.message, true); }
}

// keep the geometry sidecar in step once one exists (called after edits).
// Sends position/scale only — NOT the image — so nudging never rewrites the png.
function syncTraceSidecar() {
  if (S.bg && S.bg.sidecar && S.graph && S.graph.path) {
    const { x, y, scale, opacity, natW, natH, locked } = S.bg;
    api("/api/traceimage",
      { trace: { x, y, scale, opacity, natW, natH, locked } })
      .catch(() => { /* best-effort */ });
  }
}

/* --------------------------------------------------------- node tooltip */
let tipEl = null;
function showNodeTip(name, evt) {
  hideTip();
  const node = S.nodeIdx.get(name);
  if (!node) return;
  tipEl = el("div", { class: "chart-tip" });
  tipEl.append(el("div", { class: "d" }, name));
  tipEl.append(el("div", {}, node.type));
  const val = currentNodeValue(name);
  if (val != null) {
    tipEl.append(el("div", {}, `${val.kind}: ${fmt(val.value)}  (${currentDate() || ""})`));
  }
  document.body.append(tipEl);
  moveTip(evt);
}
function moveTip(evt) {
  if (!tipEl) return;
  tipEl.style.left = Math.min(window.innerWidth - 240, evt.clientX + 14) + "px";
  tipEl.style.top = (evt.clientY + 12) + "px";
}
function hideTip() { if (tipEl) { tipEl.remove(); tipEl = null; } }
// tooltips must never outlive their hover: clear on any click/scroll/re-render
document.addEventListener("mousedown", hideTip, true);
document.addEventListener("wheel", hideTip, true);

/* ------------------------------------------------------------ graph IO */
export function updateGraph(payload) {
  S.graph = payload;
  S.nodeIdx = new Map(payload.nodes.map(n => [n.name, n]));
  S.positions = {};
  for (const n of payload.nodes) if (n.pos) S.positions[n.name] = n.pos.slice();
  if (S.sel && S.sel.kind === "node" && !S.nodeIdx.has(S.sel.name)) S.sel = null;
  if (S.sel && S.sel.kind === "edge") S.sel = null;
  // keep the scenario picker in sync — preserve the choice across edits, but
  // clamp to the current sizes (and reset to member 0 when the shape changed)
  const dims = payload.scenario_dims || [];
  if (!Array.isArray(S.scenarioSel) || S.scenarioSel.length !== dims.length) {
    S.scenarioSel = dims.map(() => 0);
  } else {
    S.scenarioSel = dims.map((d, i) =>
      Math.max(0, Math.min(S.scenarioSel[i] || 0, d.size - 1)));
  }
  viewPrefsChanged(payload);   // a .tcm may have brought presentation state
  renderGraph();
  renderNodePanel();
  renderModelPanel();
  renderScenarioPicker();
  renderRefBadge();
  renderUndo();
  $("btn-close").classList.remove("hidden");
  $("file-chip").textContent = payload.path
    ? payload.path.split("/").pop() + (payload.dirty ? " •" : "") : "";
  $("file-chip").title = payload.path || "";
  dockModelChanged();          // the live JSON dock follows every model change
  return payload;
}

async function refreshGraph() {
  let payload;
  try {
    payload = await api("/api/graph");
  } catch {
    return;              // no model open — the only expected failure here
  }
  // deliberately not caught: a render error is a bug, and swallowing it left
  // a half-drawn page with nothing in the console to explain it
  updateGraph(payload);
}

/* --------------------------------------------------------- open / save */

/* The file browser starts where you were last time rather than at your home
   folder — a model deep under /Volumes took half a dozen clicks to reach, on
   every single open. */
const LAST_DIR_KEY = "pywr_reader_lastdir";
function lastDir() {
  try { return localStorage.getItem(LAST_DIR_KEY) || "~"; } catch { return "~"; }
}
function rememberDir(dir) {
  try { if (dir) localStorage.setItem(LAST_DIR_KEY, dir); } catch { /* ignore */ }
}
/** The folder a path sits in — for remembering where a file was picked. */
const dirOf = path => (path || "").replace(/[\\/][^\\/]*$/, "");

function openFileModal() {
  let curPath = null;
  const pathbox = el("input", { class: "pathbox mono", type: "text",
    placeholder: "full path to a model .json, .tcm or nodes.csv" });
  const list = el("div", { class: "browser-list" }, el("div", { class: "entry" }, "loading…"));
  const crumbs = el("div", { class: "mono muted small" });
  const rootsRow = el("div", { class: "row gap" });   // filled by the server

  async function browse(dir) {
    try {
      const data = await api("/api/browse?path=" + encodeURIComponent(dir));
      curPath = data.path;
      rememberDir(curPath);
      crumbs.textContent = data.path;
      // the server names the shortcuts for its own platform — drive letters on
      // Windows, /Volumes on a Mac — so nothing here has to guess
      rootsRow.replaceChildren(...(data.roots || []).map(root =>
        el("button", { class: "tiny", onclick: () => browse(root.path) },
          root.label)));
      list.replaceChildren(
        // no ".." at a filesystem root
        ...(data.parent
          ? [el("div", { class: "entry", onclick: () => browse(data.parent) },
              "📁 ..")] : []),
        ...data.entries.map(entry => el("div", {
          class: "entry",
          // entry.path is joined server-side, with that platform's separator
          onclick: () => {
            if (entry.kind === "dir") browse(entry.path);
            else pathbox.value = entry.path;
          },
          ondblclick: () => { if (entry.kind !== "dir") doOpen(); },
        },
          (entry.kind === "dir" ? "📁 " : "📄 ") + entry.name,
          entry.kind === "file"
            ? el("span", { class: "sz" }, (entry.size / 1024).toFixed(0) + " kB") : null,
        )));
    } catch (err) { toast(err.message, true); }
  }

  async function doOpen() {
    const path = pathbox.value.trim();
    if (!path) return;
    // a .tcm lands on the open model as positions and leaves it in place, so
    // only a real model swap is worth guarding
    const isTcm = /\.tcm$/i.test(path);
    if (!(isTcm && S.graph) && !await okToDiscard("Opening another model")) return;
    closeModal();
    await openPath(path);
  }

  openModal(
    el("h3", {}, "Open model"),
    // the crumb goes on its own line, not beside the shortcuts: inline it
    // reads as another drive button — on Windows "Home" is C:\Users\<name>,
    // so the row looked like Home / C:\ / D:\ / <your name>
    rootsRow,
    el("div", { class: "row gap browse-here" }, el("span", {}, "In"), crumbs),
    list,
    pathbox,
    el("div", { class: "row gap", style: "margin-top:10px; justify-content:flex-end" },
      el("button", { onclick: closeModal }, "Cancel"),
      el("button", { class: "primary", onclick: doOpen }, "Open")),
  );
  browse(lastDir());
  pathbox.addEventListener("keydown", e => { if (e.key === "Enter") doOpen(); });
}

/** Open a path and settle the whole app around it. Shared by the Open dialog
 *  and the example button so they cannot drift apart — the example button used
 *  to skip the run/results reset the dialog did. */
async function openPath(path, opts = {}) {
  let payload;
  try {
    payload = await api("/api/open", { path, ...opts });
  } catch (err) { toast(err.message, true); return null; }

  // A .tcm applied to the open model leaves that model in place — so the runs
  // and results still describe what is on screen and must survive. The server
  // says which of the two happened; the path cannot tell us, because a .tcm
  // opened as a model reports the model file it names, not itself.
  if (!payload.tcm_applied) resetForNewModel();
  rememberDir(dirOf(path));
  updateGraph(payload);
  // a .tcm that saved a camera opens where the user left off; everything
  // else gets the usual fit
  if (!restoreSavedView(applyView)) fitView();
  if (payload.layout_was_auto) {
    toast("No usable positions in the file — automatic layout applied");
  }
  (payload.warnings || []).forEach(w => toast(w));
  loadBgForModel();
  const missing = (payload.data && payload.data.missing) || [];
  if (missing.length) {
    toast(`${missing.length} data file(s) not found (${missing.join(", ")}) — `
      + "add their folder in the Model → Data files section to run.", true);
  }
  // Nothing in the .tcm matched, so it almost certainly belongs to a different
  // model. Offer to open that one instead of leaving "did not match" as the
  // whole story — before this there was no way to get there at all without
  // restarting the app.
  if (payload.tcm_unmatched) {
    const go = await confirmAsk("That .tcm describes a different model",
      "None of its node names are in the model you have open. Open the model "
      + "the .tcm itself describes instead?",
      { confirmLabel: "Open it as a model" });
    if (go && await okToDiscard("Opening the .tcm as a model")) {
      return openPath(path, { as_model: true });
    }
  }
  return payload;
}

function newModelModal() {
  const nameBox = el("input", { type: "text", value: "Traced model",
    style: "width:100%" });
  const loadImg = el("input", { type: "checkbox" });
  loadImg.checked = true;
  async function create() {
    if (!await okToDiscard("Starting a new model")) return;
    try {
      const payload = await api("/api/new", { title: nameBox.value.trim() });
      closeModal();
      resetForNewModel();
      updateGraph(payload);
      S.view = { x: 60, y: 60, k: 1 }; applyView();
      loadBgForModel();
      setMode("select");
      toast("Empty model created — load a trace image or start adding nodes");
      if (loadImg.checked) $("trace-file").click();
    } catch (err) { toast(err.message, true); }
  }
  openModal(
    el("h3", {}, "New model"),
    el("div", { class: "stack" },
      el("label", {}, "Title ", nameBox),
      el("label", { class: "row gap" }, loadImg,
        el("span", {}, "Load a map / schematic image to trace over")),
      el("p", { class: "muted small" },
        "Starts a blank pywr model. Trace a network by placing nodes over the "
        + "image (+ Node) and connecting them (+ Edge), or build one from scratch.")),
    el("div", { class: "row gap", style: "margin-top:12px; justify-content:flex-end" },
      el("button", { onclick: closeModal }, "Cancel"),
      el("button", { class: "primary", onclick: create }, "Create")),
  );
  nameBox.focus(); nameBox.select();
  nameBox.addEventListener("keydown", e => { if (e.key === "Enter") create(); });
}

/* Save As pre-fills the current path, which is convenient right up until a
   reflex Enter overwrites the file you meant to copy. So the suggestion is a
   copy's name, and anything that would land on an existing file asks first. */
function saveAsSuggestion() {
  const current = (S.graph && S.graph.path) || "";
  if (!current) return "";
  return current.replace(/(\.json)?$/i, "") + "-copy.json";
}

async function pathExists(path) {
  try {
    return (await api("/api/path/exists?path=" + encodeURIComponent(path))).exists;
  } catch { return false; }     // let the save itself report a real problem
}

function saveAsModal() {
  const pathbox = el("input", { class: "pathbox mono", type: "text",
    value: saveAsSuggestion() });
  async function doSave() {
    const path = pathbox.value.trim();
    if (!path) return;
    if (await pathExists(path)) {
      const go = await confirmAsk("Overwrite that file?",
        `${path.split(/[\\/]/).pop()} already exists.`,
        { confirmLabel: "Overwrite", danger: true });
      if (!go) return;
    }
    const prevKey = bgKey();
    try {
      const res = await api("/api/save", { path });
      closeModal();
      toast("Saved " + res.path);
      rememberDir(dirOf(res.path));
      await refreshGraph();
      rehomeBgAfterSave(prevKey);
      // data files are found relative to the model's folder, so saving
      // elsewhere can quietly cut them loose — the server says when it did
      if (res.data_note) toast(res.data_note, true);
    } catch (err) { toast(err.message, true); }
  }
  openModal(
    el("h3", {}, "Save model as"),
    pathbox,
    el("p", { class: "muted small" },
      "Positions are stored in each node as position.schematic — the file stays a valid pywr model."),
    el("p", { class: "muted small" },
      "Data files are looked for beside the model, so a copy saved elsewhere "
      + "may not find them — you'll be told if that happens."),
    el("div", { class: "row gap", style: "justify-content:flex-end" },
      el("button", { onclick: closeModal }, "Cancel"),
      el("button", { class: "primary", onclick: doSave }, "Save")),
  );
  pathbox.focus();
  // select the stem only: the folder is usually right, the name is what changes
  const stem = pathbox.value.lastIndexOf("/") + 1 || pathbox.value.lastIndexOf("\\") + 1;
  pathbox.setSelectionRange(stem, pathbox.value.length - ".json".length);
  pathbox.addEventListener("keydown", e => { if (e.key === "Enter") doSave(); });
}

/* --------------------------------------------------------- add node UI */
function openAddNodeModal(pos) {
  const nameBox = el("input", { type: "text",
    value: suggestName($("add-node-type").value) });
  const typeSel = el("select", {},
    ...NODE_TYPES.map(t => el("option", t === $("add-node-type").value ? { selected: "" } : {}, t)));
  openModal(
    el("h3", {}, "Add node"),
    el("div", { class: "stack" },
      el("label", {}, "Name ", nameBox),
      el("label", {}, "Type ", typeSel)),
    el("div", { class: "row gap", style: "margin-top:12px; justify-content:flex-end" },
      el("button", { onclick: closeModal }, "Cancel"),
      el("button", {
        class: "primary",
        onclick: async () => {
          try {
            const payload = await api("/api/node/add", {
              node: { name: nameBox.value.trim(), type: typeSel.value }, pos });
            closeModal();
            updateGraph(payload);
            selectNode(nameBox.value.trim());
            toast("Node added — set its parameters in the panel");
          } catch (err) { toast(err.message, true); }
        },
      }, "Add")),
  );
  nameBox.focus(); nameBox.select();
}
function suggestName(type) {
  let i = 1;
  while (S.nodeIdx.has(`${type}_${i}`)) i++;
  return `${type}_${i}`;
}

/* --------------------------------------------------------- node panel */
function renderNodePanel() {
  const pane = $("tab-node");
  if (!S.sel) {
    pane.replaceChildren(el("p", { class: "muted" },
      "Select a node or edge on the canvas. Drag nodes to move them; " +
      "scroll to zoom; drag the background to pan."));
    return;
  }
  if (S.sel.kind === "edge") return renderEdgePanel(pane);

  const name = S.sel.name;
  const node = S.nodeIdx.get(name);
  if (!node) { pane.replaceChildren(); return; }
  const style = typeStyle(node.type);

  const nameBox = el("input", { type: "text", value: name, style: "width:100%" });
  nameBox.addEventListener("keydown", e => { if (e.key === "Enter") doRename(); });
  async function doRename() {
    if (nameBox.value.trim() === name) return;
    try {
      const payload = await api("/api/node/rename", { old: name, new: nameBox.value.trim() });
      updateGraph(payload);
      selectNode(nameBox.value.trim());
      (payload.notes || []).slice(0, 3).forEach(n => toast(n));
    } catch (err) { toast(err.message, true); }
  }

  const typeBox = el("input", { type: "text", value: node.type, list: "types-dl" });
  typeBox.addEventListener("change", async () => {
    try {
      updateGraph(await api("/api/node/update",
        { name, changes: { type: typeBox.value.trim() } }));
      selectNode(name);
    } catch (err) { toast(err.message, true); }
  });

  const paramsTable = el("table", { class: "kv" });
  for (const [key, val] of Object.entries(node.params)) {
    paramsTable.append(paramRow(name, key, val));
  }
  const newKey = el("input", { type: "text", placeholder: "parameter", style: "width:100%" });
  const newVal = el("input", { type: "text", placeholder: "value", class: "vedit" });
  paramsTable.append(el("tr", {},
    el("td", { class: "k" }, newKey),
    el("td", {}, newVal),
    el("td", { class: "rowbtns" }, el("button", {
      class: "tiny", title: "Add parameter",
      onclick: () => {
        const key = newKey.value.trim();
        if (!key) return;
        saveParam(name, key, newVal.value);
      },
    }, "+")),
  ));

  const traceBtns = el("div", { class: "row gap", style: "flex-wrap:wrap" },
    ...[["both", "Both"], ["up", "Upstream"], ["down", "Downstream"], ["off", "Off"]]
      .map(([mode, label]) => el("button", {
        class: "tiny mode" + (S.traceMode === mode ? " active" : ""),
        onclick: () => { S.traceMode = mode; refreshSelection(); renderNodePanel(); },
      }, label)));

  // setChildren, not replaceChildren: bundlesBlock returns null for a node
  // type no template fits, and the raw DOM call would print that as "null"
  setChildren(pane,
    el("div", { class: "pane-block" },
      el("div", { class: "props-title" }, name),
      el("span", { class: "type-badge", style: `background:${style.color}` },
        `${node.type} — ${style.label}`),
      el("div", { class: "muted small" },
        `${node.in_degree} inflow edge(s), ${node.out_degree} outflow edge(s)`)),
    el("div", { class: "pane-block" },
      el("h3", {}, "Highlight water path"),
      traceBtns,
      el("p", { class: "muted small", style: "margin:6px 0 0" },
        "Blue = upstream (where its water comes from), orange = downstream (where it goes).")),
    el("div", { class: "pane-block" },
      el("h3", {}, "Rename / type"),
      el("div", { class: "stack" },
        el("div", { class: "row gap" }, nameBox,
          el("button", { class: "tiny", onclick: doRename }, "Rename")),
        typeBox)),
    el("div", { class: "pane-block" },
      el("h3", {}, "Parameters"),
      paramsTable,
      el("p", { class: "muted small" },
        "Values are JSON — numbers, strings, or {…} parameter definitions. " +
        "Δ stages a what-if change without editing the model.")),
    bundlesBlock(node),
    recordersBlock(node),
    el("div", { class: "pane-block chart-area" }),
    el("div", { class: "pane-block" },
      el("button", { class: "danger", onclick: () => deleteNode(name) },
        "Delete node")),
  );

  if (!document.getElementById("types-dl")) {
    document.body.append(el("datalist", { id: "types-dl" },
      ...NODE_TYPES.map(t => el("option", { value: t }))));
  }
  renderNodeChart();
}

/** Delete a node, having first said what the delete will break.
 *
 *  The server already knew which recorders and parameters pointed at the node
 *  — but it only said so afterwards, in a toast, by which time the damage was
 *  done and the next run died with a bare KeyError. Asking first turns the
 *  same information into a decision. */
async function deleteNode(name) {
  let stranded = [], nEdges = 0;
  try {
    const res = await api("/api/node/refs?name=" + encodeURIComponent(name));
    stranded = res.stranded || [];
    nEdges = res.n_edges || 0;
  } catch { /* report what we can; the delete itself still warns */ }

  const edgeText = nEdges
    ? ` and its ${nEdges} edge${nEdges === 1 ? "" : "s"}`
    : "";
  const go = await ask({
    title: `Delete “${name}”${edgeText}?`,
    message: stranded.length === 1
      ? "One other part of the model still points at it. Deleting the node "
        + "leaves it pointing at a name nothing defines, and the model will "
        + "not run until you fix it:"
      : stranded.length
        ? `${stranded.length} other parts of the model still point at it. `
          + "Deleting the node leaves them pointing at a name nothing defines, "
          + "and the model will not run until you fix them:"
        : `Nothing else in the model refers to ${name}.`,
    items: stranded,
    buttons: [{ label: "Cancel", value: null },
              { label: "Delete", value: true, kind: "danger" }],
  });
  if (!go) return;
  try {
    const payload = await api("/api/node/delete", { name });
    (payload.delete_warnings || []).forEach(w => toast(w, true));
    updateGraph(payload);
    if (!(payload.delete_warnings || []).length) toast(`Deleted ${name}`);
  } catch (err) { toast(err.message, true); }
}

/* ------------------------------------------------ recorders on this node */
/** What pywr will write down about this node when the model runs. Real models
 *  put the same handful on every demand centre, so these are one click each
 *  rather than a block of hand-written JSON. */
function recordersBlock(node) {
  const have = node.recorders || [];
  const taken = new Set((S.graph.nodes || []).flatMap(n =>
    (n.recorders || []).map(r => r.name)));
  const templates = recordersFor(node.type);

  const addRecorders = async entries => {
    if (!entries.length) return toast("Already recording all of those");
    try {
      const payload = await api("/api/definition/add", { entries });
      updateGraph(payload);
      selectNode(node.name);
      const n = (payload.added || []).length;
      toast(`Added ${n === 1 ? payload.added[0] : n + " recorders"}`
        + " — Save to write it to the file");
    } catch (err) { toast(err.message, true); }
  };
  const entryFor = template => ({
    section: "recorders",
    name: suggestRecorderName(template, node.name, taken),
    definition: recorderDef(template, node.name),
  });

  const list = have.length
    ? el("div", { class: "stack rec-list" }, ...have.map(rec =>
        el("div", { class: "row gap rec-row" },
          el("div", { class: "rec-name" }, rec.name,
            el("div", { class: "muted small mono" }, rec.type)),
          el("button", {
            class: "tiny", title: "Remove this recorder",
            onclick: async () => {
              try {
                const payload = await api("/api/definition/delete",
                  { section: "recorders", name: rec.name });
                updateGraph(payload);
                selectNode(node.name);
                (payload.delete_warnings || []).forEach(w => toast(w, true));
              } catch (err) { toast(err.message, true); }
            },
          }, "✕"))))
    : el("p", { class: "muted small" },
        "Nothing is recorded here yet. A run still charts this node — these "
        + "are the series pywr writes into the results.");

  const missingStandard = templates.filter(t =>
    t.standard && !taken.has(node.name + t.suffix));
  return el("div", { class: "pane-block" },
    el("h3", {}, "Recorders"),
    list,
    el("div", { class: "row gap add-recorders" },
      ...templates.map(t => el("button", {
        class: "tiny", title: t.hint,
        onclick: () => addRecorders([entryFor(t)]),
      }, "+ " + t.label))),
    missingStandard.length
      ? el("button", { class: "tiny", style: "margin-top:6px",
          title: "Add the set a real model puts on every node like this: "
            + missingStandard.map(t => t.label).join(", "),
          onclick: () => addRecorders(missingStandard.map(entryFor)) },
        `+ record the usual things (${missingStandard.length})`)
      : null);
}

function paramRow(nodeName, key, val) {
  const isComplex = typeof val === "object" && val !== null;
  const input = el("input", {
    class: "vedit" + (isComplex ? " complex" : ""), type: "text",
    value: isComplex ? JSON.stringify(val) : String(val),
    title: isComplex ? "Nested parameter definition (JSON)" : "",
  });
  const commit = () => saveParam(nodeName, key, input.value);
  input.addEventListener("keydown", e => { if (e.key === "Enter") commit(); });
  input.addEventListener("blur", () => {
    const orig = isComplex ? JSON.stringify(val) : String(val);
    if (input.value !== orig) commit();
  });
  const btns = el("td", { class: "rowbtns" });
  if (!isComplex && !isNaN(parseFloat(val))) {
    btns.append(el("button", {
      class: "tiny", title: "Stage a what-if change for the next run",
      onclick: () => addWhatIf(nodeName, key, val),
    }, "Δ"));
  }
  btns.append(el("button", {
    class: "tiny", title: "Remove parameter",
    onclick: async () => {
      try {
        updateGraph(await api("/api/node/update", { name: nodeName, removals: [key] }));
        selectNode(nodeName);
      } catch (err) { toast(err.message, true); }
    },
  }, "✕"));
  return el("tr", {}, el("td", { class: "k" }, key), el("td", {}, input), btns);
}

function parseValue(text) {
  const s = text.trim();
  try { return JSON.parse(s); } catch { return s; }
}

async function saveParam(nodeName, key, rawText) {
  try {
    updateGraph(await api("/api/node/update",
      { name: nodeName, changes: { [key]: parseValue(rawText) } }));
    selectNode(nodeName);
  } catch (err) { toast(err.message, true); }
}

function renderEdgePanel(pane) {
  const { src, dst, idx } = S.sel;
  pane.replaceChildren(
    el("div", { class: "pane-block" },
      el("div", { class: "props-title" }, `${src} → ${dst}`),
      el("p", { class: "muted small" }, "Directed edge — water flows source → destination.")),
    el("div", { class: "pane-block edge-flow-box" }),
    el("div", { class: "pane-block" },
      el("button", {
        class: "danger",
        onclick: async () => {
          if (!await confirmAsk("Delete this edge?", `${src} → ${dst}`,
            { confirmLabel: "Delete", danger: true })) return;
          try {
            updateGraph(await api("/api/edge/delete", { src, dst }));
            toast("Edge deleted");
          } catch (err) { toast(err.message, true); }
        },
      }, "Delete edge")),
  );
  const box = pane.querySelector(".edge-flow-box");
  if (S.activeRun && S.activeRun.status === "done") {
    const frame = frameAt(S.t);
    if (frame) {
      const col = frame.edgeCols.get(edgeKey(src, dst));
      const val = col != null ? frame.edges[col] : null;
      const exact = col != null ? frame.edgeExact[col] : false;
      box.append(el("h3", {}, "Flow at current timestep"),
        el("div", { class: "props-title" }, fmt(val)),
        el("p", { class: "muted small" },
          exact ? "Exact per-edge flow from the model run."
                : "Estimated from the two endpoint node flows (split/junction)."));
    }
  }
}

/* --------------------------------------------------------- model panel */
function renderModelPanel() {
  const pane = $("tab-model");
  if (!S.graph) { pane.replaceChildren(el("p", { class: "muted" }, "No model open.")); return; }
  const g = S.graph;
  const ts = g.timestepper || {};
  // the key describes the canvas, so it follows the same View switches
  const entries = legendEntries();
  const legend = el("div", { class: "stack" },
    ...entries.map(item => el("div", { class: "row gap" + (item.hidden ? " legend-off" : "") },
      el("span", { class: "swatch", style:
        `display:inline-block;width:12px;height:12px;border-radius:${item.shape === "circle" ? "50%" : "3px"};background:${item.color};`
        + (item.shape === "diamond" ? "transform:rotate(45deg);" : "")
        + (item.opacity != null && item.opacity < 1 ? `opacity:${item.opacity};` : "") }),
      el("span", { class: "small" }, item.label),
      item.hidden ? el("span", { class: "muted small" }, "hidden") : null)),
    entries.length && entries[0].source
      ? el("p", { class: "muted small" }, "Colours are the .tcm's — untick "
          + "“Use the .tcm's colours” in View to go back to ours.")
      : null);
  pane.replaceChildren(
    el("div", { class: "pane-block" },
      el("h3", {}, g.metadata && g.metadata.title || "Untitled model"),
      el("p", { class: "muted small" }, (g.metadata && g.metadata.description) || ""),
      el("table", { class: "kv" },
        kvRow("file", g.path || "—"),
        kvRow("nodes", g.nodes.length),
        kvRow("edges", g.edges.length),
        kvRow("parameters", g.n_parameters),
        kvRow("recorders", g.n_recorders),
        kvRow("tables", g.n_tables),
        kvRow("scenarios", (g.scenario_dims || []).length
          ? `${g.scenario_dims.length} · ${g.n_combinations} combination${g.n_combinations > 1 ? "s" : ""}`
          : "none"),
        ...(g.scenario_dims || []).map(d =>
          kvRow("· " + d.name, `${d.size} members: ${d.ensemble_names.join(", ")}`)),
        kvRow("period", `${ts.start || "?"} → ${ts.end || "?"} (step ${ts.timestep || "?"})`),
      )),
    dataFilesBlock(g.data),
    referenceWarningsBlock(g.reference_warnings),
    el("div", { class: "pane-block" }, el("h3", {}, "Node colour legend"), legend),
    el("div", { class: "pane-block" },
      el("button", { class: "primary", onclick: openModelExplorer }, "Browse model"),
      " ",
      el("button", {
        onclick: async () => {
          try {
            const res = await api("/api/export_csv", {});
            toast("Exported: " + res.files.map(f => f.split("/").pop()).join(", "));
          } catch (err) { toast(err.message, true); }
        },
      }, "Export CSV pair")),
  );
}

/** Names the model points at but defines nowhere.
 *
 *  The server has always computed these, but only the JSON dock rendered them
 *  — so a user who never opened the dock never learned the model was broken,
 *  and found out when a run died inside pywr instead. This block and the
 *  toolbar badge put it where the damage is visible. */
function referenceWarningsBlock(warnings) {
  const list = warnings || [];
  const block = el("div", { class: "pane-block" },
    el("h3", {}, "Broken references"));
  if (!list.length) {
    block.append(el("p", { class: "muted small" },
      "Every name this model refers to is defined somewhere in it."));
    return block;
  }
  block.classList.add("warn-block");
  block.append(
    el("p", { class: "small" }, list.length === 1
      ? "1 reference points at a name the model does not define. The model "
        + "will not run until it is fixed:"
      : `${list.length} references point at names the model does not define. `
        + "The model will not run until they are fixed:"),
    el("ul", { class: "warn-list" },
      ...list.slice(0, 8).map(w => el("li", { class: "small" }, w)),
      list.length > 8
        ? el("li", { class: "muted small" }, `and ${list.length - 8} more`)
        : null));
  return block;
}

/** The same warnings as a count beside Run, so they are visible from anywhere
 *  rather than only from the Model tab. */
function renderRefBadge() {
  const badge = $("ref-badge");
  const list = (S.graph && S.graph.reference_warnings) || [];
  badge.classList.toggle("hidden", !list.length);
  if (!list.length) return;
  badge.textContent = `⚠ ${list.length}`;
  badge.title = list.length === 1
    ? "1 reference points at a name the model does not define — click for details"
    : `${list.length} references point at names the model does not define — click for details`;
  badge.onclick = () => {
    openModal(
      el("h3", {}, "Broken references"),
      el("p", { class: "muted small" },
        "These names are referred to but defined nowhere in the model. pywr "
        + "fails on the first one it reaches, so a run will not get far."),
      el("ul", { class: "warn-list" },
        ...list.map(w => el("li", { class: "small" }, w))),
      el("div", { class: "row gap", style: "justify-content:flex-end;margin-top:10px" },
        el("button", { onclick: closeModal }, "Close")));
  };
}

/* ----------------------------------------------- model explorer (modal) */
function dataFilesBlock(data) {
  const block = el("div", { class: "pane-block" }, el("h3", {}, "Data files"));
  const report = (data && data.report) || [];
  if (!report.length) {
    block.append(el("p", { class: "muted small" },
      "This model references no external data files."));
    return block;
  }
  const missing = (data && data.missing) || [];
  block.append(el("p", { class: "muted small" }, missing.length
    ? `${missing.length} of ${report.length} data file(s) not found — the model can’t run until located.`
    : `All ${report.length} referenced data file(s) located.`));

  const table = el("table", { class: "kv" });
  for (const item of report) {
    const ok = !!item.resolved;
    table.append(el("tr", {},
      el("td", { class: "k", title: (item.urls || []).join("\n") },
        item.basename),
      el("td", {},
        el("span", { class: "status " + (ok ? "done" : "failed"),
          title: item.resolved || "not found" },
          ok ? "✓ " + (item.source || "found") : "✗ missing")),
      el("td", {}, ok ? el("button", {
        class: "tiny", title: "Look inside this data file",
        onclick: () => dataViewer(item.resolved, item.basename),
      }, "view") : null)));
  }
  block.append(table);

  // add / remove data search folders
  const dirs = (data && data.dirs) || [];
  const dirList = el("div", { class: "stack", style: "margin-top:6px" },
    ...dirs.map(d => el("div", { class: "row gap" },
      el("span", { class: "path mono small", title: d,
        style: "flex:1;overflow:hidden;text-overflow:ellipsis" }, d),
      el("button", { class: "tiny", onclick: async () => {
        updateGraph(await api("/api/data/dirs", { remove: d })
          .then(() => api("/api/graph")));
        renderModelPanel();
      } }, "✕"))));
  block.append(dirList);

  if (missing.length) {
    block.append(el("button", {
      class: "tiny", style: "margin-top:6px",
      onclick: () => pickDataDir(),
    }, "+ Add data folder…"));
  }
  return block;
}

function pickDataDir() {
  let curPath = null;
  const crumbs = el("div", { class: "mono muted small" });
  const list = el("div", { class: "browser-list" });
  const rootsRow = el("div", { class: "row gap" });   // filled by the server
  async function browse(dir) {
    const data = await api("/api/browse?path=" + encodeURIComponent(dir));
    curPath = data.path;
    crumbs.textContent = data.path;
    rootsRow.replaceChildren(...(data.roots || []).map(root =>
      el("button", { class: "tiny", onclick: () => browse(root.path) },
        root.label)));
    list.replaceChildren(
      ...(data.parent
        ? [el("div", { class: "entry", onclick: () => browse(data.parent) },
            "📁 ..")] : []),
      ...data.entries.filter(e => e.kind === "dir").map(entry =>
        el("div", { class: "entry", onclick: () => browse(entry.path) },
          "📁 " + entry.name)));
  }
  openModal(
    el("h3", {}, "Add a folder to search for data files"),
    el("p", { class: "muted small" },
      "Pick the folder (or a parent of it) that contains the model’s .xlsx / .csv / .h5 data."),
    rootsRow,
    el("div", { class: "row gap browse-here" }, el("span", {}, "In"), crumbs),
    list,
    el("div", { class: "row gap", style: "justify-content:flex-end;margin-top:10px" },
      el("button", { onclick: closeModal }, "Cancel"),
      el("button", { class: "primary", onclick: async () => {
        try {
          await api("/api/data/dirs", { directory: curPath });
          closeModal();
          updateGraph(await api("/api/graph"));
          renderModelPanel();
          toast("Added data folder — re-checking data files");
        } catch (err) { toast(err.message, true); }
      } }, "Use this folder")),
  );
  browse(lastDir());
}
const kvRow = (k, v) => el("tr", {}, el("td", { class: "k" }, k), el("td", {}, String(v)));

/* ---------------------------------------------------------- what-if */
function addWhatIf(node, key, current) {
  if (!S.whatif.some(w => w.node === node && w.key === key)) {
    S.whatif.push({ node, key, value: String(current) });
  }
  renderWhatIf();
  setTab("runs");
  toast(`Staged what-if: ${node}.${key}`);
}

function renderWhatIf() {
  const list = $("whatif-list");
  list.replaceChildren(...S.whatif.map((w, i) => el("div", { class: "whatif-item" },
    el("span", { class: "path mono", title: `${w.node}.${w.key}` }, `${w.node}.${w.key}`),
    el("input", {
      type: "text", value: w.value,
      onchange: e => { w.value = e.target.value; },
    }),
    el("button", {
      class: "tiny", onclick: () => { S.whatif.splice(i, 1); renderWhatIf(); },
    }, "✕"))));
  const count = $("whatif-count");
  count.textContent = S.whatif.length;
  count.classList.toggle("hidden", !S.whatif.length);
  $("btn-run-whatif").disabled = !S.whatif.length;
}

function whatifOverrides() {
  const nodes = {};
  for (const w of S.whatif) {
    nodes[w.node] = nodes[w.node] || {};
    nodes[w.node][w.key] = parseValue(w.value);
  }
  return { nodes };
}

/* ----------------------------------------------------------- scenarios */
// Flat combination index from the per-dimension selection, C-order (last
// scenario varies fastest — matches pywr's ScenarioCollection).
function currentScenarioIndex() {
  const dims = (S.graph && S.graph.scenario_dims) || [];
  let idx = 0;
  for (let i = 0; i < dims.length; i++) {
    const sel = Math.max(0, Math.min(S.scenarioSel[i] || 0, dims[i].size - 1));
    idx = idx * dims[i].size + sel;
  }
  return idx;
}

// Human label for a flat combination index (mirror of graphops.combo_label).
function comboLabelJS(dims, index) {
  if (!dims || !dims.length) return "base";
  const coords = [];
  let rem = index;
  for (let i = dims.length - 1; i >= 0; i--) {
    coords[i] = rem % dims[i].size;
    rem = Math.floor(rem / dims[i].size);
  }
  return dims.map((d, i) => `${d.name}=${d.ensemble_names[coords[i]]}`).join(", ");
}

function renderScenarioPicker() {
  const block = $("scenario-block");
  const dims = (S.graph && S.graph.scenario_dims) || [];
  if (!dims.length) { block.classList.add("hidden"); return; }
  block.classList.remove("hidden");
  const info = el("p", { class: "muted small", id: "scenario-info" });
  const refreshInfo = () => {
    const n = (S.graph && S.graph.n_combinations) || 1;
    info.textContent = `Showing combination ${currentScenarioIndex() + 1} of ${n}`
      + ` — ${comboLabelJS(dims, currentScenarioIndex())}`;
  };
  $("scenario-picker").replaceChildren(...dims.map((d, i) => {
    const sel = el("select", {
      title: `${d.name} — ${d.size} member${d.size > 1 ? "s" : ""}`,
      onchange: e => { S.scenarioSel[i] = +e.target.value; refreshInfo(); },
    }, ...d.ensemble_names.map((nm, j) => {
      const o = el("option", { value: j }, nm);
      if ((S.scenarioSel[i] || 0) === j) o.selected = true;
      return o;
    }));
    return el("label", { class: "scenario-row" },
      el("span", { class: "mono small" }, d.name), sel);
  }), info);
  refreshInfo();
}

/* ------------------------------------------------------------- runs */
/** Ask the browser to download a URL (the server sets the filename). */
function download(url) {
  const a = el("a", { href: url, download: "" });
  document.body.append(a);
  a.click();
  a.remove();
}

/** Write a run beside the model. Runs live in memory, so this is the only
 *  thing that makes one outlive the app. */
async function saveRun(run) {
  try {
    const res = await api(`/api/run/${run.id}/save`, {});
    toast(`Saved ${res.path.split(/[\\/]/).pop()} beside the model`);
  } catch (err) { toast(err.message, true); }
}

/** Load a run saved earlier, straight back into the runs list. */
function openRunModal() {
  const box = el("input", { class: "pathbox mono", type: "text",
    placeholder: "full path to a .pywrrun.json saved earlier" });
  const go = async () => {
    const path = box.value.trim();
    if (!path) return;
    try {
      const res = await api("/api/run/open", { path });
      closeModal();
      await refreshRuns();
      await activateRun(res.run_id);
      toast("Run loaded");
    } catch (err) { toast(err.message, true); }
  };
  openModal(
    el("h3", {}, "Open a saved run"),
    el("p", { class: "muted small" },
      "A run saved with the run list's save button — written beside the "
      + "model as <model>.<run>.pywrrun.json."),
    box,
    el("div", { class: "row gap", style: "margin-top:10px;justify-content:flex-end" },
      el("button", { onclick: closeModal }, "Cancel"),
      el("button", { class: "primary", onclick: go }, "Open")));
  box.addEventListener("keydown", e => { if (e.key === "Enter") go(); });
  box.focus();
}

function resetRunsState() {
  S.activeRun = null; S.frames.clear(); S.frameReq.clear();
  S.compare.clear(); S.seriesCache.clear(); S.t = 0;
  S.runs = [];
  stopPlay();
  $("timebar").classList.add("hidden");
  renderRuns();
}

/** Everything the old model owned, let go of at once.
 *
 *  A run, a pinned chart, a staged what-if and a search hit all name nodes in
 *  the model that produced them. Carrying any of them into a different model
 *  doesn't just look untidy — activating a stale run drove the time slider
 *  with the wrong calendar, and an empty model went on plotting the previous
 *  one. The server drops its run store on the same event (api/files.py), so
 *  this is the client half of one rule: results belong to a model. */
function resetForNewModel() {
  resetRunsState();
  resultsReset();
  S.whatif = [];
  renderWhatIf();
  S.sel = null;
  searchInput.value = "";
  searchMatches = [];
  searchResults.classList.add("hidden");
}

/** Ask before throwing away unsaved edits. Resolves true to go ahead.
 *
 *  The file chip's dot was the only sign a model was dirty, and opening
 *  another one took the edits with it without a word. */
async function okToDiscard(what) {
  if (!S.graph || !S.graph.dirty) return true;
  const name = (S.graph.path || "").split(/[\\/]/).pop() || "this model";
  const answer = await ask({
    title: "Unsaved changes",
    message: `${name} has changes that have not been saved. ${what} will `
      + "discard them.",
    buttons: [
      { label: "Cancel", value: "cancel" },
      { label: "Discard changes", value: "discard", kind: "danger" },
      { label: "Save first", value: "save", kind: "primary" },
    ],
  });
  if (answer === "save") {
    if (!S.graph.path) { saveAsModal(); return false; }
    try {
      const res = await api("/api/save", {});
      toast("Saved " + res.path);
      await refreshGraph();
      return true;
    } catch (err) { toast(err.message, true); return false; }
  }
  return answer === "discard";
}

async function startRun(overrides, label, scenarioIndex) {
  if (!S.graph) { toast("Open a model first", true); return; }
  if (!S.env || !S.env.ready) { envModal(); return; }
  try {
    const body = { overrides, label };
    if (scenarioIndex) body.scenario_index = scenarioIndex;
    const res = await api("/api/run", body);
    toast("Run started…");
    setTab("runs");
    pollRun(res.run_id);
  } catch (err) { toast(err.message, true); }
}

async function pollRun(runId) {
  await refreshRuns();
  const timer = setInterval(async () => {
    try {
      const st = await api("/api/run/" + runId);
      // redraw while it runs too — that is what shows the progress bar moving
      if (st.status === "running" || st.status === "queued") await refreshRuns();
      if (st.status === "done" || st.status === "failed") {
        clearInterval(timer);
        await refreshRuns();
        if (st.status === "done") {
          const warns = st.warnings || [];
          toast(`Run finished — ${st.n_steps} timesteps`
            + (warns.length ? ` (${warns.length} warning${warns.length > 1 ? "s" : ""})` : ""));
          if (warns.length) toast("pywr note: " + warns[0], true);
          activateRun(runId);
        } else {
          toast("Run failed — see the Runs tab", true);
        }
      }
    } catch { clearInterval(timer); }
  }, 1200);
}

async function refreshRuns() {
  try { S.runs = (await api("/api/runs")).runs; } catch { S.runs = []; }
  renderRuns();
}

/** "4m 12s" — elapsed wall-clock for a run, from the server's own clock. */
function elapsed(run) {
  const from = run.started_at;
  if (!from) return "";
  const secs = Math.max(0, Math.round((run.finished_at || Date.now() / 1000) - from));
  return secs < 60 ? `${secs}s`
    : `${Math.floor(secs / 60)}m ${String(secs % 60).padStart(2, "0")}s`;
}

/** How far a running run has got. pywr gives no progress callback, so the
 *  runner counts timesteps through a recorder and the server reads the count;
 *  an 80-year run used to show a spinner and nothing else for a minute. */
function progressLine(run) {
  const p = run.progress;
  const time = elapsed(run);
  if (!p || !p.total) {
    return el("span", { class: "muted small run-progress" },
      time ? `solving… ${time}` : "solving…");
  }
  const frac = Math.max(0, Math.min(1, p.step / p.total));
  const left = frac > 0.02 && p.step < p.total
    ? ` · ~${Math.round(((run.finished_at || Date.now() / 1000) - run.started_at)
        * (1 / frac - 1))}s left`
    : "";
  return el("span", { class: "run-progress" },
    el("span", { class: "run-bar" },
      el("span", { class: "run-bar-fill", style: `width:${(frac * 100).toFixed(1)}%` })),
    el("span", { class: "muted small" },
      `${Math.round(frac * 100)}% · ${p.step.toLocaleString()} of `
      + `${p.total.toLocaleString()} steps · ${time}${left}`));
}

function renderRuns() {
  const list = $("runs-list");
  if (!S.runs.length) {
    list.replaceChildren(el("p", { class: "muted small" },
      "No runs yet. PyWR solves the network for every timestep; results appear here."));
    return;
  }
  list.replaceChildren(...S.runs.map(run => {
    const isActive = S.activeRun && S.activeRun.id === run.id;
    const item = el("div", { class: "run-item" + (isActive ? " active" : "") },
      el("input", {
        type: "radio", name: "active-run", title: "Show this run on the canvas",
        ...(isActive ? { checked: "" } : {}),
        onchange: () => activateRun(run.id),
        ...(run.status !== "done" ? { disabled: "" } : {}),
      }),
      el("span", { class: "lbl" },
        el("span", {}, run.label + (run.overrides ? " Δ" : "")),
        el("span", { class: "muted small" },
          (run.scenario_label ? ` · ${run.scenario_label}` : "")
          + (run.n_steps ? ` · ${run.n_steps} steps` : "")
          + (run.status === "done" && elapsed(run) ? ` · ${elapsed(run)}` : "")),
        run.status === "running" || run.status === "queued"
          ? progressLine(run) : null),
      el("span", { class: "status " + run.status }, run.status),
    );
    // the whole row activates the run. Before this only the radio did, so a
    // reloaded page showed the runs but nothing on the canvas until you found
    // a 13px target.
    if (run.status === "done") {
      item.classList.add("clickable");
      item.title = "Show this run on the canvas";
      item.addEventListener("click", e => {
        if (e.target.closest("button, input")) return;   // the row's own controls
        activateRun(run.id);
      });
    }
    if (run.status === "done") {
      if ((run.warnings || []).length) {
        item.append(el("span", {
          class: "warn-badge",
          title: "pywr reported warnings — click to view",
          onclick: (e) => {
            e.stopPropagation();
            openModal(el("h3", {}, "pywr run warnings"),
              el("p", { class: "muted small" },
                "The model ran and produced results, but pywr reported:"),
              el("ul", { class: "warn-list" },
                ...run.warnings.map(w => el("li", {}, w))),
              el("div", { class: "row gap", style: "justify-content:flex-end;margin-top:10px" },
                el("button", { onclick: closeModal }, "Close")));
          },
        }, `⚠ ${run.warnings.length}`));
      }
      item.append(
        el("button", {
          class: "tiny", title: "Download every node and edge series as CSV",
          onclick: e => { e.stopPropagation(); download(`/api/run/${run.id}/csv`); },
        }, "csv"),
        el("button", {
          class: "tiny",
          title: "Write this run beside the model so it survives a restart",
          onclick: e => { e.stopPropagation(); saveRun(run); },
        }, "save"));
      const cb = el("input", {
        type: "checkbox", title: "Overlay in the node chart",
        ...(S.compare.has(run.id) ? { checked: "" } : {}),
      });
      cb.addEventListener("change", () => {
        cb.checked ? S.compare.add(run.id) : S.compare.delete(run.id);
        renderNodeChart();
        resultsChanged();     // the dock overlays the same set
      });
      cb.addEventListener("click", e => e.stopPropagation());
      item.append(cb);
    }
    if (run.status === "failed") {
      item.classList.add("clickable");
      item.title = "Show why this run failed";
      item.append(el("button", {
        class: "tiny", title: "Show why this run failed",
        onclick: e => { e.stopPropagation(); showFailure(run.id); },
      }, "why?"));
      item.addEventListener("click", () => showFailure(run.id));
    }
    return item;
  }));
  // runs live only in memory — make that plain, and nudge to save
  const anyDone = S.runs.some(r => r.status === "done");
  list.append(el("p", { class: "muted small runs-note" }, anyDone
    ? "Runs are kept in memory only. Use save on a run to keep it past a "
      + "restart; Open run… reloads a saved one."
    : "Runs are kept in memory until the app closes."));
}

/** Why a run failed, in the app's words where it has them.
 *
 *  pywr reports a reference to something deleted as a bare KeyError from
 *  inside a .pyx file. The server matches that name against the model's own
 *  dangling-reference list, so the first thing shown is what actually broke —
 *  with the traceback still there underneath for when it isn't enough. */
async function showFailure(runId) {
  let st;
  try { st = await api("/api/run/" + runId); }
  catch (err) { return toast(err.message, true); }
  const hints = st.hints || [];
  openModal(
    el("h3", {}, "Run failed"),
    hints.length
      ? el("div", { class: "fail-hint" },
          el("p", {}, hints.length === 1 ? "What went wrong:"
                                         : "What went wrong (most likely):"),
          el("ul", { class: "warn-list" },
            ...hints.map(h => el("li", { class: "small" }, h))),
          el("p", { class: "muted small" },
            "The Model tab lists every broken reference."))
      : null,
    el("p", { class: "muted small" }, "pywr's own report:"),
    el("pre", { class: "log" }, (st.error || "") + "\n\n" + (st.traceback || "")),
    el("div", { class: "row gap", style: "justify-content:flex-end;margin-top:10px" },
      el("button", { onclick: closeModal }, "Close")));
}

async function activateRun(runId) {
  try {
    const st = await api("/api/run/" + runId);
    if (st.status !== "done") return;
    S.activeRun = st;
    S.frames.clear(); S.frameReq.clear();
    S.t = Math.min(S.t, st.n_steps - 1);
    S.compare.add(runId);
    const slider = $("time-slider");
    slider.max = st.n_steps - 1;
    slider.value = S.t;
    $("flow-max").textContent = "max " + fmt(st.max_edge_flow);
    $("timebar").classList.remove("hidden");
    renderRuns();
    await ensureBlock(blockOf(S.t));
    updateFrameVisuals();
    renderNodeChart();
    resultsChanged();          // results dock, if it's open
  } catch (err) { toast(err.message, true); }
}

/* ------------------------------------------------------- frames / time */
const blockOf = t => Math.floor(t / BLOCK) * BLOCK;

/* Key for the per-frame edge lookup. A NUL byte, because a node name can
   contain anything else — including the " -> " and " " a reader would reach
   for first, which would make "A B" -> "C" and "A" -> "B C" collide. */
const edgeKey = (src, dst) => src + "\0" + dst;

async function ensureBlock(start) {
  if (!S.activeRun || S.frames.has(start) || S.frameReq.has(start)) return;
  S.frameReq.add(start);
  try {
    const data = await api(`/api/run/${S.activeRun.id}/frames?start=${start}&count=${BLOCK}`);
    const edgeCols = new Map();
    data.edge_keys.forEach((k, i) => edgeCols.set(edgeKey(k[0], k[1]), i));
    const nodeCols = new Map();
    data.node_keys.forEach((k, i) => nodeCols.set(k, i));
    S.frames.set(start, { ...data, edgeCols, nodeCols });
  } finally { S.frameReq.delete(start); }
}

function frameAt(t) {
  const blk = S.frames.get(blockOf(t));
  if (!blk || t < blk.start || t >= blk.end) return null;
  const i = t - blk.start;
  return {
    date: blk.dates[i],
    edges: blk.edges[i],
    nodes: blk.nodes[i],
    edgeCols: blk.edgeCols,
    nodeCols: blk.nodeCols,
    edgeExact: blk.edge_keys.map(k => k[2]),
  };
}

function currentDate() {
  const f = frameAt(S.t);
  return f ? f.date : null;
}

function currentNodeValue(name) {
  const f = frameAt(S.t);
  if (!f) return null;
  const col = f.nodeCols.get(name);
  if (col == null) return null;
  const node = S.nodeIdx.get(name);
  const isStorage = node && /storage|reservoir/i.test(node.type) && !/virtual/i.test(node.type);
  return { value: f.nodes[col], kind: isStorage ? "volume" : "flow" };
}

// Flow numbers over each pipe at the current timestep — shown whether or not
// a node is selected, so magnitudes stay visible while a path is highlighted.
function updateEdgeLabels(frame) {
  if (frame === undefined) {
    const r = S.activeRun;
    frame = r && r.status === "done" ? frameAt(S.t) : null;
  }
  if (!S.graph) return;
  const on = !!frame && S.showEdgeValues;
  const set = S.labelEdges;
  const maxFlow = S.activeRun ? Math.max(S.activeRun.max_edge_flow, 1e-9) : 1;
  S.graph.edges.forEach((edge, idx) => {
    const lbl = edgeLabelEls[idx];
    if (!lbl) return;
    let val = null;
    if (on && set && set.has(idx)) {
      const col = frame.edgeCols.get(edge.src + "\0" + edge.dst);
      val = col != null ? frame.edges[col] : null;
    }
    const p = S.positions[edge.src], q = S.positions[edge.dst];
    if (val == null || val <= maxFlow * 1e-6 || !p || !q) {
      lbl.style.display = "none";
      return;
    }
    lbl.setAttribute("x", (p[0] + q[0]) / 2);
    lbl.setAttribute("y", (p[1] + q[1]) / 2 - 4);
    lbl.textContent = fmt(val);
    lbl.style.display = "";
  });
}

function updateFrameVisuals() {
  const run = S.activeRun;
  const frame = run && run.status === "done" ? frameAt(S.t) : null;
  $("time-date").textContent = frame ? frame.date : "—";
  $("time-idx").textContent = frame ? `t=${S.t}` : "";
  updateEdgeLabels(frame);
  updateChartCursor();
  syncTimeInputs();
  // a selected node's up/down-stream trace owns the edge stroke; the flow
  // labels above still show magnitudes, so we just skip re-colouring the lines
  if (S.sel && S.sel.kind === "node" && S.traceMode !== "off") return;
  if (!S.graph) return;
  const maxFlow = run ? Math.max(run.max_edge_flow, 1e-9) : 1;
  S.graph.edges.forEach((edge, idx) => {
    const els = edgeEls[idx];
    if (!els) return;
    els.line.classList.remove("dim");
    if (!frame) {
      els.line.style.stroke = "";
      els.line.style.strokeWidth = "";
      els.line.classList.remove("estimated");
      return;
    }
    const col = frame.edgeCols.get(edgeKey(edge.src, edge.dst));
    const val = col != null ? frame.edges[col] : null;
    els.line.classList.toggle("estimated", col != null && !frame.edgeExact[col]);
    if (val == null) {
      els.line.style.stroke = "var(--baseline)";
      els.line.style.strokeWidth = "1";
    } else if (val <= maxFlow * 1e-6) {
      els.line.style.stroke = "var(--baseline)";
      els.line.style.strokeWidth = "1";
    } else {
      const t = Math.sqrt(val / maxFlow);
      els.line.style.stroke = flowColor(t);
      els.line.style.strokeWidth = String(1 + 5.5 * t);
    }
  });
}

async function setT(t) {
  S.t = Math.max(0, Math.min(t, S.activeRun ? S.activeRun.n_steps - 1 : 0));
  $("time-slider").value = S.t;
  await ensureBlock(blockOf(S.t));
  // prefetch the next block near the boundary
  if (S.activeRun && S.t % BLOCK > BLOCK * 0.7) ensureBlock(blockOf(S.t) + BLOCK);
  updateFrameVisuals();
  if (S.sel && S.sel.kind === "edge") renderNodePanel();
}

/* Playback ran one timestep per 90 ms, full stop. That is 11 steps a second —
   fine for a year, but an 80-year daily run is 29,586 steps, so playing it
   through took over three quarters of an hour. The tick stays at 90 ms (any
   faster and the canvas can't keep up) and the speed multiplies the *stride*,
   which is what actually gets you across a long run. */
const PLAY_TICK_MS = 90;

function playStride() {
  const perSecond = +($("play-speed").value || 4);
  return Math.max(1, Math.round(perSecond * PLAY_TICK_MS / 1000));
}

function startPlay() {
  if (!S.activeRun) return;
  S.playing = true;
  $("btn-play").textContent = "❚❚";
  clearInterval(S.playTimer);
  S.playTimer = setInterval(() => {
    const last = S.activeRun.n_steps - 1;
    const next = S.t + playStride();
    setT(next > last ? 0 : next);
  }, PLAY_TICK_MS);
}
function stopPlay() {
  S.playing = false;
  $("btn-play").textContent = "▶";
  clearInterval(S.playTimer);
}

/* Jump to a date rather than aim at it: 29,586 steps across ~190 px of slider
   is about 155 days per pixel, so the slider alone cannot reach a given day.
   The run's dates are sorted, so a binary search lands on the nearest one. */
function stepForDate(iso) {
  const run = S.activeRun;
  if (!run || !run.date_first) return null;
  if (iso <= run.date_first) return 0;
  if (iso >= run.date_last) return run.n_steps - 1;
  // interpolate into the run, then walk to the exact step using the frames we
  // can fetch — a model may skip days (monthly steps), so the guess is refined
  const span = Date.parse(run.date_last) - Date.parse(run.date_first);
  const into = Date.parse(iso) - Date.parse(run.date_first);
  if (!(span > 0)) return 0;
  return Math.max(0, Math.min(run.n_steps - 1,
    Math.round((into / span) * (run.n_steps - 1))));
}

async function gotoDate(iso) {
  const guess = stepForDate(iso);
  if (guess == null) return;
  stopPlay();
  await setT(guess);
  // the interpolation assumes an even calendar; nudge onto the real step
  for (let i = 0; i < 40; i++) {
    const here = currentDate();
    if (!here || here === iso) break;
    const step = here < iso ? 1 : -1;
    const next = S.t + step;
    if (next < 0 || next >= S.activeRun.n_steps) break;
    // stop as soon as we straddle the target rather than oscillate around it
    await setT(next);
    const now = currentDate();
    if (!now || (step > 0 ? now > iso : now < iso)) break;
  }
  syncTimeInputs();
}

/** Keep the date box showing where the slider actually is. */
function syncTimeInputs() {
  const box = $("time-goto");
  const run = S.activeRun;
  if (!run) { box.value = ""; return; }
  box.min = run.date_first || "";
  box.max = run.date_last || "";
  const here = currentDate();
  if (here) box.value = here;
}

/* ------------------------------------------------------------- chart */
export async function getSeries(runId, node) {
  const key = runId + "|" + node;
  if (!S.seriesCache.has(key)) {
    S.seriesCache.set(key, await api(`/api/run/${runId}/series?node=${encodeURIComponent(node)}`));
  }
  return S.seriesCache.get(key);
}

async function renderNodeChart() {
  const box = document.querySelector("#tab-node .chart-area");
  if (!box || !S.sel || S.sel.kind !== "node") return;
  const name = S.sel.name;
  const runIds = S.runs.filter(r => r.status === "done" &&
    (S.compare.has(r.id) || (S.activeRun && S.activeRun.id === r.id))).map(r => r.id);
  if (!runIds.length) { box.replaceChildren(); return; }

  const seriesList = [];
  for (const [i, rid] of runIds.entries()) {
    try {
      const data = await getSeries(rid, name);
      const values = data.flow || data.volume;
      if (values) {
        seriesList.push({
          label: data.label, color: RUN_COLORS[i % RUN_COLORS.length],
          dates: data.dates, values, kind: data.flow ? "flow" : "volume",
        });
      }
    } catch { /* node may not exist in that run */ }
  }
  if (S.sel && S.sel.kind === "node" && S.sel.name === name) {
    const heading = el("h3", { class: "chart-head" },
      `${seriesList[0] ? seriesList[0].kind : "series"} over time`);
    if (seriesList.length) {
      // exports exactly the runs plotted here, one column each
      heading.append(el("button", {
        class: "tiny", title: "Download this node's series as CSV",
        onclick: () => download(`/api/run/${runIds[0]}/node.csv?node=`
          + encodeURIComponent(name) + "&compare=" + runIds.join(",")),
      }, "csv"));
    }
    box.replaceChildren(heading);
    if (!seriesList.length) {
      box.append(el("p", { class: "muted small" }, "No recorded series for this node."));
      return;
    }
    box.append(buildChart(seriesList));
    if (seriesList.length >= 2) {
      box.append(el("div", { class: "chart-legend" }, ...seriesList.map(s =>
        el("span", { class: "item" },
          el("span", { class: "swatch", style: `background:${s.color}` }),
          el("span", {}, s.label)))));
    }
  }
}

/* The run charts — the node panel's and the results dock's.
 *
 *  Drawn from a visible index window rather than the whole series, because the
 *  data viewer could already zoom and pan and these could not: an 80-year daily
 *  run drawn into 312 px is ~95 timesteps per pixel, which is a shape, not a
 *  reading. Scroll to zoom about the cursor, drag to pan, double-click to
 *  reset — the same gestures the data viewer uses.
 *
 *  `dash` on a series draws it dashed, which is how the results dock separates
 *  runs while keeping one colour per node. */
export function buildChart(seriesList, { width = 312, height = 170 } = {}) {
  // the defaults are the sidebar's size; the results dock passes its own
  const W = width, H = height, m = { l: 44, r: 10, t: 8, b: 22 };
  const iw = W - m.l - m.r, ih = H - m.t - m.b;
  const n = Math.max(...seriesList.map(s => s.values.length));
  const dates = seriesList[0].dates;

  // visible window, in series indices — [lo, hi] inclusive
  let lo = 0, hi = Math.max(0, n - 1);
  const MIN_SPAN = 4;          // never zoom past a handful of points

  const wrap = el("div", { class: "chart-box" });
  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, height: H });
  const reset = el("button", {
    class: "chart-reset hidden", title: "Show the whole series (or double-click)",
    onclick: e => { e.stopPropagation(); lo = 0; hi = n - 1; draw(); },
  }, "⤢ reset");

  const span = () => hi - lo;
  const X = i => m.l + ((i - lo) / Math.max(1, span())) * iw;
  const iAt = px => lo + ((px - m.l) / iw) * Math.max(1, span());

  let cursor = null, hover = null, tip = null;

  function draw() {
    svg.replaceChildren();
    reset.classList.toggle("hidden", lo === 0 && hi === n - 1);

    // y range over what is on screen, so zooming in actually resolves detail
    let vlo = Infinity, vhi = -Infinity;
    for (const s of seriesList) {
      for (let i = Math.max(0, lo); i <= Math.min(s.values.length - 1, hi); i++) {
        const v = s.values[i];
        if (v < vlo) vlo = v;
        if (v > vhi) vhi = v;
      }
    }
    if (!isFinite(vlo)) { vlo = 0; vhi = 1; }
    if (vlo > 0) vlo = 0;
    if (vhi === vlo) vhi = vlo + 1;
    const Y = v => m.t + ih - ((v - vlo) / (vhi - vlo)) * ih;

    // gridlines + y ticks
    const ticks = 4;
    for (let i = 0; i <= ticks; i++) {
      const v = vlo + ((vhi - vlo) * i) / ticks;
      const y = Y(v);
      svg.append(svgEl("line", { x1: m.l, x2: W - m.r, y1: y, y2: y,
        stroke: i === 0 ? "var(--baseline)" : "var(--grid)", "stroke-width": 1 }));
      const label = svgEl("text", { x: m.l - 6, y: y + 3, "text-anchor": "end",
        fill: "var(--muted)", "font-size": 9 });
      label.textContent = fmt(v);
      svg.append(label);
    }
    // x date ticks
    for (let i = 0; i < 4; i++) {
      const idx = Math.round(lo + (i / 3) * span());
      const label = svgEl("text", { x: X(idx), y: H - 6,
        "text-anchor": i === 0 ? "start" : i === 3 ? "end" : "middle",
        fill: "var(--muted)", "font-size": 9 });
      // zoomed in far enough to see individual days, show them
      label.textContent = (dates[idx] || "").slice(0, span() < 400 ? 10 : 7);
      svg.append(label);
    }
    // series lines — thinned to about one point per pixel of the window
    for (const s of seriesList) {
      let d = "";
      const from = Math.max(0, lo), to = Math.min(s.values.length - 1, hi);
      const step = Math.max(1, Math.floor((to - from + 1) / Math.max(1, iw * 2)));
      for (let i = from; i <= to; i += step) {
        d += (d ? "L" : "M") + X(i).toFixed(1) + "," + Y(s.values[i]).toFixed(1);
      }
      const attrs = { d, fill: "none", stroke: s.color, "stroke-width": 1.8,
                      "stroke-linejoin": "round" };
      if (s.dash) attrs["stroke-dasharray"] = s.dash;
      svg.append(svgEl("path", attrs));
    }
    // current-timestep cursor
    cursor = svgEl("line", { class: "t-cursor", y1: m.t, y2: m.t + ih,
      stroke: "var(--ink)", "stroke-dasharray": "3 3", "stroke-width": 1,
      opacity: 0.55 });
    svg.append(cursor);

    // hover crosshair + tooltip; click scrubs the timeline
    hover = svgEl("rect", { x: m.l, y: m.t, width: iw, height: ih,
      fill: "transparent", style: "cursor: crosshair" });
    hover.addEventListener("mousemove", e => {
      if (panning) return;
      const idx = Math.round(iAt(localX(e)));
      if (idx < lo || idx > hi || idx < 0 || idx >= n) return;
      if (!tip) { tip = el("div", { class: "chart-tip" }); document.body.append(tip); }
      tip.replaceChildren(el("div", { class: "d" }, dates[idx] || ""),
        ...seriesList.map(s => el("div", { class: "s" },
          el("span", { class: "sw", style: `background:${s.color}` }),
          el("span", {}, `${s.label}: ${fmt(s.values[idx])}`))));
      tip.style.left = Math.min(window.innerWidth - 220, e.clientX + 12) + "px";
      tip.style.top = (e.clientY + 12) + "px";
    });
    hover.addEventListener("mouseleave", dropTip);
    hover.addEventListener("click", e => {
      if (dragged) return;                 // a pan, not a click
      const idx = Math.round(iAt(localX(e)));
      if (idx >= 0 && idx < n) setT(idx);
    });
    svg.append(hover);
    placeCursor();
  }

  const dropTip = () => { if (tip) { tip.remove(); tip = null; } };
  /** Pointer x in the chart's own viewBox units, whatever it was scaled to. */
  const localX = e => {
    const rect = svg.getBoundingClientRect();
    return (e.clientX - rect.left) * (W / rect.width);
  };
  function placeCursor() {
    if (!cursor) return;
    const i = Math.min(S.t, n - 1);
    const on = i >= lo && i <= hi;
    cursor.style.display = on ? "" : "none";
    if (on) { cursor.setAttribute("x1", X(i)); cursor.setAttribute("x2", X(i)); }
  }

  // ---- zoom about the cursor, pan by dragging
  svg.addEventListener("wheel", e => {
    // A chart already showing everything cannot zoom out further, so let that
    // scroll through to the panel instead of swallowing it — otherwise the
    // chart becomes a hole you cannot scroll past.
    const atFullExtent = lo === 0 && hi === n - 1;
    if (atFullExtent && e.deltaY > 0) return;
    e.preventDefault();
    dropTip();
    const at = iAt(localX(e));
    const factor = Math.exp(e.deltaY * 0.0015);
    let width2 = Math.min(n - 1, Math.max(MIN_SPAN, span() * factor));
    let lo2 = at - (at - lo) * (width2 / Math.max(1, span()));
    lo2 = Math.max(0, Math.min(n - 1 - width2, lo2));
    lo = Math.round(lo2);
    hi = Math.round(lo2 + width2);
    draw();
  }, { passive: false });

  // The window listeners live only for the length of a drag. A chart is
  // rebuilt on every selection change, so leaving them attached would pile up
  // a dead listener per chart for the life of the page.
  let panning = null, dragged = false;
  const onMove = e => {
    if (!panning) return;
    const rect = svg.getBoundingClientRect();
    const byIdx = ((panning.x - e.clientX) * (W / rect.width) / iw)
      * (panning.hi - panning.lo);
    if (Math.abs(e.clientX - panning.x) > 3) { dragged = true; dropTip(); }
    const width2 = panning.hi - panning.lo;
    const lo2 = Math.max(0, Math.min(n - 1 - width2, panning.lo + byIdx));
    lo = Math.round(lo2);
    hi = Math.round(lo2 + width2);
    draw();
  };
  const onUp = () => {
    panning = null;
    window.removeEventListener("mousemove", onMove);
    window.removeEventListener("mouseup", onUp);
    setTimeout(() => { dragged = false; }, 0);   // let the click handler see it
  };
  svg.addEventListener("mousedown", e => {
    if (lo === 0 && hi === n - 1) return;   // nothing to pan to
    panning = { x: e.clientX, lo, hi };
    dragged = false;
    e.preventDefault();
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  });
  svg.addEventListener("dblclick", () => { lo = 0; hi = n - 1; draw(); });

  wrap.append(svg, reset);
  wrap._updateCursor = placeCursor;
  draw();
  return wrap;
}

function updateChartCursor() {
  // the node panel's chart and the results dock's, whichever are on screen
  for (const box of document.querySelectorAll(".chart-box")) {
    if (box._updateCursor) box._updateCursor();
  }
}

/* ------------------------------------------------------------- env */
async function refreshEnv() {
  try { S.env = await api("/api/env"); } catch { S.env = null; }
  const chip = $("env-chip");
  chip.replaceChildren();
  if (!S.env) { chip.textContent = "server offline"; return; }
  if (S.env.ready) {
    chip.append(el("span", { class: "dot", style: "background:var(--good)" }),
      `pywr ${S.env.pywr_version}`);
  } else if (S.env.setting_up) {
    chip.append(el("span", { class: "dot", style: "background:var(--warning)" }),
      "setting up pywr…");
    setTimeout(refreshEnv, 2500);
  } else {
    chip.append(el("span", { class: "dot", style: "background:var(--critical)" }),
      "pywr not set up");
  }
  chip.style.cursor = "pointer";
  chip.onclick = envModal;
}

function envModal() {
  const logBox = el("pre", { class: "log" }, (S.env && S.env.log || []).join(""));
  const btn = el("button", {
    class: "primary",
    onclick: async () => {
      await api("/api/env/setup", {});
      toast("Environment setup started — this downloads pywr and can take a few minutes");
      closeModal();
      setTimeout(refreshEnv, 1500);
    },
  }, S.env && S.env.ready ? "Rebuild environment" : "Set up PyWR now");
  openModal(
    el("h3", {}, "PyWR environment"),
    el("p", {},
      S.env && S.env.ready
        ? `Ready — pywr ${S.env.pywr_version} (${S.env.python})`
        : "Running models needs pywr, which is installed once into a private " +
          "environment (.pywr-env). Reading, layout and editing work without it."),
    S.env && !S.env.ready && S.env.log && S.env.log.length ? logBox : "",
    el("div", { class: "row gap", style: "margin-top:10px; justify-content:flex-end" },
      el("button", { onclick: closeModal }, "Close"), btn),
  );
}

/* ------------------------------------------------------------- wiring */
function setTab(name) {
  document.querySelectorAll("#tabs button[data-tab]").forEach(b =>
    b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".tab").forEach(t =>
    t.classList.toggle("active", t.id === "tab-" + name));
}
// [data-tab] so the collapse toggle (no data-tab) isn't treated as a tab
document.querySelectorAll("#tabs button[data-tab]").forEach(b =>
  b.addEventListener("click", () => setTab(b.dataset.tab)));

// Collapse the side panel to hand its width to the network, and bring it back.
// The choice is remembered across reloads.
function setSidebarCollapsed(collapsed) {
  const before = canvas.clientWidth;
  $("sidebar").classList.toggle("collapsed", collapsed);
  $("sidebar-reopen").classList.toggle("hidden", !collapsed);
  try { localStorage.setItem("pywr_reader_sidebar", collapsed ? "1" : "0"); }
  catch { /* private mode — fine, just won't be remembered */ }
  // The canvas just gained (or lost) the sidebar's width, all on one side, so
  // the network slid off-centre. Shift the view by half the change to keep it
  // where it looks like it should be — a full re-fit would throw away the zoom
  // and the part of the network you were looking at.
  requestAnimationFrame(() => {
    const after = canvas.clientWidth;
    if (before && after) S.view.x += (after - before) / 2;
    applyView();
  });
}
$("btn-sidebar-collapse").addEventListener("click", () => setSidebarCollapsed(true));
$("sidebar-reopen").addEventListener("click", () => setSidebarCollapsed(false));

$("btn-open").addEventListener("click", openFileModal);
$("btn-open2").addEventListener("click", openFileModal);

/* The demo model ships with the app, but a packaged build unpacks it where
   nobody could browse to it — so the empty state offers it as a button, and
   only when the server says it is actually there. */
$("btn-example").addEventListener("click", async () => {
  try {
    const { path } = await api("/api/example");
    if (!path) return toast("The example model isn't bundled with this build", true);
    if (!await okToDiscard("Opening the example")) return;
    if (await openPath(path)) {
      requestAnimationFrame(() => requestAnimationFrame(fitView));
      toast("Example model opened — click a node to trace its water path");
    }
  } catch (err) { toast(err.message, true); }
});
async function offerExample() {
  try {
    const { path } = await api("/api/example");
    $("btn-example").classList.toggle("hidden", !path);
  } catch { /* no example, leave the button hidden */ }
}
$("btn-new").addEventListener("click", newModelModal);

/* ---- trace image wiring ---- */
$("btn-trace").addEventListener("click", () => {
  if (!S.graph) { toast("Open or start a model first (New), then load a trace image", true); return; }
  if (S.bg) { $("trace-panel").classList.remove("hidden", "collapsed"); }
  else $("trace-file").click();
});
$("trace-file").addEventListener("change", e => {
  const file = e.target.files && e.target.files[0];
  if (file) loadTraceImage(file);
  e.target.value = "";   // allow re-selecting the same file
});
$("tp-close").addEventListener("click", () =>
  $("trace-panel").classList.toggle("collapsed"));
$("tp-lock").addEventListener("click", () => setBgLocked(!(S.bg && S.bg.locked)));
$("tp-opacity").addEventListener("input", e => {
  if (!S.bg) return;
  S.bg.opacity = (+e.target.value) / 100;
  updateBgGeometry(); persistBg();
});
$("tp-smaller").addEventListener("click", () => scaleBgBy(1 / 1.1));
$("tp-bigger").addEventListener("click", () => scaleBgBy(1.1));
$("tp-fit").addEventListener("click", fitBgToView);
$("tp-replace").addEventListener("click", () => $("trace-file").click());
$("tp-remove").addEventListener("click", async () => {
  if (await confirmAsk("Remove the trace image?",
    "The nodes you traced over it stay where they are.",
    { confirmLabel: "Remove", danger: true })) removeTraceImage();
});
$("tp-sidecar").addEventListener("click", saveTraceSidecar);
$("tp-quick").addEventListener("change", e => { S.quickPlace = e.target.checked; });
$("btn-save").addEventListener("click", async () => {
  if (!S.graph) return;
  if (!S.graph.path) return saveAsModal();
  try {
    const res = await api("/api/save", {});
    toast("Saved " + res.path);
    refreshGraph();
  } catch (err) { toast(err.message, true); }
});
// after Save As the model gains a path — re-home the trace image under it
function rehomeBgAfterSave(prevKey) {
  if (S.bg && bgKey() !== prevKey) {
    persistBg();
    try { localStorage.removeItem(prevKey); } catch { /* ignore */ }
    renderTracePanel();   // the sidecar button can now be enabled
  }
}
$("btn-saveas").addEventListener("click", () => S.graph && saveAsModal());
/* There was no way back to the empty state once anything was open — which is
   also the only state a .tcm can be opened as a model in. */
$("btn-close").addEventListener("click", async () => {
  if (!S.graph) return;
  if (!await okToDiscard("Closing the model")) return;
  try {
    await api("/api/close", {});
    resetForNewModel();
    S.graph = null; S.nodeIdx = new Map(); S.positions = {}; S.bg = null;
    renderGraph();
    renderBg();
    renderNodePanel();
    renderModelPanel();
    renderRefBadge();
    renderUndo();
    $("empty-state").classList.remove("hidden");
    $("btn-close").classList.add("hidden");
    $("file-chip").textContent = "";
    $("file-chip").title = "";
    dockModelChanged();
    setMode("select");
    toast("Model closed");
  } catch (err) { toast(err.message, true); }
});
/* ------------------------------------------------------- toolbar menus */
function closeMenus(except) {
  document.querySelectorAll(".menu").forEach(m => {
    if (m !== except) m.classList.add("hidden");
  });
}
function toggleMenu(id) {
  const menu = $(id);
  const show = menu.classList.contains("hidden");
  closeMenus(menu);
  menu.classList.toggle("hidden", !show);
  if (show) keepMenuOnScreen(menu);
}

/* Toolbar menus hang from the left edge of their button. For the buttons over
   on the right that runs the menu off the window — so measure once it is
   visible and pin it to the button's right edge instead when it would. */
function keepMenuOnScreen(menu) {
  menu.classList.remove("flip-right");
  const right = menu.getBoundingClientRect().right;
  if (right > document.documentElement.clientWidth - 4) {
    menu.classList.add("flip-right");
  }
}
// a click anywhere else dismisses an open menu
document.addEventListener("click", e => {
  if (!e.target.closest(".menu-wrap")) closeMenus();
});

/** Fill the Layout menu from the server's list (layout.py is the one source
 *  of truth for which layouts exist and what they're called). */
async function loadLayouts() {
  try {
    const { layouts } = await api("/api/layouts");
    $("layout-menu").replaceChildren(...layouts.map(spec => el("button", {
      class: "menu-item", title: spec.hint,
      onclick: () => applyLayout(spec.kind, spec.label),
    }, el("span", { class: "menu-label" }, spec.label),
       el("span", { class: "menu-hint" }, spec.hint))));
  } catch { /* picker just stays empty if the server is unhappy */ }
}

async function applyLayout(kind, label) {
  closeMenus();
  if (!S.graph) return;
  try {
    updateGraph(await api("/api/layout", { mode: "all", kind }));
    fitView();
    toast(`${label} layout applied`);
  } catch (err) { toast(err.message, true); }
}

/* Undo used to cover layouts and nothing else, one level deep: a delete, a
   rename or a JSON Apply was final. The server now snapshots the model before
   every edit, so this is simply a button on that stack — and it says what it
   is about to take back, because "Undo" alone after a few edits is a guess. */
function renderUndo() {
  const btn = $("btn-undo");
  const label = S.graph && S.graph.undo_label;
  btn.classList.toggle("hidden", !label);
  if (!label) return;
  btn.textContent = "↶ Undo";
  btn.title = `Take back: ${label}`;
}

async function doUndo() {
  if (!S.graph || !S.graph.undo_label) return;
  try {
    const payload = await api("/api/undo", {});
    const wasLayout = /^layout/.test(payload.undone || "");
    updateGraph(payload);
    if (wasLayout) fitView();
    toast(`Undone: ${payload.undone}`);
  } catch (err) { toast(err.message, true); }
}

$("btn-layout").addEventListener("click", () => toggleMenu("layout-menu"));
$("btn-undo").addEventListener("click", doUndo);
$("btn-fit").addEventListener("click", fitView);
$("btn-view").addEventListener("click", () => toggleMenu("view-menu"));
initViewPrefs(action => {
  if (action === "restore-view") { restoreSavedView(applyView); closeMenus(); return; }
  renderGraph();
});
$("btn-add").addEventListener("click", () => toggleMenu("add-menu"));
$("btn-mode-select").addEventListener("click", () => setMode("select"));
$("btn-mode-addnode").addEventListener("click", () => {
  setMode(S.mode === "addnode" ? "select" : "addnode");
  closeMenus();
});
$("btn-mode-addedge").addEventListener("click", () => {
  setMode(S.mode === "addedge" ? "select" : "addedge");
  closeMenus();
});
$("btn-run").addEventListener("click", () => startRun(null, null, currentScenarioIndex()));
$("btn-run2").addEventListener("click", () => startRun(null, null, currentScenarioIndex()));
$("btn-open-run").addEventListener("click", openRunModal);
$("btn-open-run").addEventListener("click", openRunModal);
$("btn-run-whatif").addEventListener("click", () =>
  startRun(whatifOverrides(), `what-if ${S.runs.length + 1}`, currentScenarioIndex()));
$("btn-play").addEventListener("click", () => S.playing ? stopPlay() : startPlay());
$("time-slider").addEventListener("input", e => { stopPlay(); setT(+e.target.value); });
$("time-goto").addEventListener("change", e => {
  if (e.target.value) gotoDate(e.target.value);
});
$("play-speed").addEventListener("change", () => { if (S.playing) startPlay(); });
$("btn-values").addEventListener("click", () => {
  S.showEdgeValues = !S.showEdgeValues;
  $("btn-values").classList.toggle("active", S.showEdgeValues);
  updateEdgeLabels();
});

const typeSel = $("add-node-type");
NODE_TYPES.forEach(t => typeSel.append(el("option", {}, t)));
typeSel.value = "link";

window.addEventListener("keydown", e => {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "z" && !e.shiftKey
      && e.target.tagName !== "TEXTAREA" && e.target.tagName !== "INPUT") {
    e.preventDefault();
    doUndo();
    return;
  }
  if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA" ||
      e.target.tagName === "SELECT") return;
  if (e.key === "/") {           // jump to the node search, like many editors
    e.preventDefault();
    searchInput.focus();
    searchInput.select();
    return;
  }
  if (e.key === "Escape") {
    if (!$("modal-backdrop").classList.contains("hidden")) closeModal();
    else if (S.mode !== "select") setMode("select");
    else deselect();
  }
  if ((e.key === "Delete" || e.key === "Backspace") && S.sel) {
    e.preventDefault();
    const pane = $("tab-node");
    const btn = pane.querySelector("button.danger");
    if (btn) btn.click();
  }
  if (e.key === " " && S.activeRun) { e.preventDefault();
    S.playing ? stopPlay() : startPlay(); }
});
window.addEventListener("resize", applyView);

/* --------------------------------------------------------------- init */
(async function init() {
  applyView();
  setMode("select");
  renderWhatIf();
  initDock();
  initResults();
  try { if (localStorage.getItem("pywr_reader_sidebar") === "1")
    setSidebarCollapsed(true); } catch { /* ignore */ }
  await Promise.all([refreshGraph(), refreshEnv(), refreshRuns(), loadLayouts(),
                     offerExample()]);
  loadBgForModel();   // restore a trace image saved for this model
  // wait for CSS layout to settle before measuring the canvas
  if (S.graph) requestAnimationFrame(() => requestAnimationFrame(fitView));
  // resume polling for any run still in flight
  S.runs.filter(r => r.status === "running" || r.status === "queued")
    .forEach(r => pollRun(r.id));
  // A reload restored the run list but left nothing active, so the time slider
  // and the flow colours were gone until you hunted for the radio. Pick up
  // where the page left off instead.
  const lastDone = [...S.runs].reverse().find(r => r.status === "done");
  if (lastDone) activateRun(lastDone.id);
})();


// Debug/test surface: the browser smoke tests call these by name via
// page.evaluate, which runs in the page global scope (module scope is private).
Object.assign(window, { S, selectNode, updateGraph, openModelExplorer, toggleDock, toggleResults, buildChart,
  download });
