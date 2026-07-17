"""Unit tests for PyWR Reader — run with the project venv, no pywr needed:

    ./.venv/bin/python -m unittest discover -s tests -v

Every test builds its own fixtures in a temp dir, so the suite is
self-contained and does not depend on any file outside the release.
"""

import gzip
import json
import math
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pywr_reader import dataresolve, graphops, layout, model_io  # noqa: E402
from pywr_reader import runner  # noqa: E402


def tiny_model():
    """A 5-node source→link→demand model with a virtual storage watcher."""
    return {
        "metadata": {"title": "tiny", "minimum_version": "1.0"},
        "timestepper": {"start": "2000-01-01", "end": "2000-01-31", "timestep": 1},
        "nodes": [
            {"name": "src", "type": "input", "max_flow": 10, "cost": 1},
            {"name": "mid", "type": "link"},
            {"name": "res", "type": "storage", "max_volume": 100,
             "initial_volume": 50},
            {"name": "demand", "type": "output", "max_flow": 8, "cost": -10},
            {"name": "licence", "type": "annualvirtualstorage",
             "nodes": ["mid"], "max_volume": 200},
        ],
        "edges": [["src", "mid"], ["mid", "res"], ["res", "demand"]],
        "parameters": {},
        "recorders": {},
    }


class TestPositions(unittest.TestCase):
    def test_extract_and_inject(self):
        m = tiny_model()
        m["nodes"][0]["position"] = {"schematic": [3.0, 4.0]}
        pos = model_io.extract_positions(m)
        self.assertEqual(pos["src"], [3.0, 4.0])

        model_io.inject_positions(m, {"mid": [1, 2]})
        self.assertEqual(m["nodes"][1]["position"]["schematic"], [1.0, 2.0])

    def test_degenerate_detection(self):
        # all stacked on one point → degenerate
        stacked = {f"n{i}": [1000, 1000] for i in range(10)}
        self.assertTrue(model_io.positions_are_degenerate(stacked, 10))
        # a real spread → not degenerate
        spread = {f"n{i}": [i * 10, i * 5] for i in range(10)}
        self.assertFalse(model_io.positions_are_degenerate(spread, 10))
        # mostly-default with a couple real → still degenerate (pywr-editor case)
        mixed = {f"n{i}": [1000, 1000] for i in range(8)}
        mixed["a"], mixed["b"] = [3, 4], [9, 2]
        self.assertTrue(model_io.positions_are_degenerate(mixed, 10))
        # empty
        self.assertTrue(model_io.positions_are_degenerate({}, 5))


class TestLoaders(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _write(self, name, obj):
        path = os.path.join(self.tmp, name)
        with open(path, "w") as fh:
            json.dump(obj, fh)
        return path

    def test_load_pywr_json(self):
        path = self._write("m.json", tiny_model())
        loaded = model_io.load_any(path)
        self.assertEqual(loaded["source"], "pywr-json")
        self.assertEqual(len(loaded["model"]["nodes"]), 5)

    def test_load_json_rejects_non_model(self):
        path = self._write("bad.json", {"foo": "bar"})
        with self.assertRaises(ValueError):
            model_io.load_any(path)

    def test_load_tcm_applies_to_source_model(self):
        # model file next to the .tcm
        self._write("model.json", tiny_model())
        tcm = {
            "core": {
                "source": {"V1": {"Path": "C:\\somewhere\\model.json"}},
                "components": {
                    "node_meta": {
                        "src": {"position": {"User": {"x": 5.0, "y": 6.0}}},
                        "demand": {"position": {"User": {"x": 9.0, "y": 1.0}}},
                    },
                    "coord_transformations": {
                        "x_factor": 1.0, "y_factor": 1.0,
                        "x_offset": 0.0, "y_offset": 0.0},
                },
            },
        }
        tcm_path = os.path.join(self.tmp, "view.tcm")
        with open(tcm_path, "wb") as fh:
            fh.write(gzip.compress(json.dumps(tcm).encode()))

        loaded = model_io.load_any(tcm_path)
        self.assertEqual(loaded["source"], "tcm")
        self.assertEqual(loaded["positions"]["src"], [5.0, 6.0])
        self.assertEqual(loaded["positions"]["demand"], [9.0, 1.0])

    def test_load_tcm_plain_json_not_gzipped(self):
        tcm = {"core": {"components": {"node_meta": {
            "a": {"position": {"User": {"x": 1, "y": 2}}}}}}}
        path = os.path.join(self.tmp, "plain.tcm")
        with open(path, "w") as fh:
            json.dump(tcm, fh)
        pos, src, _ = model_io.load_tcm(path)
        self.assertEqual(pos["a"], [1.0, 2.0])

    def test_load_csv_pair(self):
        with open(os.path.join(self.tmp, "nodes.csv"), "w") as fh:
            fh.write("id,name,type,col,row,max_flow\n"
                     "1,A,input,2,3,50\n2,B,output,4,3,\n")
        with open(os.path.join(self.tmp, "nodes_edges.csv"), "w") as fh:
            fh.write("id,name,src,dst\n1,E1,A,B\n")
        loaded = model_io.load_any(os.path.join(self.tmp, "nodes.csv"))
        self.assertEqual(loaded["source"], "csv")
        names = [n["name"] for n in loaded["model"]["nodes"]]
        self.assertEqual(names, ["A", "B"])
        self.assertEqual(loaded["model"]["edges"], [["A", "B"]])
        self.assertEqual(loaded["positions"]["A"], [2.0, 3.0])
        # numeric params parsed
        a = model_io.load_any(os.path.join(self.tmp, "nodes.csv"))["model"]["nodes"][0]
        self.assertEqual(a["max_flow"], 50)

    def test_save_roundtrip_preserves_sections(self):
        m = tiny_model()
        m["tables"] = {"t": {"url": "x.csv"}}
        path = os.path.join(self.tmp, "out.json")
        model_io.save_pywr_json(m, {"src": [1, 2]}, path)
        with open(path) as fh:
            again = json.load(fh)
        self.assertIn("tables", again)
        self.assertEqual(again["nodes"][0]["position"]["schematic"], [1.0, 2.0])


class TestLayout(unittest.TestCase):
    def test_auto_layout_places_all(self):
        m = tiny_model()
        names = [n["name"] for n in m["nodes"]]
        pos = layout.auto_layout(names, m["edges"])
        self.assertEqual(set(pos), set(names))
        # connected nodes get distinct positions
        conn = [pos[n] for n in ("src", "mid", "res", "demand")]
        self.assertEqual(len({tuple(p) for p in conn}), 4)

    def test_auto_layout_flow_is_top_down(self):
        names = ["a", "b", "c"]
        pos = layout.auto_layout(names, [["a", "b"], ["b", "c"]])
        self.assertLess(pos["a"][1], pos["b"][1])
        self.assertLess(pos["b"][1], pos["c"][1])

    def test_auto_layout_handles_cycle(self):
        names = ["a", "b", "c"]
        pos = layout.auto_layout(names, [["a", "b"], ["b", "c"], ["c", "a"]])
        self.assertEqual(set(pos), set(names))  # no infinite loop / missing

    def test_affinity_places_isolated_near_reference(self):
        m = tiny_model()
        names = [n["name"] for n in m["nodes"]]
        aff = graphops.node_affinity(m)  # licence -> [mid]
        pos = layout.auto_layout(names, m["edges"], affinity=aff)
        # licence should sit near mid, not off in the spare grid
        dx = abs(pos["licence"][0] - pos["mid"][0])
        dy = abs(pos["licence"][1] - pos["mid"][1])
        self.assertLess(dx, layout.X_SPACING)
        self.assertLess(dy, layout.Y_SPACING)

    def test_layout_missing_keeps_existing(self):
        names = ["a", "b", "c"]
        existing = {"a": [0, 0], "b": [100, 0]}
        pos = layout.layout_missing(names, [["a", "c"], ["b", "c"]], existing)
        self.assertEqual(pos["a"], [0, 0])
        self.assertEqual(pos["b"], [100, 0])
        self.assertIn("c", pos)  # c got placed near a & b


class TestGraphOps(unittest.TestCase):
    def test_trace_up_and_down(self):
        m = tiny_model()
        up, _ = graphops.trace(m, "demand", "upstream")
        self.assertEqual(up, {"demand", "res", "mid", "src"})
        down, _ = graphops.trace(m, "src", "downstream")
        self.assertEqual(down, {"src", "mid", "res", "demand"})

    def test_add_node_and_edge(self):
        m = tiny_model()
        graphops.add_node(m, {"name": "extra", "type": "input"})
        self.assertIsNotNone(graphops.node_by_name(m, "extra"))
        graphops.add_edge(m, "extra", "mid")
        self.assertIn(["extra", "mid"], m["edges"])
        with self.assertRaises(ValueError):
            graphops.add_node(m, {"name": "extra"})       # duplicate
        with self.assertRaises(ValueError):
            graphops.add_edge(m, "extra", "extra")        # self-loop
        with self.assertRaises(ValueError):
            graphops.add_edge(m, "extra", "nope")         # unknown endpoint

    def test_delete_node_removes_edges_and_warns_on_refs(self):
        m = tiny_model()
        warnings = graphops.delete_node(m, "mid")
        self.assertIsNone(graphops.node_by_name(m, "mid"))
        self.assertFalse(any("mid" in e[:2] for e in m["edges"]))
        # 'mid' is still referenced by the licence virtual storage
        self.assertTrue(warnings)

    def test_delete_edge(self):
        m = tiny_model()
        graphops.delete_edge(m, "src", "mid")
        self.assertNotIn(["src", "mid"], m["edges"])
        with self.assertRaises(ValueError):
            graphops.delete_edge(m, "src", "mid")         # already gone

    def test_rename_rewrites_edges_and_references(self):
        m = tiny_model()
        graphops.rename_node(m, "mid", "middle")
        self.assertIsNotNone(graphops.node_by_name(m, "middle"))
        self.assertIn(["src", "middle"], m["edges"])
        # licence.nodes referenced 'mid' → now 'middle'
        licence = graphops.node_by_name(m, "licence")
        self.assertIn("middle", licence["nodes"])
        with self.assertRaises(ValueError):
            graphops.rename_node(m, "middle", "demand")   # name clash

    def test_update_node_protects_key_fields(self):
        m = tiny_model()
        graphops.update_node(m, "src", {"max_flow": 99, "name": "hax",
                                        "position": {"schematic": [0, 0]}})
        node = graphops.node_by_name(m, "src")
        self.assertEqual(node["max_flow"], 99)
        self.assertEqual(node["name"], "src")             # name unchanged
        graphops.update_node(m, "src", {}, removals=["cost"])
        self.assertNotIn("cost", node)

    def test_affinity_maps_virtual_to_watched(self):
        m = tiny_model()
        aff = graphops.node_affinity(m)
        self.assertEqual(aff["licence"], ["mid"])


class TestDataResolve(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_referenced_files_collects_urls(self):
        m = tiny_model()
        m["tables"] = {
            "a": {"url": "C:\\win\\data.xlsx", "sheet_name": "S"},
            "b": {"url": "/unix/data.xlsx"},
            "c": {"url": "C:\\win\\data.xlsx"},   # same basename, 2nd ref
        }
        needed = dataresolve.referenced_files(m)
        self.assertIn("data.xlsx", needed)
        self.assertEqual(needed["data.xlsx"]["refs"], 3)

    def test_resolve_finds_in_model_folder_and_extra_dir(self):
        # model references two files by Windows paths
        m = tiny_model()
        m["tables"] = {
            "x": {"url": "C:\\foo\\here.csv"},
            "y": {"url": "C:\\foo\\deep.h5"},
        }
        model_path = os.path.join(self.tmp, "m.json")
        with open(model_path, "w") as fh:
            json.dump(m, fh)
        # here.csv sits next to the model; deep.h5 in a separate data dir
        open(os.path.join(self.tmp, "here.csv"), "w").close()
        data_dir = tempfile.mkdtemp()
        sub = os.path.join(data_dir, "nested")
        os.makedirs(sub)
        open(os.path.join(sub, "deep.h5"), "w").close()

        res = dataresolve.resolve(m, model_path, extra_dirs=[data_dir])
        self.assertEqual(res["missing"], [])
        self.assertTrue(res["map"]["C:\\foo\\here.csv"].endswith("here.csv"))
        self.assertTrue(res["map"]["C:\\foo\\deep.h5"].endswith("deep.h5"))

    def test_resolve_reports_missing(self):
        m = tiny_model()
        m["tables"] = {"x": {"url": "C:\\nope\\ghost.csv"}}
        model_path = os.path.join(self.tmp, "m.json")
        with open(model_path, "w") as fh:
            json.dump(m, fh)
        res = dataresolve.resolve(m, model_path)
        self.assertEqual(res["missing"], ["ghost.csv"])

    def test_apply_map_rewrites_urls(self):
        m = tiny_model()
        m["tables"] = {"x": {"url": "C:\\foo\\here.csv"}}
        n = dataresolve.apply_map(m, {"C:\\foo\\here.csv": "/local/here.csv"})
        self.assertEqual(n, 1)
        self.assertEqual(m["tables"]["x"]["url"], "/local/here.csv")


class TestRunnerPureFns(unittest.TestCase):
    def test_compat_fixups_parameter_types(self):
        data = {"nodes": [{"name": "a", "type": "input",
                           "max_flow": {"type": "CSVParameter", "url": "x.csv"}}],
                "edges": []}
        runner.compat_fixups(data)
        self.assertEqual(data["nodes"][0]["max_flow"]["type"], "dataframe")

    def test_compat_fixups_pads_three_element_edges(self):
        data = {"nodes": [{"name": "a"}, {"name": "b"}],
                "edges": [["a", "b", "slot1"], ["a", "b"]]}
        runner.compat_fixups(data)
        self.assertEqual(data["edges"][0], ["a", "b", "slot1", None])
        self.assertEqual(data["edges"][1], ["a", "b"])   # 2-element left alone

    def test_apply_url_map(self):
        data = {"tables": {"t": {"url": "C:\\a\\f.csv"}},
                "nodes": [{"name": "n", "some": {"url": "C:\\a\\g.h5"}}]}
        n = runner.apply_url_map(data, {"C:\\a\\f.csv": "/l/f.csv",
                                        "C:\\a\\g.h5": "/l/g.h5"})
        self.assertEqual(n, 2)
        self.assertEqual(data["tables"]["t"]["url"], "/l/f.csv")

    def test_apply_overrides(self):
        data = {"nodes": [{"name": "a", "type": "input", "max_flow": 10}],
                "timestepper": {"timestep": 1}}
        applied = runner.apply_overrides(
            data, {"nodes": {"a": {"max_flow": 99}},
                   "timestepper": {"timestep": 7}})
        self.assertEqual(data["nodes"][0]["max_flow"], 99)
        self.assertEqual(data["timestepper"]["timestep"], 7)
        self.assertIn("a.max_flow", applied)


class TestScenarios(unittest.TestCase):
    """Pure-Python scenario enumeration used by the picker — no pywr needed.
    The ordering is asserted to match pywr's ScenarioCollection.combinations
    (verified empirically: a C-order cartesian product, last scenario fastest)."""

    MODEL = {"scenarios": [
        {"name": "climate", "size": 3},
        {"name": "demand", "size": 2, "ensemble_names": ["low", "high"]},
    ]}

    def test_dims_default_and_named_members(self):
        dims = graphops.scenario_dims(self.MODEL)
        self.assertEqual([d["name"] for d in dims], ["climate", "demand"])
        # unnamed members fall back to their string index
        self.assertEqual(dims[0]["ensemble_names"], ["0", "1", "2"])
        self.assertEqual(dims[1]["ensemble_names"], ["low", "high"])

    def test_combination_count_is_product(self):
        self.assertEqual(graphops.scenario_combinations(self.MODEL), 6)
        self.assertEqual(graphops.scenario_combinations({}), 1)
        self.assertEqual(graphops.scenario_combinations({"scenarios": []}), 1)

    def test_ensemble_names_clamped_to_size(self):
        short = graphops.scenario_dims(
            {"scenarios": [{"name": "s", "size": 3, "ensemble_names": ["a"]}]})
        self.assertEqual(short[0]["ensemble_names"], ["a", "1", "2"])
        long = graphops.scenario_dims(
            {"scenarios": [{"name": "s", "size": 2,
                            "ensemble_names": ["a", "b", "c"]}]})
        self.assertEqual(long[0]["ensemble_names"], ["a", "b"])

    def test_combo_label_is_c_order(self):
        dims = graphops.scenario_dims(self.MODEL)
        self.assertEqual([graphops.combo_label(dims, i) for i in range(6)], [
            "climate=0, demand=low", "climate=0, demand=high",
            "climate=1, demand=low", "climate=1, demand=high",
            "climate=2, demand=low", "climate=2, demand=high",
        ])
        self.assertEqual(graphops.combo_label([], 0), "base")

    def test_graph_summary_exposes_scenarios(self):
        summary = graphops.graph_summary(
            {"nodes": [], "edges": [], **self.MODEL}, {})
        self.assertEqual(summary["n_combinations"], 6)
        self.assertEqual([d["name"] for d in summary["scenario_dims"]],
                         ["climate", "demand"])


class TestLayoutPicker(unittest.TestCase):
    """The named layouts offered by the picker. A zone model funnels many
    sources into one row, so 'layered' is not always the readable choice."""

    # two sources feeding a chain, plus an edge-less virtual node
    NAMES = ["src_a", "src_b", "river", "resr", "town", "farm", "licence"]
    EDGES = [["src_a", "river"], ["src_b", "river"], ["river", "resr"],
             ["resr", "town"], ["resr", "farm"]]
    TYPES = {"src_a": "catchment", "src_b": "input", "river": "river",
             "resr": "reservoir", "town": "output", "farm": "output",
             "licence": "annualvirtualstorage"}

    def _groups(self):
        return {n: layout.node_group(t) for n, t in self.TYPES.items()}

    def test_node_group_classification(self):
        self.assertEqual(layout.node_group("catchment"), "source")
        self.assertEqual(layout.node_group("reservoir"), "storage")
        self.assertEqual(layout.node_group("output"), "demand")
        self.assertEqual(layout.node_group("annualvirtualstorage"), "virtual")
        self.assertEqual(layout.node_group("riversplit"), "river")
        self.assertEqual(layout.node_group("keatingaquifer"), "other")

    def test_every_layout_places_every_node(self):
        for kind in layout.LAYOUT_KINDS:
            pos = layout.compute(kind, self.NAMES, self.EDGES,
                                 groups=self._groups())
            self.assertEqual(set(pos), set(self.NAMES), kind)
            for name, xy in pos.items():
                self.assertEqual(len(xy), 2, f"{kind}/{name}")

    def test_layouts_are_deterministic(self):
        # no RNG anywhere: the same model must lay out identically every time
        for kind in layout.LAYOUT_KINDS:
            first = layout.compute(kind, self.NAMES, self.EDGES,
                                   groups=self._groups())
            again = layout.compute(kind, self.NAMES, self.EDGES,
                                   groups=self._groups())
            self.assertEqual(first, again, kind)

    def test_unknown_kind_falls_back_to_layered(self):
        self.assertEqual(layout.compute("nonsense", self.NAMES, self.EDGES),
                         layout.compute("layered", self.NAMES, self.EDGES))

    def test_grouped_keeps_a_group_in_one_block(self):
        pos = layout.compute("grouped", self.NAMES, self.EDGES,
                             groups=self._groups())
        # sources share a block, demands share a block, and the blocks are
        # laid out left→right in GROUP_ORDER (source before demand)
        src_x = [pos[n][0] for n in ("src_a", "src_b")]
        demand_x = [pos[n][0] for n in ("town", "farm")]
        self.assertLess(max(src_x), min(demand_x))

    def test_force_layout_spaces_nodes_out(self):
        pos = layout.compute("force", self.NAMES, self.EDGES)
        pts = list(pos.values())
        gaps = [math.dist(a, b) for i, a in enumerate(pts)
                for b in pts[i + 1:]]
        # the spring embedding is renormalised to the usual node spacing, so
        # nothing should end up stacked on top of anything else
        self.assertGreater(min(gaps), 1.0)

    def test_radial_rings_grow_outwards(self):
        pos = layout.compute("radial", self.NAMES, self.EDGES)
        # sources sit on an inner ring, demands further out
        r = lambda n: math.hypot(*pos[n])  # noqa: E731
        self.assertLess(r("src_a"), r("town"))


class TestEdgeProxies(unittest.TestCase):
    """Proxy-link splicing for exact per-edge flow (pure, no pywr).
    Topology: A fans out to Town and B; B is fed by A and s2 → the A→B edge is
    ambiguous (out_deg A = 2, in_deg B = 2)."""

    def _data(self):
        return {"nodes": [{"name": n} for n in ("s1", "s2", "A", "B",
                                                "Town", "Farm")],
                "edges": [["s1", "A"], ["s2", "B"], ["A", "Town"],
                          ["A", "B"], ["B", "Farm"]]}

    def test_proxies_only_the_ambiguous_edge(self):
        data = self._data()
        proxy_map = runner.insert_edge_proxies(data)
        # only edge index 3 (A->B) is ambiguous
        self.assertEqual(proxy_map, {3: "__reader_edge__3"})
        # a link proxy node was added
        self.assertIn({"name": "__reader_edge__3", "type": "link"},
                      data["nodes"])
        # A->B is gone, replaced by A->proxy and proxy->B
        self.assertNotIn(["A", "B"], data["edges"])
        self.assertIn(["A", "__reader_edge__3"], data["edges"])
        self.assertIn(["__reader_edge__3", "B"], data["edges"])
        # the unambiguous edges are untouched
        self.assertIn(["A", "Town"], data["edges"])

    def test_no_ambiguous_edges_is_a_noop(self):
        # a simple chain has no ambiguous edge → nothing spliced
        data = {"nodes": [{"name": n} for n in ("a", "b", "c")],
                "edges": [["a", "b"], ["b", "c"]]}
        before = json.loads(json.dumps(data))
        self.assertEqual(runner.insert_edge_proxies(data), {})
        self.assertEqual(data, before)

    def test_from_slot_preserved_to_slot_skipped(self):
        data = self._data()
        data["edges"][3] = ["A", "B", "branch", None]   # from_slot only
        proxy_map = runner.insert_edge_proxies(data)
        self.assertEqual(list(proxy_map), [3])
        self.assertIn(["A", "__reader_edge__3", "branch", None], data["edges"])

        data2 = self._data()
        data2["edges"][3] = ["A", "B", "branch", "inlet"]  # to_slot present
        # an explicit destination slot is left alone (kept simple)
        self.assertEqual(runner.insert_edge_proxies(data2), {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
