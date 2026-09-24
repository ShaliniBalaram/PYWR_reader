"""Open, save, browse and edit-as-JSON — the model file itself."""

import json
import os
import shutil
import string
import sys

from flask import Blueprint, jsonify, request, send_from_directory

from pywr_reader import graphops, model_io
from pywr_reader import layout as layout_mod
from pywr_reader.api.util import APP_DIR, err
from pywr_reader.session import (
    RUNS,
    WORKSPACE,
    apply_normalize_transform,
    normalize_positions,
    normalize_transform,
)

bp = Blueprint("files", __name__)


@bp.get("/")
def index():
    return send_from_directory(os.path.join(APP_DIR, "static"), "index.html")


# ---------------------------------------------------------------------------
# File browsing / open / save
# ---------------------------------------------------------------------------
def windows_drives():
    """The drive letters that actually exist, from the bitmask Windows keeps
    for exactly this. Probing A:\\ … Z:\\ with isdir() instead makes the Open
    dialog stall: a disconnected network drive blocks until it times out, and
    an empty card reader or optical drive gets spun up to answer. Falls back to
    probing if the call is unavailable."""
    try:
        import ctypes
        mask = ctypes.windll.kernel32.GetLogicalDrives()
    except Exception:      # noqa: BLE001 — any ctypes/attribute failure
        return [f"{c}:\\" for c in string.ascii_uppercase
                if os.path.isdir(f"{c}:\\")]
    return [f"{c}:\\" for i, c in enumerate(string.ascii_uppercase)
            if mask >> i & 1]


def browse_roots():
    """Shortcuts for the Open dialog, named for the platform you're on:
    the drives on Windows, /Volumes on macOS, the usual mount points on Linux.
    Returned to the browser so it never has to guess — hard-coding "/Volumes"
    left Windows with no way to reach D: at all."""
    roots = [{"label": "Home", "path": os.path.expanduser("~")}]
    if os.name == "nt":
        roots.extend({"label": drive, "path": drive}
                     for drive in windows_drives())
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


EXAMPLE_PARTS = ("examples", "gw_network")


def example_path():
    """The bundled demo model, as a file the user can actually keep.

    From source that is simply the copy in the repository. A packaged build
    unpacks it into a temporary folder that is deleted when the app exits, so
    it is copied out next to the executable first — otherwise opening the
    example and saving would write into that temp folder, and the work would
    be gone by the next launch.

    Returns None when the example isn't there at all."""
    bundled = os.path.join(APP_DIR, *EXAMPLE_PARTS, "pywr_model.json")
    if not os.path.isfile(bundled):
        return None
    if not getattr(sys, "frozen", False):
        return bundled

    beside_exe = os.path.join(os.path.dirname(os.path.abspath(sys.executable)),
                              *EXAMPLE_PARTS)
    target = os.path.join(beside_exe, "pywr_model.json")
    if not os.path.isfile(target):
        try:
            # the whole folder: the model reads params.csv beside itself.
            # "._*" are macOS resource forks that ride along from a non-native
            # filesystem — junk everywhere else in this app, junk here too.
            shutil.copytree(os.path.dirname(bundled), beside_exe,
                            dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns("._*", ".DS_Store"))
        except OSError:
            return bundled       # read-only spot — better than no example
    return target


@bp.get("/api/example")
def example_model():
    """Where the demo model is. The packaged build unpacks it somewhere the
    browser could never guess, so it has to ask."""
    path = example_path()
    return jsonify({"ok": True, "path": path})


def _view_prefs(tcm_view, transform):
    """A .tcm's presentation state, with its camera moved into the coordinate
    space the positions ended up in. Returns None when there is nothing to
    carry, so the frontend can simply test for absence."""
    if not tcm_view:
        return None
    prefs = dict(tcm_view)
    port = prefs.get("viewport")
    if port:
        scale, _, _ = transform
        prefs["viewport"] = {
            "origin": apply_normalize_transform(port["origin"], transform),
            # pixels per world unit, restated for the rescaled positions
            "scale": port["scale"] / scale,
        }
    return prefs


@bp.post("/api/open")
def open_model():
    body = request.get_json(force=True)
    path = (body.get("path") or "").strip()
    if not os.path.isfile(path):
        return err(f"file not found: {path}")

    # A .tcm opened while a model is already loaded applies its positions to
    # that model (the natural "open model, then open its view file" flow).
    # "as_model" is how the UI says "no, open the model this .tcm describes
    # instead" — otherwise a .tcm whose names don't match the open model could
    # only ever be applied, never opened, once anything was on screen.
    if (path.lower().endswith(".tcm") and WORKSPACE.model is not None
            and not body.get("as_model")):
        try:
            tcm_positions, _, _, tcm_view = model_io.load_tcm(path)
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
                laid_out = layout_mod.layout_missing(
                    all_names, WORKSPACE.model.get("edges", []),
                    WORKSPACE.positions)
                transform = normalize_transform(laid_out)
                WORKSPACE.positions = normalize_positions(laid_out)
                WORKSPACE.view_prefs = _view_prefs(tcm_view, transform)
                WORKSPACE.dirty = True
                WORKSPACE.layout_was_auto = False
            WORKSPACE.warnings = ([f".tcm positions applied to {matched} of "
                                  f"{len(names)} nodes"] if matched else
                                 [".tcm node names did not match the open model"])
        payload = WORKSPACE.graph_payload()
        # The open model stayed put and only its positions moved, so the runs
        # and results still describe what is on screen. Say so plainly rather
        # than leave the UI to infer it from the path.
        payload["tcm_applied"] = True
        # nothing matched: the .tcm almost certainly describes a different
        # model, so tell the UI it can offer to open that one instead
        payload["tcm_unmatched"] = not matched
        return jsonify(payload)

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
        # An auto-layout throws the source coordinates away, so a camera saved
        # against them no longer points anywhere — drop it rather than aim it
        # at the wrong part of a freshly invented layout.
        transform = (1.0, 0.0, 0.0)
        if not auto:
            transform = normalize_transform(positions)
            positions = normalize_positions(positions)
        WORKSPACE.load(model, positions, path=loaded["path"], auto=auto,
                       warnings=loaded["warnings"],
                       view_prefs=None if auto else
                       _view_prefs(loaded.get("view"), transform))
        # A run describes the model it solved. Keeping the old ones would drive
        # the time slider with another model's dates and chart nodes this one
        # does not have, so the store goes with the model.
        RUNS.clear()
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
        RUNS.clear()
    return jsonify(WORKSPACE.graph_payload())


@bp.post("/api/close")
def close_model():
    """Back to the empty state, so a .tcm (or anything else) can be opened as
    a model in its own right again."""
    with WORKSPACE.lock:
        WORKSPACE.reset()
        RUNS.clear()
    return jsonify({"ok": True})


@bp.get("/api/path/exists")
def path_exists():
    """Does this file already exist? Save As asks before it overwrites."""
    path = (request.args.get("path") or "").strip()
    path = os.path.abspath(os.path.expanduser(path)) if path else ""
    if path and not path.lower().endswith(".json"):
        path += ".json"
    return jsonify({"ok": True, "path": path,
                    "exists": bool(path) and os.path.isfile(path)})


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
        WORKSPACE.push_undo("JSON edit")
        WORKSPACE.model = model
        WORKSPACE.positions = normalize_positions(positions)
        WORKSPACE.dirty = True
        WORKSPACE.warnings = notes
        WORKSPACE.resolve_data()
    return jsonify(WORKSPACE.graph_payload())


def _rehome_data(old_path, new_path):
    """Keep the model's data files findable after Save As.

    Data files are resolved *relative to the model's folder*, so writing the
    model somewhere else silently changes where they are looked for. Left
    alone, the Model tab would go on reporting files it had located against the
    folder the model no longer lives in — and the saved copy would open with
    them missing.

    Re-resolves against the new home, and when that loses a file, adds the old
    folder to the search path so the session keeps working. Returns a note to
    show the user, or None when nothing moved out from under the model."""
    before = {r["basename"]: r["resolved"]
              for r in (WORKSPACE.data or {}).get("report", [])}
    WORKSPACE.resolve_data()
    if not before:
        return None
    lost = [b for b in (WORKSPACE.data or {}).get("missing", []) if before.get(b)]
    if not lost:
        return None
    old_dir = os.path.dirname(os.path.abspath(old_path)) if old_path else None
    new_dir = os.path.dirname(os.path.abspath(new_path))
    if old_dir and old_dir != new_dir and old_dir not in WORKSPACE.data_dirs:
        WORKSPACE.data_dirs.append(old_dir)
        WORKSPACE.resolve_data()
    shown = ", ".join(lost[:3]) + (f" and {len(lost) - 3} more"
                                   if len(lost) > 3 else "")
    one = len(lost) == 1
    does = "does not" if one else "do not"
    them, they = ("it", "it is") if one else ("them", "they are")
    still_missing = [b for b in (WORKSPACE.data or {}).get("missing", [])
                     if before.get(b)]
    if still_missing:
        return (f"{shown} {does} sit beside the saved copy and could not be "
                f"found from it — the copy will not run until {they} added.")
    return (f"{shown} {does} sit beside the saved copy — still being read from "
            f"{old_dir}. Copy {them} next to the model to make it portable.")


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
            old_path = WORKSPACE.path
            model_io.save_pywr_json(WORKSPACE.model, WORKSPACE.positions, path)
            WORKSPACE.path, WORKSPACE.dirty = path, False
            note = (None if os.path.abspath(old_path or "") == os.path.abspath(path)
                    else _rehome_data(old_path, path))
        return jsonify({"ok": True, "path": path, "data_note": note,
                        "data": WORKSPACE.data_payload()})
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
