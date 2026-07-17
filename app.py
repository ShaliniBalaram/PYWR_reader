"""PyWR Reader — local web app.

Run:  python app.py   →  http://127.0.0.1:5321
"""

import base64
import binascii
import json
import os
import subprocess
import tempfile
import threading
import time
import uuid

from flask import Flask, jsonify, request, send_from_directory

from pywr_reader import (dataresolve, envsetup, graphops, layout as layout_mod,
                         model_io)

APP_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, static_folder=os.path.join(APP_DIR, "static"),
            static_url_path="/static")
app.json.sort_keys = False  # keep pywr model key order in API responses

# ---------------------------------------------------------------------------
# In-memory session state (this is a single-user local tool)
# ---------------------------------------------------------------------------
STATE = {
    "model": None,        # full pywr model dict
    "positions": {},      # {name: [x, y]}
    "path": None,         # file the model came from
    "dirty": False,
    "layout_was_auto": False,
    "warnings": [],
    "data_dirs": [],      # extra folders to search for data files
    "data": None,         # dataresolve.resolve(...) result
}
RUNS = {}                 # run_id -> run dict
RUN_ORDER = []
LOCK = threading.RLock()


def _resolve_data():
    """(Re)locate the model's external data files; store the report."""
    if STATE["model"] is None:
        STATE["data"] = None
        return
    STATE["data"] = dataresolve.resolve(
        STATE["model"], STATE["path"], STATE["data_dirs"])


def _data_payload():
    d = STATE["data"] or {}
    return {"report": d.get("report", []), "missing": d.get("missing", []),
            "dirs": STATE["data_dirs"]}


def _err(msg, code=400):
    return jsonify({"ok": False, "error": str(msg)}), code


def _graph_payload():
    summary = graphops.graph_summary(STATE["model"], STATE["positions"])
    summary.update({
        "ok": True,
        "path": STATE["path"],
        "dirty": STATE["dirty"],
        "layout_was_auto": STATE["layout_was_auto"],
        "warnings": STATE["warnings"],
        "data": _data_payload(),
    })
    return summary


def _require_model():
    if STATE["model"] is None:
        raise ValueError("no model is open")


def _normalize_positions(positions):
    """Rescale positions so the median nearest-neighbour distance sits around
    the app's node spacing. Model files store positions in arbitrary units
    (grid cells, screen px, metres); without this, dense layouts render as
    overlapping blobs and sparse ones as specks."""
    pts = list(positions.values())
    if len(pts) < 2:
        return positions
    import math
    sample = pts if len(pts) <= 400 else pts[::max(1, len(pts) // 400)]
    nearest = []
    for i, p in enumerate(sample):
        best = None
        for j, q in enumerate(sample):
            if i == j:
                continue
            d = math.hypot(p[0] - q[0], p[1] - q[1])
            if d > 0 and (best is None or d < best):
                best = d
        if best is not None:
            nearest.append(best)
    if not nearest:
        return positions
    nearest.sort()
    median = nearest[len(nearest) // 2]
    if 60.0 <= median <= 400.0:
        return positions
    scale = 120.0 / median
    cx = sum(p[0] for p in pts) / len(pts)
    cy = sum(p[1] for p in pts) / len(pts)
    return {name: [(xy[0] - cx) * scale, (xy[1] - cy) * scale]
            for name, xy in positions.items()}


# ---------------------------------------------------------------------------
# Static frontend
# ---------------------------------------------------------------------------
@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


# ---------------------------------------------------------------------------
# File browsing / open / save
# ---------------------------------------------------------------------------
@app.get("/api/browse")
def browse():
    path = request.args.get("path") or os.path.expanduser("~")
    path = os.path.abspath(path)
    if not os.path.isdir(path):
        return _err(f"not a directory: {path}")
    entries = []
    try:
        for name in sorted(os.listdir(path), key=str.lower):
            if name.startswith((".", "._")):
                continue
            full = os.path.join(path, name)
            if os.path.isdir(full):
                entries.append({"name": name, "kind": "dir"})
            elif os.path.splitext(name)[1].lower() in (".json", ".tcm", ".csv"):
                try:
                    size = os.path.getsize(full)
                except OSError:
                    size = 0
                entries.append({"name": name, "kind": "file", "size": size})
    except PermissionError:
        return _err("permission denied", 403)
    roots = [os.path.expanduser("~"), "/Volumes"]
    return jsonify({"ok": True, "path": path,
                    "parent": os.path.dirname(path), "entries": entries,
                    "roots": [r for r in roots if os.path.isdir(r)]})


@app.post("/api/open")
def open_model():
    body = request.get_json(force=True)
    path = (body.get("path") or "").strip()
    if not os.path.isfile(path):
        return _err(f"file not found: {path}")

    # A .tcm opened while a model is already loaded applies its positions to
    # that model (the natural "open model, then open its view file" flow).
    if path.lower().endswith(".tcm") and STATE["model"] is not None:
        try:
            tcm_positions, _, _ = model_io.load_tcm(path)
        except Exception as exc:  # noqa: BLE001
            return _err(exc)
        with LOCK:
            names = {n["name"] for n in STATE["model"].get("nodes", [])}
            matched = 0
            for name, xy in tcm_positions.items():
                if name in names:
                    STATE["positions"][name] = xy
                    matched += 1
            if matched:
                all_names = [n["name"] for n in STATE["model"].get("nodes", [])]
                STATE["positions"] = _normalize_positions(
                    layout_mod.layout_missing(
                        all_names, STATE["model"].get("edges", []),
                        STATE["positions"]))
                STATE["dirty"] = True
                STATE["layout_was_auto"] = False
            STATE["warnings"] = ([f".tcm positions applied to {matched} of "
                                  f"{len(names)} nodes"] if matched else
                                 [".tcm node names did not match the open model"])
        return jsonify(_graph_payload())

    try:
        loaded = model_io.load_any(path)
    except Exception as exc:  # noqa: BLE001 — surface parse errors to the UI
        return _err(exc)

    with LOCK:
        model, positions = loaded["model"], loaded["positions"]
        names = [n["name"] for n in model.get("nodes", [])]
        auto = False
        if model_io.positions_are_degenerate(positions, len(names)):
            positions = layout_mod.auto_layout(
                names, model.get("edges", []),
                affinity=graphops.node_affinity(model))
            auto = True
        elif len(positions) < len(names):
            positions = layout_mod.layout_missing(
                names, model.get("edges", []), positions)
        if not auto:
            positions = _normalize_positions(positions)
        STATE.update(model=model, positions=positions, path=loaded["path"],
                     dirty=False, layout_was_auto=auto,
                     warnings=loaded["warnings"], data_dirs=[], data=None)
        _resolve_data()
    return jsonify(_graph_payload())


@app.post("/api/new")
def new_model():
    """Start an empty model — the blank canvas for tracing a network over an
    image, or building one from scratch."""
    body = request.get_json(force=True, silent=True) or {}
    title = (body.get("title") or "Untitled model").strip() or "Untitled model"
    with LOCK:
        STATE.update(
            model={
                "metadata": {"title": title, "minimum_version": "1.20.0"},
                "timestepper": {"start": "2000-01-01", "end": "2000-12-31",
                                "timestep": 1},
                "nodes": [], "edges": [], "parameters": {}, "recorders": {},
            },
            positions={}, path=None, dirty=True, layout_was_auto=False,
            warnings=[], data_dirs=[], data=None)
        _resolve_data()
    return jsonify(_graph_payload())


@app.get("/api/graph")
def get_graph():
    try:
        _require_model()
    except ValueError as exc:
        return _err(exc)
    return jsonify(_graph_payload())


@app.get("/api/model/raw")
def raw_model():
    try:
        _require_model()
    except ValueError as exc:
        return _err(exc)
    model = json.loads(json.dumps(STATE["model"]))
    model_io.inject_positions(model, STATE["positions"])
    return jsonify(model)


def _validate_model(model):
    """Cheap structural checks on hand-edited JSON, so a typo comes back as a
    clear message instead of a broken canvas or a crash deep inside pywr.
    Returns an error string, or None when the model looks sane."""
    if not isinstance(model, dict):
        return "the model must be a JSON object"
    if not isinstance(model.get("nodes"), list):
        return "no 'nodes' list — this is not a pywr model"
    if not isinstance(model.get("edges", []), list):
        return "'edges' must be a list"
    names = []
    for i, node in enumerate(model["nodes"]):
        if not isinstance(node, dict):
            return f"nodes[{i}] must be an object"
        if not node.get("name"):
            return f"nodes[{i}] has no 'name'"
        names.append(node["name"])
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        return f"duplicate node name(s): {', '.join(dupes)}"
    known = set(names)
    for i, edge in enumerate(model.get("edges", [])):
        if not isinstance(edge, list) or len(edge) < 2:
            return f"edges[{i}] must be [source, destination, …]"
        for end in edge[:2]:
            if end not in known:
                return f"edges[{i}] references unknown node {end!r}"
    for section in ("parameters", "tables", "recorders"):
        if section in model and not isinstance(model[section], dict):
            return f"'{section}' must be a JSON object"
    return None


@app.post("/api/model/raw")
def replace_raw_model():
    """Replace the model with hand-edited JSON (the JSON editor's Apply).
    The file on disk is untouched until Save.

    Optional "renames" {old: new} — when the editor sees a node's name change
    it says so, and the references are rewritten to match before validating
    (otherwise every edge to that node would look like a dangling one)."""
    body = request.get_json(force=True, silent=True)
    if not isinstance(body, dict) or "model" not in body:
        return _err("expected {\"model\": {…}}")
    model = body["model"]
    renames = body.get("renames") or {}
    if not isinstance(renames, dict):
        return _err("'renames' must be an object of {old: new}")
    notes = []
    for old, new in renames.items():
        if not isinstance(old, str) or not isinstance(new, str) or not new:
            return _err("'renames' must map a name to a non-empty name")
        if old != new:
            notes.extend(graphops.rewrite_node_refs(model, old, new))
    problem = _validate_model(model)
    if problem:
        return _err(problem)
    with LOCK:
        names = [n["name"] for n in model["nodes"]]
        # positions come from the edited JSON where it has them, otherwise
        # keep what's on screen so an unrelated edit doesn't scramble the layout
        positions = dict(STATE["positions"])
        for old, new in renames.items():
            if old != new and old in positions:
                positions[new] = positions.pop(old)   # a rename stays put
        positions.update(model_io.extract_positions(model))
        positions = {n: xy for n, xy in positions.items() if n in set(names)}
        if len(positions) < len(names):
            positions = layout_mod.layout_missing(
                names, model.get("edges", []), positions)
        STATE.update(model=model, positions=_normalize_positions(positions),
                     dirty=True, warnings=notes)
        _resolve_data()
    return jsonify(_graph_payload())


@app.post("/api/save")
def save_model():
    body = request.get_json(force=True)
    try:
        _require_model()
        path = (body.get("path") or STATE["path"] or "").strip()
        if not path:
            return _err("no target path")
        if not path.lower().endswith(".json"):
            path += ".json"
        with LOCK:
            model_io.save_pywr_json(STATE["model"], STATE["positions"], path)
            STATE["path"], STATE["dirty"] = path, False
        return jsonify({"ok": True, "path": path})
    except (ValueError, OSError) as exc:
        return _err(exc)


@app.post("/api/export_csv")
def export_csv():
    body = request.get_json(force=True)
    try:
        _require_model()
        directory = body.get("directory") or os.path.dirname(STATE["path"] or APP_DIR)
        with LOCK:
            paths = model_io.export_csv_pair(STATE["model"], STATE["positions"],
                                             directory)
        return jsonify({"ok": True, "files": paths})
    except (ValueError, OSError) as exc:
        return _err(exc)


# ---------------------------------------------------------------------------
# Layout / positions
# ---------------------------------------------------------------------------
@app.get("/api/layouts")
def list_layouts():
    """The layouts the picker can offer (label + hint come from layout.py)."""
    return jsonify({"ok": True, "layouts": layout_mod.LAYOUTS})


@app.post("/api/layout")
def relayout():
    body = request.get_json(force=True)
    mode = body.get("mode", "all")
    kind = body.get("kind") or "layered"
    try:
        _require_model()
    except ValueError as exc:
        return _err(exc)
    if mode != "missing" and kind not in layout_mod.LAYOUT_KINDS:
        return _err(f"unknown layout {kind!r}")
    with LOCK:
        model = STATE["model"]
        names = [n["name"] for n in model.get("nodes", [])]
        if mode == "missing":
            STATE["positions"] = layout_mod.layout_missing(
                names, model.get("edges", []), STATE["positions"])
        else:
            groups = {n["name"]: layout_mod.node_group(n.get("type", ""))
                      for n in model.get("nodes", [])}
            STATE["positions"] = _normalize_positions(layout_mod.compute(
                kind, names, model.get("edges", []),
                affinity=graphops.node_affinity(model), groups=groups))
        STATE["dirty"] = True
        STATE["layout_was_auto"] = True
    return jsonify(_graph_payload())


@app.post("/api/positions")
def set_positions():
    body = request.get_json(force=True)
    try:
        _require_model()
    except ValueError as exc:
        return _err(exc)
    with LOCK:
        for name, xy in (body.get("positions") or {}).items():
            if isinstance(xy, (list, tuple)) and len(xy) >= 2:
                STATE["positions"][name] = [float(xy[0]), float(xy[1])]
        STATE["dirty"] = True
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Editing
# ---------------------------------------------------------------------------
@app.post("/api/node/add")
def node_add():
    body = request.get_json(force=True)
    try:
        _require_model()
        with LOCK:
            node = graphops.add_node(STATE["model"], body.get("node") or {})
            pos = body.get("pos")
            if isinstance(pos, (list, tuple)) and len(pos) >= 2:
                STATE["positions"][node["name"]] = [float(pos[0]), float(pos[1])]
            STATE["dirty"] = True
    except ValueError as exc:
        return _err(exc)
    return jsonify(_graph_payload())


@app.post("/api/node/update")
def node_update():
    body = request.get_json(force=True)
    try:
        _require_model()
        with LOCK:
            graphops.update_node(STATE["model"], body.get("name"),
                                 body.get("changes"), body.get("removals"))
            new_type = (body.get("changes") or {}).get("type")
            if new_type:
                graphops.node_by_name(STATE["model"], body["name"])["type"] = new_type
            STATE["dirty"] = True
    except ValueError as exc:
        return _err(exc)
    return jsonify(_graph_payload())


@app.post("/api/node/rename")
def node_rename():
    body = request.get_json(force=True)
    try:
        _require_model()
        with LOCK:
            notes = graphops.rename_node(STATE["model"], body.get("old"),
                                         body.get("new"))
            if body.get("old") in STATE["positions"]:
                STATE["positions"][body["new"]] = STATE["positions"].pop(body["old"])
            STATE["dirty"] = True
    except ValueError as exc:
        return _err(exc)
    payload = _graph_payload()
    payload["notes"] = notes
    return jsonify(payload)


@app.post("/api/node/delete")
def node_delete():
    body = request.get_json(force=True)
    try:
        _require_model()
        with LOCK:
            warnings = graphops.delete_node(STATE["model"], body.get("name"))
            STATE["positions"].pop(body.get("name"), None)
            STATE["dirty"] = True
    except ValueError as exc:
        return _err(exc)
    payload = _graph_payload()
    payload["delete_warnings"] = warnings
    return jsonify(payload)


@app.post("/api/edge/add")
def edge_add():
    body = request.get_json(force=True)
    try:
        _require_model()
        with LOCK:
            graphops.add_edge(STATE["model"], body.get("src"), body.get("dst"))
            STATE["dirty"] = True
    except ValueError as exc:
        return _err(exc)
    return jsonify(_graph_payload())


@app.post("/api/edge/delete")
def edge_delete():
    body = request.get_json(force=True)
    try:
        _require_model()
        with LOCK:
            graphops.delete_edge(STATE["model"], body.get("src"), body.get("dst"))
            STATE["dirty"] = True
    except ValueError as exc:
        return _err(exc)
    return jsonify(_graph_payload())


@app.get("/api/trace")
def trace():
    name = request.args.get("name")
    direction = request.args.get("dir", "downstream")
    try:
        _require_model()
        nodes, edges = graphops.trace(STATE["model"], name, direction)
    except ValueError as exc:
        return _err(exc)
    return jsonify({"ok": True, "nodes": sorted(nodes),
                    "edges": sorted(edges)})


# ---------------------------------------------------------------------------
# PyWR environment
# ---------------------------------------------------------------------------
@app.get("/api/env")
def env_status():
    info = envsetup.check_env()
    info["ok"] = True
    info["log"] = envsetup.read_log(60) if info["setting_up"] or not info["ready"] else []
    return jsonify(info)


@app.post("/api/env/setup")
def env_setup():
    started = envsetup.start_setup()
    return jsonify({"ok": True, "started": started})


# ---------------------------------------------------------------------------
# External data files
# ---------------------------------------------------------------------------
@app.get("/api/data")
def data_status():
    try:
        _require_model()
    except ValueError as exc:
        return _err(exc)
    return jsonify({"ok": True, **_data_payload()})


@app.post("/api/data/dirs")
def data_add_dir():
    body = request.get_json(force=True)
    directory = (body.get("directory") or "").strip()
    remove = body.get("remove")
    try:
        _require_model()
    except ValueError as exc:
        return _err(exc)
    with LOCK:
        if remove:
            STATE["data_dirs"] = [d for d in STATE["data_dirs"] if d != remove]
        elif directory:
            if not os.path.isdir(directory):
                return _err(f"not a directory: {directory}")
            if directory not in STATE["data_dirs"]:
                STATE["data_dirs"].append(directory)
        _resolve_data()
    return jsonify({"ok": True, **_data_payload()})


# ---------------------------------------------------------------------------
# Trace image sidecar — a real image file + tiny geometry JSON next to the
# model. The pywr JSON itself is never touched.
#   <model>.pywrtrace.png   the traced map/schematic (actual image bytes)
#   <model>.pywrtrace.json  {image, x, y, scale, opacity, natW, natH, locked}
# ---------------------------------------------------------------------------
_MIME_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/jpg": "jpg",
             "image/gif": "gif", "image/webp": "webp", "image/bmp": "bmp",
             "image/svg+xml": "svg"}
_EXT_MIME = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
             "gif": "image/gif", "webp": "image/webp", "bmp": "image/bmp",
             "svg": "image/svg+xml"}


def _trace_geom_path():
    p = STATE["path"]
    return os.path.splitext(p)[0] + ".pywrtrace.json" if p else None


def _trace_image_glob():
    """All possible sidecar image paths (any extension) for this model."""
    stem = os.path.splitext(STATE["path"])[0] if STATE["path"] else None
    return [f"{stem}.pywrtrace.{ext}" for ext in _EXT_MIME] if stem else []


def _decode_data_url(src):
    """('image/png', b'...') from a data URL, or (None, None)."""
    if not isinstance(src, str) or not src.startswith("data:"):
        return None, None
    try:
        header, b64 = src.split(",", 1)
        mime = header[5:].split(";")[0].lower()
        return mime, base64.b64decode(b64)
    except (ValueError, binascii.Error):
        return None, None


@app.get("/api/traceimage")
def trace_get():
    try:
        _require_model()
    except ValueError as exc:
        return _err(exc)
    gp = _trace_geom_path()
    if gp and os.path.isfile(gp):
        try:
            with open(gp, encoding="utf-8") as fh:
                geom = json.load(fh)
            # reconstruct a data URL from the real image file for the browser
            img_path = os.path.join(os.path.dirname(gp), geom.get("image", ""))
            if geom.get("image") and os.path.isfile(img_path):
                ext = os.path.splitext(img_path)[1].lstrip(".").lower()
                with open(img_path, "rb") as fh:
                    b64 = base64.b64encode(fh.read()).decode("ascii")
                geom["src"] = f"data:{_EXT_MIME.get(ext, 'image/png')};base64,{b64}"
                return jsonify({"ok": True, "trace": geom,
                                "path": gp, "image": img_path})
        except (OSError, ValueError) as exc:
            return _err(f"could not read trace file: {exc}")
    return jsonify({"ok": True, "trace": None, "path": gp})


@app.post("/api/traceimage")
def trace_save():
    body = request.get_json(force=True)
    try:
        _require_model()
    except ValueError as exc:
        return _err(exc)
    gp = _trace_geom_path()
    if not gp:
        return _err("save the model first — the trace file is stored beside it",
                    409)
    trace = body.get("trace")
    try:
        if trace is None:                       # remove image + geometry
            for path in [gp] + _trace_image_glob():
                if os.path.isfile(path):
                    os.remove(path)
            return jsonify({"ok": True, "path": None})

        geom = {k: v for k, v in trace.items() if k != "src"}
        mime, data = _decode_data_url(trace.get("src"))
        if data is not None:                    # new/updated image → write it
            ext = _MIME_EXT.get(mime, "png")
            img_path = os.path.splitext(STATE["path"])[0] + f".pywrtrace.{ext}"
            for stale in _trace_image_glob():   # drop an old image of another type
                if stale != img_path and os.path.isfile(stale):
                    os.remove(stale)
            with open(img_path, "wb") as fh:
                fh.write(data)
            geom["image"] = os.path.basename(img_path)
        elif os.path.isfile(gp):                # geometry-only update
            with open(gp, encoding="utf-8") as fh:
                geom["image"] = json.load(fh).get("image")
        else:
            return _err("no image data provided for the trace")

        with open(gp, "w", encoding="utf-8") as fh:
            json.dump(geom, fh, indent=1)
        img = os.path.join(os.path.dirname(gp), geom.get("image") or "")
        return jsonify({"ok": True, "path": gp,
                        "image": img if geom.get("image") else None})
    except OSError as exc:
        return _err(exc)


# ---------------------------------------------------------------------------
# Running models
# ---------------------------------------------------------------------------
def _estimate_edge_flows(model, node_series, exact_edges=None):
    """Per-edge flow series. Exact when the runner recorded the edge directly
    (split/junction edges spliced with a proxy link) or when one endpoint
    funnels all its flow through the edge; otherwise an elementwise-min
    estimate. exact_edges maps an edge's index in model['edges'] to its
    recorded series."""
    exact_edges = exact_edges or {}
    out_deg, in_deg = {}, {}
    for edge in model.get("edges", []):
        out_deg[edge[0]] = out_deg.get(edge[0], 0) + 1
        in_deg[edge[1]] = in_deg.get(edge[1], 0) + 1

    edges_out = []
    for i, edge in enumerate(model.get("edges", [])):
        src, dst = edge[0], edge[1]
        flow_u = (node_series.get(src) or {}).get("flow")
        flow_v = (node_series.get(dst) or {}).get("flow")
        series, exact = None, False
        recorded = exact_edges.get(str(i))
        if recorded is not None:
            series, exact = recorded, True
        elif out_deg.get(src) == 1 and flow_u is not None:
            series, exact = flow_u, True
        elif in_deg.get(dst) == 1 and flow_v is not None:
            series, exact = flow_v, True
        elif flow_u is not None and flow_v is not None:
            series = [min(a, b) for a, b in zip(flow_u, flow_v)]
        elif flow_u is not None or flow_v is not None:
            series = flow_u if flow_u is not None else flow_v
        edges_out.append({"src": src, "dst": dst, "series": series,
                          "exact": exact})
    return edges_out


def _run_worker(run_id, model_snapshot, model_path, overrides):
    run = RUNS[run_id]
    python = envsetup.env_python()
    workdir = os.path.dirname(model_path) if model_path else tempfile.gettempdir()
    tmp_model = os.path.join(workdir, f".pywr_reader_run_{run_id}.json")
    tmp_out = os.path.join(tempfile.gettempdir(), f"pywr_reader_{run_id}.json")
    tmp_over = None
    try:
        with open(tmp_model, "w", encoding="utf-8") as fh:
            json.dump(model_snapshot, fh)
        cmd = [python, os.path.join(APP_DIR, "pywr_reader", "runner.py"),
               tmp_model, tmp_out]
        if overrides:
            tmp_over = os.path.join(tempfile.gettempdir(),
                                    f"pywr_reader_over_{run_id}.json")
            with open(tmp_over, "w", encoding="utf-8") as fh:
                json.dump(overrides, fh)
            cmd.append(tmp_over)
        run["status"] = "running"
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=3600)
        result = None
        if os.path.isfile(tmp_out):
            with open(tmp_out, encoding="utf-8") as fh:
                result = json.load(fh)
        if result and result.get("ok"):
            run["dates"] = result["dates"]
            run["nodes"] = result["nodes"]
            run["edges"] = _estimate_edge_flows(model_snapshot, result["nodes"],
                                                result.get("exact_edges"))
            run["meta"] = {k: result.get(k) for k in
                           ("scenario", "solver", "stats", "overrides_applied")}
            run["warnings"] = result.get("warnings") or []
            run["status"] = "done"
        else:
            run["status"] = "failed"
            run["error"] = ((result or {}).get("error")
                            or proc.stderr[-4000:] or proc.stdout[-4000:]
                            or "runner produced no output")
            run["traceback"] = (result or {}).get("traceback")
    except subprocess.TimeoutExpired:
        run["status"] = "failed"
        run["error"] = "run timed out after 1 hour"
    except Exception as exc:  # noqa: BLE001
        run["status"] = "failed"
        run["error"] = repr(exc)
    finally:
        run["finished_at"] = time.time()
        for tmp in (tmp_model, tmp_out, tmp_over):
            if tmp and os.path.isfile(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass


@app.post("/api/run")
def start_run():
    body = request.get_json(force=True)
    try:
        _require_model()
    except ValueError as exc:
        return _err(exc)
    info = envsetup.check_env()
    if not info["ready"]:
        return _err("PyWR environment is not ready — click 'Set up PyWR' first",
                    409)
    with LOCK:
        model_snapshot = json.loads(json.dumps(STATE["model"]))
        model_io.inject_positions(model_snapshot, STATE["positions"])
        model_path = STATE["path"]
        # block the run if data files are still missing — pywr would fail
        # deep in a worker with a cryptic message
        if STATE["data"] and STATE["data"].get("missing"):
            miss = ", ".join(STATE["data"]["missing"])
            return _err(f"cannot run — data file(s) not found: {miss}. "
                        "Add the folder that holds them (Data tab).", 409)
        overrides = dict(body.get("overrides") or {})
        if STATE["data"] and STATE["data"].get("map"):
            overrides["url_map"] = STATE["data"]["map"]
        # scenario picker: which combination to dump (the model still solves
        # the whole ensemble; this only chooses the member to show)
        n_comb = graphops.scenario_combinations(STATE["model"])
        scen_dims = graphops.scenario_dims(STATE["model"])
        scen_idx = 0
        if n_comb > 1 and body.get("scenario_index") is not None:
            try:
                scen_idx = max(0, min(int(body["scenario_index"]), n_comb - 1))
            except (TypeError, ValueError):
                scen_idx = 0
        if scen_idx:
            overrides["scenario_index"] = scen_idx
    run_id = uuid.uuid4().hex[:8]
    RUNS[run_id] = {
        "id": run_id, "status": "queued",
        "label": body.get("label") or f"run {len(RUN_ORDER) + 1}",
        "overrides": body.get("overrides") or None,
        "scenario_index": scen_idx,
        "scenario_label": (graphops.combo_label(scen_dims, scen_idx)
                           if n_comb > 1 else None),
        "started_at": time.time(),
    }
    RUN_ORDER.append(run_id)
    threading.Thread(target=_run_worker,
                     args=(run_id, model_snapshot, model_path,
                           overrides or None),
                     daemon=True).start()
    return jsonify({"ok": True, "run_id": run_id})


@app.get("/api/runs")
def list_runs():
    out = []
    for rid in RUN_ORDER:
        run = RUNS[rid]
        out.append({"id": rid, "status": run["status"], "label": run["label"],
                    "error": run.get("error"),
                    "n_steps": len(run.get("dates", [])),
                    "warnings": run.get("warnings", []),
                    "overrides": bool(run.get("overrides")),
                    "scenario_index": run.get("scenario_index", 0),
                    "scenario_label": run.get("scenario_label")})
    return jsonify({"ok": True, "runs": out})


@app.get("/api/run/<run_id>")
def run_status(run_id):
    run = RUNS.get(run_id)
    if not run:
        return _err("unknown run", 404)
    out = {"ok": True, "id": run_id, "status": run["status"],
           "label": run["label"], "error": run.get("error"),
           "traceback": run.get("traceback"), "meta": run.get("meta"),
           "warnings": run.get("warnings", []),
           "scenario_index": run.get("scenario_index", 0),
           "scenario_label": run.get("scenario_label")}
    if run["status"] == "done":
        dates = run["dates"]
        out["n_steps"] = len(dates)
        out["date_first"], out["date_last"] = dates[0], dates[-1]
        # global scale info for edge coloring
        max_flow = 0.0
        for series in ({"e": e["series"]} for e in run["edges"]):
            s = series["e"]
            if s:
                m = max(s)
                max_flow = max(max_flow, m)
        out["max_edge_flow"] = max_flow
    return jsonify(out)


@app.get("/api/run/<run_id>/frames")
def run_frames(run_id):
    """Per-timestep values for every edge and node over a window of steps."""
    run = RUNS.get(run_id)
    if not run or run["status"] != "done":
        return _err("run not available", 404)
    try:
        start = max(0, int(request.args.get("start", 0)))
        count = min(500, max(1, int(request.args.get("count", 1))))
    except ValueError:
        return _err("bad start/count")
    end = min(len(run["dates"]), start + count)
    frames_edges = [[(None if e["series"] is None else e["series"][t])
                     for e in run["edges"]] for t in range(start, end)]
    node_names = list(run["nodes"].keys())
    frames_nodes = [[run["nodes"][n].get("flow", run["nodes"][n].get("volume"))[t]
                     if run["nodes"][n].get("flow") or run["nodes"][n].get("volume")
                     else None
                     for n in node_names] for t in range(start, end)]
    return jsonify({"ok": True, "start": start, "end": end,
                    "dates": run["dates"][start:end],
                    "edge_keys": [[e["src"], e["dst"], e["exact"]]
                                  for e in run["edges"]],
                    "node_keys": node_names,
                    "edges": frames_edges, "nodes": frames_nodes})


@app.get("/api/run/<run_id>/series")
def run_series(run_id):
    """Full time series for one node (for the chart panel)."""
    run = RUNS.get(run_id)
    if not run or run["status"] != "done":
        return _err("run not available", 404)
    name = request.args.get("node")
    data = run["nodes"].get(name)
    if data is None:
        return _err(f"no results for node {name!r}", 404)
    return jsonify({"ok": True, "node": name, "dates": run["dates"],
                    "label": run["label"], **data})


if __name__ == "__main__":
    port = int(os.environ.get("PYWR_READER_PORT", "5321"))
    print(f"PyWR Reader → http://127.0.0.1:{port}")
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
