"""Running models and getting results out (CSV, save/open a run)."""

import contextlib
import csv
import io
import json
import os
import subprocess
import tempfile
import threading
import time
import uuid

from flask import Blueprint, Response, jsonify, request

from pywr_reader import envsetup, graphops, model_io
from pywr_reader.api.util import APP_DIR, err
from pywr_reader.session import RUNS, WORKSPACE

bp = Blueprint("runs", __name__)


def estimate_edge_flows(model, node_series, exact_edges=None):
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


RUN_TMP_PREFIX = ".pywr_reader_run_"


def sweep_run_temps(directory):
    """Delete orphaned run snapshots. The run itself removes its own, but a
    force-quit or a crash skips that — and the snapshot has to sit beside the
    model (the runner chdirs there so relative table urls resolve), so they
    pile up in the user's model folder for ever."""
    live = RUNS.live_ids()
    try:
        names = os.listdir(directory)
    except OSError:
        return
    for name in names:
        if not (name.startswith(RUN_TMP_PREFIX) and name.endswith(".json")):
            continue
        if name[len(RUN_TMP_PREFIX):-len(".json")] in live:
            continue                    # belongs to a run still in flight
        with contextlib.suppress(OSError):
            os.remove(os.path.join(directory, name))


def _run_worker(run_id, model_snapshot, model_path, overrides):
    run = RUNS.by_id[run_id]
    python = envsetup.env_python()
    workdir = os.path.dirname(model_path) if model_path else tempfile.gettempdir()
    sweep_run_temps(workdir)
    tmp_model = os.path.join(workdir, f"{RUN_TMP_PREFIX}{run_id}.json")
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
            run["edges"] = estimate_edge_flows(model_snapshot, result["nodes"],
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
                with contextlib.suppress(OSError):
                    os.remove(tmp)


@bp.post("/api/run")
def start_run():
    body = request.get_json(force=True)
    try:
        WORKSPACE.require_model()
    except ValueError as exc:
        return err(exc)
    info = envsetup.check_env()
    if not info["ready"]:
        return err("PyWR environment is not ready — click 'Set up PyWR' first",
                    409)
    with WORKSPACE.lock:
        model_snapshot = json.loads(json.dumps(WORKSPACE.model))
        model_io.inject_positions(model_snapshot, WORKSPACE.positions)
        model_path = WORKSPACE.path
        # block the run if data files are still missing — pywr would fail
        # deep in a worker with a cryptic message
        if WORKSPACE.data and WORKSPACE.data.get("missing"):
            miss = ", ".join(WORKSPACE.data["missing"])
            return err(f"cannot run — data file(s) not found: {miss}. "
                        "Add the folder that holds them (Data tab).", 409)
        overrides = dict(body.get("overrides") or {})
        if WORKSPACE.data and WORKSPACE.data.get("map"):
            overrides["url_map"] = WORKSPACE.data["map"]
        # scenario picker: which combination to dump (the model still solves
        # the whole ensemble; this only chooses the member to show)
        n_comb = graphops.scenario_combinations(WORKSPACE.model)
        scen_dims = graphops.scenario_dims(WORKSPACE.model)
        scen_idx = 0
        if n_comb > 1 and body.get("scenario_index") is not None:
            try:
                scen_idx = max(0, min(int(body["scenario_index"]), n_comb - 1))
            except (TypeError, ValueError):
                scen_idx = 0
        if scen_idx:
            overrides["scenario_index"] = scen_idx
    run_id = uuid.uuid4().hex[:8]
    RUNS.by_id[run_id] = {
        "id": run_id, "status": "queued",
        "label": body.get("label") or f"run {len(RUNS.order) + 1}",
        "overrides": body.get("overrides") or None,
        "scenario_index": scen_idx,
        "scenario_label": (graphops.combo_label(scen_dims, scen_idx)
                           if n_comb > 1 else None),
        "started_at": time.time(),
    }
    RUNS.order.append(run_id)
    threading.Thread(target=_run_worker,
                     args=(run_id, model_snapshot, model_path,
                           overrides or None),
                     daemon=True).start()
    return jsonify({"ok": True, "run_id": run_id})


@bp.get("/api/runs")
def list_runs():
    out = []
    for rid in RUNS.order:
        run = RUNS.by_id[rid]
        out.append({"id": rid, "status": run["status"], "label": run["label"],
                    "error": run.get("error"),
                    "n_steps": len(run.get("dates", [])),
                    "warnings": run.get("warnings", []),
                    "overrides": bool(run.get("overrides")),
                    "scenario_index": run.get("scenario_index", 0),
                    "scenario_label": run.get("scenario_label")})
    return jsonify({"ok": True, "runs": out})


@bp.get("/api/run/<run_id>")
def run_status(run_id):
    run = RUNS.get(run_id)
    if not run:
        return err("unknown run", 404)
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


@bp.get("/api/run/<run_id>/frames")
def run_frames(run_id):
    """Per-timestep values for every edge and node over a window of steps."""
    run = RUNS.get(run_id)
    if not run or run["status"] != "done":
        return err("run not available", 404)
    try:
        start = max(0, int(request.args.get("start", 0)))
        count = min(500, max(1, int(request.args.get("count", 1))))
    except ValueError:
        return err("bad start/count")
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


def _csv_response(rows, filename):
    """Send rows as a CSV download. utf-8-sig so Excel reads node names with
    accents properly instead of mojibake."""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerows(rows)
    return Response(
        buf.getvalue().encode("utf-8-sig"),
        # content_type, not mimetype: Flask appends its own charset to a
        # text/* mimetype, which would leave two charsets in the header
        content_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'})


def _safe_stem(text, fallback="run"):
    """A filename-safe version of a run label ('what-if 8' → 'what-if-8')."""
    keep = [c if (c.isalnum() or c in "-_") else "-" for c in (text or "")]
    stem = "".join(keep).strip("-")
    while "--" in stem:
        stem = stem.replace("--", "-")
    return stem or fallback


@bp.get("/api/run/<run_id>/csv")
def run_csv(run_id):
    """The whole run as one wide CSV: a row per timestep, a column per node
    series and per edge. Edges pywr couldn't attribute exactly are marked, so
    a reader never mistakes an estimate for a recorded flow."""
    run = RUNS.get(run_id)
    if not run or run["status"] != "done":
        return err("run not available", 404)
    node_cols = []
    for name in sorted(run["nodes"]):
        for kind in ("flow", "volume"):
            if run["nodes"][name].get(kind) is not None:
                node_cols.append((f"{name} ({kind})", run["nodes"][name][kind]))
    edge_cols = []
    for edge in run["edges"]:
        if edge["series"] is None:
            continue
        label = f"{edge['src']} -> {edge['dst']} (flow)"
        if not edge["exact"]:
            label += " [estimated]"
        edge_cols.append((label, edge["series"]))

    header = ["date"] + [c[0] for c in node_cols] + [c[0] for c in edge_cols]
    rows = [header]
    for i, date in enumerate(run["dates"]):
        row = [date]
        for _, series in node_cols + edge_cols:
            row.append(series[i] if i < len(series) else "")
        rows.append(row)
    return _csv_response(rows, f"{_safe_stem(run['label'])}.csv")


@bp.get("/api/run/<run_id>/node.csv")
def run_node_csv(run_id):
    """One node's series, with a column per run — so what you download is what
    the chart is showing, overlaid comparisons included."""
    name = request.args.get("node")
    ids = [run_id] + [i for i in (request.args.get("compare") or "").split(",")
                      if i and i != run_id]
    runs = [RUNS.get(i) for i in ids]
    runs = [r for r in runs if r and r["status"] == "done"
            and (r.get("nodes") or {}).get(name)]
    if not runs:
        return err(f"no results for node {name!r}", 404)

    cols = []
    for run in runs:
        data = run["nodes"][name]
        kind = "volume" if data.get("volume") is not None else "flow"
        cols.append((f"{run['label']} ({kind})", data[kind]))
    rows = [["date"] + [c[0] for c in cols]]
    for i, date in enumerate(runs[0]["dates"]):
        rows.append([date] + [s[i] if i < len(s) else "" for _, s in cols])
    return _csv_response(rows, f"{_safe_stem(name, 'node')}.csv")


@bp.post("/api/run/<run_id>/save")
def save_run(run_id):
    """Write a finished run beside the model so it outlives the app. Runs live
    in memory otherwise, and every one is lost when the server stops."""
    run = RUNS.get(run_id)
    if not run or run["status"] != "done":
        return err("run not available", 404)
    body = request.get_json(force=True, silent=True) or {}
    path = body.get("path")
    if not path:
        if not WORKSPACE.path:
            return err("save the model first, or give a path")
        stem = os.path.splitext(WORKSPACE.path)[0]
        path = f"{stem}.{_safe_stem(run['label'])}.pywrrun.json"
    payload = {"pywr_reader_run": 1, "label": run["label"],
               "dates": run["dates"], "nodes": run["nodes"],
               "edges": run["edges"], "meta": run.get("meta"),
               "warnings": run.get("warnings", []),
               "overrides": run.get("overrides"),
               "model": os.path.basename(WORKSPACE.path or "")}
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
    except OSError as exc:
        return err(exc)
    return jsonify({"ok": True, "path": path})


@bp.post("/api/run/open")
def open_run():
    """Load a saved run back into the runs list."""
    body = request.get_json(force=True, silent=True) or {}
    path = (body.get("path") or "").strip()
    if not os.path.isfile(path):
        return err(f"file not found: {path}")
    try:
        with open(path, encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        return err(f"could not read the run: {exc}")
    if not isinstance(data, dict) or "pywr_reader_run" not in data:
        return err("not a saved PyWR Reader run")
    for key in ("dates", "nodes", "edges"):
        if key not in data:
            return err(f"saved run has no {key!r}")
    run_id = uuid.uuid4().hex[:8]
    max_flow = max((max(e["series"]) for e in data["edges"]
                    if e.get("series")), default=0.0)
    RUNS.by_id[run_id] = {
        "id": run_id, "status": "done",
        "label": data.get("label") or os.path.basename(path),
        "dates": data["dates"], "nodes": data["nodes"], "edges": data["edges"],
        "meta": data.get("meta"), "warnings": data.get("warnings", []),
        "overrides": data.get("overrides"), "started_at": time.time(),
        "max_edge_flow": max_flow, "loaded_from": path,
    }
    RUNS.order.append(run_id)
    return jsonify({"ok": True, "run_id": run_id})


@bp.get("/api/run/<run_id>/series")
def run_series(run_id):
    """Full time series for one node (for the chart panel)."""
    run = RUNS.get(run_id)
    if not run or run["status"] != "done":
        return err("run not available", 404)
    name = request.args.get("node")
    data = run["nodes"].get(name)
    if data is None:
        return err(f"no results for node {name!r}", 404)
    return jsonify({"ok": True, "node": name, "dates": run["dates"],
                    "label": run["label"], **data})


