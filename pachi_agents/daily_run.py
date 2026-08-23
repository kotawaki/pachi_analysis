"""Run the production Pachi Agents daily cycle.

This module is deliberately independent from the existing pachi_analyze
pipeline.  It evaluates the previous locked prediction, rebuilds only the
production experience visible as of ``base_date``, creates the next day's
prediction with the Phase 9.5 feedback, and exports static web data.
"""
from __future__ import annotations

import argparse
import json
from datetime import date as Date, timedelta
from pathlib import Path
from typing import Any

from .backtest import _build_agents
from .experience import ExperienceBuilder, ExperienceStore
from .experience_feedback import adjust_agent_candidates, adjust_god_weights
from .inputs import load_daily_ohlc_rows_for_date, normalize_date
from .pachikamisama import build_pachikamisama_agent, generate_pachikamisama_prediction
from .predictions import PredictionError, PredictionStore
from .results import (
    PredictionNotLocked,
    ResultAlreadyExists,
    ResultError,
    ResultNotFound,
    ResultStore,
    _actual_map,
    _selection,
    evaluate_prediction,
)
from .export_web import export_web
from .reflection import generate_reflection_for_date


class ReportAlignmentError(ValueError):
    """当日production報告のprediction/result対応が壊れている。"""


def _add_days(value: str, days: int) -> str:
    parsed = Date.fromisoformat(f"{value[:4]}-{value[4:6]}-{value[6:]}")
    return (parsed + timedelta(days=days)).strftime("%Y%m%d")


def _read_only_store(store_type: Any, directory: Path) -> Any:
    """Construct a store without creating directories during dry-run."""
    store = object.__new__(store_type)
    store.directory = directory
    return store


def _prediction_dates(directory: Path, through: str) -> list[str]:
    dates = []
    for path in sorted(directory.glob("prediction_*.json")):
        day = path.stem.removeprefix("prediction_")
        try:
            day = normalize_date(day)
        except ValueError:
            continue
        if day <= through:
            dates.append(day)
    return dates


def _load_production_memory(
    prediction_store: PredictionStore,
    result_store: ResultStore,
    through: str,
    minimum_sample: int,
) -> ExperienceBuilder:
    """Rebuild production memory using only dates through ``through``."""
    builder = ExperienceBuilder("production", minimum_sample)
    for day in _prediction_dates(prediction_store.directory, through):
        try:
            prediction = prediction_store.load(day)
        except PredictionError:
            continue
        builder.register_prediction(prediction)
        try:
            result = result_store.load(day)
        except ResultError:
            continue
        builder.add_result(result)
    return builder


def _selected_machines(prediction: dict[str, Any]) -> list[str]:
    agents = prediction.get("agents", {})
    god = agents.get("pachikamisama", {})
    values = [
        agents.get("pachio", {}).get("primary_machine"),
        agents.get("pachiko", {}).get("primary_machine"),
        god.get("honmei"), god.get("taikou"), god.get("ana"),
    ]
    machines = []
    for value in values:
        if isinstance(value, dict):
            value = value.get("machine")
        if value:
            machines.append(str(value))
    return machines


def _preview_result_status(prediction: dict[str, Any], ohlc_root: Path, day: str) -> str:
    try:
        rows = load_daily_ohlc_rows_for_date(ohlc_root, day)
    except (FileNotFoundError, OSError):
        rows = []
    if not rows:
        return "pending"
    actual, _manifest = _actual_map(rows)
    selected = _selected_machines(prediction)
    available = sum(
        machine in actual and actual[machine].get("status") == "available"
        for machine in selected
    )
    if not selected:
        return "pending"
    return "evaluated" if available == len(selected) else "incomplete"


def _prediction_summary(prediction: dict[str, Any] | None) -> dict[str, Any]:
    if not prediction:
        return {"status": "not_generated"}
    agents = prediction.get("agents", {})
    god = agents.get("pachikamisama", {})
    return {
        "prediction_date": prediction.get("prediction_date"),
        "status": prediction.get("status"),
        "pachio_primary": agents.get("pachio", {}).get("primary_machine"),
        "pachiko_primary": agents.get("pachiko", {}).get("primary_machine"),
        "pachikamisama_honmei": god.get("honmei"),
        "pachikamisama_taikou": god.get("taikou"),
        "pachikamisama_ana": god.get("ana"),
    }


def _validate_report_alignment(
    prediction: dict[str, Any],
    result: dict[str, Any],
    base_date: str,
) -> None:
    """当日報告に翌日prediction/resultが混ざらないことを保証する。"""
    expected = normalize_date(base_date)
    prediction_date = normalize_date(prediction.get("prediction_date"))
    result_date = normalize_date(result.get("prediction_date"))
    if prediction_date != expected or result_date != expected or prediction_date != result_date:
        raise ReportAlignmentError(
            "production報告のprediction/result日付が一致しません: "
            f"expected={expected}, prediction={prediction_date}, result={result_date}"
        )


def run_daily(
    root: str | Path,
    *,
    base_date: str,
    dry_run: bool = False,
    evaluate_only: bool = False,
    minimum_sample: int = 5,
) -> dict[str, Any]:
    root = Path(root)
    base = normalize_date(base_date)
    next_day = _add_days(base, 1)
    data_root = root / "pachi_agents" / "data"
    if dry_run:
        prediction_store = _read_only_store(PredictionStore, data_root / "predictions")
        result_store = _read_only_store(ResultStore, data_root / "results")
        experience_store = None
    else:
        prediction_store = PredictionStore(data_root / "predictions")
        result_store = ResultStore(data_root / "results")
        experience_store = ExperienceStore(data_root / "experience", "production")
    ohlc_root = root / "csv" / "daily_ohlc"

    report: dict[str, Any] = {
        "mode": "production",
        "base_date": base,
        "dry_run": dry_run,
        "evaluate_only": evaluate_only,
        "evaluation": {"prediction_date": base, "result_status": "not_applicable"},
        "experience": {"updated": False, "mode": "production"},
        "next_prediction": {"prediction_date": next_day, "cutoff_date": base},
        "export": {"planned": True},
    }

    try:
        current_prediction = prediction_store.load(base)
    except PredictionError as exc:
        report["evaluation"] = {"prediction_date": base, "status": "no_locked_prediction", "error": type(exc).__name__}
        current_prediction = None

    if current_prediction is not None and current_prediction.get("status") != "locked":
        report["evaluation"] = {
            "prediction_date": base,
            "status": "prediction_not_locked",
            "action": "skip",
        }
        current_prediction = None

    if current_prediction is not None:
        report["evaluation"]["prediction"] = _prediction_summary(current_prediction)
        report["evaluation"]["prediction_file"] = str(prediction_store.path_for(base))
        try:
            current_result = result_store.load(base)
            _validate_report_alignment(current_prediction, current_result, base)
            report["evaluation"].update({
                "status": current_result.get("status"),
                "action": "skip_existing_result",
                "result_prediction_date": current_result.get("prediction_date"),
                "result_file": str(result_store.path_for(base)),
            })
        except ResultNotFound:
            status = _preview_result_status(current_prediction, ohlc_root, base)
            report["evaluation"].update({
                "prediction_date": base,
                "status": status,
                "action": "evaluate" if status != "pending" else "wait",
            })
            if not dry_run and status != "pending":
                try:
                    evaluate_prediction(prediction_store, result_store, prediction_date=base, ohlc_root=ohlc_root)
                    current_result = result_store.load(base)
                    _validate_report_alignment(current_prediction, current_result, base)
                    report["evaluation"].update({
                        "status": current_result.get("status"),
                        "result_prediction_date": current_result.get("prediction_date"),
                        "result_file": str(result_store.path_for(base)),
                    })
                except ResultAlreadyExists:
                    report["evaluation"]["action"] = "skip_race_existing_result"
        except ResultError as exc:
            report["evaluation"].update({
                "prediction_date": base,
                "status": "result_error",
                "error": type(exc).__name__,
            })

    builder = _load_production_memory(prediction_store, result_store, base, minimum_sample)
    report["experience"]["evaluated_result_dates_before_next_prediction"] = list(builder.memory["evaluated_result_dates"])
    report["experience"]["updated"] = report["evaluation"].get("status") == "evaluated"

    # A missing prediction for the completed day is a legitimate gap: do not
    # backfill it, but allow production to resume when that day's formal OHLC
    # is available and the next prediction can be built as-of that day.
    base_data_available = bool(load_daily_ohlc_rows_for_date(ohlc_root, base))
    can_generate_next = (
        report["evaluation"].get("status") == "evaluated"
        or (current_prediction is None and base_data_available)
    )
    if evaluate_only:
        report["next_prediction"]["action"] = "skip_evaluate_only"
    elif not can_generate_next:
        report["next_prediction"]["action"] = "wait_for_evaluated_result"
    elif not prediction_store.path_for(next_day).exists():
        pachio, pachiko, manifest = _build_agents(root, base)
        pachio = adjust_agent_candidates(pachio, builder.memory, "pachio", minimum_sample=minimum_sample)
        pachiko = adjust_agent_candidates(pachiko, builder.memory, "pachiko", minimum_sample=minimum_sample)
        base_god = build_pachikamisama_agent(pachio, pachiko, top_n=5)
        weights, adjustment = adjust_god_weights(
            base_god.get("agent_weights", {}), pachio, pachiko, builder.memory,
            god=base_god, minimum_sample=minimum_sample,
        )
        report["next_prediction"]["input_manifest"] = manifest
        report["next_prediction"]["agents"] = {"pachio": pachio, "pachiko": pachiko, "pachikamisama": base_god}
        if not dry_run:
            generate_pachikamisama_prediction(
                prediction_store,
                prediction_date=next_day,
                cutoff_date=base,
                pachio=pachio,
                pachiko=pachiko,
                input_manifest=manifest,
                experience_agent_weights=weights,
                experience_adjustment=adjustment,
                top_n=5,
            )
        report["next_prediction"]["action"] = "would_generate" if dry_run else "generated_and_locked"
    elif can_generate_next:
        existing_next = prediction_store.load(next_day)
        report["next_prediction"]["action"] = "skip_existing_locked" if existing_next.get("status") == "locked" else "existing_prediction_error"
        report["next_prediction"]["summary"] = _prediction_summary(existing_next)

    if not dry_run:
        builder = _load_production_memory(prediction_store, result_store, base, minimum_sample)
        if report["evaluation"].get("status") == "evaluated":
            assert experience_store is not None
            experience_store.save(builder.finalize())
            reflection_path = generate_reflection_for_date(data_root, base, "production")
            report["reflection"] = {"generated": True, "path": str(reflection_path)}
        else:
            report["reflection"] = {"generated": False, "reason": "result_not_evaluated"}
        exported = export_web(data_root, root / "docs" / "pachi_agents" / "data", "production")
        report["export"] = {"planned": True, "history_count": len(exported["history"]), "output": str(root / "docs" / "pachi_agents" / "data")}
    else:
        report["export"] = {"planned": True, "output": str(root / "docs" / "pachi_agents" / "data"), "writes": False}

    if "agents" in report["next_prediction"]:
        report["next_prediction"]["summary"] = _prediction_summary(report["next_prediction"] | {"agents": report["next_prediction"]["agents"]})
        report["next_prediction"].pop("agents", None)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the production Pachi Agents daily cycle")
    parser.add_argument("--base-date", required=True, help="Completed actual date, YYYYMMDD")
    parser.add_argument("--root", default=".")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--evaluate-only", action="store_true", help="既存locked predictionの評価・reflection・experience・exportだけを行い、翌日predictionを生成しない")
    args = parser.parse_args()
    report = run_daily(args.root, base_date=args.base_date, dry_run=args.dry_run, evaluate_only=args.evaluate_only)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
