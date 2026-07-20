"""The user's open model and its runs — held as objects, not module globals.

There is one Workspace (the model on screen and everything derived from it) and
one RunStore (the runs solved so far). Making them named objects with a lock
gives autosave, testing, and any future multiple-open-models support a seam to
hang on — without pretending to be multi-user. This is a local tool: it
browses and writes the local filesystem, so one active session is the right
model, just a tidier one than scattered globals.
"""

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

    def load(self, model, positions, path=None, warnings=None,
             auto=False, dirty=False):
        """Open a fresh model — resets the data-file search and re-resolves."""
        self.model = model
        self.positions = positions
        self.path = path
        self.dirty = dirty
        self.layout_was_auto = auto
        self.warnings = list(warnings or [])
        self.data_dirs = []
        self.data = None
        self.resolve_data()

    def require_model(self):
        if self.model is None:
            raise ValueError("no model is open")

    def resolve_data(self):
        """(Re)locate the model's external data files; store the report."""
        self.data = (None if self.model is None else
                     dataresolve.resolve(self.model, self.path, self.data_dirs))

    def data_payload(self):
        d = self.data or {}
        return {"report": d.get("report", []), "missing": d.get("missing", []),
                "dirs": self.data_dirs}

    def graph_payload(self):
        """The full graph + metadata the frontend renders from."""
        summary = graphops.graph_summary(self.model, self.positions)
        summary.update({
            "ok": True, "path": self.path, "dirty": self.dirty,
            "layout_was_auto": self.layout_was_auto,
            "warnings": self.warnings, "data": self.data_payload(),
        })
        return summary


def normalize_positions(positions):
    """Rescale positions so the median nearest-neighbour distance sits around
    the app's node spacing. Model files store positions in arbitrary units
    (grid cells, screen px, metres); without this, dense layouts render as
    overlapping blobs and sparse ones as specks."""
    pts = list(positions.values())
    if len(pts) < 2:
        return positions
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
        return positions
    nearest.sort()
    median = nearest[len(nearest) // 2]
    if 60.0 <= median <= 400.0:
        return positions
    scale = 120.0 / median
    cx = sum(p[0] for p in pts) / len(pts)
    cy = sum(p[1] for p in pts) / len(pts)
    return {name: [(xy[0] - cx) * scale, (xy[1] - cy) * scale]
            for name, xy in positions.items()}


class RunStore:
    """The runs solved this session, in order. In memory only — a run outlives
    the app only if it's saved beside the model (see the runs API)."""

    def __init__(self):
        self.by_id = {}
        self.order = []

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
        self.by_id.clear()
        self.order.clear()

    def in_order(self):
        """The runs, oldest first (skipping any pruned mid-iteration)."""
        return [self.by_id[rid] for rid in self.order if rid in self.by_id]

    def live_ids(self):
        """Runs still queued or running — their temp snapshots must be kept."""
        return {rid for rid, r in self.by_id.items()
                if r.get("status") in ("queued", "running")}


# The single active session (this is a single-user local app).
WORKSPACE = Workspace()
RUNS = RunStore()
