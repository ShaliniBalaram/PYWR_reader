"""Contract tests between the frontend and the rest of the app.

    ./.venv/bin/python -m unittest tests.test_frontend_contract -v

The frontend is vanilla JS with no build step, so nothing checks that
static/app.js still agrees with static/index.html and app.py. Rename an
element id in one file and not the other, or add an API path the server
doesn't serve, and it breaks only when a user clicks the thing.

These read the sources as text and compare — no browser, no dependencies, so
they run everywhere and every time. Actual behaviour is covered by
tests/test_frontend_smoke.py, which drives a real browser when one exists.
"""

import os
import re
import sys
import unittest
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

JS = Path(ROOT, "static", "app.js").read_text(encoding="utf-8")
HTML = Path(ROOT, "static", "index.html").read_text(encoding="utf-8")
# routes live in the API blueprints (pywr_reader/api/*.py), registered by app.py
API = "\n".join(p.read_text(encoding="utf-8")
                for p in sorted(Path(ROOT, "pywr_reader", "api").glob("*.py"))
                if not p.name.startswith("._"))    # skip macOS AppleDouble files


def _segments(path):
    """URL path → comparable segments, with dynamic bits as '*'.

    '/api/run/${id}/frames?start=1' → ['api', 'run', '*', 'frames']
    '/api/run/'  (built by concatenation) → ['api', 'run', '*']
    """
    path = path.split("?")[0]
    path = re.sub(r"\$\{[^}]*\}", "*", path)      # `${runId}` → *
    if path.endswith("/"):                         # "/api/run/" + runId
        path += "*"
    out = []
    for part in path.strip("/").split("/"):
        out.append("*" if part.startswith("*") or not part else part)
    return out


class TestDomContract(unittest.TestCase):
    def test_every_id_the_js_looks_up_exists_in_the_html(self):
        # $("thing") returns null for a missing id, and the TypeError only
        # surfaces when that code path runs — exactly the silent breakage
        used = set(re.findall(r'\$\("([\w-]+)"\)', JS))
        declared = set(re.findall(r'id="([\w-]+)"', HTML))
        self.assertTrue(used, "found no $(\"id\") lookups — regex out of date?")
        missing = sorted(used - declared)
        self.assertEqual(missing, [],
                         f"app.js looks up ids that index.html does not "
                         f"define: {missing}")

    def test_the_dollar_helper_is_still_getelementbyid(self):
        # the test above is only meaningful while $ means getElementById
        self.assertIn("const $ = id => document.getElementById(id)", JS)


class TestApiContract(unittest.TestCase):
    def _routes(self):
        rules = re.findall(r'@bp\.(?:get|post|route)\("([^"]+)"', API)
        return [re.sub(r"<[^>]+>", "*", r) for r in rules]

    def test_every_api_path_the_js_calls_is_served(self):
        # every /api/... string literal in the frontend, however it's called
        called = set(re.findall(r'["\'`](/api/[^"\'`]*)["\'`]', JS))
        self.assertTrue(called, "found no /api/ paths — regex out of date?")
        served = [_segments(r) for r in self._routes()]
        unserved = []
        for path in sorted(called):
            if not any(_segments(path) == rule for rule in served):
                unserved.append(path)
        self.assertEqual(unserved, [],
                         f"app.js calls paths app.py does not serve: "
                         f"{unserved}")

    def test_known_routes_are_reachable(self):
        # a canary: if the extraction silently stops matching, this fails too
        served = [_segments(r) for r in self._routes()]
        for path in ("/api/graph", "/api/model/raw", "/api/layouts",
                     "/api/run/${id}/frames?start=0"):
            self.assertIn(_segments(path), served, path)


class TestNoDebugLeftovers(unittest.TestCase):
    def test_no_console_or_debugger_in_shipped_js(self):
        for pattern in (r"\bconsole\.log\(", r"\bdebugger\b"):
            self.assertEqual(re.findall(pattern, JS), [],
                             f"{pattern} left in static/app.js")


if __name__ == "__main__":
    unittest.main(verbosity=2)
