/* Presentation options for the schematic — which nodes show, which get
   labels, and what they look like.

   A .tcm view file carries all of this (the TCM viewer's own style sheet,
   label toggles, virtual/aggregated filters and saved camera), so opening one
   seeds these options from it. They are useful on their own though: cutting
   labels down to one or two categories is the single biggest readability win
   on a crowded schematic, whatever the model was loaded from. So the panel is
   always available and the .tcm simply supplies the starting point. */

import { S, NODE_R } from "./state.js";
import { TCM_CATEGORIES, TYPE_STYLES, OTHER_STYLE, tcmCategory, typeStyle }
  from "./palette.js";
import { $, el } from "./dom.js";

const DEFAULTS = () => ({
  useTcmStyles: false,
  labelsAll: true,
  labelCats: Object.fromEntries(TCM_CATEGORIES.map(c => [c, true])),
  showVirtual: true,
  showAggregated: true,
});

let onChange = () => {};

/* --------------------------------------------------------------- state */

export function initViewPrefs(cb) {
  onChange = cb;
  S.viewOpts = DEFAULTS();
  renderMenu();
}

/* Called on every graph payload. A .tcm's prefs arrive with the model it was
   opened against, so re-seed only when they actually change — an edit that
   round-trips the graph must not wipe out toggles set since. */
export function viewPrefsChanged(payload) {
  const next = payload && payload.view_prefs ? payload.view_prefs : null;
  const same = JSON.stringify(next || null) === JSON.stringify(S.viewPrefs || null);
  if (!same) {
    S.viewPrefs = next;
    seedFromPrefs();
    $("btn-view").classList.toggle("has-tcm", !!next);
  }
  // the menu is rebuilt either way: its per-category counts describe the graph,
  // which can change under unchanged prefs (an edit, or a model with no .tcm)
  renderMenu();
  return !same;
}

function seedFromPrefs() {
  const opts = DEFAULTS();
  const prefs = S.viewPrefs;
  if (prefs) {
    opts.useTcmStyles = !!prefs.styles;
    const labels = prefs.labels || {};
    // show_all_labels is the viewer's master switch — read literally, so a
    // file with it on looks the way it did there and nothing vanishes on open
    if ("all" in labels) opts.labelsAll = !!labels.all;
    for (const [cat, on] of Object.entries(labels.categories || {})) {
      if (cat in opts.labelCats) opts.labelCats[cat] = !!on;
    }
    if ("show_virtual" in prefs) opts.showVirtual = !!prefs.show_virtual;
    if ("show_aggregated" in prefs) opts.showAggregated = !!prefs.show_aggregated;
  }
  S.viewOpts = opts;
}

/* ------------------------------------------------- what the canvas asks */

export function nodeHidden(node) {
  const o = S.viewOpts;
  if (!o) return false;
  const cat = tcmCategory(node.type);
  if (cat === "Virtual" && !o.showVirtual) return true;
  if (cat === "Aggregated" && !o.showAggregated) return true;
  return false;
}

/* Switch a filter back on when the user asks to go to a node it is hiding —
   jumping to an invisible node otherwise looks like the search is broken.
   Returns the category that was turned back on, or null if nothing was. */
export function revealNode(node) {
  if (!nodeHidden(node)) return null;
  const cat = tcmCategory(node.type);
  if (cat === "Virtual") S.viewOpts.showVirtual = true;
  else if (cat === "Aggregated") S.viewOpts.showAggregated = true;
  else return null;
  renderMenu();
  return cat;
}

export function labelVisible(node) {
  const o = S.viewOpts;
  if (!o) return true;
  if (o.labelsAll) return true;
  return !!o.labelCats[tcmCategory(node.type)];
}

/* The style to draw a node with: ours, or the .tcm's when that is switched on.
   Shape always stays ours — it is what keeps the schematic readable without
   colour, and a .tcm has no shapes to offer. */
export function nodeStyle(node) {
  const mine = typeStyle(node.type);
  const o = S.viewOpts;
  const sheet = S.viewPrefs && S.viewPrefs.styles;
  if (!o || !o.useTcmStyles || !sheet) return mine;
  const spec = sheet[tcmCategory(node.type)] || sheet.Other;
  if (!spec) return mine;
  return { ...mine, color: spec.color || mine.color,
           opacity: spec.opacity, stroke: spec.edge_color };
}

/* .tcm radii are in the source file's own coordinate units, which our
   positions have been rescaled out of — taking them literally would give
   either specks or boulders. What they do carry is the *relative* sizing
   between categories, so normalise against the sheet's mean and keep our own
   node size as the reference. A sheet that sizes every category the same (the
   common case) therefore changes nothing. */
function radiusScale(sheet) {
  const radii = Object.values(sheet)
    .map(s => s.radius).filter(r => typeof r === "number" && r > 0);
  if (!radii.length) return null;
  const mean = radii.reduce((a, b) => a + b, 0) / radii.length;
  return mean > 0 ? mean : null;
}

export function nodeRadius(node) {
  const o = S.viewOpts;
  const sheet = S.viewPrefs && S.viewPrefs.styles;
  if (!o || !o.useTcmStyles || !sheet) return NODE_R;
  const spec = sheet[tcmCategory(node.type)];
  const mean = radiusScale(sheet);
  if (!spec || !mean || !spec.radius) return NODE_R;
  return Math.max(4, Math.min(28, NODE_R * (spec.radius / mean)));
}

/* Our labels are 10px in CSS, which matches the 12.0 a .tcm carries by
   default — so a file left at the default changes nothing here, and one where
   the size was turned up gets bigger labels here too. Returns null to mean
   "leave the stylesheet alone". */
const TCM_DEFAULT_TEXT_SIZE = 12.0;
const BASE_LABEL_PX = 10;

export function labelFontSize() {
  const size = S.viewPrefs && S.viewPrefs.labels && S.viewPrefs.labels.text_size;
  if (!size || size === TCM_DEFAULT_TEXT_SIZE) return null;
  return Math.max(6, Math.min(28, BASE_LABEL_PX * (size / TCM_DEFAULT_TEXT_SIZE)));
}

/* What the Model tab's colour key should show.
   With the .tcm's colours switched on, the canvas is drawn from its style
   sheet and our own palette describes nothing on screen — so the key follows
   the same switch the canvas does, and marks the categories a node filter is
   currently hiding rather than listing them as if they were visible. */
const CATEGORY_SHAPE = { Storage: "square", Virtual: "diamond",
                         Aggregated: "diamond" };

export function legendEntries() {
  const o = S.viewOpts;
  const sheet = S.viewPrefs && S.viewPrefs.styles;
  const hiddenCat = cat => (cat === "Virtual" && o && !o.showVirtual)
                        || (cat === "Aggregated" && o && !o.showAggregated);
  if (o && o.useTcmStyles && sheet) {
    return TCM_CATEGORIES.filter(cat => sheet[cat]).map(cat => ({
      color: sheet[cat].color, opacity: sheet[cat].opacity,
      shape: CATEGORY_SHAPE[cat] || "circle",
      label: cat, source: "the .tcm", hidden: hiddenCat(cat),
    }));
  }
  return TYPE_STYLES.concat(OTHER_STYLE).map(style => ({
    color: style.color, shape: style.shape, label: style.label,
    // our own palette folds virtual and aggregated into one entry, so it is
    // only really hidden when both filters are off
    hidden: /virtual/.test(style.label) && hiddenCat("Virtual")
            && hiddenCat("Aggregated"),
  }));
}

/* --------------------------------------------------------- saved camera */

export function hasSavedView() {
  return !!(S.viewPrefs && S.viewPrefs.viewport);
}

/* The .tcm records the world point under its window's top-left corner and its
   pixels-per-world-unit; the server has already mapped both into the
   coordinate space our positions ended up in. We cannot know how big that
   window was, so we match the zoom and the anchor and let our own canvas size
   decide how much of the network that reveals. */
export function restoreSavedView(applyView) {
  if (!hasSavedView()) return false;
  const { origin, scale } = S.viewPrefs.viewport;
  S.view.k = scale;
  S.view.x = -origin[0] * scale;
  S.view.y = -origin[1] * scale;
  applyView();
  return true;
}

/* ---------------------------------------------------------------- menu */

/* A row's "off" state is refreshed in place rather than by rebuilding the
   menu. Rebuilding on every tick detached the element the click started on,
   and the document-level "clicked outside a menu" handler then saw a node with
   no .menu-wrap ancestor and shut the menu — so every toggle closed it. */
let labelRows = [];

function checkbox(label, checked, hint, handler) {
  const input = el("input", { type: "checkbox" });
  input.checked = checked;
  input.addEventListener("change", () => {
    handler(input.checked);
    syncRowStates();
    onChange();
  });
  const row = el("label", { class: "menu-check", title: hint || "" },
    input, el("span", {}, label));
  return row;
}

function countRow(row, n) {
  row.append(el("span", { class: "menu-count" }, String(n)));
  if (!n) {
    row.classList.add("menu-check-empty");
    row.querySelector("input").disabled = true;
  }
  return row;
}

/* Grey a per-category label row when ticking it would change nothing: either
   "show every label" is already overriding it, or the node filter below has
   that whole category off the canvas, so there is nothing left to label. */
function syncRowStates() {
  const o = S.viewOpts;
  for (const { row, cat, count } of labelRows) {
    const filtered = (cat === "Virtual" && !o.showVirtual)
                  || (cat === "Aggregated" && !o.showAggregated);
    const moot = !!count && (o.labelsAll || filtered);
    row.classList.toggle("menu-check-off", moot);
    row.title = filtered
      ? `${cat} nodes are hidden — switch them back on under Nodes below`
      : row.dataset.hint || row.title;
  }
}

function renderMenu() {
  const menu = $("view-menu");
  if (!menu) return;
  const o = S.viewOpts;
  const prefs = S.viewPrefs;
  const rows = [];
  labelRows = [];

  // how many nodes each category actually has — without this, ticking a
  // category the model has none of looks like a broken checkbox
  const counts = {};
  for (const node of (S.graph && S.graph.nodes) || []) {
    const cat = tcmCategory(node.type);
    counts[cat] = (counts[cat] || 0) + 1;
  }

  rows.push(el("div", { class: "menu-head" }, "Labels"));
  rows.push(checkbox("Show every label", o.labelsAll,
    "Label every node, whatever its type", v => { o.labelsAll = v; }));
  for (const cat of TCM_CATEGORIES) {
    const n = counts[cat] || 0;
    const hint = n ? `Label the ${n} ${cat} node${n === 1 ? "" : "s"}`
                   : `This model has no ${cat} nodes`;
    const row = countRow(checkbox(cat, o.labelCats[cat], hint,
      v => { o.labelCats[cat] = v; }), n);
    row.dataset.hint = hint;
    labelRows.push({ row, cat, count: n });
    rows.push(row);
  }

  rows.push(el("div", { class: "menu-head" }, "Nodes"));
  const filterRow = (label, key, hint, cat) => rows.push(countRow(
    checkbox(label, o[key], hint, v => { o[key] = v; }), counts[cat] || 0));
  filterRow("Show virtual nodes", "showVirtual",
    "Virtual storages — bookkeeping, not real water", "Virtual");
  filterRow("Show aggregated nodes", "showAggregated",
    "Aggregated nodes and storages", "Aggregated");

  rows.push(el("div", { class: "menu-head" }, "Style"));
  if (prefs && prefs.styles) {
    rows.push(checkbox("Use the .tcm's colours", o.useTcmStyles,
      "Colours and relative node sizes from the view file, instead of ours",
      v => { o.useTcmStyles = v; }));
  } else {
    rows.push(el("div", { class: "menu-hint pad" },
      "Open a .tcm view file to borrow its colours."));
  }
  if (hasSavedView()) {
    rows.push(el("button", { class: "menu-item", id: "btn-restore-view",
      title: "Go back to the camera the .tcm was saved with",
      onclick: () => { onChange("restore-view"); } },
      el("span", { class: "menu-label" }, "Restore the .tcm's view"),
      el("span", { class: "menu-hint" }, "same zoom and corner as saved")));
  }

  menu.replaceChildren(...rows);
  syncRowStates();
}
