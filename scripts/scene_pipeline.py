"""v0.10 dialogue/round workflow. No image API or automatic beauty judgment.

Author one short plan, reserve calls before using the host image tool, then
register and review real files. Daily and batch callers use this same module.
"""
from __future__ import annotations

import copy
import functools
import io
import math
import sys
import compact_records as compact
import dialogue
from datetime import datetime, timezone
from pathlib import Path

import palette_core as pc

VERSION = "0.10.0"
COMPACT_VERSIONS = {"0.9.0", VERSION}
GOAL_VERSIONS = {"0.8.0", *COMPACT_VERSIONS}
INTERFACE = "1.0"
PLUGIN_IDS = {"web", "built_environment", "print_packaging", "apparel", "toy_product", "generic"}
LABELS = list("ABCDE")  # frozen legacy labels; current runs use labels(state)
ALL_LABELS = list("ABCDEFGHIJ")


def labels(state):
    return dialogue.labels(state) if state["schema_version"] == VERSION else LABELS


def progress(state):
    dialogue.refresh(sys.modules[__name__], state)
    return dialogue.public(state) if state["schema_version"] == VERSION else {}

WEIGHTS = {"beauty": 0.5, "organization": 0.2, "scene_fit": 0.2, "material_naturalness": 0.1}
GATES = ("aesthetics", "targets", "permissions", "content", "consistency", "distinctiveness")
SKILL_ROOT = Path(__file__).resolve().parents[1]


def require(condition, message, code="SCENE_INVALID"):
    if not condition:
        pc.fail(code, message)


def digest(value):
    return pc.sha(pc.canonical_bytes(value))


def read_json(path):
    path = Path(path)
    return pc.parse_json_bytes(path.read_bytes(), str(path))


def fields(value, required, optional=(), label="record"):
    require(isinstance(value, dict), f"{label} must be an object")
    require(set(required) <= value.keys(), f"{label} missing {sorted(set(required) - value.keys())}")
    require(value.keys() <= set(required) | set(optional), f"{label} unknown fields {sorted(value.keys() - set(required) - set(optional))}")


def nonblank(value, label):
    require(isinstance(value, str) and bool(value.strip()), f"{label} must contain text")


def string_list(value, label, nonempty=False):
    require(isinstance(value, list), f"{label} must be an array")
    require(not nonempty or value, f"{label} must not be empty")
    for item in value:
        nonblank(item, label)


def rows_by_id(rows, label):
    require(isinstance(rows, list), f"{label} must be an array")
    out = {}
    for row in rows:
        require(isinstance(row, dict), f"{label} entries must be objects")
        ident = row.get("id")
        require(isinstance(ident, str) and pc.ID_RE.fullmatch(ident), f"{label} invalid id")
        require(ident not in out, f"{label} duplicate id {ident}")
        out[ident] = row
    return out


def inside(run, path):
    resolved = Path(path).resolve()
    require(resolved.is_relative_to(run.resolve()), f"artifact escapes run: {path}", "ARTIFACT_PATH")
    return resolved


def image_data(path):
    """Fully decode, not just a signature check. Pixels used only for checks."""
    try:
        from PIL import Image, ImageCms
    except ImportError:
        pc.fail("PILLOW_REQUIRED", "Pillow is required for image decoding; use a Python with Pillow installed")
    path = Path(path)
    raw = path.read_bytes()
    try:
        with Image.open(io.BytesIO(raw)) as probe:
            require(probe.format in {"PNG", "JPEG", "WEBP"}, "use PNG, JPEG or WebP", "IMAGE_INVALID")
            probe.verify()
        with Image.open(io.BytesIO(raw)) as src:
            src.load()
            require(getattr(src, "n_frames", 1) == 1, "animated images are not scene evidence", "IMAGE_INVALID")
            fmt = src.format
            alpha = src.convert("RGBA").getchannel("A") if "A" in src.getbands() or "transparency" in src.info else None
            profile = src.info.get("icc_profile")
            rgb = src.convert("RGB")
            if profile:
                rgb = ImageCms.profileToProfile(rgb, ImageCms.ImageCmsProfile(io.BytesIO(profile)), ImageCms.createProfile("sRGB"), outputMode="RGB")
            info = {"path": str(path.resolve()), "sha256": pc.sha(raw), "width": src.width,
                    "height": src.height, "format": fmt,
                    "pixel_sha256": pc.sha(f"{src.width}x{src.height}".encode() + rgb.tobytes() + (alpha.tobytes() if alpha else b"")),
                    "color_space": "sRGB", "color_space_source": "ICC converted to sRGB" if profile else "assumed sRGB; no embedded ICC"}
            return info, rgb, alpha
    except pc.PaletteError:
        raise
    except Exception as exc:
        pc.fail("IMAGE_INVALID", f"cannot fully decode {path}: {exc}")


def select_plugin(skill_root, requested):
    root = Path(skill_root).resolve()
    index = read_json(root / "plugins/index.json")
    fields(index, ["interface_version", "plugins"], label="plugin index")
    plugins = rows_by_id(index["plugins"], "plugins")
    for pid, row in plugins.items():
        fields(row, ["id", "path", "version", "summary"], ["enabled"], "plugin")
        require(row["path"] == f"plugins/{pid}.md", "plugin path must be plugins/<id>.md relative to Skill root", "PLUGIN_INVALID")
        require((root / row["path"]).resolve().is_relative_to(root), "plugin path/symlink escapes Skill root", "PLUGIN_INVALID")
        nonblank(row["version"], "plugin version")
        nonblank(row["summary"], "plugin summary")
        require(isinstance(row.get("enabled", True), bool), "enabled must be boolean")
    require(index["interface_version"] == INTERFACE, "unsupported plugin index interface; no compatible fallback can be established", "PLUGIN_INTERFACE")

    def usable(pid):
        row = plugins.get(pid)
        return bool(row and row.get("enabled", True) and row["version"].startswith("0.9.") and (root / row["path"]).is_file())

    fallback = None
    chosen = requested
    if not usable(chosen):
        chosen = "generic"
        fallback = f"requested plugin {requested!r} unknown, missing, disabled or incompatible; using generic"
    require(usable(chosen), "generic fallback is unavailable", "PLUGIN_MISSING")
    row = copy.deepcopy(plugins[chosen])
    return index, row, (root / row["path"]).read_bytes(), fallback


def project_lock(fn):
    @functools.wraps(fn)
    def locked(run, project_root, *args, **kwargs):
        import fcntl
        import os
        directory = Path(project_root).resolve() / ".palette-cases"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / ".init.lock"
        with path.open("a") as fh:
            os.chmod(path, 0o644)
            fcntl.flock(fh, fcntl.LOCK_EX)
            return fn(run, project_root, *args, **kwargs)
    return locked


@project_lock
def init_run(run, project_root, input_image, case_id, label, plugin_id="generic", palette=None, retry_budget=2, skill_root=SKILL_ROOT, run_mode="live", count=None, purpose="design"):
    run, project = Path(run).resolve(), Path(project_root).resolve()
    require(not run.exists() or (run.is_dir() and not any(run.iterdir())), "init requires a new empty directory", "RUN_EXISTS")
    require(isinstance(case_id, str) and pc.ID_RE.fullmatch(case_id), "case_id must be a stable identifier")
    nonblank(label, "label")
    require(run_mode in {"live", "test"}, "run_mode must be live or test")
    require(isinstance(retry_budget, int) and not isinstance(retry_budget, bool) and 0 <= retry_budget <= 2, "retry budget must be 0..2")
    require(count is None or type(count) is int and count in (5, 10), "choose 5 or 10", "COUNT_INVALID")
    require(purpose in {"design", "plan", "diagnose"}, "invalid task purpose")
    source_path = Path(input_image).resolve()
    require(not source_path.is_relative_to(run), "source image must be outside new run")
    source, _, _ = image_data(source_path)
    index, plugin, plugin_raw, fallback = select_plugin(skill_root, plugin_id)
    palette_path = Path(palette).resolve() if palette else project / "palettes/current.palette.json"
    raw = pc.read_stable(palette_path)
    checked = pc.validate_palette(pc.parse_json_bytes(raw, str(palette_path)))
    snapshot = checked["palette"]
    canon = pc.canonical_bytes(snapshot)
    manifest = {"run_id": run.name, "run_schema_version": VERSION, "schema_version": snapshot["schema_version"],
                "palette_id": snapshot["palette_id"], "display_name": snapshot["display_name"], "color_space": snapshot["color_space"],
                "palette_path": str(palette_path), "palette_source_sha256": pc.sha(raw), "palette_content_sha256": pc.sha(canon),
                "canonicalizer_version": pc.CANONICALIZER, "feature_generator_version": pc.FEATURES,
                "snapshot_path": "inputs/palette.snapshot.json", "enabled_count": checked["enabled_count"], "warnings": checked["warnings"]}
    ext = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}[source["format"]]
    # A directory rename is not a fresh budget. The project registry routes the
    # same case (or same live source) to its original, possibly missing, ledger.
    registry = project / ".palette-cases" / (run_mode + "-" + case_id + ".json")
    if registry.exists():
        require(False, "case already registered; resume " + read_json(registry)["run"], "CASE_EXISTS")
    source_registry = project / ".palette-cases" / ("live-source-" + source["sha256"] + ".json")
    if run_mode == "live" and source_registry.exists():
        require(False, "source already registered; resume " + read_json(source_registry)["run"], "CASE_EXISTS")
    frozen_source = run / ("inputs/source" + ext)
    frozen_plugin = run / "inputs/plugin.md"
    pc.write_bytes(frozen_source, source_path.read_bytes())
    require(pc.sha(frozen_source.read_bytes()) == source["sha256"], "source changed during init", "SOURCE_CHANGED")
    pc.write_bytes(run / "inputs/palette.source.json", raw)
    pc.write_bytes(run / "inputs/palette.snapshot.json", canon)
    pc.write_json(run / "palette.manifest.json", manifest)
    pc.write_bytes(frozen_plugin, plugin_raw)
    pc.write_json(run / "inputs/plugins.index.json", index)
    state = {"schema_version": VERSION, "case_id": case_id, "label": label, "target_count": count,
             "source_image": {**source, "path": str(frozen_source), "original_path": str(source_path)},
             "palette_binding": pc.binding_for(manifest, run),
             "plugin": {"id": plugin["id"], "version": plugin["version"], "path": str(frozen_plugin), "sha256": pc.sha(plugin_raw),
                        "requested_id": plugin_id, "fallback_reason": fallback, "index_sha256": pc.sha((run / "inputs/plugins.index.json").read_bytes())},
             "retry_budget": retry_budget, "plan": None, "attempts": [], "reviews": {},
             "run_mode": run_mode, "brief": None, "reconfirmation_required": False,
             "dialogue": dialogue.initial(count, purpose)}
    save(run, state)
    pc.write_json(registry, {"run": str(run), "source_sha256": source["sha256"], "case_id": case_id})
    if run_mode == "live":
        pc.write_json(source_registry, {"run": str(run), "source_sha256": source["sha256"], "case_id": case_id})
    return {**progress(state), "run": str(run), "status": "partial", "brief_status": "awaiting_proposal", "run_mode": run_mode, "plugin": state["plugin"], "source_image": state["source_image"], "palette_binding": state["palette_binding"], "target_count": count}


def load(run):
    run = Path(run).resolve()
    require((run / "scene.json").is_file(), "not a scene run; historical runs are read-only", "SCENE_MISSING")
    state = read_json(run / "scene.json")
    require(state.get("schema_version") in {VERSION, "0.9.0", "0.8.0", "0.7.0"}, "unsupported scene version", "SCENE_VERSION")
    manifest, snapshot = pc.load_manifest(run)
    require(state["palette_binding"] == pc.binding_for(manifest, run), "palette binding changed", "BINDING_STALE")
    source = state["source_image"]
    require(pc.sha(inside(run, source["path"]).read_bytes()) == source["sha256"], "frozen source changed", "BINDING_STALE")
    plugin = state["plugin"]
    require(pc.sha(inside(run, plugin["path"]).read_bytes()) == plugin["sha256"], "frozen plugin changed", "BINDING_STALE")
    require(pc.sha((run / "inputs/plugins.index.json").read_bytes()) == plugin["index_sha256"], "frozen index changed", "BINDING_STALE")
    if state["schema_version"] in GOAL_VERSIONS:
        validate_brief_record(run, state)
    if state["schema_version"] == VERSION:
        dialogue.validate_state(sys.modules[__name__], run, state)
    if state["plan"] is not None:
        require(digest(state["plan"]) == state.get("plan_record_sha256"), "plan edited outside plan command", "BINDING_STALE")
        if state["schema_version"] == VERSION:
            require(state["plan"].get("format") == "compact-0.10", "current runs require compact records")
        validate_plan(state["plan"], snapshot)
    return run, state, snapshot


def mutation(fn):
    @functools.wraps(fn)
    def locked(run, *args, **kwargs):
        import fcntl
        import os
        run = Path(run).resolve()
        require((run / "scene.json").is_file(), "not a scene run", "SCENE_MISSING")
        require(read_json(run / "scene.json").get("schema_version") == VERSION,
                "historical runs are read-only; do not backfill confirmation", "LEGACY_READ_ONLY")
        with (run / ".scene.lock").open("a") as fh:
            os.chmod(run / ".scene.lock", 0o644)
            fcntl.flock(fh, fcntl.LOCK_EX)
            return fn(run, *args, **kwargs)
    return locked


def save(run, state):
    dialogue.refresh(sys.modules[__name__], state)
    pc.write_json(run / "scene.json", state)


@mutation
def submit_dialogue(run, event):
    run, state, _ = load(run)
    dialogue.apply_event(sys.modules[__name__], run, state, event)
    save(run, state)
    return {**progress(state), "target_count": state["target_count"],
            "limitation": "Caller-interpreted conversation evidence, not authenticated authorization; no image API invoked."}


def dialogue_template(run):
    _, state, _ = load(run)
    return {"action": "start" if state.get("dialogue", {}).get("round_number", 0) == 0 else "continue",
            "source": "test_fixture" if state.get("run_mode") == "test" else "conversation",
            "user_reply": "", "message_reference": "", "target_count": None,
            "feedback": {"likes": [], "dislikes": [], "focus": [], "no_new_feedback": False}}


def brief_template(run):
    _, state, _ = load(run)
    require(state["schema_version"] == VERSION, "historical runs are read-only", "LEGACY_READ_ONLY")
    return {"summary": "", "goals": [{"id": "main-goal", "subject": "", "outcome": ""}],
            "supporting_changes": [], "preserve": [], "priorities": []}


def validate_brief(payload):
    fields(payload, ["summary", "goals", "supporting_changes", "preserve", "priorities"], label="brief")
    nonblank(payload["summary"], "brief summary")
    goals = rows_by_id(payload["goals"], "confirmed goals")
    require(goals, "at least one user goal is required", "GOAL_MISSING")
    for goal in goals.values():
        fields(goal, ["id", "subject", "outcome"], label="goal")
        nonblank(goal["subject"], "goal subject")
        nonblank(goal["outcome"], "goal outcome")
    for key in ("supporting_changes", "preserve", "priorities"):
        string_list(payload[key], key)


def validate_brief_record(run, state):
    require(state.get("run_mode") in {"live", "test"}, "invalid run_mode")
    brief = state.get("brief")
    if brief is None:
        return
    payload = brief["record"]
    require(digest(payload) == brief["record_sha256"], "brief changed outside brief command", "BINDING_STALE")
    require(read_json(inside(run, brief["path"])) == payload, "brief record changed", "BINDING_STALE")
    require(payload["source_sha256"] == state["source_image"]["sha256"], "brief belongs to another source", "BINDING_STALE")
    validate_brief(payload["content"])
    receipt = brief.get("confirmation")
    if receipt:
        require(digest(receipt["record"]) == receipt["sha256"] and
                read_json(inside(run, receipt["path"])) == receipt["record"], "confirmation record changed", "BINDING_STALE")
        require(receipt["record"]["brief_sha256"] == brief["record_sha256"], "confirmation belongs to another brief", "BINDING_STALE")
        validate_receipt(receipt["record"], state, stored=True)


def brief_status(state):
    if state["schema_version"] not in GOAL_VERSIONS:
        return "legacy_unconfirmed"
    brief = state.get("brief")
    if not brief:
        return "awaiting_proposal"
    if not brief.get("confirmation") or state.get("reconfirmation_required"):
        return "awaiting_confirmation"
    return "test_confirmed" if state["run_mode"] == "test" else "confirmed"


def confirmed_brief(state):
    if brief_status(state) not in {"confirmed", "test_confirmed"}:
        return None
    brief = state["brief"]
    return {**copy.deepcopy(brief["record"]["content"]), "version": brief["record"]["version"],
            "record_sha256": brief["record_sha256"], "source_sha256": brief["record"]["source_sha256"],
            "confirmation": copy.deepcopy(brief["confirmation"]["record"])}


def require_confirmed(state):
    require(brief_status(state) in {"confirmed", "test_confirmed"},
            "show scope/options and record an actual user reply before planning or image calls", "CONFIRMATION_REQUIRED")


def brief_binding(state):
    require_confirmed(state)
    return {"record_sha256": state["brief"]["record_sha256"],
            "confirmation_sha256": state["brief"]["confirmation"]["sha256"]}


@mutation
def submit_brief(run, content):
    run, state, _ = load(run)
    validate_brief(content)
    old = state.get("brief")
    if old and old["record"]["content"] == content and not state.get("reconfirmation_required"):
        return {"brief_sha256": old["record_sha256"], "brief_status": brief_status(state), **progress(state)}
    require(not any(a["status"] == "pending" for a in state["attempts"]), "resolve pending calls before changing scope", "CALL_PENDING")
    record = {"version": old["record"]["version"] + 1 if old else 1,
              "source_sha256": state["source_image"]["sha256"], "content": copy.deepcopy(content)}
    sha = digest(record)
    path = run / "briefs" / f"{sha}.json"
    pc.write_json(path, record)
    state["brief"] = {"record": record, "record_sha256": sha, "path": str(path), "confirmation": None}
    state["reconfirmation_required"] = False
    save(run, state)
    return {"brief_sha256": sha, "version": record["version"], "brief_status": "awaiting_confirmation",
            **progress(state), "instruction": "record existing explicit requirements without asking twice; ask only missing scope"}


def validate_receipt(receipt, state, stored=False):
    fields(receipt, ["brief_sha256", "user_reply", "source", "message_reference"] + (["recorded_at"] if stored else []), label="confirmation")
    for key in ("user_reply", "message_reference"):
        nonblank(receipt[key], key)
    require(receipt["source"] in {"conversation", "test_fixture"}, "unsupported confirmation source")
    require(receipt["source"] == ("test_fixture" if state["run_mode"] == "test" else "conversation"),
            "test confirmations may only be used in explicitly marked test runs", "CONFIRMATION_SOURCE")


@mutation
def confirm_brief(run, receipt):
    run, state, _ = load(run)
    validate_receipt(receipt, state)
    brief = state.get("brief")
    require(brief is not None and receipt["brief_sha256"] == brief["record_sha256"], "confirm the currently displayed brief", "BINDING_STALE")
    old = brief.get("confirmation")
    if old and state.get("reconfirmation_required"):
        require(receipt["message_reference"] != old["record"]["message_reference"],
                "a newly requested confirmation needs a fresh actual user reply, not the previous receipt", "CONFIRMATION_REQUIRED")
    if old and not state.get("reconfirmation_required"):
        require({k: v for k, v in old["record"].items() if k != "recorded_at"} == receipt,
                "brief already confirmed; submit a revised brief for a changed scope", "ALREADY_CONFIRMED")
        return {"brief_status": brief_status(state), "brief_binding": brief_binding(state), **progress(state)}
    record = {**copy.deepcopy(receipt), "recorded_at": datetime.now(timezone.utc).isoformat()}
    sha = digest(record)
    path = run / "confirmations" / f"{sha}.json"
    pc.write_json(path, record)
    brief["confirmation"] = {"record": record, "sha256": sha, "path": str(path)}
    state["reconfirmation_required"] = False
    if state["dialogue"]["authorization"] and not state["dialogue"]["delivery"] and not state["dialogue"]["stopped"]:
        state["dialogue"]["authorization"]["brief_binding"] = brief_binding(state)
    dialogue.maybe_start(sys.modules[__name__], run, state)
    save(run, state)
    return {**progress(state), "brief_status": brief_status(state), "brief_binding": brief_binding(state),
            "limitation": "Records caller-supplied conversation evidence; not authenticated user identity."}


@mutation
def route_plugin(run, plugin_id, skill_root=SKILL_ROOT):
    """Finalize provisional classification without changing goal or resetting calls."""
    run, state, _ = load(run)
    require_confirmed(state)
    require(not state["attempts"] and not state["dialogue"]["history"], "plugin is frozen across rounds after first call; retain budget and report scope issue", "PLUGIN_FROZEN")
    index, plugin, raw, fallback = select_plugin(skill_root, plugin_id)
    pc.write_bytes(run / "inputs/plugin.md", raw)
    pc.write_json(run / "inputs/plugins.index.json", index)
    state["plugin"] = {"id": plugin["id"], "version": plugin["version"], "path": str(run / "inputs/plugin.md"),
                       "sha256": pc.sha(raw), "requested_id": plugin_id, "fallback_reason": fallback,
                       "index_sha256": pc.sha((run / "inputs/plugins.index.json").read_bytes())}
    state["plan"] = None
    state.pop("plan_record_sha256", None)
    save(run, state)
    return {"plugin": state["plugin"], "brief_status": brief_status(state), "status": "replan_required"}


def plan_template(run):
    _, state, _ = load(run)
    require_confirmed(state)
    if state["schema_version"] == VERSION:
        dialogue.require_open(sys.modules[__name__], state)
    if state["schema_version"] in COMPACT_VERSIONS:
        return compact.template(sys.modules[__name__], state)
    permissions = {k: {"mode": "preserve" if k != "color" else "modify", "source": "observation", "reason": ""}
                   for k in ("geometry", "text", "material", "color")}
    return {"brief_binding": brief_binding(state), "diagnosis": {"purpose": "", "strengths": [], "problems": [], "reading_targets": [],
                          "goal_links": [{"goal_id": g["id"], "object_ids": ["subject"], "target_ids": ["main-target"]} for g in state["brief"]["record"]["content"]["goals"]],
                          "objects": [{"id": "subject", "label": "", "relationship": "", "permissions": permissions}],
                          "targets": [{"id": "main-target", "object_id": "subject", "description": ""}], "objective_checks": []},
            "solutions": [{"id": sid, "label": sid, "title": "", "design_logic": "",
                           "actions": [{"target_id": "main-target", "description": ""}],
                           "colors": [{"color_id": "", "role": "", "areas": [{"id": "body", "object_id": "subject", "location": "", "target_ids": ["main-target"]}]}],
                           "preserve": [], "failure_signs": ""} for sid in LABELS]}


def search(run, query="", lightness=None, chroma=None, hue=None, limit=12, offset=0):
    _, state, snapshot = load(run)
    require(1 <= limit <= 100 and offset >= 0, "limit must be 1..100 and offset nonnegative")
    rows = []
    for item in pc.build_features(snapshot):
        if not item["enabled"] or (query and query.casefold() not in (item["color_id"] + " " + item["name"]).casefold()):
            continue
        l, c, h = item["oklch"]
        if lightness and not lightness[0] <= l <= lightness[1]:
            continue
        if chroma and not chroma[0] <= c <= chroma[1]:
            continue
        if hue and (h is None or not (hue[0] <= h <= hue[1] if hue[0] <= hue[1] else h >= hue[0] or h <= hue[1])):
            continue
        rows.append(item)
    return {"palette_binding": state["palette_binding"], "total": len(rows), "colors": rows[offset:offset + limit]}


def validate_plan(plan, snapshot):
    if plan.get("format") in {"compact-0.9", "compact-0.10"}:
        return compact.validate(sys.modules[__name__], plan, snapshot)
    fields(plan, ["diagnosis", "solutions"], ["brief_binding"], label="plan")
    d = plan["diagnosis"]
    fields(d, ["purpose", "strengths", "problems", "objects", "targets", "reading_targets", "objective_checks"], ["limitations", "goal_links"], label="diagnosis")
    nonblank(d["purpose"], "purpose")
    string_list(d["strengths"], "strengths")
    colors = {c["color_id"]: c for c in snapshot["colors"] if c["enabled"]}
    objects = rows_by_id(d["objects"], "objects")
    require(objects, "diagnosis needs observed objects")
    for obj in objects.values():
        fields(obj, ["id", "label", "permissions"], ["scope", "relationship"], label="object")
        require(obj.get("scope", "task") in {"task", "out_of_scope"}, "object scope must be task or out_of_scope")
        nonblank(obj["label"], "object label")
        fields(obj["permissions"], ["geometry", "text", "material", "color"], label="permissions")
        for dimension, permission in obj["permissions"].items():
            fields(permission, ["mode", "source", "reason"], ["locked_color_id"] if dimension == "color" else [], "permission")
            require(permission["mode"] in ({"preserve", "modify", "not_applicable"} if dimension == "text" else {"preserve", "modify"}), f"invalid {dimension} permission")
            require(permission["source"] in {"user", "observation", "assumption"}, "permission source must distinguish user, observation and assumption")
            nonblank(permission["reason"], "permission reason")
            if dimension != "color" and permission["mode"] == "modify":
                require(permission["source"] == "user", "geometry/text/material modification needs user-sourced authorization", "PERMISSION_CONFLICT")
            if "locked_color_id" in permission:
                require(permission["locked_color_id"] in colors, "locked color is not enabled in snapshot", "COLOR_UNKNOWN")
                require(permission["source"] == "user", "exact color lock requires user source", "PERMISSION_CONFLICT")
        if obj.get("scope") == "out_of_scope":
            require(all(p["mode"] in {"preserve", "not_applicable"} for p in obj["permissions"].values()), "out-of-scope objects must remain preserved", "PERMISSION_CONFLICT")
            require(d.get("limitations"), "out-of-scope objects need an explicit diagnosis limitation")
    string_list(d.get("limitations", []), "diagnosis limitations")
    readings = rows_by_id(d["reading_targets"], "reading_targets")
    combinations = set()
    for reading in readings.values():
        fields(reading, ["id", "text_object_id", "background_object_id", "state", "description"], label="reading target")
        text_id, bg_id = reading["text_object_id"], reading["background_object_id"]
        require(text_id in objects and bg_id in objects and text_id != bg_id, "reading target must reference distinct observed text/background objects")
        require(objects[text_id]["permissions"]["text"]["mode"] != "not_applicable", "reading target references a non-text object")
        nonblank(reading["state"], "reading state")
        nonblank(reading["description"], "reading description")
        combo = (text_id, bg_id, reading["state"])
        require(combo not in combinations, "duplicate reading text/background/state combination")
        combinations.add(combo)
    covered_text = {r["text_object_id"] for r in readings.values()}
    required_text = {oid for oid, obj in objects.items() if obj.get("scope", "task") == "task" and obj["permissions"]["text"]["mode"] != "not_applicable"}
    require(required_text <= covered_text, "task text objects need actual reading targets even when their color is preserved", "READING_MISSING")
    targets = rows_by_id(d["targets"], "targets")
    for target in targets.values():
        fields(target, ["id", "object_id", "description"], label="target")
        require(target["object_id"] in objects, "target references unknown object")
        nonblank(target["description"], "target description")
        require(objects[target["object_id"]]["permissions"]["color"]["mode"] == "modify", f"required target {target['id']} is color locked; resolve scope conflict", "PERMISSION_CONFLICT")
    for problem in rows_by_id(d["problems"], "problems").values():
        fields(problem, ["id", "description", "target_ids"], label="problem")
        nonblank(problem["description"], "problem description")
        string_list(problem["target_ids"], "problem targets", True)
        require(set(problem["target_ids"]) <= targets.keys(), "problem references missing target", "TARGET_MISSING")
    checks = rows_by_id(d["objective_checks"], "objective_checks")
    for check in checks.values():
        kind = check.get("kind")
        common = ["id", "kind", "reason", "required", "applicable"]
        if kind == "contrast":
            fields(check, common + ["foreground", "background", "medium", "contrast_class"], ["minimum"], "contrast check")
            require(check["medium"] in {"digital", "physical"}, "invalid contrast medium")
            require(check["contrast_class"] in ({"normal_text", "large_text", "non_text"} if check["medium"] == "digital" else {"contextual"}), "contrast class must match medium")
            if "minimum" in check:
                require(isinstance(check["minimum"], (int, float)) and not isinstance(check["minimum"], bool) and math.isfinite(check["minimum"]) and 1 <= check["minimum"] <= 21, "minimum contrast must be 1..21")
                require(check["minimum"] >= threshold(check), "cannot lower digital contrast threshold")
            require(check["foreground"] != check["background"], "contrast needs distinct foreground/background")
        elif kind == "external":
            fields(check, common + ["description"], label="external check")
            nonblank(check["description"], "external check description")
        else:
            require(False, "objective kind must be contrast or external")
        nonblank(check["reason"], "check applicability/evidence reason")
        require(isinstance(check["required"], bool) and isinstance(check["applicable"], bool), "required/applicable must be boolean")
    solutions = rows_by_id(plan["solutions"], "solutions")
    require(set(solutions) == set(LABELS), "plan requires exactly A–E; failure cannot reduce target count")
    for sid, solution in solutions.items():
        fields(solution, ["id", "label", "title", "design_logic", "actions", "colors", "preserve", "failure_signs"], label="solution")
        require(solution["label"] == sid, "solution label must match A–E id")
        for key in ("title", "design_logic", "failure_signs"):
            nonblank(solution[key], key)
        action_targets = []
        require(isinstance(solution["actions"], list), "actions must be an array")
        for action in solution["actions"]:
            fields(action, ["target_id", "description"], label="action")
            nonblank(action["description"], "action description")
            action_targets.append(action["target_id"])
        require(len(action_targets) == len(set(action_targets)) and set(action_targets) == targets.keys(), "actions must cover every required target exactly once", "TARGET_MISSING")
        string_list(solution["preserve"], "preserve")
        preserved = set(solution["preserve"])
        require(len(preserved) == len(solution["preserve"]) and preserved <= objects.keys(), "invalid preserve objects")
        require(isinstance(solution["colors"], list) and solution["colors"], "solution needs selected colors")
        used_colors, area_ids, changed, covered = set(), set(), set(), set()
        for color in solution["colors"]:
            fields(color, ["color_id", "role", "areas"], label="selected color")
            cid = color["color_id"]
            require(cid in colors, f"unknown/disabled color_id {cid!r}; search frozen palette", "COLOR_UNKNOWN")
            require(cid not in used_colors, "merge areas for repeated color_id")
            used_colors.add(cid)
            nonblank(color["role"], "color role")
            require(isinstance(color["areas"], list) and color["areas"], "selected color needs areas")
            for area in color["areas"]:
                fields(area, ["id", "object_id", "location", "target_ids"], ["share"], "area")
                require(isinstance(area["id"], str) and pc.ID_RE.fullmatch(area["id"]), "invalid area id")
                require(area["id"] not in area_ids and area["id"] not in objects, "area IDs must be unique and distinct from object IDs")
                area_ids.add(area["id"])
                oid = area["object_id"]
                require(oid in objects, "area references unknown object")
                permission = objects[oid]["permissions"]["color"]
                require(permission["mode"] == "modify" and oid not in preserved, f"cannot recolor preserved object {oid}", "PERMISSION_CONFLICT")
                require(permission.get("locked_color_id", cid) == cid, f"color conflicts with user lock on {oid}", "PERMISSION_CONFLICT")
                changed.add(oid)
                nonblank(area["location"], "area location")
                string_list(area["target_ids"], "area targets")
                require(len(set(area["target_ids"])) == len(area["target_ids"]), "duplicate area target")
                for tid in area["target_ids"]:
                    require(tid in targets and targets[tid]["object_id"] == oid, "area target must belong to its object", "TARGET_MISSING")
                    covered.add(tid)
                if "share" in area:
                    share = area["share"]
                    fields(share, ["value", "basis", "reason"], label="share")
                    require(isinstance(share["value"], (int, float)) and not isinstance(share["value"], bool) and 0 < share["value"] <= 1, "share value must be >0..1")
                    require(share["basis"] in {"proposed", "estimated"}, "share is optional; do not invent measured percentages")
                    nonblank(share["reason"], "share reason/denominator")
        require(changed | preserved == objects.keys(), "every observed object must be recolored or explicitly preserved", "OBJECT_MISSING")
        require(covered == targets.keys(), "colored areas omit required targets", "TARGET_MISSING")
        for check in checks.values():
            if check["kind"] == "contrast":
                require(check["foreground"] in area_ids | objects.keys() and check["background"] in area_ids | objects.keys(), "contrast references missing area/object")
                # Known bad planned colors must be corrected before spending a call.
                planned = planned_check(check, solution, colors)
                require(planned["status"] != "fail", f"planned contrast {check['id']} is below threshold", "CONTRAST_LOW")
    return plan


def threshold(check):
    if check["medium"] == "digital":
        return max(4.5 if check["contrast_class"] == "normal_text" else 3.0, check.get("minimum", 1))
    return check.get("minimum")


def color_for_ref(ref, solution, colors):
    ids = {color["color_id"] for color in solution["colors"] for area in color["areas"] if ref in (area["id"], area.get("object_id"))}
    return colors[next(iter(ids))]["rgb"] if len(ids) == 1 else None


def planned_check(check, solution, colors, fixed=None):
    result = {"status": "unknown", "value": None, "threshold": None, "source": None, "reason": "no verified measurement method/data"}
    if not check["applicable"]:
        return {**result, "status": "not_applicable", "reason": check["reason"]}
    if check["kind"] != "contrast":
        return result
    fg = color_for_ref(check["foreground"], solution, colors)
    bg = color_for_ref(check["background"], solution, colors)
    fixed = fixed or {}
    fg = fg if fg is not None else fixed.get(check["foreground"], {}).get("rgb")
    bg = bg if bg is not None else fixed.get(check["background"], {}).get("rgb")
    result["threshold"] = threshold(check)
    if fg is None or bg is None:
        result["reason"] = "preserved/ambiguous foreground or background has no single palette base color"
        return result
    value = pc.contrast(fg, bg)
    return {**result, "value": value, "status": ("pass" if value >= result["threshold"] else "fail") if result["threshold"] else "unknown",
            "source": "script:palette_core.contrast(frozen palette bases and documented fixed references)" if fixed else "script:palette_core.contrast(frozen palette bases)",
            "reason": "planned base-color contrast; does not measure the generated image"}


def solution(state, sid):
    require(state["plan"] is not None, "submit a validated plan first", "PLAN_MISSING")
    found = next((s for s in state["plan"]["solutions"] if s["id"] == sid), None)
    require(found is not None, "solution must be A–E")
    return found


def plan_sha(state, sid):
    record = {"diagnosis": state["plan"]["diagnosis"], "solution": solution(state, sid),
              "source_sha256": state["source_image"]["sha256"], "palette_binding": state["palette_binding"], "plugin": state["plugin"]}
    if state["schema_version"] in GOAL_VERSIONS:
        record["brief_binding"] = state["plan"].get("brief_binding")
    if state["schema_version"] == VERSION:
        record["round_binding"] = state["plan"].get("round_binding")
        record["feedback_application"] = state["plan"].get("feedback_application")
    return digest(record)


def validate_goal_plan(state, plan):
    if state["schema_version"] in COMPACT_VERSIONS:
        return compact.goals(sys.modules[__name__], state, plan)
    require_confirmed(state)
    require(plan.get("brief_binding") == brief_binding(state), "plan must bind current confirmed brief", "BINDING_STALE")
    goals = {g["id"] for g in state["brief"]["record"]["content"]["goals"]}
    d = plan["diagnosis"]
    objects = {o["id"]: o for o in d["objects"]}
    targets = {t["id"]: t for t in d["targets"]}
    links = d.get("goal_links")
    require(isinstance(links, list), "map confirmed goals separately from model targets", "GOAL_MISSING")
    linked = []
    for link in links:
        fields(link, ["goal_id", "object_ids", "target_ids"], label="goal link")
        linked.append(link["goal_id"])
        for key in ("object_ids", "target_ids"):
            string_list(link[key], key, True)
            require(len(link[key]) == len(set(link[key])), "duplicate goal link member")
        require(set(link["object_ids"]) <= objects.keys() and set(link["target_ids"]) <= targets.keys(), "goal references unknown objects/targets", "GOAL_MISSING")
        require({targets[t]["object_id"] for t in link["target_ids"]} == set(link["object_ids"]), "goal objects and target owners must agree", "GOAL_MISSING")
    require(len(linked) == len(set(linked)) and set(linked) == goals, "every confirmed goal needs its own mapping", "GOAL_MISSING")
    for obj in objects.values():
        nonblank(obj.get("relationship"), "object relationship: ownership/layer/task role and identifying features")


def derived_colors(sol, snapshot):
    facts = {c["color_id"]: pc.feature(c) for c in snapshot["colors"] if c["enabled"]}
    return [{**{k: facts[c["color_id"]][k] for k in ("color_id", "name", "hex", "rgb")}, "role": c["role"], "areas": copy.deepcopy(c["areas"])} for c in sol["colors"]]


@mutation
def submit_plan(run, authored):
    run, state, snapshot = load(run)
    require_confirmed(state)
    dialogue.require_open(sys.modules[__name__], state)
    require(authored.get("format") == "compact-0.10", "use the v0.10 plan template; old runs remain read-only")
    validate_plan(authored, snapshot)
    validate_goal_plan(state, authored)
    if state["attempts"]:
        require(authored["diagnosis"]["objective_checks"] == state["plan"]["diagnosis"]["objective_checks"], "objective checks/thresholds are frozen after first call", "CHECKS_FROZEN")
        require(authored["diagnosis"].get("fixed_colors", {}) == state["plan"]["diagnosis"].get("fixed_colors", {}),
                "documented fixed reading references are frozen after first call", "CHECKS_FROZEN")
    old = {sid: plan_sha(state, sid) for sid in labels(state)} if state["plan"] else {}
    state["plan"] = copy.deepcopy(authored)
    state["plan_record_sha256"] = digest(authored)
    pc.write_json(run / "plans" / f"{state['plan_record_sha256']}.json", authored)
    changed = [sid for sid in labels(state) if old.get(sid) != plan_sha(state, sid)]
    save(run, state)
    return {**progress(state), "status": "planned", "changed_solution_ids": changed, "solutions": [{"id": sid, "plan_sha256": plan_sha(state, sid), "colors": derived_colors(solution(state, sid), snapshot)} for sid in labels(state)]}


def prompt_text(state, sid, snapshot, retry_reason=None):
    if state["schema_version"] in COMPACT_VERSIONS:
        return compact.prompt(sys.modules[__name__], state, sid, snapshot, retry_reason)
    sol = solution(state, sid)
    brief = confirmed_brief(state)
    d = state["plan"]["diagnosis"]
    objects = {o["id"]: o for o in d["objects"]}
    targets = {t["id"]: t for t in d["targets"]}
    lines = ["Use the frozen original image as the edit target; return one concept image.",
             "Do not add legends, labels or swatches. Preserve natural lighting and material response.",
             "已确认目标：" + brief["summary"]]
    lines += [f"主改 {g['subject']}：{g['outcome']}" for g in brief["goals"]]
    for label, key in (("辅助范围", "supporting_changes"), ("保持", "preserve"), ("优先考虑", "priorities")):
        if brief[key]:
            lines.append(label + "：" + "；".join(brief[key]))
    lines.append("本款设计假设：" + sol["design_logic"])
    lines.append("对象归属、层次与权限（保持结构不代表保留原色）：")
    for oid, obj in objects.items():
        modes = "; ".join(f"{k}={p['mode']}" + (f" ({p['reason']})" if k != 'color' else "")
                          for k, p in obj["permissions"].items())
        lines.append(f"- {oid} {obj['label']}：{obj['relationship']} | {modes}")
    lines.append("修改动作：")
    for action in sol["actions"]:
        target = targets[action["target_id"]]
        lines.append(f"- {target['object_id']} / {target['description']}：{action['description']}")
    lines.append("涂装分配（HEX是基色，明暗随原图光照）：")
    for color in derived_colors(sol, snapshot):
        areas = "；".join(f"{a['object_id']} / {a['location']}" +
                         (f"（{a['share']['basis']}面积约{a['share']['value']:.0%}，{a['share']['reason']}）" if "share" in a else "") for a in color["areas"])
        lines.append(f"- {color['name']} {color['hex']} [{color['color_id']}]，{color['role']} → {areas}")
    if sol["preserve"]:
        lines.append("这些对象沿用原色：" + "；".join(f"{oid} {objects[oid]['label']}" for oid in sol["preserve"]))
    if d["reading_targets"]:
        lines.append("保持文字与真实字底关系：" + "；".join(f"{r['text_object_id']} → {r['background_object_id']} ({r['state']}) {r['description']}" for r in d["reading_targets"]))
    lines.append("避免本款失败迹象：" + sol["failure_signs"])
    if retry_reason:
        lines += ["定向纠正：" + retry_reason,
                  "Follow all original permissions; this retry does not authorize wider changes."]
    return "\n".join(lines) + "\n"


def call_by_id(state, call_id):
    found = next((a for a in state["attempts"] if a["id"] == call_id), None)
    require(found is not None, "unknown call_id", "CALL_MISSING")
    return found


def current_call(state, sid):
    return next((a for a in reversed(state["attempts"]) if a["solution_id"] == sid), None)


@mutation
def reserve_call(run, sid, reason=""):
    run, state, snapshot = load(run)
    require_confirmed(state)
    dialogue.require_open(sys.modules[__name__], state)
    require(not any(a["status"] == "pending" for a in state["attempts"]), "resolve pending tool receipt before any new call", "CALL_PENDING")
    solution(state, sid)
    validate_goal_plan(state, state["plan"])
    for reviewed_sid, pointer in state["reviews"].items():
        review_path = inside(run, pointer["path"])
        require(pc.sha(review_path.read_bytes()) == pointer["sha256"], "review changed", "BINDING_STALE")
        reviewed = read_json(review_path)
        if reviewed.get("failure_stage") == "diagnosis":
            require(reviewed["binding"]["plan_sha256"] != plan_sha(state, reviewed_sid),
                    "correct known diagnosis/design failure before spending further calls", "PLAN_REVISION_REQUIRED")
    prev = current_call(state, sid)
    kind = "retry" if prev else "initial"
    if prev:
        require(prev["status"] != "pending", "resolve pending call before retry", "CALL_PENDING")
        nonblank(reason, "targeted retry reason")
        used = sum(a["kind"] == "retry" for a in state["attempts"])
        require(used < state["retry_budget"], "case retry budget exhausted", "BUDGET_EXHAUSTED")
        pointer = state["reviews"].get(sid)
        if pointer and pointer["call_id"] == prev["id"]:
            previous_review = read_json(inside(run, pointer["path"]))
            require(pc.sha(inside(run, pointer["path"]).read_bytes()) == pointer["sha256"], "review changed", "BINDING_STALE")
            if previous_review.get("failure_stage") == "diagnosis":
                require(prev["plan_sha256"] != plan_sha(state, sid), "correct diagnosis/design before retrying", "PLAN_REVISION_REQUIRED")
    call_id = f"r{state['dialogue']['round_number']:03d}-call-{len(state['attempts']) + 1:03d}"
    prompt = prompt_text(state, sid, snapshot, retry_reason=reason if prev else None).encode("utf-8")
    path = run / "prompts" / f"{call_id}-{sid}.txt"
    pc.write_bytes(path, prompt)
    call = {"id": call_id, "solution_id": sid, "kind": kind, "reason": reason or "initial draft",
            "status": "pending", "plan_sha256": plan_sha(state, sid), "prompt_path": str(path), "prompt_sha256": pc.sha(prompt)}
    call["brief_binding"] = brief_binding(state)
    call["run_mode"] = state["run_mode"]
    call["round_number"] = state["dialogue"]["round_number"]
    state["attempts"].append(call)
    save(run, state)
    return {**call, "next_action": {"kind": "external" if state["run_mode"] == "live" else "fixture",
            "action": "call_image_tool" if state["run_mode"] == "live" else "register_fixture_only",
            "image_tool_allowed": state["run_mode"] == "live",
            "instruction": "This receipt authorizes this single dispatch only. On resume reconcile pending; never resend."}, "call_id": call_id, "source_image": state["source_image"], "calls_used": len(state["attempts"]), "retry_remaining": state["retry_budget"] - sum(a["kind"] == "retry" for a in state["attempts"])}


@mutation
def fail_call(run, call_id, reason):
    run, state, _ = load(run)
    call = call_by_id(state, call_id)
    require(call["status"] == "pending", "only pending calls can fail")
    nonblank(reason, "failure reason")
    call.update(status="failed", error=reason)
    dialogue.maybe_start(sys.modules[__name__], run, state)
    save(run, state)
    return copy.deepcopy(call)


def check_call_binding(run, state, call, need_image=False):
    if state["schema_version"] in GOAL_VERSIONS:
        require(brief_status(state) in {"confirmed", "test_confirmed"}, "brief confirmation missing or requires revision", "BINDING_STALE")
        require(call.get("brief_binding") == brief_binding(state) == state["plan"].get("brief_binding"), "call belongs to old confirmed goals", "BINDING_STALE")
    require(call["plan_sha256"] == plan_sha(state, call["solution_id"]), "call belongs to an old plan", "BINDING_STALE")
    require(pc.sha(inside(run, call["prompt_path"]).read_bytes()) == call["prompt_sha256"], "prompt changed after call reservation", "BINDING_STALE")
    if need_image:
        require(call["status"] == "registered", "no registered image", "IMAGE_MISSING")
        info, _, _ = image_data(inside(run, call["image_path"]))
        require(info["sha256"] == call["image_sha256"], "current image changed", "BINDING_STALE")


@mutation
def register_image(run, call_id, image_path):
    run, state, _ = load(run)
    call = call_by_id(state, call_id)
    require(call["status"] == "pending", "call has already been resolved", "CALL_RESOLVED")
    require(current_call(state, call["solution_id"])["id"] == call_id, "call is not latest for solution", "BINDING_STALE")
    check_call_binding(run, state, call)
    info, _, _ = image_data(image_path)
    historical = [a for h in state["dialogue"]["history"] for a in read_json(inside(run, h["state_path"]))["attempts"]]
    known = {state["source_image"]["pixel_sha256"]} | {a.get("pixel_sha256") for a in state["attempts"] + historical}
    require(info["pixel_sha256"] not in known, "source/duplicate image is not a new generated result", "IMAGE_DUPLICATE")
    ext = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}[info["format"]]
    dest = run / "images" / f"{call_id}-{call['solution_id']}{ext}"
    raw = Path(image_path).read_bytes()
    require(pc.sha(raw) == info["sha256"], "image changed during registration", "IMAGE_CHANGED")
    pc.write_bytes(dest, raw)
    call.update(status="registered", image_path=str(dest), image_sha256=info["sha256"], pixel_sha256=info["pixel_sha256"], image_info={**info, "path": str(dest)})
    dialogue.maybe_start(sys.modules[__name__], run, state)
    save(run, state)
    return copy.deepcopy(call)


def review_binding(state, call):
    binding = {"call_id": call["id"], "plan_sha256": call["plan_sha256"], "image_sha256": call["image_sha256"],
            "prompt_sha256": call["prompt_sha256"], "source_image_sha256": state["source_image"]["sha256"],
            "palette_binding": state["palette_binding"], "plugin_sha256": state["plugin"]["sha256"]}
    if state["schema_version"] in GOAL_VERSIONS:
        binding["brief_binding"] = brief_binding(state)
    return binding


def review_template(run, sid):
    run, state, _ = load(run)
    solution(state, sid)
    call = current_call(state, sid)
    require(call is not None, "no image to review", "IMAGE_MISSING")
    check_call_binding(run, state, call, True)
    if state["schema_version"] in COMPACT_VERSIONS:
        return compact.review_template(sys.modules[__name__], state, call)
    gates = {key: {"status": "pending", "evidence": ""} for key in GATES}
    gates["targets"]["items"] = [{"target_id": t["id"], "status": "pending", "evidence": ""} for t in state["plan"]["diagnosis"]["targets"]]
    gates["content"]["items"] = [{"reading_id": r["id"], "status": "pending", "evidence": ""} for r in state["plan"]["diagnosis"]["reading_targets"]]
    result = {"binding": review_binding(state, call), "image_seen": False, "reviewer": "", "viewing_note": "",
            "decision": "pending", "reason": "", "subjective": {"dimensions": {key: {"score": None, "evidence": ""} for key in WEIGHTS}},
            "gates": gates, "measurements": [], "limitations": [], "warnings": []}
    if state["schema_version"] in GOAL_VERSIONS:
        result["goal_checks"] = [{"goal_id": g["id"], "status": "unknown", "note": ""} for g in confirmed_brief(state)["goals"]]
        result["failure_stage"] = "none"
    return result


def objective_results(state, sol, snapshot, call, measurements):
    colors = {c["color_id"]: c for c in snapshot["colors"] if c["enabled"]}
    checks = state["plan"]["diagnosis"]["objective_checks"]
    require(isinstance(measurements, list), "measurements must be an array")
    measurements_by_id = {}
    for m in measurements:
        fields(m, ["check_id", "method", "foreground_points", "background_points", "reason"], label="measurement")
        require(m["check_id"] not in measurements_by_id, "duplicate measurement check_id")
        match = next((c for c in checks if c["id"] == m["check_id"]), None)
        require(match and match["kind"] == "contrast" and match["applicable"], "measurement must reference applicable contrast check")
        require(m["method"] == "image_pixels", "only script-sampled image_pixels supported; self-reported objective scores are forbidden")
        nonblank(m["reason"], "sampling method/locations reason")
        measurements_by_id[m["check_id"]] = m
    rgb = alpha = image_info = None
    if measurements:
        image_info, rgb, alpha = image_data(call["image_path"])
    results = []
    for check in checks:
        planned = planned_check(check, sol, colors, state["plan"]["diagnosis"].get("fixed_colors"))
        out = {"id": check["id"], "kind": check["kind"], "applicable": check["applicable"], "required": check["required"],
               "status": "unknown", "value": None, "threshold": planned["threshold"], "source": None,
               "reason": "current image has no verified measurement; planned values are not image evidence", "planned": planned}
        if not check["applicable"]:
            out.update(status="not_applicable", reason=check["reason"])
        elif check["id"] in measurements_by_id:
            m = measurements_by_id[check["id"]]
            groups, transparent = [], False
            for name in ("foreground_points", "background_points"):
                points = m[name]
                require(isinstance(points, list) and 1 <= len(points) <= 100, "sample 1..100 points per region")
                samples = []
                for point in points:
                    require(isinstance(point, list) and len(point) == 2 and all(isinstance(n, int) and not isinstance(n, bool) for n in point), "pixel points must be integer [x,y]")
                    x, y = point
                    require(0 <= x < rgb.width and 0 <= y < rgb.height, "pixel point outside current image")
                    transparent = transparent or (alpha is not None and alpha.getpixel((x, y)) != 255)
                    samples.append(dict(zip("RGB", rgb.getpixel((x, y)))))
                groups.append(samples)
            ratios = [pc.contrast(fg, bg) for fg in groups[0] for bg in groups[1]]
            value = min(ratios)
            out.update(value=None if transparent else value,
                       source={"method": "image_pixels_min_pair_contrast", "image_sha256": call["image_sha256"],
                               "color_space": "sRGB", "color_space_source": image_info["color_space_source"],
                               "foreground_points": m["foreground_points"], "background_points": m["background_points"]},
                       reason=("transparent samples require actual compositing background" if transparent else m["reason"] + "; screenshot proxy only, not CSS/physical/print certification"))
            if out["threshold"] is not None and not transparent:
                out["status"] = "pass" if value >= out["threshold"] else "fail"
        elif check["kind"] == "external":
            out["reason"] = "external measurement unavailable in this workflow: " + check["reason"]
        results.append(out)
    applicable = sum(r["applicable"] for r in results)
    passed = sum(r["status"] == "pass" for r in results)
    failed = sum(r["status"] == "fail" for r in results)
    measured = passed + failed
    return {"checks": results, "applicable_count": applicable, "measured_count": measured,
            "passed_count": passed, "failed_count": failed, "unknown_count": applicable - measured,
            "coverage": measured / applicable if applicable else None, "pass_rate": passed / measured if measured else None}


def assess_review(run, state, snapshot, sid, review):
    if state["schema_version"] in COMPACT_VERSIONS:
        return compact.assess(sys.modules[__name__], run, state, snapshot, sid, review)
    call = current_call(state, sid)
    require(call is not None, "no image to review", "IMAGE_MISSING")
    check_call_binding(run, state, call, True)
    new = state["schema_version"] in GOAL_VERSIONS
    fields(review, ["binding", "image_seen", "reviewer", "viewing_note", "decision", "reason", "subjective", "gates", "measurements", "limitations", "warnings"] + (["goal_checks", "failure_stage"] if new else []), label="review")
    require(review["binding"] == review_binding(state, call), "review binding is stale", "BINDING_STALE")
    require(review["image_seen"] is True, "review requires actual viewing of current image", "IMAGE_NOT_SEEN")
    for key in ("reviewer", "viewing_note", "reason"):
        nonblank(review[key], key)
    require(review["decision"] in {"accept", "reject"}, "submitted decision must be accept/reject")
    if new:
        require(review["failure_stage"] in {"none", "goal", "diagnosis", "execution"}, "invalid failure stage")
        require(isinstance(review["goal_checks"], list), "goal_checks must be an array")
        ids = []
        for item in review["goal_checks"]:
            fields(item, ["goal_id", "status", "note"], label="goal review")
            ids.append(item["goal_id"])
            require(item["status"] in {"pass", "fail", "unknown"}, "invalid goal status")
            nonblank(item["note"], "goal evidence at actual subject, not author intent")
        require(len(ids) == len(set(ids)) and set(ids) == {g["id"] for g in confirmed_brief(state)["goals"]},
                "review every confirmed user goal, separately from planned targets", "GOAL_MISSING")
        if review["decision"] == "accept":
            require(review["failure_stage"] == "none" and all(g["status"] == "pass" for g in review["goal_checks"]),
                    "unfulfilled or unreviewed user goal cannot be accepted", "GOAL_FAILED")
        else:
            require(review["failure_stage"] != "none", "rejection must identify where to correct it", "FAILURE_STAGE_MISSING")
    string_list(review["limitations"], "limitations")
    string_list(review["warnings"], "warnings")
    fields(review["subjective"], ["dimensions"], label="subjective")
    dims = review["subjective"]["dimensions"]
    fields(dims, WEIGHTS, label="subjective dimensions")
    for key, value in dims.items():
        fields(value, ["score", "evidence"], label=key)
        require(isinstance(value["score"], int) and not isinstance(value["score"], bool) and 0 <= value["score"] <= 100 and value["score"] % 10 == 0, "subjective scores use integer ten-point anchors 0..100")
        nonblank(value["evidence"], "subjective visual evidence")
    fields(review["gates"], GATES, label="gates")
    for key, gate in review["gates"].items():
        fields(gate, ["status", "evidence"] + (["items"] if key in {"targets", "content"} else []), label="gate")
        require(gate["status"] in {"pass", "fail", "pending"}, "invalid visual gate status")
        nonblank(gate["evidence"], "visual gate evidence")
    target_items = review["gates"]["targets"]["items"]
    require(isinstance(target_items, list), "target evidence must be an array")
    ids = []
    for item in target_items:
        fields(item, ["target_id", "status", "evidence"], label="target review")
        ids.append(item["target_id"])
        require(item["status"] in {"pass", "fail", "pending"}, "invalid target status")
        nonblank(item["evidence"], "target evidence")
    require(len(ids) == len(set(ids)) and set(ids) == {t["id"] for t in state["plan"]["diagnosis"]["targets"]}, "review must cover every required target", "TARGET_MISSING")
    reading_items = review["gates"]["content"]["items"]
    require(isinstance(reading_items, list), "reading evidence must be an array")
    reading_ids = []
    for item in reading_items:
        fields(item, ["reading_id", "status", "evidence"], label="reading review")
        reading_ids.append(item["reading_id"])
        require(item["status"] in {"pass", "fail", "pending"}, "invalid reading status")
        nonblank(item["evidence"], "actual reading evidence")
    require(len(reading_ids) == len(set(reading_ids)) and set(reading_ids) == {r["id"] for r in state["plan"]["diagnosis"]["reading_targets"]}, "content gate must cover actual reading targets/states", "READING_MISSING")
    objective = objective_results(state, solution(state, sid), snapshot, call, review["measurements"])
    subjective = {"dimensions": copy.deepcopy(dims), "weights": WEIGHTS, "total": round(sum(dims[k]["score"] * w for k, w in WEIGHTS.items()), 1), "source": "model_visual_review", "calibrated": False}
    if review["decision"] == "accept":
        require(dims["beauty"]["score"] >= 70, "beauty below provisional acceptance anchor; averages cannot rescue it", "VISUAL_REJECTED")
        require(all(g["status"] == "pass" for g in review["gates"].values()) and all(t["status"] == "pass" for t in target_items), "unresolved primary target/visual/permission/content failure", "VISUAL_REJECTED")
        require(all(item["status"] == "pass" for item in reading_items), "actual reading target/state failed or unchecked", "READING_FAILED")
        require(objective["failed_count"] == 0, "known objective failure cannot be accepted", "OBJECTIVE_FAILED")
        if any(c["required"] and c["status"] == "unknown" for c in objective["checks"]):
            require(review["limitations"], "required unknown checks need explicit concept-only limitations", "LIMITATION_MISSING")
    return subjective, objective


@mutation
def submit_review(run, sid, review):
    run, state, snapshot = load(run)
    subjective, objective = assess_review(run, state, snapshot, sid, review)
    call = current_call(state, sid)
    # Keep each submitted review for recovery; the current pointer binds its bytes.
    review_sha = digest(review)
    path = run / "reviews" / f"{call['id']}-{review_sha[:16]}.json"
    pc.write_json(path, review)
    state["reviews"][sid] = {"path": str(path), "sha256": pc.sha(path.read_bytes()), "call_id": call["id"]}
    if review.get("failure_stage") == "goal":
        state["reconfirmation_required"] = True
    save(run, state)
    return {"solution_id": sid, "decision": review["decision"], "subjective": subjective, "objective": objective,
            "goal_checks": review.get("goal_checks", []), "failure_stage": review.get("failure_stage"), "review_path": str(path)}


def evaluate(run):
    run, state, snapshot = load(run)
    accepted, statuses = [], []
    for sid in labels(state):
        record = {"id": sid, "status": "pending", "reason": "no current accepted image"}
        try:
            sol = solution(state, sid)
            call = current_call(state, sid)
            require(call is not None, "no image call", "IMAGE_MISSING")
            check_call_binding(run, state, call, True)
            pointer = state["reviews"].get(sid)
            require(pointer and pointer["call_id"] == call["id"], "current image has no review", "REVIEW_MISSING")
            path = inside(run, pointer["path"])
            require(pc.sha(path.read_bytes()) == pointer["sha256"], "review changed outside review command", "BINDING_STALE")
            review = read_json(path)
            subjective, objective = assess_review(run, state, snapshot, sid, review)
            record.update(status="accepted" if review["decision"] == "accept" else "rejected", reason=review["reason"])
            if review["decision"] == "accept":
                accepted.append({"id": sid, "label": sol["label"], "title": sol["title"], "design_logic": sol["design_logic"],
                                 "colors": derived_colors(sol, snapshot), "image_path": call["image_path"], "image_sha256": call["image_sha256"],
                                 "plan_sha256": call["plan_sha256"], "prompt_path": call["prompt_path"], "prompt_sha256": call["prompt_sha256"],
                                 "subjective": subjective, "objective": objective, "decision": "accept", "reason": review["reason"],
                                 "limitations": review["limitations"], "warnings": review["warnings"], "integrity": {"status": "pass", "review_path": str(path), "review_sha256": pointer["sha256"]}})
                if state["schema_version"] in GOAL_VERSIONS:
                    accepted[-1].update(goal_checks=review["goal_checks"], failure_stage=review["failure_stage"], goal_binding=state["plan"]["brief_binding"])
                if state["schema_version"] in COMPACT_VERSIONS:
                    accepted[-1].update(ambition=sol["ambition"], content_check=review["content_check"])
                    if "search_evidence" in sol:
                        accepted[-1]["candidate_evidence"] = compact.search_result(sys.modules[__name__], sol, snapshot)
        except (pc.PaletteError, OSError) as exc:
            record.update(status="stale" if getattr(exc, "code", "") == "BINDING_STALE" else "pending", reason=str(exc), code=getattr(exc, "code", "FILE_MISSING"))
        statuses.append(record)
    result = {"schema_version": state["schema_version"], "case_id": state["case_id"], "label": state["label"], "plugin_id": state["plugin"]["id"],
            "plugin": state["plugin"], "source_image": state["source_image"], "palette_binding": state["palette_binding"],
            "diagnosis": state["plan"]["diagnosis"] if state["plan"] else None,
            "status": "complete" if state["target_count"] and len(accepted) == state["target_count"] else "partial", "target_count": state["target_count"], "accepted_count": len(accepted),
            "solutions": accepted, "missing_solution_ids": [sid for sid in labels(state) if sid not in {s['id'] for s in accepted}],
            "solution_statuses": statuses, "attempts": state["attempts"],
            "budget": {"initial_limit": state["target_count"] or 0, "retry_limit": state["retry_budget"], "calls_used": len(state["attempts"]), "retry_used": sum(a["kind"] == "retry" for a in state["attempts"])},
            "limitations": ["Concept images; model visual review is uncalibrated and cannot certify production or user preference."] + ([state["plugin"]["fallback_reason"]] if state["plugin"]["fallback_reason"] else [])}
    if state["schema_version"] in GOAL_VERSIONS:
        result.update(confirmed_brief=confirmed_brief(state), brief_status=brief_status(state), run_mode=state["run_mode"])
    if state["schema_version"] == VERSION:
        result.update(progress(state))
    return result


def status(run):
    result = evaluate(run)
    return {"read_only": result["schema_version"] != VERSION, **{k: result[k] for k in ("schema_version", "case_id", "status", "target_count", "accepted_count", "missing_solution_ids", "solution_statuses", "budget", "brief_status", "run_mode", "phase", "next_action", "round_number", "session_ended", "purpose", "preferences", "round_history") if k in result}}


def read_round(run, number):
    run, state, _ = load(run)
    require(state["schema_version"] == VERSION, "legacy runs use status/verify/resume")
    d = state["dialogue"]
    if number == d["round_number"]:
        record = d
    else:
        record = next((h for h in d["history"] if h["round_number"] == number), None)
        require(record is not None, "unknown round", "ROUND_MISSING")
    delivery = record.get("delivery")
    if delivery:
        path = inside(run, delivery["path"])
        require(pc.sha(path.read_bytes()) == delivery["sha256"], "old delivery changed", "BINDING_STALE")
        return {"read_only": True, "result_path": str(path), "result": read_json(path)}
    return {"read_only": True, "round_number": number, "state_path": record.get("state_path"),
            "status": "not_delivered", "next_action": {"kind": "none", "action": "read_only", "image_tool_allowed": False}}


@mutation
def export_result(run):
    _, state, _ = load(run)
    d = state["dialogue"]
    require(d["round_number"] > 0, "authorize a round before delivery", "ROUND_AUTHORIZATION_REQUIRED")
    if d["delivery"]:
        path = inside(run, d["delivery"]["path"])
        require(pc.sha(path.read_bytes()) == d["delivery"]["sha256"], "delivered result changed", "BINDING_STALE")
        return {"result_path": str(path), **status_summary(read_json(path)), **progress(state)}
    require(not any(a["status"] == "pending" for a in state["attempts"]), "resolve pending tool calls before delivery", "CALL_PENDING")
    result = evaluate(run)
    path = run / "result.json" if d["round_number"] == 1 else run / "rounds" / f"round-{d['round_number']:03d}" / "result.json"
    require(not path.exists(), "delivery is immutable; reconcile existing result before proceeding", "FILE_EXISTS")
    d["delivery"] = {"path": str(path), "sha256": None}
    result.update(progress(state))
    pc.write_json(path, result)
    d["delivery"]["sha256"] = pc.sha(path.read_bytes())
    save(run, state)
    return {"result_path": str(path), **status_summary(result), **progress(state)}


def status_summary(result):
    return {k: result[k] for k in ("case_id", "status", "accepted_count", "missing_solution_ids")}
