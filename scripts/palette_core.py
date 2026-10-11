#!/usr/bin/env python3
"""Local runtime for the traditional-colour palette Skill.

The program is deliberately dependency-free. Models provide intent and visual
review; this module owns data facts, snapshots, derived colours, rendering
facts, bindings, and machine quality checks.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import html
import json
import math
import os
import re
import shutil
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

CANONICALIZER = "pc-canonical-v1"
FEATURES = "features-v1"
SCHEMA = "1.0.0"
RUN_SCHEMA = "2.1.0"
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
LABELS = ("A", "B", "C", "D", "E", "F")


class PaletteError(Exception):
    def __init__(self, code: str, message: str, exit_code: int = 2):
        super().__init__(message)
        self.code, self.message, self.exit_code = code, message, exit_code


def fail(code: str, message: str, exit_code: int = 2) -> None:
    raise PaletteError(code, message, exit_code)


def duplicate_guard(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            fail("SCHEMA_INVALID", f"duplicate JSON object key: {key}")
        out[key] = value
    return out


def parse_json_bytes(raw: bytes, label: str) -> Any:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        fail("SCHEMA_INVALID", f"{label}: invalid UTF-8: {exc}")
    try:
        value = json.loads(text, object_pairs_hook=duplicate_guard, parse_constant=lambda x: fail("SCHEMA_INVALID", f"{label}: non-finite JSON constant {x}"))
    except PaletteError:
        raise
    except (json.JSONDecodeError, UnicodeEncodeError) as exc:
        fail("SCHEMA_INVALID", f"{label}: invalid JSON: {exc}")
    reject_surrogates(value, label)
    return value


def reject_surrogates(value: Any, label: str) -> None:
    if isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            fail("SCHEMA_INVALID", f"{label}: surrogate code point in string")
    elif isinstance(value, dict):
        for k, v in value.items():
            reject_surrogates(k, label)
            reject_surrogates(v, label)
    elif isinstance(value, list):
        for item in value:
            reject_surrogates(item, label)


def write_bytes(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(raw)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        os.chmod(path, 0o644)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def write_json(path: Path, value: Any, *, canonical: bool = False) -> bytes:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=canonical, separators=(",", ":") if canonical else (",", ": "), allow_nan=False, indent=None if canonical else 2).encode("utf-8")
    if not canonical:
        raw += b"\n"
    write_bytes(path, raw)
    return raw


def read_stable(path: Path) -> bytes:
    if not path.exists():
        fail("PALETTE_NOT_FOUND", f"palette not found: {path}")
    if not path.is_file():
        fail("PALETTE_UNREADABLE", f"palette is not a file: {path}")
    try:
        if path.stat().st_size > 16 * 1024 * 1024:
            fail("SCHEMA_INVALID", "palette exceeds 16 MiB resource limit")
        last = None
        for _ in range(3):
            raw = path.read_bytes()
            if last == raw:
                return raw
            last = raw
        fail("PALETTE_UNSTABLE_READ", f"palette changed while being read: {path}")
    except PaletteError:
        raise
    except OSError as exc:
        fail("PALETTE_UNREADABLE", f"cannot read palette {path}: {exc}")


def validate_palette(obj: Any) -> dict:
    if isinstance(obj, list):
        fail("SCHEMA_INVALID", "legacy colour-list input; convert it to the supported object contract explicitly")
    if not isinstance(obj, dict):
        fail("SCHEMA_INVALID", "palette root must be an object")
    required = {"schema_version", "palette_id", "display_name", "color_space", "colors"}
    allowed = required | {"source"}
    unknown = set(obj) - allowed
    if unknown:
        fail("SCHEMA_INVALID", f"unknown top-level fields: {sorted(unknown)}")
    missing = required - set(obj)
    if missing:
        fail("SCHEMA_INVALID", f"missing required fields: {sorted(missing)}")
    if obj["schema_version"] != SCHEMA:
        fail("PALETTE_SCHEMA_UNSUPPORTED", f"schema_version must be {SCHEMA}")
    if not isinstance(obj["palette_id"], str) or not ID_RE.fullmatch(obj["palette_id"]):
        fail("SCHEMA_INVALID", "palette_id is not a valid stable ID")
    if not isinstance(obj["display_name"], str) or not obj["display_name"].strip():
        fail("SCHEMA_INVALID", "display_name must contain non-whitespace")
    if obj["color_space"] != "sRGB":
        fail("COLOR_SPACE_UNSUPPORTED", "only sRGB is supported")
    if not isinstance(obj["colors"], list) or len(obj["colors"]) > 100000:
        fail("SCHEMA_INVALID", "colors must be an array within the resource limit")
    ids = set()
    normalized = copy.deepcopy(obj)
    normalized.setdefault("source", {})
    if not isinstance(normalized["source"], dict):
        fail("SCHEMA_INVALID", "source must be an object")
    names, rgbs = {}, {}
    colors = []
    for idx, row in enumerate(obj["colors"]):
        if not isinstance(row, dict):
            fail("SCHEMA_INVALID", f"colors[{idx}] must be an object")
        required_color = {"color_id", "name", "rgb"}
        allowed_color = required_color | {"enabled", "metadata", "source"}
        unknown_color = set(row) - allowed_color
        if unknown_color:
            fail("SCHEMA_INVALID", f"colors[{idx}] unknown fields: {sorted(unknown_color)}")
        if set(row) & required_color != required_color:
            fail("SCHEMA_INVALID", f"colors[{idx}] missing required field")
        cid = row["color_id"]
        if not isinstance(cid, str) or not ID_RE.fullmatch(cid):
            fail("SCHEMA_INVALID", f"invalid color_id at colors[{idx}]")
        if cid in ids:
            fail("PALETTE_DUPLICATE_ID", f"duplicate color_id: {cid}")
        ids.add(cid)
        name = row["name"]
        if not isinstance(name, str) or not name.strip():
            fail("SCHEMA_INVALID", f"blank name at colors[{idx}]")
        rgb = row["rgb"]
        if not isinstance(rgb, dict) or set(rgb) != {"R", "G", "B"}:
            fail("SCHEMA_INVALID", f"rgb at colors[{idx}] must contain exactly R/G/B")
        for ch in "RGB":
            value = rgb[ch]
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 255:
                fail("SCHEMA_INVALID", f"rgb.{ch} at colors[{idx}] must be an integer 0..255")
        enabled = row.get("enabled", True)
        if not isinstance(enabled, bool):
            fail("SCHEMA_INVALID", f"enabled at colors[{idx}] must be boolean")
        for extra in ("metadata", "source"):
            if extra in row and not isinstance(row[extra], dict):
                fail("SCHEMA_INVALID", f"{extra} at colors[{idx}] must be an object")
        clean = copy.deepcopy(row)
        clean["enabled"] = enabled
        clean.setdefault("metadata", {})
        clean.setdefault("source", {})
        clean["rgb"] = {ch: int(rgb[ch]) for ch in "RGB"}
        colors.append(clean)
        names.setdefault(name, []).append(cid)
        rgbs.setdefault(tuple(rgb[ch] for ch in "RGB"), []).append(cid)
    normalized["colors"] = sorted(colors, key=lambda x: x["color_id"])
    if not any(c["enabled"] for c in colors):
        fail("PALETTE_EMPTY", "no enabled colours")
    warnings = []
    warnings += [{"code": "PALETTE_NAME_DUPLICATE", "name": n, "color_ids": v} for n, v in names.items() if len(v) > 1]
    warnings += [{"code": "PALETTE_RGB_DUPLICATE", "rgb": list(rgb), "color_ids": v} for rgb, v in rgbs.items() if len(v) > 1]
    return {"palette": normalized, "warnings": warnings, "enabled_count": sum(c["enabled"] for c in colors)}


def canonical_bytes(palette: dict) -> bytes:
    return json.dumps(palette, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def binding_for(manifest: dict, run: Path) -> dict:
    return {"palette_id": manifest["palette_id"], "schema_version": manifest["schema_version"], "palette_source_sha256": manifest["palette_source_sha256"], "palette_content_sha256": manifest["palette_content_sha256"], "snapshot_path": str(Path("inputs/palette.snapshot.json")), "color_space": manifest["color_space"], "canonicalizer_version": manifest["canonicalizer_version"], "run_id": manifest["run_id"]}


def load_manifest(run: Path) -> tuple[dict, dict]:
    mp = run / "palette.manifest.json"
    sp = run / "inputs/palette.snapshot.json"
    if not mp.exists() or not sp.exists():
        fail("PALETTE_SNAPSHOT_MISSING", f"run is missing manifest or snapshot: {run}")
    manifest = parse_json_bytes(mp.read_bytes(), str(mp))
    snap_raw = sp.read_bytes()
    if sha(snap_raw) != manifest.get("palette_content_sha256"):
        fail("PALETTE_SNAPSHOT_MISMATCH", "snapshot hash does not match manifest")
    snapshot = validate_palette(parse_json_bytes(snap_raw, str(sp)))["palette"]
    if sha(canonical_bytes(snapshot)) != manifest.get("palette_content_sha256"):
        fail("PALETTE_SNAPSHOT_MISMATCH", "snapshot canonical content does not match manifest")
    source_path = run / "inputs/palette.source.json"
    if not source_path.is_file() or sha(source_path.read_bytes()) != manifest.get("palette_source_sha256"):
        fail("PALETTE_SNAPSHOT_MISMATCH", "raw palette source no longer matches manifest")
    if any(manifest.get(k) != snapshot[k] for k in ("palette_id", "schema_version", "color_space")):
        fail("PALETTE_BINDING_MISMATCH", "manifest identity does not match snapshot")
    return manifest, snapshot


def srgb_channel(x: int) -> float:
    x /= 255.0
    return x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4


def rgb_to_oklab(rgb: dict) -> tuple[float, float, float]:
    r, g, b = (srgb_channel(rgb[k]) for k in "RGB")
    l = 0.4122214708*r + 0.5363325363*g + 0.0514459929*b
    m = 0.2119034982*r + 0.6806995451*g + 0.1073969566*b
    s = 0.0883024619*r + 0.2817188376*g + 0.6299787005*b
    l, m, s = l ** (1/3), m ** (1/3), s ** (1/3)
    return (0.2104542553*l + 0.7936177850*m - 0.0040720468*s, 1.9779984951*l - 2.4285922050*m + 0.4505937099*s, 0.0259040371*l + 0.7827717662*m - 0.8086757660*s)


def luminance(rgb: dict) -> float:
    return 0.2126*srgb_channel(rgb["R"]) + 0.7152*srgb_channel(rgb["G"]) + 0.0722*srgb_channel(rgb["B"])


def hex_rgb(rgb: dict) -> str:
    return "#%02X%02X%02X" % tuple(rgb[k] for k in "RGB")


def feature(row: dict) -> dict:
    lab = rgb_to_oklab(row["rgb"])
    c = math.hypot(lab[1], lab[2])
    h = (math.degrees(math.atan2(lab[2], lab[1])) + 360) % 360 if c else None
    return {"color_id": row["color_id"], "name": row["name"], "rgb": row["rgb"], "hex": hex_rgb(row["rgb"]), "oklab": list(lab), "oklch": [lab[0], c, h], "luminance": luminance(row["rgb"]), "enabled": row["enabled"]}


def d100(a: dict, b: dict) -> float:
    return 100 * math.sqrt(sum((a[i] - b[i]) ** 2 for i in range(3)))


def contrast(a: dict, b: dict) -> float:
    la, lb = luminance(a), luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def build_features(snapshot: dict) -> list[dict]:
    return [feature(row) for row in snapshot["colors"]]


def preflight(args) -> dict:
    project = Path(args.project_root).resolve()
    palette_path = Path(args.palette).resolve() if args.palette else project / "palettes/current.palette.json"
    raw = read_stable(palette_path)
    checked = validate_palette(parse_json_bytes(raw, str(palette_path)))
    snapshot = checked["palette"]
    canon = canonical_bytes(snapshot)
    out = Path(args.out).resolve()
    if out.exists() and any(out.iterdir()):
        fail("RUN_EXISTS", "preflight requires a new empty run; use resume or fork-run")
    out.mkdir(parents=True, exist_ok=True)
    source_rel = Path("inputs/palette.source.json")
    snapshot_rel = Path("inputs/palette.snapshot.json")
    write_bytes(out / source_rel, raw)
    write_bytes(out / snapshot_rel, canon)
    run_id = out.name
    manifest = {"run_id": run_id, "run_schema_version": RUN_SCHEMA, "schema_version": SCHEMA, "palette_id": snapshot["palette_id"], "display_name": snapshot["display_name"], "color_space": snapshot["color_space"], "palette_path": str(palette_path), "palette_source_sha256": sha(raw), "palette_content_sha256": sha(canon), "canonicalizer_version": CANONICALIZER, "feature_generator_version": FEATURES, "snapshot_path": str(snapshot_rel), "enabled_count": checked["enabled_count"], "warnings": checked["warnings"]}
    cache_root = project / ".palette-cache" / manifest["palette_content_sha256"]
    cache_file = cache_root / "features.json"
    cache_rebuilt = False
    if cache_file.exists():
        try:
            cached = parse_json_bytes(cache_file.read_bytes(), str(cache_file))
            if cached.get("binding", {}).get("palette_content_sha256") != manifest["palette_content_sha256"] or cached.get("feature_generator_version") != FEATURES:
                raise ValueError("cache key mismatch")
            features = cached["features"]
            if features != build_features(snapshot):
                raise ValueError("cached facts differ from snapshot")
        except Exception:
            features = build_features(snapshot)
            cache_rebuilt = True
    else:
        features = build_features(snapshot)
        cache_rebuilt = True
    if cache_rebuilt:
        write_json(cache_file, {"feature_generator_version": FEATURES, "binding": binding_for(manifest, out), "features": features})
    write_json(out / "features.json", {"feature_generator_version": FEATURES, "binding": binding_for(manifest, out), "features": features})
    write_json(out / "palette.manifest.json", manifest)
    write_json(out / "preflight.json", {"status": "pass", "palette_binding": binding_for(manifest, out), "enabled_count": checked["enabled_count"], "warnings": checked["warnings"], "cache": "rebuilt" if cache_rebuilt else "hit"})
    write_json(out / "state.json", {"stage": "preflight", "status": "pending", "artifacts": {"palette.manifest.json": sha((out / "palette.manifest.json").read_bytes()), "inputs/palette.snapshot.json": sha(canon)}})
    return {"run": str(out), "palette_binding": binding_for(manifest, out), "enabled_count": checked["enabled_count"], "warnings": checked["warnings"], "cache": "rebuilt" if cache_rebuilt else "hit"}


def run_context(run_arg: str) -> tuple[Path, dict, dict, list[dict]]:
    run = Path(run_arg).resolve()
    manifest, snapshot = load_manifest(run)
    # Features are cheap and are never trusted as independent colour facts.
    features = build_features(snapshot)
    return run, manifest, snapshot, features
