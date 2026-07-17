"""Run a pywr model and dump every node's flow/volume as JSON.

Executed with the *pywr environment's* Python, not the app's:

    python runner.py <model.json> <output.json> [overrides.json]

overrides.json (optional): {"nodes": {"<name>": {"<param>": value, ...}},
                            "timestepper": {...},
                            "url_map": {"<original url>": "<local path>"},
                            "scenario_index": <int>}
Values overwrite the node's scalar attributes before the run — this powers
the app's what-if mode. url_map rewrites external data-file paths so a model
authored on another machine can find its data locally. scenario_index picks
which scenario combination's series to dump (default 0) — the model still
solves the whole ensemble in one run, this just chooses the member to show.

Output JSON:
    {"ok": true, "dates": [...], "nodes": {name: {"flow": [...],
     "volume": [...]}}, "exact_edges": {"<edge index>": [...]},
     "scenario": {...}, "solver": "...", "stats": {...},
     "warnings": [...]}   # non-fatal pywr notes, e.g. a version mismatch
Flows are the per-timestep values of the chosen scenario combination.
"exact_edges" holds exact per-edge flows for split/junction edges pywr can't
otherwise attribute (recorded via transparent proxy links), keyed by the
edge's index in the original model. "scenario" reports {"combinations": N,
"shown": i, "dims": [{name, size, ensemble_names}]} so the app can offer a
picker.
"""

import json
import os
import sys
import traceback
import warnings


def compat_fixups(obj):
    """Translate non-standard parameter spellings into real pywr types.
    Graph Overlay PyWR exports {"type": "CSVParameter"/"ExcelParameter"} —
    pywr's actual reader for both is the dataframe parameter."""
    if isinstance(obj, dict):
        ptype = str(obj.get("type", "")).lower()
        if ptype in ("csvparameter", "excelparameter"):
            obj["type"] = "dataframe"
            if "sheet" in obj and "sheet_name" not in obj:
                obj["sheet_name"] = obj.pop("sheet")
        # pywr wants [from, to] or [from, to, slot_from, slot_to] — pad
        # 3-element edges (Graph Overlay writes [from, to, slot])
        for edge in obj.get("edges", []) if "nodes" in obj else []:
            if isinstance(edge, list) and len(edge) == 3:
                edge.append(None)
        for val in obj.values():
            compat_fixups(val)
    elif isinstance(obj, list):
        for item in obj:
            compat_fixups(item)
    return obj


def apply_overrides(data, overrides):
    node_over = (overrides or {}).get("nodes") or {}
    applied = []
    for node in data.get("nodes", []):
        changes = node_over.get(node.get("name"))
        if not changes:
            continue
        for key, val in changes.items():
            if key in ("name", "type", "position"):
                continue
            node[key] = val
            applied.append(f"{node['name']}.{key}")
    ts_over = (overrides or {}).get("timestepper") or {}
    if ts_over:
        data.setdefault("timestepper", {}).update(ts_over)
        applied.extend(f"timestepper.{k}" for k in ts_over)
    return applied


def apply_url_map(data, url_map):
    """Rewrite external data-file urls to their resolved local paths."""
    if not url_map:
        return 0
    changed = 0

    def walk(obj):
        nonlocal changed
        if isinstance(obj, dict):
            url = obj.get("url")
            if isinstance(url, str) and url in url_map and url_map[url] != url:
                obj["url"] = url_map[url]
                changed += 1
            for val in obj.values():
                walk(val)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    walk(data)
    return changed


def scenario_meta(model, shown):
    """Describe a loaded pywr model's scenarios and which combination was
    captured. Ordering matches pywr's ScenarioCollection.combinations
    (a C-order cartesian product — the last scenario varies fastest)."""
    meta = {"combinations": 1, "shown": int(shown), "dims": []}
    try:
        sc = model.scenarios
        for s in sc.scenarios:
            names = list(getattr(s, "ensemble_names", None) or [])
            meta["dims"].append({
                "name": str(s.name), "size": int(s.size),
                "ensemble_names": [str(x) for x in names],
            })
        try:
            meta["combinations"] = int(len(sc.combinations) or 1)
        except TypeError:      # combinations is None until setup — fall back
            n = 1
            for d in meta["dims"]:
                n *= d["size"]
            meta["combinations"] = n or 1
    except Exception:          # noqa: BLE001 — a model with no scenarios
        pass
    return meta


PROXY_PREFIX = "__reader_edge__"


def insert_edge_proxies(data):
    """Splice a pass-through link node onto every *ambiguous* edge so pywr
    records its flow directly. An edge is ambiguous when neither endpoint
    funnels all its flow through it — the source fans out to more than one node
    AND the destination is fed by more than one node (a split feeding a
    junction). There, node totals can't pin the per-edge flow, so the app would
    otherwise only estimate it.

    A plain link node with no cost or limits is a transparent conduit: putting
    A→L→B in place of A→B leaves every other flow in the solution unchanged,
    while L's recorded flow IS the exact A→B flow. Edges an endpoint already
    pins are left alone (their flow equals a node total). Edges that carry an
    explicit destination slot are left alone too (rare — kept simple).

    Mutates data in place. Returns {original_edge_index: proxy_node_name}."""
    edges = data.get("edges", [])
    out_deg, in_deg = {}, {}
    for e in edges:
        if len(e) >= 2:
            out_deg[e[0]] = out_deg.get(e[0], 0) + 1
            in_deg[e[1]] = in_deg.get(e[1], 0) + 1

    proxy_map = {}
    head_edges, tail_edges, proxy_nodes = [], [], []
    for i, e in enumerate(edges):
        if len(e) < 2:
            head_edges.append(e)
            continue
        src, dst = e[0], e[1]
        from_slot = e[2] if len(e) >= 3 else None
        to_slot = e[3] if len(e) >= 4 else None
        ambiguous = out_deg.get(src, 0) >= 2 and in_deg.get(dst, 0) >= 2
        if not ambiguous or to_slot is not None:
            head_edges.append(e)
            continue
        proxy = f"{PROXY_PREFIX}{i}"
        proxy_nodes.append({"name": proxy, "type": "link"})
        head_edges.append([src, proxy, from_slot, None]
                          if from_slot is not None else [src, proxy])
        tail_edges.append([proxy, dst])
        proxy_map[i] = proxy

    if proxy_map:
        data["edges"] = head_edges + tail_edges
        data.setdefault("nodes", []).extend(proxy_nodes)
    return proxy_map


def main():
    model_path, out_path = sys.argv[1], sys.argv[2]
    overrides = None
    if len(sys.argv) > 3:
        with open(sys.argv[3]) as fh:
            overrides = json.load(fh)

    result = {"ok": False}
    caught = warnings.catch_warnings(record=True)
    warn_records = caught.__enter__()
    warnings.simplefilter("always")
    try:
        from pywr.model import Model
        try:
            from pywr.nodes import Storage
        except ImportError:
            from pywr.core import Storage
        from pywr.recorders import (NumpyArrayNodeRecorder,
                                    NumpyArrayStorageRecorder)

        with open(model_path, encoding="utf-8-sig") as fh:
            data = json.load(fh)
        compat_fixups(data)
        apply_url_map(data, (overrides or {}).get("url_map"))
        applied = apply_overrides(data, overrides)
        # splice recording proxies onto split/junction edges (transparent)
        proxy_map = insert_edge_proxies(data)
        proxy_index = {name: idx for idx, name in proxy_map.items()}

        # Loading via dict keeps relative table/CSV urls working as long as
        # we chdir to the model's folder first.
        model_dir = os.path.dirname(os.path.abspath(model_path))
        os.chdir(model_dir)
        model = Model.load(data, path=model_path)

        recorders = {}
        proxy_recorders = {}       # original edge index -> recorder
        for node in model.nodes:
            name = node.name
            safe = f"__reader_rec__{name}"
            try:
                if isinstance(node, Storage):
                    kind, rec = "volume", NumpyArrayStorageRecorder(
                        model, node, name=safe)
                else:
                    kind, rec = "flow", NumpyArrayNodeRecorder(
                        model, node, name=safe)
            except Exception:  # noqa: BLE001 — some virtual nodes reject recorders
                continue
            if name in proxy_index:
                proxy_recorders[proxy_index[name]] = rec
            else:
                recorders[name] = (kind, rec)

        run_stats = model.run()

        def date_str(d):
            if hasattr(d, "to_timestamp"):  # pandas Period
                d = d.to_timestamp()
            return str(d)[:10]

        # which scenario combination to dump (0 unless what-if asked otherwise)
        requested = int((overrides or {}).get("scenario_index") or 0)
        meta = scenario_meta(model, requested)
        shown = requested if 0 <= requested < meta["combinations"] else 0
        meta["shown"] = shown

        dates = [date_str(d) for d in model.timestepper.datetime_index]
        nodes_out = {}
        for name, (kind, rec) in recorders.items():
            arr = rec.data  # shape (time, scenario_combinations)
            col = shown if shown < arr.shape[1] else 0
            series = [round(float(v), 5) for v in arr[:, col]]
            nodes_out.setdefault(name, {})[kind] = series

        # exact per-edge flows recorded by the spliced proxies, keyed by the
        # edge's index in the original model (so the app can match them back)
        exact_edges = {}
        for idx, rec in proxy_recorders.items():
            arr = rec.data
            col = shown if shown < arr.shape[1] else 0
            exact_edges[str(idx)] = [round(float(v), 5) for v in arr[:, col]]

        result.update({
            "ok": True,
            "dates": dates,
            "nodes": nodes_out,
            "exact_edges": exact_edges,
            "scenario": meta,
            "solver": getattr(model.solver, "name", str(model.solver)),
            "overrides_applied": applied,
            "stats": {"timesteps": len(dates),
                      "speed": getattr(run_stats, "speed", None)},
        })
    except Exception as exc:  # noqa: BLE001 — report everything to the app
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["traceback"] = traceback.format_exc()
    finally:
        # surface pywr's warnings (e.g. "model requires version X") so the run
        # is reported as "ok, with notes" rather than silently succeeding
        seen = set()
        notes = []
        for w in warn_records:
            msg = str(w.message).strip()
            if msg and msg not in seen:
                seen.add(msg)
                notes.append(msg)
        result["warnings"] = notes
        caught.__exit__(None, None, None)

    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh)
    sys.exit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
