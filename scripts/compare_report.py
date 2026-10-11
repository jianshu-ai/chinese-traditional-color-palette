"""Offline, image-first v0.10 comparison report; v0.7/v0.8/v0.9 stay readable.

Manifest paths are absolute or relative to its parent. Result paths are absolute
or relative to the case output_dir. All inputs and the site must stay inside the
explicit project root. Unknown manifest extension fields are preserved/ignored.
Only scene_pipeline's current accepted export is eligible for candidate selection.
Verified rejected attempts may occupy missing slots, explicitly for viewing only.
Run scene_cli export again after changing a plan/review; this renderer does not
replace the pipeline's acceptance gate or independently judge aesthetics.

Optional manifest.summary and cases[].summary accept public text with
source="model_visual_review", calibrated=false and text (a string). Case summaries
may also contain recommendation={solution_id, image_sha256, result_sha256, reason}.
A recommendation is shown only for an accepted image matching both hashes. It is
labelled "主控推荐 · model recommendation", never inferred from scores or judgments.
Unknown extension fields are ignored, including private confirmation records.
Historical v0.6 images remain explicit manifest baselines, outside accepted counts.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import io
import json
import math
import os
from pathlib import Path
import re
import tempfile
import warnings

from PIL import Image


ASSETS = Path(__file__).resolve().parents[1] / "assets" / "comparison"
HEX = re.compile(r"#[0-9a-fA-F]{6}\Z")
SHA = re.compile(r"[0-9a-fA-F]{64}\Z")
ASSET_URL = re.compile(r"assets/[a-zA-Z0-9_.-]+\Z")
CATEGORIES = {"web": "网页", "built_environment": "建筑与街景",
              "print_packaging": "印刷与包装", "apparel": "服饰",
              "toy_product": "玩具与产品", "generic": "其他"}
DIMENSIONS = {"beauty": "整体美感与协调", "organization": "主次与色面组织",
              "scene_fit": "场景适配", "material_naturalness": "自然与材质"}
STATUSES = {"pass": "通过", "fail": "未通过", "unknown": "未知",
            "not_applicable": "不适用"}
BRIEF_STATUSES = {"awaiting_proposal": "待提出目标", "awaiting_confirmation": "待用户确认",
                  "confirmed": "用户已确认目标", "test_confirmed": "测试确认（模拟数据）"}
GOAL_STATUSES = {"pass": "达成", "fail": "未达成", "unknown": "未知"}
FAILURE_STAGES = {"none": "未记录失败", "goal": "目标理解或范围，返回目标确认",
                  "diagnosis": "观察或设计假设，返回诊断与思路",
                  "execution": "执行偏离，返回指令与执行"}
COMPARISONS = {"better": "较原图改善", "similar": "与原图相近", "worse": "不如原图"}
AMBITIONS = {"refine": "细化", "redesign": "重新设计", "bold": "大胆探索"}


class ReportError(ValueError):
    pass


def escape(value):
    return html.escape("" if value is None else str(value), quote=True)


def text_list(value):
    return [str(x) for x in value] if isinstance(value, list) else []


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def score(value):
    return f"{value:g}" if number(value) and 0 <= value <= 100 else "未知"


def percent(value):
    return f"{value:.0%}" if number(value) and 0 <= value <= 1 else "未知"


def compact_judgment_valid(subjective):
    if not isinstance(subjective, dict):
        return False
    judgment = subjective.get("judgment")
    return bool(subjective.get("source") == "model_visual_review"
                and subjective.get("calibrated") is False
                and "dimensions" not in subjective and "total" not in subjective
                and isinstance(judgment, dict)
                and isinstance(judgment.get("comparison"), str)
                and judgment["comparison"] in COMPARISONS
                and isinstance(judgment.get("evidence"), str) and judgment["evidence"].strip()
                and isinstance(judgment.get("tradeoff"), str))


def digest(data):
    return hashlib.sha256(data).hexdigest()


def local_path(value, base, root):
    """Do not interpret URLs, shell text or backslashes as local references."""
    if (not isinstance(value, str) or not value.strip() or "\\" in value
            or any(ord(c) < 32 for c in value) or value.startswith("//")
            or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", value)):
        raise ReportError("缺失或不安全的本地路径")
    path = Path(value)
    path = (path if path.is_absolute() else base / path).resolve()
    if not path.is_relative_to(root):
        raise ReportError("路径超出指定工程根目录")
    return path


def read_json(path):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ReportError(f"JSON 重复字段：{key}")
            result[key] = value
        return result
    def constant(value):
        raise ReportError(f"JSON 非有限数字：{value}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=pairs,
                           parse_constant=constant)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReportError(f"无法读取 JSON：{path.name}（{type(exc).__name__}）") from exc
    if not isinstance(value, dict):
        raise ReportError(f"JSON 根必须是对象：{path.name}")
    return value


def manifest_cases(manifest):
    cases = manifest.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ReportError("manifest.cases 必须是非空数组")
    seen = set()
    for case in cases:
        if not isinstance(case, dict) or not isinstance(case.get("id"), str) or not case["id"]:
            raise ReportError("每个 case 需要非空 id")
        if case["id"] in seen:
            raise ReportError("case id 重复")
        seen.add(case["id"])
        target = case.get("target_count", 5)
        if not isinstance(target, int) or isinstance(target, bool) or not 1 <= target <= 26:
            raise ReportError("target_count 必须为 1–26 的整数")
        if not case.get("input_copy") or not case.get("output_dir"):
            raise ReportError("每个 case 需要 input_copy 和 output_dir")
    return cases


def manifest_summary(manifest):
    cases = manifest_cases(manifest)
    return {"cases_count": len(cases), "expected_images": sum(c.get("target_count", 5) for c in cases),
            "expected_originals": len(cases),
            "baseline_references": sum(len(c.get("baselines", [])) for c in cases),
            "note": "仅清单检查；未读取图片或结果，不代表已生成或已验收"}


def atomic_write(path, data):
    """Publish a complete file, with ordinary permissions, without following links."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or path.parent.is_symlink():
        raise ReportError("输出路径不能为符号链接")
    fd, temporary = tempfile.mkstemp(prefix=".report-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            os.fchmod(handle.fileno(), 0o644)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class ImageCollector:
    def __init__(self, root, site):
        self.root, self.site = root, site
        self.files = []

    def collect(self, value, base, expected=None, **identity):
        record = {**identity, "path": value, "expected_sha256": expected,
                  "sha256": None, "width": None, "height": None, "format": None,
                  "decode_ok": False, "hash_matches": None, "asset_path": None,
                  "asset_sha256": None, "link_decode_ok": False, "error": None}
        self.files.append(record)
        try:
            path = local_path(value, base, self.root)
            record["path"] = path.relative_to(self.root).as_posix()
            raw = path.read_bytes()
            record["sha256"] = digest(raw)
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(raw)) as image:
                    if image.format not in {"PNG", "JPEG", "WEBP"}:
                        raise ReportError("仅接受 PNG、JPEG、WebP 静态图片")
                    if getattr(image, "n_frames", 1) != 1:
                        raise ReportError("动态图不能作为单张最终图")
                    record.update(width=image.width, height=image.height, format=image.format)
                    image.verify()
                with Image.open(io.BytesIO(raw)) as image:
                    image.load()
            record["decode_ok"] = True
            if expected is not None:
                record["hash_matches"] = bool(isinstance(expected, str) and SHA.fullmatch(expected)
                                                and record["sha256"] == expected.lower())
                if not record["hash_matches"]:
                    raise ReportError("文件 SHA 与登记值不一致")
            extension = {"PNG": "png", "JPEG": "jpg", "WEBP": "webp"}[record["format"]]
            relative = f"assets/{record['sha256']}.{extension}"
            destination = self.site / relative
            atomic_write(destination, raw)
            copied = destination.read_bytes()
            record["asset_sha256"] = digest(copied)
            with Image.open(io.BytesIO(copied)) as image:
                image.load()
            record["link_decode_ok"] = record["asset_sha256"] == record["sha256"]
            record["asset_path"] = relative
        except (OSError, ValueError, SyntaxError, Image.DecompressionBombError,
                Image.DecompressionBombWarning) as exc:
            record["error"] = str(exc) if isinstance(exc, ReportError) else f"缺失或无法完整解码（{type(exc).__name__}）"
        return record


def objective_summary(objective, image_sha256=None):
    """Recount observed checks. Never use planned or human scores as measurements."""
    source_checks = objective.get("checks", []) if isinstance(objective, dict) else []
    checks = []
    for raw in source_checks if isinstance(source_checks, list) else []:
        if not isinstance(raw, dict):
            continue
        check = dict(raw)
        status = check.get("status", "unknown")
        if check.get("applicable") is False:
            status = "not_applicable"
        elif status not in {"pass", "fail", "unknown"}:
            status = "unknown"
        if status in {"pass", "fail"}:
            source = check.get("source")
            measured = (isinstance(source, dict)
                        and source.get("method") == "image_pixels_min_pair_contrast"
                        and bool(SHA.fullmatch(str(source.get("image_sha256", ""))))
                        and (image_sha256 is None or source["image_sha256"] == image_sha256))
            if not measured:
                status = "unknown"
                check["reason"] = "缺少有效成图测量来源；不将人工/计划判断计为客观测量"
        check["status"] = status
        checks.append(check)
    applicable = sum(c["status"] != "not_applicable" for c in checks)
    passed = sum(c["status"] == "pass" for c in checks)
    failed = sum(c["status"] == "fail" for c in checks)
    measured = passed + failed
    return {"checks": checks, "applicable_count": applicable, "measured_count": measured,
            "passed_count": passed, "failed_count": failed, "unknown_count": applicable - measured,
            "coverage": measured / applicable if applicable else None,
            "pass_rate": passed / measured if measured else None}


def render_subjective(subjective, version=None):
    subjective = subjective if isinstance(subjective, dict) else {}
    if version in {"0.9.0", "0.10.0"} or "judgment" in subjective:
        judgment = subjective.get("judgment")
        judgment = judgment if isinstance(judgment, dict) else {}
        comparison = judgment.get("comparison")
        label = COMPARISONS.get(comparison, "未记录有效判断") if isinstance(comparison, str) else "未记录有效判断"
        source = subjective.get("source", "未记录")
        calibration = "未校准" if subjective.get("calibrated") is False else "校准状态未核实"
        return ('<details class="score subjective"><summary>模型看图判断</summary>'
                f'<p>{label}</p><p>{escape(judgment.get("evidence") or "未记录画面依据")}</p>'
                + (f'<p>取舍：{escape(judgment["tradeoff"])}</p>' if judgment.get("tradeoff") else '')
                + f'<p class="note">来源：{escape(source)} · {calibration}；不代表用户偏好。</p></details>')
    dimensions = subjective.get("dimensions", {})
    weights = subjective.get("weights", {})
    rows = []
    for key, label in DIMENSIONS.items():
        item = dimensions.get(key, {}) if isinstance(dimensions, dict) else {}
        item = item if isinstance(item, dict) else {}
        weight = weights.get(key) if isinstance(weights, dict) else None
        rows.append(f"<tr><th>{label}</th><td>{score(item.get('score'))}</td>"
                    f"<td>{percent(weight)}</td><td>{escape(item.get('evidence', '未记录'))}</td></tr>")
    source = subjective.get("source", "未记录")
    source_label = "模型看图评审" if source == "model_visual_review" else str(source)
    return ('<details class="score subjective"><summary>S 主观 · '
            f"{score(subjective.get('total'))} / 100</summary><p class=\"note\">"
            f"来源：{escape(source_label)}；暂定框架，分数不代表用户已确认的 70 分。</p>"
            '<table><thead><tr><th>维度</th><th>分数</th><th>权重</th><th>图像依据</th></tr></thead>'
            f"<tbody>{''.join(rows)}</tbody></table></details>")


def render_objective(objective, image_sha256=None):
    summary = objective_summary(objective, image_sha256)
    rows = []
    for check in summary["checks"]:
        planned = check.get("planned") or {}
        planned = planned if isinstance(planned, dict) else {}
        value = check.get("value")
        detail = f"{value:g}" if number(value) else "—"
        threshold = check.get("threshold")
        if number(threshold):
            detail += f" / 门槛 {threshold:g}"
        planned_value = planned.get("value")
        planned_text = (f"{planned_value:g}" if number(planned_value) else "未知")
        source = check.get("source", "未知")
        if isinstance(source, dict):
            source = ("图像像素采样 · " + str(source.get("color_space", "色彩空间未知"))
                      if source.get("method") == "image_pixels_min_pair_contrast" else "测量来源未知")
        rows.append(f"<tr><th>{escape(check.get('id', '未命名'))}</th>"
                    f"<td>{STATUSES[check['status']]} · {detail}</td>"
                    f"<td>{escape(source)}<br>{escape(check.get('reason', ''))}</td>"
                    f"<td>{planned_text}（仅计划）</td></tr>")
    return ('<details class="score objective"><summary>O 客观 · 已测通过率 '
            f"{percent(summary['pass_rate'])} · 覆盖 {percent(summary['coverage'])}"
            f" · {summary['unknown_count']} 项未知</summary>"
            '<p class="note">成图测量与计划值分列；O 不衡量美观，不与 S 平均，不作跨类别排名。</p>'
            + ('<table><thead><tr><th>检查</th><th>成图</th><th>来源与限制</th><th>计划值</th></tr></thead>'
               f"<tbody>{''.join(rows)}</tbody></table>" if rows else '<p class="note">未登记适用检查；O 未知。</p>')
            + '</details>')


def render_colors(colors, *, compact=False):
    rows = []
    for color in colors if isinstance(colors, list) else []:
        if not isinstance(color, dict):
            continue
        hexadecimal = color.get("hex", "")
        swatch = (f'<span class="swatch" style="background:{hexadecimal}" aria-hidden="true"></span>'
                  if isinstance(hexadecimal, str) and HEX.fullmatch(hexadecimal) else "")
        areas = color.get("areas", [])
        locations = "、".join(" · ".join(str(a[k]) for k in ("id", "target_id", "location") if a.get(k))
                            for a in (areas if isinstance(areas, list) else []) if isinstance(a, dict))
        name = escape(color.get('name', '色名未提供'))
        role = str(color.get('role') or '用途未提供')
        if compact:
            # Shorten only the overview; the full authored role stays in details.
            short_role = re.split(r'[；;。\n]', role, maxsplit=1)[0].strip() or role
            short_role = short_role if len(short_role) <= 24 else short_role[:23] + '…'
            rows.append(f'<li>{swatch}<span class="color-name">{name}</span>'
                        f'<span class="color-role">{escape(short_role)}</span></li>')
        else:
            rows.append(f"<li>{swatch}<span>{name} {escape(hexadecimal)} · {escape(role)}"
                        f" · {escape(locations)}</span></li>")
    return f'<ul class="colors">{"".join(rows)}</ul>' if rows else '<p class="note">尚无派生颜色事实。</p>'


def render_target_palette(colors):
    if not colors:
        return ('<section class="target-palette"><p class="note">目标色卡未知：'
                '缺少与该图调用匹配的冻结计划或色表证据。</p></section>')
    return ('<section class="target-palette" aria-label="官方目标色卡">'
            '<p class="palette-label">官方目标色卡 · 不代表成图像素精确匹配</p>'
            + render_colors(colors, compact=True)
            + '<details class="palette"><summary>HEX 与完整用途、区域</summary>'
            + render_colors(colors) + '</details></section>')


def canonical_digest(value):
    """The existing pc-canonical-v1 representation used by scene plan bindings."""
    return digest(json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False).encode("utf-8"))


def call_target_colors(result, output, call):
    """Read target colours for this image's call, never the latest plan by default.

    Reproduce scene_pipeline.plan_sha's v0.7–v0.10 projection against the export's
    frozen context. Plans are retained by content hash; scene.json is only a
    fallback source of a plan that must pass the same call binding. Nothing here
    changes acceptance, reviews, or any run artifact.
    """
    try:
        expected = call["plan_sha256"]
        if not isinstance(expected, str) or not SHA.fullmatch(expected):
            return None
        binding = result["palette_binding"]
        snapshot_path = local_path(binding["snapshot_path"], output, output)
        raw = snapshot_path.read_bytes()
        snapshot = read_json(snapshot_path)
        if (digest(raw) != binding["palette_content_sha256"]
                or canonical_digest(snapshot) != binding["palette_content_sha256"]
                or any(snapshot.get(k) != binding.get(k)
                       for k in ("palette_id", "schema_version", "color_space"))):
            return None
        facts = {c["color_id"]: c for c in snapshot["colors"] if c["enabled"] is True}
        if len(facts) != sum(c["enabled"] is True for c in snapshot["colors"]):
            return None

        def plans():
            directory = local_path("plans", output, output)
            for path in sorted(directory.glob("*.json")):
                if not SHA.fullmatch(path.stem):
                    continue
                try:
                    plan = read_json(local_path(str(path), output, output))
                    if canonical_digest(plan) == path.stem:
                        yield plan
                except (OSError, ValueError):
                    continue
            try:
                state = read_json(local_path("scene.json", output, output))
                plan = state.get("plan")
                if (state.get("case_id") == result.get("case_id") and isinstance(plan, dict)
                        and canonical_digest(plan) == state.get("plan_record_sha256")):
                    yield plan
            except (OSError, ValueError):
                pass

        for plan in plans():
            solutions = plan.get("solutions")
            if not isinstance(solutions, list):
                continue
            matches = [s for s in solutions if isinstance(s, dict) and s.get("id") == call["solution_id"]]
            if len(matches) != 1 or "diagnosis" not in plan:
                continue
            sol = matches[0]
            record = {"diagnosis": plan["diagnosis"], "solution": sol,
                      "source_sha256": result["source_image"]["sha256"],
                      "palette_binding": binding, "plugin": result["plugin"]}
            if result["schema_version"] in {"0.8.0", "0.9.0", "0.10.0"}:
                record["brief_binding"] = plan.get("brief_binding")
            if result["schema_version"] == "0.10.0":
                record["round_binding"] = plan.get("round_binding")
                record["feedback_application"] = plan.get("feedback_application")
            if canonical_digest(record) != expected:
                continue
            colors = []
            for selected in sol["colors"]:
                fact = facts[selected["color_id"]]
                rgb = [fact["rgb"][k] for k in ("R", "G", "B")]
                if (not all(type(v) is int and 0 <= v <= 255 for v in rgb)
                        or not isinstance(fact["name"], str) or not fact["name"].strip()):
                    return None
                colors.append({"color_id": fact["color_id"], "name": fact["name"],
                               "hex": "#" + ''.join(f'{v:02X}' for v in rgb),
                               "role": selected["role"], "areas": selected["areas"]})
            return colors or None
    except (OSError, ValueError, KeyError, TypeError):
        # Missing, unsafe, or stale evidence affects only the target colour card.
        pass
    return None


def safe_asset_url(value):
    return value if isinstance(value, str) and ASSET_URL.fullmatch(value) else ""


def brief_context(result):
    """Public, allowlisted projection. Never copy confirmation text into a report."""
    if result.get("schema_version") not in {"0.8.0", "0.9.0", "0.10.0"} and not any(
            key in result for key in ("confirmed_brief", "brief_status", "run_mode")):
        return None
    raw = result.get("confirmed_brief")
    brief = None
    if isinstance(raw, dict):
        def public_text(value):
            return value if isinstance(value, str) else ""
        confirmation = raw.get("confirmation")
        confirmation = confirmation if isinstance(confirmation, dict) else {}
        goals = raw.get("goals", [])
        brief = {"version": raw.get("version") if isinstance(raw.get("version"), (str, int)) else None,
                 "record_sha256": public_text(raw.get("record_sha256")),
                 "summary": public_text(raw.get("summary")),
                 "goals": [{key: public_text(goal.get(key)) for key in ("id", "subject", "outcome")}
                           for goal in (goals if isinstance(goals, list) else []) if isinstance(goal, dict)],
                 "confirmation": {"source": public_text(confirmation.get("source"))}}
        for key in ("supporting_changes", "preserve", "priorities"):
            values = raw.get(key)
            brief[key] = [item for item in values if isinstance(item, str)] if isinstance(values, list) else []
    status = result.get("brief_status")
    mode = result.get("run_mode")
    source = brief["confirmation"]["source"] if brief else ""
    return {"brief_status": status if isinstance(status, str) else "unknown",
            "run_mode": mode if isinstance(mode, str) else "unknown", "confirmed_brief": brief,
            "is_test": mode == "test" or status == "test_confirmed" or source.lower().startswith("test")}


def goal_attainment(solution, context):
    """Describe recorded visual judgments; S/O and plan targets cannot fill gaps."""
    brief = context.get("confirmed_brief") or {}
    goals = brief.get("goals", [])
    raw_checks = solution.get("goal_checks")
    by_id = {}
    for check in raw_checks if isinstance(raw_checks, list) else []:
        if isinstance(check, dict) and isinstance(check.get("goal_id"), str):
            by_id.setdefault(check["goal_id"], []).append(check)
    binding = solution.get("goal_binding")
    binding_status = "unbound"
    if binding is not None:
        binding_sha = binding.get("record_sha256") if isinstance(binding, dict) else binding
        binding_status = ("match" if isinstance(binding_sha, str) and SHA.fullmatch(binding_sha)
                          and binding_sha.lower() == brief.get("record_sha256", "").lower() else "mismatch")
    confirmed = bool(goals and context.get("brief_status") in {"confirmed", "test_confirmed"})
    checks = []
    for goal in goals:
        matches = by_id.get(goal["id"], [])
        check = matches[0] if len(matches) == 1 else {}
        status = check.get("status")
        note = check.get("note")
        note = note if isinstance(note, str) else ""
        if not isinstance(status, str) or status not in GOAL_STATUSES or (status == "pass" and not note.strip()):
            status = "unknown"
        if not confirmed or binding_status != "match":
            status = "unknown"
        checks.append({**goal, "status": status, "note": note or "未记录该目标的实际画面依据"})
    stage = solution.get("failure_stage")
    return {"checks": checks, "binding_status": binding_status, "confirmed": confirmed,
            "failure_stage": stage if isinstance(stage, str) and stage in FAILURE_STAGES else None,
            "passed_count": sum(c["status"] == "pass" for c in checks),
            "failed_count": sum(c["status"] == "fail" for c in checks),
            "unknown_count": sum(c["status"] == "unknown" for c in checks)}


def render_goal_panel(context):
    if context is None:
        return ('<div class="goal-panel"><h3>用户目标 · 历史记录</h3>'
                '<p class="note">旧记录未提供独立确认目标；目标达成未知。旧接受状态不代表用户认可新版目标。</p></div>')
    brief = context.get("confirmed_brief") or {}
    status = context["brief_status"]
    label = BRIEF_STATUSES.get(status, "确认状态未知")
    if not brief.get("goals") and status in {"confirmed", "test_confirmed"}:
        label = "确认记录缺失（导出声称已确认）"
    if context["is_test"]:
        label = "测试数据 · " + ("测试确认（模拟数据）" if brief.get("goals") and status in {"confirmed", "test_confirmed"} else label)
    source = (brief.get("confirmation") or {}).get("source") or "未记录"
    mode = {"live": "实际运行", "test": "测试运行"}.get(context["run_mode"], "运行模式未知")
    goals = ''.join(f'<li>{escape(g["id"])} · {escape(g["subject"])}：{escape(g["outcome"])}</li>'
                    for g in brief.get("goals", []))
    scope = ''.join(f'<p>{label}：{escape("；".join(brief[key]))}</p>'
                    for key, label in (("supporting_changes", "辅助修改"), ("preserve", "保持项"),
                                       ("priorities", "优先事项")) if brief.get(key))
    return ('<div class="goal-panel"><h3>用户目标与确认状态</h3>'
            f'<p class="note">{escape(label)} · {mode} · 来源：{escape(source)}'
            f' · 目标版本：{escape(brief.get("version", "未记录"))}</p>'
            f'<p>{escape(brief.get("summary") or "暂无独立确认目标摘要")}</p>'
            + (f'<ul>{goals}</ul>' if goals else '<p class="note">尚无可对照的目标条目。</p>')
            + scope + '<p class="note">确认的是目标与范围，不是成图认可；目标达成须看实际画面。'
            '用户答复原文不在报告中展示；来源记录不等于独立身份验证。</p>'
            + ('<p class="decision-note">本案例含模拟测试确认，不代表真实用户授权或偏好。</p>'
               if context["is_test"] else '') + '</div>')


def render_goal_attainment(attainment):
    checks = attainment["checks"]
    counts = (f'{attainment["passed_count"]} 项达成 · {attainment["failed_count"]} 项未达成 · '
              f'{attainment["unknown_count"]} 项未知' if checks else '未知（无独立目标可对照）')
    rows = ''.join(f'<li>{escape(c["id"])} · {escape(c["subject"])}：{escape(c["outcome"])}'
                   f' — {GOAL_STATUSES[c["status"]]}<p class="note">实际画面依据：{escape(c["note"])}</p></li>'
                   for c in checks)
    binding = {"match": "目标记录 SHA 与评审引用一致；仅证明引用一致性",
               "mismatch": "目标绑定不一致，不能据此确认当前目标达成",
               "unbound": "未提供独立目标绑定（兼容旧评审读取）；不能作为本轮正式接受候选"}[attainment["binding_status"]]
    stage = FAILURE_STAGES.get(attainment["failure_stage"], "未记录（兼容旧评审）")
    return ('<div class="goal-attainment"><h3>用户目标达成 · 看图评审记录</h3>'
            f'<p>{counts}</p>' + (f'<ul>{rows}</ul>' if rows else '')
            + f'<p class="note">{binding}。失败归因：{stage}。</p>'
            '<p class="note">这是语义与视觉判断，不是机器验证的美观；与主观评审、客观测量及作者计划执行分别呈现。'
            '同一模型复核不称独立盲评，也不代表用户已认可。</p></div>')


def render_card(card):
    asset = safe_asset_url(card.get("asset"))
    kind = card.get("kind", "solution")
    kind = kind if kind in {"solution", "original", "baseline"} else "solution"
    title = escape(card.get("title", ""))
    anonymous = escape(card.get("anonymous", "候选"))
    status = escape(card.get("status", "未接受"))
    warning = " warning" if not card.get("accepted") and kind == "solution" else ""
    media = (f'<button class="media" type="button" data-image="{asset}" aria-label="放大图片：{title}">'
             f'<img src="{asset}" alt="{anonymous}" loading="lazy" decoding="async"></button>'
             if asset else f'<div class="media placeholder"><div><strong>{status}</strong>'
             '<span>详情见下方记录</span></div></div>')
    details = ""
    logic = ""
    solution = card.get("solution")
    if isinstance(solution, dict):
        limits = text_list(solution.get("limitations")) + text_list(solution.get("warnings"))
        design = escape(solution.get("design_logic", "说明未提供"))
        logic = f'<p class="logic" title="{design}">{design}</p>'
        ambition = solution.get("ambition")
        ambition = AMBITIONS.get(ambition, "") if isinstance(ambition, str) else ""
        details = (f'<p>作者方案意图：{design}</p>'
                   + (f'<p>设计幅度：{ambition}</p>' if ambition else '')
                   + render_subjective(solution.get("subjective"), card.get("schema_version"))
                   + render_objective(solution.get("objective"), solution.get("image_sha256"))
                   + f'<p class="note">导出接受理由（评审记录）：{escape(solution.get("reason", "未记录"))}</p>'
                   + (f'<p class="decision-note">限制：{escape("；".join(limits))}</p>' if limits else ""))
        integrity = solution.get("integrity")
        integrity = integrity if isinstance(integrity, dict) else {}
        hashes = ''.join(f'<dt>{label}</dt><dd>{escape(solution.get(key, "未记录"))}</dd>'
                         for key, label in (("image_sha256", "图片 SHA"), ("plan_sha256", "计划 SHA"),
                                            ("prompt_sha256", "提示词 SHA")))
        details += ('<details class="integrity"><summary>计划与文件绑定</summary>'
                    f'<p>导出完整性状态：{escape(integrity.get("status", "未记录"))}</p><dl>{hashes}</dl></details>')
        content = solution.get("content_check")
        if isinstance(content, dict):
            details += ('<details class="content-check"><summary>内容与范围评审</summary>'
                        f'<p>{escape(content.get("status", "unknown"))} · {escape(content.get("note", ""))}</p></details>')
    elif card.get("message"):
        details = f'<p class="note">{escape(card["message"])}</p>'
    if card.get("goal_attainment"):
        details = render_goal_attainment(card["goal_attainment"]) + details
    recommendation = card.get("recommendation")
    badge = '<span class="recommendation">主控推荐 · model recommendation</span>' if recommendation else ''
    if recommendation:
        details += f'<p class="note">主控看图自荐依据（未校准）：{escape(recommendation["reason"])}</p>'
    if details:
        details = f'<details class="card-details"><summary>查看记录</summary>{details}</details>'
    action = (f'<div class="card-actions"><button type="button" data-choice="{escape(card["choice"])}" '
              'aria-pressed="false">选这张</button></div>' if card.get("choice") and asset else "")
    palette = render_target_palette(card.get("target_colors")) if kind == "solution" and asset else ""
    return (f'<article class="card {kind}"><div class="card-head"><h3>'
            f'<span class="identity">{title}</span><span class="anonymous">{anonymous}</span></h3>'
            f'<span class="status{warning}">{status}</span></div>{media}'
            f'<div class="card-body">{badge}{logic}{palette}{details}</div>{action}</article>')


def render_case(case, show_goals=False):
    diagnosis = case.get("diagnosis")
    diagnosis = diagnosis if isinstance(diagnosis, dict) else {}
    summary = escape(diagnosis.get("purpose", case.get("request", "")))
    problems = diagnosis.get("problems", [])
    problem_text = "；".join(str(p.get("description", "")) if isinstance(p, dict) else str(p)
                           for p in (problems if isinstance(problems, list) else []))
    strengths = "；".join(text_list(diagnosis.get("preserve", diagnosis.get("strengths"))))
    notes = "；".join(case.get("notes", []))
    extra = f'<p class="decision-note">{escape(notes)}</p>' if notes else ""
    if problem_text or strengths:
        extra += ('<details><summary>诊断与保留项</summary>'
                  f'<p>本次问题：{escape(problem_text or "未记录")}</p>'
                  f'<p>保留优点：{escape(strengths or "未记录")}</p></details>')
    goals = ""
    if show_goals or case.get("goal_context"):
        goals = render_goal_panel(case.get("goal_context"))
        summary = "作者诊断（观察与假设）：" + summary
        extra = extra.replace("诊断与保留项</summary>", "作者诊断与保留项</summary>")
        targets = diagnosis.get("targets", [])
        target_rows = ''.join(f'<li>{escape(t.get("id"))}：{escape(t.get("description"))}'
                              f'<span class="note"> · 目标 {escape("、".join(text_list(t.get("goal_ids"))))}</span></li>'
                              for t in (targets if isinstance(targets, list) else []) if isinstance(t, dict))
        if target_rows:
            extra += ('<details class="plan-targets"><summary>作者计划目标 · 执行核对依据</summary>'
                      f'<ul>{target_rows}</ul><p class="note">计划目标不替代用户目标；'
                      '执行情况以当前图评审为准，不能从色号或文件校验推断。</p></details>')
    for key, label in (("objective_checks", "客观检查计划"), ("fixed_colors", "固定颜色与来源")):
        if diagnosis.get(key):
            extra += (f'<details class="plan-data"><summary>{label}</summary><pre>'
                      f'{escape(json.dumps(diagnosis[key], ensure_ascii=False, indent=2))}</pre></details>')
    baselines = ""
    if case.get("baselines"):
        baselines = ('<details class="history"><summary>历史参考 · 非用户已选最优</summary>'
                     '<p class="note">仅展示清单明确提供的代表。此处不据此宣称新版更好。</p>'
                     '<div class="grid baseline-grid">' + ''.join(map(render_card, case["baselines"])) + '</div></details>')
    calls = case.get("calls", {})
    legacy = show_goals and case.get("goal_context") is None
    accepted_label = "历史已接受" if legacy else "已接受"
    cards = [dict(card, status="历史已接受") if legacy and card.get("accepted") else card
             for card in case["cards"]]
    case_summary = case.get("summary", {})
    summary_line = (f'<p class="case-summary" title="{escape(case_summary["text"])}">'
                    f'模型总结 · {escape(case_summary["text"])}</p>' if case_summary.get("text") else '')
    test_badge = '<span class="tag">测试数据</span>' if (case.get("goal_context") or {}).get("is_test") else ''
    round_note = (f'<p class="round-dialogue">第{case["round_number"]}轮 · ' +
                  ('会话已结束。' if case.get("session_ended") else '这轮继续还是结束？继续请在对话中说明喜欢、不喜欢或修改重点（可说无新意见），以及5张还是10张。') +
                  '本页选择只记录偏好，不授权继续生图。</p>') if case.get("round_number") else ""
    return (f'<section class="case" data-case="{escape(case["id"])}" '
            f'data-source-case="{escape(case.get("source_case_id", case["id"]))}" '
            f'data-round="{escape(case.get("round_number", ""))}" data-result-sha256="{escape(case.get("result_sha256", ""))}" '
            f'data-category="{escape(case["category"])}" data-original="{safe_asset_url(case["original"].get("asset"))}">'
            f'<div class="case-head"><h2>{escape(case["label"])}</h2><span class="tag">'
            f'{escape(CATEGORIES.get(case["category"], case["category"]))}</span>'
            f'<span class="note">{accepted_label} {case["accepted_count"]} / {case["target_count"]} · '
            f'{escape(case["status"])}</span>{test_badge}</div>{summary_line}{round_note}<div class="grid current-grid">'
            + render_card(case["original"]) + ''.join(map(render_card, cards)) + '</div>'
            '<div class="selection"><span class="note">当前场景：</span>'
            '<button type="button" data-choice="tie" aria-pressed="false">差不多</button>'
            '<button type="button" data-choice="none" aria-pressed="false">都不喜欢</button>'
            '<span class="selection-output" aria-live="polite"></span></div>'
            '<details class="case-details"><summary>目标确认、诊断与调用记录</summary>'
            f'{goals}<div class="case-intro"><p>{summary}</p>{extra}</div>'
            f'<p class="note">调用 {calls.get("total", 0)} 次（重试 {calls.get("retry", 0)} 次，'
            f'失败 {calls.get("failed", 0)} 次）；按 attempts 逐次登记计数。</p></details>'
            + baselines + '</section>')


def render_document(cases, audit, title="中国传统色 · 方案对比"):
    categories = sorted(set(c["category"] for c in cases))
    options = ''.join(f'<option value="{escape(c)}">{escape(CATEGORIES.get(c, c))}</option>' for c in categories)
    current = any(c.get("goal_context") is not None for c in cases)
    mixed = current and any(c.get("goal_context") is None for c in cases)
    accepted_label = "方案已接受（含历史）" if mixed else "新方案已接受"
    version = "v0.10" if audit["schema_version"] == "0.10.0" else ("v0.9" if audit["schema_version"] == "0.9.0" else ("v0.8" if current else "v0.7"))
    picture_label = "只看图"
    shuffle_label = "隐藏名称并重排" if current else "盲化重排"
    shuffle_note = "隐藏名称不构成独立盲评；模型评审不代表用户偏好。" if current else "重排仅帮助偏好比较，不是独立实验。"
    batch_summary = audit.get("summary", {}).get("text")
    summary_line = (f'<p class="batch-summary" title="{escape(batch_summary)}">模型批次总结 · {escape(batch_summary)}</p>'
                    if batch_summary else '')
    return f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'">
<title>{escape(title)}</title><link rel="stylesheet" href="assets/report.css"><script defer src="assets/report.js"></script></head>
<body data-report-id="{escape(audit['report_id'])}"><header><div class="eyebrow">TRADITIONAL COLOUR / {version}</div><h1>{escape(title)}</h1>
{summary_line}
<div class="summary"><span><strong>{audit['cases_count']}</strong>场景</span><span><strong>{audit['accepted_count']} / {audit['expected_images']}</strong>{accepted_label}</span><span><strong>{audit['missing_count']}</strong>候选缺口</span><span>{escape(audit['status'])}</span></div></header>
<div class="toolbar"><label>类别<select id="category"><option value="">全部场景</option>{options}</select></label>
<label><input type="checkbox" id="picture-mode">{picture_label}</label><button type="button" id="shuffle">{shuffle_label}</button>
</div>
<main>{''.join(render_case(c, current) for c in cases)}</main><footer>
<details class="report-details"><summary>报告说明、文件审计与本地选择</summary>
<p>{shuffle_note} model recommendation 仅表示有记录的模型自荐，不按分数排序。</p>
<p>已拒绝的图片仅供查看，仍计为候选缺口。历史参考不计入本轮接受数量。</p>
<p id="storage-note">选择仅存于本机浏览器，不会外传，也不授权生图；导出可留存。换浏览器或清理存储后可能丢失。</p>
<p><button type="button" id="export-choices">导出本地选择</button> <button type="button" id="clear-choices">清空本地选择</button> <a href="audit.json">查看文件审计</a></p>
<p>本页不作审美自动验收。修改计划、图像或评审后，请重新执行 scene export，再构建本页。</p></details></footer>
<dialog id="viewer" aria-label="图片对比"><div class="viewer-tools"><h2>图片</h2><button type="button" id="compare" aria-pressed="false">并排原图</button><button type="button" id="switch-image">切换原图</button><button type="button" id="close-viewer">关闭</button></div>
<div class="viewer-panes"><figure class="reference-pane"><figcaption>原图</figcaption><img id="viewer-reference" alt="原图"></figure><figure><figcaption id="viewer-caption">当前图</figcaption><img id="viewer-image" alt="当前图"></figure></div></dialog>
</body></html>'''


def public_summary(raw, cards=(), result_sha256=None):
    """Only explicit public summary fields; never infer or sort recommendations."""
    if (not isinstance(raw, dict) or raw.get("source") != "model_visual_review"
            or raw.get("calibrated") is not False):
        return {}
    summary = {"source": "model_visual_review", "calibrated": False}
    if isinstance(raw.get("text"), str) and raw["text"].strip():
        summary["text"] = raw["text"].strip()
    recommendation = raw.get("recommendation")
    if isinstance(recommendation, dict):
        for card in cards:
            solution = card.get("solution") or {}
            if (card.get("accepted") and card.get("asset")
                    and recommendation.get("solution_id") == solution.get("id")
                    and recommendation.get("image_sha256") == solution.get("image_sha256")
                    and result_sha256 and recommendation.get("result_sha256") == result_sha256
                    and isinstance(recommendation.get("reason"), str) and recommendation["reason"].strip()):
                summary["recommendation"] = {key: recommendation[key] for key in
                                              ("solution_id", "image_sha256", "result_sha256", "reason")}
                break
    return summary if "text" in summary or "recommendation" in summary else {}


def load_case(entry, base, root, collector, skip_baselines=False):
    case_id = entry["id"]
    target = entry.get("target_count", 5)
    ids = [chr(65 + i) for i in range(target)]
    notes, issues = [], []
    result, result_path, result_hash = {}, None, None
    try:
        output = local_path(entry["output_dir"], base, root)
        result_path = local_path(entry.get("result_path", str(output / "result.json")), base, root)
        if not result_path.is_relative_to(output):
            raise ReportError("result_path 必须在当前 case output_dir 内")
        if result_path.exists():
            result = read_json(result_path)
            result_hash = digest(result_path.read_bytes())
        else:
            notes.append("尚未导出 result.json")
    except ReportError as exc:
        output = base
        issues.append(str(exc))
    if result:
        if result.get("schema_version") not in {"0.7.0", "0.8.0", "0.9.0", "0.10.0"}:
            # Preserve historical fallback output, including unsupported exports.
            issues.append("result 不是支持的 v0.7–v0.10 导出；旧图片可作为历史参考")
        if result.get("case_id") != entry.get("case_id", case_id):
            issues.append("result.case_id 与清单不一致")
        if result.get("schema_version") == "0.10.0":
            if target not in (5, 10):
                issues.append("v0.10 每轮必须为5或10张")
            if result.get("round_number") != entry.get("round_number", result.get("round_number")):
                issues.append("轮次与清单不一致")
        if result.get("target_count") != target:
            issues.append("result 目标数量与清单不一致")
        if not isinstance(result.get("palette_binding"), dict) or not result.get("palette_binding"):
            issues.append("缺少色表绑定")
    context = brief_context(result)
    if context is not None and (not context["confirmed_brief"] or context["brief_status"] not in {"confirmed", "test_confirmed"}):
        notes.append("本轮目标尚未确认；不能将作者诊断视为用户确认或生成许可")
    exported_source = result.get("source_image") or {}
    exported_source = exported_source if isinstance(exported_source, dict) else {}
    expected_source = entry.get("source_sha256", exported_source.get("sha256"))
    original = collector.collect(entry["input_copy"], base, expected_source, case_id=case_id, role="original")
    if result and exported_source.get("sha256") != original["sha256"]:
        issues.append("导出原图 SHA 与清单原图不一致")
    if not original["asset_path"]:
        issues.append("原图缺失、无法解码或 SHA 不一致")
    solutions = result.get("solutions", [])
    if not isinstance(solutions, list):
        solutions = []
        issues.append("solutions 不是数组")
    by_id = {}
    for solution in solutions:
        if not isinstance(solution, dict) or solution.get("id") not in ids:
            issues.append("导出含目标范围外方案")
            continue
        if solution["id"] in by_id:
            issues.append("导出含重复方案 ID")
        by_id[solution["id"]] = solution
    attempts = result.get("attempts", [])
    attempts = [a for a in attempts if isinstance(a, dict)] if isinstance(attempts, list) else []
    missing_details = result.get("missing_details", [])
    missing_details = {d.get("id"): d for d in missing_details if isinstance(d, dict)} if isinstance(missing_details, list) else {}
    solution_statuses = result.get("solution_statuses", [])
    if isinstance(solution_statuses, list):
        missing_details.update({d.get("id"): d for d in solution_statuses if isinstance(d, dict)})
    declared_missing = result.get("missing_solution_ids", [])
    cards, records, image_shas = [], [], set()
    for solution_id in ids:
        solution = by_id.get(solution_id)
        latest = next((a for a in reversed(attempts) if a.get("solution_id") == solution_id), {})
        detail = missing_details.get(solution_id, {})
        attainment = goal_attainment(solution or detail, context) if context is not None else None
        errors = list(issues)
        image_record = None
        accepted = False
        state = "missing"
        reason = str(detail.get("reason") or latest.get("error") or "尚无当前可接受的新图")
        if solution:
            image_record = collector.collect(solution.get("image_path"), output, solution.get("image_sha256"),
                                             case_id=case_id, role="final", solution_id=solution_id)
            if solution.get("decision") != "accept":
                errors.append("导出未接受此方案")
            if not image_record["asset_path"] or image_record["hash_matches"] is not True:
                errors.append(image_record["error"] or "缺少当前图 SHA")
            if not isinstance(solution.get("plan_sha256"), str) or not SHA.fullmatch(solution["plan_sha256"]):
                errors.append("缺少有效计划 SHA")
            if solution_id in declared_missing:
                errors.append("该方案同时出现在导出缺口中")
            if detail.get("status") and detail["status"] != "accepted":
                errors.append("逐方案状态与接受导出不一致")
            # The prompt is part of the exported plan/image binding, not a web link.
            try:
                prompt = local_path(solution.get("prompt_path"), output, root)
                if digest(prompt.read_bytes()) != solution.get("prompt_sha256"):
                    errors.append("提示词 SHA 不一致")
            except (OSError, ReportError):
                errors.append("提示词缺失或路径无效")
            if image_record["sha256"] == original["sha256"]:
                errors.append("最终文件与原图相同，不能充当新方案")
            if image_record["sha256"] in image_shas:
                errors.append("同场景重复图片，不能重复计为候选")
            if objective_summary(solution.get("objective"), solution.get("image_sha256"))["failed_count"]:
                errors.append("导出仍有已知客观失败")
            if result.get("schema_version") in {"0.9.0", "0.10.0"}:
                if not compact_judgment_valid(solution.get("subjective")):
                    errors.append("compact 看图判断缺失或格式不符")
                elif solution["subjective"]["judgment"]["comparison"] == "worse":
                    errors.append("导出看图判断仍为不如原图")
            if attainment is not None:
                if not attainment["confirmed"]:
                    errors.append("缺少本轮已确认目标，不能展示为当前接受候选")
                if attainment["binding_status"] != "match":
                    errors.append("缺少当前确认目标绑定" if attainment["binding_status"] == "unbound"
                                  else "目标绑定与当前确认记录不一致")
                if attainment["failed_count"]:
                    errors.append("导出仍有未达成的用户目标")
                if not attainment["checks"] or attainment["unknown_count"]:
                    errors.append("用户目标评审缺项、未知或缺少画面依据；须全部目标有证据通过")
                if attainment["failure_stage"] != "none":
                    errors.append("未明确记录 failure_stage=none" if attainment["failure_stage"] is None
                                  else "导出仍记录未解决的失败归因")
            accepted = not errors
            if accepted:
                image_shas.add(image_record["sha256"])
                state, reason = "accepted", str(solution.get("reason", ""))
            else:
                state, reason = "invalid", "；".join(errors)
        else:
            if latest.get("image_path"):
                image_record = collector.collect(latest["image_path"], output, latest.get("image_sha256"),
                                                 case_id=case_id, role="unaccepted", solution_id=solution_id)
                state = "unaccepted" if image_record["asset_path"] else "missing"
                reason = str(detail.get("reason") or "已有成图但未导出接受；拒绝、待评或过期以运行记录为准")
            elif latest.get("status") == "failed":
                reason = str(latest.get("error") or "图像调用失败")
            elif latest.get("status") == "pending":
                reason = "图像调用已登记，尚未收到可用结果"
            if detail.get("decision") in {"reject", "rejected"}:
                state = "rejected"
            if detail.get("status") in {"rejected", "stale"}:
                state = detail["status"]
            elif detail.get("status") == "pending" and image_record and image_record["asset_path"]:
                state = "pending"
            if issues:
                reason = "；".join(issues + [reason])
        statuses = {"accepted": "已接受", "missing": "缺图", "unaccepted": "未接受",
                    "rejected": "已拒绝", "invalid": "证据失效", "stale": "证据过期", "pending": "待评"}
        rejected_visible = bool(not solution and state == "rejected" and not issues
                                and latest.get("status") == "registered" and image_record
                                and image_record["asset_path"] and image_record["hash_matches"] is True)
        title = f"{solution_id} · {solution.get('title', '')}" if solution else f"方案 {solution_id}"
        cards.append({"kind": "solution", "title": title, "anonymous": f"候选 {len(cards) + 1}",
                      "status": "已拒绝 · 仅供查看" if rejected_visible else statuses[state],
                      "message": reason, "accepted": accepted, "schema_version": result.get("schema_version"),
                      "asset": image_record["asset_path"] if accepted or rejected_visible else None,
                      "solution": solution if accepted else None,
                      "choice": f"solution:{solution_id}:{image_record['sha256']}" if accepted else None})
        if accepted:
            cards[-1]["target_colors"] = solution.get("colors")
        elif rejected_visible:
            cards[-1]["target_colors"] = call_target_colors(result, output, latest)
        if attainment is not None:
            cards[-1]["goal_attainment"] = attainment
        records.append({"id": solution_id, "state": state, "reason": reason,
                        "rejected_image_displayed": rejected_visible,
                        "image_sha256": image_record["sha256"] if image_record else None,
                        "plan_sha256": solution.get("plan_sha256") if solution else None})
        if attainment is not None:
            records[-1]["goal_attainment"] = attainment
    baselines = []
    for index, baseline in enumerate(entry.get("baselines", [])):
        if not isinstance(baseline, dict):
            continue
        if skip_baselines:
            record = None
        else:
            record = collector.collect(baseline.get("image_path"), base, baseline.get("sha256"),
                                       case_id=case_id, role="baseline", version=baseline.get("version"))
        baselines.append({"kind": "baseline", "title": f"{baseline.get('version', '')} · {baseline.get('label', '历史参考')}",
                          "anonymous": f"历史参考 {index + 1}", "status": "历史参考",
                          "asset": record["asset_path"] if record else None,
                          "message": ("本次跳过历史图片读取" if skip_baselines else
                                      (record["error"] or "非用户已选最优；不计入本轮接受数量")),
                          "choice": f"baseline:{index}:{record['sha256']}" if record and record["asset_path"] else None})
    count = sum(c["accepted"] for c in cards)
    status = "complete" if count == target and result.get("status") == "complete" and not issues else "partial"
    if result.get("status") == "blocked":
        status = "blocked"
    notes.extend(issues)
    notes.extend(text_list(result.get("limitations")))
    calls = {"total": len(attempts), "retry": sum(a.get("kind") == "retry" for a in attempts),
             "failed": sum(a.get("status") == "failed" for a in attempts)}
    view = {"id": case_id, "label": str(entry.get("label", case_id)), "target_count": target,
            "schema_version": result.get("schema_version"),
            "category": str(entry.get("category", result.get("plugin_id", "generic"))),
            "status": status, "accepted_count": count, "diagnosis": result.get("diagnosis") or {},
            "request": entry.get("request", ""), "notes": notes, "calls": calls, "cards": cards,
            "baselines": baselines, "original": {"kind": "original", "title": "原图", "anonymous": "原图",
                "status": "参照", "asset": original["asset_path"], "message": original["error"] or "同一原图，完整比例",
                "choice": f"original:{original['sha256']}" if original["asset_path"] else None}}
    audit = {"id": case_id, "status": status, "target_count": target, "accepted_count": count,
             "result_schema_version": result.get("schema_version"),
             "result_path": result_path.relative_to(root).as_posix() if result_path else None,
             "result_sha256": result_hash, "export_status": result.get("status"), "issues": issues,
             "calls": calls, "solutions": records}
    if result.get("schema_version") == "0.10.0":
        view["round_number"] = audit["round_number"] = result.get("round_number")
        view["source_case_id"] = result.get("case_id")
        view["result_sha256"] = result_hash
        view["session_ended"] = result.get("session_ended", False)
        for card in view["cards"]:
            if card.get("choice"):
                card["choice"] = f"round:{result.get('round_number')}:" + card["choice"]
    if context is not None:
        view["goal_context"] = context
        audit["goal_context"] = context
    summary = public_summary(entry.get("summary"), cards, result_hash)
    if summary:
        view["summary"] = audit["summary"] = summary
        recommendation = summary.get("recommendation")
        for card in cards:
            if recommendation and (card.get("solution") or {}).get("id") == recommendation["solution_id"]:
                card["recommendation"] = recommendation
    return view, audit


def build_report(manifest_path, project_root, site, title="中国传统色 · 方案对比", skip_baselines=False):
    root = Path(project_root).resolve()
    manifest_path = local_path(str(manifest_path), Path.cwd(), root)
    site = local_path(str(site), Path.cwd(), root)
    if site == root:
        raise ReportError("site 必须为工程内独立的输出目录")
    manifest = read_json(manifest_path)
    entries = manifest_cases(manifest)
    report_id = digest(str(manifest_path).encode("utf-8"))[:24]
    # Refuse to overwrite input trees, historical runs or another report.
    for entry in entries:
        output = local_path(entry["output_dir"], manifest_path.parent, root)
        if site == output or output.is_relative_to(site) or site.is_relative_to(output):
            raise ReportError("site 不能与 case run 重叠")
        source = local_path(entry["input_copy"], manifest_path.parent, root)
        if source.is_relative_to(site):
            raise ReportError("site 不能包含原图输入")
    if manifest_path.is_relative_to(site):
        raise ReportError("site 不能包含 manifest 输入")
    if site.exists() and any(site.iterdir()):
        previous = read_json(site / "audit.json")
        if previous.get("report_kind") != "scene_comparison" or previous.get("report_id") != report_id:
            raise ReportError("site 非空且不属于本清单的报告，请指定新目录")
    if (site / "assets").is_symlink():
        raise ReportError("site/assets 不能为符号链接")
    # Establish ownership before copying large files so an interrupted first
    # build can be resumed. A building marker never claims delivery complete.
    if not (site / "audit.json").exists():
        marker = {"schema_version": "0.7.0", "report_kind": "scene_comparison",
                  "report_id": report_id, "status": "building"}
        atomic_write(site / "audit.json", (json.dumps(marker, ensure_ascii=False) + "\n").encode("utf-8"))
    collector = ImageCollector(root, site)
    views, case_audits = [], []
    for entry in entries:
        view, case_audit = load_case(entry, manifest_path.parent, root, collector, skip_baselines)
        views.append(view)
        case_audits.append(case_audit)
    missing, rejected = [], []
    for case in case_audits:
        for solution in case["solutions"]:
            item = {"case_id": case["id"], **solution}
            if solution["state"] != "accepted":
                missing.append(item)
            if solution["state"] == "rejected":
                rejected.append(item)
    audit = {"schema_version": "0.7.0", "report_kind": "scene_comparison", "report_id": report_id,
             "manifest_sha256": digest(manifest_path.read_bytes()),
             "cases_count": len(entries), "expected_images": sum(v["target_count"] for v in views),
             "expected_originals": len(entries), "accepted_count": sum(v["accepted_count"] for v in views),
             "missing_count": len(missing), "missing": missing, "rejected_count": len(rejected), "rejected": rejected,
             "unaccepted_count": sum(s["state"] == "unaccepted" for c in case_audits for s in c["solutions"]),
             "status": "complete" if all(c["status"] == "complete" for c in case_audits) else "partial",
             "baselines_skipped": skip_baselines, "cases": case_audits, "files": collector.files,
             "limits": ["接受判定来自 scene export；本页只核对导出与现存文件，不替代当前计划/评审重新导出",
                        "rejected_count 只统计明确 reject；未导出接受不自动推断审美拒绝",
                        "哈希、尺寸与解码证明文件一致性，不证明美观；未校准到用户 70 分"]}
    if any(v.get("goal_context") is not None for v in views):
        audit["schema_version"] = "0.8.0"
        audit["limits"].append("目标达成来自看图评审，独立于作者计划、S/O；确认来源不作独立身份验证，测试确认不是真实用户意见")
    if any(v.get("schema_version") == "0.9.0" for v in views):
        audit["schema_version"] = "0.9.0"
    if any(v.get("schema_version") == "0.10.0" for v in views):
        audit["schema_version"] = "0.10.0"
    summary = public_summary(manifest.get("summary"))
    if summary:
        audit["summary"] = summary
    for filename in ("report.css", "report.js"):
        atomic_write(site / "assets" / filename, (ASSETS / filename).read_bytes())
    document = render_document(views, audit, title).encode("utf-8")
    atomic_write(site / "index.html", document)
    audit["report_files"] = [{"path": name, "sha256": digest((site / name).read_bytes()),
                              "exists": (site / name).is_file()}
                             for name in ("index.html", "assets/report.css", "assets/report.js")]
    atomic_write(site / "audit.json", (json.dumps(audit, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8"))
    return audit


def main(argv=None):
    parser = argparse.ArgumentParser(description="生成 v0.10 多轮单页中文对比报告（兼容 v0.7/v0.8/v0.9）；仅复制现有图片，不生成/重着色。",
        epilog="必需输入：batch manifest 的 cases[id,label,input_copy,output_dir,target_count,category]；"
               "每个 output_dir/result.json 可暂缺（显示 partial）。路径可绝对或相对清单所在目录。"
               "baseline 数组项为 version,label,image_path,sha256,user_confirmed；未知扩展字段不妨碍读取。"
               "修改计划/评审后先重新 scene_cli export。仅依赖 Python 标准库和现有 Pillow；无需服务或联网。")
    parser.add_argument("--manifest", required=True, type=Path, help="batch manifest.json")
    parser.add_argument("--project-root", required=True, type=Path, help="所有读取和输出的边界")
    parser.add_argument("--site", type=Path, help="工程内新建 site 目录；同清单可安全重建")
    parser.add_argument("--title", default="中国传统色 · 方案对比", help="页面中文标题")
    parser.add_argument("--skip-baselines", action="store_true", help="不读取历史图片，仅保留标注空位（开发检查用）")
    parser.add_argument("--check-manifest", action="store_true", help="只检查清单数量，不读图片/result，不写文件")
    args = parser.parse_args(argv)
    try:
        if args.check_manifest:
            path = local_path(str(args.manifest), Path.cwd(), args.project_root.resolve())
            result = manifest_summary(read_json(path))
        else:
            if args.site is None:
                parser.error("构建报告需要 --site；只读检查可用 --check-manifest")
            audit = build_report(args.manifest, args.project_root, args.site, args.title, args.skip_baselines)
            result = {key: audit[key] for key in ("cases_count", "expected_images", "accepted_count", "missing_count", "status")}
            result["index"] = str(args.site.resolve() / "index.html")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ReportError, OSError) as exc:
        parser.exit(2, f"报告失败：{exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
