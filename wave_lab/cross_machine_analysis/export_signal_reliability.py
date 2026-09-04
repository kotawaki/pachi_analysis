#!/usr/bin/env python3
"""Export prospective Signal Summary results and observed-rate records.

This is a derived, read-only view of locked Forward JSON files.  It never
recomputes signals and it never writes the Forward source files.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
FORWARD_DIR = ROOT / "docs" / "wave_lab" / "data" / "forward"
OUTPUT = FORWARD_DIR / "signal_reliability.json"
START_DATE = "20260828"

FEATURES = (
    "UP_UP_UP",
    "RIGHT",
    "LOW_CONVERGENCE_RIGHT",
    "DOWN_DOWN_DOWN",
    "ALL_3",
)
SIGNAL_COMBOS = (
    "ALL_3",
    "ALL_2_UP_RIGHT",
    "ALL_2_UP_LOWCONV_RIGHT",
    "ALL_2_RIGHT_LOWCONV_RIGHT",
    "ALL_2_TOTAL",
)
ALL_2_COMBOS = SIGNAL_COMBOS[1:4]


def compact_date(value: Any) -> str:
    return str(value or "").replace("-", "")


def as_bool(value: Any) -> bool:
    return value is True or str(value).lower() == "true"


def evaluated(row: dict[str, Any], status: str) -> bool:
    return status == "evaluated" and row.get("actual_bullish") not in (None, "")


def empty_stat(key: str) -> dict[str, Any]:
    return {"key": key, "bullish_hits": 0, "samples": 0, "bullish_rate": None, "pending_count": 0, "dates": []}


def add_result(stat: dict[str, Any], row: dict[str, Any], status: str, date: str) -> None:
    if evaluated(row, status):
        stat["samples"] += 1
        stat["bullish_hits"] += int(as_bool(row.get("actual_bullish")))
        if date not in stat["dates"]:
            stat["dates"].append(date)
    else:
        stat["pending_count"] += 1


def finish(stat: dict[str, Any]) -> dict[str, Any]:
    stat["dates"].sort()
    stat["bullish_rate"] = (
        stat["bullish_hits"] / stat["samples"] if stat["samples"] else None
    )
    return stat


def combo_for(row: dict[str, Any]) -> str | None:
    up = as_bool(row.get("UP_UP_UP"))
    right = as_bool(row.get("RIGHT"))
    low = as_bool(row.get("LOW_CONVERGENCE_RIGHT"))
    if up and right and low:
        return "ALL_3"
    if up and right and not low:
        return "ALL_2_UP_RIGHT"
    if up and not right and low:
        return "ALL_2_UP_LOWCONV_RIGHT"
    if not up and right and low:
        return "ALL_2_RIGHT_LOWCONV_RIGHT"
    return None


def machine_view(row: dict[str, Any], machine_stats: dict[str, dict[str, dict[str, Any]]],
                combo_stats: dict[str, dict[str, dict[str, Any]]],
                status: str) -> dict[str, Any]:
    machine = str(row.get("machine", ""))
    features = {feature: as_bool(row.get(feature)) for feature in FEATURES}
    combo = combo_for(row)
    result = {
        "machine": machine,
        "group": row.get("group"),
        "signals": features,
        "combination": combo,
        "actual_bullish": (as_bool(row.get("actual_bullish"))
                            if evaluated(row, status) else None),
        "evaluation_status": status,
        "history": {},
    }
    for feature in FEATURES:
        result["history"][feature] = machine_stats[machine][feature]
    for combo_name in SIGNAL_COMBOS:
        result["history"][combo_name] = combo_stats[machine][combo_name]
    return result


def load_forwards() -> list[dict[str, Any]]:
    records = []
    for path in sorted(FORWARD_DIR.glob("*.json")):
        if path.name in {"latest.json", "history.json", "signal_reliability.json"}:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        signal_date = compact_date(data.get("signal_date"))
        if signal_date < START_DATE or not signal_date.isdigit():
            continue
        if data.get("mode") != "forward/prospective":
            continue
        if compact_date(data.get("max_input_date")) > signal_date:
            raise ValueError(f"future max_input_date in {path.name}")
        if as_bool(data.get("future_data_used")):
            raise ValueError(f"future_data_used=true in {path.name}")
        records.append(data)
    return sorted(records, key=lambda item: compact_date(item.get("signal_date")))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=str(OUTPUT))
    args = parser.parse_args()

    forwards = load_forwards()
    if not forwards:
        raise SystemExit("no eligible prospective Forward JSON files")

    global_stats = {name: empty_stat(name) for name in FEATURES}
    combo_global = {name: empty_stat(name) for name in SIGNAL_COMBOS}
    machine_stats: dict[str, dict[str, dict[str, Any]]] = {}
    combo_machine_stats: dict[str, dict[str, dict[str, Any]]] = {}
    evaluated_dates: list[str] = []
    pending_dates: list[str] = []
    today: dict[str, Any] | None = None

    for data in forwards:
        signal_date = compact_date(data.get("signal_date"))
        status = str(data.get("evaluation_status") or "pending").lower()
        (evaluated_dates if status == "evaluated" else pending_dates).append(signal_date)
        if today is None or signal_date > compact_date(today.get("signal_date")):
            today = data
        for row in data.get("machine_signals", []):
            machine = str(row.get("machine", ""))
            machine_stats.setdefault(machine, {name: empty_stat(name) for name in FEATURES})
            combo_machine_stats.setdefault(machine, {name: empty_stat(name) for name in SIGNAL_COMBOS})
            for feature in FEATURES:
                if as_bool(row.get(feature)):
                    add_result(global_stats[feature], row, status, signal_date)
                    add_result(machine_stats[machine][feature], row, status, signal_date)
            combo = combo_for(row)
            if combo:
                add_result(combo_global[combo], row, status, signal_date)
                add_result(combo_machine_stats[machine][combo], row, status, signal_date)
                if combo in ALL_2_COMBOS:
                    add_result(combo_global["ALL_2_TOTAL"], row, status, signal_date)
                    add_result(combo_machine_stats[machine]["ALL_2_TOTAL"], row, status, signal_date)

    for stat in global_stats.values():
        finish(stat)
    for stat in combo_global.values():
        finish(stat)
    for stats in machine_stats.values():
        for stat in stats.values():
            finish(stat)
    for stats in combo_machine_stats.values():
        for stat in stats.values():
            finish(stat)

    if today is None:
        raise SystemExit("no latest prospective Forward")
    today_date = compact_date(today.get("signal_date"))
    today_status = str(today.get("evaluation_status") or "pending").lower()
    today_machines = [
        machine_view(row, machine_stats, combo_machine_stats, today_status)
        for row in today.get("machine_signals", [])
    ]
    today_payload = {
        "signal_date": today_date,
        "target_date": compact_date(today.get("target_date")),
        "evaluation_status": today_status,
        "machines": today_machines,
    }

    payload = {
        "metadata": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "prospective_start_date": START_DATE,
            "evaluated_dates": sorted(set(evaluated_dates)),
            "pending_dates": sorted(set(pending_dates)),
            "source_type": "derived from locked prospective Forward JSON",
            "source_files": [f"{d}.json" for d in sorted(set(evaluated_dates + pending_dates))],
            "prediction_use": False,
            "signal_recalculation": False,
            "historical_backfill": False,
        },
        "today": today_payload,
        "global_signal_stats": {name: finish(stat) for name, stat in global_stats.items()},
        "exact_combination_stats": {name: finish(stat) for name, stat in combo_global.items()},
        "machine_signal_stats": machine_stats,
        "machine_combination_stats": combo_machine_stats,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"exported {len(forwards)} Forward records to {output}")


if __name__ == "__main__":
    main()
