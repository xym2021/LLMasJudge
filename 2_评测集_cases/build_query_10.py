"""Extract the evaluation cases from Trip+ and build the two paired cases 0029P and 0006P.

Usage:
    python build_query_10.py <path-to-trip-plus>/query/query_en/multiturn/query.json
Writes query_10.json next to this script.
"""

import copy
import json
import sys
from pathlib import Path

# Typical cases first, then boundary cases; each paired case follows its base case.
CASE_IDS = [
    "mt_single_0065",
    "mt_single_0125",
    "mt_single_0029",
    "mt_single_0006",
    "mt_single_0148",
    "mt_single_0005",
    "mt_single_0115",
    "mt_single_0050",
]

PAIRS = {
    "mt_single_0029": {
        "suffix": "P",
        "note": (
            "Only T1 changes: the dinner request now carries the day, route anchor, cuisine "
            "and price cap, so nothing is missing. Expected mode flips from clarification to plan."
        ),
        "utterance": (
            "Could we also add a dinner on Day 2? Somewhere along our Day 2 route, local "
            "Cantonese food is our priority, and please keep it under 100 yuan per person."
        ),
        "must_update": ["resolved_restaurant_preference"],
        "resolution": {
            "resolved_restaurant_preference": {
                "meal_day": 2,
                "meal_type": "dinner",
                "price_per_person_max": 100,
                "cuisine_priority": "local Cantonese cuisine",
                "route_anchor": "near the existing day-2 route",
            }
        },
    },
    "mt_single_0006": {
        "suffix": "P",
        "note": (
            "Only T1 changes: the user states that the elder's knee comfort outranks "
            "attraction count and fixes the cap at 2 per day. Expected mode flips from "
            "clarification to plan."
        ),
        "utterance": (
            "I was hoping to fit in at least four popular spots each day and walk most of the "
            "way, but my mom's knees come first. So please cap it at 2 attractions per day and "
            "use subway or taxi instead of long walks."
        ),
        "must_update": ["resolved_pacing_limit"],
        "resolution": {
            "resolved_pacing_limit": {
                "priority": "comfort and feasible pacing outrank packed attraction coverage",
                "max_attractions_per_day": 2,
            }
        },
    },
}


def build_pair_case(base: dict, spec: dict) -> dict:
    """Copy the base case and change T1 only, so that a plan is expected instead of a clarification."""
    case = copy.deepcopy(base)
    case["id"] = base["id"] + spec["suffix"]
    case["pair_of"] = base["id"]
    case["pair_note"] = spec["note"]
    t1 = case["turns"][1]
    t1["utterance"] = spec["utterance"]
    t1["response_expectation"] = "plan"
    t1["must_update"] = spec["must_update"]
    t1.pop("blocking_constraints", None)
    key = next(iter(spec["resolution"]))
    for turn in case["turns"][1:]:
        resolutions = turn["oracle_state_after_turn"].setdefault("active_request_resolutions", [])
        if not any(key in r for r in resolutions):
            resolutions.append(copy.deepcopy(spec["resolution"]))
    return case


def main() -> None:
    source = Path(sys.argv[1])
    records = {r["id"]: r for r in json.loads(source.read_text(encoding="utf-8"))}
    cases = []
    for cid in CASE_IDS:
        cases.append(records[cid])
        if cid in PAIRS:
            cases.append(build_pair_case(records[cid], PAIRS[cid]))
    out = Path(__file__).with_name("query_10.json")
    out.write_text(json.dumps(cases, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"wrote {len(cases)} cases -> {out}")


if __name__ == "__main__":
    main()
