/* Shared mutable UI state and a couple of layout constants.
   S is a single object every module mutates in place (never reassigned). */

export const S = {
  graph: null,            // last /api/graph payload
  positions: {},          // {name: [x, y]} client copy
  nodeIdx: new Map(),     // name -> node object
  sel: null,              // {kind:'node'|'edge', name} | {kind:'edge', src, dst, idx}
  traceMode: "both",      // both | up | down | off
  mode: "select",         // select | addnode | addedge
  edgeSrc: null,
  view: { x: 40, y: 40, k: 1 },
  env: null,
  runs: [],
  activeRun: null,        // status payload of the active (done) run
  compare: new Set(),
  t: 0,
  frames: new Map(),      // blockStart -> frames payload (for activeRun)
  frameReq: new Set(),
  playing: false,
  playTimer: null,
  whatif: [],             // [{node, key, value}]
  scenarioSel: [],        // per-scenario-dimension selected member index
  showEdgeValues: true,   // draw flow numbers on the selected path during a run
  labelEdges: new Set(),  // edge indices to label (selected node's path)
  layoutUndo: null,       // positions before the last layout, for Undo
  seriesCache: new Map(), // `${runId}|${node}` -> series payload
  bg: null,               // trace image {src,x,y,scale,opacity,locked,natW,natH}
  quickPlace: false,      // place traced nodes without the dialog
};

export const BLOCK = 200;
export const NODE_R = 11;
