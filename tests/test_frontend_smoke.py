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

# a 1×1 png and a one-page pdf, built in-process so the tests carry no binaries
_PNG_1x1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000d4944415478da63f8cfc0f01f0005000155a2b4e70000000049454e44ae426082")


def _one_page_pdf():
    """A minimal valid single-page PDF with a diagonal line, xref offsets
    computed exactly so pdf.js parses it without falling back to recovery."""
    objs = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 400 300]"
        b"/Contents 4 0 R/Resources<<>>>>",
        b"<</Length 34>>\nstream\n1 0 0 RG 5 w 40 40 m 360 260 l S\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n".encode() + b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (f"trailer<</Size {len(objs) + 1}/Root 1 0 R>>\n"
            f"startxref\n{xref}\n%%EOF").encode()
    return bytes(out)


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

    def test_find_a_node_by_name_and_jump_to_it(self):
        # type into the toolbar search → matches; pick one → it's selected and
        # centred (the panel opens on it). Pan away first to prove it re-centres.
        self.page.fill("#node-search-input", "demand")
        self.page.wait_for_selector("#node-search-results .sr-item")
        names = self.page.evaluate(
            "() => [...document.querySelectorAll('#node-search-results .sr-name')]"
            ".map(e => e.textContent)")
        self.assertIn("Demand_Urban", names)
        self.assertIn("Demand_Irrigation", names)
        self.page.evaluate("() => { S.view.x = -9000; S.view.y = -9000; }")
        # mousedown fires before the input blur that would hide the list
        self.page.eval_on_selector(
            "#node-search-results .sr-item:has-text('Demand_Urban')",
            "el => el.dispatchEvent(new MouseEvent('mousedown', {bubbles: true}))")
        self.assertEqual(self.page.evaluate("() => S.sel && S.sel.name"),
                         "Demand_Urban")
        # it was panned far off-screen; picking centres it on the canvas
        self.page.wait_for_function("""() => {
          const p = S.positions['Demand_Urban'];
          const c = document.getElementById('canvas');
          if (!c.clientWidth) return false;          // not laid out yet
          const sx = S.view.x + p[0] * S.view.k, sy = S.view.y + p[1] * S.view.k;
          return Math.abs(sx - c.clientWidth / 2) < 2 &&
                 Math.abs(sy - c.clientHeight / 2) < 2;
        }""")
        self.assertNoConsoleErrors()

    def test_the_slash_key_focuses_the_node_search(self):
        self.page.evaluate("() => document.body.focus()")
        self.page.keyboard.press("/")
        self.assertEqual(self.page.evaluate("() => document.activeElement.id"),
                         "node-search-input")
        self.assertNoConsoleErrors()

    def test_node_search_reports_when_nothing_matches(self):
        self.page.fill("#node-search-input", "zzz-no-such-node")
        self.page.wait_for_selector("#node-search-results .sr-empty")
        self.assertIn("No matching node",
                      self.page.inner_text("#node-search-results"))
        self.assertNoConsoleErrors()

    def test_the_empty_state_offers_the_example_and_opens_it(self):
        # with no model open, the demo is one click away — the packaged build
        # unpacks it somewhere nobody could browse to, so a button is the
        # only way anyone finds it
        app_module.WORKSPACE.reset()
        page = self.browser.new_page(viewport={"width": 1280, "height": 800})
        try:
            page.goto(self.base)
            page.wait_for_selector("#btn-example:not(.hidden)")
            self.assertTrue(page.locator("#empty-state").is_visible())
            page.click("#btn-example")
            page.wait_for_selector("#canvas .node")
            self.assertEqual(page.locator("#canvas .node").count(), 11)
            self.assertFalse(page.locator("#empty-state").is_visible())
        finally:
            page.close()

    def test_the_side_panel_collapses_and_comes_back(self):
        panel = self.page.locator("#sidebar")
        self.assertTrue(panel.is_visible())
        # collapse → panel gone, canvas wider, reopen handle shown
        before = self.page.evaluate(
            "() => document.getElementById('canvas').getBoundingClientRect().width")
        self.page.click("#btn-sidebar-collapse")
        self.page.wait_for_selector("#sidebar.collapsed", state="attached")
        self.assertFalse(panel.is_visible())
        self.assertTrue(self.page.locator("#sidebar-reopen").is_visible())
        after = self.page.evaluate(
            "() => document.getElementById('canvas').getBoundingClientRect().width")
        self.assertGreater(after, before, "canvas did not reclaim the width")
        # reopen → panel back, handle gone
        self.page.click("#sidebar-reopen")
        self.assertTrue(panel.is_visible())
        self.assertFalse(self.page.locator("#sidebar-reopen").is_visible())
        self.assertNoConsoleErrors()

    def test_the_collapse_toggle_is_not_treated_as_a_tab(self):
        # it lives in #tabs but has no data-tab; clicking it must not blank the
        # active tab (that was the trap in wiring every #tabs button as a tab)
        self.page.click("#btn-sidebar-collapse")
        self.page.click("#sidebar-reopen")
        active = self.page.evaluate(
            "() => [...document.querySelectorAll('.tab')]"
            ".filter(t => t.classList.contains('active')).length")
        self.assertEqual(active, 1, "exactly one tab should stay active")
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

    def test_the_current_path_is_not_shown_among_the_shortcuts(self):
        # it used to sit in the same row as the drive buttons, so on Windows
        # (where Home is C:\Users\<name>) the row read as a drive called
        # after the user. The path belongs on its own line.
        self._open_dialog()
        shortcut_row_text = self.page.evaluate("""() => {
          const btn = document.querySelector('#modal .row.gap button');
          return btn.parentElement.textContent;
        }""")
        home = self.page.evaluate(
            "() => document.querySelector('#modal .mono.muted.small').textContent")
        self.assertNotIn(home, shortcut_row_text,
                         "the current path is rendered beside the shortcuts")
        # and it is still on screen, labelled, just elsewhere (the label is
        # uppercased by CSS, so compare case-insensitively)
        here = self.page.inner_text(".browse-here")
        self.assertIn("in", here.lower())
        self.assertIn(home, here)
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

    # -- tracing over an image / PDF ------------------------------------
    def _load_trace_file(self, name, data):
        import tempfile
        path = os.path.join(tempfile.mkdtemp(), name)
        with open(path, "wb") as fh:
            fh.write(data)
        # the input is hidden; set_input_files drives it directly and fires
        # the change handler the button would
        self.page.set_input_files("#trace-file", path)
        self.page.wait_for_function("() => !!window.S.bg")

    def test_the_trace_image_lets_clicks_through_only_while_tracing(self):
        # the bug: an unlocked image swallowed the click, so "add a node over
        # the image" moved the image (or, once locked, did nothing) instead of
        # placing a node. It must be draggable while positioning (select mode)
        # and click-through while tracing (+Node / +Edge).
        self._load_trace_file("map.png", _PNG_1x1)
        pe = lambda: self.page.evaluate(          # noqa: E731
            "() => getComputedStyle(document.querySelector('#g-bg image'))"
            ".pointerEvents")
        # the mode buttons live in a closed menu, so drive them by id
        mode = lambda m: self.page.evaluate(      # noqa: E731
            "m => document.getElementById('btn-mode-' + m).click()", arg=m)
        mode("select")
        self.assertEqual(pe(), "auto", "not draggable while positioning")
        mode("addnode")
        self.assertEqual(pe(), "none", "swallows the click while tracing")
        mode("select")
        self.assertEqual(pe(), "auto", "not draggable again after tracing")
        self.assertNoConsoleErrors()

    def test_a_click_over_the_image_places_a_node(self):
        # with the image loaded, a click in add-node mode reaches the canvas and
        # places a node. (That the image doesn't intercept the click is pinned
        # by the pointer-events test above; here we confirm the canvas handler
        # still fires and places.)
        self._load_trace_file("map.png", _PNG_1x1)
        self.page.evaluate("() => { S.quickPlace = true; }")
        self.page.evaluate(
            "() => document.getElementById('btn-mode-addnode').click()")
        before = self.page.evaluate("() => S.graph.nodes.length")
        self.page.evaluate("""() => {
          const c = document.getElementById('canvas');
          const r = c.getBoundingClientRect();
          c.dispatchEvent(new MouseEvent('mousedown', {
            bubbles: true, cancelable: true, button: 0,
            clientX: r.left + r.width / 2, clientY: r.top + r.height / 2}));
        }""")
        self.page.wait_for_function("n => S.graph.nodes.length > n", arg=before)
        self.assertNoConsoleErrors()

    def test_a_pdf_becomes_a_raster_trace_background(self):
        # a PDF can't load into an <img>; its first page is rasterised to a png
        # by the vendored pdf.js so the rest of the trace machinery is unchanged
        self._load_trace_file("schematic.pdf", _one_page_pdf())
        bg = self.page.evaluate(
            "() => ({w: S.bg.natW, h: S.bg.natH, png: "
            "S.bg.src.startsWith('data:image/png')})")
        self.assertTrue(bg["png"], "PDF page was not rasterised to a png")
        self.assertGreater(bg["w"], 0)
        self.assertGreater(bg["h"], 0)
        # 400×300 page → aspect preserved
        self.assertAlmostEqual(bg["w"] / bg["h"], 400 / 300, places=1)
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


    # -- adding recorders and parameters without writing JSON -----------
    def _add_recorder_buttons(self, node):
        self.page.evaluate("n => selectNode(n)", arg=node)
        self.page.wait_for_selector(".add-recorders")
        return self.page.evaluate(
            "() => [...document.querySelectorAll('.add-recorders button')]"
            ".map(b => b.textContent.replace('+ ', ''))")

    def test_the_recorders_offered_suit_the_node(self):
        # a demand node can run a deficit; a storage node has a volume
        demand = self._add_recorder_buttons("Demand_Urban")
        self.assertIn("deficit (time series)", demand)
        self.assertNotIn("volume (time series)", demand)
        storage = self._add_recorder_buttons("Reservoir_A")
        self.assertIn("volume (time series)", storage)
        self.assertNotIn("deficit (time series)", storage)
        self.assertNoConsoleErrors()

    def test_one_click_adds_a_named_recorder_on_the_node(self):
        self.page.evaluate("selectNode('River_Outlet')")
        self.page.wait_for_selector(".add-recorders")
        self.page.evaluate("""() => [...document.querySelectorAll(
          '.add-recorders button')].find(
            b => b.textContent === '+ flow (time series)').click()""")
        self.page.wait_for_function(
            "() => 'River_Outlet_flow' in "
            "  (window.S.nodeIdx.get('River_Outlet').recorders || [])"
            "    .reduce((a, r) => (a[r.name] = 1, a), {})")
        self.assertEqual(
            app_module.WORKSPACE.model["recorders"]["River_Outlet_flow"],
            {"type": "NumpyArrayNodeRecorder", "node": "River_Outlet"})
        self.assertNoConsoleErrors()

    def test_the_standard_set_lands_as_one_edit(self):
        self.page.evaluate("selectNode('Demand_Irrigation')")
        self.page.wait_for_selector(".add-recorders")
        # Irrigation_supply already records its flow, so the set offered is
        # what is missing rather than the whole list
        self.page.evaluate("""() => [...document.querySelectorAll(
          '#tab-node button')].find(
            b => b.textContent.startsWith('+ record the usual')).click()""")
        self.page.wait_for_function(
            "() => !document.getElementById('toast').classList.contains('hidden')")
        recorders = app_module.WORKSPACE.model["recorders"]
        for suffix, kind in (("_total_flow", "TotalFlowNodeRecorder"),
                             ("_deficit", "NumpyArrayNodeDeficitRecorder"),
                             ("_total_deficit", "TotalDeficitNodeRecorder")):
            name = "Demand_Irrigation" + suffix
            self.assertIn(name, recorders)
            self.assertEqual(recorders[name]["type"], kind)
        self.assertNoConsoleErrors()

    def test_removing_a_recorder_from_the_node_panel(self):
        self.page.evaluate("selectNode('Demand_Urban')")
        self.page.wait_for_selector(".rec-row")
        self.page.evaluate(
            "() => document.querySelector('.rec-row button').click()")
        self.page.wait_for_function(
            "() => !('Urban_supply' in "
            "  (window.S.nodeIdx.get('Demand_Urban').recorders || [])"
            "    .reduce((a, r) => (a[r.name] = 1, a), {}))")
        self.assertNotIn("Urban_supply", app_module.WORKSPACE.model["recorders"])
        self.assertNoConsoleErrors()

    def test_explorer_adds_a_parameter_from_a_form(self):
        self.page.evaluate("openModelExplorer()")
        self.page.wait_for_selector(".explorer-body")
        self.page.click(".explorer-nav button[data-sec='Parameters']")
        self.page.click(".explorer-bar button.primary")
        self.page.wait_for_selector("#modal select")
        self.page.fill("#modal input[type=text]", "urban_cap")
        self.page.evaluate("""() => {
          const labelled = label => [...document.querySelectorAll(
            '#modal label.stack')].find(
              l => l.childNodes[0].textContent === label).querySelector('input');
          labelled('Value').value = '12.5';
        }""")
        self.page.click("#modal button.primary")
        self.page.wait_for_selector(".explorer-body details")
        self.assertEqual(app_module.WORKSPACE.model["parameters"]["urban_cap"],
                         {"type": "constant", "value": 12.5})
        self.assertNoConsoleErrors()

    def test_explorer_add_form_offers_the_models_own_names(self):
        self.page.evaluate("openModelExplorer()")
        self.page.wait_for_selector(".explorer-body")
        self.page.click(".explorer-nav button[data-sec='Recorders']")
        self.page.click(".explorer-bar button.primary")
        self.page.wait_for_selector("#modal select")
        options = self.page.evaluate("""() => {
          const input = [...document.querySelectorAll('#modal label.stack')]
            .find(l => l.childNodes[0].textContent === 'Node')
            .querySelector('input');
          return [...document.getElementById(
            input.getAttribute('list')).options].map(o => o.value);
        }""")
        # a reference is picked from what the model actually defines
        self.assertIn("Demand_Urban", options)
        self.assertIn("Reservoir_A", options)
        self.assertNoConsoleErrors()


    # -- parameter-chain templates --------------------------------------
    def _open_bundle(self, node, label):
        self.page.evaluate("n => selectNode(n)", arg=node)
        self.page.wait_for_selector(".add-recorders")
        self.page.evaluate("""label => [...document.querySelectorAll(
          '#tab-node .add-recorders button')].find(
            b => b.textContent === '+ ' + label).click()""", arg=label)
        self.page.wait_for_selector(".bundle-preview")

    def _bundle_field(self, label, value):
        self.page.evaluate("""({label, value}) => {
          const input = [...document.querySelectorAll('#modal label.stack')]
            .find(l => l.childNodes[0].textContent === label)
            .querySelector('input');
          input.value = value;
          input.dispatchEvent(new Event('input', {bubbles: true}));
        }""", arg={"label": label, "value": value})

    def _panel_buttons(self, node):
        self.page.evaluate("n => selectNode(n)", arg=node)
        self.page.wait_for_selector("#tab-node")
        return self.page.evaluate(
            "() => [...document.querySelectorAll('#tab-node .add-recorders "
            "button')].map(b => b.textContent.replace('+ ', ''))")

    def test_the_templates_offered_suit_the_node(self):
        demand = self._panel_buttons("Demand_Urban")
        self.assertIn("seasonal demand cap", demand)
        self.assertIn("deficit alarm", demand)
        self.assertNotIn("annual licence volume", demand)
        storage = self._panel_buttons("Reservoir_A")
        self.assertIn("annual licence volume", storage)
        self.assertNotIn("seasonal demand cap", storage)
        source = self._panel_buttons("Rainfall_Catchment")
        self.assertIn("base + top-up abstraction", source)
        # a plain link needs no set-up, so it is offered none
        self.assertEqual([b for b in self._panel_buttons("River_Main")
                          if "abstraction" in b or "cap" in b], [])
        self.assertNoConsoleErrors()

    def test_a_template_previews_before_it_lands(self):
        self._open_bundle("Demand_Urban", "seasonal demand cap")
        self._bundle_field("Row for this node", "Urban")
        preview = self.page.evaluate(
            "() => JSON.parse(document.querySelector('.bundle-preview')"
            ".textContent)")
        self.assertEqual(sorted(preview), ["Demand_Urban_max_flow",
                                           "Demand_Urban_max_flow_base",
                                           "Demand_Urban_max_flow_factor"])
        # the chain is wired: the product names the two it multiplies
        self.assertEqual(preview["Demand_Urban_max_flow"]["parameters"],
                         ["Demand_Urban_max_flow_base",
                          "Demand_Urban_max_flow_factor"])
        self.assertIn("max_flow on Demand_Urban will point at it",
                      self.page.inner_text(".bundle-details"))
        # nothing has happened yet — a preview is only a preview
        self.assertNotIn("Demand_Urban_max_flow",
                         app_module.WORKSPACE.model.get("parameters", {}))
        self.assertNoConsoleErrors()

    def test_a_template_lands_as_one_edit_and_wires_the_node(self):
        self._open_bundle("Demand_Urban", "deficit alarm")
        self.page.click("#modal button.primary")
        self.page.wait_for_selector("#modal-backdrop", state="hidden")
        model = app_module.WORKSPACE.model
        self.assertIn("Demand_Urban_deficit", model["recorders"])
        threshold = model["parameters"]["EDO_threshold_param_Demand_Urban"]
        self.assertEqual(threshold["recorder"], "Demand_Urban_deficit")
        events = model["recorders"]["EDO_events_Demand_Urban"]
        self.assertEqual(events["threshold"], "EDO_threshold_param_Demand_Urban")
        self.assertEqual(events["minimum_event_length"], 4)
        # everything it referred to, it also created
        self.assertEqual(self.page.evaluate(
            "() => (window.S.graph.reference_warnings || []).length"), 0)
        self.assertNoConsoleErrors()

    def test_a_template_leaves_what_is_already_there_alone(self):
        # Urban_supply already records this node's flow; run the template that
        # would add a deficit recorder twice and the second is a no-op
        self._open_bundle("Demand_Urban", "deficit alarm")
        self.page.click("#modal button.primary")
        self.page.wait_for_selector("#modal-backdrop", state="hidden")
        self._open_bundle("Demand_Urban", "deficit alarm")
        self.assertIn("3 already there and left alone",
                      self.page.inner_text(".bundle-details"))
        self.assertNoConsoleErrors()

    def test_a_template_says_when_it_would_not_be_connected(self):
        # give the node a max_flow parameter of the name the template ends at
        self.page.evaluate("""async () => {
          await fetch('/api/definition/add', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({section: 'parameters',
              name: 'Demand_Urban_max_flow',
              definition: {type: 'constant', value: 1}}),
          }).then(r => r.json()).then(updateGraph);
        }""")
        self._open_bundle("Demand_Urban", "seasonal demand cap")
        self._bundle_field("Row for this node", "Urban")
        self.page.wait_for_selector(".bundle-warn:not(.hidden)")
        warning = self.page.inner_text(".bundle-warn")
        self.assertIn("Demand_Urban_max_flow", warning)
        self.assertIn("would not be connected", warning)
        self.assertNoConsoleErrors()


if __name__ == "__main__":
    unittest.main(verbosity=2)
