"""Automatic schematic layout for pywr networks (no external dependencies).

Water networks are mostly directed and acyclic (source → treatment → demand),
so a layered "Sugiyama-lite" layout usually reads best:

  1. break cycles (DFS back-edge removal, for layering only)
  2. assign layers by longest path from the sources
  3. order nodes within layers by repeated barycenter sweeps
  4. assign coordinates with fixed spacing; flow runs top → bottom

Disconnected components are laid out independently and placed side by side.

Layered is not always the most readable, though: a zone model with many
sources funnels ~40 nodes into one row and then trails off in a long thin
tail. So several layouts are offered (see LAYOUTS) and the user picks:

  layered  the Sugiyama-lite default above — follows the flow direction
  force    Fruchterman-Reingold spring embedding — untangles meshy networks
  grouped  blocks by functional group (source / river / storage / …)
  radial   rings by distance from the sources — compact for wide, shallow nets

These are the same algorithms networkx would provide, implemented here in the
standard library so the reader keeps working on a bare checkout (networkx's
spring_layout needs numpy; kamada_kawai needs scipy). All are deterministic —
the same model always lays out identically.
"""

import math
import re
from collections import defaultdict, deque

X_SPACING = 140.0
Y_SPACING = 110.0
COMPONENT_GAP = 220.0

# functional groups, mirroring the frontend's TYPE_STYLES (node colours)
GROUP_PATTERNS = [
    ("virtual", re.compile(r"virtual|aggregated", re.I)),
    ("storage", re.compile(r"reservoir|storage", re.I)),
    ("source", re.compile(r"catchment|input|discharge", re.I)),
    ("river", re.compile(r"river|gauge", re.I)),
    ("demand", re.compile(r"output|demand", re.I)),
    ("link", re.compile(r"link|delay|break|piecewise|split", re.I)),
]
# left-to-right reading order for the grouped layout: water's journey
GROUP_ORDER = ["source", "river", "storage", "link", "demand", "virtual",
               "other"]


def node_group(type_name):
    """Functional group for a pywr node type (matches the canvas colours)."""
    for name, pattern in GROUP_PATTERNS:
        if pattern.search(type_name or ""):
            return name
    return "other"


def _components(names, adj_undirected):
    seen, comps = set(), []
    for name in names:
        if name in seen:
            continue
        comp, queue = [], deque([name])
        seen.add(name)
        while queue:
            cur = queue.popleft()
            comp.append(cur)
            for nxt in adj_undirected[cur]:
                if nxt not in seen:
                    seen.add(nxt)
                    queue.append(nxt)
        comps.append(comp)
    return comps


def _break_cycles(nodes, out_adj):
    """Return acyclic out-adjacency (back edges dropped) via iterative DFS."""
    acyclic = defaultdict(list)
    state = {n: 0 for n in nodes}  # 0 unvisited, 1 on stack, 2 done
    for root in nodes:
        if state[root] != 0:
            continue
        stack = [(root, iter(out_adj[root]))]
        state[root] = 1
        while stack:
            cur, it = stack[-1]
            advanced = False
            for nxt in it:
                if nxt not in state:
                    continue
                if state[nxt] == 0:
                    acyclic[cur].append(nxt)
                    state[nxt] = 1
                    stack.append((nxt, iter(out_adj[nxt])))
                    advanced = True
                    break
                if state[nxt] == 2:
                    acyclic[cur].append(nxt)
                # state 1 → back edge → skip
            if not advanced:
                state[cur] = 2
                stack.pop()
    return acyclic


def _layers_longest_path(comp, acyclic):
    comp_set = set(comp)
    in_deg = {n: 0 for n in comp}
    for u in comp:
        for v in acyclic[u]:
            if v in comp_set:
                in_deg[v] += 1
    layer = {n: 0 for n in comp}
    queue = deque([n for n in comp if in_deg[n] == 0]) or deque(comp[:1])
    visited = set()
    while queue:
        u = queue.popleft()
        if u in visited:
            continue
        visited.add(u)
        for v in acyclic[u]:
            if v not in comp_set:
                continue
            layer[v] = max(layer[v], layer[u] + 1)
            in_deg[v] -= 1
            if in_deg[v] <= 0:
                queue.append(v)
    return layer


def _barycenter_order(comp, layer, out_adj, in_adj, sweeps=8):
    by_layer = defaultdict(list)
    for n in comp:
        by_layer[layer[n]].append(n)
    for nodes in by_layer.values():
        nodes.sort()
    order = {n: i for nodes in by_layer.values() for i, n in enumerate(nodes)}
    levels = sorted(by_layer)

    def sweep(level_seq, neighbors_of):
        for lev in level_seq:
            nodes = by_layer[lev]
            keyed = []
            for n in nodes:
                nbrs = [order[m] for m in neighbors_of(n) if m in order and layer.get(m) != lev]
                keyed.append((sum(nbrs) / len(nbrs) if nbrs else order[n], n))
            keyed.sort(key=lambda t: (t[0], t[1]))
            for i, (_, n) in enumerate(keyed):
                order[n] = i
            by_layer[lev] = [n for _, n in keyed]

    for _ in range(sweeps):
        sweep(levels, lambda n: in_adj[n])
        sweep(reversed(levels), lambda n: out_adj[n])
    return by_layer


def auto_layout(node_names, edges, affinity=None):
    """Compute positions for every node. Returns {name: [x, y]} (y grows down,
    flow direction top → bottom).

    affinity: optional {isolated_node: [related_names]} — nodes with no edges
    (virtual storages, aggregated monitors) are parked next to the nodes they
    reference instead of in the spare grid.
    """
    names = list(dict.fromkeys(node_names))
    name_set = set(names)
    out_adj, in_adj, und = defaultdict(list), defaultdict(list), defaultdict(set)
    for edge in edges:
        u, v = edge[0], edge[1]
        if u in name_set and v in name_set and u != v:
            out_adj[u].append(v)
            in_adj[v].append(u)
            und[u].add(v)
            und[v].add(u)

    isolated = [n for n in names if not und[n]]
    connected = [n for n in names if und[n]]

    acyclic = _break_cycles(names, out_adj)
    positions = {}
    x_cursor = 0.0
    for comp in sorted(_components(connected, und), key=len, reverse=True):
        layer = _layers_longest_path(comp, acyclic)
        by_layer = _barycenter_order(comp, layer, out_adj, in_adj)
        width = max(len(nodes) for nodes in by_layer.values())
        for lev, nodes in sorted(by_layer.items()):
            offset = (width - len(nodes)) / 2.0
            for i, n in enumerate(nodes):
                positions[n] = [x_cursor + (offset + i) * X_SPACING,
                                lev * Y_SPACING]
        x_cursor += width * X_SPACING + COMPONENT_GAP

    _place_satellites(positions, isolated, affinity)
    return positions


def _place_satellites(positions, isolated, affinity):
    """Park edge-less nodes (virtual storages, aggregated monitors) beside the
    nodes they reference; the rest go in a compact grid below everything.
    Mutates positions in place."""
    leftover = []
    slots_used = defaultdict(int)  # anchor name -> how many satellites so far
    for n in isolated:
        refs = [r for r in (affinity or {}).get(n, []) if r in positions]
        if refs:
            bx = sum(positions[r][0] for r in refs) / len(refs)
            by = sum(positions[r][1] for r in refs) / len(refs)
            k = slots_used[refs[0]]
            slots_used[refs[0]] += 1
            positions[n] = [bx + X_SPACING * 0.45 + (k % 2) * X_SPACING * 0.35,
                            by - Y_SPACING * 0.5 - (k // 2) * Y_SPACING * 0.45]
        else:
            leftover.append(n)

    if leftover:
        if positions:
            xs = [p[0] for p in positions.values()]
            ys = [p[1] for p in positions.values()]
            gx, gy = min(xs), max(ys) + 2 * Y_SPACING
        else:
            gx, gy = 0.0, 0.0
        cols = max(4, int(len(leftover) ** 0.5) + 1)
        for i, n in enumerate(leftover):
            positions[n] = [gx + (i % cols) * X_SPACING,
                            gy + (i // cols) * Y_SPACING]
    return positions


def _adjacency(node_names, edges):
    """names (de-duplicated) plus out/in/undirected adjacency, self-loops and
    edges to unknown nodes dropped."""
    names = list(dict.fromkeys(node_names))
    name_set = set(names)
    out_adj, in_adj, und = defaultdict(list), defaultdict(list), defaultdict(set)
    for edge in edges:
        if len(edge) < 2:
            continue
        u, v = edge[0], edge[1]
        if u in name_set and v in name_set and u != v:
            out_adj[u].append(v)
            in_adj[v].append(u)
            und[u].add(v)
            und[v].add(u)
    return names, out_adj, in_adj, und


def _depths(names, out_adj, und):
    """Longest-path depth from the sources, across every component."""
    acyclic = _break_cycles(names, out_adj)
    depth = {}
    for comp in _components(names, und):
        depth.update(_layers_longest_path(comp, acyclic))
    return depth


def force_layout(node_names, edges, affinity=None, iterations=None):
    """Fruchterman-Reingold spring embedding — the algorithm behind networkx's
    spring_layout, in the standard library and deterministic.

    Nodes repel one another (k²/d) while edges pull their endpoints together
    (d²/k), with the step size cooling linearly so the graph settles. It starts
    from the layered layout rather than a random cloud, which keeps the
    top → bottom flow direction recognisable and converges much faster; the
    springs then push apart the very wide source rows that make the layered
    view unreadable on zone models.

    Cost is O(n²) per iteration, so the iteration count tapers as the model
    grows (a few hundred nodes settles in about a second).
    """
    names, out_adj, in_adj, und = _adjacency(node_names, edges)
    connected = [n for n in names if und[n]]
    isolated = [n for n in names if not und[n]]
    if not connected:
        return _place_satellites({}, isolated, affinity)
    if len(connected) == 1:
        return _place_satellites({connected[0]: [0.0, 0.0]}, isolated, affinity)

    n = len(connected)
    seed = auto_layout(connected, edges)
    side = math.sqrt(n) * X_SPACING * 1.2          # target canvas side
    xs = [seed[m][0] for m in connected]
    ys = [seed[m][1] for m in connected]
    span_x = (max(xs) - min(xs)) or 1.0
    span_y = (max(ys) - min(ys)) or 1.0
    pos = {m: [(seed[m][0] - min(xs)) / span_x * side,
               (seed[m][1] - min(ys)) / span_y * side] for m in connected}

    k = side / math.sqrt(n)
    if iterations is None:
        iterations = max(40, min(200, int(12000 / n) + 30))
    temp = side * 0.10
    cool = temp / (iterations + 1)
    for _ in range(iterations):
        disp = {m: [0.0, 0.0] for m in connected}
        for i in range(n):                          # repulsion, every pair
            a = connected[i]
            ax, ay = pos[a]
            for j in range(i + 1, n):
                b = connected[j]
                dx, dy = ax - pos[b][0], ay - pos[b][1]
                d2 = dx * dx + dy * dy
                if d2 < 1e-9:                       # coincident: nudge apart
                    dx, dy, d2 = 1e-3 * (i + 1), 1e-3 * (j + 1), 2e-6
                f = k * k / d2                      # k²/d along the unit vector
                disp[a][0] += dx * f
                disp[a][1] += dy * f
                disp[b][0] -= dx * f
                disp[b][1] -= dy * f
        for u in connected:                         # attraction along edges
            for v in out_adj[u]:
                dx, dy = pos[u][0] - pos[v][0], pos[u][1] - pos[v][1]
                d = math.hypot(dx, dy) or 1e-6
                f = d / k                           # d²/k along the unit vector
                disp[u][0] -= dx * f
                disp[u][1] -= dy * f
                disp[v][0] += dx * f
                disp[v][1] += dy * f
        for m in connected:                         # step, capped by temperature
            dx, dy = disp[m]
            d = math.hypot(dx, dy) or 1e-6
            step = min(d, temp)
            pos[m][0] += dx / d * step
            pos[m][1] += dy / d * step
        temp -= cool

    # A spring embedding settles at whatever scale balances the forces, which
    # on a sparse network is far wider than the other layouts. Renormalise to
    # the usual node spacing so nodes stay a readable size when fitted to the
    # view — and so the satellite offsets below (in X_SPACING units) land right.
    _rescale_to_spacing(pos, connected)
    return _place_satellites(pos, isolated, affinity)


def _rescale_to_spacing(pos, names, target=X_SPACING * 0.8):
    """Scale positions in place so the median nearest-neighbour gap is target."""
    pts = [pos[m] for m in names]
    if len(pts) < 2:
        return pos
    gaps = []
    for i, a in enumerate(pts):
        best = min((math.dist(a, b) for j, b in enumerate(pts) if i != j),
                   default=0.0)
        if best > 1e-9:
            gaps.append(best)
    if not gaps:
        return pos
    gaps.sort()
    median = gaps[len(gaps) // 2]
    if median <= 1e-9:
        return pos
    scale = target / median
    for m in names:
        pos[m][0] *= scale
        pos[m][1] *= scale
    return pos


def grouped_layout(node_names, edges, groups=None, affinity=None):
    """One block per functional group, left to right along water's journey
    (source → river → storage → link → demand → virtual). Each block is a
    compact grid ordered by depth, so a 162-node model reads as six tidy
    clusters instead of one 38-wide row. affinity is unused — virtual nodes
    get their own block rather than being parked beside what they watch.
    """
    names, out_adj, in_adj, und = _adjacency(node_names, edges)
    groups = groups or {}
    depth = _depths(names, out_adj, und)

    buckets = defaultdict(list)
    for n in names:
        buckets[groups.get(n) or "other"].append(n)

    positions = {}
    x_cursor = 0.0
    for group in GROUP_ORDER:
        members = buckets.get(group)
        if not members:
            continue
        members.sort(key=lambda n: (depth.get(n, 0), n))
        cols = max(1, int(math.ceil(math.sqrt(len(members)))))
        for i, n in enumerate(members):
            positions[n] = [x_cursor + (i % cols) * X_SPACING,
                            (i // cols) * Y_SPACING]
        x_cursor += cols * X_SPACING + COMPONENT_GAP
    return positions


def radial_layout(node_names, edges, affinity=None):
    """Concentric rings by depth from the sources — water flows outwards from
    the centre. Compact for wide, shallow networks: a row that would be 38
    nodes long becomes a ring. Each ring is pushed out far enough to seat its
    nodes and to stay outside the previous one.
    """
    names, out_adj, in_adj, und = _adjacency(node_names, edges)
    connected = [n for n in names if und[n]]
    isolated = [n for n in names if not und[n]]
    if not connected:
        return _place_satellites({}, isolated, affinity)

    depth = _depths(connected, out_adj, und)
    by_depth = defaultdict(list)
    for n in connected:
        by_depth[depth.get(n, 0)].append(n)

    positions = {}
    prev_r = 0.0
    for d in sorted(by_depth):
        members = sorted(by_depth[d])
        # radius: honour the ring index, seat every node, clear the last ring
        needed = len(members) * X_SPACING * 0.55 / (2 * math.pi)
        radius = max((d + 0.6) * Y_SPACING * 1.2, needed,
                     prev_r + Y_SPACING * 0.8)
        prev_r = radius
        for i, n in enumerate(members):
            angle = 2 * math.pi * i / len(members) - math.pi / 2
            positions[n] = [radius * math.cos(angle),
                            radius * math.sin(angle)]
    return _place_satellites(positions, isolated, affinity)


# name -> (label for the picker, one-line hint). "layered" stays the default.
LAYOUTS = [
    {"kind": "layered", "label": "Layered (flow)",
     "hint": "Sources at the top, demands at the bottom — follows the water"},
    {"kind": "force", "label": "Force-directed",
     "hint": "Spring embedding — untangles meshy networks"},
    {"kind": "grouped", "label": "Grouped by function",
     "hint": "Blocks: source, river, storage, link, demand, virtual"},
    {"kind": "radial", "label": "Radial",
     "hint": "Rings by distance from the sources — compact for wide networks"},
]
LAYOUT_KINDS = [spec["kind"] for spec in LAYOUTS]


def compute(kind, node_names, edges, affinity=None, groups=None):
    """Dispatch to a named layout; unknown names fall back to layered."""
    if kind == "force":
        return force_layout(node_names, edges, affinity=affinity)
    if kind == "grouped":
        return grouped_layout(node_names, edges, groups=groups,
                              affinity=affinity)
    if kind == "radial":
        return radial_layout(node_names, edges, affinity=affinity)
    return auto_layout(node_names, edges, affinity=affinity)


def layout_missing(node_names, edges, existing):
    """Keep existing positions; place only missing nodes near the barycenter
    of their placed neighbours (or in a spare row below everything)."""
    positions = {k: list(v) for k, v in existing.items()}
    missing = [n for n in node_names if n not in positions]
    if not missing:
        return positions
    if not positions:
        return auto_layout(node_names, edges)

    nbrs = defaultdict(set)
    for edge in edges:
        u, v = edge[0], edge[1]
        nbrs[u].add(v)
        nbrs[v].add(u)

    xs = [p[0] for p in positions.values()]
    ys = [p[1] for p in positions.values()]
    spare_x, spare_y = min(xs), max(ys) + Y_SPACING
    changed = True
    while changed:
        changed = False
        for n in list(missing):
            placed = [positions[m] for m in nbrs[n] if m in positions]
            if placed:
                positions[n] = [sum(p[0] for p in placed) / len(placed) + 30.0,
                                sum(p[1] for p in placed) / len(placed) + 30.0]
                missing.remove(n)
                changed = True
    for n in missing:  # isolated leftovers
        positions[n] = [spare_x, spare_y]
        spare_x += X_SPACING
    return positions
