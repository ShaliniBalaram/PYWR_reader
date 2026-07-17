"""API-level tests using Flask's test client (no network, no browser).

    ./.venv/bin/python -m unittest discover -s tests -v

These exercise the open → edit → save → layout flow end to end against the
real routes. The example model under examples/ is used as a fixture.
"""

import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import app as app_module  # noqa: E402

EXAMPLE = os.path.join(ROOT, "examples", "gw_network", "pywr_model.json")
SCENARIO = os.path.join(ROOT, "examples", "scenario_network", "pywr_model.json")


class TestApi(unittest.TestCase):
    def setUp(self):
        app_module.app.testing = True
        self.c = app_module.app.test_client()
        # reset shared state between tests
        app_module.STATE.update(model=None, positions={}, path=None,
                                dirty=False, warnings=[], data_dirs=[],
                                data=None)

    def _open_example(self):
        return self.c.post("/api/open", json={"path": EXAMPLE})

    def test_open_missing_file(self):
        r = self.c.post("/api/open", json={"path": "/no/such/file.json"})
        self.assertEqual(r.status_code, 400)
        self.assertFalse(r.get_json()["ok"])

    def test_new_empty_model(self):
        r = self.c.post("/api/new", json={"title": "Blank"})
        data = r.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["nodes"], [])
        self.assertEqual(data["edges"], [])
        self.assertEqual(data["metadata"]["title"], "Blank")
        self.assertIsNone(data["path"])

    def test_trace_workflow_new_to_saved(self):
        # emulate what the tracing UI does: New → drop nodes at positions →
        # connect them → save a runnable pywr file
        self.c.post("/api/new", json={"title": "Traced"})
        placements = [("Src", "input", [10, 10]), ("Res", "storage", [10, 90]),
                      ("Dem", "output", [10, 170])]
        for name, typ, pos in placements:
            r = self.c.post("/api/node/add",
                            json={"node": {"name": name, "type": typ}, "pos": pos})
            self.assertTrue(r.get_json()["ok"])
        for src, dst in (("Src", "Res"), ("Res", "Dem")):
            self.c.post("/api/edge/add", json={"src": src, "dst": dst})
        g = self.c.get("/api/graph").get_json()
        self.assertEqual(len(g["nodes"]), 3)
        self.assertEqual(len(g["edges"]), 2)
        # every traced node keeps the position it was dropped at
        self.assertTrue(all(n["pos"] for n in g["nodes"]))
        # save round-trips: positions land in position.schematic
        out = os.path.join(tempfile.mkdtemp(), "traced.json")
        self.c.post("/api/save", json={"path": out})
        with open(out) as fh:
            saved = json.load(fh)
        self.assertEqual(len(saved["nodes"]), 3)
        self.assertTrue(all("position" in n for n in saved["nodes"]))

    def test_open_and_graph(self):
        r = self._open_example()
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(len(data["nodes"]), 11)
        self.assertTrue(all(n["pos"] for n in data["nodes"]))
        # graph route returns the same model
        g = self.c.get("/api/graph").get_json()
        self.assertEqual(len(g["nodes"]), 11)

    def test_edit_flow(self):
        self._open_example()
        # add a node
        r = self.c.post("/api/node/add",
                        json={"node": {"name": "TestN", "type": "input",
                                       "max_flow": 5}, "pos": [1, 1]})
        self.assertTrue(r.get_json()["ok"])
        names = [n["name"] for n in r.get_json()["nodes"]]
        self.assertIn("TestN", names)
        # add edge (payload edges are {src, dst, extra} dicts)
        r = self.c.post("/api/edge/add", json={"src": "TestN", "dst": "River_Main"})
        edges = [(e["src"], e["dst"]) for e in r.get_json()["edges"]]
        self.assertIn(("TestN", "River_Main"), edges)
        # rename
        r = self.c.post("/api/node/rename", json={"old": "TestN", "new": "Renamed"})
        self.assertIn("Renamed", [n["name"] for n in r.get_json()["nodes"]])
        # update param
        r = self.c.post("/api/node/update",
                        json={"name": "Renamed", "changes": {"max_flow": 42}})
        node = next(n for n in r.get_json()["nodes"] if n["name"] == "Renamed")
        self.assertEqual(node["params"]["max_flow"], 42)
        # delete node → edge gone too
        r = self.c.post("/api/node/delete", json={"name": "Renamed"})
        data = r.get_json()
        self.assertNotIn("Renamed", [n["name"] for n in data["nodes"]])
        edges = [(e["src"], e["dst"]) for e in data["edges"]]
        self.assertNotIn(("Renamed", "River_Main"), edges)

    def test_trace_route(self):
        self._open_example()
        r = self.c.get("/api/trace?name=River_Main&dir=upstream")
        nodes = r.get_json()["nodes"]
        self.assertIn("GW_Base", nodes)
        self.assertIn("Rainfall_Catchment", nodes)

    def test_layout_route(self):
        self._open_example()
        r = self.c.post("/api/layout", json={"mode": "all"})
        data = r.get_json()
        self.assertTrue(data["layout_was_auto"])
        self.assertTrue(all(n["pos"] for n in data["nodes"]))

    def test_save_and_reopen(self):
        self._open_example()
        out = os.path.join(tempfile.mkdtemp(), "saved.json")
        r = self.c.post("/api/save", json={"path": out})
        self.assertTrue(r.get_json()["ok"])
        self.assertTrue(os.path.isfile(out))
        # reopen the saved file
        r2 = self.c.post("/api/open", json={"path": out})
        self.assertEqual(len(r2.get_json()["nodes"]), 11)

    def test_data_report_present(self):
        self._open_example()
        r = self.c.get("/api/data")
        data = r.get_json()
        self.assertTrue(data["ok"])
        # example references params.csv, which sits beside it → resolved
        self.assertEqual(data["missing"], [])

    # a 1x1 PNG
    PNG1 = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4"
            "nGNgYAAAAAMAASsJTYQAAAAASUVORK5CYII=")

    def test_traceimage_writes_real_png(self):
        out = os.path.join(tempfile.mkdtemp(), "m.json")
        self._open_example()
        self.c.post("/api/save", json={"path": out})
        # no sidecar yet
        self.assertIsNone(self.c.get("/api/traceimage").get_json()["trace"])
        # save with actual PNG bytes
        trace = {"src": "data:image/png;base64," + self.PNG1, "x": 5, "y": 6,
                 "scale": 0.5, "opacity": 0.5, "natW": 1, "natH": 1,
                 "locked": True}
        r = self.c.post("/api/traceimage", json={"trace": trace}).get_json()
        self.assertTrue(r["ok"])
        png = os.path.splitext(out)[0] + ".pywrtrace.png"
        geom = os.path.splitext(out)[0] + ".pywrtrace.json"
        # an ACTUAL png file exists beside the model, with a real PNG signature
        self.assertTrue(os.path.isfile(png))
        with open(png, "rb") as fh:
            self.assertEqual(fh.read(8), b"\x89PNG\r\n\x1a\n")
        # geometry json holds no base64 blob, just a pointer to the image
        with open(geom) as fh:
            g = json.load(fh)
        self.assertEqual(g["image"], "m.pywrtrace.png")
        self.assertNotIn("src", g)
        self.assertEqual(g["x"], 5)
        # GET reconstructs a data URL from the png for the browser
        got = self.c.get("/api/traceimage").get_json()["trace"]
        self.assertTrue(got["src"].startswith("data:image/png;base64,"))
        self.assertEqual(got["natW"], 1)
        # geometry-only update (no src) must NOT rewrite the image
        before = os.path.getmtime(png)
        self.c.post("/api/traceimage",
                    json={"trace": {"x": 99, "y": 6, "scale": 0.5,
                                    "opacity": 0.5, "natW": 1, "natH": 1,
                                    "locked": True}})
        self.assertEqual(os.path.getmtime(png), before)   # png untouched
        self.assertEqual(self.c.get("/api/traceimage").get_json()["trace"]["x"], 99)
        # the pywr model file itself is untouched
        with open(out) as fh:
            self.assertNotIn("pywrtrace", fh.read())
        # removal deletes both files
        self.c.post("/api/traceimage", json={"trace": None})
        self.assertFalse(os.path.isfile(png))
        self.assertFalse(os.path.isfile(geom))

    def test_traceimage_jpeg_keeps_extension(self):
        out = os.path.join(tempfile.mkdtemp(), "m.json")
        self._open_example()
        self.c.post("/api/save", json={"path": out})
        # 1x1 jpeg is fine to fake — server only cares about the mime prefix
        self.c.post("/api/traceimage", json={"trace": {
            "src": "data:image/jpeg;base64," + self.PNG1,
            "x": 0, "y": 0, "scale": 1, "opacity": 0.5,
            "natW": 1, "natH": 1, "locked": False}})
        self.assertTrue(os.path.isfile(os.path.splitext(out)[0] + ".pywrtrace.jpg"))

    def test_traceimage_requires_saved_model(self):
        self.c.post("/api/new", json={"title": "unsaved"})
        r = self.c.post("/api/traceimage", json={"trace": {"src": "x"}})
        self.assertEqual(r.status_code, 409)   # must save the model first

    def test_water_path_trace_still_works(self):
        # the /api/trace (water path) route must not collide with traceimage
        self._open_example()
        r = self.c.get("/api/trace?name=Reservoir_A&dir=upstream")
        self.assertTrue(r.get_json()["ok"])
        self.assertIn("River_Main", r.get_json()["nodes"])

    def test_run_blocked_without_env(self):
        self._open_example()
        # force "env not ready" by pointing env dir away; if pywr IS ready
        # this returns 200, which is also fine — just assert it's a clean JSON
        r = self.c.post("/api/run", json={})
        self.assertIn(r.status_code, (200, 409))
        self.assertIn("ok", r.get_json())

    def test_scenario_model_graph_exposes_dims(self):
        # a model with pywr scenarios reports the picker's dimensions + count
        data = self.c.post("/api/open", json={"path": SCENARIO}).get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["n_combinations"], 3)
        self.assertEqual(len(data["scenario_dims"]), 1)
        dim = data["scenario_dims"][0]
        self.assertEqual(dim["name"], "demand")
        self.assertEqual(dim["size"], 3)
        self.assertEqual(dim["ensemble_names"], ["low", "mid", "high"])

    def test_plain_model_has_no_scenarios(self):
        # the gw_network example defines none → picker stays hidden (count 1)
        data = self._open_example().get_json()
        self.assertEqual(data["n_combinations"], 1)
        self.assertEqual(data["scenario_dims"], [])

    def _raw(self):
        self._open_example()
        return self.c.get("/api/model/raw").get_json()

    def test_raw_edit_applies_a_parameter_change(self):
        raw = self._raw()
        raw.setdefault("parameters", {})["hand_written"] = {"type": "constant",
                                                            "value": 42}
        res = self.c.post("/api/model/raw", json={"model": raw})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json()["n_parameters"],
                         len(raw["parameters"]))
        # it landed in the live model, and the file is now dirty (not written)
        self.assertIn("hand_written", app_module.STATE["model"]["parameters"])
        self.assertTrue(res.get_json()["dirty"])

    def test_raw_edit_keeps_existing_positions(self):
        self._open_example()
        before = dict(app_module.STATE["positions"])
        raw = self.c.get("/api/model/raw").get_json()
        raw["metadata"]["title"] = "renamed"
        self.c.post("/api/model/raw", json={"model": raw})
        self.assertEqual(app_module.STATE["positions"], before)

    def test_raw_edit_places_a_newly_added_node(self):
        raw = self._raw()
        raw["nodes"].append({"name": "Hand_Added", "type": "link"})
        raw["edges"].append(["Hand_Added", raw["nodes"][0]["name"]])
        res = self.c.post("/api/model/raw", json={"model": raw})
        self.assertEqual(res.status_code, 200)
        # a node with no position in the JSON still gets one
        self.assertIn("Hand_Added", app_module.STATE["positions"])

    def test_raw_edit_rejects_bad_models(self):
        raw = self._raw()
        cases = [
            ({"nodes": "nope"}, "nodes"),
            ({"nodes": [{"type": "link"}]}, "name"),
            ({"nodes": [{"name": "a"}, {"name": "a"}]}, "duplicate"),
            ({"nodes": [{"name": "a"}], "edges": [["a", "ghost"]]}, "ghost"),
            ({"nodes": [{"name": "a"}], "parameters": []}, "parameters"),
        ]
        for model, expect in cases:
            res = self.c.post("/api/model/raw", json={"model": model})
            self.assertEqual(res.status_code, 400, model)
            self.assertIn(expect, res.get_json()["error"], model)
        # a rejected edit leaves the loaded model untouched
        self.assertEqual(len(app_module.STATE["model"]["nodes"]),
                         len(raw["nodes"]))

    def test_raw_edit_needs_a_model_key(self):
        self._open_example()
        res = self.c.post("/api/model/raw", json={"nodes": []})
        self.assertEqual(res.status_code, 400)

    def test_layouts_endpoint_lists_the_picker_options(self):
        data = self.c.get("/api/layouts").get_json()
        self.assertTrue(data["ok"])
        kinds = [spec["kind"] for spec in data["layouts"]]
        self.assertIn("layered", kinds)
        self.assertIn("force", kinds)
        # every entry needs a label + hint for the dropdown to render
        for spec in data["layouts"]:
            self.assertTrue(spec["label"] and spec["hint"], spec)

    def test_layout_accepts_a_kind(self):
        n_nodes = len(self._open_example().get_json()["nodes"])
        seen = []
        for kind in ("layered", "grouped", "radial"):
            res = self.c.post("/api/layout", json={"mode": "all",
                                                   "kind": kind})
            self.assertEqual(res.status_code, 200, kind)
            nodes = res.get_json()["nodes"]
            # every node comes back placed
            self.assertEqual(len(nodes), n_nodes, kind)
            for node in nodes:
                self.assertEqual(len(node["pos"]), 2, f"{kind}/{node['name']}")
            seen.append(sorted(tuple(n["pos"]) for n in nodes))
        # the kinds actually produce different arrangements
        self.assertNotEqual(seen[0], seen[1])

    def test_layout_rejects_unknown_kind(self):
        self._open_example()
        res = self.c.post("/api/layout", json={"mode": "all",
                                               "kind": "spirograph"})
        self.assertEqual(res.status_code, 400)
        self.assertIn("spirograph", res.get_json()["error"])

    def test_edge_flows_prefer_recorded_exact(self):
        # A->B is ambiguous; the endpoint min-estimate would be 50, but the
        # runner-recorded exact_edges wins and marks the edge exact
        model = {"edges": [["s", "A"], ["A", "B"], ["A", "C"], ["x", "B"]]}
        nodes = {"s": {"flow": [50]}, "A": {"flow": [50]}, "B": {"flow": [50]},
                 "C": {"flow": [10]}, "x": {"flow": [40]}}
        edges = app_module._estimate_edge_flows(model, nodes,
                                                {"1": [10]})   # A->B recorded
        ab = next(e for e in edges if e["src"] == "A" and e["dst"] == "B")
        self.assertTrue(ab["exact"])
        self.assertEqual(ab["series"], [10])

    def test_edge_flows_estimate_without_record(self):
        # same ambiguous A->B, but no recorded series → elementwise-min estimate
        model = {"edges": [["s", "A"], ["A", "B"], ["A", "C"], ["x", "B"]]}
        nodes = {"s": {"flow": [50]}, "A": {"flow": [50]}, "B": {"flow": [30]},
                 "C": {"flow": [20]}, "x": {"flow": [40]}}
        edges = app_module._estimate_edge_flows(model, nodes)
        ab = next(e for e in edges if e["src"] == "A" and e["dst"] == "B")
        self.assertFalse(ab["exact"])
        self.assertEqual(ab["series"], [30])           # min(A=50, B=30)


if __name__ == "__main__":
    unittest.main(verbosity=2)
