"""The user's open model and its runs — held as objects, not module globals.

There is one Workspace (the model on screen and everything derived from it) and
one RunStore (the runs solved so far). Making them named objects with a lock
gives autosave, testing, and any future multiple-open-models support a seam to
hang on — without pretending to be multi-user. This is a local tool: it
browses and writes the local filesystem, so one active session is the right
model, just a tidier one than scattered globals.
"""

import copy
import math
import threading

from pywr_reader import dataresolve, graphops


class Workspace:
    """The single model a user has open, plus everything derived from it."""

    def __init__(self):
        self.lock = threading.RLock()
        self.reset()

    def reset(self):
        """Back to no model open."""
        self.model = None            # full pywr model dict
        self.positions = {}          # {name: [x, y]}
        self.path = None             # file the model came from
        self.dirty = False
        self.layout_was_auto = False
        self.warnings = []
        self.data_dirs = []          # extra folders to search for data files
        self.data = None             # dataresolve.resolve(...) result
        self.view_prefs = None       # presentation state from a .tcm, if any
        self.undo = []               # [{label, model, positions, ...}]

    def load(self, model, positions, path=None, warnings=None,
             auto=False, dirty=False, view_prefs=None):
        """Open a fresh model — resets the data-file search and re-resolves."""
        self.model = model
        self.positions = positions
        self.path = path
        self.dirty = dirty
        self.layout_was_auto = auto
        self.warnings = list(warnings or [])
        self.data_dirs = []
        self.data = None
        self.view_prefs = view_prefs or None
        self.undo = []               # a different model — nothing to go back to
        self.resolve_data()

    def require_model(self):
        if self.model is None:
            raise ValueError("no model is open")

    # -- undo ---------------------------------------------------------------
    # Every edit goes through one of the API handlers, and each one snapshots
    # first. A snapshot is a deep copy of the model dict and the positions —
    # the only two things an edit touches. Copying a 1,200-node model costs
    # ~3 ms, which is far cheaper than the per-operation inverse-patch
    # bookkeeping the alternative would need, and it cannot drift out of step
    # with what the operation actually did.
    UNDO_DEPTH = 25

    def push_undo(self, label):
        """Remember the model as it is now, so `label` can be taken back."""
        if self.model is None:
            return
        self.undo.append({
            "label": label,
            "model": copy.deepcopy(self.model),
            "positions": copy.deepcopy(self.positions),
            "dirty": self.dirty,
            "layout_was_auto": self.layout_was_auto,
        })
        del self.undo[:-self.UNDO_DEPTH]

    def undo_label(self):
        """What Undo would take back, or None when there is nothing to undo."""
        return self.undo[-1]["label"] if self.undo else None

    def pop_undo(self):
        """Restore the last snapshot. Returns its label.

        Raises ValueError when the stack is empty, so the caller can turn that
        into a message rather than a 500."""
        if not self.undo:
            raise ValueError("nothing to undo")
        snap = self.undo.pop()
        self.model = snap["model"]
        self.positions = snap["positions"]
        self.dirty = snap["dirty"]
        self.layout_was_auto = snap["layout_was_auto"]
        self.resolve_data()
        return snap["label"]

    def resolve_data(self):
        """(Re)locate the model's external data files; store the report."""
        self.data = (None if self.model is None else
                     dataresolve.resolve(self.model, self.path, self.data_dirs))

    def data_payload(self):
        d = self.data or {}
        return {"report": d.get("report", []), "missing": d.get("missing", []),
                "dirs": self.data_dirs}

    def graph_payload(self):
        """The full graph + metadata the frontend renders from.

        reference_warnings is recomputed here rather than cached: it has to
        reflect the model as it stands after whatever edit just happened, and
        it costs ~1.5 ms on a 1,200-node model."""
        summary = graphops.graph_summary(self.model, self.positions)
        summary.update({
            "ok": True, "path": self.path, "dirty": self.dirty,
            "layout_was_auto": self.layout_was_auto,
            "warnings": self.warnings, "data": self.data_payload(),
            "reference_warnings": graphops.dangling_references(self.model),
            "view_prefs": self.view_prefs,
            "undo_label": self.undo_label(),
        })
        return summary


def normalize_transform(positions):
    """The (scale, cx, cy) normalize_positions would apply to these positions.

    Returned separately so callers holding another point in the *same* source
    space — a .tcm's saved camera, say — can carry it through the identical
    mapping instead of guessing at it afterwards. (1.0, 0.0, 0.0) means the
    positions are already well scaled and are passed through untouched.
    """
    pts = list(positions.values())
    if len(pts) < 2:
        return 1.0, 0.0, 0.0
    sample = pts if len(pts) <= 400 else pts[::max(1, len(pts) // 400)]
    nearest = []
    for i, p in enumerate(sample):
        best = None
        for j, q in enumerate(sample):
            if i == j:
                continue
            d = math.hypot(p[0] - q[0], p[1] - q[1])
            if d > 0 and (best is None or d < best):
                best = d
        if best is not None:
            nearest.append(best)
    if not nearest:
        return 1.0, 0.0, 0.0
    nearest.sort()
    median = nearest[len(nearest) // 2]
    if 60.0 <= median <= 400.0:
        return 1.0, 0.0, 0.0
    return (120.0 / median,
            sum(p[0] for p in pts) / len(pts),
            sum(p[1] for p in pts) / len(pts))


def apply_normalize_transform(point, transform):
    """Map one [x, y] through what normalize_positions did to the positions."""
    scale, cx, cy = transform
    return [(point[0] - cx) * scale, (point[1] - cy) * scale]


def normalize_positions(positions):
    """Rescale positions so the median nearest-neighbour distance sits around
    the app's node spacing. Model files store positions in arbitrary units
    (grid cells, screen px, metres); without this, dense layouts render as
    overlapping blobs and sparse ones as specks."""
    transform = normalize_transform(positions)
    if transform == (1.0, 0.0, 0.0):
        return positions
    return {name: apply_normalize_transform(xy, transform)
            for name, xy in positions.items()}


class RunStore:
    """The runs solved this session, in order. In memory only — a run outlives
    the app only if it's saved beside the model (see the runs API)."""

    def __init__(self):
        self.by_id = {}
        self.order = []
        # ids dropped by clear() while still solving. Their temp snapshots sit
        # beside the model and the worker is still reading one, so the sweep
        # has to keep treating them as live even though they are off the list.
        self.abandoned = set()

    def add(self, run):
        self.by_id[run["id"]] = run
        self.order.append(run["id"])
        return run

    def get(self, run_id):
        return self.by_id.get(run_id)

    def __contains__(self, run_id):
        return run_id in self.by_id

    def __len__(self):
        return len(self.by_id)

    def clear(self):
        self.abandoned |= self.live_ids()
        self.by_id.clear()
        self.order.clear()

    def release(self, run_id):
        """A worker is finished with its snapshot — it can be swept again."""
        self.abandoned.discard(run_id)

    def in_order(self):
        """The runs, oldest first (skipping any pruned mid-iteration)."""
        return [self.by_id[rid] for rid in self.order if rid in self.by_id]

    def live_ids(self):
        """Runs still queued or running — their temp snapshots must be kept."""
        return {rid for rid, r in self.by_id.items()
                if r.get("status") in ("queued", "running")} | self.abandoned


# The single active session (this is a single-user local app).
WORKSPACE = Workspace()
RUNS = RunStore()
