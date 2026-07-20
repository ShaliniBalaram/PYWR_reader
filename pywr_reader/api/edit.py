"""Editing the network: layouts, node/edge CRUD, path tracing."""


from flask import Blueprint, jsonify, request

from pywr_reader import graphops
from pywr_reader import layout as layout_mod
from pywr_reader.api.util import err
from pywr_reader.session import WORKSPACE, normalize_positions

bp = Blueprint("edit", __name__)


@bp.get("/api/layouts")
def list_layouts():
    """The layouts the picker can offer (label + hint come from layout.py)."""
    return jsonify({"ok": True, "layouts": layout_mod.LAYOUTS})


@bp.post("/api/layout")
def relayout():
    body = request.get_json(force=True)
    mode = body.get("mode", "all")
    kind = body.get("kind") or "layered"
    try:
        WORKSPACE.require_model()
    except ValueError as exc:
        return err(exc)
    if mode != "missing" and kind not in layout_mod.LAYOUT_KINDS:
        return err(f"unknown layout {kind!r}")
    with WORKSPACE.lock:
        model = WORKSPACE.model
        names = [n["name"] for n in model.get("nodes", [])]
        if mode == "missing":
            WORKSPACE.positions = layout_mod.layout_missing(
                names, model.get("edges", []), WORKSPACE.positions)
        else:
            groups = {n["name"]: layout_mod.node_group(n.get("type", ""))
                      for n in model.get("nodes", [])}
            WORKSPACE.positions = normalize_positions(layout_mod.compute(
                kind, names, model.get("edges", []),
                affinity=graphops.node_affinity(model), groups=groups))
        WORKSPACE.dirty = True
        WORKSPACE.layout_was_auto = True
    return jsonify(WORKSPACE.graph_payload())


@bp.post("/api/positions")
def set_positions():
    body = request.get_json(force=True)
    try:
        WORKSPACE.require_model()
    except ValueError as exc:
        return err(exc)
    with WORKSPACE.lock:
        for name, xy in (body.get("positions") or {}).items():
            if isinstance(xy, (list, tuple)) and len(xy) >= 2:
                WORKSPACE.positions[name] = [float(xy[0]), float(xy[1])]
        WORKSPACE.dirty = True
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Editing
# ---------------------------------------------------------------------------
@bp.post("/api/node/add")
def node_add():
    body = request.get_json(force=True)
    try:
        WORKSPACE.require_model()
        with WORKSPACE.lock:
            node = graphops.add_node(WORKSPACE.model, body.get("node") or {})
            pos = body.get("pos")
            if isinstance(pos, (list, tuple)) and len(pos) >= 2:
                WORKSPACE.positions[node["name"]] = [float(pos[0]), float(pos[1])]
            WORKSPACE.dirty = True
    except ValueError as exc:
        return err(exc)
    return jsonify(WORKSPACE.graph_payload())


@bp.post("/api/node/update")
def node_update():
    body = request.get_json(force=True)
    try:
        WORKSPACE.require_model()
        with WORKSPACE.lock:
            graphops.update_node(WORKSPACE.model, body.get("name"),
                                 body.get("changes"), body.get("removals"))
            new_type = (body.get("changes") or {}).get("type")
            if new_type:
                graphops.node_by_name(WORKSPACE.model, body["name"])["type"] = new_type
            WORKSPACE.dirty = True
    except ValueError as exc:
        return err(exc)
    return jsonify(WORKSPACE.graph_payload())


@bp.post("/api/node/rename")
def node_rename():
    body = request.get_json(force=True)
    try:
        WORKSPACE.require_model()
        with WORKSPACE.lock:
            notes = graphops.rename_node(WORKSPACE.model, body.get("old"),
                                         body.get("new"))
            if body.get("old") in WORKSPACE.positions:
                WORKSPACE.positions[body["new"]] = WORKSPACE.positions.pop(body["old"])
            WORKSPACE.dirty = True
    except ValueError as exc:
        return err(exc)
    payload = WORKSPACE.graph_payload()
    payload["notes"] = notes
    return jsonify(payload)


@bp.post("/api/node/delete")
def node_delete():
    body = request.get_json(force=True)
    try:
        WORKSPACE.require_model()
        with WORKSPACE.lock:
            warnings = graphops.delete_node(WORKSPACE.model, body.get("name"))
            WORKSPACE.positions.pop(body.get("name"), None)
            WORKSPACE.dirty = True
    except ValueError as exc:
        return err(exc)
    payload = WORKSPACE.graph_payload()
    payload["delete_warnings"] = warnings
    return jsonify(payload)


@bp.post("/api/edge/add")
def edge_add():
    body = request.get_json(force=True)
    try:
        WORKSPACE.require_model()
        with WORKSPACE.lock:
            graphops.add_edge(WORKSPACE.model, body.get("src"), body.get("dst"))
            WORKSPACE.dirty = True
    except ValueError as exc:
        return err(exc)
    return jsonify(WORKSPACE.graph_payload())


@bp.post("/api/edge/delete")
def edge_delete():
    body = request.get_json(force=True)
    try:
        WORKSPACE.require_model()
        with WORKSPACE.lock:
            graphops.delete_edge(WORKSPACE.model, body.get("src"), body.get("dst"))
            WORKSPACE.dirty = True
    except ValueError as exc:
        return err(exc)
    return jsonify(WORKSPACE.graph_payload())


@bp.get("/api/trace")
def trace():
    name = request.args.get("name")
    direction = request.args.get("dir", "downstream")
    try:
        WORKSPACE.require_model()
        nodes, edges = graphops.trace(WORKSPACE.model, name, direction)
    except ValueError as exc:
        return err(exc)
    return jsonify({"ok": True, "nodes": sorted(nodes),
                    "edges": sorted(edges)})


# ---------------------------------------------------------------------------
# PyWR environment
# ---------------------------------------------------------------------------
