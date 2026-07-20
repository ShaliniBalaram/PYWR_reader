/* Node/flow colours and the pywr node-type list. Node colour = functional
   group; the flow ramp colours run edges by magnitude. */

export const TYPE_STYLES = [
  { re: /virtual|aggregated/, color: "#9085e9", shape: "diamond", label: "virtual / aggregated" },
  { re: /reservoir|storage/, color: "#3987e5", shape: "square", label: "storage / reservoir" },
  { re: /catchment|input|discharge/, color: "#008300", shape: "circle", label: "source / inflow" },
  { re: /river|gauge/, color: "#199e70", shape: "circle", label: "river" },
  { re: /output|demand/, color: "#d95926", shape: "circle", label: "demand / output" },
  { re: /link|delay|break|piecewise|split/, color: "#c98500", shape: "circle", label: "link / conveyance" },
];
export const OTHER_STYLE = { color: "#d55181", shape: "circle", label: "other" };
export const RUN_COLORS = ["#3987e5", "#199e70", "#c98500", "#9085e9", "#e66767", "#d55181"];
export const FLOW_RAMP = ["#0d366b", "#104281", "#184f95", "#1c5cab", "#256abf",
  "#2a78d6", "#3987e5", "#5598e7", "#6da7ec", "#86b6ef", "#9ec5f4", "#cde2fb"];
export const NODE_TYPES = ["input", "output", "link", "storage", "reservoir", "catchment",
  "river", "rivergauge", "riversplit", "riversplitwithgauge", "discharge",
  "losslink", "delaynode", "breaklink", "piecewiselink", "multisplitlink",
  "virtualstorage", "annualvirtualstorage", "seasonalvirtualstorage",
  "monthlyvirtualstorage", "rollingvirtualstorage", "aggregatednode",
  "aggregatedstorage", "keatingaquifer"];

export function typeStyle(type) {
  const t = String(type || "").toLowerCase();
  return TYPE_STYLES.find(s => s.re.test(t)) || OTHER_STYLE;
}
function lerpHex(a, b, t) {
  const pa = [1, 3, 5].map(i => parseInt(a.substr(i, 2), 16));
  const pb = [1, 3, 5].map(i => parseInt(b.substr(i, 2), 16));
  return "#" + pa.map((v, i) => Math.round(v + (pb[i] - v) * t)
    .toString(16).padStart(2, "0")).join("");
}
export function flowColor(t) {
  const x = Math.max(0, Math.min(1, t)) * (FLOW_RAMP.length - 1);
  const i = Math.min(FLOW_RAMP.length - 2, Math.floor(x));
  return lerpHex(FLOW_RAMP[i], FLOW_RAMP[i + 1], x - i);
}
