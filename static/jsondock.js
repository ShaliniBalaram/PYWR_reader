/* The live JSON dock — a JSON view of the open model that stays put under the
   canvas and follows whatever is selected, so an edit made either way round
   shows up on the other side.

   Canvas → JSON is automatic: every edit already flows through updateGraph(),
   which calls dockModelChanged(). JSON → canvas happens on Apply, not on every
   keystroke — half-typed JSON is invalid by definition, and a canvas that
   redraws through those states is unusable. Typing is validated as you go; only
   Apply moves the model. */

import { S } from "./state.js";
import { $, el, toast } from "./dom.js";
import { api } from "./api.js";
// updateGraph is a canvas-core function in app.js, which imports initDock from
// here — the same deliberate cycle explorer.js has, and safe for the same
// reason: nothing in this module runs at import time, only from events.
import { updateGraph } from "./app.js";

const SCOPES = [
  ["node", "node", "The selected node on its own."],
  ["related", "node + related",
    "The node, the parameters it uses, the recorders watching it, and the "
    + "tables they read. Remove an entry here and it goes from the model."],
  ["model", "whole model", "Every section of the model at once."],
];

/* Dock state. `base` is the text as last rendered from the server — the text
   differing from it is what "edited" means. `keys` remembers which entries the
   slice was built from, so deleting one from the JSON deletes it from the
   model rather than silently doing nothing. */
const D = {
  open: false,
  scope: "related",
  target: null,     // node name the current text was built for
  base: null,
  keys: null,
  stale: false,
};
let syncSeq = 0;     // guards against two overlapping refreshes
let validateTimer = null;

const box = () => $("dock-text");
const isDirty = () => D.base != null && box().value !== D.base;

/* ------------------------------------------------------- reference walking */

/** Every string anywhere inside a JSON value. pywr wires a model together by
 *  name — a node's "max_flow" is the name of a parameter, an Aggregated
 *  parameter lists the names of others — so a string that matches a key in a
 *  block is a reference to that entry. */
function strings(value, out = new Set()) {
  if (typeof value === "string") out.add(value);
  else if (Array.isArray(value)) for (const v of value) strings(v, out);
  else if (value && typeof value === "object") {
    for (const v of Object.values(value)) strings(v, out);
  }
  return out;
}

/** What hangs off one node: the parameters its attributes name and everything
 *  those build on, plus anything watching the node or watching one of those.
 *
 *  The two directions are deliberately not symmetric. Following references
 *  *down* from the node is bounded — a parameter's dependencies are its own
 *  business. Following them down from a watcher is not: an aggregate recorder
 *  over every demand centre belongs on this node's list, but the forty other
 *  demand centres it names do not, and expanding it would drag in the model. */
function relatedTo(model, node) {
  const P = model.parameters || {}, R = model.recorders || {}, T = model.tables || {};
  const params = new Set(), recs = new Set(), tables = new Set();
  // one walk per definition, reused across passes
  const refs = new Map();
  const refsOf = (block, name) => {
    const key = block === P ? "p:" + name : "r:" + name;
    if (!refs.has(key)) refs.set(key, strings(block[name]));
    return refs.get(key);
  };

  // down: the parameters the node names, and what they are built from
  const pending = [...strings(node)];
  while (pending.length) {
    const name = pending.pop();
    if (name in T) tables.add(name);
    if (name in R) recs.add(name);
    if (name in P && !params.has(name)) {
      params.add(name);
      pending.push(...refsOf(P, name));
    }
  }

  // up: whatever mentions the node, or mentions something already included —
  // a recorder on the node, a recorder on one of its parameters, a parameter
  // reading that recorder. Chains resolve over successive passes.
  for (let pass = 0; pass < 25; pass++) {
    const before = params.size + recs.size;
    const watched = new Set([node.name, ...params, ...recs]);
    for (const [block, into] of [[P, params], [R, recs]]) {
      for (const name of Object.keys(block)) {
        if (into.has(name)) continue;
        for (const s of refsOf(block, name)) {
          if (watched.has(s)) { into.add(name); break; }
        }
      }
    }
    if (params.size + recs.size === before) break;
  }

  // the tables everything included reads its numbers from
  for (const [block, names] of [[P, params], [R, recs]]) {
    for (const name of names) {
      for (const s of refsOf(block, name)) if (s in T) tables.add(s);
    }
  }
  return { parameters: params, recorders: recs, tables };
}

const pick = (block, names) => {
  const out = {};
  for (const name of [...names].sort()) out[name] = block[name];
  return out;
};

/** The document shown for the current scope, and the entry names it was built
 *  from (null when the scope is not a slice). */
function buildDoc(model) {
  if (D.scope === "model") return { doc: model, keys: null };
  const node = (model.nodes || []).find(n => n.name === D.target);
  if (!node) return { doc: null, keys: null };
  if (D.scope === "node") return { doc: node, keys: null };

  const related = relatedTo(model, node);
  const doc = { node }, keys = {};
  for (const section of ["parameters", "recorders", "tables"]) {
    const want = related[section];
    if (!want.size) continue;
    doc[section] = pick(model[section] || {}, want);
    keys[section] = [...want];
  }
  return { doc, keys };
}

/* ------------------------------------------------------------- rendering */

function setText(text, { placeholder, enabled } = {}) {
  const t = box();
  t.value = text;
  t.disabled = enabled === false;
  t.placeholder = placeholder || "";
  D.base = enabled === false ? null : text;
  showError(null);
}

/* Two things report errors here: the as-you-type syntax check and Apply. The
   typing check must only clear its own — the server's answer to an Apply often
   lands first, and having a stray keystroke wipe "edges[0] references unknown
   node 'X'" off the screen loses the one message that mattered. */
let errKind = null;
function showError(msg, kind) {
  const errEl = $("dock-err");
  errEl.textContent = msg || "";
  errEl.classList.toggle("hidden", !msg);
  errKind = msg ? kind : null;
}

function renderBar() {
  $("dock-bar").classList.toggle("hidden", !D.stale);
}

/** Names the model refers to but defines nowhere. Model-wide rather than
 *  slice-scoped on purpose: deleting a parameter here usually breaks something
 *  that is not on screen, which is exactly the case worth seeing. */
function renderRefs() {
  const warnings = (S.graph && S.graph.reference_warnings) || [];
  const strip = $("dock-refs");
  strip.classList.toggle("hidden", !warnings.length);
  if (!warnings.length) return;
  const n = warnings.length;
  strip.title = warnings.join("\n");
  strip.replaceChildren(
    el("span", { class: "dock-refs-head" }, n === 1
      ? "⚠ 1 reference points at a name the model does not define"
      : `⚠ ${n} references point at names the model does not define`),
    el("span", { class: "muted small dock-refs-list" },
      warnings.slice(0, 2).join(" · ")
      + (warnings.length > 2 ? ` · and ${warnings.length - 2} more` : "")));
}

function renderHead() {
  $("dock-scopes").querySelectorAll("button").forEach(b =>
    b.classList.toggle("active", b.dataset.scope === D.scope));
  $("dock-target").textContent = D.scope === "model" ? "whole model"
    : (D.target || "no node selected");

  const status = $("dock-status");
  let label = "—", cls = "";
  if (box().disabled) label = "—";
  else if (!isDirty()) { label = "in sync"; cls = "ok"; }
  else {
    try { JSON.parse(box().value); label = "edited"; cls = "edited"; }
    catch { label = "invalid JSON"; cls = "bad"; }
  }
  status.textContent = label;
  status.className = "dock-status " + cls;
  $("dock-apply").disabled = box().disabled;
  $("dock-revert").disabled = box().disabled || !isDirty();
}

/** Pull the current model and redraw the dock from it. */
async function renderFromServer() {
  const seq = ++syncSeq;
  let model;
  try { model = await api("/api/model/raw"); }
  catch {
    if (seq !== syncSeq) return;
    D.target = null;
    setText("", { enabled: false, placeholder: "No model open." });
    D.stale = false; renderBar(); renderRefs(); renderHead();
    return;
  }
  if (seq !== syncSeq) return;      // a newer refresh already ran

  D.target = S.sel && S.sel.kind === "node" ? S.sel.name : null;
  if (D.scope !== "model" && !D.target) {
    setText("", { enabled: false,
      placeholder: "Select a node on the canvas — its JSON shows here.\n"
        + "Or switch to “whole model” above." });
    D.keys = null;
  } else {
    const { doc, keys } = buildDoc(model);
    D.keys = keys;
    if (doc == null) {
      setText("", { enabled: false, placeholder: "That node is no longer in the model." });
    } else {
      setText(JSON.stringify(doc, null, 2));
    }
  }
  D.stale = false;
  renderBar();
  renderRefs();
  renderHead();
}

/** Something changed that the dock should be showing. Unapplied typing wins:
 *  it is never overwritten, the dock just says it has fallen behind. */
function sync() {
  if (!D.open) return;
  if (isDirty()) { D.stale = true; renderBar(); renderRefs(); renderHead(); return; }
  renderFromServer();
}

export function dockModelChanged() { sync(); }
export function dockSelectionChanged() { sync(); }

/* --------------------------------------------------------------- applying */

function readSlice(parsed) {
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("this scope expects a JSON object");
  }
  const node = D.scope === "node" ? parsed : parsed.node;
  if (!node || typeof node !== "object" || Array.isArray(node)) {
    throw new Error(D.scope === "node" ? "a node must be a JSON object"
      : "expected a \"node\" object in this slice");
  }
  if (!node.name) throw new Error("a node needs a \"name\"");
  return node;
}

/** One key gone and one key arrived in the same block is usually a rename —
 *  but it is indistinguishable from deleting one entry and adding another, so
 *  this only ever proposes. The user's answer decides whether references
 *  follow; guessing either way would be wrong half the time. */
function detectRename(had, now) {
  const gone = had.filter(key => !(key in now));
  const fresh = Object.keys(now).filter(key => !had.includes(key));
  return gone.length === 1 && fresh.length === 1
    ? { old: gone[0], new: fresh[0] } : null;
}

async function applyDock() {
  if (box().disabled) return;
  let parsed;
  try { parsed = JSON.parse(box().value); }
  catch (e) { showError("Invalid JSON — " + e.message, "syntax"); return renderHead(); }

  try {
    let payload;
    if (D.scope === "model") {
      payload = await api("/api/model/raw", { model: parsed });
    } else {
      const node = readSlice(parsed);
      // merge onto the model as it is now, not as it was when this text was
      // rendered — so an edit made on the canvas meanwhile is not undone
      const model = await api("/api/model/raw");
      const i = (model.nodes || []).findIndex(n => n.name === D.target);
      if (i < 0) throw new Error(`node "${D.target}" is no longer in the model`);
      model.nodes[i] = node;
      const renames = {};
      if (D.scope === "related") {
        for (const section of ["parameters", "recorders", "tables"]) {
          const edited = parsed[section];
          const had = (D.keys && D.keys[section]) || [];
          if (edited === undefined && !had.length) continue;
          if (edited !== undefined && (edited === null || typeof edited !== "object"
              || Array.isArray(edited))) {
            throw new Error(`"${section}" must be a JSON object`);
          }
          const now = edited || {};
          let block = model[section] || (model[section] = {});
          let renamed = null;
          const guess = detectRename(had, now);
          if (guess && confirm(
            `Rename ${section.replace(/s$/, "")} “${guess.old}” to `
            + `“${guess.new}”, updating every reference to it?\n\n`
            + `Cancel treats it as removing “${guess.old}” and adding `
            + `“${guess.new}”, leaving references pointing at the old name.`)) {
            renames[section] = { [guess.old]: guess.new };
            renamed = guess;
            // swap the key in place: a renamed entry that jumps to the end of
            // the block turns a one-line rename into a whole-file diff
            const rebuilt = {};
            for (const [key, def] of Object.entries(block)) {
              if (key === guess.old) rebuilt[guess.new] = now[guess.new];
              else rebuilt[key] = def;
            }
            model[section] = block = rebuilt;
          }
          // an entry the slice arrived with and no longer has was deleted
          for (const key of had) {
            if (key !== (renamed && renamed.old) && !(key in now)) delete block[key];
          }
          for (const [key, def] of Object.entries(now)) {
            if (key !== (renamed && renamed.new)) block[key] = def;
          }
        }
      }
      if (node.name !== D.target) renames.nodes = { [D.target]: node.name };
      payload = await api("/api/model/raw", {
        model, renames: Object.keys(renames).length ? renames : undefined });
      if (renames.nodes) S.sel = { kind: "node", name: node.name };
    }
    D.base = box().value;      // applied — no longer counts as unsaved typing
    D.stale = false;
    showError(null);
    updateGraph(payload);      // redraws the canvas, and syncs the dock back
    const rewrote = (payload.warnings || []).length;
    toast(rewrote
      ? `Model updated — ${rewrote} reference${rewrote > 1 ? "s" : ""} rewritten. `
        + "Save to write it to the file"
      : "Model updated — Save to write it to the file");
  } catch (e) {
    showError(e.message, "apply");
    renderHead();
  }
}

/* ---------------------------------------------------------------- wiring */

function setScope(scope) {
  if (scope === D.scope) return;
  if (isDirty() && !confirm("Discard the JSON edits you have not applied?")) return;
  D.scope = scope;
  D.base = null;               // nothing to lose now — always take the server's
  renderFromServer();
}

export function toggleDock(force) {
  D.open = force == null ? !D.open : !!force;
  $("jsondock").classList.toggle("hidden", !D.open);
  $("btn-dock").classList.toggle("active", D.open);
  if (D.open) renderFromServer();
}

/** Drag the dock's top edge to resize it. */
function initGrip() {
  const grip = $("dock-grip"), dock = $("jsondock");
  grip.addEventListener("mousedown", down => {
    down.preventDefault();
    const startY = down.clientY, startH = dock.getBoundingClientRect().height;
    const move = e => {
      const h = Math.max(120, Math.min(window.innerHeight - 160,
        startH + (startY - e.clientY)));
      dock.style.height = h + "px";
    };
    const up = () => {
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
    };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
  });
}

export function initDock() {
  $("dock-scopes").replaceChildren(...SCOPES.map(([scope, label, title]) =>
    el("button", { class: "tiny", title, "data-scope": scope,
      onclick: () => setScope(scope) }, label)));

  $("btn-dock").addEventListener("click", () => toggleDock());
  $("dock-close").addEventListener("click", () => toggleDock(false));
  $("dock-apply").addEventListener("click", applyDock);
  $("dock-revert").addEventListener("click", () => {
    if (isDirty() && !confirm("Discard the JSON edits you have not applied?")) return;
    D.base = null;
    renderFromServer();
  });
  $("dock-reload").addEventListener("click", () => {
    D.base = null;             // the model won, drop the typing
    renderFromServer();
  });
  $("dock-keep").addEventListener("click", () => {
    D.stale = false; renderBar();   // keep typing; Apply merges onto the model as it is now
  });

  const t = box();
  t.addEventListener("input", () => {
    renderHead();
    clearTimeout(validateTimer);
    validateTimer = setTimeout(() => {
      try {
        JSON.parse(t.value);
        if (errKind === "syntax") showError(null);
      } catch (e) { showError("Invalid JSON — " + e.message, "syntax"); }
    }, 250);
  });
  t.addEventListener("keydown", e => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); applyDock(); }
  });
  initGrip();
  renderHead();
}
