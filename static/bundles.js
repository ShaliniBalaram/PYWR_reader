/* Building a whole parameter chain from a template.

   A demand centre's capacity is three parameters wired together plus the node
   attribute pointing at the result; a licence is one parameter plus a
   max_volume. Typing those by hand is where a model picks up inconsistencies —
   the twelfth demand centre spelled slightly unlike the first eleven. These
   dialogs ask only for what differs between one node and the next and write the
   rest to the shape the model already uses.

   Everything created is ordinary JSON. The preview shows exactly what will land
   before it lands, and the { } edit buttons own it afterwards. */

import { $, el, openModal, closeModal, toast } from "./dom.js";
import { api } from "./api.js";
import { bundlesFor } from "./catalog.js";
// the same deliberate cycle explorer.js and jsondock.js have with app.js —
// nothing here runs at import time, only from click handlers
import { updateGraph, selectNode } from "./app.js";

/** Names already defined anywhere in the model, so a reference field can offer
 *  what exists and the preview can say which entries would be skipped. */
async function modelNames() {
  const raw = await api("/api/model/raw");
  return {
    raw,
    parameters: Object.keys(raw.parameters || {}),
    recorders: Object.keys(raw.recorders || {}),
    tables: Object.keys(raw.tables || {}),
    node: (raw.nodes || []).map(n => n.name),
    defined: new Set([...Object.keys(raw.parameters || {}),
      ...Object.keys(raw.recorders || {}), ...Object.keys(raw.tables || {})]),
  };
}

export async function openBundleModal(bundle, nodeName) {
  let names;
  try { names = await modelNames(); }
  catch (err) { return toast(err.message, true); }

  const inputs = {};
  const err = el("div", { class: "json-err hidden" });
  const previewEl = el("pre", { class: "bundle-preview" });
  const summaryEl = el("div", { class: "muted small" });
  const warnEl = el("div", { class: "bundle-warn hidden" });

  const fields = bundle.fields.map(([key, label, hint, kind]) => {
    const listId = names[kind] ? `dl-bundle-${key}` : null;
    const input = el("input", { type: "text", placeholder: hint,
      style: "width:100%", ...(listId ? { list: listId } : {}) });
    // Fill in what does not vary. Across a real model the table, the column
    // and the profile are the same for every node of a kind — only the row to
    // look up differs, and that one is left empty so it is the thing you type.
    if (kind === "perNode") input.value = "";
    else if (kind === "table") input.value = names.tables[0] || hint;
    else if (kind === "parameter") {
      input.value = names.parameters.includes(hint) ? hint : "";
    } else input.value = hint;
    input.addEventListener("input", renderPreview);
    inputs[key] = { input, kind, label };
    return el("label", { class: "stack small" }, label, input,
      listId ? el("datalist", { id: listId },
        ...names[kind].map(v => el("option", { value: v }))) : null);
  });

  /** What the current field values would create. Returns null (and shows why)
   *  when a field is empty or holds unparseable JSON. */
  function collect() {
    const values = {};
    for (const [key, { input, kind, label }] of Object.entries(inputs)) {
      const text = input.value.trim();
      if (!text) return { problem: `“${label}” is empty` };
      if (kind === "json") {
        try { values[key] = JSON.parse(text); }
        catch (e) { return { problem: `“${label}”: invalid JSON — ${e.message}` }; }
      } else { values[key] = text; }
    }
    const built = bundle.build(nodeName, values);
    // an entry that already exists is left alone rather than refused: running
    // this on a node that is half set up should finish the job
    const fresh = built.entries.filter(e => !names.defined.has(e.name));
    return { built, fresh, skipped: built.entries.filter(e =>
      names.defined.has(e.name)) };
  }

  function renderPreview() {
    const { problem, built, fresh, skipped } = collect();
    if (problem) {
      previewEl.textContent = "";
      summaryEl.textContent = problem;
      return;
    }
    previewEl.textContent = JSON.stringify(
      Object.fromEntries(fresh.map(e => [e.name, e.definition])), null, 2);
    const bits = [`${fresh.length} new ${fresh.length === 1 ? "entry" : "entries"}`];
    if (skipped.length) {
      bits.push(`${skipped.length} already there and left alone `
        + `(${skipped.map(e => e.name).join(", ")})`);
    }
    if (built.nodeChanges) {
      bits.push(`${Object.keys(built.nodeChanges).join(", ")} on ${nodeName} `
        + "will point at it");
    }
    summaryEl.textContent = bits.join(" · ");

    // The entry the node ends up pointing at is the one the rest feed into.
    // If that one already exists it is left alone — and then the new
    // parameters are built but joined to nothing, which is worth saying out
    // loud rather than leaving to be discovered later.
    const wired = Object.values(built.nodeChanges || {});
    const blocked = skipped.filter(e => wired.includes(e.name));
    warnEl.classList.toggle("hidden", !blocked.length);
    if (blocked.length) {
      warnEl.textContent = `⚠ ${blocked.map(e => e.name).join(", ")} already `
        + "exists and is left as it is, so the new parameters above would not "
        + "be connected to anything. Rename or remove it first if you want this "
        + "template to take over.";
    }
  }

  const go = async () => {
    err.classList.add("hidden");
    const { problem, built, fresh } = collect();
    if (problem) {
      err.textContent = problem; err.classList.remove("hidden");
      return;
    }
    if (!fresh.length && !built.nodeChanges) {
      return toast("Everything in this template is already there");
    }
    try {
      const payload = await api("/api/definition/add", {
        entries: fresh.length ? fresh : [],
        node_changes: built.nodeChanges
          ? { name: nodeName, changes: built.nodeChanges } : undefined,
      });
      updateGraph(payload);
      closeModal();
      selectNode(nodeName);
      toast(`Added ${bundle.label} — ${fresh.length} `
        + `${fresh.length === 1 ? "entry" : "entries"}. `
        + "Save to write it to the file");
    } catch (e) {
      err.textContent = e.message; err.classList.remove("hidden");
    }
  };

  openModal(
    el("h3", {}, `${bundle.label} · ${nodeName}`),
    el("p", { class: "muted small" }, bundle.hint),
    el("div", { class: "stack" }, ...fields),
    el("details", { class: "bundle-details", open: "" },
      el("summary", {}, "What this creates"),
      summaryEl, warnEl, previewEl),
    err,
    el("div", { class: "row gap", style: "margin-top:10px;justify-content:flex-end" },
      el("button", { onclick: closeModal }, "Cancel"),
      el("button", { class: "primary", onclick: go }, "Add")));
  $("modal").classList.add("explorer");
  renderPreview();
}

/** The templates that suit the selected node, as a panel block. Nothing is
 *  shown for a node type none of them fit — a plain link needs no set-up. */
export function bundlesBlock(node) {
  const applicable = bundlesFor(node.type);
  if (!applicable.length) return null;
  return el("div", { class: "pane-block" },
    el("h3", {}, "Common set-ups"),
    el("p", { class: "muted small" },
      "Build a whole parameter chain the way the rest of the model spells it. "
      + "You see exactly what it creates before it lands."),
    el("div", { class: "row gap add-recorders" },
      ...applicable.map(b => el("button", {
        class: "tiny", title: b.hint,
        onclick: () => openBundleModal(b, node.name),
      }, "+ " + b.label))));
}
