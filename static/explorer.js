/* The "Browse model" explorer modal, and editing the model as JSON at
   three levels (whole model / a section / one entry, plus a node). */

import { api } from "./api.js";
import { $, el, openModal, closeModal, toast } from "./dom.js";
import { FORMS } from "./catalog.js";
// selectNode/updateGraph are canvas-core functions in app.js. app.js also
// imports openModelExplorer from here, so this is a deliberate cycle — safe
// because these are only called from click handlers, never at module load.
import { selectNode, updateGraph } from "./app.js";

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

function detailRow(name, def, actions = {}) {
  const button = (label, title, fn, cls) => el("button", {
    class: "tiny " + (cls || ""), title,
    onclick: e => { e.preventDefault(); e.stopPropagation(); fn(); },
  }, label);
  return el("details", {},
    el("summary", {}, name,
      el("span", { class: "pill-type" }, compactVal(def)),
      el("span", { class: "row gap row-edit" },
        actions.onEdit ? button("{ } edit", "Edit this entry as JSON",
          actions.onEdit) : null,
        actions.onRename ? button("rename",
          "Rename, updating everything that refers to it", actions.onRename) : null,
        actions.onDelete ? button("✕", "Remove this entry",
          actions.onDelete, "danger-quiet") : null)),
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

export async function openModelExplorer() {
  let raw;
  try { raw = await api("/api/model/raw"); }
  catch (err) { return toast(err.message, true); }

  let nodes, edges, params, tables, recorders;
  const readRaw = () => {
    nodes = raw.nodes || [];
    edges = raw.edges || [];
    params = raw.parameters || {};
    tables = raw.tables || {};
    recorders = raw.recorders || {};
  };
  readRaw();
  const sections = () => [
    ["Overview", 1],
    ["Nodes", nodes.length],
    ["Edges", edges.length],
    ["Parameters", Object.keys(params).length],
    ["Tables", Object.keys(tables).length],
    ["Recorders", Object.keys(recorders).length],
  ];
  let active = "Overview";

  /** Pull the model again and redraw, staying on the section you were in —
   *  for edits made from a row, which change the model without closing this. */
  async function reload() {
    raw = await api("/api/model/raw");
    readRaw();
    renderNav();
    renderBody();
  }

  const filter = el("input", { class: "explorer-filter", type: "text",
    placeholder: "Filter by name / type…",
    oninput: () => renderBody() });
  const bodyEl = el("div", { class: "explorer-body" });
  const nav = el("div", { class: "explorer-nav" });

  /** Put the explorer back in the modal. These elements are kept, not rebuilt,
   *  so a dialog opened over the explorer (rename) can hand it back with the
   *  filter text and the section you were in intact. */
  function showExplorer() {
    openModal(
      el("h3", {}, (raw.metadata && raw.metadata.title) || "Model"),
      nav, filter, bodyEl,
      el("div", { class: "row gap", style: "margin-top:10px; justify-content:flex-end" },
        el("button", { onclick: closeModal }, "Close")),
    );
    $("modal").classList.add("explorer");
  }

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
  /** How many places refer to an entry — asked before renaming or deleting so
   *  the dialog can say what is at stake rather than guessing. */
  async function refCount(section, name) {
    try {
      const res = await api(
        `/api/definition/refs?section=${encodeURIComponent(section)}`
        + `&name=${encodeURIComponent(name)}`);
      return (res.refs || []).length;
    } catch { return null; }
  }
  const places = n =>
    n == null ? "" : `${n} ${n === 1 ? "place refers" : "places refer"} to it`;

  async function renameEntry(section, name) {
    const used = await refCount(section, name);
    const box = el("input", { type: "text", value: name, style: "width:100%" });
    const err = el("div", { class: "json-err hidden" });
    const go = async () => {
      const next = box.value.trim();
      if (!next || next === name) return showExplorer();
      try {
        const payload = await api("/api/definition/rename",
          { section, old: name, new: next });
        updateGraph(payload);
        // a filter holding the old name would hide the row you just renamed
        if (filter.value.trim() === name) filter.value = next;
        await reload();
        showExplorer();
        const notes = (payload.notes || []).length;
        toast(`Renamed to ${next}`
          + (notes ? ` — ${notes} reference${notes > 1 ? "s" : ""} updated` : "")
          + ". Save to write it to the file");
      } catch (e) {
        err.textContent = e.message; err.classList.remove("hidden");
      }
    };
    box.addEventListener("keydown", e => { if (e.key === "Enter") go(); });
    openModal(
      el("h3", {}, `Rename ${section.replace(/s$/, "")} · ${name}`),
      el("p", { class: "muted small" },
        used ? `Every reference follows the new name — ${places(used)}.`
             : "Nothing else refers to this yet."),
      box, err,
      el("div", { class: "row gap", style: "margin-top:10px;justify-content:flex-end" },
        el("button", { onclick: showExplorer }, "Cancel"),
        el("button", { class: "primary", onclick: go }, "Rename")));
    box.focus(); box.select();
  }

  async function deleteEntry(section, name) {
    const used = await refCount(section, name);
    const warning = !used ? ""
      : `\n\n${used} other ${used === 1 ? "place refers" : "places refer"} to it`
        + ` — ${used === 1 ? "it" : "they"} will point at a name the model no `
        + "longer defines.";
    if (!confirm(`Remove ${section.replace(/s$/, "")} “${name}”?${warning}`)) return;
    try {
      const payload = await api("/api/definition/delete", { section, name });
      updateGraph(payload);
      await reload();
      (payload.delete_warnings || []).forEach(w => toast(w, true));
      if (!(payload.delete_warnings || []).length) toast(`Removed ${name}`);
    } catch (e) { toast(e.message, true); }
  }

  const rowActions = (section, name, def) => ({
    onEdit: () => editEntry(section, name, def),
    onRename: () => renameEntry(section, name),
    onDelete: () => deleteEntry(section, name),
  });

  /** Add one entry from a template. The forms cover what real models are
   *  actually built from; "as JSON" is the way in for everything else. */
  function addEntry(section) {
    const forms = FORMS[section] || [];
    const singular = section.replace(/s$/, "");
    const nameBox = el("input", { type: "text", style: "width:100%",
      placeholder: `name for the new ${singular}` });
    const kindSel = el("select", {},
      ...forms.map(f => el("option", { value: f.key }, f.label)),
      el("option", { value: "__json__" }, "Write it as JSON myself"));
    const fieldsEl = el("div", { class: "stack" });
    const err = el("div", { class: "json-err hidden" });
    // names already defined anywhere, offered as datalist suggestions so a
    // reference to a parameter/recorder/node is picked, not retyped
    const suggestions = {
      table: Object.keys(tables), parameter: Object.keys(params),
      recorder: Object.keys(recorders), node: nodes.map(n => n.name),
    };
    let inputs = {};

    function renderFields() {
      inputs = {};
      const form = forms.find(f => f.key === kindSel.value);
      if (!form) {
        const box = el("textarea", { class: "json-edit", spellcheck: "false",
          style: "min-height:32vh" });
        box.value = "{\n  \"type\": \"\"\n}";
        inputs.__json__ = box;
        fieldsEl.replaceChildren(box);
        return;
      }
      fieldsEl.replaceChildren(...form.fields.map(([key, label, hint, kind]) => {
        const listId = suggestions[kind] ? `dl-${section}-${key}` : null;
        const input = el("input", { type: "text", placeholder: hint,
          style: "width:100%", ...(listId ? { list: listId } : {}) });
        inputs[key] = { input, kind, label };
        return el("label", { class: "stack small" }, label, input,
          listId ? el("datalist", { id: listId },
            ...suggestions[kind].map(v => el("option", { value: v }))) : null);
      }));
    }
    kindSel.addEventListener("change", renderFields);
    renderFields();

    const go = async () => {
      err.classList.add("hidden");
      const fail = msg => { err.textContent = msg; err.classList.remove("hidden"); };
      const name = nameBox.value.trim();
      if (!name) return fail(`the ${singular} needs a name`);
      let definition;
      const form = forms.find(f => f.key === kindSel.value);
      if (!form) {
        try { definition = JSON.parse(inputs.__json__.value); }
        catch (e) { return fail("Invalid JSON — " + e.message); }
      } else {
        const values = {};
        for (const [key, { input, kind, label }] of Object.entries(inputs)) {
          const text = input.value.trim();
          if (!text) return fail(`“${label}” is empty`);
          if (kind === "json") {
            try { values[key] = JSON.parse(text); }
            catch (e) { return fail(`“${label}”: invalid JSON — ${e.message}`); }
          } else { values[key] = text; }
        }
        definition = form.build(values);
      }
      try {
        const payload = await api("/api/definition/add",
          { section, name, definition });
        updateGraph(payload);
        await reload();
        showExplorer();
        toast(`Added ${name} — Save to write it to the file`);
      } catch (e) { fail(e.message); }
    };

    openModal(
      el("h3", {}, `Add a ${singular}`),
      el("label", { class: "stack small" }, "Name", nameBox),
      el("label", { class: "stack small", style: "margin-top:8px" },
        "What it does", kindSel),
      el("div", { style: "margin-top:8px" }, fieldsEl),
      err,
      el("div", { class: "row gap", style: "margin-top:10px;justify-content:flex-end" },
        el("button", { onclick: showExplorer }, "Cancel"),
        el("button", { class: "primary", onclick: go }, "Add")));
    $("modal").classList.add("explorer");
    nameBox.focus();
  }

  const sectionBar = (section, obj) =>
    el("div", { class: "row gap explorer-bar" },
      el("button", { class: "tiny primary", onclick: () => addEntry(section) },
        `+ add ${section.replace(/s$/, "")}`),
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
        .map(([n, d]) => detailRow(n, d, rowActions("parameters", n, d)));
      content = el("div", {}, sectionBar("parameters", params),
        rows.length ? el("div", {}, ...rows) : emptyMsg());
    } else if (active === "Tables") {
      const rows = Object.entries(tables)
        .filter(([n, d]) => hit(n, (d && d.url) || ""))
        .map(([n, d]) => detailRow(n, d, rowActions("tables", n, d)));
      content = el("div", {}, sectionBar("tables", tables),
        rows.length ? el("div", {}, ...rows) : emptyMsg());
    } else if (active === "Recorders") {
      const rows = Object.entries(recorders)
        .filter(([n, d]) => hit(n, (d && d.type) || ""))
        .map(([n, d]) => detailRow(n, d, rowActions("recorders", n, d)));
      content = el("div", {}, sectionBar("recorders", recorders),
        rows.length ? el("div", {}, ...rows) : emptyMsg());
    }
    bodyEl.replaceChildren(content);
  }
  function emptyMsg() {
    return el("div", { class: "explorer-empty" },
      q() ? "Nothing matches the filter." : "None in this model.");
  }

  function renderNav() {
    nav.replaceChildren(
    ...sections().map(([name, count]) => el("button", {
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
  }
  renderNav();
  showExplorer();
  renderBody();
}

/** Look inside a data file. h5/xlsx are read by pandas out in the pywr
 *  environment, so this needs pywr set up — the error says so if it isn't. */
