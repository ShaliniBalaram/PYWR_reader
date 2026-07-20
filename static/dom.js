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
  modal.replaceChildren(...content);
  $("modal-backdrop").classList.remove("hidden");
}
export function closeModal() {
  $("modal-backdrop").classList.add("hidden");
  $("modal").classList.remove("explorer");
}
