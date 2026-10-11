"""Command line entrypoint shared by normal use and batch scene runs."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import palette_core as pc
import scene_pipeline as scene


def parser():
    root = argparse.ArgumentParser(description="v0.10 dialogue and 5/10-image rounds; records/scheduling only, no image API calls")
    commands = root.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="freeze source, palette and selected plugin in a NEW run")
    for name in ("project-root", "run", "input", "case-id", "label"):
        init.add_argument("--" + name, required=True)
    init.add_argument("--plugin", default="generic")
    init.add_argument("--palette")
    init.add_argument("--retry-budget", type=int, default=2)
    init.add_argument("--run-mode", choices=("live", "test"), default="live")
    init.add_argument("--count", type=int, choices=(5, 10), help="requested count only; dialogue evidence still required")
    init.add_argument("--purpose", choices=("design", "plan", "diagnose"), default="design")
    for name in ("oldread", "dialogue-template", "dialogue", "brief-template", "brief", "confirm", "route", "plan-template", "search", "groups", "plan", "call", "call-failed", "register", "review-template", "review", "verify", "export", "status", "resume"):
        p = commands.add_parser(name)
        p.add_argument("--run", required=True)
        if name == "oldread":
            p.add_argument("--round", type=int, required=True)
        if name in {"dialogue-template", "brief-template", "plan-template", "review-template"}:
            p.add_argument("--out", help="new authoring file; never overwrite existing drafts")
        if name in {"call", "review-template", "review"}:
            p.add_argument("--solution", choices=scene.ALL_LABELS, required=True)
        if name in {"dialogue", "brief", "confirm", "plan", "review"}:
            p.add_argument("--file", required=True)
        if name == "groups":
            p.add_argument("--spec", required=True)
        if name == "route":
            p.add_argument("--plugin", required=True)
        if name in {"register", "call-failed"}:
            p.add_argument("--call", required=True)
        if name == "register":
            p.add_argument("--image", required=True)
        if name in {"call", "call-failed"}:
            p.add_argument("--reason", default="", required=name == "call-failed")
        if name == "search":
            p.add_argument("--query", default="")
            for axis in ("lightness", "chroma", "hue"):
                p.add_argument("--" + axis, nargs=2, type=float)
            p.add_argument("--limit", type=int, default=12)
            p.add_argument("--offset", type=int, default=0)
    return root


def dispatch(a):
    run = Path(a.run).resolve()
    if a.command == "init":
        return scene.init_run(run, a.project_root, a.input, a.case_id, a.label, a.plugin, a.palette, a.retry_budget, run_mode=a.run_mode, count=a.count, purpose=a.purpose)
    if a.command in {"dialogue-template", "brief-template", "plan-template", "review-template"}:
        result = scene.dialogue_template(run) if a.command == "dialogue-template" else scene.brief_template(run) if a.command == "brief-template" else scene.plan_template(run) if a.command == "plan-template" else scene.review_template(run, a.solution)
        if a.out:
            target = Path(a.out).resolve()
            scene.require(not target.exists(), "template output already exists; edit existing draft", "FILE_EXISTS")
            # Templates cannot overwrite frozen/internal run records.
            scene.require(target.parent == run or not target.is_relative_to(run), "write authoring templates at run root or outside run")
            pc.write_json(target, result)
            return {"path": str(target), "status": "pending"}
        return result
    if a.command == "search":
        return scene.search(run, a.query, a.lightness, a.chroma, a.hue, a.limit, a.offset)
    if a.command == "groups":
        import relationship_search
        _, state, snapshot = scene.load(run)
        return {"palette_binding": state["palette_binding"], **relationship_search.search_groups(pc.build_features(snapshot), scene.read_json(a.spec))}
    if a.command == "brief":
        return scene.submit_brief(run, scene.read_json(a.file))
    if a.command == "dialogue":
        return scene.submit_dialogue(run, scene.read_json(a.file))
    if a.command == "oldread":
        return scene.read_round(run, a.round)
    if a.command == "confirm":
        return scene.confirm_brief(run, scene.read_json(a.file))
    if a.command == "route":
        return scene.route_plugin(run, a.plugin)
    if a.command == "plan":
        return scene.submit_plan(run, scene.read_json(a.file))
    if a.command == "call":
        return scene.reserve_call(run, a.solution, a.reason)
    if a.command == "call-failed":
        return scene.fail_call(run, a.call, a.reason)
    if a.command == "register":
        return scene.register_image(run, a.call, a.image)
    if a.command == "review":
        return scene.submit_review(run, a.solution, scene.read_json(a.file))
    if a.command == "export":
        return scene.export_result(run)
    return scene.status(run)


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        result = dispatch(args)
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except (pc.PaletteError, OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "blocked", "error": getattr(exc, "code", "SCENE_INVALID"), "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return getattr(exc, "exit_code", 2)


if __name__ == "__main__":
    sys.exit(main())
