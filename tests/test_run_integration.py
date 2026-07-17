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
class TestExactEdges(unittest.TestCase):
    def test_no_ambiguous_edges_leaves_exact_empty(self):
        # gw_network's edges are all endpoint-pinned → no proxies spliced
        out = os.path.join(tempfile.mkdtemp(), "e.json")
        res = _run(EXAMPLE, out)
        self.assertTrue(res["ok"], res.get("error"))
        self.assertEqual(res["exact_edges"], {})


@unittest.skipUnless(ENV_READY, "pywr environment not set up")
class TestDataView(unittest.TestCase):
    """dataview runs inside the pywr environment (pandas/PyTables live there),
    so like the runner it is driven as a subprocess."""

    def _view(self, path, key=None):
        out = os.path.join(tempfile.mkdtemp(), "view.json")
        cmd = [envsetup.env_python(),
               os.path.join(ROOT, "pywr_reader", "dataview.py"), path, out]
        if key:
            cmd.append(key)
        subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        with open(out) as fh:
            return json.load(fh)

    def _make_h5(self, fmt):
        """A timeseries h5 written by pandas, in the given format."""
        tmp = os.path.join(tempfile.mkdtemp(), f"{fmt}.h5")
        script = (
            "import pandas as pd, numpy as np\n"
            f"idx = pd.date_range('1920-01-01', periods=1000, freq='D')\n"
            "df = pd.DataFrame({'river': np.arange(1000.0),"
            " 'gw': np.arange(1000.0)}, index=idx)\n"
            f"df.to_hdf(r'{tmp}', key='flows', format='{fmt}')\n")
        subprocess.run([envsetup.env_python(), "-c", script],
                       capture_output=True, text=True, timeout=300)
        return tmp

    def test_reads_a_fixed_format_h5_and_reports_its_real_length(self):
        # the trap this caught on a real file: a fixed-format store's .nrows
        # is None, and the length is in .shape — without that the viewer
        # reports its own page size as the file's row count
        res = self._view(self._make_h5("fixed"), "/flows")
        self.assertTrue(res["ok"], res.get("error"))
        self.assertEqual([k["key"] for k in res["keys"]], ["/flows"])
        self.assertEqual(res["keys"][0]["rows"], 1000)
        preview = res["preview"]
        self.assertEqual(preview["n_rows"], 1000)     # not 200, the page size
        self.assertTrue(preview["truncated"])
        self.assertEqual(len(preview["rows"]), 200)   # only the head is read
        self.assertEqual(preview["columns"], ["river", "gw"])
        self.assertTrue(preview["index"][0].startswith("1920-01-01"))

    def test_reads_a_table_format_h5(self):
        res = self._view(self._make_h5("table"), "/flows")
        self.assertTrue(res["ok"], res.get("error"))
        self.assertEqual(res["preview"]["n_rows"], 1000)

    def test_a_single_key_previews_without_being_asked(self):
        res = self._view(self._make_h5("fixed"))
        self.assertEqual(res["key"], "/flows")
        self.assertIn("preview", res)

    def test_reads_a_csv(self):
        res = self._view(os.path.join(ROOT, "examples", "gw_network",
                                      "params.csv"))
        self.assertTrue(res["ok"], res.get("error"))
        self.assertEqual(res["kind"], "csv")
        self.assertGreater(res["preview"]["n_cols"], 1)

    def test_reports_an_unreadable_file_rather_than_crashing(self):
        res = self._view(EXAMPLE)          # a .json model, not data
        self.assertFalse(res["ok"])
        self.assertIn("json", res["error"])

    def _series(self, path, key=None):
        out = os.path.join(tempfile.mkdtemp(), "s.json")
        cmd = [envsetup.env_python(),
               os.path.join(ROOT, "pywr_reader", "dataview.py"), path, out]
        if key:
            cmd.append(key)
        cmd.append("--series")
        subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        with open(out) as fh:
            return json.load(fh)

    def test_series_reads_the_whole_column_downsampled(self):
        # the plot needs the whole thing, not the head — but thinned so a long
        # series doesn't ship every point
        res = self._series(self._make_h5("fixed"), "/flows")
        self.assertTrue(res["ok"], res.get("error"))
        self.assertEqual(res["kind"], "series")
        self.assertEqual(res["n_rows"], 1000)
        names = [s["name"] for s in res["series"]]
        self.assertEqual(names, ["river", "gw"])
        # covers the whole span, first date to last
        self.assertTrue(res["dates"][0].startswith("1920-01-01"))
        self.assertTrue(res["dates"][-1].startswith("1922-09-26"))
        # a 1000-row series is under the plot cap, so it is not thinned
        self.assertFalse(res["downsampled"])
        self.assertEqual(len(res["series"][0]["values"]), 1000)

    def test_series_window_returns_full_daily_detail(self):
        # a deep zoom re-requests a row window; a window under the plot cap
        # comes back at every row (step 1), and each point carries its
        # absolute row so the client can stitch chunks together
        path = self._make_h5("table")
        s = os.path.join(tempfile.mkdtemp(), "w.json")
        subprocess.run([envsetup.env_python(),
                        os.path.join(ROOT, "pywr_reader", "dataview.py"),
                        path, s, "/flows", "--series",
                        "--start", "200", "--stop", "500"],
                       capture_output=True, text=True, timeout=300)
        with open(s) as fh:
            res = json.load(fh)
        self.assertTrue(res["ok"], res.get("error"))
        self.assertEqual(res["n_rows"], 1000)            # the whole file
        self.assertEqual((res["start"], res["stop"]), (200, 500))
        self.assertFalse(res["downsampled"])             # 300 rows < the cap
        self.assertEqual(res["rows"][0], 200)            # absolute row indices
        self.assertEqual(res["rows"][-1], 499)
        self.assertEqual(res["rows"][1] - res["rows"][0], 1)   # every day
        self.assertEqual(len(res["rows"]), 300)

    def test_series_reads_a_csv_and_windows_it(self):
        # csv has no key; its columns plot directly, and a window reads only
        # those rows (skiprows) at full detail with absolute indices
        csv = os.path.join(ROOT, "examples", "gw_network", "params.csv")
        full = self._series(csv)
        self.assertTrue(full["ok"], full.get("error"))
        self.assertGreater(full["n_rows"], 300)
        self.assertIn("Rainfall_Flow", [s["name"] for s in full["series"]])

        out = os.path.join(tempfile.mkdtemp(), "w.json")
        subprocess.run([envsetup.env_python(),
                        os.path.join(ROOT, "pywr_reader", "dataview.py"),
                        csv, out, "--series", "--start", "10", "--stop", "40"],
                       capture_output=True, text=True, timeout=120)
        with open(out) as fh:
            win = json.load(fh)
        self.assertEqual((win["start"], win["stop"]), (10, 40))
        self.assertEqual(win["rows"][0], 10)
        self.assertEqual(win["rows"][-1], 39)
        self.assertFalse(win["downsampled"])

    def test_series_thins_a_long_column(self):
        # 8000 rows > the 3000-point plot cap → downsampled, last point kept
        tmp = os.path.join(tempfile.mkdtemp(), "long.h5")
        subprocess.run([envsetup.env_python(), "-c",
                        "import pandas as pd, numpy as np;"
                        "idx=pd.date_range('1900-01-01', periods=8000, freq='D');"
                        "pd.DataFrame({'v': np.arange(8000.0)}, index=idx)"
                        f".to_hdf(r'{tmp}', key='s', format='table')"],
                       capture_output=True, text=True, timeout=300)
        res = self._series(tmp, "/s")
        self.assertEqual(res["n_rows"], 8000)
        self.assertTrue(res["downsampled"])
        self.assertLessEqual(len(res["series"][0]["values"]), 3001)
        self.assertEqual(res["series"][0]["values"][-1], 7999.0)   # last kept


if __name__ == "__main__":
    unittest.main(verbosity=2)
