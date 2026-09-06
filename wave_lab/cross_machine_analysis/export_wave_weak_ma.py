#!/usr/bin/env python3
"""Research-only prospective Wave + weak-MA observation tracker."""
from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT_PATH = ROOT / "docs/wave_lab/data/forward/prospective_state_snapshots.json"
OUTPUT_DIR = ROOT / "wave_lab/cross_machine_analysis/tracking"
CSV_PATH = OUTPUT_DIR / "wave_weak_ma_prospective.csv"
JSON_PATH = OUTPUT_DIR / "wave_weak_ma_summary.json"
START_DATE = "20260828"
UNIVERSE = {f"{n:03d}" for n in range(39, 78)}
SIGNALS = ("UP_UP_UP", "RIGHT", "LOW_CONVERGENCE_RIGHT")
FIELDNAMES = [
    "signal_date", "target_date", "machine", "group", "UP_UP_UP", "RIGHT",
    "LOW_CONVERGENCE_RIGHT", "ALL_3", "score", "wave_direction_pattern",
    "region", "convergence_score", "signal_close", "ma5", "ma20", "ma75",
    "ma5_direction", "ma20_direction", "ma75_direction", "close_vs_ma5",
    "close_vs_ma20", "close_vs_ma75", "alignment", "evaluation_status",
    "actual_open", "actual_high", "actual_low", "actual_close",
    "actual_bullish", "max_input_date", "future_data_used", "prediction_use",
    "source_snapshot", "prospective_record",
]


def truth(value: Any) -> bool:
    return value is True or str(value).lower() == "true"


def machine_id(value: Any) -> str:
    text = str(value).strip()
    return f"{int(text):03d}" if text.isdigit() else text


def qualifies(row: dict[str, Any]) -> bool:
    return (
        any(truth(row.get(signal)) for signal in SIGNALS)
        and all(row.get(f"ma{period}_direction") == "DOWN" for period in (5, 20, 75))
        and all(row.get(f"close_vs_ma{period}") == "BELOW" for period in (5, 20, 75))
    )


def normalize_record(row: dict[str, Any], source_snapshot: str) -> dict[str, Any]:
    record = {field: row.get(field) for field in FIELDNAMES}
    record["machine"] = machine_id(record.get("machine"))
    for signal in (*SIGNALS, "ALL_3"):
        record[signal] = truth(record.get(signal))
    record["evaluation_status"] = str(record.get("evaluation_status") or "pending").lower()
    if record["evaluation_status"] != "evaluated":
        for field in ("actual_bullish", "actual_open", "actual_high", "actual_low", "actual_close"):
            record[field] = None
    else:
        record["actual_bullish"] = truth(record.get("actual_bullish"))
    record["future_data_used"] = False
    record["prediction_use"] = False
    record["source_snapshot"] = source_snapshot
    record["prospective_record"] = True
    return record


def apply_evaluation(existing: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    """Attach actual fields only; never alter signal or MA feature fields."""
    if str(existing.get("evaluation_status", "pending")).lower() == "evaluated":
        return existing
    if str(current.get("evaluation_status", "pending")).lower() != "evaluated":
        return existing
    updated = dict(existing)
    for field in ("evaluation_status", "actual_open", "actual_high", "actual_low", "actual_close", "actual_bullish"):
        updated[field] = current.get(field)
    updated["evaluation_status"] = "evaluated"
    updated["actual_bullish"] = truth(current.get("actual_bullish"))
    return updated


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    evaluated = [r for r in records if str(r.get("evaluation_status")).lower() == "evaluated"]
    hits = sum(1 for r in evaluated if truth(r.get("actual_bullish")))
    signal_stats = {}
    for signal in SIGNALS:
        rows = [r for r in evaluated if truth(r.get(signal))]
        signal_hits = sum(1 for r in rows if truth(r.get("actual_bullish")))
        signal_stats[signal] = {
            "samples": len(rows), "bullish_count": signal_hits,
            "bullish_rate": round(signal_hits / len(rows), 4) if rows else None,
        }
    return {
        "total_samples": len(records), "evaluated_samples": len(evaluated),
        "pending_samples": len(records) - len(evaluated), "bullish_count": hits,
        "bullish_rate": round(hits / len(evaluated), 4) if evaluated else None,
        "signal_stats": signal_stats,
    }


def load_existing() -> list[dict[str, Any]]:
    if not CSV_PATH.exists():
        return []
    with CSV_PATH.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def update(target_date: str, bootstrap: bool = True) -> dict[str, Any]:
    payload = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    snapshots = [r for r in payload.get("records", []) if machine_id(r.get("machine")) in UNIVERSE]
    by_key = {(str(r.get("signal_date")), machine_id(r.get("machine"))): r for r in snapshots}
    existing = {(str(r.get("signal_date")), machine_id(r.get("machine"))): r for r in load_existing()}
    # Explicitly authorized initial prospective sample; all other additions are current-date only.
    if bootstrap and not existing and ("20260904", "066") in by_key:
        existing[("20260904", "066")] = normalize_record(by_key[("20260904", "066")], "docs/wave_lab/data/forward/20260904.json")
    evaluated_before = sum(1 for record in existing.values() if str(record.get("evaluation_status")).lower() == "evaluated")
    for key, record in list(existing.items()):
        if key in by_key:
            existing[key] = apply_evaluation(record, by_key[key])
    for source in snapshots:
        # A new prospective record is allowed only from the current pending
        # Forward.  If its target was already evaluated, do not backfill it.
        if (str(source.get("signal_date")) == target_date
                and str(source.get("evaluation_status", "pending")).lower() != "evaluated"
                and qualifies(source)):
            key = (target_date, machine_id(source.get("machine")))
            existing.setdefault(key, normalize_record(source, f"docs/wave_lab/data/forward/{target_date}.json"))
    records = sorted(existing.values(), key=lambda r: (str(r.get("signal_date")), machine_id(r.get("machine"))))
    summary = summarize(records)
    summary.update({
        "generated_at": datetime.now().isoformat(timespec="seconds"), "research_only": True,
        "prediction_use": False, "prospective_start_date": START_DATE,
        "signal_recalculation_used": False, "historical_backfill_used": False,
        "target_data_used_for_new_sample": False, "processed_signal_date": target_date,
        "evaluated_this_run": summary["evaluated_samples"] - evaluated_before,
        "new_pending": sum(1 for r in records if r.get("signal_date") == target_date and str(r.get("evaluation_status")).lower() != "evaluated"),
        "records": records,
    })
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with CSV_PATH.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader(); writer.writerows(records)
    JSON_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def report_section(summary: dict[str, Any]) -> str:
    lines = ["", "## Wave + Weak MA Prospective Observation", "",
             "_Research/validation only; prediction_use=false._", "",
             f"- evaluated: {summary['evaluated_samples']} / total: {summary['total_samples']}",
             f"- bullish: {summary['bullish_count']} / rate: {summary['bullish_rate']}",
             f"- processed signal date: {summary['processed_signal_date']}", "- signal별:"]
    for signal, values in summary["signal_stats"].items():
        lines.append(f"  - {signal}: {values['bullish_count']}/{values['samples']} ({values['bullish_rate']})")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--no-bootstrap", action="store_true")
    args = parser.parse_args()
    summary = update(args.date, bootstrap=not args.no_bootstrap)
    print(json.dumps({key: summary[key] for key in ("processed_signal_date", "total_samples", "evaluated_samples", "pending_samples")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
