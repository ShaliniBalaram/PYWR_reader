"""Graph queries and edit operations on a pywr model dict."""

import json
from collections import defaultdict, deque


def build_adjacency(model):
    out_adj, in_adj = defaultdict(list), defaultdict(list)
    for edge in model.get("edges", []):
        if len(edge) >= 2:
            out_adj[edge[0]].append(edge[1])
            in_adj[edge[1]].append(edge[0])
    return out_adj, in_adj


def trace(model, name, direction):
    """BFS from a node. direction: 'upstream' | 'downstream'.
    Returns (node_names_set, edge_index_set)."""
    out_adj, in_adj = build_adjacency(model)
    adj = in_adj if direction == "upstream" else out_adj
    edges = model.get("edges", [])
    edge_lookup = defaultdict(list)
    for i, edge in enumerate(edges):
        if len(edge) >= 2:
            key = (edge[1], edge[0]) if direction == "upstream" else (edge[0], edge[1])
            edge_lookup[key[0]].append((i, key[1]))

    seen, hit_edges = {name}, set()
    queue = deque([name])
    while queue:
        cur = queue.popleft()
        for edge_idx, nxt in edge_lookup[cur]:
            hit_edges.add(edge_idx)
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
        for nxt in adj[cur]:
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return seen, hit_edges


REF_KEYS = ("node", "nodes", "storage_node", "storage_nodes",
            "first_node", "second_node")


def node_affinity(model):
    """{node_name: [referenced node names]} for nodes that point at other
    nodes (virtual storages, aggregated nodes) — used to place edge-less
    nodes near what they monitor."""
    names = {n.get("name") for n in model.get("nodes", [])}
    affinity = {}
    for node in model.get("nodes", []):
        refs = []
        for key in REF_KEYS:
            val = node.get(key)
            if isinstance(val, str) and val in names:
                refs.append(val)
            elif isinstance(val, list):
                refs.extend(v for v in val if isinstance(v, str) and v in names)
        if refs:
            affinity[node["name"]] = refs
    return affinity


def node_by_name(model, name):
    for node in model.get("nodes", []):
        if node.get("name") == name:
            return node
    return None


def add_node(model, node_def):
    name = (node_def.get("name") or "").strip()
    if not name:
        raise ValueError("node needs a name")
    if node_by_name(model, name) is not None:
        raise ValueError(f"a node named {name!r} already exists")
    node_def.setdefault("type", "link")
    model.setdefault("nodes", []).append(node_def)
    return node_def


def rename_node(model, old, new):
    new = (new or "").strip()
    if not new:
        raise ValueError("new name is empty")
    if old == new:
        return []
    if node_by_name(model, new) is not None:
        raise ValueError(f"a node named {new!r} already exists")
    node = node_by_name(model, old)
    if node is None:
        raise ValueError(f"no node named {old!r}")
    node["name"] = new
    return rewrite_node_refs(model, old, new)


def rewrite_node_refs(model, old, new):
    """Point every reference to node `old` at `new` — edges, aggregated node
    lists, parameters and recorders — without touching any node's own name.

    rename_node() calls this after renaming the node itself. The JSON editor
    calls it when the rename has *already* happened in hand-edited JSON, so
    only the references are left dangling. Returns human-readable notes."""
    notes = []
    for i, edge in enumerate(model.get("edges", [])):
        for j, endpoint in enumerate(edge):
            if endpoint == old:
                edge[j] = new
                notes.append(f"updated reference at edges[{i}]")
    # references elsewhere (aggregated nodes, parameters, recorders)
    notes.extend(_rewrite_references(model, old, new))
    return notes


def _rewrite_references(model, old, new):
    """Rewrite exact-string references to a node name in nodes/parameters/
    recorders sections. Returns a list of human-readable notes."""
    notes = []

    def rewrite(obj, path):
        if isinstance(obj, dict):
            for key, val in obj.items():
                if isinstance(val, str) and val == old and key in (
                        "node", "storage_node", "storage", "first_node", "second_node"):
                    obj[key] = new
                    notes.append(f"updated reference at {path}.{key}")
                elif key in ("nodes", "storage_nodes") and isinstance(val, list):
                    for i, item in enumerate(val):
                        if item == old:
                            val[i] = new
                            notes.append(f"updated reference at {path}.{key}[{i}]")
                else:
                    rewrite(val, f"{path}.{key}")
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                rewrite(item, f"{path}[{i}]")

    for section in ("nodes", "parameters", "recorders"):
        rewrite(model.get(section), section)
    return notes


# ---------------------------------------------------------------------------
# Parameters, recorders and tables are wired together by name, the same way
# nodes are — a node's "max_flow" holds the name of a parameter, an Aggregated
# parameter lists the names of others, a RecorderThresholdParameter names a
# recorder. Renaming one therefore has to carry its references, and deleting
# one has to say what it leaves dangling.
# ---------------------------------------------------------------------------

# Keys whose string value is never the name of another definition: free text,
# a pywr class name, or a coordinate into a data file. Rewriting a name found
# at one of these would corrupt the model rather than repair it.
LITERAL_KEYS = frozenset((
    "name", "type", "comment", "url", "table", "column", "index", "index_col",
    "key", "agg_func", "predicate", "scenario", "ensemble_names", "position",
    "interp_day", "kind", "units", "unit", "where",
))

NODE_REF_KEYS = frozenset((
    "node", "nodes", "storage_node", "storage_nodes", "storage",
    "first_node", "second_node",
))
RECORDER_REF_KEYS = frozenset(("recorder", "recorders"))

DEFINITION_SECTIONS = ("parameters", "recorders", "tables")


def _name_is_ambiguous(model, section, name):
    """True when the other definition block defines the same name, so a bare
    reference to it could mean either. pywr keeps parameters and recorders in
    separate namespaces, so this is legal — just not something a rewrite can
    resolve on its own."""
    other = "recorders" if section == "parameters" else "parameters"
    if section == "tables":
        return False
    return name in (model.get(other) or {})


def _is_ref(key, section, ambiguous):
    """Does a string sitting at `key` name an entry of `section`?"""
    if section == "tables":
        return key == "table"
    if key in LITERAL_KEYS or key in NODE_REF_KEYS:
        return False
    if key in RECORDER_REF_KEYS:
        return section == "recorders"
    # Any other key holding a bare name is a parameter reference in pywr — a
    # node attribute (max_flow, cost, loss_factor), or a parameter's own
    # operands (parameters, control_curves, threshold). Recorders are named at
    # some of those keys too (EventRecorder's threshold), so the key alone
    # cannot separate them; the name can, as long as only one block defines it.
    return not ambiguous


def _visit_refs(model, section, name, new=None):
    """Every place `name` is referenced as an entry of `section`, as readable
    paths. Passing `new` rewrites them in place as it goes."""
    hits = []

    def visit(container, path, inherited_key):
        if isinstance(container, dict):
            for key, val in container.items():
                where = f"{path}.{key}"
                if isinstance(val, str):
                    if val == name and _is_ref(key, section, ambiguous):
                        hits.append(where)
                        if new is not None:
                            container[key] = new
                else:
                    visit(val, where, key)
        elif isinstance(container, list):
            for i, val in enumerate(container):
                where = f"{path}[{i}]"
                if isinstance(val, str):
                    # a list inherits the key it hangs off: {"parameters": [...]}
                    if val == name and _is_ref(inherited_key, section, ambiguous):
                        hits.append(where)
                        if new is not None:
                            container[i] = new
                else:
                    visit(val, where, inherited_key)

    ambiguous = _name_is_ambiguous(model, section, name)
    for block in ("nodes", "parameters", "recorders"):
        visit(model.get(block), block, None)
    return hits


def find_definition_refs(model, section, name):
    """Where a parameter / recorder / table is referenced from."""
    return _visit_refs(model, section, name)


def rewrite_definition_refs(model, section, old, new):
    """Point every reference to `old` at `new`, without touching the entry's
    own key. Returns human-readable notes."""
    notes = [f"updated reference at {path}"
             for path in _visit_refs(model, section, old, new=new)]
    if _name_is_ambiguous(model, section, old):
        notes.append(
            f"{old!r} names both a parameter and a recorder — only the "
            "references that say which one they mean were rewritten; check "
            "the rest by hand.")
    return notes


def rename_definition(model, section, old, new):
    """Rename a parameter / recorder / table and carry its references.
    Key order is preserved, so a renamed entry stays where it was in the
    file."""
    if section not in DEFINITION_SECTIONS:
        raise ValueError(f"cannot rename in {section!r}")
    singular = section[:-1]
    block = model.get(section) or {}
    new = (new or "").strip()
    if not new:
        raise ValueError("new name is empty")
    if old not in block:
        raise ValueError(f"no {singular} named {old!r}")
    if old == new:
        return []
    if new in block:
        raise ValueError(f"a {singular} named {new!r} already exists")
    model[section] = {(new if k == old else k): v for k, v in block.items()}
    return rewrite_definition_refs(model, section, old, new)


def delete_definition(model, section, name):
    """Remove a parameter / recorder / table. Returns warnings naming what
    still points at it — deleting something three parameters depend on is
    allowed, but you should be told."""
    if section not in DEFINITION_SECTIONS:
        raise ValueError(f"cannot delete from {section!r}")
    block = model.get(section) or {}
    if name not in block:
        raise ValueError(f"no {section[:-1]} named {name!r}")
    refs = find_definition_refs(model, section, name)
    del block[name]
    model[section] = block
    if not refs:
        return []
    shown = ", ".join(refs[:4])
    if len(refs) > 4:
        shown += f" and {len(refs) - 4} more"
    return [f"{name!r} was still referenced by {len(refs)} "
            f"{'place' if len(refs) == 1 else 'places'} ({shown}) — those now "
            "point at a name the model does not define."]


# Keys that certainly hold the name of something defined elsewhere. The
# dangling check uses this narrow list rather than the broad rule above: a
# false "undefined" warning on every run of a valid model would be worse than
# missing one, and unknown keys holding enum-ish strings are common.
CERTAIN_REF_KEYS = frozenset((
    "parameter", "parameters", "control_curve", "control_curves",
    "index_parameter", "index_parameters", "recorder", "recorders", "table",
)) | NODE_REF_KEYS


def dangling_references(model):
    """Names the model refers to but defines nowhere — a typo, or something
    deleted while things still pointed at it. Reported as warnings, never
    errors: a half-finished edit legitimately has them, and pywr is the final
    judge of what a given node type accepts.

    A name counts as defined if any block defines it. Separating the
    namespaces here would turn every parameter/recorder name collision into a
    false alarm, and the failure actually worth catching is a name that exists
    nowhere at all."""
    known = {n.get("name") for n in model.get("nodes", []) if isinstance(n, dict)}
    for section in DEFINITION_SECTIONS:
        known |= set(model.get(section) or {})
    known.discard(None)
    out = []

    def check(val, where):
        if isinstance(val, str) and val not in known:
            out.append(f"{where} references {val!r}, which the model does not define")
        elif isinstance(val, list):
            for i, item in enumerate(val):
                check(item, f"{where}[{i}]")

    def visit(container, path, on_node):
        if isinstance(container, dict):
            for key, val in container.items():
                where = f"{path}.{key}"
                if key in CERTAIN_REF_KEYS:
                    check(val, where)
                elif on_node and isinstance(val, str) and key not in LITERAL_KEYS:
                    # a node attribute holding a bare string is a parameter name
                    check(val, where)
                elif not isinstance(val, str):
                    visit(val, where, False)
        elif isinstance(container, list):
            for i, item in enumerate(container):
                visit(item, f"{path}[{i}]", on_node)

    for i, node in enumerate(model.get("nodes", [])):
        visit(node, f"nodes[{i}]", True)
    for section in ("parameters", "recorders"):
        for name, definition in (model.get(section) or {}).items():
            visit(definition, f"{section}.{name}", False)
    return out


def delete_node(model, name):
    """Remove a node and all its edges. Returns warnings about leftover
    references elsewhere in the model (aggregated nodes, recorders, ...)."""
    nodes = model.get("nodes", [])
    before = len(nodes)
    model["nodes"] = [n for n in nodes if n.get("name") != name]
    if len(model["nodes"]) == before:
        raise ValueError(f"no node named {name!r}")
    model["edges"] = [e for e in model.get("edges", [])
                      if name not in e[:2]]

    warnings = []
    blob = json.dumps({k: v for k, v in model.items() if k != "edges"})
    if f'"{name}"' in blob:
        warnings.append(
            f"{name!r} is still referenced elsewhere in the model "
            "(aggregated node, parameter or recorder) — the model may not run "
            "until those references are removed.")
    return warnings


def add_edge(model, src, dst):
    if src == dst:
        raise ValueError("cannot connect a node to itself")
    names = {n.get("name") for n in model.get("nodes", [])}
    for endpoint in (src, dst):
        if endpoint not in names:
            raise ValueError(f"no node named {endpoint!r}")
    for edge in model.get("edges", []):
        if edge[0] == src and edge[1] == dst:
            raise ValueError("edge already exists")
    model.setdefault("edges", []).append([src, dst])


def delete_edge(model, src, dst):
    edges = model.get("edges", [])
    before = len(edges)
    model["edges"] = [e for e in edges if not (e[0] == src and e[1] == dst)]
    if len(model["edges"]) == before:
        raise ValueError(f"no edge {src!r} → {dst!r}")


def update_node(model, name, changes, removals=None):
    """Apply {key: value} changes to a node dict; removals is a list of keys
    to delete. 'name' and 'position' are protected here."""
    node = node_by_name(model, name)
    if node is None:
        raise ValueError(f"no node named {name!r}")
    for key in (removals or []):
        if key not in ("name", "type", "position"):
            node.pop(key, None)
    for key, val in (changes or {}).items():
        if key in ("name", "position"):
            continue
        node[key] = val
    return node


def scenario_dims(model):
    """Per-scenario dimensions read straight from the model JSON, in pywr's
    combination order. Returns [{name, size, ensemble_names:[...]}].
    Ensemble names default to the string index when the model doesn't name
    them, and are padded/truncated to match 'size'."""
    dims = []
    for s in model.get("scenarios", []) or []:
        try:
            size = max(1, int(s.get("size", 1)))
        except (TypeError, ValueError):
            size = 1
        names = [str(x) for x in (s.get("ensemble_names") or [])][:size]
        if len(names) < size:
            names += [str(i) for i in range(len(names), size)]
        dims.append({"name": str(s.get("name") or f"scenario{len(dims)}"),
                     "size": size, "ensemble_names": names})
    return dims


def scenario_combinations(model):
    """Number of scenario combinations = product of the scenario sizes (the
    full cartesian product, which is pywr's default). 1 when the model
    defines no scenarios."""
    n = 1
    for d in scenario_dims(model):
        n *= d["size"]
    return n


def combo_label(dims, index):
    """Human label for a flat combination index, decoded in C-order — the
    last scenario varies fastest, matching pywr's ScenarioCollection. E.g.
    'demand=high' or 'climate=2, demand=low'. 'base' when there are no
    scenarios."""
    if not dims:
        return "base"
    coords, rem = [], int(index)
    for d in reversed(dims):
        coords.append(rem % d["size"])
        rem //= d["size"]
    coords.reverse()
    return ", ".join(f"{d['name']}={d['ensemble_names'][c]}"
                     for d, c in zip(dims, coords))


def graph_summary(model, positions):
    """Compact JSON-friendly description of the network for the frontend."""
    out_adj, in_adj = build_adjacency(model)
    nodes = []
    for node in model.get("nodes", []):
        name = node.get("name", "?")
        params = {k: v for k, v in node.items()
                  if k not in ("name", "type", "position")}
        nodes.append({
            "name": name,
            "type": str(node.get("type", "link")),
            "pos": positions.get(name),
            "in_degree": len(in_adj.get(name, [])),
            "out_degree": len(out_adj.get(name, [])),
            "params": params,
        })
    edges = [{"src": e[0], "dst": e[1], "extra": e[2:]}
             for e in model.get("edges", []) if len(e) >= 2]
    return {
        "metadata": model.get("metadata", {}),
        "timestepper": model.get("timestepper", {}),
        "nodes": nodes,
        "edges": edges,
        "n_parameters": len(model.get("parameters", {}) or {}),
        "n_recorders": len(model.get("recorders", {}) or {}),
        "n_tables": len(model.get("tables", {}) or {}),
        "scenarios": model.get("scenarios", []),
        "scenario_dims": scenario_dims(model),
        "n_combinations": scenario_combinations(model),
    }
