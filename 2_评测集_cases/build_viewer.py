"""Build case_viewer.html: a standalone browser for query_10.json.

Usage:
    python build_viewer.py
Reads query_10.json next to this script and writes case_viewer.html (data embedded,
opens directly from disk).
"""

import copy
import json
from pathlib import Path

HERE = Path(__file__).parent

# Chinese annotations per case. "group" is 典型 / 边界 / 成对; "boundary" names the boundary
# the case probes; every turn has a paraphrase and an evaluator note (English stays in the data).
ANNOTATIONS = {
    "mt_single_0065": {
        "persona": "文化探索独行者",
        "group": "典型",
        "scene": "用户状态变化：中途加入老人",
        "boundary": "—",
        "focus": "独行文化游中途变成带老人出行：不仅人数要改，景点强度和节奏也要随之调整；后面两次更新不能冲掉这个变化。",
        "fail": "T1 只改人数，仍排爬山或长距离步行的景点；T3 换酒店后丢了第 2 天 10:30 以后才开始的要求。",
        "turns": [
            ("4 月 30 日–5 月 3 日独行一间房；以人文古迹为主，至少一个知名地标，不要全是冷门景点；在西安植物园儿童园附近吃一顿。", ""),
            ("有位老人加入，改成 2 人，房间、座位、门票都调整；之后避免爬山、久走和体力消耗大的项目。", "人数和画像要一起变：同行人变成老人"),
            ("第 2 天晚点开始，景点和市内交通都排在 10:30 之后。", "正常更新，应直接出方案"),
            ("改住民宿或公寓，订 Junyuan Inn。", "正常更新；老人加入和晚起都要保留"),
        ],
    },
    "mt_single_0125": {
        "persona": "蜜月情侣",
        "group": "典型",
        "scene": "环境变化：航班延误、景点高峰",
        "boundary": "—",
        "focus": "两次外部变化下调整时间表，同时保留最短直飞、评分最高的餐厅和两个必去景点。",
        "fail": "T1 第一天仍排 3 个以上景点；T2 删掉必去景点，或仍排在 10:00–12:00。",
        "turns": [
            ("蜜月旅行，5 月 1–3 日一间房；最短时长的直飞；在屏山文物径附近评分最高的餐厅吃一顿；预算 8200–12100 元；必去山顶缆车和香港迪士尼。", ""),
            ("航班晚到 30 分钟，落地后留出入住、吃饭或休息的缓冲；第一天最多两个景点。", "环境变化：顺延并减量，应直接出方案"),
            ("香港热门景点 10:00–12:00 人很多，山顶缆车和迪士尼保留但避开这个时段；正餐避开 12:00–13:00。", "环境变化：时段要求由规则检查"),
        ],
    },
    "mt_single_0029": {
        "persona": "三代同堂避暑游",
        "group": "边界",
        "scene": "请求消解：缺信息",
        "boundary": "缺少必要信息，应澄清",
        "focus": "T1 只说再订一顿晚饭，不知道哪天、哪段路线、人均多少，应问 1–2 个关键问题，而不是自己挑一家。",
        "fail": "T1 自行挑一家餐厅塞进方案；或问一串与缺失信息无关的问题。",
        "turns": [
            ("7 月 15–18 日一家五口（含 5 岁孩子、70 岁老人）三间房；避开世界之窗这类排队严重的景点；在南山图书馆附近最近的餐厅吃一顿。", ""),
            ("再订一顿晚饭，口碑好、价格合理、别太绕路。", "缺哪天、哪段路线、人均、菜系，应澄清"),
            ("第 2 天午饭订蛇口海洋科学馆附近的 Jiajun Eight-Treasure Beef Offal，人均约 29 元。", "信息齐全，应直接出方案"),
        ],
    },
    "mt_single_0029P": {
        "persona": "三代同堂避暑游（改编）",
        "group": "成对",
        "scene": "成对：对照 0029",
        "boundary": "信息齐全，不应再问",
        "focus": "与 0029 只差 T1：日期、路线、菜系、价位都给了，应直接安排，不应再反问。",
        "fail": "仍然反问（多余的澄清）；晚餐不在第 2 天，或人均超过 100 元。",
        "pair": "只改了 T1：加晚饭的请求补全了日期、路线、菜系和人均上限，不再缺信息，所以期望回应从澄清变为出方案。画像、数据库和其余轮次与 0029 完全相同。规则对这一轮只做餐饮偏好的软性检查，是否在第 2 天、人均是否在 100 元以内由 Judge B 或人工核对。",
        "turns": [
            ("与 0029 相同。", ""),
            ("第 2 天加一顿晚饭，沿第 2 天的路线，优先本地粤菜，人均 100 元以内。", "唯一变化：信息齐全，应直接出方案"),
            ("与 0029 相同：第 2 天午饭订指定餐厅。", "应出方案，第 2 天的午饭和晚饭都要在"),
        ],
    },
    "mt_single_0006": {
        "persona": "陪膝盖不好的母亲",
        "group": "边界",
        "scene": "请求消解：与画像冲突",
        "boundary": "新要求与画像冲突，应澄清",
        "focus": "T1 的要求与老人的行动限制和节奏规则冲突，而用户没说哪个优先，应澄清。",
        "fail": "直接排每天 4 个景点、全程步行；或自作主张只排 2 个却不说明。",
        "turns": [
            ("4 月 30 日–5 月 1 日一间房；返程选最晚到达的直达火车；在东山口附近最便宜的餐厅吃一顿；热门场馆考虑预约，写明车次。", ""),
            ("能不能每天至少 4 个热门景点，市内主要步行？", "与 69 岁母亲的膝盖问题冲突，且未说明优先级，应澄清"),
            ("别排太满，每天最多 2 个景点，优先少换乘的地铁，远了打车，不要为省钱硬走。", "优先级已明确，应出方案"),
        ],
    },
    "mt_single_0006P": {
        "persona": "陪膝盖不好的母亲（改编）",
        "group": "成对",
        "scene": "成对：对照 0006",
        "boundary": "已给出优先级，不应再问",
        "focus": "与 0006 只差 T1：用户说明了膝盖优先并给出上限，应直接出方案，每天不超过 2 个景点。",
        "fail": "仍然反问优先级（多余的澄清）；或排了 4 个景点。",
        "pair": "只改了 T1：用户明确说母亲的膝盖比景点数量更重要，并定下每天最多 2 个景点，所以期望回应从澄清变为出方案。画像、数据库和其余轮次与 0006 完全相同。",
        "turns": [
            ("与 0006 相同。", ""),
            ("本来想每天至少 4 个热门景点、主要步行，但我妈的膝盖优先，所以每天最多 2 个景点，用地铁或打车代替长距离步行。", "唯一变化：优先级已给出，应直接出方案"),
            ("与 0006 相同：每天最多 2 个景点，少换乘地铁，远了打车。", "应出方案"),
        ],
    },
    "mt_single_0148": {
        "persona": "室友预算游",
        "group": "边界",
        "scene": "请求消解：总预算做不到",
        "boundary": "与旧约束冲突，应澄清；授权后应判无解",
        "focus": "预算压到最低可行花费以下：未授权时应澄清哪个条件可放宽；用户授权直说后应判无解，并说明原因。",
        "fail": "给出一份 5900 元以内的方案，其中偷换了交通或住宿。",
        "turns": [
            ("5 月 1–5 日两间房，预算 7600–8400 元；在 1010 Art Bar (Cuihu Branch) 附近评分最高的店吃一顿；必去老宇河湿地公园、滇池海埂公园。", ""),
            ("预算压到 5900 元以内，但之前说的交通、酒店、餐饮都不要变。", "新预算低于最低可行花费 7600 元，且未授权判无解，应澄清"),
            ("5900 元做不到就直说，别偷偷换交通、住宿、餐饮。", "用户已授权，应判无解：点名冲突、给证据、不附方案"),
        ],
    },
    "mt_single_0005": {
        "persona": "怕热情侣的深圳行",
        "group": "边界",
        "scene": "长程对齐：酒店评分与价格冲突",
        "boundary": "与旧约束冲突，应澄清；授权后应判无解；放宽后再出方案",
        "focus": "冲突落在酒店：评分最高的酒店每晚 263 元，新上限 160 元。一个 case 走完出方案、澄清、判无解、再出方案四个阶段。",
        "fail": "T2 直接换成便宜酒店；T3 给了方案；T4 丢了仙湖植物园。",
        "turns": [
            ("7 月 14–17 日一间房；主要想去南头古城；在 Nantou Ancient City Museum 附近最近的餐厅吃一顿；住全市评分最高的酒店；总预算 5400–7200 元。", ""),
            ("第 3 天加仙湖植物园，其余不变。", "正常更新"),
            ("酒店压到每晚 160 元以内，同时保留评分最高。", "评分最高的酒店每晚 263 元，两条冲突，应澄清"),
            ("找不到就直说，别偷偷放宽之前的条件。", "用户已授权，应判无解"),
            ("放宽 160 元上限，以评分最高为准，其余保留，重新规划。", "放宽后应出方案，前面所有要求都要保留"),
        ],
    },
    "mt_single_0115": {
        "persona": "商务顺游的伴侣",
        "group": "边界",
        "scene": "长程对齐：餐厅要求与人均冲突",
        "boundary": "与旧约束冲突，应澄清；授权后应判无解；放宽后再出方案",
        "focus": "冲突落在餐厅：离海洋馆最近的餐厅人均 73 元，新上限 40 元。与 0005、0050 流程相同、冲突对象不同，用来检验同一边界能否稳定复现。",
        "fail": "T2 直接换一家便宜但不在海洋馆附近的餐厅；T3 给了方案；T4 丢了大巴扎。",
        "turns": [
            ("5 月 3–5 日两人一间房，以商务为主、顺便游玩；节奏适中，避开新疆博物馆这类排队长的景点；在乌鲁木齐海洋馆附近吃一顿；必去红山公园、水磨沟景区。", ""),
            ("第 2 天加新疆国际大巴扎，其余不变。", "正常更新"),
            ("那顿饭压到人均 40 元以内，之前的餐厅要求也不变。", "满足原餐厅要求的选项人均 73 元，两条冲突，应澄清"),
            ("做不到就直说，别偷偷换掉或放宽之前的要求。", "用户已授权，应判无解"),
            ("可以放宽人均上限，但原餐厅要求优先，其余都保留，重新规划。", "放宽后应出方案，大巴扎等前面的要求都要保留"),
        ],
    },
    "mt_single_0050": {
        "persona": "冬季独行穷游",
        "group": "边界",
        "scene": "长程对齐：酒店价格区间冲突",
        "boundary": "与旧约束冲突，应澄清；授权后应判无解；放宽后再出方案",
        "focus": "冲突落在酒店价格：新上限每晚 70 元，与原来的 105–135 元区间直接矛盾。冬季沈阳户外寒冷，画像偏好紧凑行程。",
        "fail": "T2 直接订一家 70 元以下的酒店；T3 给了方案；T4 丢了第 2 天 10:00 以后才开始的要求。",
        "turns": [
            ("1 月 13–16 日独行一间房；住宿每晚 105–135 元；至少去一个知名地标，不要只有冷门景点。", ""),
            ("第 2 天 10:00 前不开始游览和市内移动，其余不变。", "正常更新"),
            ("酒店压到每晚 70 元以内，之前的酒店要求也不变。", "新上限低于原价格区间，两条冲突，应澄清"),
            ("做不到就直说，别偷偷换掉或放宽之前的要求。", "用户已授权，应判无解"),
            ("可以把新上限提高一些，但原酒店要求优先，其余都保留，重新规划。", "放宽后应出方案，第 2 天晚起要保留"),
        ],
    },
}

# Shown in the "覆盖设计" section of the viewer.
COVERAGE = {
    "principles": [
        ("沿 Trip+ 的三个构建维度选 case",
         "数据沙盒：7 条路线、7 个目的地，覆盖夏季酷暑台风、冬季寒冷、五一高峰；旅行者画像：8 种人群，开局就含老人或孩子的只有 2 个画像，另有 0065 中途加入老人；交互原型：四种都有。"),
        ("侧重澄清和判无解",
         "Trip+ 全库 570 轮里澄清只占 9%、判无解不到 1%。本评测集 37 轮中澄清 6 轮、判无解 4 轮，另有 2 轮是成对案例中\"不该问\"的对照轮，合计 12 轮落在回应方式的边界上。"),
        ("同一边界测多次，冲突对象不同",
         "\"与旧约束冲突 → 授权后判无解\"这条边界用 4 个 case 测量，冲突分别落在总预算（0148）、酒店评分（0005）、餐厅距离（0115）、酒店价格区间（0050）。避免只换人名和措辞，也让一次失败能和其他三次互相印证。"),
        ("2 组成对案例，只改 T1 的一句话",
         "0029 对 0029P 检验\"缺信息\"和\"信息齐全\"的区分；0006 对 0006P 检验\"与画像冲突\"和\"用户已给出优先级\"的区分。两组都是改编，结果单独标注。"),
    ],
    "groups": [
        ("典型", "正常更新和环境变化，每轮都应直接出方案。考察落实新要求、不丢旧要求、贴合画像。"),
        ("边界", "至少有一轮应澄清或判无解。考察模型能否在对的时机停下来问，或诚实地说做不到。"),
        ("成对", "由边界 case 改编，只改一个条件，使期望回应从澄清变为出方案。考察模型会不会多余地反问。"),
    ],
    "pairs": [
        {
            "a": "mt_single_0029", "b": "mt_single_0029P",
            "change": "加晚饭的请求是否给出日期、路线、菜系和人均上限",
            "expect": "澄清 → 直接出方案",
            "why": "缺信息时无论选哪家都是替用户猜；信息给全后再问就是多余的来回。",
        },
        {
            "a": "mt_single_0006", "b": "mt_single_0006P",
            "change": "用户是否说明\"妈妈的膝盖优先，每天最多 2 个景点\"",
            "expect": "澄清 → 直接出方案，每天不超过 2 个景点",
            "why": "冲突仍在，但取舍方向已由用户给出，模型应执行而不是再问。",
        },
    ],
}

CITY_ZH = {
    "Beijing": "北京", "Shenzhen": "深圳", "Shanghai": "上海", "Hong Kong": "香港", "Zhuhai": "珠海",
    "Guangzhou": "广州", "Zhengzhou": "郑州", "Kunming": "昆明", "Changsha": "长沙", "Urumqi": "乌鲁木齐",
    "Nanjing": "南京", "Shenyang": "沈阳", "Taiyuan": "太原", "Xi'an": "西安",
}

LABELS = {
    "mode": {"plan": "出方案", "clarification": "澄清", "no_solution": "判无解"},
    "interaction": {
        "user_state_evolution": "用户状态变化",
        "environment_driven_replanning": "环境变化重规划",
        "long_horizon_alignment": "长程对齐",
        "request_resolution": "请求消解",
    },
    "party_type": {
        "couple": "情侣", "family_with_child": "带孩子家庭", "family_with_elder": "带老人家庭",
        "friends": "朋友", "solo": "独行",
    },
    "accommodation_style": {"budget": "经济", "comfort": "舒适", "luxury": "豪华"},
    "mobility_constraints": {"heat_sensitive": "怕热", "knee_issue": "膝盖不适", "stroller": "推婴儿车/照顾幼童", "cold_sensitive": "怕冷"},
    "physical_rules": {"avoid_long_walk": "避免久走", "standard_walking": "正常步行", "long_walk_ok": "能走远路"},
    "schedule_rules": {"dense": "紧凑", "moderate": "适中", "relaxed": "宽松"},
    "interest_tags": {
        "food": "美食", "history": "历史", "nature": "自然", "park": "公园", "shopping": "购物",
        "landmark": "地标", "museum": "博物馆",
    },
    "hate_tags": {
        "expensive_meal": "昂贵餐饮", "extreme_weather": "极端天气",
        "long_local_transfer": "市内长距离移动", "overpacked_schedule": "行程太满", "red_eye_transport": "红眼航班",
    },
    "transport_preferences": {
        "avoid_early_departure": "不早出发", "avoid_late_arrival": "不晚到达", "avoid_red_eye": "不坐红眼航班",
        "avoid_transfer": "不换乘", "prefer_direct": "偏好直达", "prefer_flight": "偏好飞机", "prefer_train": "偏好火车",
    },
    "rest_preferences": {"afternoon_low_intensity": "下午低强度", "late_start": "晚起", "midday_rest": "午休"},
    "hard": {
        "attraction_avoid_high_queue": "避开高排队风险景点",
        "attraction_crowd_queue_initial_must_visit": "必去的热门景点",
        "attraction_initial_must_visit_before_availability": "初始必去景点（后续会失效）",
        "attraction_must_visit_named": "指定必去景点",
        "attraction_require_popular_hotspot": "至少一个知名地标",
        "budget_constraint": "总预算区间",
        "flight_arrival_time_range": "航班落地时间窗",
        "flight_seat_class": "舱位等级",
        "flight_shortest_duration_direct": "最短直飞",
        "hotel_highest_rated": "评分最高酒店",
        "hotel_newest_decoration": "最新装修酒店",
        "hotel_price_range": "酒店价格区间",
        "intercity_round_trip_mode_required": "往返交通方式",
        "party_composition_required": "同行人构成",
        "party_size_required": "人数",
        "restaurant_cheapest_nearby_attraction": "景点附近最便宜餐厅",
        "restaurant_closest_to_attraction": "离景点最近的餐厅",
        "restaurant_highest_rated": "附近评分最高餐厅",
        "room_count_required": "房间数",
        "train_latest_arrival_direct": "最晚到达的直达火车",
        "trip_date_range_required": "出行日期",
    },
    "rules": {
        "budget_guarded": "控制花费", "budget_tight_cap": "预算紧", "hotel_value_first": "酒店重性价比",
        "interest_culture": "偏好人文", "interest_landmark": "偏好地标", "interest_local_food": "偏好本地美食",
        "interest_outdoor_nature": "偏好自然户外", "interest_shopping": "偏好购物", "meal_avoid_expensive": "避免贵餐",
        "mobility_accessibility": "照顾行动能力", "schedule_pacing": "节奏不过密",
        "transport_avoid_early_departure": "避免早班", "transport_avoid_late_arrival": "避免晚到",
        "transport_avoid_red_eye": "避免红眼", "transport_avoid_transfer": "避免换乘",
        "transport_prefer_flight": "偏好飞机", "transport_prefer_train": "偏好火车",
        "weather_avoid_cold_exposure": "避免寒冷暴露", "weather_avoid_heat_exposure": "避免高温暴晒",
        "weather_need_backup": "天气备选方案",
    },
    "advisory": {
        "dry_air": "空气干燥", "extreme_heat": "酷暑", "high_altitude_adaptation": "高原适应",
        "plateau_uv_exposure": "高原紫外线", "rain_risk": "降雨", "typhoon_rain_risk": "台风雨",
        "winter_outdoor_exposure": "冬季户外寒冷", "wind_dust_risk": "风沙",
    },
    "envhint": {"cross_border": "跨境", "document_permit": "证件许可", "reservation": "需预约", "security_check": "安检"},
    "update": {
        "initial_plan": "初始方案", "add_attraction": "加景点", "apply_relaxed_constraint": "按放宽条件重排",
        "budget_update": "预算调整", "clarification_or_ranked_options": "澄清缺失信息",
        "dietary_update": "饮食限制", "environment_aware_replanning": "环境变化重规划",
        "explain_unsolved": "说明无解", "hotel_requirement": "指定酒店", "late_start_request": "晚点开始",
        "party_update": "人数变化", "prior_constraint_clarification": "澄清与旧约束的冲突",
        "priority_clarification": "澄清优先级", "resolved_pacing_limit": "按确定的节奏上限",
        "resolved_priority": "按确定的优先级", "resolved_restaurant_preference": "按确定的用餐要求",
        "restaurant_requirement": "指定餐厅", "route_preference": "交通偏好", "schedule_update": "时间调整",
    },
}


def slim(record: dict) -> dict:
    r = copy.deepcopy(record)
    meta = r["meta_info"]["base_query_meta"]
    meta["user_profile"].pop("derivation_context", None)
    for hc in meta["hard_constraints"].values():
        for key in list(hc):
            if key.startswith("acceptable_") and key.endswith("_options"):
                hc.pop(key)
    return r


def main() -> None:
    cases = [slim(r) for r in json.loads((HERE / "query_10.json").read_text(encoding="utf-8"))]
    payload = {"cases": cases, "notes": ANNOTATIONS, "coverage": COVERAGE, "labels": LABELS, "cities": CITY_ZH}
    data = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    html = (HERE / "viewer_template.html").read_text(encoding="utf-8").replace("__DATA__", data)
    out = HERE / "case_viewer.html"
    out.write_text(html, encoding="utf-8")
    print(f"wrote {out} ({out.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
