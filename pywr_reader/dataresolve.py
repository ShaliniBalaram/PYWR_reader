"""Resolve the external data files a pywr model references.

Real pywr models point `tables`/`parameters`/`recorders` at data files by
`url` — very often an *absolute path from another machine*
(`C:\\Data\\...\\inflows.xlsx`) that does not exist here. This module finds a
local file with the same basename, searching the model's folder and any
user-supplied data directories, so the model can actually run.
"""

import os

# directories never worth walking into during a data-file hunt
SKIP_DIRS = {".git", ".hg", ".svn", "node_modules", "__pycache__",
             ".venv", "venv", ".pywr-env", ".micromamba", ".uv-bootstrap",
             ".idea", ".vscode", "site-packages"}
MAX_DIRS = 6000          # bound the walk so a huge tree can't hang the app
DATA_EXTS = (".csv", ".xlsx", ".xls", ".h5", ".hdf5", ".hdf", ".nc", ".txt")


def _basename(url):
    """Last path component, handling both / and \\ separators."""
    return os.path.basename(str(url).replace("\\", "/").rstrip("/"))


def iter_url_containers(model):
    """Yield every dict inside the model that carries a 'url' string
    (tables, parameters, recorders, and anything nested)."""
    def walk(obj):
        if isinstance(obj, dict):
            if isinstance(obj.get("url"), str):
                yield obj
            for val in obj.values():
                yield from walk(val)
        elif isinstance(obj, list):
            for item in obj:
                yield from walk(item)

    for section in ("tables", "parameters", "recorders", "nodes"):
        yield from walk(model.get(section))


def referenced_files(model):
    """Unique {basename: {'urls': set, 'refs': int}} the model needs."""
    needed = {}
    for container in iter_url_containers(model):
        url = container["url"]
        base = _basename(url)
        entry = needed.setdefault(base, {"urls": set(), "refs": 0})
        entry["urls"].add(url)
        entry["refs"] += 1
    return needed


def _index_dir(root):
    """basename(lower) -> full path, for files under root (bounded walk)."""
    index = {}
    dirs_seen = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in SKIP_DIRS and not d.startswith(".")]
        dirs_seen += 1
        if dirs_seen > MAX_DIRS:
            break
        for fn in filenames:
            if fn.startswith("._"):
                continue
            if os.path.splitext(fn)[1].lower() in DATA_EXTS:
                index.setdefault(fn.lower(), os.path.join(dirpath, fn))
    return index


def resolve(model, model_path, extra_dirs=None):
    """Locate each referenced data file.

    Returns {'map': {original_url: local_path}, 'report': [...], 'missing':
    [basenames]}. `report` items: {basename, urls, refs, resolved, source}.
    """
    needed = referenced_files(model)
    model_dir = os.path.dirname(os.path.abspath(model_path)) if model_path else None

    # search roots, nearest first: model dir, parent, grandparent, user dirs.
    # (models often sit in a per-zone subfolder while data lives in a sibling
    # tree a couple of levels up — so we climb two levels by default.)
    roots = []
    if model_dir:
        roots.append(model_dir)
        climb = model_dir
        for _ in range(2):
            parent = os.path.dirname(climb)
            if parent and parent != climb:
                roots.append(parent)
                climb = parent
    for d in (extra_dirs or []):
        if d and os.path.isdir(d) and d not in roots:
            roots.append(d)

    # lazily index roots only until everything is found
    indexes = {}
    url_map, report, missing = {}, [], []

    for base, info in sorted(needed.items()):
        resolved, source = None, None

        # 1. any original url that happens to exist verbatim
        for url in info["urls"]:
            if os.path.isfile(url):
                resolved, source = url, "original path"
                break

        # 2. basename directly in the model folder
        if resolved is None and model_dir:
            cand = os.path.join(model_dir, base)
            if os.path.isfile(cand):
                resolved, source = cand, "model folder"

        # 3. basename anywhere under a search root
        if resolved is None:
            for root in roots:
                if root not in indexes:
                    indexes[root] = _index_dir(root)
                hit = indexes[root].get(base.lower())
                if hit:
                    resolved, source = hit, root
                    break

        for url in info["urls"]:
            if resolved:
                url_map[url] = resolved
        if resolved is None:
            missing.append(base)
        report.append({
            "basename": base,
            "urls": sorted(info["urls"]),
            "refs": info["refs"],
            "resolved": resolved,
            "source": source,
        })
    return {"map": url_map, "report": report, "missing": missing}


def apply_map(model, url_map):
    """Rewrite every url in the model to its resolved local path (in place)."""
    changed = 0
    for container in iter_url_containers(model):
        new = url_map.get(container["url"])
        if new and new != container["url"]:
            container["url"] = new
            changed += 1
    return changed
