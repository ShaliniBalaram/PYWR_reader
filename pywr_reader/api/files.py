"""Open, save, browse and edit-as-JSON — the model file itself."""

import json
import os
import string

from flask import Blueprint, jsonify, request, send_from_directory

from pywr_reader import graphops, model_io
from pywr_reader import layout as layout_mod
from pywr_reader.api.util import APP_DIR, err
from pywr_reader.session import WORKSPACE, normalize_positions

bp = Blueprint("files", __name__)


@bp.get("/")
def index():
    return send_from_directory(os.path.join(APP_DIR, "static"), "index.html")


# ---------------------------------------------------------------------------
# File browsing / open / save
# ---------------------------------------------------------------------------
def browse_roots():
    """Shortcuts for the Open dialog, named for the platform you're on:
    the drives on Windows, /Volumes on macOS, the usual mount points on Linux.
    Returned to the browser so it never has to guess — hard-coding "/Volumes"
    left Windows with no way to reach D: at all."""
    roots = [{"label": "Home", "path": os.path.expanduser("~")}]
    if os.name == "nt":
        for letter in string.ascii_uppercase:
            drive = f"{letter}:\\"
            if os.path.isdir(drive):
                roots.append({"label": drive, "path": drive})
    else:
        for path, label in (("/Volumes", "Volumes"),      # macOS
                            ("/media", "Media"),          # Linux removable
                            ("/mnt", "Mounts")):          # Linux mounts
            if os.path.isdir(path):
                roots.append({"label": label, "path": path})
    return roots


@bp.get("/api/browse")
def browse():
    path = request.args.get("path") or os.path.expanduser("~")
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.isdir(path):
        return err(f"not a directory: {path}")
    entries = []
    try:
        for name in sorted(os.listdir(path), key=str.lower):
            if name.startswith((".", "._")):
                continue
            full = os.path.join(path, name)      # os.path.join, not "/" —
            try:                                 # Windows wants a backslash
                is_dir = os.path.isdir(full)
            except OSError:                      # unreadable mount, skip it
                continue
            if is_dir:
                entries.append({"name": name, "kind": "dir", "path": full})
            elif os.path.splitext(name)[1].lower() in (".json", ".tcm", ".csv"):
                try:
                    size = os.path.getsize(full)
                except OSError:
                    size = 0
                entries.append({"name": name, "kind": "file", "size": size,
                                "path": full})
    except PermissionError:
        return err("permission denied", 403)
    parent = os.path.dirname(path)
    return jsonify({"ok": True, "path": path,
                    # at a filesystem root ("C:\\", "/") dirname is itself:
                    # say so, rather than offer a ".." that goes nowhere
                    "parent": parent if parent != path else None,
                    "entries": entries, "roots": browse_roots()})


@bp.post("/api/open")
def open_model():
    body = request.get_json(force=True)
    path = (body.get("path") or "").strip()
    if not os.path.isfile(path):
        return err(f"file not found: {path}")

    # A .tcm opened while a model is already loaded applies its positions to
    # that model (the natural "open model, then open its view file" flow).
    if path.lower().endswith(".tcm") and WORKSPACE.model is not None:
        try:
            tcm_positions, _, _ = model_io.load_tcm(path)
        except Exception as exc:  # noqa: BLE001
            return err(exc)
        with WORKSPACE.lock:
            names = {n["name"] for n in WORKSPACE.model.get("nodes", [])}
            matched = 0
            for name, xy in tcm_positions.items():
                if name in names:
                    WORKSPACE.positions[name] = xy
                    matched += 1
            if matched:
                all_names = [n["name"] for n in WORKSPACE.model.get("nodes", [])]
                WORKSPACE.positions = normalize_positions(
                    layout_mod.layout_missing(
                        all_names, WORKSPACE.model.get("edges", []),
                        WORKSPACE.positions))
                WORKSPACE.dirty = True
                WORKSPACE.layout_was_auto = False
            WORKSPACE.warnings = ([f".tcm positions applied to {matched} of "
                                  f"{len(names)} nodes"] if matched else
                                 [".tcm node names did not match the open model"])
        return jsonify(WORKSPACE.graph_payload())

    try:
        loaded = model_io.load_any(path)
    except Exception as exc:  # noqa: BLE001 — surface parse errors to the UI
        return err(exc)

    with WORKSPACE.lock:
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
            positions = normalize_positions(positions)
        WORKSPACE.load(model, positions, path=loaded["path"], auto=auto,
                       warnings=loaded["warnings"])
    return jsonify(WORKSPACE.graph_payload())


@bp.post("/api/new")
def new_model():
    """Start an empty model — the blank canvas for tracing a network over an
    image, or building one from scratch."""
    body = request.get_json(force=True, silent=True) or {}
    title = (body.get("title") or "Untitled model").strip() or "Untitled model"
    with WORKSPACE.lock:
        WORKSPACE.load({
            "metadata": {"title": title, "minimum_version": "1.20.0"},
            "timestepper": {"start": "2000-01-01", "end": "2000-12-31",
                            "timestep": 1},
            "nodes": [], "edges": [], "parameters": {}, "recorders": {},
        }, {}, dirty=True)
    return jsonify(WORKSPACE.graph_payload())


@bp.get("/api/graph")
def get_graph():
    try:
        WORKSPACE.require_model()
    except ValueError as exc:
        return err(exc)
    return jsonify(WORKSPACE.graph_payload())


@bp.get("/api/model/raw")
def raw_model():
    try:
        WORKSPACE.require_model()
    except ValueError as exc:
        return err(exc)
    model = json.loads(json.dumps(WORKSPACE.model))
    model_io.inject_positions(model, WORKSPACE.positions)
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


RENAMEABLE = ("nodes",) + graphops.DEFINITION_SECTIONS


def _renames_by_section(renames):
    """Normalise the two accepted "renames" shapes to {section: {old: new}}.
    Returns an error string instead when the payload is malformed."""
    if not isinstance(renames, dict):
        return "'renames' must be an object"
    # the flat {old: new} form means node renames — the shape the node editor
    # has always sent, kept working
    if all(isinstance(v, str) for v in renames.values()):
        renames = {"nodes": renames}
    out = {}
    for section, pairs in renames.items():
        if section not in RENAMEABLE:
            return f"cannot rename in {section!r}"
        if not isinstance(pairs, dict):
            return f"'renames.{section}' must be an object of {{old: new}}"
        for old, new in pairs.items():
            if not isinstance(old, str) or not isinstance(new, str) or not new:
                return "'renames' must map a name to a non-empty name"
        out[section] = pairs
    return out


@bp.post("/api/model/raw")
def replace_raw_model():
    """Replace the model with hand-edited JSON (the JSON editor's Apply).
    The file on disk is untouched until Save.

    Optional "renames" — when the editor sees a name change it says so, and
    the references are rewritten to match before validating (otherwise every
    edge to a renamed node would look like a dangling one). Two accepted
    shapes: a flat {old: new} of node renames, or {section: {old: new}} for
    "nodes" / "parameters" / "recorders" / "tables". The renaming has already
    happened in the edited JSON — only the references are left to carry."""
    body = request.get_json(force=True, silent=True)
    if not isinstance(body, dict) or "model" not in body:
        return err("expected {\"model\": {…}}")
    model = body["model"]
    by_section = _renames_by_section(body.get("renames") or {})
    if isinstance(by_section, str):
        return err(by_section)
    notes = []
    for section, pairs in by_section.items():
        for old, new in pairs.items():
            if old == new:
                continue
            notes.extend(
                graphops.rewrite_node_refs(model, old, new) if section == "nodes"
                else graphops.rewrite_definition_refs(model, section, old, new))
    problem = _validate_model(model)
    if problem:
        return err(problem)
    with WORKSPACE.lock:
        names = [n["name"] for n in model["nodes"]]
        # positions come from the edited JSON where it has them, otherwise
        # keep what's on screen so an unrelated edit doesn't scramble the layout
        positions = dict(WORKSPACE.positions)
        for old, new in by_section.get("nodes", {}).items():
            if old != new and old in positions:
                positions[new] = positions.pop(old)   # a rename stays put
        positions.update(model_io.extract_positions(model))
        positions = {n: xy for n, xy in positions.items() if n in set(names)}
        if len(positions) < len(names):
            positions = layout_mod.layout_missing(
                names, model.get("edges", []), positions)
        # an in-place edit: keep the current path and data-file search
        WORKSPACE.model = model
        WORKSPACE.positions = normalize_positions(positions)
        WORKSPACE.dirty = True
        WORKSPACE.warnings = notes
        WORKSPACE.resolve_data()
    return jsonify(WORKSPACE.graph_payload())


@bp.post("/api/save")
def save_model():
    body = request.get_json(force=True)
    try:
        WORKSPACE.require_model()
        path = (body.get("path") or WORKSPACE.path or "").strip()
        if not path:
            return err("no target path")
        if not path.lower().endswith(".json"):
            path += ".json"
        with WORKSPACE.lock:
            model_io.save_pywr_json(WORKSPACE.model, WORKSPACE.positions, path)
            WORKSPACE.path, WORKSPACE.dirty = path, False
        return jsonify({"ok": True, "path": path})
    except (ValueError, OSError) as exc:
        return err(exc)


@bp.post("/api/export_csv")
def export_csv():
    body = request.get_json(force=True)
    try:
        WORKSPACE.require_model()
        directory = body.get("directory") or os.path.dirname(WORKSPACE.path or APP_DIR)
        with WORKSPACE.lock:
            paths = model_io.export_csv_pair(WORKSPACE.model, WORKSPACE.positions,
                                             directory)
        return jsonify({"ok": True, "files": paths})
    except (ValueError, OSError) as exc:
        return err(exc)


# ---------------------------------------------------------------------------
# Layout / positions
# ---------------------------------------------------------------------------
