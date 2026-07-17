"""Automatic schematic layout for pywr networks (no external dependencies).

Water networks are mostly directed and acyclic (source → treatment → demand),
so a layered "Sugiyama-lite" layout reads far better than a force layout:

  1. break cycles (DFS back-edge removal, for layering only)
  2. assign layers by longest path from the sources
  3. order nodes within layers by repeated barycenter sweeps
  4. assign coordinates with fixed spacing; flow runs top → bottom

Disconnected components are laid out independently and placed side by side.
"""

from collections import defaultdict, deque

X_SPACING = 140.0
Y_SPACING = 110.0
COMPONENT_GAP = 220.0


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

    # isolated nodes with an affinity target sit beside what they reference
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

    # remaining isolated nodes: compact grid below everything
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
