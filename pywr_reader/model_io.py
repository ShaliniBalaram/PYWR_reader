"""Load and save PyWR models from multiple sources.

Supported inputs:
  - PyWR model JSON (the native format, as produced by pywr, pywr-editor,
    Graph Overlay PyWR, etc.)
  - .tcm view files (gzipped JSON produced by the PyWR TCM viewer): hold node
    positions keyed by node name plus a pointer to the source model JSON.
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
    with open(path, "r", encoding="utf-8-sig") as fh:
        model = json.load(fh)
    if "nodes" not in model:
        raise ValueError(f"{os.path.basename(path)} has no 'nodes' key — "
                         "not a pywr model file")
    return model


def load_tcm(path):
    """Parse a .tcm viewer file.

    Returns (positions, source_model_path, transforms). source_model_path is
    the path recorded inside the file (often from another machine — the caller
    should also try basename matches near the .tcm itself).
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

    return positions, source_path, transforms


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
        with open(path, "r", encoding="utf-8-sig", newline="") as fh:
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
                node[key] = float(sval) if "." in sval or "e" in sval.lower() else int(sval)
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
                src, dst = (row.get("src") or "").strip(), (row.get("dst") or "").strip()
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
        positions, source_path, _ = load_tcm(path)
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
                "source": "tcm", "warnings": warnings}

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
            if key in ("name", "type", "position") or not isinstance(val, (int, float, str)):
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
