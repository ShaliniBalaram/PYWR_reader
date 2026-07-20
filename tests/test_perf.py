"""Performance guardrails on a large synthetic model.

    ./.venv/bin/python -m unittest tests.test_perf -v

The app's value is staying responsive on real water models, which run to
hundreds or thousands of nodes. These budgets are generous — they exist to
catch a regression that makes something an order of magnitude slower (an
accidental O(n²), a full re-read on every request), not to police milliseconds.
Needs only Flask.
"""

import json
import os
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import app as app_module  # noqa: E402
from pywr_reader import graphops, layout  # noqa: E402

N_SOURCES, N_LINKS, N_DEMANDS = 300, 600, 300      # 1200 nodes, ~1200 edges


def big_model():
    """A wide, deep, connected model: many sources into a link chain, out to
    many demands — the shape that stresses layout and payload building."""
    nodes, edges = [], []
    srcs = [f"src_{i}" for i in range(N_SOURCES)]
    links = [f"link_{i}" for i in range(N_LINKS)]
    dems = [f"dem_{i}" for i in range(N_DEMANDS)]
    nodes += [{"name": s, "type": "catchment", "flow": 10} for s in srcs]
    nodes += [{"name": ln, "type": "link"} for ln in links]
    nodes += [{"name": d, "type": "output", "max_flow": 5} for d in dems]
    edges += [[s, links[i % N_LINKS]] for i, s in enumerate(srcs)]
    edges += [[links[i], links[i + 1]] for i in range(N_LINKS - 1)]
    edges += [[links[i % N_LINKS], d] for i, d in enumerate(dems)]
    return {"metadata": {"title": "big", "minimum_version": "1.20.0"},
            "timestepper": {"start": "2000-01-01", "end": "2000-12-31",
                            "timestep": 1},
            "nodes": nodes, "edges": edges, "parameters": {}, "recorders": {}}


class TestLargeModelPerformance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = big_model()
        cls.names = [n["name"] for n in cls.model["nodes"]]

    def _timed(self, budget_s, label, fn):
        t = time.perf_counter()
        result = fn()
        dt = time.perf_counter() - t
        self.assertLess(dt, budget_s,
                        f"{label} took {dt:.2f}s on {len(self.names)} nodes "
                        f"(budget {budget_s}s) — a scaling regression?")
        return result

    def test_every_layout_is_responsive(self):
        # force was O(n²) and took ~20s here before the grid; the rest are
        # near-linear. Budgets catch a return to quadratic.
        budgets = {"layered": 2.0, "grouped": 2.0, "radial": 2.0, "force": 6.0}
        groups = {n["name"]: layout.node_group(n.get("type", ""))
                  for n in self.model["nodes"]}
        for kind, budget in budgets.items():
            pos = self._timed(budget, f"layout {kind}",
                              lambda k=kind: layout.compute(
                                  k, self.names, self.model["edges"],
                                  groups=groups))
            self.assertEqual(len(pos), len(self.names), kind)

    def test_graph_summary_is_cheap(self):
        summary = self._timed(1.0, "graph_summary",
                              lambda: graphops.graph_summary(self.model, {}))
        self.assertEqual(len(summary["nodes"]), len(self.names))

    def test_open_graph_save_round_trip(self):
        path = os.path.join(tempfile.mkdtemp(), "big.json")
        with open(path, "w") as fh:
            json.dump(self.model, fh)
        app_module.app.testing = True
        c = app_module.app.test_client()
        app_module.WORKSPACE.reset()
        r = self._timed(4.0, "/api/open",
                        lambda: c.post("/api/open", json={"path": path}))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.get_json()["nodes"]), len(self.names))
        self._timed(1.0, "/api/graph", lambda: c.get("/api/graph"))
        out = os.path.join(tempfile.mkdtemp(), "out.json")
        self._timed(3.0, "/api/save",
                    lambda: c.post("/api/save", json={"path": out}))
        self.assertTrue(os.path.isfile(out))


if __name__ == "__main__":
    unittest.main(verbosity=2)
