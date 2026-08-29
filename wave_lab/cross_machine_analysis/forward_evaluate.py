"""Evaluate a locked prospective Wave Lab record without recalculating its signals."""
from __future__ import annotations

import csv
import json
from pathlib import Path
from statistics import mean, median

ROOT = Path(__file__).resolve().parents[2]
TRACK = Path(__file__).resolve().parent / "tracking"
OHLC = ROOT / "csv" / "daily_ohlc" / "20260829" / "20260829_daily_ohlc.csv"
SIGNAL_DATE = "2026-08-28"
TARGET_DATE = "2026-08-29"
SUMMARY = TRACK / "forward_validation_20260828_summary.json"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict]) -> None:
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def num(value: str) -> float:
    return float(value)


def fmt(value: float) -> str:
    return str(int(value)) if value.is_integer() else str(value)


def enrich(row: dict[str, str], actual: dict[str, str]) -> dict[str, str]:
    result = dict(row)
    close = num(actual["Close"])
    result.update({
        "evaluation_status": "evaluated",
        "actual_bullish": str(num(actual["Close"]) > num(actual["Open"])),
        "actual_open": actual["Open"], "actual_high": actual["High"],
        "actual_low": actual["Low"], "actual_close": actual["Close"],
        "actual_close_ge_5000": str(close >= 5000),
        "actual_close_ge_10000": str(close >= 10000),
        "actual_close_ge_15000": str(close >= 15000),
        "actual_close_ge_20000": str(close >= 20000),
    })
    return result


def stats(rows: list[dict[str, str]]) -> dict[str, object]:
    closes = [num(r["actual_close"]) for r in rows]
    highs = [num(r["actual_high"]) for r in rows]
    lows = [num(r["actual_low"]) for r in rows]
    bullish = [r for r in rows if r["actual_bullish"] == "True"]
    return {
        "actual_bullish_count": len(bullish),
        "actual_bullish_rate": len(bullish) / len(rows) if rows else None,
        "actual_close_mean": mean(closes) if closes else None,
        "actual_close_median": median(closes) if closes else None,
        "actual_high_mean": mean(highs) if highs else None,
        "actual_low_mean": mean(lows) if lows else None,
        **{f"actual_close_ge_{threshold}_count": sum(c >= threshold for c in closes)
           for threshold in (5000, 10000, 15000, 20000)},
    }


def main() -> int:
    actual_rows = {str(int(row["Machine"])).zfill(3): row for row in read_csv(OHLC)}
    machine_path = TRACK / "forward_machine_signal_tracking.csv"
    machine_rows = read_csv(machine_path)
    target = [r for r in machine_rows if r["signal_date"] == SIGNAL_DATE and r["target_date"] == TARGET_DATE]
    if len(target) != 39:
        raise ValueError(f"expected 39 locked rows, got {len(target)}")
    evaluated = [enrich(row, actual_rows[row["machine"].zfill(3)]) for row in target]
    all_rows = [r for r in machine_rows if not (r["signal_date"] == SIGNAL_DATE and r["target_date"] == TARGET_DATE)] + evaluated
    write_csv(machine_path, all_rows)

    daily_path = TRACK / "forward_daily_signal_tracking.csv"
    daily_rows = read_csv(daily_path)
    daily = next(r for r in daily_rows if r["signal_date"] == SIGNAL_DATE and r["target_date"] == TARGET_DATE)
    daily.update(stats(evaluated)); daily["evaluation_status"] = "evaluated"
    write_csv(daily_path, daily_rows)

    group_path = TRACK / "forward_group_signal_tracking.csv"
    group_rows = read_csv(group_path)
    for group in group_rows:
        if group["signal_date"] != SIGNAL_DATE or group["target_date"] != TARGET_DATE:
            continue
        members = [r for r in evaluated if r["group"] == group["group"]]
        group.update(stats(members)); group["evaluation_status"] = "evaluated"
    write_csv(group_path, group_rows)

    strong_path = TRACK / "forward_strong_group_tracking.csv"
    strong_rows = read_csv(strong_path)
    for strong in strong_rows:
        if strong.get("signal_date") != SIGNAL_DATE or strong.get("target_date") != TARGET_DATE:
            continue
        members = [r for r in evaluated if r["group"] == strong["group"]]
        candidate = next((r for r in members if r["machine"] == strong.get("candidate_machine")), None)
        closes = sorted(((num(r["actual_close"]), r["machine"]) for r in members), reverse=True)
        strong.update(stats(members)); strong["evaluation_status"] = "evaluated"
        if candidate:
            rank = next(i for i, (_close, machine) in enumerate(closes, 1) if machine == candidate["machine"])
            strong.update({"candidate_actual_bullish": candidate["actual_bullish"],
                           "candidate_actual_open": candidate["actual_open"],
                           "candidate_actual_high": candidate["actual_high"],
                           "candidate_actual_low": candidate["actual_low"],
                           "candidate_actual_close": candidate["actual_close"],
                           "candidate_group_close_rank": rank,
                           "candidate_group_top2": str(rank <= 2),
                           "candidate_group_top3": str(rank <= 3),
                           "candidate_group_max_close_diff": fmt(closes[0][0] - num(candidate["actual_close"]))})
    write_csv(strong_path, strong_rows)

    with SUMMARY.open(encoding="utf-8") as handle:
        summary = json.load(handle)
    summary["evaluation_status"] = "evaluated"
    summary["actual_source"] = "csv/daily_ohlc/20260829/20260829_daily_ohlc.csv"
    summary["evaluation"] = stats(evaluated)
    for strong_group in summary.get("strong_groups", []):
        strong_group["evaluation_status"] = "evaluated"
    summary["future_outcome"] = {"bullish_evaluated": True, "close_thresholds_evaluated": True,
                                  "group_result_evaluated": True, "all3_result_evaluated": True}
    SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    candidate = next(r for r in evaluated if r["machine"] == "046")
    g1 = [r for r in evaluated if r["group"] == "g1"]
    ordered = sorted(g1, key=lambda r: num(r["actual_close"]), reverse=True)
    rank = next(i for i, r in enumerate(ordered, 1) if r["machine"] == "046")
    print(json.dumps({"candidate_046": candidate, "group_close_rank": rank, "group_stats": stats(g1)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
