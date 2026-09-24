/* DOM helpers used everywhere: element lookup/creation, number formatting,
   the toast, and the modal. */

export const $ = id => document.getElementById(id);

export const el = (tag, attrs = {}, ...kids) => {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  for (const kid of kids) {
    if (kid == null) continue;
    node.append(kid.nodeType ? kid : document.createTextNode(kid));
  }
  return node;
};

/* Native replaceChildren renders a null child as the text "null" — unlike el(),
   which drops it. Panel builders legitimately return null when they have
   nothing to show, so go through this rather than the raw DOM call. */
export const setChildren = (node, ...kids) =>
  node.replaceChildren(...kids.filter(k => k != null));

export const svgEl = (tag, attrs = {}) => {
  const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  return node;
};

export function fmt(v) {
  if (v == null) return "—";
  if (Math.abs(v) >= 1000) return v.toLocaleString(undefined, { maximumFractionDigits: 0 });
  return Number(v.toPrecision(4)).toString();
}

let toastTimer = null;
export function toast(msg, isError) {
  const box = $("toast");
  box.textContent = msg;
  box.classList.toggle("error", !!isError);
  box.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => box.classList.add("hidden"), isError ? 7000 : 3500);
}

export function openModal(...content) {
  const modal = $("modal");
  modal.replaceChildren(...content.filter(c => c != null));
  $("modal-backdrop").classList.remove("hidden");
}
export function closeModal() {
  const back = $("modal-backdrop");
  const wasOpen = !back.classList.contains("hidden");
  back.classList.add("hidden");
  $("modal").classList.remove("explorer");
  // ask() has to hear about a backdrop click or an Escape as well as its own
  // buttons — otherwise a dismissed question never settles its promise
  if (wasOpen) back.dispatchEvent(new CustomEvent("modal-dismiss"));
}

/* A replacement for the browser's confirm(): same one-question shape, but it
   matches the rest of the app, can carry an explanation and a list of what an
   action is about to break, and offers more than two answers when the honest
   set of answers is three ("Save first" as well as "Discard").

   Resolves to the chosen button's `value`, or null when dismissed. Native
   confirm() cannot do any of that, and blocks the event loop while it is up. */
export function ask({ title, message, detail, items, buttons }) {
  return new Promise(resolve => {
    let settled = false;
    const done = value => {
      if (settled) return;
      settled = true;
      closeModal();
      $("modal-backdrop").removeEventListener("modal-dismiss", dismiss);
      document.removeEventListener("keydown", onKey, true);
      resolve(value);
    };
    const dismiss = () => done(null);
    const onKey = e => {
      if (e.key === "Escape") { e.stopPropagation(); dismiss(); }
    };

    const list = items && items.length
      ? el("ul", { class: "ask-items" },
          ...items.slice(0, 6).map(t => el("li", { class: "mono small" }, t)),
          items.length > 6
            ? el("li", { class: "muted small" }, `and ${items.length - 6} more`)
            : null)
      : null;

    openModal(
      el("h3", {}, title),
      message ? el("p", {}, message) : null,
      list,
      detail ? el("p", { class: "muted small" }, detail) : null,
      el("div", { class: "row gap ask-buttons" },
        ...buttons.map(b => el("button", {
          class: b.kind || "", onclick: () => done(b.value),
        }, b.label))),
    );
    $("modal-backdrop").addEventListener("modal-dismiss", dismiss);
    document.addEventListener("keydown", onKey, true);
    const primary = $("modal").querySelector(".ask-buttons .primary, .ask-buttons .danger");
    if (primary) primary.focus();
  });
}

/* The everyday two-button case. */
export const confirmAsk = (title, message, opts = {}) => ask({
  title, message, detail: opts.detail, items: opts.items,
  buttons: [{ label: opts.cancelLabel || "Cancel", value: null },
            { label: opts.confirmLabel || "OK", value: true,
              kind: opts.danger ? "danger" : "primary" }],
}).then(Boolean);
