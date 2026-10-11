"""Bounded joint colour search; see references/candidate-search.md for the API.

No I/O, cache, image calls, inferred contrast requirement, or beauty score.
The caller supplies palette_core.build_features(frozen_snapshot) and owns its
palette_binding. Resource limits are engineering choices, not design rules.
"""
from __future__ import annotations

import copy
import math
from collections import Counter

import palette_core as pc


DEFAULT_LIMITS = {"per_role": 8, "max_nodes": 10000, "max_results": 5}
MAX_LIMITS = {"per_role": 32, "max_nodes": 100000, "max_results": 20}
# Suppress numerical hue left by the existing conversion of exact sRGB grays.
# A perceptual near-neutral cutoff must be supplied explicitly for hue queries.
NUMERICAL_CHROMA_FLOOR = 1e-6
METRICS = {
    "lightness_delta": "OKLCh L (a - b)",
    "chroma_delta": "OKLCh C (a - b)",
    "hue_distance": "degrees, shorter arc",
    "oklab_distance": "d100 = 100 * Euclidean OKLab distance",
    "contrast": "sRGB relative-luminance ratio",
}


def _require(condition, message, code="GROUP_SPEC_INVALID"):
    if not condition:
        pc.fail(code, message)


def _keys(value, allowed, required, label):
    _require(isinstance(value, dict), f"{label}: expected an object")
    _require(all(isinstance(k, str) for k in value), f"{label}: keys must be strings")
    _require(not (set(value) - set(allowed)),
             f"{label}: unknown fields {sorted(set(value) - set(allowed))}")
    _require(not (set(required) - set(value)),
             f"{label}: missing fields {sorted(set(required) - set(value))}")


def _number(value, label, minimum=None, maximum=None):
    finite = False
    if type(value) in (int, float):
        try:
            finite = math.isfinite(value)
        except OverflowError:
            pass
    _require(finite, f"{label}: expected a finite number, not a boolean")
    _require(minimum is None or value >= minimum, f"{label}: below {minimum}")
    _require(maximum is None or value <= maximum, f"{label}: above {maximum}")
    return value


def _text(value, label, stable_id=False):
    _require(isinstance(value, str) and bool(value.strip()) and len(value) <= 4096,
             f"{label}: expected nonblank text (at most 4096 characters)")
    pc.reject_surrogates(value, label)
    if stable_id:
        _require(bool(pc.ID_RE.fullmatch(value)), f"{label}: invalid stable ID")
    return value


def _rgb(value, label):
    _keys(value, "RGB", "RGB", label)
    for channel in "RGB":
        _require(type(value[channel]) is int and 0 <= value[channel] <= 255,
                 f"{label}.{channel}: expected integer 0..255")


def _features(features):
    _require(isinstance(features, list) and len(features) <= 100000,
             "features: expected a list of at most 100000 palette_core features",
             "GROUP_FEATURES_INVALID")
    by_id = {}
    fields = {"color_id", "name", "rgb", "hex", "oklab", "oklch", "luminance", "enabled"}
    for row in features:
        _keys(row, fields, fields, "feature")
        cid = _text(row["color_id"], "feature.color_id", stable_id=True)
        _require(cid not in by_id, f"duplicate feature color_id: {cid}", "GROUP_FEATURES_INVALID")
        _text(row["name"], f"{cid}.name")
        _rgb(row["rgb"], f"{cid}.rgb")
        _require(type(row["enabled"]) is bool, f"{cid}.enabled must be boolean")
        _number(row["luminance"], f"{cid}.luminance")
        for space in ("oklab", "oklch"):
            values = row[space]
            _require(isinstance(values, list) and len(values) == 3,
                     f"{cid}.{space}: expected a three-element list")
            for i, value in enumerate(values):
                if space == "oklch" and i == 2 and value is None:
                    continue
                _number(value, f"{cid}.{space}[{i}]")
        # This keeps raw L/C units, d100 and cached feature semantics identical
        # to palette_core; hand-written or rounded derived facts are rejected.
        _require(row == pc.feature(row), f"{cid}: facts differ from palette_core.feature(rgb)",
                 "GROUP_FEATURES_INVALID")
        by_id[cid] = row
    return by_id


def _validate(spec, by_id):
    _keys(spec, {"roles", "relations", "limits", "hue_chroma_min"}, {"roles"}, "spec")
    limits = dict(DEFAULT_LIMITS)
    if "limits" in spec:
        _keys(spec["limits"], limits, (), "limits")
        for key, value in spec["limits"].items():
            _require(type(value) is int and 1 <= value <= MAX_LIMITS[key],
                     f"limits.{key}: expected integer 1..{MAX_LIMITS[key]}")
            limits[key] = value
    roles = spec["roles"]
    _require(isinstance(roles, (list, dict)) and 1 <= len(roles) <= 8,
             "roles: expected a mapping or list with 1..8 roles")
    if isinstance(roles, list):
        mapped = {}
        for role in roles:
            _keys(role, {"id", "candidates", "filter", "fixed"}, {"id"}, "role")
            rid = _text(role["id"], "role.id", stable_id=True)
            _require(rid not in mapped, f"duplicate role: {rid}")
            mapped[rid] = {k: v for k, v in role.items() if k != "id"}
        roles = mapped
    hue_used = False
    for rid, role in roles.items():
        _text(rid, "role ID", stable_id=True)
        _keys(role, {"candidates", "filter", "fixed"}, (), f"roles.{rid}")
        if "fixed" in role:
            _require(set(role) == {"fixed"}, f"{rid}: fixed cannot be combined with candidates/filter")
            _keys(role["fixed"], {"rgb", "provenance"}, {"rgb", "provenance"}, f"{rid}.fixed")
            _rgb(role["fixed"]["rgb"], f"{rid}.fixed.rgb")
            _text(role["fixed"]["provenance"], f"{rid}.fixed.provenance")
            continue
        _require(bool(role), f"{rid}: provide candidates, a bounded filter, or fixed")
        if "candidates" in role:
            ids = role["candidates"]
            _require(isinstance(ids, list) and 1 <= len(ids) <= 100000,
                     f"{rid}.candidates: expected a nonempty list within 100000 entries")
            for cid in ids:
                _text(cid, f"{rid}.candidates ID", stable_id=True)
                _require(cid in by_id, f"{rid}: unknown color_id {cid}")
                _require(by_id[cid]["enabled"], f"{rid}: disabled color_id {cid}")
            _require(len(set(ids)) == len(ids), f"{rid}: duplicate candidate ID")
        if "filter" in role:
            filters = role["filter"]
            _keys(filters, {"lightness", "chroma", "hue"}, (), f"{rid}.filter")
            _require(bool(filters), f"{rid}.filter must have at least one bounded axis")
            for axis, bounds in filters.items():
                _require(isinstance(bounds, list) and len(bounds) == 2,
                         f"{rid}.filter.{axis}: expected [lower, upper]")
                upper = {"lightness": 1, "chroma": None, "hue": 360}[axis]
                for value in bounds:
                    _number(value, f"{rid}.filter.{axis}", 0, upper)
                _require(axis == "hue" or bounds[0] <= bounds[1],
                         f"{rid}.filter.{axis}: lower exceeds upper")
                hue_used |= axis == "hue"
    relations = spec.get("relations", [])
    _require(isinstance(relations, list) and len(relations) <= 64,
             "relations: expected a list with at most 64 relations")
    for i, relation in enumerate(relations):
        label = f"relations[{i}]"
        _keys(relation, {"a", "b", "metric", "kind", "reason", "min", "max"},
              {"a", "b", "metric", "kind", "reason"}, label)
        for side in ("a", "b"):
            _text(relation[side], f"{label}.{side}", stable_id=True)
            _require(relation[side] in roles, f"{label}: unknown role {relation[side]}")
        _require(relation["a"] != relation["b"], f"{label}: pair must use distinct roles")
        metric = relation["metric"]
        _require(isinstance(metric, str) and metric in METRICS, f"{label}: unknown metric")
        _require(relation["kind"] in ("hard", "preference"), f"{label}: kind must be hard/preference")
        _text(relation["reason"], f"{label}.reason")
        _require("min" in relation or "max" in relation, f"{label}: supply min and/or max")
        for key in ("min", "max"):
            if key in relation:
                minimum = 1 if metric == "contrast" else (0 if metric in {"hue_distance", "oklab_distance"} else None)
                maximum = {"hue_distance": 180, "contrast": 21}.get(metric)
                _number(relation[key], f"{label}.{key}", minimum, maximum)
        _require("min" not in relation or "max" not in relation or relation["min"] <= relation["max"],
                 f"{label}: min exceeds max")
        hue_used |= metric == "hue_distance"
    _require(not hue_used or "hue_chroma_min" in spec,
             "hue queries require explicit hue_chroma_min for near-neutral handling")
    hue_min = _number(spec.get("hue_chroma_min", NUMERICAL_CHROMA_FLOOR),
                      "hue_chroma_min", NUMERICAL_CHROMA_FLOOR)
    return dict(sorted(roles.items())), relations, limits, hue_min


def _hue(color, hue_min):
    return color["oklch"][2] if color["oklch"][1] > hue_min else None


def _matches(color, filters, hue_min):
    for axis, (lo, hi) in filters.items():
        if axis == "hue":
            hue = _hue(color, hue_min)
            if hue is None:
                return False
            if lo == 0 and hi == 360:
                continue
            lo, hi = lo % 360, hi % 360
            if not (lo <= hue <= hi if lo <= hi else hue >= lo or hue <= hi):
                return False
        else:
            value = color["oklch"][0 if axis == "lightness" else 1]
            if not lo <= value <= hi:
                return False
    return True


def _measure(relation, assignment, hue_min):
    a, b = (assignment[relation[side]] for side in ("a", "b"))
    metric = relation["metric"]
    if metric in {"lightness_delta", "chroma_delta"}:
        axis = 0 if metric == "lightness_delta" else 1
        return a["oklch"][axis] - b["oklch"][axis]
    if metric == "hue_distance":
        ha, hb = _hue(a, hue_min), _hue(b, hue_min)
        if ha is None or hb is None:
            return None
        delta = abs(ha - hb) % 360
        return min(delta, 360 - delta)
    if metric == "oklab_distance":
        return pc.d100(a["oklab"], b["oklab"])
    return pc.contrast(a["rgb"], b["rgb"])


def _outcome(index, relation, assignment, hue_min):
    value = _measure(relation, assignment, hue_min)
    gap = None if value is None else max(0, relation.get("min", value) - value,
                                         value - relation.get("max", value))
    result = dict(relation, index=index, value=value, unit=METRICS[relation["metric"]],
                  outcome="undefined" if value is None else ("met" if gap == 0 else "unmet"),
                  gap=gap)
    if value is None:
        result["detail"] = "Hue is undefined at or below hue_chroma_min; no hue inferred."
    return result


def _dominates(a, b):
    """Compare each declared preference independently; never sum unlike units."""
    strictly_better = False
    for x, y in zip(a, b):
        if x is None or y is None:
            if x != y:
                return False  # Unknown does not outrank, or lose to, a measurement.
        else:
            if x > y:
                return False
            strictly_better |= x < y
    return strictly_better


def _fixed_feature(role):
    return pc.feature({"color_id": "fixed-original", "name": "Retained original RGB",
                       "rgb": role["fixed"]["rgb"], "enabled": True})


def _shortlist(pool, limit):
    """Partition only the admitted domain; choose an actual colour per cell.

    Midrange splits on the widest OKLab axis avoid hue wrap and arbitrary ID
    prefixes, including sparse regions. Representatives are not a beauty score.
    """
    if len(pool) <= limit:
        return pool

    def cell(rows):
        spans = [max(c["oklab"][axis] for c in rows) - min(c["oklab"][axis] for c in rows)
                 for axis in range(3)]
        axis = max(range(3), key=lambda i: spans[i])
        return rows, axis, spans[axis]

    cells = [cell(pool)]
    while len(cells) < limit:
        index = max((i for i, (rows, _, _) in enumerate(cells) if len(rows) > 1),
                    key=lambda i: (cells[i][2], len(cells[i][0]), -i))
        rows, axis, span = cells[index]
        midpoint = min(c["oklab"][axis] for c in rows) + span / 2
        left = [c for c in rows if c["oklab"][axis] <= midpoint]
        right = [c for c in rows if c["oklab"][axis] > midpoint]
        if not left or not right:
            # Identical facts (or a rounded endpoint): stable ID halves.
            middle = len(rows) // 2
            left, right = rows[:middle], rows[middle:]
        cells[index:index + 1] = [cell(left), cell(right)]
    selected = []
    for rows, _, _ in cells:
        center = [math.fsum(c["oklab"][i] for c in rows) / len(rows) for i in range(3)]
        selected.append(min(rows, key=lambda c: (
            sum((c["oklab"][i] - center[i]) ** 2 for i in range(3)), c["color_id"])))
    return sorted(selected, key=lambda c: c["color_id"])


def _trim_archive(archive, role_order):
    """Remove one entry, preserving gap profiles, then balanced role coverage.

    RGB identity counts variation; duplicate palette IDs for the same colour
    earn no extra coverage. No colour-distance magnitude ranks feasible groups.
    The archive has at most max_results + 1 entries while choosing a removal.
    """
    profiles = Counter(entry[1] for entry in archive)
    values = [tuple(tuple(entry[2][rid]["rgb"][c] for c in "RGB") for rid in role_order)
              for entry in archive]
    counts = [Counter(value[i] for value in values) for i in range(len(role_order))]

    def removal_key(index):
        key, gaps, _, _ = archive[index]
        frequencies = [counts[i][value] for i, value in enumerate(values[index])]
        remaining_coverage = tuple(sorted(len(counts[i]) - (n == 1)
                                          for i, n in enumerate(frequencies)))
        # First keep distinct known/unknown preference profiles when possible.
        # Next protect the least-covered roles, then reduce repeated role RGBs.
        return (profiles[gaps] > 1, remaining_coverage,
                sum(n - 1 for n in frequencies), key)

    archive.pop(max(range(len(archive)), key=removal_key))


def _candidate(roles, chosen, outcomes, hue_min):
    output_roles = {}
    for rid, color in chosen.items():
        item = {k: copy.deepcopy(color[k]) for k in
                ("color_id", "name", "rgb", "hex", "oklab", "oklch", "luminance")}
        item["hue_for_relations"] = _hue(color, hue_min)
        if "fixed" in roles[rid]:
            item.update(source="fixed_original", color_id=None, name=None,
                        provenance=roles[rid]["fixed"]["provenance"])
        else:
            item["source"] = "palette"
        output_roles[rid] = item
    unmet = [r["index"] for r in outcomes if r["kind"] == "hard" and r["outcome"] == "unmet"]
    undefined = [r["index"] for r in outcomes if r["kind"] == "hard" and r["outcome"] == "undefined"]
    return {"roles": output_roles, "relations": copy.deepcopy(outcomes),
            "checks": {"hard_relations_met": not unmet and not undefined,
                       "unmet_hard_relations": unmet, "undefined_hard_relations": undefined},
            "tradeoffs": {
                "unmet_preferences": [r["index"] for r in outcomes
                                      if r["kind"] == "preference" and r["outcome"] == "unmet"],
                "undefined_preferences": [r["index"] for r in outcomes
                                          if r["kind"] == "preference" and r["outcome"] == "undefined"],
            }}


def evaluate_selection(features, spec, selection):
    """Recheck final role -> color_id choices against the full declared domain.

    Returns the same roles/relations/checks/tradeoffs as a search candidate.
    Every palette role is required; fixed roles may be omitted or mapped to
    None. Invalid/out-of-domain selections and unmet/undefined hard relations
    raise palette_core.PaletteError; unknown preferences remain explicit.
    No search budget is consumed and no adoption or permission is inferred.
    """
    by_id = _features(features)
    roles, relations, _, hue_min = _validate(spec, by_id)
    _keys(selection, roles, [rid for rid, role in roles.items() if "fixed" not in role], "selection")
    chosen = {}
    for rid, role in roles.items():
        if "fixed" in role:
            _require(selection.get(rid) is None, f"selection.{rid}: fixed original cannot be replaced")
            chosen[rid] = _fixed_feature(role)
            continue
        cid = _text(selection[rid], f"selection.{rid}", stable_id=True)
        _require(cid in by_id, f"selection.{rid}: unknown color_id {cid}")
        color = by_id[cid]
        _require(color["enabled"], f"selection.{rid}: disabled color_id {cid}")
        _require("candidates" not in role or cid in role["candidates"],
                 f"selection.{rid}: color_id outside declared candidates")
        _require(_matches(color, role.get("filter", {}), hue_min),
                 f"selection.{rid}: color_id outside declared filter")
        chosen[rid] = color
    outcomes = [_outcome(i, relation, chosen, hue_min) for i, relation in enumerate(relations)]
    result = _candidate(roles, chosen, outcomes, hue_min)
    checks = result["checks"]
    _require(checks["hard_relations_met"],
             f"selection: hard relations failed; unmet={checks['unmet_hard_relations']}; "
             f"undefined={checks['undefined_hard_relations']}", "GROUP_SELECTION_HARD_FAILED")
    return result


def search_groups(features, spec):
    """Return a JSON-compatible dict of bounded joint candidates and diagnostics.

    Invalid input raises palette_core.PaletteError. Inputs are not mutated.
    Infeasible hard conditions are never relaxed; a truncated search cannot
    establish infeasibility over omitted candidates. See candidate-search.md.
    """
    by_id = _features(features)
    roles, relations, limits, hue_min = _validate(spec, by_id)
    pools, summaries = {}, []
    for rid, role in roles.items():
        if "fixed" in role:
            pool = [_fixed_feature(role)]
        else:
            ids = sorted(role.get("candidates", by_id))
            pool = [by_id[cid] for cid in ids if by_id[cid]["enabled"]
                    and _matches(by_id[cid], role.get("filter", {}), hue_min)]
        matched = len(pool)
        pools[rid] = _shortlist(pool, limits["per_role"])
        summaries.append({"role": rid, "matched": matched, "selected": len(pools[rid]),
                          "truncated": matched > len(pools[rid]),
                          "selected_color_ids": [c["color_id"] for c in pools[rid]]
                          if "fixed" not in role else [],
                          "source": "fixed_original" if "fixed" in role else "palette"})

    role_order = list(roles)
    positions = {rid: i for i, rid in enumerate(role_order)}
    hard_at_depth = [[] for _ in roles]
    rejections = []
    for i, relation in enumerate(relations):
        rejections.append({"relation": i, "unmet": 0, "undefined": 0})
        if relation["kind"] == "hard":
            hard_at_depth[max(positions[relation["a"]], positions[relation["b"]])].append(i)
    archive, assignment = [], {}
    nodes = feasible = capacity_drops = 0

    def consider():
        nonlocal feasible, capacity_drops, archive
        feasible += 1
        outcomes = [_outcome(i, r, assignment, hue_min) for i, r in enumerate(relations)]
        gaps = tuple(r["gap"] for r in outcomes if r["kind"] == "preference")
        if any(_dominates(entry[1], gaps) for entry in archive):
            return
        archive = [entry for entry in archive if not _dominates(gaps, entry[1])]
        key = tuple(assignment[rid]["color_id"] for rid in role_order)
        archive.append((key, gaps, dict(assignment), outcomes))
        archive.sort(key=lambda entry: entry[0])
        if len(archive) > limits["max_results"]:
            _trim_archive(archive, role_order)
            capacity_drops += 1

    def check_hard(depth):
        passed = True
        for index in hard_at_depth[depth]:
            result = _outcome(index, relations[index], assignment, hue_min)
            if result["outcome"] != "met":
                rejections[index][result["outcome"]] += 1
                passed = False
        return passed

    # Try at most per_role cyclic diagonals first, so a short budget can reach
    # different choices in every role instead of spending it in one DFS branch.
    # All assignments (including later revisits) consume the SAME node budget.
    seeded = set()

    def seed():
        nonlocal nodes
        for offset in range(max(len(pool) for pool in pools.values())):
            assignment.clear()
            for depth, rid in enumerate(role_order):
                if nodes == limits["max_nodes"]:
                    return False
                nodes += 1
                assignment[rid] = pools[rid][offset % len(pools[rid])]
                if not check_hard(depth):
                    break
            else:
                seeded.add(tuple(assignment[rid]["color_id"] for rid in role_order))
                consider()
        assignment.clear()
        return True

    def visit(depth):
        nonlocal nodes
        if depth == len(role_order):
            if tuple(assignment[rid]["color_id"] for rid in role_order) not in seeded:
                consider()
            return True
        rid = role_order[depth]
        for color in pools[rid]:
            if nodes == limits["max_nodes"]:
                return False
            nodes += 1
            assignment[rid] = color
            if check_hard(depth) and not visit(depth + 1):
                return False
        assignment.pop(rid, None)
        return True

    empty_roles = [row["role"] for row in summaries if not row["matched"]]
    shortlist_combinations = math.prod(len(pool) for pool in pools.values())
    if empty_roles:
        tree_exhausted = True
    else:
        seeds_cover_tree = max(len(pool) for pool in pools.values()) == shortlist_combinations
        tree_exhausted = seed() and (seeds_cover_tree or visit(0))
    pools_truncated = any(row["truncated"] for row in summaries)
    infeasibility_proven = not archive and (bool(empty_roles) or (tree_exhausted and not pools_truncated))
    candidates = [_candidate(roles, chosen, outcomes, hue_min) for _, _, chosen, outcomes in archive]
    return {
        "status": "ok" if candidates else ("infeasible" if infeasibility_proven else "not_found_within_limits"),
        "candidates": candidates,
        "search": {
            "limits": limits, "role_order": role_order, "role_pools": summaries,
            "domain_combinations": str(math.prod(row["matched"] for row in summaries)),
            "shortlist_combinations": shortlist_combinations,
            "nodes_visited": nodes, "feasible_groups_seen": feasible,
            "candidate_pools_truncated": pools_truncated, "tree_exhausted": tree_exhausted,
            "archive_truncated": bool(capacity_drops), "archive_capacity_drops": capacity_drops,
            "empty_roles": empty_roles, "hard_rejections": rejections,
            "hard_constraints_relaxed": False, "infeasibility_proven": infeasibility_proven,
            "hue_chroma_min": hue_min,
            "hue_threshold_source": "explicit" if "hue_chroma_min" in spec else "numerical_gray_guard_only",
            "shortlisting": "Within declared domains, bisect widest OKLab cells at their axis midrange; take a nearest-centroid colour per cell. IDs break ties.",
            "ordering": "Cyclic role coverage seeds, then depth-first search under one node budget; failed/undefined hard relations prune immediately. Feasible seeds count once.",
            "selection": "Bounded per-preference gap archive; overflow preserves gap profiles, balanced role RGB coverage, then fewer repeated role RGBs. IDs break ties; no beauty score.",
            "scope": "Results are alternatives within declared domains and disclosed limits, not a complete Pareto frontier or an aesthetic ranking.",
        },
    }
