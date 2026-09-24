"""Render one Trip+ multi-turn case into a readable Markdown report.

Usage:
    python render_run_report.py <result_dir> <case_id> [query_file]
Example:
    python render_run_report.py ../trip-plus/result/smoke/deepseek-v4-pro_en mt_single_0006
Writes <result_dir>/report_<case_id>.md
"""

import json
import sys
from pathlib import Path

DEFAULT_QUERY = Path(__file__).resolve().parent.parent / "2_评测集_cases" / "query_10.json"


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def mark(passed) -> str:
    return {True: "✅", False: "❌", None: "—"}.get(passed, "—")


def short(text, limit=160) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit] + " …"


def render_tool_process(trajectory: dict) -> list[str]:
    lines = []
    prior = len(trajectory.get("prior_chat_messages") or [])
    messages = trajectory.get("messages") or []
    step = 0
    for message in messages[prior + 2 :]:
        for call in message.get("tool_calls") or []:
            step += 1
            fn = call.get("function", {})
            lines.append(f"{step}. `{fn.get('name')}` {short(fn.get('arguments', ''), 120)}")
    stats = trajectory.get("runtime_stats") or {}
    usage = stats.get("token_usage") or {}
    lines.append("")
    lines.append(
        f"LLM 调用 {stats.get('llm_calls')} 次，工具调用 {stats.get('tool_calls')} 次"
        f"（重复 {stats.get('duplicate_tool_calls')}），token 共 {usage.get('total_tokens')}"
        f"（缓存命中 {usage.get('prompt_cache_hit_tokens')}），用时 {trajectory.get('elapsed_time', 0):.1f}s"
    )
    return lines


def render_scores(score: dict) -> list[str]:
    lines = []
    alignment = score.get("turn_alignment") or {}

    lines.append("**多轮检查（回应方式 / 本轮更新 / 前序保留）**")
    lines.append("")
    lines.append("| 检查项 | 结果 | 说明 |")
    lines.append("|---|---|---|")
    for kind, key in (("更新", "fulfillment_checks"), ("保留", "preserve_checks")):
        for check in alignment.get(key) or []:
            lines.append(f"| {kind}：{check['name']} | {mark(check.get('passed'))} | {short(check.get('message') or '', 200)} |")
    lines.append("")

    requirement = score.get("requirement_details") or {}
    constraints = requirement.get("constraints") or {}
    if constraints:
        lines.append(
            f"**硬约束**（{requirement.get('hard_constraints_passed')}/{requirement.get('hard_constraints_total')} 通过）"
        )
        lines.append("")
        lines.append("| 约束 | 结果 | 说明 |")
        lines.append("|---|---|---|")
        for name, detail in constraints.items():
            lines.append(f"| {name} | {mark(detail.get('passed'))} | {short(detail.get('message') or '', 200)} |")
        lines.append("")

    soft = (requirement.get("soft_preferences") or {}).get("checks") or []
    if soft:
        lines.append(f"**软偏好**（得分 {requirement.get('soft_preference_score')}）")
        lines.append("")
        lines.append("| 规则 | 得分 | 严重程度 | 说明 |")
        lines.append("|---|---|---|---|")
        for check in soft:
            lines.append(
                f"| {check.get('rule_id')} | {check.get('score')} | {check.get('severity')} | {short(check.get('message') or '', 120)} |"
            )
        lines.append("")

    feasibility = score.get("feasibility_details") or {}
    failed = {k: v for k, v in (feasibility.get("checks") or {}).items() if v.get("passed") is False}
    if feasibility:
        lines.append(
            f"**可行性**（{feasibility.get('passed_checks')}/{feasibility.get('total_checks')} 项通过，"
            f"得分 {round(feasibility.get('score') or 0, 3)}）未通过的项："
        )
        lines.append("")
        for name, detail in failed.items():
            lines.append(f"- `{name}`：{short(detail.get('message') or '', 300)}")
        if not failed:
            lines.append("- 无")
        lines.append("")

    scores = score.get("scores") or {}
    lines.append(
        "**本轮分数**："
        + "，".join(f"{k}={round(v, 3) if isinstance(v, float) else v}" for k, v in scores.items())
    )
    return lines


def main() -> None:
    result_dir = Path(sys.argv[1])
    case_id = sys.argv[2]
    query_file = Path(sys.argv[3]) if len(sys.argv) > 3 else DEFAULT_QUERY
    case = next(r for r in load(query_file) if r["id"] == case_id)
    profile = case["meta_info"]["base_query_meta"]["observable_profile"]

    out = [f"# 运行报告：{case_id}", ""]
    out.append(f"- 结果目录：`{result_dir}`")
    out.append(f"- 交互类型：{case['interaction_type']}")
    out.append(f"- 用户画像：`{json.dumps(profile, ensure_ascii=False)}`")
    out.append("")

    for turn in case["turns"]:
        tid = turn["turn_id"]
        name = f"{case_id}_turn_{tid}"
        trajectory = load(result_dir / "trajectories" / f"{name}.json") or {}
        report = (result_dir / "reports" / f"{name}.txt")
        score = load(result_dir / "evaluation" / f"{name}_score.json") or {}

        out.append(f"## 第 {tid} 轮（期望：{turn['response_expectation']}）")
        out.append("")
        out.append(f"**用户**：{turn['utterance']}")
        out.append("")
        out.append(f"**标注**：必须落实 {turn['must_update']}；必须保留 {turn['must_preserve']}")
        if turn.get("blocking_constraints"):
            out.append(f"；冲突原因 {turn['blocking_constraints']}")
        out.append("")
        out.append("### 运行过程（工具调用）")
        out.append("")
        out.extend(render_tool_process(trajectory) if trajectory else ["（无轨迹文件）"])
        out.append("")
        out.append("### 模型最终回复")
        out.append("")
        out.append("```text")
        out.append(report.read_text(encoding="utf-8").strip() if report.exists() else "（无回复文件）")
        out.append("```")
        out.append("")
        out.append("### 规则评分")
        out.append("")
        out.extend(render_scores(score) if score else ["（无评分文件）"])
        out.append("")

    target = result_dir / f"report_{case_id}.md"
    target.write_text("\n".join(out), encoding="utf-8")
    print(f"wrote {target}")


if __name__ == "__main__":
    main()
