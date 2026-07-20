/* The "Browse model" explorer modal, and editing the model as JSON at
   three levels (whole model / a section / one entry, plus a node). */

import { api } from "./api.js";
import { $, el, openModal, closeModal, toast } from "./dom.js";
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

export async function openModelExplorer() {
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
