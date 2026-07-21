"""Smoke tests that drive the real UI in a headless browser.

Skipped automatically unless playwright and its chromium are installed, so
./run_tests.sh still needs nothing but Flask. To enable them:

    ./.venv/bin/pip install -r requirements-dev.txt
    ./.venv/bin/playwright install chromium
    ./.venv/bin/python -m unittest tests.test_frontend_smoke -v

tests/test_frontend_contract.py checks that app.js still agrees with
index.html and app.py; these check that the thing actually works when clicked.
The app is served in-process, so the session the tests set up is the same
session the page talks to.
"""

import logging
import os
import sys
import threading
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import app as app_module  # noqa: E402
from pywr_reader import layout  # noqa: E402
from pywr_reader.api import files  # noqa: E402

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
        # open a model before the page loads — the page fetches /api/graph
        app_module.WORKSPACE.reset()
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
        kinds = len(layout.LAYOUTS)
        self.page.click("#btn-layout")
        self.assertEqual(self.page.locator("#layout-menu .menu-item").count(),
                         kinds)
        self.assertNoConsoleErrors()

    def test_each_layout_applies_and_moves_the_nodes(self):
        seen = []
        for spec in layout.LAYOUTS:
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

    # -- the Open dialog ------------------------------------------------
    CRUMB = "#modal .mono.muted.small"

    def _open_dialog(self):
        """Open it and wait for the *listing* — the dialog paints a 'loading…'
        row of class .entry first, so waiting on .entry alone races the fetch.
        The crumb only gets text once browse() has come back."""
        self.page.click("#btn-open")
        self.page.wait_for_function(
            "sel => { const c = document.querySelector(sel);"
            "  return c && c.textContent.length > 0; }", arg=self.CRUMB)

    def test_open_dialog_takes_its_roots_from_the_server(self):
        # the shortcuts must be whatever the server's platform offers —
        # a hard-coded "Volumes" button left Windows unable to reach a drive
        self._open_dialog()
        expected = [r["label"] for r in files.browse_roots()]
        shown = self.page.evaluate(
            "() => [...document.querySelectorAll('#modal .row.gap button')]"
            ".map(b => b.textContent)")
        for label in expected:
            self.assertIn(label, shown)
        self.assertNoConsoleErrors()

    def test_open_dialog_navigates_using_the_server_path(self):
        self._open_dialog()
        crumb = self.CRUMB
        before = self.page.inner_text(crumb)
        self.page.click(".browser-list .entry:not(:has-text('..')):has-text('📁')")
        self.page.wait_for_function(
            "b => document.querySelector('#modal .mono.muted.small')"
            ".textContent !== b", arg=before)
        # it moved somewhere below where it started, with no "/" spliced in
        after = self.page.inner_text(crumb)
        self.assertTrue(after.startswith(before), f"{before} -> {after}")
        self.assertNotIn("//", after)
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
        self.assertEqual(app_module.WORKSPACE.model["metadata"]["title"],
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
        self.assertEqual(len(app_module.WORKSPACE.model["nodes"]), 11)
        self.assertNoConsoleErrors()


    # -- the live JSON dock ---------------------------------------------
    def _open_dock(self, scope, node=None):
        if node:
            self.page.evaluate("n => selectNode(n)", arg=node)
        self.page.evaluate("toggleDock(true)")
        self.page.click(f"#dock-scopes button[data-scope='{scope}']")
        self.page.wait_for_function(
            "() => document.getElementById('dock-status').textContent"
            " === 'in sync'")

    def _dock_json(self):
        return self.page.evaluate(
            "() => JSON.parse(document.getElementById('dock-text').value)")

    def _edit_dock(self, mutate_js):
        """Rewrite the dock's JSON in the page and mark it edited, the way
        typing would."""
        self.page.evaluate("""fn => {
          const box = document.getElementById('dock-text');
          const doc = JSON.parse(box.value);
          (new Function('doc', fn))(doc);
          box.value = JSON.stringify(doc, null, 2);
          box.dispatchEvent(new Event('input', {bubbles: true}));
        }""", arg=mutate_js)

    def test_dock_slice_holds_the_node_and_what_hangs_off_it(self):
        self._open_dock("related", node="Demand_Urban")
        doc = self._dock_json()
        self.assertEqual(doc["node"]["name"], "Demand_Urban")
        # the recorder watching this node, and no other node's recorder
        self.assertIn("Urban_supply", doc["recorders"])
        self.assertNotIn("Gauge_flow", doc["recorders"])
        self.assertNoConsoleErrors()

    def test_dock_follows_an_edit_made_on_the_canvas(self):
        self._open_dock("related", node="Demand_Urban")
        self.page.evaluate("""async () => {
          await fetch('/api/node/update', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({name: 'Demand_Urban', changes: {cost: -321}}),
          }).then(r => r.json()).then(updateGraph);
        }""")
        self.page.wait_for_function(
            "() => JSON.parse(document.getElementById('dock-text').value)"
            ".node.cost === -321")
        self.assertEqual(self.page.inner_text("#dock-status"), "in sync")
        self.assertNoConsoleErrors()

    def test_dock_apply_updates_the_model_and_removing_an_entry_removes_it(self):
        self._open_dock("related", node="Demand_Urban")
        self._edit_dock("doc.node.cost = -55; delete doc.recorders.Urban_supply;")
        self.page.click("#dock-apply")
        self.page.wait_for_function(
            "() => document.getElementById('dock-status').textContent"
            " === 'in sync'")
        node = next(n for n in app_module.WORKSPACE.model["nodes"]
                    if n["name"] == "Demand_Urban")
        self.assertEqual(node["cost"], -55)
        # the one dropped from the slice is gone; its neighbours are not
        self.assertNotIn("Urban_supply", app_module.WORKSPACE.model["recorders"])
        self.assertIn("Gauge_flow", app_module.WORKSPACE.model["recorders"])
        self.assertNoConsoleErrors()

    def test_dock_rename_carries_every_reference(self):
        self._open_dock("related", node="Demand_Urban")
        self._edit_dock("doc.node.name = 'Demand_Town';")
        self.page.click("#dock-apply")
        self.page.wait_for_function(
            "() => document.getElementById('dock-target').textContent"
            " === 'Demand_Town'")
        model = app_module.WORKSPACE.model
        self.assertEqual(model["recorders"]["Urban_supply"]["node"], "Demand_Town")
        self.assertTrue(any("Demand_Town" in e for e in model["edges"]))
        self.assertFalse(any("Demand_Urban" in e for e in model["edges"]))
        self.assertNoConsoleErrors()

    def test_dock_keeps_your_typing_when_the_model_moves_under_it(self):
        self._open_dock("related", node="Demand_Urban")
        self._edit_dock("doc.node.cost = -999;")
        self.page.evaluate("selectNode('Demand_Irrigation')")   # model view moved on
        self.page.wait_for_selector("#dock-bar:not(.hidden)")
        # the unapplied text is still there, still pointed at the old node
        self.assertIn("-999", self.page.input_value("#dock-text"))
        self.assertEqual(self.page.inner_text("#dock-target"), "Demand_Urban")
        self.page.click("#dock-reload")
        self.page.wait_for_function(
            "() => document.getElementById('dock-target').textContent"
            " === 'Demand_Irrigation'")
        self.assertNotIn("-999", self.page.input_value("#dock-text"))
        self.assertNoConsoleErrors()

    def test_dock_reports_a_rejected_edit_and_keeps_your_text(self):
        self._open_dock("model")
        self._edit_dock("doc.edges[0][1] = 'NoSuchNode';")
        self.page.click("#dock-apply")
        self.page.wait_for_selector("#dock-err:not(.hidden)")
        self.assertIn("NoSuchNode", self.page.inner_text("#dock-err"))
        self.assertIn("NoSuchNode", self.page.input_value("#dock-text"))
        self.assertEqual(len(app_module.WORKSPACE.model["nodes"]), 11)
        # no assertNoConsoleErrors here: the rejected POST is a 400, and the
        # browser logs every 400 as a console error. That 400 is the test.


    # -- reference safety for parameters / recorders --------------------
    def _wire_a_parameter(self):
        """Give the example model a parameter chain to rename and break."""
        self.page.evaluate("""async () => {
          const m = await (await fetch('/api/model/raw')).json();
          m.parameters = {
            urban_cap: {type: 'Aggregated', agg_func: 'product',
                        parameters: ['urban_base']},
            urban_base: {type: 'constant', value: 7},
          };
          m.nodes.find(n => n.name === 'Demand_Urban').max_flow = 'urban_cap';
          await fetch('/api/model/raw', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({model: m}),
          }).then(r => r.json()).then(updateGraph);
        }""")

    def _explorer_row_button(self, section, name, label):
        self.page.evaluate("openModelExplorer()")
        self.page.wait_for_selector(".explorer-body")
        self.page.click(f".explorer-nav button[data-sec='{section}']")
        self.page.fill(".explorer-filter", name)
        self.page.wait_for_selector(".explorer-body details")
        self.page.evaluate("""label => {
          const row = document.querySelector('.explorer-body details summary');
          [...row.querySelectorAll('button')]
            .find(b => b.textContent === label).click();
        }""", arg=label)

    def test_explorer_renames_a_parameter_and_references_follow(self):
        self._wire_a_parameter()
        self._explorer_row_button("Parameters", "urban_base", "rename")
        self.page.wait_for_selector("#modal input[type=text]")
        # the dialog says what is at stake before you commit
        self.assertIn("1 place refers to it", self.page.inner_text("#modal p"))
        self.page.fill("#modal input[type=text]", "urban_baseline")
        self.page.click("#modal button.primary")
        self.page.wait_for_selector(".explorer-body details")
        params = app_module.WORKSPACE.model["parameters"]
        self.assertIn("urban_baseline", params)
        self.assertEqual(params["urban_cap"]["parameters"], ["urban_baseline"])
        self.assertNoConsoleErrors()

    def test_explorer_delete_warns_about_what_still_points_at_it(self):
        self._wire_a_parameter()
        self.page.evaluate("window.confirm = () => true")
        self._explorer_row_button("Parameters", "urban_base", "✕")
        self.page.wait_for_function(
            "() => !document.getElementById('toast').classList.contains('hidden')")
        self.assertIn("still referenced", self.page.inner_text("#toast"))
        self.assertNotIn("urban_base", app_module.WORKSPACE.model["parameters"])
        self.assertNoConsoleErrors()

    def test_dock_offers_to_carry_references_when_a_key_is_renamed(self):
        self._wire_a_parameter()
        self.page.evaluate("""() => {
          window.__asked = [];
          window.confirm = m => { window.__asked.push(m); return true; };
        }""")
        self._open_dock("related", node="Demand_Urban")
        self._edit_dock("""
          doc.parameters.urban_baseline = doc.parameters.urban_base;
          delete doc.parameters.urban_base;
        """)
        self.page.click("#dock-apply")
        self.page.wait_for_function(
            "() => document.getElementById('dock-status').textContent"
            " === 'in sync'")
        asked = self.page.evaluate("() => window.__asked")
        self.assertTrue(asked and "Rename parameter" in asked[0], asked)
        params = app_module.WORKSPACE.model["parameters"]
        self.assertEqual(params["urban_cap"]["parameters"], ["urban_baseline"])
        self.assertNoConsoleErrors()

    def test_dock_flags_a_reference_left_pointing_nowhere(self):
        self._wire_a_parameter()
        self._open_dock("related", node="Demand_Urban")
        self._edit_dock("delete doc.parameters.urban_base;")
        self.page.click("#dock-apply")
        self.page.wait_for_selector("#dock-refs:not(.hidden)")
        strip = self.page.inner_text("#dock-refs")
        self.assertIn("does not define", strip)
        self.assertIn("urban_base", strip)
        self.assertNoConsoleErrors()


if __name__ == "__main__":
    unittest.main(verbosity=2)
