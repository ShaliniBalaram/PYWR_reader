/* Templates for the recorders and parameters real models are built from.

   Not a pywr type list — pywr has hundreds and this is not trying to be a
   schema. These are the handful that make up almost every water resource
   model, with the field defaults and the naming convention they conventionally
   carry, so adding one is a click instead of thirty characters of JSON. The
   { } edit escape hatch covers everything else. */

/* Which templates suit a node: pywr records flow through most things, volume
   only from a storage, and a deficit only where demand can go unmet. */
const STORAGE_TYPES = ["storage", "reservoir", "annualvirtualstorage",
  "virtualstorage", "monthlyvirtualstorage", "seasonalvirtualstorage",
  "rollingvirtualstorage", "aggregatedstorage"];
const DEMAND_TYPES = ["output", "demand"];
const SOURCE_TYPES = ["input", "catchment", "rivergauge"];

const isType = (list, type) => list.includes(String(type || "").toLowerCase());
export const isStorage = type => isType(STORAGE_TYPES, type);
export const isDemand = type => isType(DEMAND_TYPES, type);
export const isSource = type => isType(SOURCE_TYPES, type);

/** suffix: how the model names it. `standard` marks the set that "record the
 *  usual things" adds — the ones a real model puts on every demand centre. */
export const RECORDERS = [
  { key: "flow", label: "flow (time series)", suffix: "_flow",
    type: "NumpyArrayNodeRecorder", standard: true,
    hint: "Flow through the node at every timestep." },
  { key: "total_flow", label: "total flow", suffix: "_total_flow",
    type: "TotalFlowNodeRecorder", standard: true,
    hint: "One number: everything that passed through over the run." },
  { key: "deficit", label: "deficit (time series)", suffix: "_deficit",
    type: "NumpyArrayNodeDeficitRecorder", demandOnly: true, standard: true,
    hint: "Unmet demand at every timestep." },
  { key: "total_deficit", label: "total deficit", suffix: "_total_deficit",
    type: "TotalDeficitNodeRecorder", demandOnly: true, standard: true,
    hint: "One number: all the demand the run failed to meet." },
  { key: "deficit_frequency", label: "deficit frequency",
    suffix: "_deficit_frequency", type: "DeficitFrequencyNodeRecorder",
    demandOnly: true, standard: true,
    hint: "The share of timesteps that ran a deficit." },
  { key: "rolling_mean", label: "30-day rolling mean",
    suffix: "_rolling_mean_30d", type: "RollingMeanFlowNodeRecorder",
    extra: { timesteps: 30 },
    hint: "Smoothed flow — the shape behind a noisy daily series." },
  { key: "fdc", label: "flow duration curve", suffix: "_fdc",
    type: "FlowDurationCurveRecorder",
    extra: { percentiles: [5, 10, 25, 50, 75, 90, 95] },
    hint: "Flow at each percentile of the run." },
  { key: "volume", label: "volume (time series)", suffix: "_volume",
    type: "NumpyArrayStorageRecorder", storageOnly: true, standard: true,
    hint: "Stored volume at every timestep." },
  { key: "volume_pc", label: "volume (% full)", suffix: "_pct",
    type: "NumpyArrayStorageRecorder", storageOnly: true,
    extra: { proportional: true },
    hint: "Stored volume as a fraction of capacity." },
];

/** The templates that apply to a node, given its type. */
export function recordersFor(nodeType) {
  const storage = isStorage(nodeType), demand = isDemand(nodeType);
  return RECORDERS.filter(r =>
    (r.storageOnly ? storage : !storage) && (!r.demandOnly || demand));
}

/** What a template would be called on a node, avoiding names already taken. */
export function suggestName(template, nodeName, taken) {
  const base = nodeName + template.suffix;
  if (!taken || !taken.has(base)) return base;
  let i = 2;
  while (taken.has(`${base}_${i}`)) i++;
  return `${base}_${i}`;
}

export const recorderDef = (template, nodeName) => ({
  type: template.type,
  node: nodeName,
  ...(template.extra || {}),
});

/* ------------------------------------------------------------ parameters */

/** fields: [name, label, placeholder, kind]. kind "json" is parsed, anything
 *  else is kept as the string typed. */
export const PARAMETERS = [
  { key: "constant_value", label: "Constant — a fixed number",
    build: f => ({ type: "constant", value: f.value }),
    fields: [["value", "Value", "150", "json"]] },
  { key: "constant_table", label: "Constant — read from a table",
    build: f => ({ type: "constant", table: f.table, index: f.index,
                   column: f.column }),
    fields: [["table", "Table", "flow_and_licences", "table"],
             ["index", "Row (index)", "Woodgarston GW"],
             ["column", "Column", "Annual licence"]] },
  { key: "monthly_profile", label: "Monthly profile — from a table",
    build: f => ({ type: "MonthlyProfile", table: f.table, column: f.column }),
    fields: [["table", "Table", "Monthly_profiles", "table"],
             ["column", "Column", "Seasonal factor"]] },
  { key: "monthly_values", label: "Monthly profile — twelve values",
    build: f => ({ type: "MonthlyProfile", values: f.values }),
    fields: [["values", "Twelve values",
              "[1, 1, 1, 1.1, 1.2, 1.3, 1.3, 1.2, 1.1, 1, 1, 1]", "json"]] },
  { key: "aggregated", label: "Aggregated — combine other parameters",
    build: f => ({ type: "Aggregated", agg_func: f.agg_func,
                   parameters: f.parameters }),
    fields: [["agg_func", "Combine with", "product"],
             ["parameters", "Parameters", "[\"a_base\", \"a_factor\"]", "json"]] },
  { key: "recorder_threshold", label: "Recorder threshold — react to a recorder",
    build: f => ({ type: "RecorderThresholdParameter", recorder: f.recorder,
                   threshold: f.threshold, predicate: f.predicate,
                   values: f.values }),
    fields: [["recorder", "Recorder", "DC_Boyneswood_deficit", "recorder"],
             ["threshold", "Threshold", "0", "json"],
             ["predicate", "Predicate", "GT"],
             ["values", "Values [below, above]", "[0, 1]", "json"]] },
];

export const RECORDER_FORMS = [
  { key: "node", label: "Node — flow or volume over the run",
    build: f => ({ type: f.type, node: f.node }),
    fields: [["type", "Recorder type", "NumpyArrayNodeRecorder"],
             ["node", "Node", "DC_Woodgarston", "node"]] },
  { key: "parameter", label: "Parameter — record what a parameter returns",
    build: f => ({ type: "NumpyArrayParameterRecorder", parameter: f.parameter }),
    fields: [["parameter", "Parameter", "DC_Woodgarston_max_flow", "parameter"]] },
  { key: "aggregated", label: "Aggregated — combine other recorders",
    build: f => ({ type: "AggregatedRecorder",
                   recorder_agg_func: f.recorder_agg_func,
                   recorders: f.recorders }),
    fields: [["recorder_agg_func", "Combine with", "sum"],
             ["recorders", "Recorders", "[\"a_deficit\", \"b_deficit\"]", "json"]] },
  { key: "event", label: "Event — count spells over a threshold",
    build: f => ({ type: "EventRecorder", threshold: f.threshold,
                   minimum_event_length: f.minimum_event_length }),
    fields: [["threshold", "Threshold parameter", "EDO_threshold_param_DC_X",
              "parameter"],
             ["minimum_event_length", "Minimum length (timesteps)", "4", "json"]] },
];

export const TABLE_FORMS = [
  { key: "csv", label: "A data file read by row and column",
    build: f => ({ url: f.url, index_col: f.index_col }),
    fields: [["url", "File", "flows and licences.csv"],
             ["index_col", "Index column", "Node name"]] },
];

export const FORMS = {
  parameters: PARAMETERS,
  recorders: RECORDER_FORMS,
  tables: TABLE_FORMS,
};

/* ---------------------------------------------------------------- bundles */

/* A parameter is rarely added alone. A demand centre's capacity is three
   parameters wired together — a base read from a licence table, a monthly
   profile, and the product of the two — plus the node attribute that points at
   the result. These build the whole chain from the few facts that actually
   differ between one node and the next.

   Modelled on the repeated structures in real zone models, so a new demand
   centre matches the twelve already in the file rather than being spelled a
   new way. Every entry is ordinary JSON afterwards: nothing here is a special
   kind of parameter, only a faster way to type a familiar one. */
export const BUNDLES = [
  {
    key: "seasonal_demand",
    label: "seasonal demand cap",
    applies: type => isDemand(type),
    hint: "A base capacity read from a table, scaled by a monthly profile, "
      + "wired to this node's max_flow.",
    fields: [
      ["table", "Table holding the capacity", "flow_and_licences", "table"],
      ["index", "Row for this node", "DC Boyneswood", "perNode"],
      ["column", "Capacity column", "Demand"],
      ["profileTable", "Table holding the profile", "DC maxflow table", "table"],
      ["profileColumn", "Profile column", "Seasonal factor"],
    ],
    build: (node, f) => ({
      entries: [
        { section: "parameters", name: `${node}_max_flow_base`,
          definition: { type: "ConstantParameter", table: f.table,
            index: f.index, column: f.column,
            comment: "Base capacity before seasonal scaling" } },
        { section: "parameters", name: `${node}_max_flow_factor`,
          definition: { type: "MonthlyProfile", table: f.profileTable,
            column: f.profileColumn } },
        { section: "parameters", name: `${node}_max_flow`,
          definition: { type: "Aggregated", agg_func: "product",
            parameters: [`${node}_max_flow_base`, `${node}_max_flow_factor`] } },
      ],
      nodeChanges: { max_flow: `${node}_max_flow` },
    }),
  },
  {
    key: "annual_licence",
    label: "annual licence volume",
    applies: type => isStorage(type),
    hint: "The annual volume the licence allows, read from a table and wired "
      + "to this node's max_volume.",
    fields: [
      ["table", "Table holding the licence", "flow_and_licences", "table"],
      ["index", "Row for this licence", "Woodgarston GW", "perNode"],
      ["column", "Licence column", "Annual licence"],
    ],
    build: (node, f) => ({
      entries: [
        { section: "parameters", name: `${node}_max_volume`,
          definition: { type: "constant", table: f.table, index: f.index,
            column: f.column } },
      ],
      nodeChanges: { max_volume: `${node}_max_volume` },
    }),
  },
  {
    key: "base_and_topup",
    label: "base + top-up abstraction",
    applies: type => isSource(type),
    hint: "Average and peak output from a table, with the top-up above average "
      + "shaped by a monthly profile — wired to this node's max_flow.",
    fields: [
      ["table", "Table holding the outputs", "flow_and_licences", "table"],
      ["index", "Row for this source", "Woodgarston GW", "perNode"],
      ["baseColumn", "Average output column", "ADO"],
      ["peakColumn", "Peak output column", "PDO"],
      ["profile", "Monthly profile parameter", "topup_monthly_profile",
        "parameter"],
    ],
    build: (node, f) => ({
      entries: [
        { section: "parameters", name: `${node}_base_max_flow`,
          definition: { type: "constant", table: f.table, index: f.index,
            column: f.baseColumn } },
        { section: "parameters", name: `${node}_peak_max_flow`,
          definition: { type: "constant", table: f.table, index: f.index,
            column: f.peakColumn } },
        { section: "parameters", name: `${node}_topup_max_flow`,
          definition: { type: "aggregated", agg_func: "sum",
            parameters: [`${node}_peak_max_flow`,
              { type: "negative", parameter: `${node}_base_max_flow` }],
            comment: "How much the peak output exceeds the average" } },
        { section: "parameters", name: `${node}_max_flow`,
          definition: { type: "aggregated", agg_func: "product",
            parameters: [f.profile, `${node}_topup_max_flow`] } },
      ],
      nodeChanges: { max_flow: `${node}_max_flow` },
    }),
  },
  {
    key: "deficit_events",
    label: "deficit alarm",
    applies: type => isDemand(type),
    hint: "Flags every timestep in deficit and counts sustained spells — the "
      + "drought-order pattern.",
    fields: [
      ["minimum_event_length", "Shortest spell worth counting (timesteps)",
        "4", "json"],
    ],
    build: (node, f) => ({
      entries: [
        { section: "recorders", name: `${node}_deficit`,
          definition: { type: "NumpyArrayNodeDeficitRecorder", node } },
        { section: "parameters", name: `EDO_threshold_param_${node}`,
          definition: { type: "RecorderThresholdParameter",
            recorder: `${node}_deficit`, threshold: 0, predicate: "GT",
            values: [0, 1] } },
        { section: "recorders", name: `EDO_events_${node}`,
          definition: { type: "EventRecorder",
            threshold: `EDO_threshold_param_${node}`,
            minimum_event_length: f.minimum_event_length } },
      ],
    }),
  },
];

export const bundlesFor = nodeType =>
  BUNDLES.filter(b => b.applies(nodeType));
