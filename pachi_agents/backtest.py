"""Phase 9 walk-forward backtest runner.

Inputs are read from the existing analysis data, while predictions, results,
experience, and summaries are written below a dedicated backtest directory.
The runner processes one calendar day at a time and never uses the target
day's OHLC until evaluation.
"""
from __future__ import annotations

import argparse
import json
from datetime import date as Date, datetime, timedelta
from pathlib import Path
from typing import Any

from .experience import ExperienceBuilder, ExperienceStore
from .experience_feedback import adjust_agent_candidates, adjust_god_weights
from .inputs import (
    assert_as_of,
    available_snapshot_dates,
    load_daily_ohlc_rows_for_date,
    load_pair_history_as_of,
    load_snapshot,
    normalize_date,
)
from .pachikamisama import build_pachikamisama_agent, generate_pachikamisama_prediction
from .pachiko import _daytime_hit_days, build_pachiko_agent
from .pachio import _manifest as pachio_manifest
from .pachio import build_pachio_agent
from .predictions import PredictionStore, make_manifest_entry
from .results import ResultStore, evaluate_prediction


class BacktestError(Exception):
    pass


def _ohlc_rows_as_of(root: Path, cutoff: str) -> list[dict[str, Any]]:
    """Read only dated OHLC directories at or before cutoff.

    The general Phase 1 bulk reader intentionally raises when it sees a
    future directory. Walk-forward needs to coexist with the full dataset,
    so this adapter filters the directory first and still applies assert_as_of
    before every read.
    """
    rows: list[dict[str, Any]] = []
    for directory in sorted(root.iterdir()) if root.exists() else []:
        if not directory.is_dir() or len(directory.name) != 8 or not directory.name.isdigit():
            continue
        try:
            day = normalize_date(directory.name)
        except ValueError:
            continue
        if day > cutoff:
            continue
        assert_as_of(day, cutoff)
        rows.extend(load_daily_ohlc_rows_for_date(root, day))
    return rows


def _dates(start: str, end: str) -> list[str]:
    first = Date.fromisoformat(f"{start[:4]}-{start[4:6]}-{start[6:]}")
    last = Date.fromisoformat(f"{end[:4]}-{end[4:6]}-{end[6:]}")
    if first > last:
        raise ValueError("start_date must be <= end_date")
    return [(first + timedelta(days=i)).strftime("%Y%m%d") for i in range((last - first).days + 1)]


def _previous(day: str) -> str:
    value = Date.fromisoformat(f"{day[:4]}-{day[4:6]}-{day[6:]}") - timedelta(days=1)
    return value.strftime("%Y%m%d")


def _pachiko_manifest(snapshot_root: Path, pair_history: Path, daytime_root: Path, ohlc_root: Path, cutoff: str) -> list[dict[str, Any]]:
    snapshot_dates = [day for day in available_snapshot_dates(snapshot_root) if day <= cutoff][-7:]
    manifest = [make_manifest_entry(snapshot_root / f"{day}_snapshot.json", kind="snapshot", data_date=day) for day in snapshot_dates]
    manifest.append(make_manifest_entry(pair_history, kind="propagation_history", data_date=cutoff))
    for path in sorted(daytime_root.glob("daytime_hits_*.json")):
        day = path.stem.removeprefix("daytime_hits_")
        if len(day) == 8 and day.isdigit() and day <= cutoff:
            manifest.append(make_manifest_entry(path, kind="daytime_hits", data_date=day))
    for directory in sorted(ohlc_root.iterdir()) if ohlc_root.exists() else []:
        if directory.is_dir() and len(directory.name) == 8 and directory.name.isdigit() and directory.name <= cutoff:
            for path in sorted(directory.glob("*_daily_ohlc.csv")):
                manifest.append(make_manifest_entry(path, kind="ohlc", data_date=directory.name))
    return manifest


def _build_agents(input_root: Path, cutoff: str) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    ohlc_root = input_root / "csv" / "daily_ohlc"
    snapshot_root = input_root / "csv" / "replay"
    pair_history = input_root / "pair_history.json"
    daytime_root = input_root / "data"
    ohlc_rows = _ohlc_rows_as_of(ohlc_root, cutoff)
    pachio = build_pachio_agent(ohlc_rows, top_n=5)
    snapshot_dates = [day for day in available_snapshot_dates(snapshot_root) if day <= cutoff][-7:]
    snapshots = [load_snapshot(snapshot_root, day, cutoff_date=cutoff) for day in snapshot_dates]
    pachiko = build_pachiko_agent(
        snapshots=snapshots,
        pair_history=load_pair_history_as_of(pair_history, cutoff),
        daytime_hit_days=_daytime_hit_days(daytime_root, cutoff),
        ohlc_rows=ohlc_rows,
        top_n=5,
    )
    manifest = pachio_manifest(ohlc_root, cutoff)
    manifest.extend(_pachiko_manifest(snapshot_root, pair_history, daytime_root, ohlc_root, cutoff))
    return pachio, pachiko, manifest


def _baseline(input_root: Path, day: str) -> dict[str, Any]:
    rows = []
    directory = input_root / "csv" / "daily_ohlc" / day
    for path in sorted(directory.glob("*_daily_ohlc.csv")) if directory.exists() else []:
        import csv
        with path.open(encoding="utf-8-sig", newline="") as handle:
            rows.extend(csv.DictReader(handle))
    available = 0
    success = 0
    for row in rows:
        try:
            opening = float(str(row.get("Open", "")).replace(",", "").replace("+", ""))
            close = float(str(row.get("Close", "")).replace(",", "").replace("+", ""))
        except (TypeError, ValueError):
            continue
        available += 1
        success += close > opening
    return {"available": available, "success": success, "success_rate": round(success / available, 4) if available else None}


def _experience_progress(builder: ExperienceBuilder) -> dict[str, Any]:
    """Small immutable-in-output snapshot for each walk-forward date."""
    return {
        "evaluated_result_dates": list(builder.memory["evaluated_result_dates"]),
        "agents": {
            name: {
                key: builder.memory["agents"][name]["summary"][key]
                for key in ("total_predictions", "evaluated_count", "success", "failure")
            }
            for name in ("pachio", "pachiko", "pachikamisama")
        },
    }


def _load_existing(
    builder: ExperienceBuilder,
    prediction_store: PredictionStore,
    result_store: ResultStore,
    day: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    try:
        prediction = prediction_store.load(day)
    except Exception:
        return None, None
    builder.register_prediction(prediction)
    try:
        result = result_store.load(day)
    except Exception:
        return prediction, None
    builder.add_result(result)
    return prediction, result


def run_walk_forward(
    input_root: str | Path,
    backtest_root: str | Path,
    *,
    start_date: str,
    end_date: str,
    dry_run: bool = False,
    minimum_sample: int = 5,
    use_experience_feedback: bool = False,
) -> dict[str, Any]:
    start = normalize_date(start_date)
    end = normalize_date(end_date)
    days = _dates(start, end)
    source = Path(input_root)
    target = Path(backtest_root)
    prediction_dir = target / "predictions"
    result_dir = target / "results"
    experience_root = target / "experience"
    prediction_store = PredictionStore(prediction_dir) if not dry_run else None
    result_store = ResultStore(result_dir) if not dry_run else None
    experience_store = ExperienceStore(experience_root, "backtest") if not dry_run else None
    builder = ExperienceBuilder("backtest", minimum_sample)
    records: list[dict[str, Any]] = []

    for day in days:
        cutoff = _previous(day)
        existing_prediction = None
        existing_result = None
        if not dry_run:
            existing_prediction, existing_result = _load_existing(builder, prediction_store, result_store, day)
        pachio, pachiko, manifest = _build_agents(source, cutoff)
        base_god = build_pachikamisama_agent(pachio, pachiko, top_n=5)
        god_weights = None
        god_adjustment = None
        if use_experience_feedback:
            pachio = adjust_agent_candidates(pachio, builder.memory, "pachio", minimum_sample=minimum_sample)
            pachiko = adjust_agent_candidates(pachiko, builder.memory, "pachiko", minimum_sample=minimum_sample)
            base_god = build_pachikamisama_agent(pachio, pachiko, top_n=5)
            god_weights, god_adjustment = adjust_god_weights(
                base_god.get("agent_weights", {}), pachio, pachiko, builder.memory, god=base_god, minimum_sample=minimum_sample
            )
        record: dict[str, Any] = {
            "prediction_date": day,
            "cutoff_date": cutoff,
            "pachio": {"primary_machine": pachio.get("primary_machine"), "confidence": pachio.get("confidence"), "reason_codes": pachio.get("reason_codes", [])},
            "pachiko": {"primary_machine": pachiko.get("primary_machine"), "confidence": pachiko.get("confidence"), "reason_codes": pachiko.get("reason_codes", [])},
            "baseline": _baseline(source, day),
            "status": "dry_run" if dry_run else "predicted",
            "experience_feedback": use_experience_feedback,
        }
        if not dry_run:
            if existing_prediction is None:
                generate_pachikamisama_prediction(
                    prediction_store,
                    prediction_date=day,
                    cutoff_date=cutoff,
                    pachio=pachio,
                    pachiko=pachiko,
                    input_manifest=manifest,
                    experience_agent_weights=god_weights,
                    experience_adjustment=god_adjustment,
                    top_n=5,
                )
            prediction = existing_prediction or prediction_store.load(day)
            if existing_prediction is None:
                builder.register_prediction(prediction)
            if existing_result is None and not result_store.path_for(day).exists():
                evaluate_prediction(prediction_store, result_store, prediction_date=day, ohlc_root=source / "csv" / "daily_ohlc")
            result = existing_result or result_store.load(day)
            if existing_result is None:
                builder.add_result(result)
            record["status"] = result.get("status", "unknown")
            record["result"] = result
            # Persisting is deferred until all dates are processed so that the
            # derived file is built from the complete chronological sequence.
        record["experience_after"] = _experience_progress(builder)
        records.append(record)

    memory = builder.finalize()
    summary = {
        "schema_version": 1,
        "mode": "backtest",
        "start_date": start,
        "end_date": end,
        "experience_feedback": use_experience_feedback,
        "generated_at": datetime.now().astimezone().isoformat(),
        "days": records,
        "experience": memory,
    }
    if not dry_run:
        experience_store.save(memory)
        summary_path = target / "summary.json"
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Pachi Agents walk-forward backtest")
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--input-root", default=".")
    parser.add_argument("--backtest-root", default="pachi_agents/data/backtest")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--experience-feedback", action="store_true")
    args = parser.parse_args()
    summary = run_walk_forward(args.input_root, args.backtest_root, start_date=args.start_date, end_date=args.end_date, dry_run=args.dry_run, use_experience_feedback=args.experience_feedback)
    print(json.dumps({"mode": summary["mode"], "start_date": summary["start_date"], "end_date": summary["end_date"], "days": len(summary["days"]), "dry_run": args.dry_run}, ensure_ascii=False))


if __name__ == "__main__":
    main()
