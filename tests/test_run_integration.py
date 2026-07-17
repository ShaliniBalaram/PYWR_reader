"""End-to-end run test: actually executes a model with pywr.

Skipped automatically when the pywr environment has not been set up
(so the fast unit suite still passes on a bare checkout). Run explicitly:

    ./.venv/bin/python -m unittest tests.test_run_integration -v
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from pywr_reader import envsetup  # noqa: E402

EXAMPLE = os.path.join(ROOT, "examples", "gw_network", "pywr_model.json")
SCENARIO = os.path.join(ROOT, "examples", "scenario_network", "pywr_model.json")
SPLIT = os.path.join(ROOT, "examples", "split_network", "pywr_model.json")
ENV_READY = envsetup.check_env()["ready"]


def _run(model, out, overrides=None):
    python = envsetup.env_python()
    cmd = [python, os.path.join(ROOT, "pywr_reader", "runner.py"), model, out]
    if overrides is not None:
        over_file = out + ".over.json"
        with open(over_file, "w") as fh:
            json.dump(overrides, fh)
        cmd.append(over_file)
    subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    with open(out) as fh:
        return json.load(fh)


@unittest.skipUnless(ENV_READY, "pywr environment not set up")
class TestRealRun(unittest.TestCase):
    def test_runner_produces_series(self):
        python = envsetup.env_python()
        out = os.path.join(tempfile.mkdtemp(), "result.json")
        proc = subprocess.run(
            [python, os.path.join(ROOT, "pywr_reader", "runner.py"),
             EXAMPLE, out],
            capture_output=True, text=True, timeout=600)
        self.assertTrue(os.path.isfile(out), proc.stderr[-2000:])
        with open(out) as fh:
            result = json.load(fh)
        self.assertTrue(result["ok"], result.get("error"))
        self.assertEqual(len(result["dates"]), 366)      # daily, leap year 2000
        # the reservoir should have a recorded volume series
        self.assertIn("Reservoir_A", result["nodes"])
        vol = result["nodes"]["Reservoir_A"].get("volume")
        self.assertEqual(len(vol), 366)
        # a demand node should have a flow series
        self.assertIn("flow", result["nodes"]["Demand_Urban"])
        # the result always carries a warnings list (empty for this clean model)
        self.assertIn("warnings", result)
        self.assertIsInstance(result["warnings"], list)

    def test_whatif_override_changes_result(self):
        python = envsetup.env_python()
        tmp = tempfile.mkdtemp()
        base_out = os.path.join(tmp, "base.json")
        over_out = os.path.join(tmp, "over.json")
        over_file = os.path.join(tmp, "over_in.json")
        # halve the reservoir's starting volume
        with open(over_file, "w") as fh:
            json.dump({"nodes": {"Reservoir_A": {"initial_volume": 50}}}, fh)

        for out, extra in ((base_out, []), (over_out, [over_file])):
            subprocess.run(
                [python, os.path.join(ROOT, "pywr_reader", "runner.py"),
                 EXAMPLE, out] + extra,
                capture_output=True, text=True, timeout=600)
        with open(base_out) as fh:
            base = json.load(fh)
        with open(over_out) as fh:
            over = json.load(fh)
        self.assertTrue(base["ok"] and over["ok"])
        # first reservoir volume should differ (300 vs 50 start)
        self.assertNotEqual(base["nodes"]["Reservoir_A"]["volume"][0],
                            over["nodes"]["Reservoir_A"]["volume"][0])


@unittest.skipUnless(ENV_READY, "pywr environment not set up")
class TestScenarioRun(unittest.TestCase):
    def test_scenario_metadata_reported(self):
        tmp = os.path.join(tempfile.mkdtemp(), "s.json")
        result = _run(SCENARIO, tmp)
        self.assertTrue(result["ok"], result.get("error"))
        scen = result["scenario"]
        self.assertEqual(scen["combinations"], 3)
        self.assertEqual(scen["shown"], 0)          # default combination
        self.assertEqual(len(scen["dims"]), 1)
        dim = scen["dims"][0]
        self.assertEqual(dim["name"], "demand")
        self.assertEqual(dim["size"], 3)
        self.assertEqual(dim["ensemble_names"], ["low", "mid", "high"])

    def test_scenario_index_selects_member(self):
        tmp = tempfile.mkdtemp()
        flows = {}
        for idx in (0, 1, 2):
            out = os.path.join(tmp, f"s{idx}.json")
            res = _run(SCENARIO, out, {"scenario_index": idx})
            self.assertTrue(res["ok"], res.get("error"))
            self.assertEqual(res["scenario"]["shown"], idx)
            flows[idx] = res["nodes"]["Town"]["flow"]
        # the three demand members draw distinctly different amounts
        self.assertEqual(flows[0][0], 40.0)          # low
        self.assertEqual(flows[1][0], 70.0)          # mid
        self.assertGreater(flows[2][0], 70.0)        # high draws the most
        self.assertNotEqual(flows[0], flows[2])

    def test_out_of_range_index_falls_back_to_zero(self):
        out = os.path.join(tempfile.mkdtemp(), "s.json")
        res = _run(SCENARIO, out, {"scenario_index": 99})
        self.assertTrue(res["ok"], res.get("error"))
        self.assertEqual(res["scenario"]["shown"], 0)


@unittest.skipUnless(ENV_READY, "pywr environment not set up")
class TestExactEdges(unittest.TestCase):
    def test_ambiguous_edge_recorded_exactly(self):
        out = os.path.join(tempfile.mkdtemp(), "e.json")
        res = _run(SPLIT, out)
        self.assertTrue(res["ok"], res.get("error"))
        # Junction total 100 (45 to Town, 55 to Mixer); Mixer total 95.
        self.assertEqual(res["nodes"]["Junction"]["flow"][0], 100.0)
        self.assertEqual(res["nodes"]["Mixer"]["flow"][0], 95.0)
        # the ambiguous edge is Junction->Mixer, index 3 in the model's edges
        exact = res["exact_edges"]
        self.assertIn("3", exact)
        self.assertEqual(exact["3"][0], 55.0)                 # the true flow
        # ...which is NOT the min-of-endpoints estimate (min(100, 95) = 95)
        self.assertNotEqual(exact["3"][0], 95.0)
        # proxy nodes never leak into the node series
        self.assertFalse(any(n.startswith("__reader_edge__")
                             for n in res["nodes"]))

    def test_no_ambiguous_edges_leaves_exact_empty(self):
        # gw_network's edges are all endpoint-pinned → no proxies spliced
        out = os.path.join(tempfile.mkdtemp(), "e.json")
        res = _run(EXAMPLE, out)
        self.assertTrue(res["ok"], res.get("error"))
        self.assertEqual(res["exact_edges"], {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
