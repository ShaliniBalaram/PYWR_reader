/* PyWR Reader frontend — entry module. Imports the leaf modules (state,
   palette, dom, api) and wires the whole app together. Loaded as
   <script type="module">, so it keeps the "no build step" promise. */

import { S, BLOCK, NODE_R } from "./state.js";
import { TYPE_STYLES, OTHER_STYLE, RUN_COLORS, FLOW_RAMP, NODE_TYPES,
         typeStyle, flowColor } from "./palette.js";
import { $, el, svgEl, fmt, toast, openModal, closeModal } from "./dom.js";
import { api } from "./api.js";
import { dataViewer } from "./dataviewer.js";

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

function edgePath(edge) {
  const a = S.positions[edge.src], b = S.positions[edge.dst];
  if (!a || !b) return null;
  const dx = b[0] - a[0], dy = b[1] - a[1];
  const len = Math.hypot(dx, dy) || 1;
  const ux = dx / len, uy = dy / len;
  const r = NODE_R + 2;
  return `M${a[0] + ux * r},${a[1] + uy * r} L${b[0] - ux * (r + 3)},${b[1] - uy * (r + 3)}`;
}

function nodeShape(style) {
  if (style.shape === "square") {
    return svgEl("rect", { x: -NODE_R, y: -NODE_R, width: NODE_R * 2,
      height: NODE_R * 2, rx: 4, fill: style.color });
  }
  if (style.shape === "diamond") {
    const r = NODE_R + 2;
    return svgEl("polygon", { points: `0,${-r} ${r},0 0,${r} ${-r},0`,
      fill: style.color });
  }
  return svgEl("circle", { r: NODE_R, fill: style.color });
}

function renderGraph() {
  gEdges.replaceChildren();
  gNodes.replaceChildren();
  gLabels.replaceChildren();
  nodeEls.clear();
  edgeEls = [];
  edgeLabelEls = [];
  if (!S.graph) return;

  S.graph.edges.forEach((edge, idx) => {
    const d = edgePath(edge);
    const hit = svgEl("path", { class: "edge-hit", d: d || "" });
    const line = svgEl("path", { class: "edge", d: d || "", "marker-end": "url(#arrow)" });
    hit.addEventListener("mousedown", e => { e.stopPropagation(); selectEdge(idx); });
    gEdges.append(hit, line);
    edgeEls.push({ hit, line });
    const vlabel = svgEl("text", { class: "edge-val" });
    vlabel.style.display = "none";
    gLabels.append(vlabel);
    edgeLabelEls.push(vlabel);
  });

  for (const node of S.graph.nodes) {
    const style = typeStyle(node.type);
    const g = svgEl("g", { class: "node" });
    const shapeNode = nodeShape(style);
    // circle nodes get their type color; shape carries the group for CVD
    const label = svgEl("text", { y: NODE_R + 13 });
    label.textContent = node.name;
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

function selectNode(name) {
  S.sel = { kind: "node", name };
  refreshSelection();
  renderNodePanel();
  setTab("node");
}
function selectEdge(idx) {
  const e = S.graph.edges[idx];
  S.sel = { kind: "edge", idx, src: e.src, dst: e.dst };
  refreshSelection();
  renderNodePanel();
  setTab("node");
}
function deselect() {
  S.sel = null;
  refreshSelection();
  renderNodePanel();
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

  if (bg.locked) {
    bgImgEl.style.pointerEvents = "none";
    bgFrameEl.style.display = "none";
    bgHandleEl.style.display = "none";
  } else {
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
  refreshBgHandle();
  renderTracePanel();
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

function loadTraceImage(file) {
  const reader = new FileReader();
  reader.onload = () => {
    const img = new Image();
    img.onload = () => {
      // place the image centred in the current view, sized to ~70% of it
      const w = canvas.clientWidth || 900, h = canvas.clientHeight || 600;
      const worldW = w / S.view.k * 0.7;
      const scale = worldW / img.naturalWidth;
      const cx = (w / 2 - S.view.x) / S.view.k;
      const cy = (h / 2 - S.view.y) / S.view.k;
      S.bg = {
        src: reader.result, natW: img.naturalWidth, natH: img.naturalHeight,
        scale, opacity: 0.55, locked: false,
        x: cx - img.naturalWidth * scale / 2,
        y: cy - img.naturalHeight * scale / 2,
      };
      renderBg();
      persistBg();
      toast("Image loaded — position it, then Lock and trace with + Node / + Edge");
    };
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
function updateGraph(payload) {
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
  renderGraph();
  renderNodePanel();
  renderModelPanel();
  renderScenarioPicker();
  $("file-chip").textContent = payload.path
    ? payload.path.split("/").pop() + (payload.dirty ? " •" : "") : "";
  $("file-chip").title = payload.path || "";
  return payload;
}

async function refreshGraph() {
  try { updateGraph(await api("/api/graph")); }
  catch { /* no model open */ }
}

/* --------------------------------------------------------- open / save */
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
    try {
      const payload = await api("/api/open", { path });
      closeModal();
      resetRunsState();
      updateGraph(payload);
      fitView();
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
    } catch (err) { toast(err.message, true); }
  }

  openModal(
    el("h3", {}, "Open model"),
    el("div", { class: "row gap" }, rootsRow, crumbs),
    list,
    pathbox,
    el("div", { class: "row gap", style: "margin-top:10px; justify-content:flex-end" },
      el("button", { onclick: closeModal }, "Cancel"),
      el("button", { class: "primary", onclick: doOpen }, "Open")),
  );
  browse("~");
  pathbox.addEventListener("keydown", e => { if (e.key === "Enter") doOpen(); });
}

function newModelModal() {
  const nameBox = el("input", { type: "text", value: "Traced model",
    style: "width:100%" });
  const loadImg = el("input", { type: "checkbox" });
  loadImg.checked = true;
  async function create() {
    try {
      const payload = await api("/api/new", { title: nameBox.value.trim() });
      closeModal();
      resetRunsState();
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

function saveAsModal() {
  const pathbox = el("input", { class: "pathbox mono", type: "text",
    value: (S.graph && S.graph.path) || "" });
  openModal(
    el("h3", {}, "Save model as"),
    pathbox,
    el("p", { class: "muted small" },
      "Positions are stored in each node as position.schematic — the file stays a valid pywr model."),
    el("div", { class: "row gap", style: "justify-content:flex-end" },
      el("button", { onclick: closeModal }, "Cancel"),
      el("button", {
        class: "primary",
        onclick: async () => {
          const prevKey = bgKey();
          try {
            const res = await api("/api/save", { path: pathbox.value.trim() });
            closeModal(); toast("Saved " + res.path);
            await refreshGraph();
            rehomeBgAfterSave(prevKey);
          } catch (err) { toast(err.message, true); }
        },
      }, "Save")),
  );
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

  pane.replaceChildren(
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
    el("div", { class: "pane-block chart-area" }),
    el("div", { class: "pane-block" },
      el("button", {
        class: "danger",
        onclick: async () => {
          if (!confirm(`Delete node “${name}” and all its edges?`)) return;
          try {
            const payload = await api("/api/node/delete", { name });
            (payload.delete_warnings || []).forEach(w => toast(w, true));
            updateGraph(payload);
            toast(`Deleted ${name}`);
          } catch (err) { toast(err.message, true); }
        },
      }, "Delete node")),
  );

  if (!document.getElementById("types-dl")) {
    document.body.append(el("datalist", { id: "types-dl" },
      ...NODE_TYPES.map(t => el("option", { value: t }))));
  }
  renderNodeChart();
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
          if (!confirm(`Delete edge ${src} → ${dst}?`)) return;
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
      const col = frame.edgeCols.get(src + " " + dst);
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
  const legend = el("div", { class: "stack" },
    ...TYPE_STYLES.concat(OTHER_STYLE).map(s => el("div", { class: "row gap" },
      el("span", { class: "swatch", style:
        `display:inline-block;width:12px;height:12px;border-radius:${s.shape === "circle" ? "50%" : "3px"};background:${s.color};${s.shape === "diamond" ? "transform:rotate(45deg);" : ""}` }),
      el("span", { class: "small" }, s.label))));
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

/* ----------------------------------------------- model explorer (modal) */
function compactVal(v) {
  if (v == null) return "—";
  if (typeof v !== "object") return String(v);
  if (Array.isArray(v)) return `[${v.length} item${v.length === 1 ? "" : "s"}]`;
  const bits = [];
  if (v.type) bits.push(v.type);
  if (v.url) bits.push(v.url.replace(/\\/g, "/").split("/").pop());
  if (v.table) bits.push(`table:${v.table}`);
  if ("value" in v) bits.push(`= ${v.value}`);
  if (Array.isArray(v.values)) bits.push(`${v.values.length} values`);
  if (v.column) bits.push(`col:${v.column}`);
  return bits.length ? bits.join(" · ") : "{…}";
}

function detailRow(name, def, onEdit) {
  return el("details", {},
    el("summary", {}, name,
      el("span", { class: "pill-type" }, compactVal(def)),
      onEdit ? el("button", {
        class: "tiny row-edit", title: "Edit this entry as JSON",
        onclick: e => { e.preventDefault(); e.stopPropagation(); onEdit(); },
      }, "{ } edit") : null),
    el("pre", {}, JSON.stringify(def, null, 2)));
}

/* ------------------------------------------------------- JSON editing */
/** Modal JSON editor. onApply(parsed) may throw — the message shows inline,
 *  so a bad edit never closes the box and loses your typing. */
function jsonEditorModal(title, value, hint, onApply) {
  const box = el("textarea", { class: "json-edit", spellcheck: "false" });
  box.value = JSON.stringify(value, null, 2);
  const err = el("div", { class: "json-err hidden" });
  const fail = msg => { err.textContent = msg; err.classList.remove("hidden"); };
  const apply = async () => {
    err.classList.add("hidden");        // drop any error from a previous try
    let parsed;
    try { parsed = JSON.parse(box.value); }
    catch (e) { return fail("Invalid JSON — " + e.message); }
    try { await onApply(parsed); }
    catch (e) { return fail(e.message); }
  };
  openModal(
    el("h3", {}, title),
    el("p", { class: "muted small" }, hint),
    box, err,
    el("div", { class: "row gap", style: "margin-top:10px;justify-content:flex-end" },
      el("button", { onclick: closeModal }, "Cancel"),
      el("button", { class: "primary", onclick: apply }, "Apply")));
  $("modal").classList.add("explorer");
  box.focus();
}

/** Push an edited model to the server; the canvas redraws from the response.
 *  renames {old: new} lets the server rewrite references to a node the edit
 *  renamed, so the edges pointing at it don't come back as dangling. */
async function applyRawModel(model, renames) {
  const payload = await api("/api/model/raw", { model, renames });
  updateGraph(payload);
  closeModal();
  const rewrote = (payload.warnings || []).length;
  toast(rewrote
    ? `Model updated — ${rewrote} reference${rewrote > 1 ? "s" : ""} rewritten. `
      + "Save to write it to the file"
    : "Model updated — Save to write it to the file");
}

async function openModelExplorer() {
  let raw;
  try { raw = await api("/api/model/raw"); }
  catch (err) { return toast(err.message, true); }

  const nodes = raw.nodes || [];
  const edges = raw.edges || [];
  const params = raw.parameters || {};
  const tables = raw.tables || {};
  const recorders = raw.recorders || {};
  const SECTIONS = [
    ["Overview", 1],
    ["Nodes", nodes.length],
    ["Edges", edges.length],
    ["Parameters", Object.keys(params).length],
    ["Tables", Object.keys(tables).length],
    ["Recorders", Object.keys(recorders).length],
  ];
  let active = "Overview";

  const filter = el("input", { class: "explorer-filter", type: "text",
    placeholder: "Filter by name / type…",
    oninput: () => renderBody() });
  const bodyEl = el("div", { class: "explorer-body" });
  const nav = el("div", { class: "explorer-nav" });

  function keyScalars(obj) {
    // one-line summary of a node's own scalar attributes
    return Object.entries(obj).filter(([k]) =>
      !["name", "type", "position"].includes(k))
      .map(([k, v]) => `${k}=${compactVal(v)}`).join("  ") || "—";
  }

  // --- editing straight from the explorer -------------------------------
  const patched = (fn, renames) => {      // edit a copy, never `raw` itself
    const next = JSON.parse(JSON.stringify(raw));
    fn(next);
    return applyRawModel(next, renames);
  };
  function editEntry(section, name, def) {
    jsonEditorModal(`${section} · ${name}`, def,
      `Editing one entry. Rename the key with “edit all ${section}”.`,
      parsed => patched(next => { (next[section] ||= {})[name] = parsed; }));
  }
  function editSection(section, obj) {
    jsonEditorModal(`${section} — all ${Object.keys(obj).length}`, obj,
      `The whole ${section} block: add, rename or remove entries freely.`,
      parsed => {
        if (parsed === null || typeof parsed !== "object" ||
            Array.isArray(parsed)) throw new Error(`${section} must be a JSON object`);
        return patched(next => { next[section] = parsed; });
      });
  }
  function editNode(node) {
    jsonEditorModal(`node · ${node.name}`, node,
      "The node's full JSON — type, parameters, position. Change \"name\" and "
      + "every reference to it (edges, aggregated nodes, parameters, "
      + "recorders) is rewritten to match.",
      parsed => {
        if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
          throw new Error("a node must be a JSON object");
        }
        if (!parsed.name) throw new Error("a node needs a \"name\"");
        // a changed name is a rename, not a new node — tell the server so it
        // can carry the references (and the node's position) across
        const renames = parsed.name !== node.name
          ? { [node.name]: parsed.name } : undefined;
        return patched(next => {
          const i = next.nodes.findIndex(x => x.name === node.name);
          next.nodes[i] = parsed;
        }, renames);
      });
  }
  const sectionBar = (section, obj) =>
    el("div", { class: "row gap explorer-bar" },
      el("button", { class: "tiny", onclick: () => editSection(section, obj) },
        `{ } edit all ${section}`),
      el("span", { class: "muted small" },
        "or use { } edit on any row"));
  const q = () => filter.value.trim().toLowerCase();
  const hit = (...s) => { const t = q(); return !t || s.join(" ").toLowerCase().includes(t); };

  function renderBody() {
    let content;
    if (active === "Overview") {
      const m = raw.metadata || {}, ts = raw.timestepper || {};
      content = el("table", { class: "grid" },
        ...[["title", m.title], ["description", m.description],
            ["minimum_version", m.minimum_version],
            ["solver", (raw.solver || {}).name],
            ["start", ts.start], ["end", ts.end], ["timestep", ts.timestep],
            ["nodes", nodes.length], ["edges", edges.length],
            ["parameters", Object.keys(params).length],
            ["tables", Object.keys(tables).length],
            ["recorders", Object.keys(recorders).length]]
          .filter(([, v]) => v != null && v !== "")
          .map(([k, v]) => el("tr", {}, el("td", { class: "k" }, k),
            el("td", {}, String(v)))));
    } else if (active === "Nodes") {
      const rows = nodes.filter(n => hit(n.name, n.type))
        .map(n => el("tr", { class: "clickable",
          onclick: () => { closeModal(); selectNode(n.name); } },
          el("td", {}, n.name),
          el("td", {}, el("span", { class: "pill-type" }, String(n.type || "link"))),
          el("td", { class: "mono" }, keyScalars(n)),
          el("td", {}, el("button", {
            class: "tiny", title: "Edit this node as JSON",
            onclick: e => { e.stopPropagation(); editNode(n); },
          }, "{ } edit"))));
      content = rows.length ? el("table", { class: "grid" },
        el("tr", {}, el("th", {}, "Name"), el("th", {}, "Type"),
          el("th", {}, "Attributes  (click a row to select on canvas)"),
          el("th", {})),
        ...rows) : emptyMsg();
    } else if (active === "Edges") {
      const rows = edges.filter(e => hit(e[0], e[1]))
        .map(e => el("tr", {}, el("td", {}, e[0]), el("td", {}, "→"),
          el("td", {}, e[1]),
          el("td", { class: "mono" }, e.slice(2).filter(x => x != null).join(", "))));
      content = rows.length ? el("table", { class: "grid" },
        el("tr", {}, el("th", {}, "Source"), el("th", {}), el("th", {}, "Destination"),
          el("th", {}, "Slot")), ...rows) : emptyMsg();
    } else if (active === "Parameters") {
      const rows = Object.entries(params)
        .filter(([n, d]) => hit(n, (d && d.type) || ""))
        .map(([n, d]) => detailRow(n, d, () => editEntry("parameters", n, d)));
      content = el("div", {}, sectionBar("parameters", params),
        rows.length ? el("div", {}, ...rows) : emptyMsg());
    } else if (active === "Tables") {
      const rows = Object.entries(tables)
        .filter(([n, d]) => hit(n, (d && d.url) || ""))
        .map(([n, d]) => detailRow(n, d, () => editEntry("tables", n, d)));
      content = el("div", {}, sectionBar("tables", tables),
        rows.length ? el("div", {}, ...rows) : emptyMsg());
    } else if (active === "Recorders") {
      const rows = Object.entries(recorders)
        .filter(([n, d]) => hit(n, (d && d.type) || ""))
        .map(([n, d]) => detailRow(n, d, () => editEntry("recorders", n, d)));
      content = el("div", {}, sectionBar("recorders", recorders),
        rows.length ? el("div", {}, ...rows) : emptyMsg());
    }
    bodyEl.replaceChildren(content);
  }
  function emptyMsg() {
    return el("div", { class: "explorer-empty" },
      q() ? "Nothing matches the filter." : "None in this model.");
  }

  nav.replaceChildren(
    ...SECTIONS.map(([name, count]) => el("button", {
      class: "tiny" + (name === active ? " active" : ""),
      onclick: () => {
        active = name; filter.value = "";
        nav.querySelectorAll("button").forEach(b =>
          b.classList.toggle("active", b.dataset.sec === name));
        renderBody();
      },
      "data-sec": name,
    }, name === "Overview" ? name : `${name} (${count})`)),
    el("span", { class: "spacer" }),
    el("button", { class: "tiny", title: "View and edit the whole model as JSON",
      onclick: () => jsonEditorModal("Model JSON", raw,
        "The entire model. Apply updates it in memory and redraws the canvas; " +
        "the file on disk changes only when you Save.",
        applyRawModel) },
      "{ } edit JSON"),
  );

  openModal(
    el("h3", {}, (raw.metadata && raw.metadata.title) || "Model"),
    nav, filter, bodyEl,
    el("div", { class: "row gap", style: "margin-top:10px; justify-content:flex-end" },
      el("button", { onclick: closeModal }, "Close")),
  );
  $("modal").classList.add("explorer");
  renderBody();
}

/** Look inside a data file. h5/xlsx are read by pandas out in the pywr
 *  environment, so this needs pywr set up — the error says so if it isn't. */
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
    el("div", { class: "row gap" }, rootsRow, crumbs), list,
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
  browse("~");
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
  stopPlay();
  $("timebar").classList.add("hidden");
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
          + (run.n_steps ? ` · ${run.n_steps} steps` : ""))),
      el("span", { class: "status " + run.status }, run.status),
    );
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
      });
      item.append(cb);
    }
    if (run.status === "failed") {
      item.style.cursor = "pointer";
      item.addEventListener("click", async () => {
        const st = await api("/api/run/" + run.id);
        openModal(el("h3", {}, "Run failed"),
          el("pre", { class: "log" }, (st.error || "") + "\n\n" + (st.traceback || "")));
      });
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
  } catch (err) { toast(err.message, true); }
}

/* ------------------------------------------------------- frames / time */
const blockOf = t => Math.floor(t / BLOCK) * BLOCK;

async function ensureBlock(start) {
  if (!S.activeRun || S.frames.has(start) || S.frameReq.has(start)) return;
  S.frameReq.add(start);
  try {
    const data = await api(`/api/run/${S.activeRun.id}/frames?start=${start}&count=${BLOCK}`);
    const edgeCols = new Map();
    data.edge_keys.forEach((k, i) => edgeCols.set(k[0] + " " + k[1], i));
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
      // edgeCols keys join with a NUL byte (this file's separator), not a space
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
    const col = frame.edgeCols.get(edge.src + " " + edge.dst);
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

function startPlay() {
  if (!S.activeRun) return;
  S.playing = true;
  $("btn-play").textContent = "❚❚";
  S.playTimer = setInterval(() => {
    const next = S.t + 1 > S.activeRun.n_steps - 1 ? 0 : S.t + 1;
    setT(next);
  }, 90);
}
function stopPlay() {
  S.playing = false;
  $("btn-play").textContent = "▶";
  clearInterval(S.playTimer);
}

/* ------------------------------------------------------------- chart */
async function getSeries(runId, node) {
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

function buildChart(seriesList) {
  const W = 312, H = 170, m = { l: 44, r: 10, t: 8, b: 22 };
  const iw = W - m.l - m.r, ih = H - m.t - m.b;
  const n = Math.max(...seriesList.map(s => s.values.length));
  let lo = Infinity, hi = -Infinity;
  for (const s of seriesList) for (const v of s.values) {
    if (v < lo) lo = v;
    if (v > hi) hi = v;
  }
  if (!isFinite(lo)) { lo = 0; hi = 1; }
  if (lo > 0) lo = 0;
  if (hi === lo) hi = lo + 1;
  const X = i => m.l + (i / Math.max(1, n - 1)) * iw;
  const Y = v => m.t + ih - ((v - lo) / (hi - lo)) * ih;

  const wrap = el("div", { class: "chart-box" });
  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, height: H });

  // gridlines + y ticks
  const ticks = 4;
  for (let i = 0; i <= ticks; i++) {
    const v = lo + ((hi - lo) * i) / ticks;
    const y = Y(v);
    svg.append(svgEl("line", { x1: m.l, x2: W - m.r, y1: y, y2: y,
      stroke: i === 0 ? "var(--baseline)" : "var(--grid)", "stroke-width": 1 }));
    const label = svgEl("text", { x: m.l - 6, y: y + 3, "text-anchor": "end",
      fill: "var(--muted)", "font-size": 9 });
    label.textContent = fmt(v);
    svg.append(label);
  }
  // x date ticks
  const dates = seriesList[0].dates;
  for (let i = 0; i < 4; i++) {
    const idx = Math.round((i / 3) * (n - 1));
    const label = svgEl("text", { x: X(idx), y: H - 6,
      "text-anchor": i === 0 ? "start" : i === 3 ? "end" : "middle",
      fill: "var(--muted)", "font-size": 9 });
    label.textContent = (dates[idx] || "").slice(0, 7);
    svg.append(label);
  }
  // series lines
  for (const s of seriesList) {
    let d = "";
    const step = Math.max(1, Math.floor(s.values.length / 1200));
    for (let i = 0; i < s.values.length; i += step) {
      d += (d ? "L" : "M") + X(i).toFixed(1) + "," + Y(s.values[i]).toFixed(1);
    }
    svg.append(svgEl("path", { d, fill: "none", stroke: s.color,
      "stroke-width": 1.8, "stroke-linejoin": "round" }));
  }
  // current-timestep cursor
  const cursor = svgEl("line", { class: "t-cursor", y1: m.t, y2: m.t + ih,
    stroke: "var(--ink)", "stroke-dasharray": "3 3", "stroke-width": 1,
    opacity: 0.55 });
  svg.append(cursor);

  // hover crosshair + tooltip; click scrubs the timeline
  const hover = svgEl("rect", { x: m.l, y: m.t, width: iw, height: ih,
    fill: "transparent", style: "cursor: crosshair" });
  let tip = null;
  hover.addEventListener("mousemove", e => {
    const rect = svg.getBoundingClientRect();
    const frac = (e.clientX - rect.left) * (W / rect.width);
    const idx = Math.round(((frac - m.l) / iw) * (n - 1));
    if (idx < 0 || idx >= n) return;
    if (!tip) { tip = el("div", { class: "chart-tip" }); document.body.append(tip); }
    tip.replaceChildren(el("div", { class: "d" }, dates[idx] || ""),
      ...seriesList.map(s => el("div", { class: "s" },
        el("span", { class: "sw", style: `background:${s.color}` }),
        el("span", {}, `${s.label}: ${fmt(s.values[idx])}`))));
    tip.style.left = Math.min(window.innerWidth - 220, e.clientX + 12) + "px";
    tip.style.top = (e.clientY + 12) + "px";
  });
  hover.addEventListener("mouseleave", () => { if (tip) { tip.remove(); tip = null; } });
  hover.addEventListener("click", e => {
    const rect = svg.getBoundingClientRect();
    const frac = (e.clientX - rect.left) * (W / rect.width);
    const idx = Math.round(((frac - m.l) / iw) * (n - 1));
    if (idx >= 0 && idx < n) setT(idx);
  });
  svg.append(hover);

  wrap.append(svg);
  wrap._updateCursor = () => {
    const i = Math.min(S.t, n - 1);
    cursor.setAttribute("x1", X(i));
    cursor.setAttribute("x2", X(i));
  };
  wrap._updateCursor();
  return wrap;
}

function updateChartCursor() {
  const box = document.querySelector("#tab-node .chart-box");
  if (box && box._updateCursor) box._updateCursor();
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
  document.querySelectorAll("#tabs button").forEach(b =>
    b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".tab").forEach(t =>
    t.classList.toggle("active", t.id === "tab-" + name));
}
document.querySelectorAll("#tabs button").forEach(b =>
  b.addEventListener("click", () => setTab(b.dataset.tab)));

$("btn-open").addEventListener("click", openFileModal);
$("btn-open2").addEventListener("click", openFileModal);
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
$("tp-remove").addEventListener("click", () => {
  if (confirm("Remove the trace image?")) removeTraceImage();
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
  const before = Object.fromEntries(
    Object.entries(S.positions).map(([k, v]) => [k, [...v]]));
  try {
    updateGraph(await api("/api/layout", { mode: "all", kind }));
    fitView();
    S.layoutUndo = before;                 // only offer Undo once one worked
    $("btn-undo-layout").classList.remove("hidden");
    toast(`${label} layout applied`);
  } catch (err) { toast(err.message, true); }
}

$("btn-layout").addEventListener("click", () => toggleMenu("layout-menu"));
$("btn-undo-layout").addEventListener("click", async () => {
  if (!S.layoutUndo) return;
  try {
    await api("/api/positions", { positions: S.layoutUndo });
    updateGraph(await api("/api/graph"));
    fitView();
    S.layoutUndo = null;
    $("btn-undo-layout").classList.add("hidden");
    toast("Positions restored");
  } catch (err) { toast(err.message, true); }
});
$("btn-fit").addEventListener("click", fitView);
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
$("btn-values").addEventListener("click", () => {
  S.showEdgeValues = !S.showEdgeValues;
  $("btn-values").classList.toggle("active", S.showEdgeValues);
  updateEdgeLabels();
});

const typeSel = $("add-node-type");
NODE_TYPES.forEach(t => typeSel.append(el("option", {}, t)));
typeSel.value = "link";

window.addEventListener("keydown", e => {
  if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA" ||
      e.target.tagName === "SELECT") return;
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
  await Promise.all([refreshGraph(), refreshEnv(), refreshRuns(), loadLayouts()]);
  loadBgForModel();   // restore a trace image saved for this model
  // wait for CSS layout to settle before measuring the canvas
  if (S.graph) requestAnimationFrame(() => requestAnimationFrame(fitView));
  // resume polling for any run still in flight
  S.runs.filter(r => r.status === "running" || r.status === "queued")
    .forEach(r => pollRun(r.id));
})();


// Debug/test surface: the browser smoke tests call these by name via
// page.evaluate, which runs in the page global scope (module scope is private).
Object.assign(window, { S, selectNode, openModelExplorer, download });
