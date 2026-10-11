#!/usr/bin/env python3
"""Command-line entrypoint; all facts and acceptance are derived locally."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Also supports direct loading by local test runners.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from palette_core import *
from design_engine import (analyze, validate_intent, search_colors, plan_template,
    propose, register_image, render, verify, verify_delivery, export_run,
    resume_run, fork_run, evaluate, fact_for)


def make_parser():
    p = argparse.ArgumentParser(description="Authored traditional-colour design plans and current-artifact verification")
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("preflight")
    s.add_argument("--project-root", default=".")
    s.add_argument("--palette")
    s.add_argument("--out", required=True)
    s.set_defaults(fn=preflight)
    s = sub.add_parser("analyze")
    s.add_argument("--run", required=True)
    s.add_argument("--input")
    s.add_argument("--request", default="")
    s.add_argument("--object-kind", choices=("toy", "streetscape", "packaging", "other"), default="other")
    s.add_argument("--text-status", choices=("unknown", "absent", "present"), default="unknown")
    s.add_argument("--solution-count", type=int, choices=(4, 5, 6), default=5)
    s.add_argument("--count-reason", default="")
    s.add_argument("--mode", choices=("palette_design", "faithful_recolor"), default="palette_design")
    s.set_defaults(fn=analyze)
    s = sub.add_parser("search-colors", help="Snapshot lookup, never autonomous aesthetic selection")
    s.add_argument("--run", required=True)
    s.add_argument("--query", default="")
    s.add_argument("--lightness", type=float, nargs=2, metavar=("MIN", "MAX"))
    s.add_argument("--chroma", type=float, nargs=2, metavar=("MIN", "MAX"))
    s.add_argument("--hue", type=float, nargs=2, metavar=("MIN", "MAX"))
    s.add_argument("--near-id")
    s.add_argument("--limit", type=int, default=20)
    s.add_argument("--offset", type=int, default=0)
    s.set_defaults(fn=search_colors)
    s = sub.add_parser("plan-template")
    s.add_argument("--run", required=True)
    s.add_argument("--out")
    s.set_defaults(fn=plan_template)
    s = sub.add_parser("validate-intent")
    s.add_argument("--run", required=True)
    s.add_argument("--intent")
    s.set_defaults(fn=validate_intent)
    s = sub.add_parser("propose")
    s.add_argument("--run", required=True)
    s.add_argument("--intent")
    s.add_argument("--plan", help="Required authored plan; omission is DESIGN_PLAN_REQUIRED")
    s.set_defaults(fn=propose)
    s = sub.add_parser("register-image")
    s.add_argument("--run", required=True)
    s.add_argument("--solution", required=True)
    s.add_argument("--image", required=True)
    s.add_argument("--attempt-kind", choices=("initial", "local_repair", "redesign"), default="initial")
    s.add_argument("--repair-reason", default="")
    s.set_defaults(fn=register_image)
    for command, fn in (("render", render), ("verify", verify), ("verify-delivery", verify_delivery),
                        ("export", export_run), ("resume", resume_run)):
        s = sub.add_parser(command)
        s.add_argument("--run", required=True)
        s.set_defaults(fn=fn)
    s = sub.add_parser("fork-run")
    s.add_argument("--run", required=True)
    s.add_argument("--palette", required=True)
    s.add_argument("--out", required=True)
    s.set_defaults(fn=fork_run)
    return p


def main(argv=None):
    try:
        args = make_parser().parse_args(argv)
        result = args.fn(args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except PaletteError as exc:
        print(json.dumps({"status": "blocked", "error_code": exc.code, "message": exc.message}, ensure_ascii=False), file=sys.stderr)
        return exc.exit_code
    except (OSError, KeyError, ValueError, TypeError, AttributeError) as exc:
        print(json.dumps({"status": "blocked", "error_code": "SCHEMA_INVALID", "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
