"""Build run_viewer.html: leaderboard, run log and turn-by-turn replay of model runs.

Usage:
    python build_run_viewer.py --pipeline-root ../../trip-plus/result/pipeline_0925
    python build_run_viewer.py --run LABEL=RESULT_DIR [--run ...]
Also writes <pipeline-root>/leaderboard.json and ../运行结果报告.md when --pipeline-root is given.
"""

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "2_评测集_cases"))
from build_viewer import ANNOTATIONS, LABELS  # noqa: E402

TOOL_RESULT_LIMIT = 1800

FEAS_DIMS = {
    "structure_completeness": "结构完整性",
    "evidence_validity": "证据有效性",
    "execution_operability": "执行可操作性",
}
CHECK_ZH = {
    "valid_trip_duration": "行程天数正确",
    "closed_loop_route_structure": "往返路线闭环",
    "seamless_intercity_transfers": "城际衔接连贯",
    "day_boundary_continuity": "跨天位置连续",
    "traceable_accommodation": "每晚住处可追溯",
    "ends_with_accommodation": "每天以回住处结束",
    "essential_meal_coverage": "必要正餐齐全",
    "essential_attraction_coverage": "每天安排了景点",
    "validated_accommodation": "酒店真实存在且价格一致",
    "validated_attractions": "景点真实存在且票价一致",
    "validated_meals": "餐厅真实存在且人均一致",
    "validated_transportation": "城际交通与数据库一致",
    "local_move_sanity": "市内移动合理",
    "no_time_overlaps": "时间不重叠",
    "reasonable_transfer_time": "换乘时间合理",
    "attraction_visit_within_opening_hours": "景点在开放时间内",
    "dining_within_service_hours": "用餐在营业时间内",
    "avoidance_of_closure_days": "避开闭馆日",
    "reasonable_duration_at_attractions": "景点停留时长合理",
    "reasonable_meal_duration": "用餐时长合理",
    "intercity_buffer_adequacy": "城际交通留足缓冲",
    "cost_calculation_correctness": "费用计算正确",
    "response_expectation": "回应方式",
}
SUBDIM_ZH = {
    "valid_trip_duration": "行程天数", "route_and_stay_continuity": "路线与住宿连续", "daily_content_coverage": "每日内容覆盖",
    "poi_grounding_valid": "地点真实性", "transport_grounding_valid": "交通真实性",
    "time_and_transfer_feasible": "时间与换乘", "venue_and_duration_feasible": "营业与停留时长",
    "intercity_buffer_feasible": "城际缓冲", "budget_arithmetic_valid": "预算算术",
}
FAMILY_ZH = {"comfort_and_pace": "舒适与节奏", "transport_convenience": "交通便利", "budget_and_value": "预算与性价比",
             "budget": "预算与性价比", "interest_match": "兴趣匹配"}
VIOLATION_ZH = {
    "max_daily_local_transfer_minutes": "单日市内移动分钟数", "route_long_local_transfer_count": "长距离市内移动次数",
    "route_very_long_local_transfer_count": "超长市内移动次数", "mobility_long_local_transfer_count": "行动受限者长距离移动次数",
    "mobility_very_long_local_transfer_count": "行动受限者超长移动次数", "max_daily_outdoor_minutes": "单日户外分钟数",
    "max_daily_attraction_count": "单日景点数", "max_day_span_hours": "单日行程跨度（小时）",
    "early_start_hours_before_08": "早于 8 点出发（小时）", "late_end_hours_after_21": "晚于 21 点结束（小时）",
}
SEVERITY_ZH = {"minor": "轻微", "major": "严重", "none": "无"}
MESSAGE_ZH = [
    ("Expected clarification response for response_expectation=clarification, got plan.", "本轮应澄清，模型却直接出了方案。"),
    ("Expected a clarification/conflict response without daily_plans", "应先澄清或说明冲突，不应给出逐日行程"),
    ("Skipped for non-itinerary turn; preservation is checked after the request is clarified/resolved", "本轮不出方案，保留项在需求澄清后再检查"),
    ("mobility burden is compatible with profile", "行动负担符合画像"),
    ("local movement or outdoor load has a profile violation", "市内移动或户外时长超出画像承受范围"),
    ("schedule pacing is compatible with profile", "日程节奏符合画像"),
    ("schedule has a profile pacing violation", "日程节奏与画像冲突"),
    ("weather exposure is handled for the active profile", "天气暴露已按画像处理"),
    ("weather exposure has a profile violation", "天气暴露与画像冲突"),
    ("transport timing/mode is compatible with profile", "交通时间与方式符合画像"),
    ("transport choice has a profile violation", "交通选择与画像冲突"),
    ("hotel cost has a value-profile violation", "酒店花费与性价比偏好冲突"),
    ("cost choices have a budget-profile violation", "花费选择与预算画像冲突"),
    ("plan includes profile-matching interest content", "方案包含符合兴趣的内容"),
    ("No deterministic soft check is active for this update", "本项更新没有可确定性检查的规则"),
    ("Soft preference score", "软偏好得分"),
    ("Budget component mismatch under explicit party/room metadata", "按人数/房间数核算的预算分项不一致"),
    ("Budget subtotal sum mismatch", "预算分项之和与总额不符"),
    ("Budget accuracy failed", "预算计算错误"),
    ("Plan total=", "方案总额="), ("Calculated total=", "核算总额="),
    ("Location tracking inconsistent", "位置跟踪不一致"),
    ("Day boundary continuity mismatch", "跨天位置不连续"),
    ("Accommodation not traceable on days", "以下日期住处不可追溯"),
    ("Last activity not hotel on days", "以下日期最后一项不是回住处"),
    ("Meal necessity", "正餐必要性"), ("days correct", "天正确"), ("Violations", "问题"),
    ("Non-intercity day must arrange lunch and dinner", "非城际日必须安排午餐和晚餐"),
    ("Attraction arrangements unreasonable", "景点安排不合理"),
    ("Hotels not in database", "酒店不在数据库中"), ("Hotel price mismatch", "酒店价格不一致"),
    ("Attractions not in database", "景点不在数据库中"), ("Attraction price mismatch", "景点票价不一致"),
    ("Restaurants not in database", "餐厅不在数据库中"), ("Restaurant price per person mismatch", "餐厅人均不一致"),
    ("Missing fields", "缺少字段"), ("Not found in database", "数据库中找不到"), ("Price mismatch", "价格不一致"),
    ("Segment mismatch", "车次/航段与数据库不一致"), ("does not match any DB segment", "与数据库任何班次都不匹配"),
    ("candidates", "候选"),
    ("Time overlaps exist", "存在时间重叠"), ("Local travel records implausible", "市内交通记录不合理"),
    ("Anchor transfer time unreasonable", "换乘时间不合理"), ("Intercity buffer inadequate", "城际交通缓冲不足"),
    ("Attraction opening hours mismatch", "景点不在开放时间内"), ("Meal time not within business hours", "用餐不在营业时间内"),
    ("Attractions visited on closing dates", "在闭馆日游览景点"), ("Attraction visit duration unreasonable", "景点停留时长不合理"),
    ("Meal duration unreasonable", "用餐时长不合理"), ("not in recommended", "不在推荐范围"), ("plan has", "方案安排"),
    ("Date range inconsistent", "日期范围不一致"), ("Actual budget exceeds limit", "实际花费超出预算上限"),
    ("Required hotel not found in acceptable set", "所选酒店不在可接受答案中"),
    ("Required restaurant not found in acceptable set", "所选餐厅不在可接受答案中"),
    ("Plan includes banned high-queue attractions", "方案包含应避开的高排队景点"),
    ("Missing required attractions", "缺少必去景点"), ("Missing top rated attractions", "缺少评分最高的景点"),
    ("Missing free attractions", "缺少免费景点"), ("Missing attractions", "缺少景点"),
    ("Unavailable POI still appears in plan", "已不可用的地点仍出现在方案中"),
    ("Plan exceeds max attractions per day", "方案超过每日景点上限"),
    ("Evaluation error", "评估出错"), ("Unknown constraint type", "未知约束类型"),
    ("subtotals sum to", "分项合计"), ("but total_estimated_budget is", "但总预算写的是"),
    ("intercity train before-buffer", "城际火车发车前缓冲"), ("intercity flight before-buffer", "航班起飞前缓冲"),
    ("after-buffer", "到达后缓冲"), ("< required", "< 要求"), ("expected=", "应为="), ("plan=", "方案="),
    ("current location should be", "当前位置应为"), ("but next day starts from", "但次日从以下位置出发"),
    ("previous night stays in", "前一晚住在"),
]
B_DIMS = {
    "B1_clarification_or_no_solution_quality": ("B1 澄清与判无解的质量", "key"),
    "B2_change_transparency": ("B2 改动透明度", "important"),
    "B3_proactive_risk_and_profile": ("B3 风险与画像提示", "important"),
    "B4_factual_boundary": ("B4 事实边界", "important"),
}
A_DIMS = {"physical_comfort": "身体舒适", "environmental_comfort": "环境舒适", "schedule_comfort": "日程舒适",
          "budget_comfort": "预算舒适", "preference_satisfaction": "偏好满足"}
METRICS = [
    ("resp_acc", "回应准确率", "interaction"), ("fulfill", "请求落实", "interaction"), ("preserve", "意图保留", "interaction"),
    ("feas", "可行性", "plan"), ("hard", "硬约束", "plan"), ("soft", "软偏好", "plan"), ("user_sim", "用户模拟", "plan"),
]


def zh_msg(text):
    if not text:
        return text
    out = str(text)
    for en, zh in MESSAGE_ZH:
        out = out.replace(en, zh)
    return out


def load(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    except json.JSONDecodeError:
        return None


def clip(text, limit=TOOL_RESULT_LIMIT):
    text = text or ""
    return text if len(text) <= limit else text[:limit] + f"\n…（截断，原文 {len(text)} 字符）"


def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def trajectory_steps(traj):
    msgs = traj.get("messages", [])
    users = [i for i, m in enumerate(msgs) if m.get("role") == "user"]
    last_user = users[-1] if users else 0
    results = {m.get("tool_call_id"): m for m in msgs[last_user:] if m.get("role") == "tool"}
    steps, reply = [], ""
    for m in msgs[last_user + 1:]:
        if m.get("role") != "assistant":
            continue
        calls = m.get("tool_calls") or []
        if not calls:
            reply = m.get("content") or ""
            continue
        steps.append({
            "thinking": m.get("reasoning_content") or m.get("content") or "",
            "calls": [{"name": c["function"]["name"], "args": c["function"].get("arguments", ""),
                       "result": clip(results.get(c["id"], {}).get("content", "（无返回）"))} for c in calls],
        })
    return steps, reply or traj.get("final_plan", "")


def named(items):
    return [{"name": c["name"], "zh": CHECK_ZH.get(c["name"]), "passed": c.get("passed"), "message": zh_msg(c.get("message"))} for c in items]


def rule_block(score, expected):
    ta = score.get("turn_alignment", {})
    checks = ta.get("fulfillment_checks", [])
    mode = next((c for c in checks if c["name"] == "response_expectation"), {})
    details = mode.get("details", {})
    out = {
        "mode": {"expected": expected, "actual": details.get("actual_mode"), "passed": mode.get("passed"), "message": zh_msg(mode.get("message"))},
        "update": named([c for c in checks if c["name"] != "response_expectation"]),
        "preserve": named(ta.get("preserve_checks", [])),
        "scores": score.get("scores", {}),
        "fulfillment_score": ta.get("fulfillment_score"),
        "preserve_score": ta.get("preserve_score"),
    }
    feas = score.get("feasibility_details")
    if feas and score.get("evaluation_mode") != "response_expectation_mismatch" and feas.get("dimensions"):
        dims = []
        for key, zh in FEAS_DIMS.items():
            d = feas["dimensions"].get(key, {})
            subs = [{"name": sk, "zh": SUBDIM_ZH.get(sk, sk),
                     "checks": named([{"name": c["name"], "passed": c["passed"], "message": feas["checks"].get(c["name"], {}).get("message")} for c in sv.get("checks", [])])}
                    for sk, sv in d.get("subdimensions", {}).items()]
            dims.append({"key": key, "zh": zh, "score": d.get("score"), "subs": subs})
        out["feasibility"] = {"score": feas["score"], "passed": feas["passed_checks"], "total": feas["total_checks"], "dims": dims}
        req = score.get("requirement_details", {})
        out["hard"] = named([{"name": k, "passed": v.get("passed"), "message": v.get("message")} for k, v in req.get("constraints", {}).items()])
        soft = req.get("soft_preferences", {})
        out["soft"] = {
            "score": soft.get("score"),
            "families": [{"family": FAMILY_ZH.get(f["family"], f["family"]), "score": f.get("score")} for f in soft.get("preference_family_scores", [])],
            "checks": [{"rule_id": c["rule_id"], "family": FAMILY_ZH.get(c.get("preference_family"), c.get("preference_family")), "score": c.get("score"),
                        "message": zh_msg(c.get("message")),
                        "violations": [{"name": VIOLATION_ZH.get(v["name"], v["name"]), "value": v.get("value"), "severity": SEVERITY_ZH.get(v.get("severity"), v.get("severity"))}
                                       for v in c.get("violations", []) if v.get("severity") not in (None, "none")]}
                       for c in soft.get("checks", [])],
        }
    return out


def verdict(rule, judge):
    """Per-turn verdict from 评估方案与验证.md §1.2."""
    items = []

    def add(name, level, ok, note=""):
        items.append({"name": name, "level": level, "ok": ok, "note": note})

    add("L1 回应方式", "key", rule["mode"]["passed"])
    if rule["mode"]["passed"] is False:
        return {"label": "不合格", "items": items, "stopped": True}
    if "feasibility" in rule:
        f = rule["feasibility"]
        add("L2 行程可行性", "important", f["passed"] == f["total"], f"{f['passed']}/{f['total']} 项通过")
        hard = [h for h in rule["hard"] if h["passed"] is not None]
        if hard:
            add("L3 硬约束", "key", all(h["passed"] for h in hard), f"{sum(h['passed'] for h in hard)}/{len(hard)}")
        s = rule["soft"]["score"]
        if s is not None:
            add("L3 软偏好", "important", s >= 0.7, f"{s:.2f}，门槛 0.7")
    upd = [u for u in rule["update"] if u["passed"] is not None]
    if upd:
        add("L3 本轮更新落实", "important", all(u["passed"] for u in upd), f"{sum(u['passed'] for u in upd)}/{len(upd)}")
    pre = [p for p in rule["preserve"] if p["passed"] is not None]
    if pre:
        add("L3 前序要求保留", "key", all(p["passed"] for p in pre), f"{sum(p['passed'] for p in pre)}/{len(pre)}")
    review = False
    for key, d in (judge or {}).get("dimensions", {}).items():
        zh, level = B_DIMS.get(key, (key, "important"))
        label = d.get("label")
        if label in ("not_applicable", "insufficient_evidence", None):
            continue
        if level == "key" and label == "partial":
            review = True
            add(zh, level, None, "partial，交人工复核")
            continue
        add(zh, level, label == "pass", label)
    key_fail = any(i["level"] == "key" and i["ok"] is False for i in items)
    imp_fail = sum(i["level"] == "important" and i["ok"] is False for i in items)
    if key_fail or imp_fail >= 2:
        label = "不合格"
    elif review:
        label = "待复核"
    else:
        label = "合格" if imp_fail == 0 else "基本合格"
    return {"label": label, "items": items, "stopped": False}


def judge_a_block(path: Path):
    d = load(path) if path else None
    if not d:
        return None
    dims = {k: {"zh": zh, "score": (d.get("experience_dimensions", {}).get(k) or {}).get("score_1_5"),
                "applicable": (d.get("experience_dimensions", {}).get(k) or {}).get("applicable", True)} for k, zh in A_DIMS.items()}
    acts = []
    for a in d.get("activity_simulations", []):
        act = a.get("activity") or {}
        ups = {k: {"score": (v or {}).get("score_1_5"), "reason": (v or {}).get("reason") or (v or {}).get("not_applicable_reason")}
               for k, v in (a.get("dimension_updates") or {}).items()}
        acts.append({"ref": a.get("item_ref"), "time": act.get("time_slot"), "type": act.get("type"), "name": act.get("name"), "dims": ups})
    rec = d.get("score_recalculation") or {}
    rt = d.get("runtime") or {}
    return {
        "turn": d.get("turn_id"), "model": d.get("simulator_model"),
        "score": rec.get("recomputed_score"), "score_1_5": rec.get("recomputed_score_1_5"),
        "llm_overall_1_5": (d.get("llm_reported_overall") or {}).get("score_1_5"),
        "dims": dims, "activities": acts, "missing": d.get("missing_evidence") or [],
        "notes": [n for chunk in (d.get("audit_notes") or {}).get("chunk_notes", []) for n in chunk],
        "profile": d.get("profile_summary"), "tokens": (rt.get("token_usage") or {}).get("total_tokens"), "llm_calls": rt.get("llm_calls"),
    }


def build_run(label, result_dir, note, cases, extra=None):
    run = {"label": label, "note": note, "available": bool(result_dir and result_dir.exists()), "cases": {}, **(extra or {})}
    if not run["available"]:
        return run
    for case in cases:
        cid = case["id"]
        turns = []
        for t in case["turns"]:
            n = t["turn_id"]
            traj = load(result_dir / f"trajectories/{cid}_turn_{n}.json")
            score = load(result_dir / f"evaluation/{cid}_turn_{n}_score.json")
            if not traj or not score:
                turns.append(None)
                continue
            steps, reply = trajectory_steps(traj)
            jb = load(result_dir / f"judge_b/{cid}_turn_{n}.json") or {}
            judge = jb.get("judge")
            rule = rule_block(score, t["response_expectation"])
            rs = traj.get("runtime_stats", {})
            turns.append({
                "steps": steps, "reply": reply,
                "runtime": {"elapsed": traj.get("elapsed_time"), "llm_calls": rs.get("llm_calls"), "tool_calls": rs.get("tool_calls"),
                            "duplicate": rs.get("duplicate_tool_calls"), "errors": rs.get("tool_errors"),
                            "tokens": (rs.get("token_usage") or {}).get("total_tokens")},
                "plan": load(result_dir / f"converted_plans/id_{cid}_turn_{n}_converted.json"),
                "rule": rule,
                "judge_b": judge and {"summary": judge.get("turn_summary"), "dimensions": judge.get("dimensions"), "flags": judge.get("flags", []),
                                      "missing": judge.get("missing_evidence", []), "reasoning": jb.get("reasoning", ""),
                                      "model": jb.get("judge_model"), "elapsed": jb.get("elapsed_s"), "tokens": (jb.get("usage") or {}).get("total_tokens")},
                "verdict": verdict(rule, judge),
            })
        if not any(turns):
            continue
        hits = sorted((result_dir / "judge_a").glob(f"*/user_simulations/id_{cid}_turn_*_user_simulation.json"))
        run["cases"][cid] = {"turns": turns, "judge_a": judge_a_block(hits[-1] if hits else None)}
    return run


# ---------------- aggregation ----------------
def aggregate_run(run, cases_by_id, only=None):
    per = defaultdict(list)
    counts = {"verdict": Counter(), "mode": defaultdict(lambda: [0, 0]), "b": defaultdict(Counter),
              "feas_fail": Counter(), "hard_fail": Counter(), "soft_family": defaultdict(list), "subdim": defaultdict(list),
              "a_dims": defaultdict(list), "itype": defaultdict(lambda: defaultdict(list))}
    cost = defaultdict(list)
    n_turns = 0
    for cid, rc in run["cases"].items():
        if only is not None and cid not in only:
            continue
        case = cases_by_id[cid]
        itype = case.get("interaction_type", "")
        for i, t in enumerate(rc["turns"]):
            if not t:
                continue
            n_turns += 1
            exp = case["turns"][i]["response_expectation"]
            r = t["rule"]
            ok = 1 if r["mode"]["passed"] else 0
            per["resp_acc"].append(ok)
            counts["mode"][exp][0] += ok
            counts["mode"][exp][1] += 1
            if cid.endswith("P") and i == 1:
                counts["mode"]["no_ask"][0] += ok
                counts["mode"]["no_ask"][1] += 1
            if r["fulfillment_score"] is not None:
                per["fulfill"].append(r["fulfillment_score"])
            if r["preserve_score"] is not None:
                per["preserve"].append(r["preserve_score"])
            counts["itype"][itype]["resp_acc"].append(ok)
            if r["fulfillment_score"] is not None:
                counts["itype"][itype]["fulfill"].append(r["fulfillment_score"])
            if exp == "plan" and r["mode"]["actual"] == "plan" and "feasibility" in r:
                s = r["scores"]
                per["feas"].append(s.get("feasibility_score"))
                per["hard"].append(s.get("hard_constraint_score"))
                if s.get("soft_preference_score") is not None:
                    per["soft"].append(s.get("soft_preference_score"))
                for d in r["feasibility"]["dims"]:
                    for sub in d["subs"]:
                        cs = sub["checks"]
                        if cs:
                            counts["subdim"][sub["zh"]].append(sum(bool(c["passed"]) for c in cs) / len(cs))
                        for c in cs:
                            if not c["passed"]:
                                counts["feas_fail"][c["zh"] or c["name"]] += 1
                for h in r["hard"]:
                    if h["passed"] is False:
                        counts["hard_fail"][LABELS["hard"].get(h["name"], h["name"])] += 1
                for f in r["soft"]["families"]:
                    counts["soft_family"][f["family"]].append(f["score"])
            counts["verdict"][t["verdict"]["label"]] += 1
            for k, d in ((t.get("judge_b") or {}).get("dimensions") or {}).items():
                counts["b"][k][d.get("label")] += 1
            rt = t["runtime"]
            for k in ("llm_calls", "tool_calls", "tokens", "elapsed"):
                if rt.get(k) is not None:
                    cost[k].append(rt[k])
        ja = rc.get("judge_a")
        if ja and ja.get("score") is not None:
            per["user_sim"].append(ja["score"])
            for k, d in ja["dims"].items():
                if d.get("score") is not None:
                    counts["a_dims"][d["zh"]].append((d["score"] - 1) / 4)
    m = {k: mean(per[k]) for k, _, _ in METRICS}
    plan_parts = [m["feas"], m["hard"], m["soft"], m["user_sim"]]
    m["plan_avg"] = mean(plan_parts) if all(x is not None for x in plan_parts) else mean(plan_parts)
    rule_parts = [m[k] for k in ("resp_acc", "fulfill", "preserve", "feas", "hard", "soft")]
    m["rule_total"] = mean(rule_parts)
    m["total7"] = mean([m[k] for k, _, _ in METRICS])
    good = counts["verdict"]["合格"] + counts["verdict"]["基本合格"]
    m["pass_rate"] = good / n_turns if n_turns else None
    return {
        "metrics": m, "n_turns": n_turns, "n_cases": len([c for c in run["cases"] if only is None or c in only]),
        "verdict": dict(counts["verdict"]),
        "mode": {k: {"ok": v[0], "n": v[1], "acc": v[0] / v[1] if v[1] else None} for k, v in counts["mode"].items()},
        "judge_b": {k: dict(v) for k, v in counts["b"].items()},
        "feas_fail": counts["feas_fail"].most_common(8), "hard_fail": counts["hard_fail"].most_common(8),
        "soft_family": {k: mean(v) for k, v in counts["soft_family"].items()},
        "subdim": {k: mean(v) for k, v in counts["subdim"].items()},
        "a_dims": {k: mean(v) for k, v in counts["a_dims"].items()},
        "itype": {k: {kk: mean(vv) for kk, vv in v.items()} for k, v in counts["itype"].items()},
        "cost": {k: mean(v) for k, v in cost.items()},
    }


def leaderboard(runs, cases_by_id):
    avail = [r for r in runs if r["available"] and r["cases"]]
    board = {r["label"]: aggregate_run(r, cases_by_id) for r in avail}
    common = set.intersection(*[set(r["cases"]) for r in avail]) if avail else set()
    common_board = {r["label"]: aggregate_run(r, cases_by_id, only=common)["metrics"] for r in avail} if common else {}
    keys = [k for k, _, _ in METRICS]
    wins = Counter()
    for k in keys:
        vals = {lab: b["metrics"][k] for lab, b in board.items() if b["metrics"][k] is not None}
        if vals:
            best = max(vals.values())
            for lab, v in vals.items():
                if abs(v - best) < 1e-9:
                    wins[lab] += 1
    for lab, b in board.items():
        b["metrics"]["win"] = wins[lab] / len(keys) if board else None
    order = sorted(board, key=lambda lab: -(board[lab]["metrics"]["rule_total"] or 0))
    return {"order": order, "board": board, "common_cases": sorted(common), "common": common_board,
            "metrics": [{"key": k, "zh": zh, "group": g} for k, zh, g in METRICS]}


def fmt(x):
    return "—" if x is None else f"{x:.4f}"


def write_report(lb, state, out: Path):
    lines = ["# 运行结果报告（自动生成）", ""]
    if state:
        lines += [f"- 运行：{state.get('started', '')} → {state.get('finished', '进行中')}，状态 `{state.get('status')}`",
                  f"- 裁判：`{state.get('judge_model')}`（Judge A 与 Judge B）",
                  f"- case：{', '.join(c.replace('mt_single_', '') for c in state.get('case_ids', []))}", ""]
    lines += ["## 主表（Trip+ 论文口径）", "",
              "| 排名 | 模型 | 回应准确率 | 请求落实 | 意图保留 | 可行性 | 硬约束 | 软偏好 | 用户模拟 | Plan Avg | Win% | 规则总分 | 七项总分 | 逐轮合格率 | 完成 case |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for i, lab in enumerate(lb["order"], 1):
        b = lb["board"][lab]
        m = b["metrics"]
        lines.append(f"| {i} | {lab} | " + " | ".join(fmt(m[k]) for k in ("resp_acc", "fulfill", "preserve", "feas", "hard", "soft", "user_sim", "plan_avg"))
                     + f" | {m['win'] * 100:.1f} | {fmt(m['rule_total'])} | {fmt(m['total7'])} | {fmt(m['pass_rate'])} | {b['n_cases']} |")
    lines += ["", "排名按规则总分（前 6 项的均值，不含由 deepseek 裁判给出的用户模拟分）。Plan Avg 与 Win% 与论文定义一致。", ""]
    if lb["common"] and len(lb["common_cases"]) < max(b["n_cases"] for b in lb["board"].values()):
        lines += [f"## 共同 case 口径（{len(lb['common_cases'])} 个 case，各模型都跑完的部分）", "",
                  "| 模型 | 规则总分 | 七项总分 | 回应准确率 |", "|---|---|---|---|"]
        for lab, m in lb["common"].items():
            lines.append(f"| {lab} | {fmt(m['rule_total'])} | {fmt(m['total7'])} | {fmt(m['resp_acc'])} |")
        lines.append("")
    out.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="append", default=[], help="LABEL=RESULT_DIR")
    ap.add_argument("--pipeline-root")
    args = ap.parse_args()
    all_cases = json.loads((ROOT / "2_评测集_cases/query_10.json").read_text(encoding="utf-8"))
    state = None
    if args.pipeline_root:
        proot = Path(args.pipeline_root).resolve()
        state = load(proot / "pipeline_state.json") or {}
        case_ids = state.get("case_ids") or [c["id"] for c in all_cases]
        cases = [c for c in all_cases if c["id"] in case_ids]
        runs_spec = [(lab, proot / info["dir"], info.get("note", ""), {"config": info.get("config"), "exhausted": info.get("exhausted"),
                                                                       "exhausted_reason": info.get("exhausted_reason")})
                     for lab, info in state.get("models", {}).items()]
        judge_model = state.get("judge_model", "")
    else:
        proot = None
        cases = all_cases
        runs_spec = [(s.split("=", 1)[0], Path(s.split("=", 1)[1]), "", {}) for s in args.run]
        judge_model = ""
    cases_by_id = {c["id"]: c for c in cases}
    runs = [build_run(lab, d, note, cases, extra) for lab, d, note, extra in runs_spec]
    lb = leaderboard(runs, cases_by_id)
    brief = [{"id": c["id"], "itype": c.get("interaction_type"),
              "turns": [{"turn_id": t["turn_id"], "utterance": t["utterance"], "expect": t["response_expectation"],
                         "must_update": t.get("must_update", []), "must_preserve": t.get("must_preserve", [])} for t in c["turns"]]}
             for c in cases]
    log = None
    if state:
        log = {"events": state.get("events", []), "tasks": list(state.get("tasks", {}).values()),
               "status": state.get("status"), "started": state.get("started"), "finished": state.get("finished"),
               "max_attempts": state.get("max_attempts")}
    payload = {
        "cases": brief, "notes": ANNOTATIONS, "labels": LABELS, "runs": runs, "judge_model": judge_model,
        "b_dims": {k: v[0] for k, v in B_DIMS.items()}, "a_dims": A_DIMS, "leaderboard": lb, "log": log,
        "itype_zh": LABELS.get("interaction", {}),
    }
    data = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    html = (HERE / "run_viewer_template.html").read_text(encoding="utf-8").replace("__DATA__", data)
    out = HERE / "run_viewer.html"
    out.write_text(html, encoding="utf-8")
    if proot:
        (proot / "leaderboard.json").write_text(json.dumps(lb, ensure_ascii=False, indent=1), encoding="utf-8")
        write_report(lb, state, HERE.parent / "运行结果报告.md")
    print(f"wrote {out} ({out.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
