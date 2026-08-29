#!/usr/bin/env python3
"""Export prospective Wave Lab tracking data for the static web page."""

import csv
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TRACKING = ROOT / "wave_lab" / "cross_machine_analysis" / "tracking"
WEB = ROOT / "docs" / "wave_lab" / "data" / "forward"


def read_csv(name):
    with (TRACKING / name).open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def as_number(value):
    if value in (None, ""):
        return None
    try:
        number = float(value)
        return int(number) if number.is_integer() else number
    except ValueError:
        return value


def as_bool(value):
    return value is True or str(value).lower() == "true"


def machine_row(row):
    result = dict(row)
    for key in ("convergence_score", "score"):
        result[key] = as_number(result.get(key))
    for key in ("UP_UP_UP", "RIGHT", "LOW_CONVERGENCE_RIGHT", "DOWN_DOWN_DOWN", "ALL_3"):
        result[key] = as_bool(result.get(key))
    return result


def group_row(row):
    result = dict(row)
    for key in ("machine_count", "UP_UP_UP_count", "RIGHT_count", "LOW_CONVERGENCE_RIGHT_count",
                "ALL_3_count", "DOWN_DOWN_DOWN_count", "direction_balance", "group_signal_total",
                "group_signal_rank"):
        result[key] = as_number(result.get(key))
    for key in ("A_rank_top3", "B_all3_ge1", "C_direction_positive", "STRONG_GROUP"):
        result[key] = as_bool(result.get(key))
    result["group_signal_score"] = as_number(result.get("group_signal_score"))
    return result


def history_row(summary):
    strong = summary.get("strong_groups", [])
    first = strong[0] if strong else {}
    return {
        "signal_date": summary["signal_date"],
        "target_date": summary["target_date"],
        "candidate": first.get("candidate_machine", "—"),
        "strong_group": first.get("group", "—"),
        "ALL_3_count": summary["machine_counts"]["ALL_3_count"],
        "evaluation_status": summary.get("evaluation_status", "pending"),
    }


def main():
    summaries = []
    for path in TRACKING.glob("forward_validation_*_summary.json"):
        with path.open(encoding="utf-8") as handle:
            summaries.append(json.load(handle))
    if not summaries:
        raise FileNotFoundError("no forward validation summaries")
    summary = max(summaries, key=lambda item: item["signal_date"])
    machines = [machine_row(row) for row in read_csv("forward_machine_signal_tracking.csv")]
    groups = [group_row(row) for row in read_csv("forward_group_signal_tracking.csv")]
    def detail_for(item):
        item_machines = [row for row in machines if row["signal_date"] == item["signal_date"]]
        item_groups = [row for row in groups if row["signal_date"] == item["signal_date"]]
        return {
            **item,
            "machine_signals": item_machines,
            "group_signals": item_groups,
            "strong_groups": json.loads(json.dumps(item.get("strong_groups", []))),
            "signal_candidates": {
                "ALL_3": [row["machine"] for row in item_machines if row["ALL_3"]],
                "UP_UP_UP": [row["machine"] for row in item_machines if row["UP_UP_UP"]],
                "RIGHT": [row["machine"] for row in item_machines if row["RIGHT"]],
                "LOW_CONVERGENCE_RIGHT": [row["machine"] for row in item_machines if row["LOW_CONVERGENCE_RIGHT"]],
            },
            "group_top3": item_groups[:3],
            "future_outcome": {
                "actual_open": None, "actual_high": None, "actual_low": None, "actual_close": None,
                "bullish": None, "close_ge_5000": None, "close_ge_10000": None, "close_ge_20000": None,
                "group_bullish_machine_rate": None, "group_mean_close": None, "group_max_close": None,
                "strong_machines_ge_5000": None, "strong_machines_ge_10000": None,
                "all3_group_close_rank": None, "all3_top2": None, "all3_top3": None,
            },
        }
    WEB.mkdir(parents=True, exist_ok=True)
    existing = []
    history_path = WEB / "history.json"
    if history_path.exists():
        with history_path.open(encoding="utf-8") as handle:
            existing = json.load(handle).get("rows", [])
    rows = {row["signal_date"]: row for row in existing}
    for item in summaries:
        rows[item["signal_date"]] = history_row(item)
    history = {"rows": sorted(rows.values(), key=lambda row: row["signal_date"], reverse=True)}
    detail = detail_for(summary)
    for item in summaries:
        detail_path = WEB / f'{item["signal_date"].replace("-", "")}.json'
        with detail_path.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(detail_for(item), handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    for path, payload in ((WEB / "latest.json", detail), (WEB / "history.json", history)):
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    print("exported", len(machines), "machines", len(groups), "groups", "to", WEB)


if __name__ == "__main__":
    main()
