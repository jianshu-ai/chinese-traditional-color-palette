"""Authored design plans, local materialization, and one current-artifact gate.

No model or image service is called here. A review is an attributed observation,
not proof that a program has assessed beauty or fully decoded an image.
"""
from __future__ import annotations

import argparse
import copy
import math
from pathlib import Path

from palette_core import (RUN_SCHEMA, LABELS, ID_RE, PaletteError, binding_for,
    canonical_bytes, contrast, d100, fail, feature, hex_rgb, load_manifest,
    parse_json_bytes, preflight, rgb_to_oklab, run_context, sha, write_bytes, write_json)

ASPECTS = ("aesthetics", "purpose", "expression", "regions", "color_relationships",
           "preservation", "design_consistency", "typography", "distinctiveness")
OBJECT_MODULES = {"storefront", "residential_streetscape", "apparel", "toy_product", "text_design", "generic"}


def read_json(path):
    path = Path(path)
    return parse_json_bytes(path.read_bytes(), str(path))


def require(condition, message, code="SCHEMA_INVALID"):
    if not condition:
        fail(code, message)


def nonblank(value):
    return isinstance(value, str) and bool(value.strip())


def number(value):
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def object_keys(obj, required, optional=()):
    require(isinstance(obj, dict), "expected an object")
    require(set(required) <= set(obj), f"missing fields: {sorted(set(required) - set(obj))}")
    require(not set(obj) - set(required) - set(optional), f"unknown fields: {sorted(set(obj) - set(required) - set(optional))}")


def unique_ids(values, label):
    require(isinstance(values, list) and all(isinstance(v, str) and ID_RE.fullmatch(v) for v in values), f"{label} must contain valid IDs")
    require(len(set(values)) == len(values), f"{label} contains duplicates")
    return set(values)


def validate_observations(plan):
    """Validate attributed observations, not completeness of image understanding."""
    scene = plan["scene_context"]
    object_keys(scene, ("object_module", "overlays", "preferences"))
    require(scene["object_module"] in OBJECT_MODULES, "unsupported object module")
    require(isinstance(scene["overlays"], list) and all(nonblank(s) for s in scene["overlays"]), "scene overlays must be nonblank strings")
    require(len(set(scene["overlays"])) == len(scene["overlays"]), "duplicate scene overlay")
    require(isinstance(scene["preferences"], list), "scene preferences must be an array")
    for preference in scene["preferences"]:
        object_keys(preference, ("statement", "scope", "source"))
        require(all(nonblank(v) for v in preference.values()), "preference requires statement, scope and source")
    inventory = plan["observation_inventory"]
    object_keys(inventory, ("elements", "reading_targets"))
    require(isinstance(inventory["elements"], list) and inventory["elements"], "observe source elements before designing regions")
    elements = {}
    for element in inventory["elements"]:
        object_keys(element, ("element_id", "description", "kind"))
        eid = element["element_id"]
        unique_ids([eid], "element_id")
        require(eid not in elements, "duplicate observed element")
        require(nonblank(element["description"]) and element["kind"] in {"surface", "text", "prop", "context"}, "element needs description and supported kind")
        elements[eid] = element
    require(isinstance(inventory["reading_targets"], list), "reading_targets must be an array")
    readings, combinations, text_ids = {}, set(), set()
    for target in inventory["reading_targets"]:
        object_keys(target, ("reading_id", "text_element_id", "background_element_id", "state", "medium", "description", "contrast_class"))
        rid = target["reading_id"]
        unique_ids([rid], "reading_id")
        require(rid not in readings, "duplicate reading ID")
        t, b = target["text_element_id"], target["background_element_id"]
        require(t in elements and b in elements and t != b and elements[t]["kind"] == "text", "reading target needs observed text and distinct actual background")
        require(all(nonblank(target[k]) for k in ("state", "description")), "reading state and observation required")
        require(target["medium"] in {"physical", "digital"}, "unsupported reading medium")
        allowed = {"normal_text", "large_text"} if target["medium"] == "digital" else {"contextual"}
        require(target["contrast_class"] in allowed, "digital reading requires normal_text or evidenced large_text; physical reading is contextual")
        combo = (t, b, target["state"])
        require(combo not in combinations, "duplicate text/background/state reading target")
        combinations.add(combo)
        readings[rid] = target
        text_ids.add(t)
    require(text_ids == {e for e, v in elements.items() if v["kind"] == "text"}, "every observed text element needs a reading target; unrelated background lettering may be scoped as context", "READING_COVERAGE_MISSING")
    require(bool(readings) == (plan["object"]["text_status"] == "present"), "text status must match observed in-scope reading targets")
    return elements, readings


def validate_coverage(sol, elements, readings, regions):
    require(isinstance(sol["coverage"], list), "coverage must be an array")
    coverage, mapped = {}, set()
    for item in sol["coverage"]:
        object_keys(item, ("element_id", "disposition", "region_ids", "reason"))
        eid = item["element_id"]
        require(eid in elements and eid not in coverage, "coverage must uniquely reference observed elements", "ELEMENT_COVERAGE_INVALID")
        refs = unique_ids(item["region_ids"], "coverage region_ids")
        require(item["disposition"] in {"recolor", "preserve", "out_of_scope"} and nonblank(item["reason"]), "element disposition needs a concrete reason")
        if item["disposition"] == "recolor":
            require(refs and refs <= set(regions), "recolored element must map to actual design regions", "ELEMENT_COVERAGE_INVALID")
            mapped.update(refs)
        else:
            require(refs <= set(regions), "retained visual associations must reference real regions", "ELEMENT_COVERAGE_INVALID")
        coverage[eid] = item
    require(set(coverage) == set(elements), "every observed element requires a disposition", "ELEMENT_COVERAGE_MISSING")
    require(mapped == set(regions), "every design region must map to an observed recolored element", "ELEMENT_COVERAGE_INVALID")
    require(isinstance(sol["reading_coverage"], list), "reading_coverage must be an array")
    pairs = {(p["text_region_id"], p["background_region_id"]): p for p in sol["text_pairings"]}
    seen, used_pairs = set(), set()
    for item in sol["reading_coverage"]:
        object_keys(item, ("reading_id", "mode", "reason"), ("text_region_id", "background_region_id"))
        rid = item["reading_id"]
        require(rid in readings and rid not in seen, "reading coverage must uniquely reference observed targets", "READING_COVERAGE_INVALID")
        require(nonblank(item["reason"]), "reading coverage needs concrete conditions and reason")
        target = readings[rid]
        t, b = (coverage[target[k]] for k in ("text_element_id", "background_element_id"))
        if item["mode"] == "paired":
            pair_key = (item.get("text_region_id"), item.get("background_region_id"))
            require(pair_key in pairs, "reading coverage must reference a real text pairing")
            require(t["disposition"] == b["disposition"] == "recolor" and pair_key[0] in t["region_ids"] and pair_key[1] in b["region_ids"],
                    f"pairing does not represent observed text/background; never invent a palette background; "
                    f"solution_id={sol['solution_id']!r}, reading_id={rid!r}; "
                    f"observed text(element_id={target['text_element_id']!r}, disposition={t['disposition']!r}, region_ids={t['region_ids']!r}); "
                    f"observed background(element_id={target['background_element_id']!r}, disposition={b['disposition']!r}, region_ids={b['region_ids']!r}); "
                    f"submitted pair(text_region_id={pair_key[0]!r}, background_region_id={pair_key[1]!r})",
                    "READING_COVERAGE_INVALID")
            pair = pairs[pair_key]
            require(pair["medium"] == target["medium"], "pairing medium differs from reading target")
            if target["medium"] == "digital":
                minimum = 3.0 if target["contrast_class"] == "large_text" else 4.5
                require(number(pair["minimum_contrast"]) and pair["minimum_contrast"] >= minimum, "digital contrast threshold below applicable normal-text 4.5 / evidenced large-text 3 floor", "TEXT_CONTRAST_FAILED")
            used_pairs.add(pair_key)
        elif item["mode"] == "visual":
            require("text_region_id" not in item and "background_region_id" not in item, "visual review must not invent designed text/background regions")
            require(t["disposition"] != "recolor" or b["disposition"] != "recolor", "two designed base colours require a computed pairing, not visual bypass", "READING_COVERAGE_INVALID")
        else:
            require(False, "reading coverage mode must be paired or visual")
        seen.add(rid)
    require(seen == set(readings), "every actual reading target/state requires coverage", "READING_COVERAGE_MISSING")
    require(used_pairs == set(pairs), "text pairing has no actual observed reading target", "READING_COVERAGE_INVALID")


def current_run(run_arg):
    ctx = run_context(run_arg)
    require(ctx[1].get("run_schema_version") == RUN_SCHEMA,
            "legacy run is read-only; fork a new run and author a current plan and review", "LEGACY_RUN_REQUIRES_REPLAN")
    return ctx


def check_count(count, reason):
    require(type(count) is int and 4 <= count <= 6, "solution_count must be 4, 5 or 6")
    require(isinstance(reason, str), "count_reason must be text")
    require(count == 5 or nonblank(reason), "four or six solutions require an object-specific count_reason")


def analyze(args):
    run, manifest, _, _ = current_run(args.run)
    require(not (run / "design_plan.json").exists(), "analyze cannot replace a planned run; start a new run", "RUN_EXISTS")
    count = getattr(args, "solution_count", 5)
    reason = getattr(args, "count_reason", "")
    check_count(count, reason)
    input_info = {"type": "none", "sha256": None}
    evidence = [{"evidence_id": "REQ-01", "source_type": "user_quote", "quote": args.request or ""}]
    if args.input:
        path = Path(args.input).resolve()
        require(path.is_file(), "input must be an existing file")
        require(not path.is_relative_to(run), "source image must be outside the run", "SOURCE_PROTECTION")
        input_info = {"type": path.suffix.lower().lstrip("."), "path": str(path), "sha256": sha(path.read_bytes())}
        evidence.append({"evidence_id": "INPUT-01", "source_type": "input_file", "path": str(path)})
    intent = {"schema_version": RUN_SCHEMA, "palette_binding": binding_for(manifest, run),
              "user_request": args.request or "", "input": input_info,
              "mode": getattr(args, "mode", "palette_design"),
              "solution_count": count, "count_reason": reason,
              "object_kind": getattr(args, "object_kind", "other"),
              "text_status": getattr(args, "text_status", "unknown"),
              "evidence": evidence, "constraints": {"hard": []}}
    write_json(run / "intent.json", intent)
    write_json(run / "analysis.json", {"palette_binding": intent["palette_binding"], "input": input_info,
        "observations": [], "unknowns": ["Object, purpose, expression and paint regions require inspection by the current model."]})
    write_json(run / "state.json", {"stage": "analyze", "status": "pending"})
    return intent


def validate_intent_data(intent, manifest, snapshot, run):
    require(isinstance(intent, dict) and intent.get("schema_version") == RUN_SCHEMA, "current intent schema required")
    require(intent.get("palette_binding") == binding_for(manifest, run), "intent binding differs from this run", "PALETTE_BINDING_MISMATCH")
    check_count(intent.get("solution_count"), intent.get("count_reason"))
    require(intent.get("mode") in {"palette_design", "faithful_recolor"}, "unsupported intent mode")
    require(intent.get("object_kind") in {"toy", "streetscape", "packaging", "other"}, "unsupported object kind")
    require(intent.get("text_status") in {"unknown", "absent", "present"}, "invalid text status")
    require(isinstance(intent.get("input"), dict), "input binding is required")
    constraints = intent.get("constraints", {})
    object_keys(constraints, ("hard",), ("fidelity",))
    require(isinstance(constraints["hard"], list), "hard constraints must be a list")
    enabled = {c["color_id"]: c for c in snapshot["colors"] if c["enabled"]}
    for lock in constraints["hard"]:
        require(isinstance(lock, dict), "hard constraint must be an object")
        kind = lock.get("type")
        require(kind in {"color_id", "exact_rgb"}, "unsupported hard constraint; do not silently ignore it", "CONSTRAINT_UNSUPPORTED")
        object_keys(lock, ("type", "color_id" if kind == "color_id" else "rgb"), ("region_id",))
        if kind == "color_id":
            require(lock["color_id"] in enabled, "locked colour unavailable", "PALETTE_ID_UNAVAILABLE")
        else:
            require(any(c["rgb"] == lock["rgb"] for c in enabled.values()), "exact RGB unavailable in snapshot", "CONSTRAINT_CONFLICT")
    if intent["mode"] == "faithful_recolor":
        f = constraints.get("fidelity", {})
        object_keys(f, ("max_d100", "regions", "evidence"))
        require(number(f["max_d100"]) and f["max_d100"] >= 0 and nonblank(f["evidence"]), "faithful recolour needs an explicit distance limit and user evidence")
        require(isinstance(f["regions"], list) and f["regions"], "faithful recolour needs observed source regions")
        ids = set()
        for item in f["regions"]:
            object_keys(item, ("region_id", "source_rgb"))
            require(nonblank(item["region_id"]) and item["region_id"] not in ids, "fidelity region IDs must be unique")
            ids.add(item["region_id"])
            rgb = item["source_rgb"]
            require(isinstance(rgb, dict) and set(rgb) == set("RGB") and all(type(v) is int and 0 <= v <= 255 for v in rgb.values()), "source RGB must contain integer R/G/B")
    else:
        require("fidelity" not in constraints, "distance constraints apply only to explicit faithful_recolor")


def validate_intent(args):
    run, manifest, snapshot, _ = current_run(args.run)
    intent = read_json(args.intent or run / "intent.json")
    validate_intent_data(intent, manifest, snapshot, run)
    return {"status": "pass", "palette_binding": binding_for(manifest, run), "solution_count": intent["solution_count"]}


def search_colors(args):
    run, manifest, _, features = current_run(args.run)
    colors = [f for f in features if f["enabled"]]
    near = next((c for c in colors if c["color_id"] == args.near_id), None) if args.near_id else None
    require(not args.near_id or near is not None, "near-id must be enabled in snapshot", "PALETTE_ID_UNAVAILABLE")
    for key in ("lightness", "chroma", "hue"):
        limits = getattr(args, key, None)
        if limits:
            require(all(number(x) for x in limits), f"{key} limits must be finite")
            require(0 <= limits[0] and 0 <= limits[1], f"{key} limits must be nonnegative")
            if key != "hue":
                require(limits[0] <= limits[1], f"{key} range must ascend")
            require(key != "lightness" or limits[1] <= 1, "lightness is 0..1")
            require(key != "hue" or max(limits) <= 360, "hue is 0..360")
    if args.query:
        q = args.query.casefold()
        colors = [c for c in colors if q in c["name"].casefold() or q in c["color_id"].casefold()]
    for key, idx in (("lightness", 0), ("chroma", 1), ("hue", 2)):
        limits = getattr(args, key, None)
        if limits:
            lo, hi = limits
            colors = [c for c in colors if c["oklch"][idx] is not None and
                ((lo <= c["oklch"][idx] <= hi) if lo <= hi else (c["oklch"][idx] >= lo or c["oklch"][idx] <= hi))]
    colors.sort(key=lambda c: ((d100(near["oklab"], c["oklab"]) if near else 0), c["color_id"]))
    require(type(args.limit) is int and 1 <= args.limit <= 1000 and args.offset >= 0, "limit must be 1..1000; offset nonnegative")
    return {"palette_binding": binding_for(manifest, run), "total": len(colors), "offset": args.offset,
            "colors": colors[args.offset:args.offset + args.limit], "purpose": "lookup only; no aesthetic ranking"}


def plan_template(args):
    run, manifest, snapshot, _ = current_run(args.run)
    intent = read_json(run / "intent.json")
    validate_intent_data(intent, manifest, snapshot, run)
    plan = {"schema_version": RUN_SCHEMA, "palette_binding": binding_for(manifest, run),
        "source_image_sha256": intent["input"].get("sha256"), "solution_count": intent["solution_count"],
        "count_reason": intent["count_reason"],
        "object": {"kind": intent["object_kind"], "purpose": "", "expression_relevant": None,
                   "text_status": intent["text_status"], "paint_boundaries": "preserve" if intent["mode"] == "faithful_recolor" else "redesign"},
        "scene_context": {"object_module": "generic", "overlays": [], "preferences": []},
        "observation_inventory": {"elements": [], "reading_targets": []},
        "preserve": {"geometry": True, "camera": True, "material": True, "notes": []}, "solutions": []}
    for label in LABELS[:intent["solution_count"]]:
        plan["solutions"].append({"solution_id": f"{label}-01", "label": label, "design_intent": "",
            "method": {"name": "", "rationale": ""}, "composition": "",
            "colors": [{"color_id": "", "role": "", "relationship": "", "regions": [
                {"region_id": "", "description": "", "shape": "", "area": {"value": None, "basis": "proposed", "reason": ""}}]}], "text_pairings": [], "coverage": [], "reading_coverage": []})
    if args.out:
        path = Path(args.out).resolve()
        require(not path.exists(), "template output already exists", "RUN_EXISTS")
        write_json(path, plan)
    return plan


def design_signature(solution):
    # Layout is part of the design; explanatory wording and palette row order are not.
    regions = []
    for c in solution["colors"]:
        for r in c["regions"]:
            regions.append({"color_id": c["color_id"], "description": r["description"], "shape": r["shape"], "area": r["area"]["value"]})
    return sha(canonical_bytes(sorted(regions, key=lambda r: canonical_bytes(r))))


def validate_plan(plan, intent, manifest, snapshot, run):
    validate_intent_data(intent, manifest, snapshot, run)
    object_keys(plan, ("schema_version", "palette_binding", "source_image_sha256", "solution_count", "count_reason", "object", "preserve", "scene_context", "observation_inventory", "solutions"))
    require(plan["schema_version"] == RUN_SCHEMA, "current plan schema required", "LEGACY_RUN_REQUIRES_REPLAN")
    require(plan["palette_binding"] == binding_for(manifest, run), "plan binding differs from run", "PALETTE_BINDING_MISMATCH")
    require(plan["source_image_sha256"] == intent["input"].get("sha256"), "plan source differs from intent", "SOURCE_MISMATCH")
    check_count(plan["solution_count"], plan["count_reason"])
    require((plan["solution_count"], plan["count_reason"]) == (intent["solution_count"], intent["count_reason"]), "plan count/reason must match intent")
    obj = plan["object"]
    object_keys(obj, ("kind", "purpose", "expression_relevant", "text_status", "paint_boundaries"))
    require(obj["kind"] in {"toy", "streetscape", "packaging", "other"}, "unsupported object kind")
    require(intent["object_kind"] == "other" or obj["kind"] == intent["object_kind"], "object kind conflicts with intent")
    require(nonblank(obj["purpose"]) and type(obj["expression_relevant"]) is bool, "purpose and observed expression relevance required")
    require(obj["text_status"] in {"absent", "present"}, "inspect actual lettering; unknown text status is not a completed plan")
    require(intent["text_status"] == "unknown" or obj["text_status"] == intent["text_status"], "text status conflicts with intent")
    require(obj["paint_boundaries"] in {"redesign", "preserve"}, "invalid paint boundary policy")
    require(intent["mode"] != "faithful_recolor" or obj["paint_boundaries"] == "preserve", "faithful recolour preserves paint boundaries")
    elements, readings = validate_observations(plan)
    object_keys(plan["preserve"], ("geometry", "camera", "material", "notes"))
    require(all(plan["preserve"][k] is True for k in ("geometry", "camera", "material")), "physical geometry, camera and material must be preserved")
    require(isinstance(plan["preserve"]["notes"], list) and all(nonblank(n) for n in plan["preserve"]["notes"]), "preservation notes must be strings")
    require(isinstance(plan["solutions"], list) and len(plan["solutions"]) == plan["solution_count"], "plan must contain the chosen number of solutions")
    by_id = {c["color_id"]: c for c in snapshot["colors"] if c["enabled"]}
    ids, labels, signatures = set(), set(), set()
    for sol in plan["solutions"]:
        object_keys(sol, ("solution_id", "label", "design_intent", "method", "composition", "colors", "text_pairings", "coverage", "reading_coverage"))
        sid = sol["solution_id"]
        require(isinstance(sid, str) and ID_RE.fullmatch(sid) and sid not in ids, "solution IDs must be unique stable IDs")
        ids.add(sid)
        require(sol["label"] in LABELS[:plan["solution_count"]] and sol["label"] not in labels, "labels must uniquely cover A onward; they are not fidelity tiers")
        labels.add(sol["label"])
        object_keys(sol["method"], ("name", "rationale"))
        require(all(nonblank(v) for v in [sol["design_intent"], sol["composition"], *sol["method"].values()]), "each solution needs a dominant design method, rationale and composition")
        require(isinstance(sol["colors"], list) and sol["colors"], "each solution needs used colours")
        color_ids, regions, total = set(), {}, 0.0
        for c in sol["colors"]:
            object_keys(c, ("color_id", "role", "relationship", "regions"))
            require(c["color_id"] in by_id, "colour unavailable in frozen palette", "PALETTE_ID_UNAVAILABLE")
            require(c["color_id"] not in color_ids, "combine regions sharing a colour into one colour record")
            color_ids.add(c["color_id"])
            require(nonblank(c["role"]) and nonblank(c["relationship"]), "each colour needs role and relationship reasons")
            require(isinstance(c["regions"], list) and c["regions"], "every colour must be used in regions")
            for r in c["regions"]:
                object_keys(r, ("region_id", "description", "shape", "area"))
                rid = r["region_id"]
                require(isinstance(rid, str) and ID_RE.fullmatch(rid) and rid not in regions, "region IDs must be valid and unique within each solution")
                require(nonblank(r["description"]) and nonblank(r["shape"]), "region position and paint boundary shape required")
                a = r["area"]
                object_keys(a, ("value", "basis", "reason"))
                require(number(a["value"]) and 0 < a["value"] <= 1 and a["basis"] in {"proposed", "estimated"} and nonblank(a["reason"]), "area needs a positive proposed/estimated visible design share and reason")
                total += a["value"]
                regions[rid] = c["color_id"]
        require(abs(total - 1) <= 0.02,
                f"solution_id={sid}: visible painted-region shares must sum to 1; "
                f"actual_sum={total!r}; allowed_range=[0.98, 1.02]; "
                f"difference_from_1={total - 1:+.12g} (tolerance .02); not physical surface measurements")
        for lock in intent["constraints"]["hard"]:
            target_ids = [regions.get(lock["region_id"])] if "region_id" in lock else list(color_ids)
            wanted = {lock["color_id"]} if lock["type"] == "color_id" else {i for i, c in by_id.items() if c["rgb"] == lock["rgb"]}
            require(bool(wanted.intersection(target_ids)), "authored solution violates a hard colour lock", "CONSTRAINT_CONFLICT")
        if intent["mode"] == "faithful_recolor":
            f = intent["constraints"]["fidelity"]
            for item in f["regions"]:
                cid = regions.get(item["region_id"])
                require(cid is not None and d100(rgb_to_oklab(by_id[cid]["rgb"]), rgb_to_oklab(item["source_rgb"])) <= f["max_d100"], "faithful recolour distance exceeded", "CONSTRAINT_CONFLICT")
        pairs = sol["text_pairings"]
        require(isinstance(pairs, list), "text_pairings must be an array")
        require(not pairs or obj["text_status"] == "present", "only actual lettering has text pairings")
        pair_keys = set()
        for pair in pairs:
            object_keys(pair, ("text_region_id", "background_region_id", "intended_use", "medium", "minimum_contrast", "threshold_reason"))
            require(pair["text_region_id"] in regions and pair["background_region_id"] in regions and pair["text_region_id"] != pair["background_region_id"], "text pairing must reference two existing regions")
            pair_key = (pair["text_region_id"], pair["background_region_id"])
            require(pair_key not in pair_keys, "duplicate text/background pairing")
            pair_keys.add(pair_key)
            require(nonblank(pair["intended_use"]) and nonblank(pair["threshold_reason"]), "text use and context/threshold reason required")
            require(pair["medium"] in {"physical", "digital"}, "text medium must be physical or digital")
            threshold = pair["minimum_contrast"]
            require(threshold is None or (number(threshold) and 1 <= threshold <= 21), "contrast threshold must be null or 1..21")
            require(pair["medium"] != "digital" or threshold is not None, "digital text needs an explicit evidenced threshold")
            if threshold is not None:
                ratio = contrast(by_id[regions[pair["text_region_id"]]]["rgb"], by_id[regions[pair["background_region_id"]]]["rgb"])
                require(ratio >= threshold, "unrounded text contrast below the authored threshold", "TEXT_CONTRAST_FAILED")
        validate_coverage(sol, elements, readings, regions)
        signature = design_signature(sol)
        require(signature not in signatures, "duplicate colour and region layout; relabelling does not create a design", "DUPLICATE_CANDIDATE")
        signatures.add(signature)
    return plan


def fact_for(color):
    f = feature(color)
    return {k: f[k] for k in ("color_id", "name", "rgb", "hex", "oklab", "oklch", "luminance")}


def solution_binding(plan, sol, intent):
    content = {"solution": sol, "object": plan["object"], "preserve": plan["preserve"],
               "scene_context": plan["scene_context"], "observation_inventory": plan["observation_inventory"],
               "palette_binding": plan["palette_binding"], "source_image_sha256": plan["source_image_sha256"],
               "intent": intent}
    return {"schema_version": RUN_SCHEMA, "solution_id": sol["solution_id"], "label": sol["label"],
            "palette_binding": plan["palette_binding"], "source_image_sha256": plan["source_image_sha256"],
            "plan_sha256": sha(canonical_bytes(content))}


def materialize(plan, intent, snapshot):
    by_id = {c["color_id"]: c for c in snapshot["colors"]}
    solutions = []
    for sol in plan["solutions"]:
        entry = {**solution_binding(plan, sol, intent), "design_intent": sol["design_intent"],
                 "method": sol["method"], "composition": sol["composition"], "colors": [], "typography": [],
                 "scene_context": plan["scene_context"], "observation_inventory": plan["observation_inventory"],
                 "coverage": sol["coverage"], "reading_coverage": sol["reading_coverage"]}
        region_colors = {}
        for c in sol["colors"]:
            entry["colors"].append({**c, **fact_for(by_id[c["color_id"]])})
            for r in c["regions"]:
                region_colors[r["region_id"]] = c["color_id"]
        for pair in sol["text_pairings"]:
            t, b = (by_id[region_colors[pair[key]]] for key in ("text_region_id", "background_region_id"))
            entry["typography"].append({**pair, "text_color": fact_for(t), "background_color": fact_for(b), "contrast": contrast(t["rgb"], b["rgb"])})
        readings = {r["reading_id"]: r for r in plan["observation_inventory"]["reading_targets"]}
        for item in sol["reading_coverage"]:
            if item["mode"] == "visual":
                entry["typography"].append({**item, "target": readings[item["reading_id"]], "contrast": None,
                    "limitation": "actual retained or variable background; no numeric contrast claim; current-image review required"})
        solutions.append(entry)
    return {"schema_version": RUN_SCHEMA, "palette_binding": plan["palette_binding"],
            "author_plan_sha256": sha(canonical_bytes(plan)), "solution_count": plan["solution_count"],
            "generator": "authored-plan-materializer-v2.1", "solutions": solutions}


def check_source(intent):
    source = intent["input"]
    if source.get("sha256") is not None:
        path = Path(source.get("path", ""))
        require(path.is_file() and sha(path.read_bytes()) == source["sha256"], "original image missing or changed", "SOURCE_MODIFIED")


def propose(args):
    require(getattr(args, "plan", None), "provide --plan from the current model; no autonomous colour-selection fallback", "DESIGN_PLAN_REQUIRED")
    run, manifest, snapshot, _ = current_run(args.run)
    intent = read_json(args.intent or run / "intent.json")
    plan = validate_plan(read_json(args.plan), intent, manifest, snapshot, run)
    check_source(intent)
    candidates = materialize(plan, intent, snapshot)
    write_json(run / "intent.json", intent)
    write_json(run / "design_plan.json", plan)
    write_json(run / "candidates.json", candidates)
    write_json(run / "image_plan.json", {**candidates, "object": plan["object"], "preserve": plan["preserve"],
        "source_image": intent["input"], "mode": intent["mode"], "instruction": "Edit each design from the original source. Use this solution's region design and snapshot-derived colour facts."})
    write_json(run / "state.json", {"stage": "propose", "status": "pending", "candidate_count": len(plan["solutions"])})
    return candidates


def load_design(run, manifest, snapshot):
    intent, plan = read_json(run / "intent.json"), read_json(run / "design_plan.json")
    validate_plan(plan, intent, manifest, snapshot, run)
    check_source(intent)
    expected = materialize(plan, intent, snapshot)
    require(read_json(run / "candidates.json") == expected, "candidates differ from authored plan or snapshot facts", "STALE_EVIDENCE")
    expected_image = {**expected, "object": plan["object"], "preserve": plan["preserve"],
        "source_image": intent["input"], "mode": intent["mode"], "instruction": "Edit each design from the original source. Use this solution's region design and snapshot-derived colour facts."}
    require(read_json(run / "image_plan.json") == expected_image, "image plan differs from current design", "STALE_EVIDENCE")
    return intent, plan, expected


def safe_run_path(run, relative):
    require(isinstance(relative, str) and relative and not Path(relative).is_absolute(), "artifact path must be relative to run", "ARTIFACT_PATH_INVALID")
    path = (run / relative).resolve()
    require(path.is_relative_to(run) and path != run, "artifact path escapes run", "ARTIFACT_PATH_INVALID")
    return path


def image_header(raw):
    # Intentionally a signature check only. It is never labelled decoded/opened.
    if raw.startswith(b"\x89PNG\r\n\x1a\n") and len(raw) >= 33 and raw[12:16] == b"IHDR":
        return "png"
    if raw.startswith(b"\xff\xd8\xff") and len(raw) >= 16 and raw.endswith(b"\xff\xd9"):
        return "jpg"
    if len(raw) >= 20 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "webp"
    fail("IMAGE_FORMAT_UNSUPPORTED", "supported image signatures: PNG, JPEG, WebP; full decoding belongs to visual inspection")


def description_for(entry, image_sha):
    return {**entry, "image_sha256": image_sha, "area_scope": "visible painted design shares, not measured physical surface area"}


def review_template(entry, image_sha, description_sha):
    review = {**{k: entry[k] for k in ("schema_version", "solution_id", "palette_binding", "source_image_sha256", "plan_sha256")},
            "scene_context": entry["scene_context"],
            "image_sha256": image_sha, "design_description_sha256": description_sha,
            "image_seen": False, "decision": "pending", "reviewer": {"kind": "current_model", "context": "same_context_limited"},
            "checks": {key: {"status": "pending", "region_ids": [], "observation": ""} for key in ASPECTS}, "issues": []}
    review["checks"]["regions"]["element_ids"] = []
    review["checks"]["typography"]["reading_ids"] = []
    return review


def register_image(args):
    run, manifest, snapshot, _ = current_run(args.run)
    intent, plan, candidates = load_design(run, manifest, snapshot)
    entry = next((s for s in candidates["solutions"] if s["solution_id"] == args.solution), None)
    require(entry is not None, "unknown solution ID")
    source = Path(args.image).resolve()
    require(source.is_file(), "image must exist", "IMAGE_OUTPUT_MISSING")
    original = intent["input"].get("path")
    require(not original or source != Path(original).resolve(), "cannot register the original as an edited output", "SOURCE_PROTECTION")
    raw = source.read_bytes()
    digest = sha(raw)
    require(digest != intent["input"].get("sha256"), "unchanged original is not a design output", "SOURCE_PROTECTION")
    ext = image_header(raw)
    delivery_path = run / "delivery_manifest.json"
    delivery = read_json(delivery_path) if delivery_path.exists() else {"schema_version": RUN_SCHEMA, "palette_binding": plan["palette_binding"], "outputs": [], "attempts": []}
    require(delivery.get("schema_version") == RUN_SCHEMA and delivery.get("palette_binding") == plan["palette_binding"], "delivery binding mismatch", "PALETTE_BINDING_MISMATCH")
    attempts = [a for a in delivery["attempts"] if a["solution_id"] == args.solution]
    kind = getattr(args, "attempt_kind", "initial")
    reason = getattr(args, "repair_reason", "")
    require(len(attempts) < 4, "four-attempt budget exhausted for this solution", "REPAIR_BUDGET_EXHAUSTED")
    require((not attempts and kind == "initial") or (attempts and kind in {"local_repair", "redesign"}), "initial is only for the first attempt")
    require(kind == "initial" or nonblank(reason), "repair/redesign must state a concrete change")
    require(kind != "local_repair" or sum(a["kind"] == kind for a in attempts) < 2, "two local repairs exhausted", "REPAIR_BUDGET_EXHAUSTED")
    require(kind != "redesign" or not any(a["kind"] == kind for a in attempts), "one redesign exhausted", "REPAIR_BUDGET_EXHAUSTED")
    # The author classifies the scope. Local area/colour corrections also change
    # the plan hash; hash freshness must not consume a redesign allowance.
    require(not any(a["image_sha256"] == digest for a in delivery["attempts"]), "duplicate current/previous image is not a new design attempt", "DUPLICATE_IMAGE")
    version = len(attempts) + 1
    sid = args.solution
    image_rel = f"images/{sid}-v{version}.{ext}"
    dest = safe_run_path(run, image_rel)
    require(not dest.exists(), "image version already exists", "RUN_EXISTS")
    write_bytes(dest, raw)
    desc_rel = f"descriptions/{sid}.json"
    desc_raw = write_json(safe_run_path(run, desc_rel), description_for(entry, digest))
    record = {**{k: entry[k] for k in ("schema_version", "solution_id", "label", "palette_binding", "source_image_sha256", "plan_sha256")},
        "image_path": image_rel, "image_sha256": digest, "image_check": "signature_only_not_full_decode",
        "design_description_path": desc_rel, "design_description_sha256": sha(desc_raw),
        "review_path": f"reviews/{sid}.json", "attempt": version}
    if entry["typography"]:
        text_rel = f"typography/{sid}.json"
        text_raw = write_json(safe_run_path(run, text_rel), {"schema_version": RUN_SCHEMA, "solution_id": sid,
            "palette_binding": plan["palette_binding"], "source_image_sha256": entry["source_image_sha256"],
            "plan_sha256": entry["plan_sha256"], "image_sha256": digest, "records": entry["typography"]})
        record.update(typography_path=text_rel, typography_sha256=sha(text_raw))
    delivery["outputs"] = [o for o in delivery["outputs"] if o["solution_id"] != sid] + [record]
    delivery["attempts"].append({"solution_id": sid, "attempt": version, "kind": kind, "reason": reason,
        "plan_sha256": entry["plan_sha256"], "source_image_sha256": entry["source_image_sha256"], "image_sha256": digest})
    write_json(safe_run_path(run, record["review_path"]), review_template(entry, digest, sha(desc_raw)))
    write_json(delivery_path, delivery)
    write_json(run / "state.json", {"stage": "register-image", "status": "pending"})
    return {"status": "pending_visual_review", "output": record, "attempts_remaining": 4-version}


def review_failures(review, entry, output, obj):
    errors = []
    if review.get("scene_context") != entry["scene_context"]:
        errors.append("STALE_REVIEW")
    for key in ("schema_version", "solution_id", "palette_binding", "source_image_sha256", "plan_sha256", "image_sha256", "design_description_sha256"):
        if review.get(key) != output.get(key):
            errors.append("STALE_REVIEW")
    if review.get("image_seen") is not True:
        errors.append("VISUAL_REVIEW_REQUIRED")
    if review.get("decision") != "accept":
        errors.append("VISUAL_REJECTED" if review.get("decision") == "reject" else "VISUAL_REVIEW_REQUIRED")
    reviewer = review.get("reviewer", {})
    if reviewer.get("kind") not in {"current_model", "human"} or not nonblank(reviewer.get("context")):
        errors.append("REVIEW_EVIDENCE_MISSING")
    regions = {r["region_id"] for c in entry["colors"] for r in c["regions"]}
    for aspect in ASPECTS:
        item = review.get("checks", {}).get(aspect, {})
        applicable = not ((aspect == "expression" and not obj["expression_relevant"]) or (aspect == "typography" and obj["text_status"] == "absent"))
        expected = "pass" if applicable else "not_applicable"
        if item.get("status") != expected:
            errors.append("VISUAL_CHECK_FAILED" if item.get("status") == "fail" else "REVIEW_EVIDENCE_MISSING")
        refs = item.get("region_ids", [])
        # Preserved lettering may have no painted region at all. It still needs
        # exact reading IDs and an actual observation below, not an N/A escape.
        visual_only_text = aspect == "typography" and all(r["mode"] == "visual" for r in entry["reading_coverage"])
        if not isinstance(refs, list) or any(r not in regions for r in refs) or (applicable and not refs and not visual_only_text) or not nonblank(item.get("observation")):
            errors.append("REVIEW_EVIDENCE_MISSING")
        coverage_field = {"regions": ("element_ids", "elements", "element_id"), "typography": ("reading_ids", "reading_targets", "reading_id")}.get(aspect)
        if coverage_field:
            field, collection, id_key = coverage_field
            actual = item.get(field)
            expected_ids = {v[id_key] for v in entry["observation_inventory"][collection]}
            if not isinstance(actual, list) or not all(isinstance(v, str) for v in actual) or len(set(actual)) != len(actual) or set(actual) != expected_ids:
                errors.append("REVIEW_COVERAGE_MISSING")
    issues = review.get("issues")
    if not isinstance(issues, list):
        errors.append("REVIEW_EVIDENCE_MISSING")
    elif issues:
        errors.append("VISUAL_ISSUES_UNRESOLVED")
    return sorted(set(errors))


def evaluate(run_arg):
    # All terminal entrypoints use this function and recompute current evidence.
    run, manifest, snapshot, _ = run_context(run_arg)
    binding = binding_for(manifest, run)
    result = {"schema_version": RUN_SCHEMA, "palette_binding": binding, "status": "blocked",
        "aesthetic_calibration": "uncalibrated", "accepted_solution_ids": [], "missing_solution_ids": [],
        "checks": [], "failures": [], "solution_count": None,
        "limitations": ["Image signatures are checked; full decoding and visual judgement are reviewer observations."]}
    try:
        require(manifest.get("run_schema_version") == RUN_SCHEMA, "legacy artifacts require a new plan/run", "LEGACY_RUN_REQUIRES_REPLAN")
        intent, plan, candidates = load_design(run, manifest, snapshot)
        result["solution_count"] = plan["solution_count"]
        expected_ids = {s["solution_id"] for s in candidates["solutions"]}
        result["missing_solution_ids"] = sorted(expected_ids)
        path = run / "delivery_manifest.json"
        delivery = read_json(path) if path.exists() else {"schema_version": RUN_SCHEMA, "palette_binding": binding, "outputs": [], "attempts": []}
        require(delivery.get("schema_version") == RUN_SCHEMA and delivery.get("palette_binding") == binding, "delivery belongs to another schema or binding", "PALETTE_BINDING_MISMATCH")
        outputs, attempts = delivery.get("outputs"), delivery.get("attempts")
        require(isinstance(outputs, list) and isinstance(attempts, list), "invalid delivery arrays")
        output_ids = [o["solution_id"] for o in outputs]
        require(len(set(output_ids)) == len(output_ids) and set(output_ids) <= expected_ids, "duplicate or unknown delivery solution", "DELIVERY_MISMATCH")
        require(all(a.get("solution_id") in expected_ids for a in attempts), "attempt references unknown solution")
        result["status"] = "partial" if outputs else "blocked"
        image_hashes = [o.get("image_sha256") for o in outputs]
        for entry in candidates["solutions"]:
            sid = entry["solution_id"]
            machine, visual = [], []
            output = next((o for o in outputs if o["solution_id"] == sid), None)
            try:
                require(output is not None, "no image registered", "IMAGE_OUTPUT_MISSING")
                for key in ("schema_version", "solution_id", "label", "palette_binding", "source_image_sha256", "plan_sha256"):
                    require(output.get(key) == entry[key], f"output {key} differs from current design", "STALE_EVIDENCE")
                image_path = safe_run_path(run, output["image_path"])
                require(image_path.is_file(), "image missing", "IMAGE_OUTPUT_MISSING")
                raw = image_path.read_bytes()
                image_header(raw)
                require(sha(raw) == output["image_sha256"], "image changed since registration", "STALE_EVIDENCE")
                require(sha(raw) != intent["input"].get("sha256") and str(image_path) != intent["input"].get("path"), "source cannot be its own output", "SOURCE_PROTECTION")
                require(image_hashes.count(output["image_sha256"]) == 1, "same image used for multiple solutions", "DUPLICATE_IMAGE")
                history = [a for a in attempts if a["solution_id"] == sid]
                require(1 <= len(history) <= 4 and [a.get("attempt") for a in history] == list(range(1, len(history)+1)), "invalid attempt history", "REPAIR_BUDGET_EXHAUSTED")
                require(history[0].get("kind") == "initial" and all(a.get("kind") in {"local_repair", "redesign"} and nonblank(a.get("reason")) for a in history[1:]), "invalid repair reasons/history")
                require(sum(a["kind"] == "local_repair" for a in history) <= 2 and sum(a["kind"] == "redesign" for a in history) <= 1, "attempt budgets exceeded", "REPAIR_BUDGET_EXHAUSTED")
                require(all(a.get("source_image_sha256") == entry["source_image_sha256"] for a in history), "attempt used a different original", "SOURCE_MISMATCH")
                require(history[-1]["image_sha256"] == output["image_sha256"] and history[-1]["plan_sha256"] == entry["plan_sha256"] and output.get("attempt") == len(history), "output not latest attempt", "STALE_EVIDENCE")
                desc_path = safe_run_path(run, output["design_description_path"])
                require(desc_path.is_file() and sha(desc_path.read_bytes()) == output["design_description_sha256"] and read_json(desc_path) == description_for(entry, output["image_sha256"]), "design description differs from current image or derived facts", "DESIGN_DESCRIPTION_MISMATCH")
                if entry["typography"]:
                    tp = safe_run_path(run, output.get("typography_path", ""))
                    expected_text = {"schema_version": RUN_SCHEMA, "solution_id": sid, "palette_binding": binding,
                        "source_image_sha256": entry["source_image_sha256"], "plan_sha256": entry["plan_sha256"],
                        "image_sha256": output["image_sha256"], "records": entry["typography"]}
                    require(tp.is_file() and sha(tp.read_bytes()) == output.get("typography_sha256") and read_json(tp) == expected_text, "typography differs from current design", "TEXT_PLAN_MISMATCH")
                else:
                    require("typography_path" not in output, "absent lettering must not create typography", "TEXT_PLAN_MISMATCH")
            except PaletteError as exc:
                machine.append(exc.code)
            except (OSError, KeyError, TypeError, ValueError):
                machine.append("ARTIFACT_INVALID")
            if output:
                try:
                    rp = safe_run_path(run, output.get("review_path", ""))
                    if rp.is_file():
                        visual = review_failures(read_json(rp), entry, output, plan["object"])
                    else:
                        visual = ["VISUAL_REVIEW_REQUIRED"]
                except (PaletteError, OSError, TypeError, KeyError, AttributeError):
                    visual = ["REVIEW_EVIDENCE_MISSING"]
            else:
                visual = ["VISUAL_REVIEW_REQUIRED"]
            accepted = not machine and not visual
            result["checks"].append({"solution_id": sid, "machine": "pass" if not machine else "fail",
                "visual": "pass" if not visual else "not_accepted", "accepted": accepted})
            result["failures"].extend({"solution_id": sid, "code": code, "gate": gate} for gate, codes in (("machine", machine), ("visual", visual)) for code in codes)
            if accepted:
                result["accepted_solution_ids"].append(sid)
        result["missing_solution_ids"] = sorted(expected_ids - set(result["accepted_solution_ids"]))
        if len(result["accepted_solution_ids"]) == plan["solution_count"]:
            result["status"] = "complete"
        if any(c["visual"] == "pass" for c in result["checks"]):
            result["aesthetic_calibration"] = "model_reviewed"
    except PaletteError as exc:
        result["failures"].append({"code": exc.code, "message": exc.message, "gate": "machine"})
        result["status"] = "blocked"
    except (OSError, KeyError, TypeError, ValueError, AttributeError) as exc:
        result["failures"].append({"code": "ARTIFACT_INVALID", "message": str(exc), "gate": "machine"})
        result["status"] = "blocked"
    return result


def persist_quality(run, quality, stage):
    # Legacy history is inspected without overwriting its artifacts.
    manifest, _ = load_manifest(run)
    if manifest.get("run_schema_version") == RUN_SCHEMA:
        write_json(run / "quality.json", quality)
        write_json(run / "delivery_quality.json", quality)
        write_json(run / "state.json", {"stage": stage, "status": quality["status"], "accepted_solution_ids": quality["accepted_solution_ids"]})


def verify(args):
    quality = evaluate(args.run)
    persist_quality(Path(args.run).resolve(), quality, "verify")
    return quality


def verify_delivery(args):
    return verify(args)


def export_run(args):
    run = Path(args.run).resolve()
    quality = evaluate(args.run)
    persist_quality(run, quality, "export")
    manifest, snapshot = load_manifest(run)
    if manifest.get("run_schema_version") != RUN_SCHEMA:
        return quality
    accepted = set(quality["accepted_solution_ids"])
    solutions = []
    if accepted:
        _, _, candidates = load_design(run, manifest, snapshot)
        solutions = [s for s in candidates["solutions"] if s["solution_id"] in accepted]
    write_json(run / "palettes.json", {"schema_version": RUN_SCHEMA, "palette_binding": quality["palette_binding"], "status": quality["status"], "solutions": solutions})
    write_json(run / "build_manifest.json", {"schema_version": RUN_SCHEMA, "runtime": "traditional-color-palette",
        "model_routing": "current user-selected model; no required planner/editor model", "gate": "current-artifacts-machine-and-visual"})
    return quality


def render(args):
    # Existing command remains a read-only handoff; a swatch proxy cannot satisfy image delivery.
    run, manifest, snapshot, _ = current_run(args.run)
    _, plan, candidates = load_design(run, manifest, snapshot)
    return {"status": "pending_image_edit", "image_plan_path": str(run / "image_plan.json"),
        "solution_ids": [s["solution_id"] for s in candidates["solutions"]],
        "source_image_sha256": plan["source_image_sha256"], "notice": "Use the host image tool from the original, then register-image. No images generated by this command."}


def resume_run(args):
    # No cached state can confer acceptance. Resuming never rewrites old runs.
    return evaluate(args.run)


def fork_run(args):
    old = Path(args.run).resolve()
    manifest, _ = load_manifest(old)
    result = preflight(argparse.Namespace(project_root=str(Path(args.out).resolve().parent.parent), palette=args.palette, out=args.out))
    write_json(Path(args.out).resolve() / "forked_from.json", {"run": str(old), "old_palette_content_sha256": manifest["palette_content_sha256"]})
    return result
