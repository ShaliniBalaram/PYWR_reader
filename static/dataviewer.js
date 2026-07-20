/* The data-file viewer modal: table + zoomable plot of an h5/xlsx/csv
   column. Reads via /api/data/preview and /api/data/series. */

import { api } from "./api.js";
import { $, el, svgEl, fmt, openModal, closeModal } from "./dom.js";
import { RUN_COLORS } from "./palette.js";

export async function dataViewer(path, basename) {
  const filter = el("input", { class: "explorer-filter", type: "text",
    placeholder: "Filter keys…" });
  const keyList = el("div", { class: "browser-list" });
  const keyPane = el("div", { class: "dv-keys" }, filter, keyList);
  const tabTable = el("button", { class: "tiny active",
    onclick: () => setMode("table") }, "Table");
  const tabPlot = el("button", { class: "tiny",
    onclick: () => setMode("plot") }, "Plot");
  const content = el("div", { class: "dv-content" },
    el("p", { class: "muted small" }, "reading…"));
  const body = el("div", { class: "dv-body" },
    el("div", { class: "row gap dv-modes" }, tabTable, tabPlot), content);

  let keys = [], current = null, mode = "table", preview = null;
  const seriesCache = new Map();      // key -> /api/data/series result
  const selCols = new Map();          // key -> Set of column names to plot
  const plotView = new Map();         // key -> {i0, i1, lockY} zoom window

  const renderKeys = () => {
    const q = filter.value.trim().toLowerCase();
    const hits = keys.filter(k => !q || k.key.toLowerCase().includes(q));
    keyPane.classList.toggle("hidden", !keys.length);
    keyList.replaceChildren(...(hits.length
      ? hits.slice(0, 600).map(k => el("div", {
          class: "entry" + (k.key === current ? " sel" : ""),
          title: k.key + (k.dtype ? ` · ${k.dtype}` : ""),
          onclick: () => load(k.key),
        }, k.key,
          k.rows != null ? el("span", { class: "sz" }, k.rows + " rows") : null))
      : [el("div", { class: "entry muted" }, "no keys match")]));
  };

  function setMode(m) {
    mode = m;
    tabTable.classList.toggle("active", m === "table");
    tabPlot.classList.toggle("active", m === "plot");
    render();
  }
  const render = () => (mode === "plot" ? renderPlot() : renderTable());

  function renderTable() {
    const p = preview && preview.preview;
    const parts = [];
    if (preview && preview.note)
      parts.push(el("p", { class: "muted small" }, preview.note));
    if (!p) {
      parts.push(el("p", { class: "muted small" },
        keys.length ? "Pick a key on the left to see its values."
                    : "Nothing to preview in this file."));
      content.replaceChildren(...parts);
      return;
    }
    const table = el("table", { class: "grid dv-table" },
      el("tr", {}, el("th", {}, ""), ...p.columns.map(c => el("th", {}, c))),
      ...p.rows.map((row, i) => el("tr", {},
        el("td", { class: "k" }, p.index[i]),
        ...row.map(v => el("td", { class: "mono" },
          v === null ? "—" : String(v))))));
    parts.push(el("p", { class: "muted small" },
      `${p.n_rows != null ? p.n_rows.toLocaleString() : "?"} rows × ${p.n_cols} `
      + (p.truncated ? `— first ${p.rows.length} shown` : "")),
      el("div", { class: "dv-scroll" }, table));
    content.replaceChildren(...parts);
  }

  async function renderPlot() {
    if (keys.length && !current) {          // a multi-key file, none picked yet
      content.replaceChildren(el("p", { class: "muted small" },
        "Pick a key on the left to plot it."));
      return;
    }
    // a csv/single-sheet has no key at all — plot its columns directly
    const startedKey = current;
    const cacheKey = current || "__whole__";
    const keyQ = current ? "&key=" + encodeURIComponent(current) : "";
    let overview = seriesCache.get(cacheKey);          // full range, thinned
    if (!overview) {
      content.replaceChildren(el("p", { class: "muted small" }, "reading…"));
      try {
        overview = await api("/api/data/series?path=" + encodeURIComponent(path) + keyQ);
        seriesCache.set(cacheKey, overview);
      } catch (err) {
        content.replaceChildren(el("div", { class: "json-err" }, err.message));
        return;
      }
    }
    if (mode !== "plot" || current !== startedKey) return;   // user moved on
    const names = overview.series.map(s => s.name);
    if (!names.length) {
      content.replaceChildren(el("p", { class: "muted small" },
        "No numeric columns to plot in this key."));
      return;
    }
    let sel = selCols.get(cacheKey);
    if (!sel) { sel = new Set([names[0]]); selCols.set(cacheKey, sel); }
    let view = plotView.get(cacheKey);         // row window, survives chip toggles
    if (!view) { view = { r0: 0, r1: overview.n_rows, lockY: false };
                 plotView.set(cacheKey, view); }
    const colorFor = nm => RUN_COLORS[names.indexOf(nm) % RUN_COLORS.length];
    const total = overview.n_rows;

    // whole-file value range from the overview, so lock-Y is a stable scale
    const fullRange = (() => {
      let lo = Infinity, hi = -Infinity;
      for (const s of overview.series) for (const v of s.values) {
        if (v == null) continue;
        if (v < lo) lo = v;
        if (v > hi) hi = v;
      }
      return isFinite(lo) ? { lo, hi } : { lo: 0, hi: 1 };
    })();

    let chunk = overview;                       // the resolution currently loaded
    let fetchSeq = 0, refetchTimer = null;
    const chartHost = el("div", { class: "dv-charthost" });
    const chips = el("div", { class: "dv-chips" });
    const status = el("span", { class: "muted small" });

    const seriesFor = () => names.filter(nm => sel.has(nm)).map(nm =>
      ({ name: nm, color: colorFor(nm),
         values: chunk.series.find(s => s.name === nm).values }));
    const renderChart = () => {
      chartHost.replaceChildren(dataChart(chunk.rows, chunk.dates, total,
        seriesFor(), view, fullRange, onView));
      [...chips.children].forEach(c =>
        c.classList.toggle("on", sel.has(c.dataset.name)));
    };

    function onView() {                         // debounce a detail re-fetch
      clearTimeout(refetchTimer);
      refetchTimer = setTimeout(maybeRefetch, 350);
    }
    async function maybeRefetch() {
      const viewRows = view.r1 - view.r0;
      if (viewRows >= total * 0.9) {            // back near the whole range
        if (chunk !== overview) { chunk = overview; renderChart(); }
        return;
      }
      const covered = chunk.start <= view.r0 && chunk.stop >= view.r1;
      if (covered && !chunk.downsampled) return;    // already every row here
      const inView = chunk.rows.filter(r => r >= view.r0 && r <= view.r1).length;
      if (covered && inView >= Math.min(Math.ceil(viewRows), 700)) return;
      const margin = Math.round(viewRows * 0.5);    // headroom for panning
      const s = Math.max(0, Math.floor(view.r0 - margin));
      const e = Math.min(total, Math.ceil(view.r1 + margin));
      const seq = ++fetchSeq;
      status.textContent = " · loading detail…";
      try {
        const win = await api("/api/data/series?path=" + encodeURIComponent(path)
          + keyQ + "&start=" + s + "&stop=" + e);
        if (seq !== fetchSeq || mode !== "plot" || current !== startedKey) return;
        chunk = win;
        renderChart();
      } catch { /* keep the coarser view */ }
      finally { if (seq === fetchSeq) status.textContent = ""; }
    }

    chips.replaceChildren(...names.map(nm => el("button", {
      class: "chip-toggle" + (sel.has(nm) ? " on" : ""), "data-name": nm,
      onclick: () => {
        sel.has(nm) ? sel.delete(nm) : sel.add(nm);
        if (!sel.size) sel.add(nm);      // keep at least one line
        renderChart();
      },
    }, el("span", { class: "chip-dot", style: `background:${colorFor(nm)}` }), nm)));

    const note = el("p", { class: "muted small" },
      `${total.toLocaleString()} rows · scroll to zoom in for daily detail`,
      status);
    if (overview.cols_truncated)
      note.append(` · first ${names.length} of ${overview.n_series_available} columns`);
    content.replaceChildren(
      names.length > 1 ? chips : el("span"), chartHost, note);
    renderChart();
  }

  async function load(key) {
    current = key || null;
    renderKeys();
    content.replaceChildren(el("p", { class: "muted small" }, "reading…"));
    try {
      const data = await api("/api/data/preview?path=" + encodeURIComponent(path)
        + (key ? "&key=" + encodeURIComponent(key) : ""));
      keys = data.keys || [];
      if (data.key) current = data.key;
      preview = data;
      renderKeys();
      render();
    } catch (err) {
      content.replaceChildren(el("div", { class: "json-err" }, err.message));
    }
  }

  openModal(
    el("h3", {}, basename),
    el("div", { class: "dv-wrap" }, keyPane, body),
    el("div", { class: "row gap", style: "margin-top:10px;justify-content:flex-end" },
      el("button", { onclick: closeModal }, "Close")));
  $("modal").classList.add("explorer");
  filter.addEventListener("input", renderKeys);
  load(null);
}

/** A line chart for a data file — zoomable in time, with the x-axis in
 *  absolute row coordinates so chunks at different resolutions line up.
 *
 *  rows[i] is the absolute row of point i (ascending); dates[i] its label;
 *  total is the file's row count. view {r0, r1, lockY} is the visible row
 *  window, mutated in place. onView() fires after a zoom/pan so the caller can
 *  re-fetch that window at higher resolution. Scroll to zoom, drag to pan,
 *  double-click or Reset for the whole range; "lock Y" holds fullRange. */
function dataChart(rows, dates, total, seriesList, view, fullRange, onView) {
  const W = 640, H = 250, m = { l: 56, r: 12, t: 10, b: 26 };
  const iw = W - m.l - m.r, ih = H - m.t - m.b;
  const P = rows.length;
  if (view.r0 == null || view.r1 == null || view.r1 > total) {
    view.r0 = 0; view.r1 = total;
  }
  const box = el("div", { class: "chart-box dv-chart" });
  const readout = el("div", { class: "dv-readout muted small" },
    "scroll to zoom · drag to pan · double-click to reset");
  const svgWrap = el("div", { class: "dv-svgwrap" });
  let dragging = false, lastX = 0;

  // last point at or before row r (binary search; rows ascending)
  const ptAtOrBefore = r => {
    let lo = 0, hi = P - 1, ans = 0;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (rows[mid] <= r) { ans = mid; lo = mid + 1; } else hi = mid - 1;
    }
    return ans;
  };
  const clamp = () => {
    const minSpan = Math.min(3, total);
    if (view.r1 - view.r0 < minSpan) view.r1 = view.r0 + minSpan;
    if (view.r0 < 0) { view.r1 -= view.r0; view.r0 = 0; }
    if (view.r1 > total) { view.r0 -= (view.r1 - total); view.r1 = total; }
    if (view.r0 < 0) view.r0 = 0;
  };
  const rowAtClientX = clientX => {
    const svg = svgWrap.querySelector("svg");
    const r = svg.getBoundingClientRect();
    const f = Math.max(0, Math.min(1, ((clientX - r.left) * (W / r.width) - m.l) / iw));
    return view.r0 + f * (view.r1 - view.r0);
  };
  function zoom(centerRow, factor) {
    const span = view.r1 - view.r0;
    const f = span > 0 ? (centerRow - view.r0) / span : 0.5;
    const ns = Math.max(Math.min(3, total), Math.min(total, span * factor));
    view.r0 = centerRow - f * ns; view.r1 = view.r0 + ns;
    clamp(); draw(); if (onView) onView();
  }
  function pan(dxFrac) {
    const span = view.r1 - view.r0;
    view.r0 -= dxFrac * span; view.r1 -= dxFrac * span;
    clamp(); draw(); if (onView) onView();
  }

  function draw() {
    const r0 = view.r0, r1 = view.r1, span = Math.max(1, r1 - r0);
    const a = ptAtOrBefore(r0), b = Math.min(P - 1, ptAtOrBefore(r1) + 1);
    let lo = fullRange.lo, hi = fullRange.hi;
    if (!view.lockY) {                            // auto-fit Y to the window
      lo = Infinity; hi = -Infinity;
      for (const s of seriesList) for (let i = a; i <= b; i++) {
        const v = s.values[i];
        if (v == null) continue;
        if (v < lo) lo = v;
        if (v > hi) hi = v;
      }
      if (!isFinite(lo)) { lo = fullRange.lo; hi = fullRange.hi; }
    }
    if (hi === lo) { hi = lo + 1; lo -= 1; }
    const pad = (hi - lo) * 0.05; lo -= pad; hi += pad;
    const X = row => m.l + ((row - r0) / span) * iw;
    const Y = v => m.t + ih - ((v - lo) / (hi - lo)) * ih;

    const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, class: "dv-svg" });
    for (let k = 0; k <= 4; k++) {                // y gridlines + ticks
      const v = lo + ((hi - lo) * k) / 4, y = Y(v);
      svg.append(svgEl("line", { x1: m.l, x2: W - m.r, y1: y, y2: y,
        stroke: k === 0 ? "var(--baseline)" : "var(--grid)", "stroke-width": 1 }));
      const t = svgEl("text", { x: m.l - 6, y: y + 3, "text-anchor": "end",
        fill: "var(--muted)", "font-size": 10 });
      t.textContent = fmt(v);
      svg.append(t);
    }
    for (let k = 0; k < 5; k++) {                 // x ticks (dates of the window)
      const pi = ptAtOrBefore(r0 + (k / 4) * span);
      const t = svgEl("text", { x: m.l + (k / 4) * iw, y: H - 8, fill: "var(--muted)",
        "text-anchor": k === 0 ? "start" : k === 4 ? "end" : "middle",
        "font-size": 10 });
      t.textContent = (dates[pi] || "").slice(0, 10);
      svg.append(t);
    }
    const showPts = (b - a) <= 80;                // dots once zoomed in far
    for (const s of seriesList) {
      let d = "", started = false;
      for (let i = a; i <= b; i++) {
        const v = s.values[i];
        if (v == null) { started = false; continue; }
        const x = X(rows[i]);
        d += (started ? "L" : "M") + x.toFixed(1) + "," + Y(v).toFixed(1);
        started = true;
        if (showPts) svg.append(svgEl("circle", { cx: x, cy: Y(v), r: 1.8,
          fill: s.color }));
      }
      svg.append(svgEl("path", { d, fill: "none", stroke: s.color,
        "stroke-width": 1.5, "stroke-linejoin": "round" }));
    }
    const guide = svgEl("line", { y1: m.t, y2: m.t + ih, stroke: "var(--ink)",
      "stroke-dasharray": "3 3", "stroke-width": 1, opacity: 0 });
    svg.append(guide);
    const hit = svgEl("rect", { x: m.l, y: m.t, width: iw, height: ih,
      fill: "transparent", style: "cursor:crosshair", "touch-action": "none" });
    svg.append(hit);

    // pointer events with capture: a drag that leaves the chart still tracks,
    // and no window-level listeners leak across redraws
    hit.addEventListener("pointerdown", e => {
      dragging = true; lastX = e.clientX;
      hit.setPointerCapture(e.pointerId); hit.style.cursor = "grabbing";
    });
    hit.addEventListener("pointerup", e => {
      dragging = false; hit.style.cursor = "crosshair";
      hit.releasePointerCapture(e.pointerId);
    });
    hit.addEventListener("pointermove", e => {
      if (dragging) {
        const r = svg.getBoundingClientRect();
        pan(((e.clientX - lastX) * (W / r.width)) / iw);
        lastX = e.clientX;
        return;
      }
      const pi = ptAtOrBefore(rowAtClientX(e.clientX));
      guide.setAttribute("x1", X(rows[pi])); guide.setAttribute("x2", X(rows[pi]));
      guide.setAttribute("opacity", 0.5);
      readout.replaceChildren(
        el("span", { class: "mono" }, (dates[pi] || "").slice(0, 10)),
        ...seriesList.map(s => el("span", { class: "dv-rv" },
          el("span", { class: "chip-dot", style: `background:${s.color}` }),
          el("span", { class: "mono" },
            s.values[pi] == null ? "—" : fmt(s.values[pi])))));
    });
    hit.addEventListener("mouseleave", () => guide.setAttribute("opacity", 0));
    hit.addEventListener("wheel", e => {
      e.preventDefault();
      zoom(rowAtClientX(e.clientX), e.deltaY < 0 ? 0.8 : 1.25);
    }, { passive: false });
    hit.addEventListener("dblclick", () => {
      view.r0 = 0; view.r1 = total; draw(); if (onView) onView();
    });

    svgWrap.replaceChildren(svg);
  }

  const ctlBtn = (label, title, fn) =>
    el("button", { class: "tiny", title, onclick: fn }, label);
  const lockCb = el("input", { type: "checkbox",
    ...(view.lockY ? { checked: "" } : {}) });
  lockCb.addEventListener("change", () => { view.lockY = lockCb.checked; draw(); });
  const controls = el("div", { class: "row gap dv-plotctl" },
    ctlBtn("−", "Zoom out", () => zoom((view.r0 + view.r1) / 2, 1.5)),
    ctlBtn("+", "Zoom in", () => zoom((view.r0 + view.r1) / 2, 0.66)),
    ctlBtn("Reset", "Show the whole range",
      () => { view.r0 = 0; view.r1 = total; draw(); if (onView) onView(); }),
    el("label", { class: "dv-lock", title: "Keep the full value scale while zooming" },
      lockCb, " lock Y"));

  box.append(controls, readout, svgWrap);
  draw();
  return box;
}

