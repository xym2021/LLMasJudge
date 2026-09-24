"""Run Judge B (interaction behaviour) on Trip+ multi-turn results.

Usage:
    python run_judge_b.py <result_dir> <case_id> [<case_id> ...] [--model qwen3.8-flash] [--query <query_file>]
Example:
    python run_judge_b.py ../trip-plus/result/smoke/deepseek-v4-pro_en mt_single_0006
Writes <result_dir>/judge_b/<case_id>_turn_<i>.json (judge output + the exact prompt sent)
and <result_dir>/judge_b/summary.md.
"""

import argparse
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from openai import OpenAI

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DEFAULT_QUERY = ROOT / "2_评测集_cases" / "query_10.json"
PROMPT_DOC = HERE / "Judge_Prompt.md"
ENV_FILE = ROOT / "trip-plus" / ".env"
DIMENSIONS = [
    "B1_clarification_or_no_solution_quality",
    "B2_change_transparency",
    "B3_proactive_risk_and_profile",
    "B4_factual_boundary",
]
JUDGE_RETRIES = 5
TOOL_RESULT_LIMIT = 2500
TOOL_EVIDENCE_LIMIT = 60000


def load_env(path: Path) -> None:
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def load_prompt_template() -> str:
    text = PROMPT_DOC.read_text(encoding="utf-8")
    match = re.search(r"````text\n(.*?)\n````", text, re.S)
    if not match:
        raise RuntimeError(f"Judge B prompt block not found in {PROMPT_DOC}")
    return match.group(1)


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)


def dialogue_so_far(trajectory: dict) -> str:
    prior = trajectory.get("prior_chat_messages") or []
    if not prior:
        return "none"
    lines = []
    for message in prior:
        role = "用户" if message["role"] == "user" else "助手"
        lines.append(f"[{role}]\n{message.get('content') or ''}")
    return "\n\n".join(lines)


def tool_evidence(trajectory: dict) -> str:
    prior = len(trajectory.get("prior_chat_messages") or [])
    messages = trajectory.get("messages") or []
    calls = {}
    chunks = []
    for message in messages[prior + 2 :]:
        for call in message.get("tool_calls") or []:
            fn = call.get("function", {})
            calls[call.get("id")] = f"{fn.get('name')}({fn.get('arguments', '')})"
        if message.get("role") == "tool":
            content = str(message.get("content") or "")
            if len(content) > TOOL_RESULT_LIMIT:
                content = content[:TOOL_RESULT_LIMIT] + " …[truncated]"
            chunks.append(f"### {calls.get(message.get('tool_call_id'), message.get('name'))}\n{content}")
    if not chunks:
        return "not_provided"
    text = "\n\n".join(chunks)
    return text if len(text) <= TOOL_EVIDENCE_LIMIT else text[:TOOL_EVIDENCE_LIMIT] + "\n…[truncated]"


def rule_results(score: dict) -> str:
    alignment = score.get("turn_alignment") or {}
    requirement = score.get("requirement_details") or {}
    feasibility = score.get("feasibility_details") or {}

    def checks(items):
        return [{"name": c.get("name"), "passed": c.get("passed"), "message": c.get("message")} for c in items or []]

    result = {
        "response_mode": score.get("response_expectation_details"),
        "must_update_checks": checks(alignment.get("fulfillment_checks")),
        "must_preserve_checks": checks(alignment.get("preserve_checks")),
        "hard_constraints": {
            name: {"passed": d.get("passed"), "message": d.get("message")}
            for name, d in (requirement.get("constraints") or {}).items()
        },
        "feasibility_failed_checks": {
            name: d.get("message")
            for name, d in (feasibility.get("checks") or {}).items()
            if d.get("passed") is False
        },
        "scores": score.get("scores"),
    }
    return dumps(result)


def turn_oracle(turn: dict) -> str:
    return dumps({k: v for k, v in turn.items() if k != "utterance"})


def build_prompt(template: str, case: dict, turn: dict, trajectory: dict, reply: str, score: dict) -> str:
    meta = case["meta_info"]["base_query_meta"]
    advisories = (meta.get("city_context") or {}).get("city_tags") or "none"
    fields = {
        "USER_PROFILE": dumps(meta.get("observable_profile")),
        "CITY_ADVISORIES": dumps(advisories),
        "DIALOGUE_SO_FAR": dialogue_so_far(trajectory),
        "CURRENT_USER_TURN": turn["utterance"],
        "ASSISTANT_REPLY": reply,
        "TURN_ORACLE": turn_oracle(turn),
        "RULE_RESULTS": rule_results(score) if score else "not_provided",
        "TOOL_EVIDENCE": tool_evidence(trajectory),
    }
    prompt = template
    for tag, value in fields.items():
        prompt = re.sub(
            rf"<{tag}>\n\{{\{{.*?\}}\}}\n</{tag}>",
            lambda _m, t=tag, v=value: f"<{t}>\n{v}\n</{t}>",
            prompt,
            flags=re.S,
        )
    leftover = re.findall(r"\{\{.*?\}\}", prompt)
    if leftover:
        raise RuntimeError(f"Unfilled placeholders: {leftover}")
    return prompt


def parse_judge_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    return json.loads(text[start : end + 1])


def call_judge(client: OpenAI, model: str, prompt: str, thinking_budget: int) -> tuple[dict, str, str, dict]:
    last_error = None
    for attempt in range(JUDGE_RETRIES):
        try:
            stream = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                stream=True,
                stream_options={"include_usage": True},
                extra_body={"enable_thinking": True, "thinking_budget": thinking_budget},
            )
            reasoning, content, usage = [], [], {}
            for chunk in stream:
                if chunk.usage:
                    usage = chunk.usage.model_dump()
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if getattr(delta, "reasoning_content", None):
                    reasoning.append(delta.reasoning_content)
                if delta.content:
                    content.append(delta.content)
            raw = "".join(content)
            judge = parse_judge_json(raw)
            missing = [d for d in DIMENSIONS if not (judge.get("dimensions", {}).get(d) or {}).get("label")]
            if missing:
                raise ValueError(f"judge output missing dimensions {missing}")
            return judge, raw, "".join(reasoning), usage
        except Exception as error:  # noqa: BLE001
            last_error = error
            print(f"  retry {attempt + 1}: {error}", flush=True)
            time.sleep(min(60, 3 * 2**attempt))
    raise RuntimeError(f"Judge call failed after retries: {last_error}")


def summary_rows(result_dir: Path, outputs: list[dict]) -> str:
    lines = [f"# Judge B 结果汇总", "", f"- 结果目录：`{result_dir}`", ""]
    lines.append("| case / 轮 | 期望 | 规则判定模式 | B1 澄清/无解 | B2 改动透明 | B3 风险提示 | B4 事实边界 | flags |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for item in outputs:
        dims = item["judge"].get("dimensions", {})
        mode = item.get("rule_mode") or {}
        cells = [dims.get(d, {}).get("label", "?") for d in DIMENSIONS]
        lines.append(
            f"| {item['case_id']} / {item['turn_id']} | {item['expected']} | "
            f"{mode.get('actual_mode', '?')}（{'通过' if mode.get('passed') else '未通过'}） | "
            + " | ".join(cells)
            + f" | {', '.join(item['judge'].get('flags') or []) or '—'} |"
        )
    lines.append("")
    for item in outputs:
        lines.append(f"## {item['case_id']} 第 {item['turn_id']} 轮")
        lines.append("")
        lines.append(f"**概括**：{item['judge'].get('turn_summary', '')}")
        lines.append("")
        for d in DIMENSIONS:
            dim = item["judge"].get("dimensions", {}).get(d, {})
            lines.append(f"- **{d}**：{dim.get('label')}（置信度 {dim.get('confidence')}）{dim.get('reason', '')}")
            for quote in dim.get("evidence") or []:
                lines.append(f"  - 证据：「{quote}」")
        if item["judge"].get("missing_evidence"):
            lines.append(f"- 缺少证据：{item['judge']['missing_evidence']}")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("result_dir")
    parser.add_argument("case_ids", nargs="+")
    parser.add_argument("--model", default="deepseek-v4-flash")
    parser.add_argument("--query", default=str(DEFAULT_QUERY))
    parser.add_argument("--thinking-budget", type=int, default=8192)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()

    load_env(ENV_FILE)
    client = OpenAI(
        api_key=os.environ["QWEN_LLM_API_KEY"], base_url=os.environ["QWEN_LLM_BASE_URL"], timeout=600
    )
    template = load_prompt_template()
    result_dir = Path(args.result_dir)
    out_dir = result_dir / "judge_b"
    out_dir.mkdir(exist_ok=True)
    cases = {c["id"]: c for c in load_json(Path(args.query))}

    def judge_turn(case: dict, turn: dict):
        name = f"{case['id']}_turn_{turn['turn_id']}"
        trajectory = load_json(result_dir / "trajectories" / f"{name}.json")
        if not trajectory:
            print(f"skip {name}: no trajectory", flush=True)
            return None
        report = result_dir / "reports" / f"{name}.txt"
        reply = report.read_text(encoding="utf-8").strip() if report.exists() else str(trajectory.get("final_plan") or "")
        score = load_json(result_dir / "evaluation" / f"{name}_score.json") or {}

        prompt = build_prompt(template, case, turn, trajectory, reply, score)
        started = time.time()
        try:
            judge, raw, reasoning, usage = call_judge(client, args.model, prompt, args.thinking_budget)
        except Exception as error:  # noqa: BLE001
            print(f"FAILED {name}: {error}", flush=True)
            return None
        expectation = score.get("response_expectation_details") or {}
        record = {
            "case_id": case["id"],
            "turn_id": turn["turn_id"],
            "expected": turn["response_expectation"],
            "rule_mode": {
                "actual_mode": (expectation.get("details") or {}).get("actual_mode"),
                "passed": expectation.get("passed"),
            },
            "judge_model": args.model,
            "judge": judge,
            "raw_output": raw,
            "reasoning": reasoning,
            "usage": usage,
            "elapsed_s": round(time.time() - started, 1),
            "prompt": prompt,
        }
        (out_dir / f"{name}.json").write_text(dumps(record), encoding="utf-8")
        labels = {d.split("_")[0]: judge.get("dimensions", {}).get(d, {}).get("label") for d in DIMENSIONS}
        print(f"{name}: {labels} flags={judge.get('flags')} ({record['elapsed_s']}s)", flush=True)
        return record

    jobs = [(cases[cid], turn) for cid in args.case_ids for turn in cases[cid]["turns"]]
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        list(pool.map(lambda job: judge_turn(*job), jobs))

    outputs = sorted((load_json(p) for p in out_dir.glob("*_turn_*.json")), key=lambda r: (r["case_id"], r["turn_id"]))
    (out_dir / "summary.md").write_text(summary_rows(result_dir, outputs), encoding="utf-8")
    print(f"wrote {out_dir / 'summary.md'}")


if __name__ == "__main__":
    main()
