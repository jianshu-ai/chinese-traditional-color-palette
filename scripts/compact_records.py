"""Compact v0.10 authoring plus v0.9 reading; shared ledger in scene_pipeline.

No inferred permissions, fabricated review scores, or automatic aesthetic decisions.
"""
import copy


def template(s, state):
    count = state["target_count"]
    current = state["schema_version"] == s.VERSION
    return {"format": "compact-0.10" if current else "compact-0.9", "brief_binding": s.brief_binding(state),
            **({"target_count": count, "round_binding": s.dialogue.binding(s, state),
                "feedback_application": "" if state["dialogue"]["preferences"] else "首轮，按已确认目标设计。"} if current else {}),
            "diagnosis": {"purpose": "", "targets": [{"id": "subject", "goal_ids": [g["id"] for g in s.confirmed_brief(state)["goals"]],
                                                       "description": ""}], "preserve": [], "objective_checks": []},
            "solutions": [{"id": sid, "label": sid, "title": "", "design_logic": "", "ambition": degree,
                           "colors": [{"color_id": "", "role": "", "areas": [{"id": "body", "target_id": "subject", "location": ""}]}]}
                          for sid, degree in zip(s.labels(state), ["refine", "redesign", "redesign", "bold", "bold"] * (count // 5))]}


def validate(s, plan, snapshot):
    current = plan.get("format") == "compact-0.10"
    s.fields(plan, ["format", "brief_binding", "diagnosis", "solutions"] +
             (["target_count", "round_binding", "feedback_application"] if current else []), label="compact plan")
    s.require(plan["format"] in {"compact-0.9", "compact-0.10"}, "use current compact plan template")
    count = plan["target_count"] if current else 5
    s.require(type(count) is int and count in (5, 10), "plan count must be 5 or 10", "COUNT_INVALID")
    if current:
        s.nonblank(plan["feedback_application"], "how feedback changes this round design")
    d = plan["diagnosis"]
    s.fields(d, ["purpose", "targets", "preserve", "objective_checks"], ["fixed_colors"], "diagnosis")
    s.nonblank(d["purpose"], "design opportunity")
    s.string_list(d["preserve"], "specific preservation exceptions")
    targets = s.rows_by_id(d["targets"], "targets")
    s.require(targets, "locate the actual editing subjects", "TARGET_MISSING")
    for target in targets.values():
        s.fields(target, ["id", "goal_ids", "description"], label="target")
        s.string_list(target["goal_ids"], "goal links")
        s.require(len(target["goal_ids"]) == len(set(target["goal_ids"])), "duplicate goal link")
        s.nonblank(target["description"], "target location, ownership and task role")
    fixed = d.get("fixed_colors", {})
    s.require(isinstance(fixed, dict), "fixed_colors must be a mapping")
    for key, color in fixed.items():
        s.require(isinstance(key, str) and s.pc.ID_RE.fullmatch(key) and key not in targets, "invalid fixed reference")
        s.fields(color, ["rgb", "provenance"], label="fixed reference")
        s.fields(color["rgb"], ["R", "G", "B"], label="RGB")
        s.require(all(type(v) is int and 0 <= v <= 255 for v in color["rgb"].values()), "invalid RGB")
        s.nonblank(color["provenance"], "fixed reference provenance")
    checks = s.rows_by_id(d["objective_checks"], "objective checks")
    for check in checks.values():
        common = ["id", "kind", "reason", "required", "applicable"]
        if check.get("kind") == "contrast":
            s.fields(check, common + ["foreground", "background", "medium", "contrast_class"], ["minimum"], "contrast")
            s.require(check["medium"] in {"digital", "physical"}, "invalid contrast medium")
            classes = {"normal_text", "large_text", "non_text"} if check["medium"] == "digital" else {"contextual"}
            s.require(check["contrast_class"] in classes, "contrast class incompatible with medium")
            if "minimum" in check:
                n = check["minimum"]
                s.require(type(n) in (int, float) and s.math.isfinite(n) and 1 <= n <= 21, "invalid contrast threshold")
                s.require(n >= s.threshold(check), "cannot reduce digital contrast threshold")
            s.require(check["foreground"] != check["background"], "contrast needs distinct references")
        else:
            s.fields(check, common + ["description"], label="external check")
            s.require(check["kind"] == "external", "unknown objective method")
            s.nonblank(check["description"], "external check description")
        s.nonblank(check["reason"], "objective applicability reason")
        s.require(type(check["required"]) is bool and type(check["applicable"]) is bool, "invalid objective flags")
    facts = {c["color_id"]: c for c in snapshot["colors"] if c["enabled"]}
    solutions = s.rows_by_id(plan["solutions"], "solutions")
    s.require(set(solutions) == set(s.ALL_LABELS[:count]), "provide exactly A-E or A-J according to target_count")
    intents = []
    for sid, sol in solutions.items():
        s.fields(sol, ["id", "label", "title", "design_logic", "ambition", "colors"], ["search_evidence"], "solution")
        s.require(sol["label"] == sid, "label must match solution id")
        for key in ("title", "design_logic"):
            s.nonblank(sol[key], key)
        intents.append(sol["design_logic"].strip())
        s.require(sol["ambition"] in {"refine", "redesign", "bold"}, "invalid change direction")
        used, areas, covered = set(), set(), set()
        s.require(isinstance(sol["colors"], list) and sol["colors"], "selected palette colors required")
        for color in sol["colors"]:
            s.fields(color, ["color_id", "role", "areas"], label="color")
            cid = color["color_id"]
            s.require(cid in facts, "unknown or disabled palette ID: " + str(cid), "COLOR_UNKNOWN")
            s.require(cid not in used, "merge areas sharing the same color")
            used.add(cid)
            s.nonblank(color["role"], "color role")
            s.require(isinstance(color["areas"], list) and color["areas"], "locate color areas")
            for area in color["areas"]:
                s.fields(area, ["id", "target_id", "location"], label="color area")
                aid = area["id"]
                s.require(isinstance(aid, str) and s.pc.ID_RE.fullmatch(aid), "invalid area id")
                s.require(aid not in areas | set(targets) | set(fixed), "duplicate/ambiguous area reference")
                s.require(area["target_id"] in targets, "color references missing target", "TARGET_MISSING")
                s.nonblank(area["location"], "color placement")
                areas.add(aid)
                covered.add(area["target_id"])
        s.require(covered == targets.keys(), "colors omit an identified editing target", "TARGET_MISSING")
        for check in checks.values():
            if check["kind"] == "contrast":
                s.require(check["foreground"] in areas | set(fixed) and check["background"] in areas | set(fixed),
                          "use exact color areas or documented fixed colors for a reading pair", "READING_MISSING")
                s.require(s.planned_check(check, sol, facts, fixed)["status"] != "fail", "planned contrast is below its applicable threshold", "CONTRAST_LOW")
        if "search_evidence" in sol:
            search_result(s, sol, snapshot)
    s.require(len(set(intents)) == count, "each candidate needs a distinct design intention", "DUPLICATE_INTENT")
    return plan


def goals(s, state, plan):
    s.require_confirmed(state)
    s.require(plan["brief_binding"] == s.brief_binding(state), "plan does not bind current brief", "BINDING_STALE")
    if state["schema_version"] == s.VERSION:
        s.require(plan["target_count"] == state["target_count"], "plan count differs from authorized count", "COUNT_INVALID")
        s.require(plan["round_binding"] == s.dialogue.binding(s, state), "plan must incorporate current round and preferences", "BINDING_STALE")
    user_ids = {g["id"] for g in s.confirmed_brief(state)["goals"]}
    linked = {gid for target in plan["diagnosis"]["targets"] for gid in target["goal_ids"]}
    s.require(linked == user_ids, "locate every confirmed goal without inventing goals", "GOAL_MISSING")
    if state["plugin"]["id"] == "web":
        s.require(any(c["applicable"] and c["required"] for c in plan["diagnosis"]["objective_checks"]),
                  "web needs actual reading pairs, or an explicit required external/unknown check", "READING_MISSING")


def search_result(s, sol, snapshot):
    import relationship_search as rs
    e = sol["search_evidence"]
    s.fields(e, ["spec", "selection", "mode", "reason"], label="candidate adoption")
    s.require(e["mode"] in {"adopted", "adapted"}, "identify adoption or adjustment")
    s.nonblank(e["reason"], "candidate choice reason")
    s.require(isinstance(e["selection"], dict), "selection maps search roles to actual palette IDs")
    selected_ids = {c["color_id"] for c in sol["colors"]}
    s.require(all(cid is None or cid in selected_ids for cid in e["selection"].values()), "search evidence does not match final colors", "BINDING_STALE")
    features = s.pc.build_features(snapshot)
    candidates = rs.search_groups(features, e["spec"])
    final = rs.evaluate_selection(features, e["spec"], e["selection"])
    s.require(all(r["kind"] != "hard" or r["outcome"] == "met" for r in final["relations"]),
              "final selected colors violate a declared hard relation", "RELATION_FAILED")
    if e["mode"] == "adopted":
        ids = {rid: role["color_id"] for rid, role in final["roles"].items()}
        s.require(any({rid: role["color_id"] for rid, role in row["roles"].items()} == ids for row in candidates["candidates"]),
                  "adopted selection is not in returned candidates; record adapted with its reason", "CANDIDATE_MISSING")
    return {"mode": e["mode"], "reason": e["reason"], "search": candidates, "final": final}


def prompt(s, state, sid, snapshot, retry_reason):
    sol, brief = s.solution(state, sid), s.confirmed_brief(state)
    d = state["plan"]["diagnosis"]
    lines = ["Edit this original image into ONE finished colour-design concept. No added labels or swatches.",
             "目标：" + brief["summary"],
             "改善重点：" + "；".join(g["subject"] + "：" + g["outcome"] for g in brief["goals"]),
             "本款：" + sol["design_logic"],
             "主改范围：" + "；".join(t["description"] for t in d["targets"])]
    if state["schema_version"] == s.VERSION:
        lines.append("本轮反馈应用：" + state["plan"]["feedback_application"])
        for preference in state["dialogue"]["preferences"]:
            for key, label in (("likes", "延续"), ("dislikes", "避免")):
                for ref in preference[key]:
                    lines.append(f"{label}第{ref['round_number']}轮{ref['solution_id']}的配色意见：{ref['reason']}")
            if preference["focus"]:
                lines.append("修改重点：" + "；".join(preference["focus"]))
        lines.append("历史候选仅作配色偏好参照，形态、构图仍以当前原图为基准。")
    for label, key in (("可协同", "supporting_changes"), ("优先", "priorities")):
        if brief[key]:
            lines.append(label + "：" + "；".join(brief[key]))
    for c in s.derived_colors(sol, snapshot):
        lines.append(f"{c['name']} {c['hex']}（{c['role']}）→ " + "；".join(a["location"] for a in c["areas"]))
    keep = list(dict.fromkeys(brief["preserve"] + d["preserve"]))
    lines.append("保留形态、文字内容、材质和摄影光照；这不限制已授权区域的换色与涂装分区。")
    if keep:
        lines.append("本图保持：" + "；".join(keep))
    if retry_reason:
        lines.append("纠正：" + retry_reason)
    return "\n".join(lines) + "\n"


def review_template(s, state, call):
    return {"binding": s.review_binding(state, call), "image_seen": False, "reviewer": "", "decision": "pending", "reason": "",
            "judgment": {"comparison": "pending", "evidence": "", "tradeoff": ""},
            "goal_checks": [{"goal_id": g["id"], "status": "unknown", "note": ""} for g in s.confirmed_brief(state)["goals"]],
            "content_check": {"status": "unknown", "note": ""}, "failure_stage": "none",
            "measurements": [], "limitations": [], "warnings": []}


def assess(s, run, state, snapshot, sid, r):
    call = s.current_call(state, sid)
    s.require(call is not None, "no current image", "IMAGE_MISSING")
    s.check_call_binding(run, state, call, True)
    s.fields(r, ["binding", "image_seen", "reviewer", "decision", "reason", "judgment", "goal_checks", "content_check",
                 "failure_stage", "measurements", "limitations", "warnings"], label="compact review")
    s.require(r["binding"] == s.review_binding(state, call), "review binding changed", "BINDING_STALE")
    s.require(r["image_seen"] is True, "review requires viewing the current image", "IMAGE_NOT_SEEN")
    s.nonblank(r["reviewer"], "reviewer")
    s.nonblank(r["reason"], "brief verdict")
    s.require(r["decision"] in {"accept", "reject"}, "decision must be accept/reject")
    s.fields(r["judgment"], ["comparison", "evidence", "tradeoff"], label="visual comparison")
    s.require(r["judgment"]["comparison"] in {"better", "similar", "worse"}, "compare with original, not author intent")
    s.nonblank(r["judgment"]["evidence"], "observed visual evidence")
    s.require(isinstance(r["judgment"]["tradeoff"], str), "tradeoff must be text")
    s.require(r["failure_stage"] in {"none", "goal", "diagnosis", "execution"}, "invalid failure attribution")
    s.require(isinstance(r["goal_checks"], list), "goal checks must be an array")
    ids = []
    for g in r["goal_checks"]:
        s.fields(g, ["goal_id", "status", "note"], label="goal check")
        ids.append(g["goal_id"])
        s.require(g["status"] in {"pass", "fail", "unknown"}, "invalid goal status")
        s.nonblank(g["note"], "observed goal evidence")
    s.require(len(set(ids)) == len(ids) and set(ids) == {g["id"] for g in s.confirmed_brief(state)["goals"]}, "review each confirmed goal", "GOAL_MISSING")
    s.fields(r["content_check"], ["status", "note"], label="important content and scope")
    s.require(r["content_check"]["status"] in {"pass", "fail", "unknown"}, "invalid content status")
    s.nonblank(r["content_check"]["note"], "content/scope observation, including reading where relevant")
    for key in ("limitations", "warnings"):
        s.string_list(r[key], key)
    objective = s.objective_results(state, s.solution(state, sid), snapshot, call, r["measurements"])
    if r["decision"] == "accept":
        s.require(r["failure_stage"] == "none" and all(g["status"] == "pass" for g in r["goal_checks"]), "unfulfilled goal cannot be accepted", "GOAL_FAILED")
        s.require(r["judgment"]["comparison"] != "worse" and r["content_check"]["status"] == "pass", "unresolved visual/content failure", "VISUAL_REJECTED")
        s.require(objective["failed_count"] == 0, "measured objective failure", "OBJECTIVE_FAILED")
        if any(c["required"] and c["status"] == "unknown" for c in objective["checks"]):
            s.require(r["limitations"], "unknown required checks need explicit limitations", "LIMITATION_MISSING")
    else:
        s.require(r["failure_stage"] != "none", "identify the correction stage")
    return {"source": "model_visual_review", "calibrated": False, "judgment": copy.deepcopy(r["judgment"])}, objective
