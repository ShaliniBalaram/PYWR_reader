"""Load and save PyWR models from multiple sources.

Supported inputs:
  - PyWR model JSON (the native format, as produced by pywr, pywr-editor,
    Graph Overlay PyWR, etc.)
  - .tcm view files (gzipped JSON produced by the PyWR TCM viewer): hold node
    positions keyed by node name, a pointer to the source model JSON, and the
    viewer's own presentation state — per-category colours and radii, label
    toggles, virtual/aggregated filters and the saved camera.
  - CSV pairs (nodes.csv + nodes_edges.csv) in the Graph Overlay format.

The in-memory representation keeps the *full* pywr model dict untouched
(metadata, timestepper, parameters, tables, recorders, scenarios, includes)
and overlays a positions dict {node_name: [x, y]} so nothing is lost on save.
"""

import csv
import gzip
import io
import json
import os

# --------------------------------------------------------------------------
# Position extraction / injection
# --------------------------------------------------------------------------

POSITION_KEYS = ("schematic", "editor_position", "geographic")


def extract_positions(model):
    """Read {name: [x, y]} from a pywr model dict's node position fields."""
    positions = {}
    for node in model.get("nodes", []):
        pos = node.get("position") or {}
        for key in POSITION_KEYS:
            xy = pos.get(key)
            if (isinstance(xy, (list, tuple)) and len(xy) >= 2
                    and all(isinstance(v, (int, float)) for v in xy[:2])):
                positions[node["name"]] = [float(xy[0]), float(xy[1])]
                break
    return positions


def positions_are_degenerate(positions, node_count):
    """True when positions are missing or useless — all stacked on one spot,
    or a large share parked on one default coordinate (pywr-editor and other
    tools drop unplaced nodes at e.g. [1000, 1000])."""
    if not positions or len(positions) < max(2, node_count // 2):
        return True
    counts = {}
    for xy in positions.values():
        key = (round(xy[0], 1), round(xy[1], 1))
        counts[key] = counts.get(key, 0) + 1
    if len(counts) <= max(1, len(positions) // 10):
        return True
    return max(counts.values()) >= max(3, len(positions) * 0.25)


def inject_positions(model, positions):
    """Write positions back into each node as position.schematic (in place)."""
    for node in model.get("nodes", []):
        xy = positions.get(node["name"])
        if xy is None:
            continue
        pos = node.setdefault("position", {})
        pos["schematic"] = [round(float(xy[0]), 3), round(float(xy[1]), 3)]
    return model


# --------------------------------------------------------------------------
# Loaders
# --------------------------------------------------------------------------

def load_pywr_json(path):
    with open(path, encoding="utf-8-sig") as fh:
        model = json.load(fh)
    if "nodes" not in model:
        raise ValueError(f"{os.path.basename(path)} has no 'nodes' key — "
                         "not a pywr model file")
    return model


# .tcm style sheets name five node categories; the label toggles name eight.
# Categories with no entry in a style sheet fall back along this chain.
_STYLE_FALLBACK = {"Gauge": "Link", "Aggregated": "Other", "Virtual": "Other"}

_LABEL_FLAGS = {
    "Storage": "show_storage_labels", "Input": "show_input_labels",
    "Link": "show_link_labels", "Output": "show_output_labels",
    "Gauge": "show_gauge_labels", "Aggregated": "show_aggregated_labels",
    "Virtual": "show_virtual_labels", "Other": "show_other_labels",
}


def _rgba_hex(value):
    """[r, g, b, a] (0-255) -> ("#rrggbb", alpha 0-1). None if unusable."""
    if not isinstance(value, (list, tuple)) or len(value) < 3:
        return None
    try:
        r, g, b = (max(0, min(255, int(round(float(c))))) for c in value[:3])
    except (TypeError, ValueError):
        return None
    alpha = 1.0
    if len(value) > 3:
        try:
            alpha = max(0.0, min(1.0, float(value[3]) / 255.0))
        except (TypeError, ValueError):
            alpha = 1.0
    return f"#{r:02x}{g:02x}{b:02x}", alpha


def _tcm_view(core, components):
    """The viewer's presentation state, normalised for the frontend.

    Everything here is optional — .tcm files in the wild omit sections — so
    each block is read defensively and left out entirely when absent.
    """
    view = {}

    sheet = (components.get("style_sheet") or {}).get("nodes") or {}
    styles = {}
    for category, spec in sheet.items():
        if not isinstance(spec, dict):
            continue
        entry = {}
        fill = _rgba_hex(spec.get("color"))
        if fill:
            entry["color"], entry["opacity"] = fill
        stroke = _rgba_hex(spec.get("edge_color"))
        if stroke:
            entry["edge_color"] = stroke[0]
        try:
            radius = float(spec.get("radius"))
            if radius > 0:
                entry["radius"] = radius
        except (TypeError, ValueError):
            pass
        if entry:
            styles[str(category)] = entry
    for category, fallback in _STYLE_FALLBACK.items():
        if category not in styles and fallback in styles:
            styles[category] = dict(styles[fallback])
    if styles:
        view["styles"] = styles

    settings = core.get("settings") or {}
    nodes_cfg = settings.get("nodes") or {}
    if "show_virtual" in nodes_cfg:
        view["show_virtual"] = bool(nodes_cfg.get("show_virtual"))
    if "show_aggregated" in nodes_cfg:
        view["show_aggregated"] = bool(nodes_cfg.get("show_aggregated"))

    labels_cfg = nodes_cfg.get("labels") or {}
    if labels_cfg:
        labels = {}
        # show_all_labels is the viewer's master switch; the per-category flags
        # only bite once it is on, so carry both rather than collapsing them.
        if "show_all_labels" in labels_cfg:
            labels["all"] = bool(labels_cfg.get("show_all_labels"))
        per_category = {}
        for category, flag in _LABEL_FLAGS.items():
            if flag in labels_cfg:
                per_category[category] = bool(labels_cfg.get(flag))
        if per_category:
            labels["categories"] = per_category
        try:
            size = float(labels_cfg.get("text_size"))
            if size > 0:
                labels["text_size"] = size
        except (TypeError, ValueError):
            pass
        if labels:
            view["labels"] = labels

    # The camera is recorded in the viewer's *display* space — the same space
    # the transformed node positions land in — as the world point under the
    # window's top-left corner plus pixels-per-world-unit.
    port = core.get("view_port") or {}
    origin = port.get("origin") or {}
    try:
        scale = float(port.get("scale"))
        if scale > 0 and "x" in origin and "y" in origin:
            view["viewport"] = {"origin": [float(origin["x"]), float(origin["y"])],
                                "scale": scale}
    except (TypeError, ValueError):
        pass

    return view


def load_tcm(path):
    """Parse a .tcm viewer file.

    Returns (positions, source_model_path, transforms, view). source_model_path
    is the path recorded inside the file (often from another machine — the
    caller should also try basename matches near the .tcm itself). view is the
    viewer's presentation state (see _tcm_view); it is {} when the file carries
    none of it.
    """
    with open(path, "rb") as fh:
        raw = fh.read()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    data = json.loads(raw.decode("utf-8"))

    core = data.get("core", {})
    components = core.get("components", {})
    node_meta = components.get("node_meta", {})
    transforms = components.get("coord_transformations", {}) or {}

    fx = float(transforms.get("x_factor") or 1.0)
    fy = float(transforms.get("y_factor") or 1.0)
    ox = float(transforms.get("x_offset") or 0.0)
    oy = float(transforms.get("y_offset") or 0.0)
    sx = -1.0 if transforms.get("invert_x_coords") else 1.0
    sy = -1.0 if transforms.get("invert_y_coords") else 1.0

    positions = {}
    for name, meta in node_meta.items():
        pos = (meta or {}).get("position") or {}
        user = pos.get("User") or pos.get("user")
        if isinstance(user, dict) and "x" in user and "y" in user:
            positions[name] = [sx * (float(user["x"]) * fx + ox),
                               sy * (float(user["y"]) * fy + oy)]

    source_path = None
    source = core.get("source", {})
    for version in source.values():
        if isinstance(version, dict) and version.get("Path"):
            source_path = str(version["Path"])
            break

    return positions, source_path, transforms, _tcm_view(core, components)


def find_tcm_source_model(tcm_path, source_path):
    """Locate the model JSON a .tcm refers to, trying local candidates."""
    candidates = []
    if source_path:
        candidates.append(source_path)
        base = os.path.basename(source_path.replace("\\", "/"))
        tcm_dir = os.path.dirname(os.path.abspath(tcm_path))
        candidates.append(os.path.join(tcm_dir, base))
        candidates.append(os.path.join(os.path.dirname(tcm_dir), base))
    # any single model-looking json sitting next to the tcm
    tcm_dir = os.path.dirname(os.path.abspath(tcm_path))
    try:
        siblings = [os.path.join(tcm_dir, f) for f in os.listdir(tcm_dir)
                    if f.lower().endswith(".json")]
    except OSError:
        siblings = []
    candidates.extend(siblings)

    for cand in candidates:
        if not cand or not os.path.isfile(cand):
            continue
        try:
            model = load_pywr_json(cand)
            return cand, model
        except (ValueError, json.JSONDecodeError, OSError):
            continue
    return None, None


def load_csv_pair(nodes_csv_path):
    """Import Graph Overlay nodes.csv (+ sibling nodes_edges.csv) as a model."""
    def read_rows(path):
        with open(path, encoding="utf-8-sig", newline="") as fh:
            return list(csv.DictReader(fh))

    rows = read_rows(nodes_csv_path)
    if not rows or "name" not in rows[0]:
        raise ValueError("nodes CSV needs at least 'name' column")

    reserved = {"id", "name", "type", "col", "row", "px", "py", "x", "y"}
    nodes, positions = [], {}
    for row in rows:
        name = (row.get("name") or "").strip()
        if not name:
            continue
        node = {"name": name, "type": (row.get("type") or "link").strip() or "link"}
        for key, val in row.items():
            # csv.DictReader files rows' surplus cells under a None key
            if not isinstance(key, str) or key in reserved:
                continue
            if val is None or not isinstance(val, str) or val.strip() == "":
                continue
            sval = str(val).strip()
            try:
                node[key] = (float(sval) if "." in sval or "e" in sval.lower()
                             else int(sval))
            except ValueError:
                node[key] = sval
        nodes.append(node)
        for xk, yk in (("col", "row"), ("px", "py"), ("x", "y")):
            try:
                positions[name] = [float(row[xk]), float(row[yk])]
                break
            except (KeyError, TypeError, ValueError):
                continue

    edges = []
    base, _ = os.path.splitext(nodes_csv_path)
    for cand in (base + "_edges.csv",
                 os.path.join(os.path.dirname(nodes_csv_path), "nodes_edges.csv"),
                 base.replace("nodes", "edges") + ".csv"):
        if os.path.isfile(cand) and cand != nodes_csv_path:
            for row in read_rows(cand):
                src = (row.get("src") or "").strip()
                dst = (row.get("dst") or "").strip()
                if src and dst:
                    edges.append([src, dst])
            break

    model = {
        "metadata": {"title": os.path.basename(base),
                     "description": "Imported by PyWR Reader from CSV",
                     "minimum_version": "0.1"},
        "timestepper": {"start": "2020-01-01", "end": "2020-12-31", "timestep": 1},
        "nodes": nodes,
        "edges": edges,
    }
    inject_positions(model, positions)
    return model


def load_any(path):
    """Load any supported file. Returns a dict:
    {model, positions, path, source (str tag), warnings (list)}"""
    ext = os.path.splitext(path)[1].lower()
    warnings = []

    if ext == ".tcm":
        positions, source_path, _, tcm_view = load_tcm(path)
        model_path, model = find_tcm_source_model(path, source_path)
        if model is None:
            raise ValueError(
                "This .tcm view file references a model JSON that could not be "
                f"found locally (recorded path: {source_path!r}). Open the model "
                "JSON first, then apply the .tcm positions onto it.")
        native = extract_positions(model)
        merged = dict(native)
        matched = 0
        for name, xy in positions.items():
            if name in {n["name"] for n in model.get("nodes", [])}:
                merged[name] = xy
                matched += 1
        if matched == 0:
            warnings.append(".tcm node names did not match the model — "
                            "using the model's own positions")
        else:
            warnings.append(f".tcm positions applied to {matched} nodes "
                            f"(model: {os.path.basename(model_path)})")
        return {"model": model, "positions": merged, "path": model_path,
                "source": "tcm", "warnings": warnings,
                "view": tcm_view if matched else {}}

    if ext == ".csv":
        model = load_csv_pair(path)
        return {"model": model, "positions": extract_positions(model),
                "path": path, "source": "csv", "warnings": warnings}

    # default: pywr JSON
    model = load_pywr_json(path)
    return {"model": model, "positions": extract_positions(model),
            "path": path, "source": "pywr-json", "warnings": warnings}


# --------------------------------------------------------------------------
# Saving
# --------------------------------------------------------------------------

def save_pywr_json(model, positions, path):
    inject_positions(model, positions)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(model, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, path)
    return path


def export_csv_pair(model, positions, directory, stem="nodes"):
    """Write Graph-Overlay-compatible nodes.csv / nodes_edges.csv."""
    os.makedirs(directory, exist_ok=True)
    nodes_path = os.path.join(directory, f"{stem}.csv")
    edges_path = os.path.join(directory, f"{stem}_edges.csv")

    param_keys = []
    for node in model.get("nodes", []):
        for key, val in node.items():
            if (key in ("name", "type", "position")
                    or not isinstance(val, (int, float, str))):
                continue
            if key not in param_keys:
                param_keys.append(key)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["id", "name", "type", "col", "row"] + param_keys)
    for i, node in enumerate(model.get("nodes", []), start=1):
        xy = positions.get(node["name"], ["", ""])
        writer.writerow([i, node["name"], node.get("type", "link"), xy[0], xy[1]]
                        + [node.get(k, "") for k in param_keys])
    with open(nodes_path, "w", encoding="utf-8", newline="") as fh:
        fh.write(buf.getvalue())

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["id", "name", "src", "dst"])
    for i, edge in enumerate(model.get("edges", []), start=1):
        writer.writerow([i, f"E{i}", edge[0], edge[1]])
    with open(edges_path, "w", encoding="utf-8", newline="") as fh:
        fh.write(buf.getvalue())

    return nodes_path, edges_path
