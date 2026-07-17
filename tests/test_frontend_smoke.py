"""Smoke tests that drive the real UI in a headless browser.

Skipped automatically unless playwright and its chromium are installed, so
./run_tests.sh still needs nothing but Flask. To enable them:

    ./.venv/bin/pip install -r requirements-dev.txt
    ./.venv/bin/playwright install chromium
    ./.venv/bin/python -m unittest tests.test_frontend_smoke -v

tests/test_frontend_contract.py checks that app.js still agrees with
index.html and app.py; these check that the thing actually works when clicked.
The app is served in-process, so the Flask STATE the tests set up is the same
STATE the page talks to.
"""

import logging
import os
import sys
import threading
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import app as app_module  # noqa: E402

EXAMPLE = os.path.join(ROOT, "examples", "gw_network", "pywr_model.json")

try:
    from playwright.sync_api import sync_playwright
    HAVE_PLAYWRIGHT = True
except ImportError:                       # pragma: no cover - env dependent
    HAVE_PLAYWRIGHT = False


class _Server(threading.Thread):
    """The real app on a free port, shut down cleanly at the end."""

    def __init__(self):
        super().__init__(daemon=True)
        from werkzeug.serving import make_server
        self.srv = make_server("127.0.0.1", 0, app_module.app, threaded=True)
        self.port = self.srv.server_port

    def run(self):
        self.srv.serve_forever()

    def stop(self):
        self.srv.shutdown()


def _browser_available():
    if not HAVE_PLAYWRIGHT:
        return False
    try:                                  # chromium is a separate download
        with sync_playwright() as p:
            p.chromium.launch().close()
        return True
    except Exception:                     # noqa: BLE001 - any launch failure
        return False


BROWSER_OK = _browser_available()


@unittest.skipUnless(BROWSER_OK, "playwright + chromium not installed")
class TestFrontendSmoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app_module.app.testing = True
        logging.getLogger("werkzeug").setLevel(logging.ERROR)  # quiet the log
        cls.server = _Server()
        cls.server.start()
        cls.base = f"http://127.0.0.1:{cls.server.port}"
        cls._pw = sync_playwright().start()
        cls.browser = cls._pw.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls._pw.stop()
        cls.server.stop()

    def setUp(self):
        # a model in STATE before the page loads — the page fetches /api/graph
        app_module.STATE.update(model=None, positions={}, path=None,
                                dirty=False, warnings=[], data_dirs=[],
                                data=None)
        app_module.app.test_client().post("/api/open", json={"path": EXAMPLE})
        self.errors = []
        self.page = self.browser.new_page(viewport={"width": 1280,
                                                    "height": 800})
        self.page.on("console", lambda m: m.type == "error"
                     and self.errors.append(m.text))
        self.page.on("pageerror", lambda e: self.errors.append(str(e)))
        self.page.goto(self.base)
        # app.js is a classic script, so its top-level `const S` is a lexical
        # binding, not a window property — wait on the drawn canvas instead
        self.page.wait_for_selector("#canvas .node")

    def tearDown(self):
        self.page.close()

    def assertNoConsoleErrors(self):
        self.assertEqual(self.errors, [], f"console errors: {self.errors}")

    def _bbox(self):
        # `S` is a top-level const in a classic script: reachable as a bare
        # name in page context, but not as window.S
        return self.page.evaluate(
            "() => { const p = Object.values(S.positions);"
            "  const xs = p.map(q => q[0]), ys = p.map(q => q[1]);"
            "  return [Math.max(...xs) - Math.min(...xs),"
            "          Math.max(...ys) - Math.min(...ys)]; }")

    def _apply_layout(self, label, differs_from=None):
        """Pick a layout from the menu and wait for it to actually land."""
        self.page.click("#btn-layout")
        self.page.click(f"#layout-menu .menu-item:has-text('{label}')")
        if differs_from is None:
            self.page.wait_for_selector("#btn-undo-layout", state="visible")
        else:   # positions must have moved off the previous arrangement
            self.page.wait_for_function(
                "w => { const xs = Object.values(S.positions).map(q => q[0]);"
                "  return Math.abs((Math.max(...xs) - Math.min(...xs)) - w)"
                "         > 0.5; }", arg=differs_from[0])

    # -- the network actually draws ------------------------------------
    def test_model_renders_nodes_and_edges_on_the_canvas(self):
        self.assertEqual(self.page.locator("#canvas .node").count(), 11)
        self.assertEqual(self.page.locator("#canvas .edge").count(), 10)
        self.assertNoConsoleErrors()

    def test_clicking_a_node_opens_its_panel_and_traces_the_path(self):
        # Rainfall_Catchment leaves the groundwater branch off its path, so
        # the trace both highlights and dims. (Reservoir_A would not do: it
        # sits mid-network, so every node is upstream or downstream of it and
        # nothing dims — which is correct, just untestable here.)
        self.page.evaluate("selectNode('Rainfall_Catchment')")
        self.page.wait_for_selector("#canvas .node.sel")
        highlighted = self.page.evaluate(
            "() => [...document.querySelectorAll('#canvas .edge')]"
            ".filter(e => e.style.stroke).length")
        self.assertGreater(highlighted, 0, "no edge was highlighted")
        self.assertGreater(self.page.locator("#canvas .dim").count(), 0,
                           "nothing off the path was dimmed")
        self.assertIn("Rainfall_Catchment", self.page.inner_text("#tab-node"))
        self.assertNoConsoleErrors()

    def test_trace_off_clears_the_highlighting(self):
        self.page.evaluate("selectNode('Rainfall_Catchment')")
        self.page.wait_for_selector("#canvas .dim")
        self.page.click("#tab-node button:has-text('Off')")
        self.page.wait_for_function(
            "() => document.querySelectorAll('#canvas .dim').length === 0")
        self.assertNoConsoleErrors()

    # -- layout picker --------------------------------------------------
    def test_layout_menu_lists_every_layout_the_server_offers(self):
        kinds = len(app_module.layout_mod.LAYOUTS)
        self.page.click("#btn-layout")
        self.assertEqual(self.page.locator("#layout-menu .menu-item").count(),
                         kinds)
        self.assertNoConsoleErrors()

    def test_each_layout_applies_and_moves_the_nodes(self):
        seen = []
        for spec in app_module.layout_mod.LAYOUTS:
            self._apply_layout(spec["label"])
            box = self._bbox()
            self.assertTrue(all(v > 0 for v in box), spec["kind"])
            # every node still placed, none lost or stacked at the origin
            self.assertEqual(
                self.page.evaluate("() => Object.keys(S.positions).length"), 11,
                spec["kind"])
            seen.append(box)
            self.assertNoConsoleErrors()
        # the layouts are genuinely different arrangements, not one repeated
        self.assertGreater(len({tuple(b) for b in seen}), 1)

    def test_undo_restores_the_previous_positions(self):
        self._apply_layout("Grouped by function")
        before = self._bbox()
        self._apply_layout("Radial", differs_from=before)
        self.assertNotEqual(self._bbox(), before)   # it really did change
        self.page.click("#btn-undo-layout")
        # .hidden is display:none, so wait for hidden — never for "visible"
        self.page.wait_for_selector("#btn-undo-layout", state="hidden")
        self.assertEqual(self._bbox(), before)
        self.assertNoConsoleErrors()

    # -- add menu -------------------------------------------------------
    def test_add_menu_switches_mode_and_says_what_it_is_placing(self):
        self.page.click("#btn-add")
        self.page.click("#btn-mode-addnode")
        self.assertIn("Node", self.page.inner_text("#btn-add"))
        self.assertEqual(self.page.evaluate("S.mode"), "addnode")
        self.page.click("#btn-mode-select")
        self.assertIn("Add", self.page.inner_text("#btn-add"))
        self.assertNoConsoleErrors()

    # -- JSON editing ---------------------------------------------------
    def test_json_editor_round_trips_an_edit(self):
        self.page.evaluate("openModelExplorer()")
        self.page.click("button:has-text('{ } edit JSON')")
        self.page.wait_for_selector(".json-edit")
        self.page.evaluate("""() => {
          const box = document.querySelector('.json-edit');
          const m = JSON.parse(box.value);
          m.metadata.title = 'Smoke Tested';
          box.value = JSON.stringify(m, null, 2);
        }""")
        self.page.click("#modal button.primary")
        self.page.wait_for_selector("#modal-backdrop", state="hidden")
        self.assertEqual(app_module.STATE["model"]["metadata"]["title"],
                         "Smoke Tested")
        self.assertNoConsoleErrors()

    def test_json_editor_shows_an_error_and_keeps_your_text(self):
        self.page.evaluate("openModelExplorer()")
        self.page.click("button:has-text('{ } edit JSON')")
        self.page.wait_for_selector(".json-edit")
        self.page.evaluate(
            "() => { document.querySelector('.json-edit').value = '{ nope'; }")
        self.page.click("#modal button.primary")
        self.page.wait_for_selector(".json-err:not(.hidden)")
        self.assertIn("Invalid JSON", self.page.inner_text(".json-err"))
        # the box still holds what was typed, and the model is untouched
        self.assertEqual(self.page.input_value(".json-edit"), "{ nope")
        self.assertEqual(len(app_module.STATE["model"]["nodes"]), 11)
        self.assertNoConsoleErrors()


if __name__ == "__main__":
    unittest.main(verbosity=2)
