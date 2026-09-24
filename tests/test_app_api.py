"""API-level tests using Flask's test client (no network, no browser).

    ./.venv/bin/python -m unittest discover -s tests -v

These exercise the open → edit → save → layout flow end to end against the
real routes. The example model under examples/ is used as a fixture.
"""

import json
import os
import pathlib
import socket
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import app as app_module  # noqa: E402
from pywr_reader.api import files, runs  # noqa: E402

EXAMPLE = os.path.join(ROOT, "examples", "gw_network", "pywr_model.json")


class TestApi(unittest.TestCase):
    def setUp(self):
        app_module.app.testing = True
        self.c = app_module.app.test_client()
        # reset shared state between tests
        app_module.WORKSPACE.reset()

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

    def test_plain_model_has_no_scenarios(self):
        # the gw_network example defines none → picker stays hidden (count 1)
        data = self._open_example().get_json()
        self.assertEqual(data["n_combinations"], 1)
        self.assertEqual(data["scenario_dims"], [])

    def test_data_preview_only_serves_this_models_data_files(self):
        # not an arbitrary file reader: only what the open model references
        self._open_example()
        r = self.c.get("/api/data/preview?path=/etc/passwd")
        self.assertEqual(r.status_code, 403)
        self.assertIn("not one of this model", r.get_json()["error"])

    def test_data_preview_refuses_before_a_model_is_open(self):
        r = self.c.get("/api/data/preview?path=/anything.h5")
        self.assertEqual(r.status_code, 403)

    def test_data_series_shares_the_same_access_guard(self):
        # the plot endpoint is no more of a file reader than the preview one
        self._open_example()
        r = self.c.get("/api/data/series?path=/etc/hosts")
        self.assertEqual(r.status_code, 403)

    def test_data_series_without_a_key_is_allowed(self):
        # a csv has no key — the plot must still be able to request it
        self._open_example()
        resolved = [i["resolved"] for i in
                    self.c.get("/api/data").get_json()["report"]]
        r = self.c.get("/api/data/series?path=" + resolved[0])   # no &key
        self.assertNotEqual(r.status_code, 403)                  # not refused
        # 200 if pywr is set up, 409 if not — either way it got past the guard

    def test_data_series_rejects_non_integer_window(self):
        self._open_example()
        resolved = self.c.get("/api/data").get_json()["report"][0]["resolved"]
        r = self.c.get("/api/data/series?path=" + resolved
                       + "&start=x&stop=y")
        self.assertEqual(r.status_code, 400)

    def test_data_preview_allows_a_referenced_file(self):
        # params.csv sits beside the example and is referenced by it, so it
        # passes the allow-list — it then needs the pywr env to actually read
        self._open_example()
        resolved = [i["resolved"] for i in
                    self.c.get("/api/data").get_json()["report"]]
        self.assertTrue(resolved, "example should reference a data file")
        r = self.c.get("/api/data/preview?path=" + resolved[0])
        # allowed through: either it read it, or it said pywr isn't set up —
        # never the 403 refusal
        self.assertIn(r.status_code, (200, 409, 400))
        self.assertNotEqual(r.status_code, 403)

    # -- getting results out ----------------------------------------------
    def _fake_run(self, label="run 1"):
        """A finished run in memory, without needing pywr."""
        rid = "test1234"
        app_module.RUNS.by_id[rid] = {
            "id": rid, "status": "done", "label": label,
            "dates": ["2000-01-01", "2000-01-02"],
            "nodes": {"Res": {"volume": [10.0, 11.0]},
                      "Dem": {"flow": [1.0, 2.0]}},
            "edges": [{"src": "Res", "dst": "Dem", "series": [1.0, 2.0],
                       "exact": True},
                      {"src": "A", "dst": "B", "series": [3.0, 4.0],
                       "exact": False}],
            "meta": {"solver": "glpk"}, "warnings": [], "overrides": None,
            "started_at": 0, "max_edge_flow": 4.0,
        }
        app_module.RUNS.order.append(rid)
        return rid

    def tearDown(self):
        app_module.RUNS.clear()
        app_module.RUNS.order.clear()

    def test_whole_run_csv_has_every_node_and_edge(self):
        rid = self._fake_run()
        r = self.c.get(f"/api/run/{rid}/csv")
        self.assertEqual(r.status_code, 200)
        self.assertIn("attachment", r.headers["Content-Disposition"])
        # exactly one charset — Flask appends its own to a text/* mimetype
        self.assertEqual(r.headers["Content-Type"].count("charset"), 1)
        rows = r.data.decode("utf-8-sig").splitlines()
        header = rows[0].split(",")
        self.assertEqual(header[0], "date")
        self.assertIn("Res (volume)", header)
        self.assertIn("Dem (flow)", header)
        self.assertIn("Res -> Dem (flow)", header)
        # an estimated edge is labelled, so it is never mistaken for recorded
        self.assertIn("A -> B (flow) [estimated]", header)
        self.assertEqual(len(rows), 3)                    # header + 2 dates
        self.assertTrue(rows[1].startswith("2000-01-01,"))

    def test_node_csv_has_a_column_per_compared_run(self):
        rid = self._fake_run("base")
        other = "test5678"
        app_module.RUNS.by_id[other] = dict(app_module.RUNS.by_id[rid], id=other,
                                      label="what-if")
        app_module.RUNS.order.append(other)
        r = self.c.get(f"/api/run/{rid}/node.csv?node=Res&compare={other}")
        rows = r.data.decode("utf-8-sig").splitlines()
        self.assertEqual(rows[0], "date,base (volume),what-if (volume)")
        self.assertEqual(rows[1], "2000-01-01,10.0,10.0")
        self.assertIn('filename="Res.csv"', r.headers["Content-Disposition"])

    def test_node_csv_for_an_unknown_node_is_404(self):
        rid = self._fake_run()
        self.assertEqual(self.c.get(f"/api/run/{rid}/node.csv?node=Ghost")
                         .status_code, 404)

    def test_csv_of_an_unfinished_run_is_404(self):
        app_module.RUNS.by_id["pending"] = {"id": "pending", "status": "running",
                                      "label": "x"}
        self.assertEqual(self.c.get("/api/run/pending/csv").status_code, 404)

    def test_save_run_then_reopen_it_after_a_restart(self):
        rid = self._fake_run("baseline")
        out = os.path.join(tempfile.mkdtemp(), "r.pywrrun.json")
        res = self.c.post(f"/api/run/{rid}/save", json={"path": out}).get_json()
        self.assertTrue(res["ok"])
        self.assertTrue(os.path.isfile(out))

        app_module.RUNS.clear()          # the app restarts; memory is gone
        app_module.RUNS.order.clear()
        opened = self.c.post("/api/run/open", json={"path": out}).get_json()
        self.assertTrue(opened["ok"])
        run = app_module.RUNS.by_id[opened["run_id"]]
        self.assertEqual(run["label"], "baseline")
        self.assertEqual(run["status"], "done")
        self.assertEqual(run["dates"], ["2000-01-01", "2000-01-02"])
        self.assertEqual(run["max_edge_flow"], 4.0)     # recomputed on load
        # and it serves like any other run
        self.assertEqual(self.c.get(f"/api/run/{opened['run_id']}")
                         .get_json()["n_steps"], 2)

    def test_save_run_defaults_to_a_sidecar_beside_the_model(self):
        out = os.path.join(tempfile.mkdtemp(), "m.json")
        self._open_example()
        self.c.post("/api/save", json={"path": out})
        rid = self._fake_run("what-if 8")
        res = self.c.post(f"/api/run/{rid}/save", json={}).get_json()
        # label is made filename-safe, beside the model, .pywrrun.json
        self.assertEqual(res["path"],
                         os.path.join(os.path.dirname(out),
                                      "m.what-if-8.pywrrun.json"))

    def test_open_run_rejects_a_file_that_is_not_a_run(self):
        r = self.c.post("/api/run/open", json={"path": EXAMPLE})
        self.assertEqual(r.status_code, 400)
        self.assertIn("not a saved", r.get_json()["error"])

    def test_open_run_rejects_a_missing_file(self):
        r = self.c.post("/api/run/open", json={"path": "/no/such/run.json"})
        self.assertEqual(r.status_code, 400)

    def test_stale_run_snapshots_are_swept(self):
        # a force-quit leaves the snapshot beside the model; the next run
        # clears the orphans but must not touch one still in flight
        tmp = tempfile.mkdtemp()
        orphan = os.path.join(tmp, runs.RUN_TMP_PREFIX + "dead.json")
        live = os.path.join(tmp, runs.RUN_TMP_PREFIX + "alive.json")
        for p in (orphan, live):
            open(p, "w").close()
        app_module.RUNS.by_id["alive"] = {"id": "alive", "status": "running",
                                    "label": "x"}
        runs.sweep_run_temps(tmp)
        self.assertFalse(os.path.exists(orphan), "orphan not swept")
        self.assertTrue(os.path.exists(live), "swept a run in flight")

    # -- the Open dialog's file browser, on every platform -----------------
    def test_browse_lists_dirs_and_models_with_full_paths(self):
        tmp = tempfile.mkdtemp()
        os.mkdir(os.path.join(tmp, "sub"))
        for name in ("m.json", "v.tcm", "nodes.csv", "notes.txt"):
            open(os.path.join(tmp, name), "w").close()
        data = self.c.get("/api/browse?path=" + tmp).get_json()
        self.assertTrue(data["ok"])
        names = [e["name"] for e in data["entries"]]
        self.assertIn("sub", names)
        self.assertIn("m.json", names)
        self.assertNotIn("notes.txt", names)      # only openable types
        # every entry carries a server-joined path, so the browser never has
        # to guess the separator (a "/" join breaks on Windows)
        for entry in data["entries"]:
            self.assertEqual(entry["path"], os.path.join(tmp, entry["name"]))

    def test_browse_offers_roots_for_this_platform(self):
        data = self.c.get("/api/browse").get_json()
        self.assertTrue(data["roots"], "no roots offered")
        for root in data["roots"]:
            self.assertTrue(root["label"] and root["path"], root)
            self.assertTrue(os.path.isdir(root["path"]), root)
        self.assertEqual(data["roots"][0]["label"], "Home")

    def test_browse_roots_on_windows_are_the_drives(self):
        # can't run this on Windows here, so pin the behaviour by simulating it:
        # Windows must be offered its drives, never a hard-coded /Volumes
        drives = {"C:\\", "D:\\"}
        with mock.patch.object(files.os, "name", "nt"), \
             mock.patch.object(files.os.path, "isdir",
                               side_effect=lambda p: p in drives):
            roots = files.browse_roots()
        labels = [r["label"] for r in roots]
        self.assertEqual(labels[0], "Home")
        self.assertIn("C:\\", labels)
        self.assertIn("D:\\", labels)
        self.assertNotIn("Volumes", labels)

    def test_windows_drives_come_from_the_bitmask_not_by_probing(self):
        # probing A:\ … Z:\ with isdir stalls on a disconnected network drive
        # and spins up empty optical/card readers; Windows keeps a bitmask
        fake = types.SimpleNamespace(windll=types.SimpleNamespace(
            kernel32=types.SimpleNamespace(
                GetLogicalDrives=lambda: (1 << 2) | (1 << 25))))  # C: and Z:
        probed = []
        with mock.patch.dict(sys.modules, {"ctypes": fake}), \
             mock.patch.object(files.os.path, "isdir",
                               side_effect=lambda p: probed.append(p) or True):
            self.assertEqual(files.windows_drives(), ["C:\\", "Z:\\"])
        self.assertEqual(probed, [], "drive letters were probed anyway")

    def test_windows_drives_fall_back_to_probing_without_ctypes(self):
        drives = {"C:\\", "E:\\"}
        with mock.patch.dict(sys.modules, {"ctypes": None}), \
             mock.patch.object(files.os.path, "isdir",
                               side_effect=lambda p: p in drives):
            self.assertEqual(files.windows_drives(), ["C:\\", "E:\\"])

    def test_no_root_is_labelled_with_the_users_folder_name(self):
        # the shortcuts are places ("Home", "C:\\", "Volumes"), never a path.
        # A root labelled C:\Users\<name> would read as a drive called <name>.
        with mock.patch.object(files.os.path, "expanduser",
                               return_value=os.path.join("C:\\", "Users", "Ada")):
            roots = files.browse_roots()
        self.assertEqual(roots[0], {"label": "Home",
                                    "path": os.path.join("C:\\", "Users", "Ada")})
        for root in roots:
            self.assertNotIn("Ada", root["label"], root)

    def test_browse_roots_on_mac_include_volumes(self):
        with mock.patch.object(files.os, "name", "posix"), \
             mock.patch.object(files.os.path, "isdir",
                               side_effect=lambda p: p == "/Volumes"):
            roots = files.browse_roots()
        labels = [r["label"] for r in roots]
        self.assertIn("Volumes", labels)
        self.assertNotIn("C:\\", labels)

    def test_browse_has_no_parent_at_a_filesystem_root(self):
        # dirname("/") is "/" — a ".." there would just loop
        root = os.path.abspath(os.sep)
        self.assertIsNone(self.c.get("/api/browse?path=" + root)
                          .get_json()["parent"])

    def test_browse_expands_a_tilde(self):
        data = self.c.get("/api/browse?path=~").get_json()
        self.assertEqual(data["path"], os.path.expanduser("~"))

    def test_browse_rejects_a_non_directory(self):
        r = self.c.get("/api/browse?path=" + EXAMPLE)
        self.assertEqual(r.status_code, 400)
        self.assertIn("not a directory", r.get_json()["error"])

    def test_vendored_pdfjs_is_present_and_served(self):
        # PDF trace backgrounds need pdf.js vendored locally (not a CDN) so the
        # app stays offline. If either file goes missing, opening a PDF fails
        # with a network error the user can't fix — catch that here.
        for name in ("pdf.min.mjs", "pdf.worker.min.mjs"):
            path = os.path.join(ROOT, "static", "vendor", "pdfjs", name)
            self.assertTrue(os.path.isfile(path), f"missing vendored {name}")
            r = self.c.get(f"/static/vendor/pdfjs/{name}")
            self.assertEqual(r.status_code, 200, name)

    def test_pdfimport_points_at_the_vendored_worker(self):
        # the worker path is a bare string, so a moved file wouldn't fail a
        # test unless we pin it: the module must name the file that exists
        src = pathlib.Path(ROOT, "static", "pdfimport.js").read_text(
            encoding="utf-8")
        self.assertIn("/static/vendor/pdfjs/pdf.worker.min.mjs", src)

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
        self.assertIn("hand_written", app_module.WORKSPACE.model["parameters"])
        self.assertTrue(res.get_json()["dirty"])

    def test_raw_edit_keeps_existing_positions(self):
        self._open_example()
        before = dict(app_module.WORKSPACE.positions)
        raw = self.c.get("/api/model/raw").get_json()
        raw["metadata"]["title"] = "renamed"
        self.c.post("/api/model/raw", json={"model": raw})
        self.assertEqual(app_module.WORKSPACE.positions, before)

    def test_raw_edit_places_a_newly_added_node(self):
        raw = self._raw()
        raw["nodes"].append({"name": "Hand_Added", "type": "link"})
        raw["edges"].append(["Hand_Added", raw["nodes"][0]["name"]])
        res = self.c.post("/api/model/raw", json={"model": raw})
        self.assertEqual(res.status_code, 200)
        # a node with no position in the JSON still gets one
        self.assertIn("Hand_Added", app_module.WORKSPACE.positions)

    def test_raw_edit_rename_rewrites_references(self):
        raw = self._raw()
        old = raw["edges"][0][0]
        new = old + "_renamed"
        for node in raw["nodes"]:
            if node["name"] == old:
                node["name"] = new        # rename the node only, as a hand edit
        res = self.c.post("/api/model/raw",
                          json={"model": raw, "renames": {old: new}})
        self.assertEqual(res.status_code, 200, res.get_json())
        model = app_module.WORKSPACE.model
        # no edge still points at the old name
        self.assertFalse(any(old in e for e in model["edges"]))
        self.assertTrue(any(new in e for e in model["edges"]))
        # and the rewrite is reported back
        self.assertTrue(res.get_json()["warnings"])

    def test_raw_edit_rename_keeps_the_node_where_it_was(self):
        self._open_example()
        raw = self.c.get("/api/model/raw").get_json()
        old = raw["nodes"][0]["name"]
        new = "Moved_Check"
        was = list(app_module.WORKSPACE.positions[old])
        raw["nodes"][0]["name"] = new
        raw["nodes"][0].pop("position", None)   # position only in app state
        self.c.post("/api/model/raw", json={"model": raw,
                                            "renames": {old: new}})
        # the renamed node keeps its spot instead of being re-placed
        self.assertNotIn(old, app_module.WORKSPACE.positions)
        self.assertEqual(app_module.WORKSPACE.positions[new], was)

    def test_raw_edit_rename_onto_an_existing_name_is_rejected(self):
        raw = self._raw()
        old, clash = raw["nodes"][0]["name"], raw["nodes"][1]["name"]
        raw["nodes"][0]["name"] = clash
        res = self.c.post("/api/model/raw",
                          json={"model": raw, "renames": {old: clash}})
        self.assertEqual(res.status_code, 400)
        self.assertIn("duplicate", res.get_json()["error"])

    def test_raw_edit_rejects_bad_renames(self):
        raw = self._raw()
        for bad in (["a"], "a", {"a": ""}, {"a": 3}):
            res = self.c.post("/api/model/raw",
                              json={"model": raw, "renames": bad})
            self.assertEqual(res.status_code, 400, bad)

    def test_raw_edit_treats_an_empty_renames_as_none(self):
        # falsy values just mean "nothing was renamed" — not an error
        raw = self._raw()
        for empty in (None, {}, []):
            res = self.c.post("/api/model/raw",
                              json={"model": raw, "renames": empty})
            self.assertEqual(res.status_code, 200, empty)

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
        self.assertEqual(len(app_module.WORKSPACE.model["nodes"]),
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
        edges = runs.estimate_edge_flows(model, nodes,
                                                {"1": [10]})   # A->B recorded
        ab = next(e for e in edges if e["src"] == "A" and e["dst"] == "B")
        self.assertTrue(ab["exact"])
        self.assertEqual(ab["series"], [10])

    def test_edge_flows_estimate_without_record(self):
        # same ambiguous A->B, but no recorded series → elementwise-min estimate
        model = {"edges": [["s", "A"], ["A", "B"], ["A", "C"], ["x", "B"]]}
        nodes = {"s": {"flow": [50]}, "A": {"flow": [50]}, "B": {"flow": [30]},
                 "C": {"flow": [20]}, "x": {"flow": [40]}}
        edges = runs.estimate_edge_flows(model, nodes)
        ab = next(e for e in edges if e["src"] == "A" and e["dst"] == "B")
        self.assertFalse(ab["exact"])
        self.assertEqual(ab["series"], [30])           # min(A=50, B=30)


class TestBootstrap(unittest.TestCase):
    """`python app.py` sets itself up on first run, so nobody has to remember
    the venv/pip dance. The end-to-end path (create .venv, install, re-exec)
    needs a network and a Python without Flask, so it isn't run here — these
    pin the pieces that decide *what* it would do."""

    def test_bootstrap_is_a_no_op_when_flask_is_importable(self):
        # the whole suite imports app, so this must never shell out
        self.assertTrue(app_module.has_flask())
        with mock.patch.object(app_module.subprocess, "run") as run:
            self.assertIsNone(app_module.bootstrap())
        run.assert_not_called()

    def test_venv_python_path_per_platform(self):
        with mock.patch.object(app_module.os, "name", "nt"):
            self.assertEqual(app_module.venv_python("V"),
                             os.path.join("V", "Scripts", "python.exe"))
        with mock.patch.object(app_module.os, "name", "posix"):
            self.assertEqual(app_module.venv_python("V"),
                             os.path.join("V", "bin", "python"))

    def test_manual_steps_use_this_platforms_spelling(self):
        # the fallback message is only useful if it can be copy-pasted
        with mock.patch.object(app_module.os, "name", "nt"):
            steps = "\n".join(app_module.manual_steps())
        self.assertIn(r".venv\Scripts\python app.py", steps)
        self.assertNotIn("./.venv", steps)
        with mock.patch.object(app_module.os, "name", "posix"):
            steps = "\n".join(app_module.manual_steps())
        self.assertIn("./.venv/bin/python app.py", steps)

    def test_has_flask_reports_on_another_interpreter(self):
        self.assertTrue(app_module.has_flask(sys.executable))
        # a path that isn't an interpreter must be False, not an exception
        self.assertFalse(app_module.has_flask(os.path.join(ROOT, "README.md")))

    def test_the_readme_badge_matches_the_enforced_minimum(self):
        # app.py refuses to start below MIN_PYTHON; the badge must say the same
        readme = pathlib.Path(ROOT, "README.md").read_text(encoding="utf-8")
        major, minor = app_module.MIN_PYTHON
        self.assertIn(f"Python-{major}.{minor}+", readme)
        self.assertIn(f"Python {major}.{minor} or newer", readme)


class TestLaunchers(unittest.TestCase):
    """The double-click launchers, and the start-up checks behind them."""

    MAC = "Start PyWR Reader.command"
    WIN = "Start PyWR Reader.bat"

    def test_both_launchers_exist_and_start_the_app_with_a_browser(self):
        for name in (self.MAC, self.WIN):
            path = pathlib.Path(ROOT, name)
            self.assertTrue(path.is_file(), f"missing {name}")
            self.assertIn("app.py --open", path.read_text(encoding="utf-8"),
                          f"{name} does not start the app with --open")

    def test_the_mac_launcher_is_executable_in_the_repository(self):
        # a .command without the exec bit does nothing when double-clicked, and
        # this repo has core.fileMode=false, so chmod alone is not recorded —
        # the mode has to be right in git's index
        out = subprocess.run(["git", "ls-files", "-s", self.MAC],
                             cwd=ROOT, capture_output=True, text=True).stdout
        self.assertTrue(out.startswith("100755"),
                        f"{self.MAC} is not executable in git: {out.strip()!r}")

    def test_the_launcher_changes_into_its_own_folder(self):
        # double-clicked, the working directory is wherever the user was
        self.assertIn('cd "$(dirname "$0")"',
                      pathlib.Path(ROOT, self.MAC).read_text(encoding="utf-8"))
        self.assertIn('cd /d "%~dp0"',
                      pathlib.Path(ROOT, self.WIN).read_text(encoding="utf-8"))

    def test_port_in_use_sees_a_listening_socket(self):
        with socket.socket() as server:
            server.bind(("127.0.0.1", 0))
            server.listen(1)
            port = server.getsockname()[1]
            self.assertTrue(app_module.port_in_use(port))
        self.assertFalse(app_module.port_in_use(port))   # closed again

    def test_open_when_ready_waits_for_the_server_then_opens(self):
        with socket.socket() as server:
            server.bind(("127.0.0.1", 0))
            server.listen(1)
            port = server.getsockname()[1]
            with mock.patch.object(app_module.webbrowser, "open") as opened:
                app_module.open_when_ready("http://here", port, tries=1, delay=0)
        opened.assert_called_once_with("http://here")

    def test_is_pywr_reader_is_false_when_nothing_answers(self):
        with socket.socket() as probe:      # grab then release a free port
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        self.assertFalse(app_module.is_pywr_reader(f"http://127.0.0.1:{port}",
                                                  timeout=0.3))


class TestExampleModel(unittest.TestCase):
    """The empty state offers the bundled demo, because a packaged build
    unpacks it where nobody could browse to it."""

    def setUp(self):
        app_module.app.testing = True
        self.c = app_module.app.test_client()

    def test_the_example_is_reported_and_openable(self):
        path = self.c.get("/api/example").get_json()["path"]
        self.assertTrue(os.path.isfile(path), path)
        opened = self.c.post("/api/open", json={"path": path}).get_json()
        self.assertEqual(len(opened["nodes"]), 11)
        # its data file has to come along, or the demo can't be run
        self.assertEqual(opened["data"]["missing"], [])

    def test_a_packaged_build_copies_the_example_somewhere_it_survives(self):
        # bundled data lives in a temp folder that is deleted on exit; opening
        # the example from there and saving would lose the work
        with tempfile.TemporaryDirectory() as bundle, \
             tempfile.TemporaryDirectory() as beside:
            demo = os.path.join(bundle, "examples", "gw_network")
            os.makedirs(demo)
            for name in ("pywr_model.json", "params.csv", "._pywr_model.json"):
                pathlib.Path(demo, name).write_text("x", encoding="utf-8")
            exe = os.path.join(beside, "PyWR Reader")
            with mock.patch.object(files, "APP_DIR", bundle), \
                 mock.patch.object(files.sys, "frozen", True, create=True), \
                 mock.patch.object(files.sys, "executable", exe):
                got = files.example_path()
            self.assertEqual(got, os.path.join(beside, "examples", "gw_network",
                                               "pywr_model.json"))
            self.assertTrue(os.path.isfile(got), "not copied out of the bundle")
            copied = os.listdir(os.path.dirname(got))
            self.assertIn("params.csv", copied)     # the model reads it
            self.assertNotIn("._pywr_model.json", copied)   # macOS junk

    def test_no_example_reports_none_rather_than_a_bad_path(self):
        with tempfile.TemporaryDirectory() as empty, \
             mock.patch.object(files, "APP_DIR", empty):
            self.assertIsNone(files.example_path())
            self.assertIsNone(self.c.get("/api/example").get_json()["path"])


class TestPackagedBuild(unittest.TestCase):
    """build_exe.py packages the app so it runs without Python installed."""

    def test_the_frozen_build_never_bootstraps_a_venv(self):
        # sys.executable is the app itself once packaged, so `-m venv` on it
        # relaunches the app — which recurses until the machine gives up.
        # This guard is the difference between "starts" and "fork bomb".
        with mock.patch.object(app_module, "has_flask") as checked, \
             mock.patch.object(app_module.subprocess, "run") as ran, \
             mock.patch.object(app_module.sys, "frozen", True, create=True):
            self.assertIsNone(app_module.bootstrap())
        ran.assert_not_called()
        checked.assert_not_called()   # doesn't even need to look

    def test_browser_opening_precedence(self):
        cases = [   # (argv, env, frozen) -> expected
            ([], {}, False, False),                 # from source: quiet
            ([], {}, True, True),                   # packaged: opens
            (["--open"], {}, False, True),          # launchers pass this
            (["--no-open"], {}, True, False),       # headless build server
            (["--open", "--no-open"], {}, False, False),   # off wins
            ([], {"PYWR_READER_OPEN": "1"}, False, True),
            ([], {"PYWR_READER_OPEN": "0"}, True, False),
            ([], {"PYWR_READER_OPEN": ""}, True, False),
        ]
        for argv, env, frozen, expected in cases:
            with self.subTest(argv=argv, env=env, frozen=frozen):
                self.assertEqual(
                    app_module.wants_browser(argv, env, frozen), expected)

    def test_the_build_ships_the_scripts_that_run_as_subprocesses(self):
        # runner.py and dataview.py are handed to a *separate* interpreter (the
        # pywr environment), so they must exist as real files in the bundle,
        # not merely as modules compiled into the binary
        src = pathlib.Path(ROOT, "build_exe.py").read_text(encoding="utf-8")
        for name in ("runner.py", "dataview.py", "static", "examples"):
            self.assertIn(name, src, f"build_exe.py does not bundle {name}")

    def test_the_workflow_builds_on_windows(self):
        # a .exe cannot be cross-compiled from macOS/Linux — if the Windows
        # runner ever disappears from this matrix, there is no .exe
        workflow = pathlib.Path(ROOT, ".github", "workflows", "build.yml")
        self.assertTrue(workflow.is_file(), "no build workflow")
        text = workflow.read_text(encoding="utf-8")
        self.assertIn("windows-latest", text)
        self.assertIn("build_exe.py", text)
        self.assertIn("--no-open", text)   # or the runner waits on a browser

    def test_console_output_survives_an_old_windows_code_page(self):
        """Windows consoles are usually cp1252 or cp437, not UTF-8. A "→" in a
        status line raised UnicodeEncodeError and killed the packaged app on
        startup — before it printed anything explaining why. Keep every string
        we print encodable on the narrowest of those."""
        import ast
        offenders = []
        for name in ("app.py", "build_exe.py"):
            tree = ast.parse(pathlib.Path(ROOT, name).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Name)
                        and node.func.id in ("print", "_die")):
                    continue
                for piece in ast.walk(node):    # covers f-string fragments too
                    if isinstance(piece, ast.Constant) and \
                            isinstance(piece.value, str):
                        try:
                            piece.value.encode("cp437")
                        except UnicodeEncodeError:
                            offenders.append(f"{name}: {piece.value.strip()[:60]!r}")
        self.assertEqual(offenders, [],
                         "these printed strings would crash a Windows console")

    def test_app_dir_follows_the_bundle_when_frozen(self):
        # PyInstaller unpacks bundled data to _MEIPASS; static/ has to be found
        # there, not next to a source file that isn't shipped
        import importlib

        from pywr_reader.api import util
        with mock.patch.object(util.sys, "frozen", True, create=True), \
             mock.patch.object(util.sys, "_MEIPASS", "/tmp/bundle", create=True):
            reloaded = importlib.reload(util)
            self.assertEqual(reloaded.APP_DIR, "/tmp/bundle")
        importlib.reload(util)          # put the real path back for other tests
        self.assertTrue(os.path.isdir(os.path.join(util.APP_DIR, "static")))


class TestSessionLifecycle(unittest.TestCase):
    """Runs, undo and the empty state — the session state that used to outlive
    the model it described."""

    def setUp(self):
        app_module.app.testing = True
        self.c = app_module.app.test_client()
        app_module.WORKSPACE.reset()
        app_module.RUNS.clear()
        app_module.RUNS.abandoned.clear()

    def _fake_done_run(self, run_id="r1"):
        app_module.RUNS.add({"id": run_id, "status": "done", "label": "run 1",
                             "dates": ["2000-01-01"],
                             "nodes": {"GW_Base": {"flow": [1.0]}},
                             "edges": [], "started_at": 0.0})

    def test_opening_another_model_drops_the_previous_runs(self):
        self.c.post("/api/open", json={"path": EXAMPLE})
        self._fake_done_run()
        self.assertEqual(len(app_module.RUNS), 1)
        self.c.post("/api/open", json={"path": EXAMPLE})
        self.assertEqual(len(app_module.RUNS), 0)
        self.assertEqual(self.c.get("/api/runs").get_json()["runs"], [])

    def test_a_new_model_drops_the_previous_runs(self):
        self.c.post("/api/open", json={"path": EXAMPLE})
        self._fake_done_run()
        self.c.post("/api/new", json={"title": "Blank"})
        self.assertEqual(len(app_module.RUNS), 0)

    def test_close_goes_back_to_the_empty_state(self):
        self.c.post("/api/open", json={"path": EXAMPLE})
        self._fake_done_run()
        r = self.c.post("/api/close", json={})
        self.assertTrue(r.get_json()["ok"])
        self.assertIsNone(app_module.WORKSPACE.model)
        self.assertEqual(len(app_module.RUNS), 0)
        self.assertEqual(self.c.get("/api/graph").status_code, 400)

    def test_undo_takes_back_a_delete(self):
        self.c.post("/api/open", json={"path": EXAMPLE})
        before = len(self.c.get("/api/graph").get_json()["nodes"])
        self.c.post("/api/node/delete", json={"name": "Reservoir_A"})
        self.assertEqual(
            len(self.c.get("/api/graph").get_json()["nodes"]), before - 1)
        r = self.c.post("/api/undo", json={})
        data = r.get_json()
        self.assertEqual(data["undone"], "delete Reservoir_A")
        self.assertEqual(len(data["nodes"]), before)
        self.assertIn("Reservoir_A", [n["name"] for n in data["nodes"]])
        # and the recorder that pointed at it is unbroken again
        self.assertEqual(data["reference_warnings"], [])

    def test_undo_takes_back_a_rename_and_its_reference_rewrites(self):
        self.c.post("/api/open", json={"path": EXAMPLE})
        self.c.post("/api/node/rename",
                    json={"old": "Reservoir_A", "new": "Res_B"})
        self.c.post("/api/undo", json={})
        model = app_module.WORKSPACE.model
        self.assertEqual(model["recorders"]["Reservoir_volume"]["node"],
                         "Reservoir_A")

    def test_undo_is_more_than_one_deep(self):
        self.c.post("/api/open", json={"path": EXAMPLE})
        for name in ("Demand_Urban", "Demand_Irrigation"):
            self.c.post("/api/node/delete", json={"name": name})
        self.c.post("/api/undo", json={})
        self.c.post("/api/undo", json={})
        names = [n["name"] for n in self.c.get("/api/graph").get_json()["nodes"]]
        self.assertIn("Demand_Urban", names)
        self.assertIn("Demand_Irrigation", names)

    def test_the_graph_says_what_undo_would_take_back(self):
        self.c.post("/api/open", json={"path": EXAMPLE})
        self.assertIsNone(self.c.get("/api/graph").get_json()["undo_label"])
        self.c.post("/api/edge/add",
                    json={"src": "Reservoir_A", "dst": "River_Outlet"})
        self.assertEqual(self.c.get("/api/graph").get_json()["undo_label"],
                         "add edge")

    def test_undo_with_nothing_to_undo_is_a_message_not_a_500(self):
        self.c.post("/api/open", json={"path": EXAMPLE})
        r = self.c.post("/api/undo", json={})
        self.assertEqual(r.status_code, 400)
        self.assertIn("undo", r.get_json()["error"])

    def test_node_refs_separates_edges_from_what_a_delete_strands(self):
        self.c.post("/api/open", json={"path": EXAMPLE})
        data = self.c.get("/api/node/refs?name=Reservoir_A").get_json()
        self.assertGreater(data["n_edges"], 0)
        self.assertIn("recorders.Reservoir_volume.node", data["stranded"])
        self.assertTrue(all(not r.startswith("edges[") for r in data["stranded"]))

    def test_node_refs_on_an_unknown_node_is_an_error(self):
        self.c.post("/api/open", json={"path": EXAMPLE})
        self.assertEqual(self.c.get("/api/node/refs?name=Nope").status_code, 400)


class TestSaveAsRehomesData(unittest.TestCase):
    """Data files are found relative to the model's folder, so Save As can cut
    them loose without saying so."""

    def setUp(self):
        app_module.app.testing = True
        self.c = app_module.app.test_client()
        app_module.WORKSPACE.reset()
        self.tmp = tempfile.TemporaryDirectory()
        # dataresolve climbs two folders above the model and indexes what it
        # finds, so "elsewhere" has to be out of that reach for the test to be
        # about Save As rather than about the search being generous
        self.home = os.path.join(self.tmp.name, "home")
        self.away = os.path.join(self.tmp.name, "a", "b", "away")
        os.makedirs(self.home)
        os.makedirs(self.away)
        self.model_path = os.path.join(self.home, "m.json")
        with open(os.path.join(self.home, "inflow.csv"), "w") as fh:
            fh.write("date,flow\n2000-01-01,1\n")
        with open(self.model_path, "w") as fh:
            json.dump({
                "metadata": {"title": "t"},
                "timestepper": {"start": "2000-01-01", "end": "2000-01-02",
                                "timestep": 1},
                "nodes": [{"name": "A", "type": "input",
                           "position": {"schematic": [0, 0]}},
                          {"name": "B", "type": "output",
                           "position": {"schematic": [100, 0]}}],
                "edges": [["A", "B"]],
                "parameters": {"p": {"type": "dataframe",
                                     "url": "inflow.csv"}},
            }, fh)

    def tearDown(self):
        self.tmp.cleanup()

    def test_saving_beside_the_original_says_nothing(self):
        self.c.post("/api/open", json={"path": self.model_path})
        r = self.c.post("/api/save",
                        json={"path": os.path.join(self.home, "copy.json")})
        self.assertIsNone(r.get_json()["data_note"])
        self.assertEqual(r.get_json()["data"]["missing"], [])

    def test_saving_elsewhere_warns_and_keeps_the_file_findable(self):
        self.c.post("/api/open", json={"path": self.model_path})
        # it was located before the save
        self.assertEqual(
            self.c.get("/api/graph").get_json()["data"]["missing"], [])
        r = self.c.post("/api/save",
                        json={"path": os.path.join(self.away, "copy.json")})
        note = r.get_json()["data_note"]
        self.assertIsNotNone(note)
        self.assertIn("inflow.csv", note)
        # the old folder was added to the search path, so the session still runs
        self.assertEqual(r.get_json()["data"]["missing"], [])
        self.assertIn(self.home, app_module.WORKSPACE.data_dirs)
        # and the Model tab now agrees with reality
        self.assertEqual(
            self.c.get("/api/graph").get_json()["data"]["missing"], [])

    def test_plain_save_over_the_same_path_does_not_touch_the_search(self):
        self.c.post("/api/open", json={"path": self.model_path})
        r = self.c.post("/api/save", json={})
        self.assertIsNone(r.get_json()["data_note"])
        self.assertEqual(app_module.WORKSPACE.data_dirs, [])

    def test_path_exists_answers_before_save_as_overwrites(self):
        self.assertFalse(
            self.c.get("/api/path/exists?path="
                       + os.path.join(self.away, "nope.json")).get_json()["exists"])
        self.assertTrue(
            self.c.get("/api/path/exists?path=" + self.model_path).get_json()["exists"])
        # the .json the save itself would append is taken into account
        stem = self.model_path[: -len(".json")]
        self.assertTrue(self.c.get("/api/path/exists?path=" + stem)
                        .get_json()["exists"])


class TestRunProgressAndHints(unittest.TestCase):
    def setUp(self):
        app_module.app.testing = True
        self.c = app_module.app.test_client()
        app_module.WORKSPACE.reset()
        app_module.RUNS.clear()

    def test_progress_is_read_from_the_file_the_runner_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "p.json")
            run = {"id": "x", "status": "running", "progress_path": path}
            self.assertIsNone(runs.read_progress(run))      # not written yet
            with open(path, "w") as fh:
                json.dump({"step": 120, "total": 29586}, fh)
            self.assertEqual(runs.read_progress(run),
                             {"step": 120, "total": 29586})

    def test_a_half_written_progress_file_is_ignored_not_fatal(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "p.json")
            with open(path, "w") as fh:
                fh.write("{\"step\": 1")
            self.assertIsNone(
                runs.read_progress({"id": "x", "progress_path": path}))

    def test_a_finished_run_reports_no_progress(self):
        self.assertIsNone(runs.read_progress({"id": "x", "status": "done"}))

    def test_a_keyerror_from_pywr_is_explained_in_the_apps_own_words(self):
        model = {"nodes": [{"name": "DC", "type": "output"}], "edges": [],
                 "recorders": {"Gauge_flow": {"type": "NumpyArrayNodeRecorder",
                                              "node": "River_Gauge_A"}}}
        hints = runs.failure_hints(model, "KeyError: 'River_Gauge_A'")
        self.assertEqual(len(hints), 1)
        self.assertIn("Gauge_flow", hints[0])
        self.assertIn("River_Gauge_A", hints[0])

    def test_a_clean_model_gets_no_hints(self):
        model = {"nodes": [{"name": "DC", "type": "output"}], "edges": []}
        self.assertEqual(runs.failure_hints(model, "SolverError: infeasible"), [])

    def test_a_broken_model_is_still_flagged_for_an_unrelated_error(self):
        model = {"nodes": [{"name": "DC", "type": "output",
                            "max_flow": "missing_param"}], "edges": []}
        hints = runs.failure_hints(model, "SolverError: infeasible")
        self.assertEqual(len(hints), 1)
        self.assertIn("Model tab", hints[0])

    def test_the_status_endpoint_carries_progress_and_timing(self):
        app_module.RUNS.add({"id": "q1", "status": "queued", "label": "run 1",
                             "started_at": 100.0})
        data = self.c.get("/api/run/q1").get_json()
        self.assertEqual(data["started_at"], 100.0)
        self.assertIsNone(data["progress"])
        self.assertEqual(data["hints"], [])
        listed = self.c.get("/api/runs").get_json()["runs"][0]
        self.assertEqual(listed["started_at"], 100.0)


class TestTcmAsModel(unittest.TestCase):
    """A .tcm opened while a model is loaded is applied as positions. When
    nothing in it matches, that used to be the end of the story."""

    def setUp(self):
        app_module.app.testing = True
        self.c = app_module.app.test_client()
        app_module.WORKSPACE.reset()
        self.tmp = tempfile.TemporaryDirectory()
        self.tcm = os.path.join(self.tmp.name, "other.tcm")
        self._write_tcm({"Elsewhere_1": (0, 0), "Elsewhere_2": (100, 0)})
        with open(os.path.join(self.tmp.name, "other.json"), "w") as fh:
            json.dump({
                "metadata": {"title": "other"},
                "timestepper": {"start": "2000-01-01", "end": "2000-01-02",
                                "timestep": 1},
                "nodes": [{"name": "Elsewhere_1", "type": "input"},
                          {"name": "Elsewhere_2", "type": "output"}],
                "edges": [["Elsewhere_1", "Elsewhere_2"]],
            }, fh)

    def _write_tcm(self, nodes):
        """A .tcm in the real shape: JSON, naming the model beside it."""
        with open(self.tcm, "w") as fh:
            json.dump({"core": {
                "source": {"V1": {"Path": "C:\\elsewhere\\other.json"}},
                "components": {"node_meta": {
                    name: {"position": {"User": {"x": x, "y": y}}}
                    for name, (x, y) in nodes.items()}},
            }}, fh)

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_tcm_that_matches_nothing_says_so(self):
        self.c.post("/api/open", json={"path": EXAMPLE})
        data = self.c.post("/api/open", json={"path": self.tcm}).get_json()
        self.assertTrue(data["tcm_unmatched"])
        # the open model is untouched
        self.assertTrue(data["tcm_applied"])
        self.assertEqual(data["path"], EXAMPLE)

    def test_as_model_opens_the_tcm_itself_instead(self):
        self.c.post("/api/open", json={"path": EXAMPLE})
        data = self.c.post("/api/open",
                           json={"path": self.tcm, "as_model": True}).get_json()
        # a .tcm names the model it describes, so opening it "as a model"
        # lands on that file — which is the point
        self.assertEqual(data["path"],
                         os.path.join(self.tmp.name, "other.json"))
        self.assertEqual(sorted(n["name"] for n in data["nodes"]),
                         ["Elsewhere_1", "Elsewhere_2"])
        self.assertNotIn("tcm_applied", data)

    def test_a_matching_tcm_still_applies_as_positions(self):
        self.c.post("/api/open", json={"path": EXAMPLE})
        self._write_tcm({"Reservoir_A": (10, 20), "Demand_Urban": (30, 40)})
        data = self.c.post("/api/open", json={"path": self.tcm}).get_json()
        self.assertFalse(data["tcm_unmatched"])
        self.assertTrue(data["tcm_applied"])
        self.assertEqual(data["path"], EXAMPLE)


class TestDefinitionApi(unittest.TestCase):
    """Renaming and deleting parameters / recorders / tables over the API —
    the counterpart of /api/node/rename for the blocks nodes point at."""

    def setUp(self):
        app_module.app.testing = True
        self.c = app_module.app.test_client()
        app_module.WORKSPACE.reset()
        self.c.post("/api/new", json={"title": "wiring"})
        model = app_module.WORKSPACE.model
        model["nodes"] = [{"name": "DC", "type": "output",
                           "max_flow": "DC_max_flow"}]
        model["parameters"] = {
            "DC_max_flow": {"type": "Aggregated", "agg_func": "product",
                            "parameters": ["DC_base"]},
            "DC_base": {"type": "constant", "value": 3},
            "DC_threshold": {"type": "RecorderThresholdParameter",
                             "recorder": "DC_deficit"},
        }
        model["recorders"] = {
            "DC_deficit": {"type": "NumpyArrayNodeDeficitRecorder", "node": "DC"},
        }

    def test_rename_a_parameter_carries_the_node_attribute(self):
        r = self.c.post("/api/definition/rename",
                        json={"section": "parameters", "old": "DC_max_flow",
                              "new": "cap"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()["notes"])
        model = app_module.WORKSPACE.model
        self.assertEqual(model["nodes"][0]["max_flow"], "cap")
        self.assertEqual(r.get_json()["reference_warnings"], [])

    def test_rename_a_recorder_carries_the_parameter_that_reads_it(self):
        self.c.post("/api/definition/rename",
                    json={"section": "recorders", "old": "DC_deficit",
                          "new": "shortfall"})
        self.assertEqual(
            app_module.WORKSPACE.model["parameters"]["DC_threshold"]["recorder"],
            "shortfall")

    def test_rename_into_a_taken_name_is_refused(self):
        r = self.c.post("/api/definition/rename",
                        json={"section": "parameters", "old": "DC_base",
                              "new": "DC_max_flow"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("already exists", r.get_json()["error"])

    def test_rename_in_an_unrenameable_section_is_refused(self):
        r = self.c.post("/api/definition/rename",
                        json={"section": "metadata", "old": "a", "new": "b"})
        self.assertEqual(r.status_code, 400)

    def test_delete_reports_what_still_points_at_it(self):
        r = self.c.post("/api/definition/delete",
                        json={"section": "parameters", "name": "DC_base"})
        data = r.get_json()
        self.assertEqual(len(data["delete_warnings"]), 1)
        self.assertIn("DC_base", data["delete_warnings"][0])
        # and the payload now carries the dangling reference it created
        self.assertTrue(any("DC_base" in w for w in data["reference_warnings"]))

    def test_refs_endpoint_counts_before_you_commit(self):
        r = self.c.get("/api/definition/refs?section=parameters&name=DC_base")
        self.assertEqual(r.get_json()["refs"],
                         ["parameters.DC_max_flow.parameters[0]"])

    def test_refs_endpoint_rejects_an_unknown_section(self):
        r = self.c.get("/api/definition/refs?section=nope&name=x")
        self.assertEqual(r.status_code, 400)

    def test_raw_model_accepts_renames_per_section(self):
        # the JSON editors send the rename that already happened in their text
        model = json.loads(json.dumps(app_module.WORKSPACE.model))
        model["parameters"]["cap"] = model["parameters"].pop("DC_max_flow")
        r = self.c.post("/api/model/raw", json={
            "model": model, "renames": {"parameters": {"DC_max_flow": "cap"}}})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(app_module.WORKSPACE.model["nodes"][0]["max_flow"], "cap")
        self.assertEqual(r.get_json()["reference_warnings"], [])

    def test_raw_model_still_takes_the_flat_node_rename_form(self):
        # the node editor has always sent {old: new} — that must keep working
        model = json.loads(json.dumps(app_module.WORKSPACE.model))
        model["nodes"][0]["name"] = "Demand"
        r = self.c.post("/api/model/raw",
                        json={"model": model, "renames": {"DC": "Demand"}})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(
            app_module.WORKSPACE.model["recorders"]["DC_deficit"]["node"],
            "Demand")

    def test_add_a_recorder(self):
        r = self.c.post("/api/definition/add", json={
            "section": "recorders", "name": "DC_flow",
            "definition": {"type": "NumpyArrayNodeRecorder", "node": "DC"}})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["added"], ["DC_flow"])
        self.assertIn("DC_flow", app_module.WORKSPACE.model["recorders"])

    def test_added_recorders_come_back_on_the_node_they_watch(self):
        self.c.post("/api/definition/add", json={
            "section": "recorders", "name": "DC_flow",
            "definition": {"type": "NumpyArrayNodeRecorder", "node": "DC"}})
        node = self.c.get("/api/graph").get_json()["nodes"][0]
        self.assertIn({"name": "DC_flow", "type": "NumpyArrayNodeRecorder"},
                      node["recorders"])
        # the one the fixture starts with is still listed beside it
        self.assertIn({"name": "DC_deficit",
                       "type": "NumpyArrayNodeDeficitRecorder"},
                      node["recorders"])

    def test_add_refuses_to_overwrite(self):
        r = self.c.post("/api/definition/add", json={
            "section": "parameters", "name": "DC_base",
            "definition": {"type": "constant", "value": 1}})
        self.assertEqual(r.status_code, 400)
        self.assertIn("already exists", r.get_json()["error"])
        # the existing one is untouched
        self.assertEqual(app_module.WORKSPACE.model["parameters"]["DC_base"],
                         {"type": "constant", "value": 3})

    def test_a_batch_add_is_all_or_nothing(self):
        # the second entry clashes — the first must not be left behind
        r = self.c.post("/api/definition/add", json={"entries": [
            {"section": "recorders", "name": "fresh",
             "definition": {"type": "NumpyArrayNodeRecorder", "node": "DC"}},
            {"section": "parameters", "name": "DC_base",
             "definition": {"type": "constant", "value": 1}},
        ]})
        self.assertEqual(r.status_code, 400)
        self.assertNotIn("fresh", app_module.WORKSPACE.model["recorders"])

    def test_a_batch_add_lands_together(self):
        r = self.c.post("/api/definition/add", json={"entries": [
            {"section": "recorders", "name": "a",
             "definition": {"type": "NumpyArrayNodeRecorder", "node": "DC"}},
            {"section": "recorders", "name": "b",
             "definition": {"type": "TotalFlowNodeRecorder", "node": "DC"}},
        ]})
        self.assertEqual(r.get_json()["added"], ["a", "b"])
        self.assertIn("a", app_module.WORKSPACE.model["recorders"])
        self.assertIn("b", app_module.WORKSPACE.model["recorders"])

    def test_add_wires_the_node_in_the_same_edit(self):
        # a parameter chain is half-built until the node points at it
        r = self.c.post("/api/definition/add", json={
            "entries": [
                {"section": "parameters", "name": "cap_base",
                 "definition": {"type": "constant", "value": 5}},
                {"section": "parameters", "name": "cap",
                 "definition": {"type": "Aggregated", "agg_func": "product",
                                "parameters": ["cap_base"]}},
            ],
            "node_changes": {"name": "DC", "changes": {"max_flow": "cap"}}})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(app_module.WORKSPACE.model["nodes"][0]["max_flow"], "cap")
        self.assertEqual(r.get_json()["reference_warnings"], [])

    def test_a_failed_node_change_adds_nothing(self):
        r = self.c.post("/api/definition/add", json={
            "entries": [{"section": "parameters", "name": "cap",
                         "definition": {"type": "constant", "value": 5}}],
            "node_changes": {"name": "NoSuchNode",
                             "changes": {"max_flow": "cap"}}})
        self.assertEqual(r.status_code, 400)
        self.assertNotIn("cap", app_module.WORKSPACE.model["parameters"])

    def test_add_rejects_a_nameless_or_non_object_definition(self):
        for body in ({"section": "recorders", "name": "",
                      "definition": {"type": "x"}},
                     {"section": "recorders", "name": "x", "definition": 5},
                     {"section": "nodes", "name": "x", "definition": {}}):
            self.assertEqual(
                self.c.post("/api/definition/add", json=body).status_code, 400,
                body)

    def test_raw_model_rejects_a_rename_in_a_section_that_has_no_names(self):
        r = self.c.post("/api/model/raw", json={
            "model": app_module.WORKSPACE.model,
            "renames": {"edges": {"a": "b"}}})
        self.assertEqual(r.status_code, 400)


if __name__ == "__main__":
    unittest.main(verbosity=2)
