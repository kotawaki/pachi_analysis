"""Official Daily Runner v1.

This is intentionally a small orchestration layer.  The existing
``run_pscube_morning_pipeline.py`` remains the implementation of the daily
steps; this entry point supplies a repo-root/capture convention, records the
stage result, and performs the final source-to-web validation.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from wave_lab.universe import machines_for_signal_date

HOLIDAYS = {"20260827"}


def add_days(value: str, days: int) -> str:
    date = dt.datetime.strptime(value, "%Y%m%d").date()
    return (date + dt.timedelta(days=days)).strftime("%Y%m%d")


def validate_date(value: str) -> str:
    if len(value) != 8 or not value.isdigit():
        raise argparse.ArgumentTypeError("date must be YYYYMMDD")
    try:
        dt.datetime.strptime(value, "%Y%m%d")
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    if value in HOLIDAYS:
        raise argparse.ArgumentTypeError(f"{value} is protected holiday data")
    return value


def capture_root_for(date: str, override: Path | None) -> Path:
    return (override or (ROOT / "data" / "local_capture" / date / "morning")).resolve()


def daily_command(date: str, capture_root: Path) -> list[str]:
    return [
        sys.executable,
        str(ROOT / "tools" / "run_pscube_morning_pipeline.py"),
        "--date", date,
        "--capture-root", str(capture_root),
        "--pachi-agents-external",
        "--defer-wave-weak-ma",
    ]


def planned_stages(date: str) -> list[dict[str, Any]]:
    previous = add_days(date, -1)
    next_date = add_days(date, 1)
    return [
        {"name": "01_analyze", "command": ["analyze_pscube.py", "<capture-root>"]},
        {"name": "02_canonical_ohlc", "command": ["copy analyze OHLC to csv/daily_ohlc/<date>"]},
        {"name": "03_ohlc_web", "command": ["ohlc_chart.py"]},
        {"name": "04_daily_ingest", "command": ["daily_ingest.py", "--date", date, "--skip-wave-weak-ma"]},
        {"name": "05_pachi_agents", "command": ["python", "-m", "pachi_agents.daily_run", "--base-date", date]},
        {"name": "06_propagation", "command": ["propagation_lookup_html.py", "--daytime-date", date]},
        {"name": "07_cycle_daytime", "command": ["cycle_watch.py / cycle_after_hit_analysis.py / intraday_hit_regime_analysis.py"]},
        {"name": "08_combined_signal", "command": ["combined_signal_analysis.py", "--end", date]},
        {"name": "09_group_ranking", "command": ["group_ranking.py"]},
        {"name": "10_wave_forward", "command": ["forward_evaluate.py / forward_update.py", previous, date, next_date]},
        {"name": "11_signal_reliability", "command": ["export_signal_reliability.py"]},
        {"name": "12_state_snapshots", "command": ["export_state_snapshots.py"]},
        {"name": "13_group_flow", "command": ["analyze_group_flow.py", "--date", date]},
        {"name": "14_transition", "command": ["analyze_transition.py", "--date", date]},
        {"name": "15_transition_validation", "command": ["transition_validation.py"]},
        {"name": "16_ma_position_research", "command": ["track_ma_position.py", "--max-signal-date", date]},
        {"name": "17_tug_replay", "command": ["export_tug_replay.py", "--date", date, "g1..g9/all"]},
        {"name": "18_wave_weak_ma", "command": ["export_wave_weak_ma.py / export_wave_weak_ma_web.py", "--date", date]},
        {"name": "final_validation", "command": ["validate_web_outputs", date]},
    ]


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _embedded_json(path: Path, name: str) -> Any:
    text = path.read_text(encoding="utf-8", errors="ignore") if path.exists() else ""
    match = re.search(rf"const {name}\s*=\s*(\{{.*?\}});", text, re.S)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None


def _json_contains_date(value: Any, date: str) -> bool:
    iso = f"{date[:4]}-{date[4:6]}-{date[6:]}"
    if isinstance(value, str):
        return value == date or value == iso
    if isinstance(value, dict):
        return any(_json_contains_date(item, date) for item in value.values())
    if isinstance(value, list):
        return any(_json_contains_date(item, date) for item in value)
    return False


def validate_public_web_outputs(date: str) -> tuple[bool, dict[str, Any]]:
    """Validate the files actually read by the published 01-09 pages."""
    previous = _business_date(date, -1)
    next_date = _business_date(date, 1)
    required_files: list[str] = []
    checks: dict[str, Any] = {}
    warnings: list[str] = []

    def required(path: Path) -> bool:
        required_files.append(str(path.relative_to(ROOT)).replace("\\", "/"))
        return path.exists()

    print("========================================")
    print(" PUBLIC WEB OUTPUT CHECK")
    print(f" processing_date={date}")
    print("========================================")

    ohlc_path = ROOT / "docs/ohlc.html"
    ohlc = _embedded_json(ohlc_path, "ALL_DATA") if required(ohlc_path) else None
    ohlc_ok = bool(ohlc and _json_contains_date(ohlc, date))
    checks["01_ohlc_public"] = {"latest_date": date if ohlc_ok else None, "expected": date, "status": "OK" if ohlc_ok else "INCOMPLETE"}

    propagation_path = ROOT / "docs/propagation_lookup.html"
    propagation = _embedded_json(propagation_path, "DATA") if required(propagation_path) else None
    propagation_latest = propagation.get("meta", {}).get("to") if isinstance(propagation, dict) else None
    propagation_ok = propagation_latest == date
    checks["02_propagation_public"] = {"latest_date": propagation_latest, "expected": date, "status": "OK" if propagation_ok else "INCOMPLETE"}

    combined_path = ROOT / "docs/combined_signal_analysis.html"
    combined_text = combined_path.read_text(encoding="utf-8", errors="ignore") if required(combined_path) else ""
    combined_ok = date in combined_text
    checks["03_combined_public"] = {"latest_date": date if combined_ok else None, "expected": date, "status": "OK" if combined_ok else "INCOMPLETE"}

    groups_path = ROOT / "docs/groups.html"
    groups = _embedded_json(groups_path, "DATA") if required(groups_path) else None
    iso = f"{date[:4]}-{date[4:6]}-{date[6:]}"
    groups_ok = isinstance(groups, dict) and iso in groups.get("dates", [])
    checks["04_groups_public"] = {"latest_date": date if groups_ok else None, "expected": date, "status": "OK" if groups_ok else "INCOMPLETE"}

    cycle_path = ROOT / "docs/data/cycle_watch_config.json"
    cycle = load_json(cycle_path) if required(cycle_path) else {}
    cycle_latest = cycle.get("latest_data_date")
    cycle_ok = cycle_latest == date
    checks["05_cycle_public"] = {"latest_date": cycle_latest, "expected": date, "status": "OK" if cycle_ok else "INCOMPLETE"}

    latest_prediction_path = ROOT / "docs/pachi_agents/data/latest_prediction.json"
    pachi_history_path = ROOT / "docs/pachi_agents/data/history.json"
    pachi_experience_path = ROOT / "docs/pachi_agents/data/experience.json"
    latest_result_path = ROOT / "docs/pachi_agents/data/latest_result.json"
    latest_reflection_path = ROOT / "docs/pachi_agents/data/latest_reflection.json"
    for path in (latest_prediction_path, pachi_history_path, pachi_experience_path, latest_result_path, latest_reflection_path):
        required(path)
    latest_prediction = load_json(latest_prediction_path)
    pachi_history = json.loads(pachi_history_path.read_text(encoding="utf-8")) if pachi_history_path.exists() else []
    pachi_experience = load_json(pachi_experience_path)
    pachi_next_ok = (
        latest_prediction.get("prediction_date") == next_date
        and latest_prediction.get("cutoff_date") == date
        and latest_prediction.get("status") == "locked"
    )
    pachi_entry = next((row for row in pachi_history if row.get("prediction_date") == date), None) if isinstance(pachi_history, list) else None
    pachi_next_entry = next((row for row in pachi_history if row.get("prediction_date") == next_date), None) if isinstance(pachi_history, list) else None
    pachi_history_ok = bool(pachi_entry and pachi_entry.get("result") and pachi_entry.get("reflection") and pachi_next_entry)
    pachi_experience_ok = date in pachi_experience.get("processed_prediction_dates", []) and date in pachi_experience.get("evaluated_result_dates", [])
    latest_result = load_json(latest_result_path)
    latest_reflection = load_json(latest_reflection_path)
    latest_canonical_ok = all(
        payload is None or payload.get("prediction_date") == date
        for payload in (latest_result, latest_reflection)
    )
    pachi_ok = pachi_next_ok and pachi_history_ok and pachi_experience_ok and latest_canonical_ok
    checks["06_pachi_public"] = {
        "prediction_date": latest_prediction.get("prediction_date"),
        "cutoff_date": latest_prediction.get("cutoff_date"),
        "status": "OK" if pachi_ok else "INCOMPLETE",
    }

    forward_dir = ROOT / "docs/wave_lab/data/forward"
    previous_path = forward_dir / f"{previous}.json"
    current_path = forward_dir / f"{date}.json"
    history_path = forward_dir / "history.json"
    latest_path = forward_dir / "latest.json"
    reliability_path = forward_dir / "signal_reliability.json"
    for path in (previous_path, current_path, history_path, latest_path, reliability_path):
        required(path)
    previous_forward = load_json(previous_path)
    current_forward = load_json(current_path)
    forward_history = load_json(history_path)
    latest_forward = load_json(latest_path)
    rows = forward_history.get("rows", [])
    previous_history_ok = any(row.get("signal_date") == previous and row.get("target_date") == date and str(row.get("evaluation_status", "")).lower() == "evaluated" for row in rows)
    current_history_ok = any(row.get("signal_date") == date and row.get("target_date") == next_date and str(row.get("evaluation_status", "")).lower() == "pending" for row in rows)
    wave_ok = (
        previous_forward.get("signal_date") == previous
        and previous_forward.get("target_date") == date
        and str(previous_forward.get("evaluation_status", "")).lower() == "evaluated"
        and current_forward.get("signal_date") == date
        and current_forward.get("target_date") == next_date
        and str(current_forward.get("evaluation_status", "")).lower() == "pending"
        and latest_forward.get("signal_date") == date
        and latest_forward.get("target_date") == next_date
        and str(latest_forward.get("evaluation_status", "")).lower() == "pending"
        and previous_history_ok
        and current_history_ok
        and current_forward.get("future_data_used") is False
        and latest_forward.get("future_data_used") is False
    )
    checks["07_wave_public"] = {"previous": f"{previous}->{date}", "current": f"{date}->{next_date}", "status": "OK" if wave_ok else "INCOMPLETE"}

    tug_index_path = ROOT / "docs/wave_lab/tug_replay/data/index.json"
    tug_index = load_json(tug_index_path) if required(tug_index_path) else {}
    tug_rows = [row for row in tug_index.get("datasets", []) if row.get("date") == date]
    tug_groups = {row.get("group") for row in tug_rows}
    expected_groups = {*(f"g{i}" for i in range(1, 10)), "all"}
    tug_ok = tug_groups >= expected_groups
    for group in sorted(expected_groups):
        path = ROOT / "docs/wave_lab/tug_replay/data" / date / f"{group}.json"
        required(path)
        if not path.exists() or not load_json(path):
            tug_ok = False
    skipped = [row for row in tug_rows if row.get("skipped_machines")]
    if skipped:
        warnings.append("08 skipped_machines=" + ",".join(sorted({item.get("machine", "?") for row in skipped for item in row.get("skipped_machines", [])})))
    checks["08_tug_public"] = {"latest_date": date if tug_ok else None, "groups": sorted(tug_groups), "skipped_machines": skipped, "status": "OK" if tug_ok else "INCOMPLETE"}

    weak_html_path = ROOT / "docs/wave_weak_ma/index.html"
    weak_summary_path = ROOT / "wave_lab/cross_machine_analysis/tracking/wave_weak_ma_summary.json"
    weak_html = weak_html_path.read_text(encoding="utf-8", errors="ignore") if required(weak_html_path) else ""
    weak_summary = load_json(weak_summary_path) if required(weak_summary_path) else {}
    weak_ok = (
        weak_summary.get("processed_signal_date") == date
        and weak_summary.get("prediction_use") is False
        and date in weak_html
        and next_date in weak_html
    )
    checks["09_weak_ma_public"] = {"processed_signal_date": weak_summary.get("processed_signal_date"), "pending_target": next_date if next_date in weak_html else None, "status": "OK" if weak_ok else "INCOMPLETE"}

    for key, value in checks.items():
        if isinstance(value, dict):
            print(f"{key}: {value.get('status', 'INFO')}")
    print("PUBLIC_COMMIT_REQUIRED_FILES:")
    for path in required_files:
        print(path)
    ok = all(value.get("status") == "OK" for value in checks.values() if isinstance(value, dict) and "status" in value)
    print(f"PUBLIC_OUTPUT_STATUS={'ALL READY' if ok else 'INCOMPLETE'}")
    return ok, {"checks": checks, "required_files": required_files, "warnings": warnings}


def _business_date(value: str, days: int) -> str:
    candidate = dt.datetime.strptime(value, "%Y%m%d").date()
    step = 1 if days >= 0 else -1
    remaining = abs(days)
    while remaining:
        candidate += dt.timedelta(days=step)
        compact = candidate.strftime("%Y%m%d")
        if compact not in HOLIDAYS:
            remaining -= 1
    return candidate.strftime("%Y%m%d")


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _bool_value(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None or value == "":
        return None
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1"}:
            return True
        if lowered in {"false", "0"}:
            return False
    return bool(value)


def _load_ohlc(date: str) -> dict[str, dict[str, float]]:
    path = ROOT / "csv" / "daily_ohlc" / date / f"{date}_daily_ohlc.csv"
    if not path.exists():
        return {}
    result: dict[str, dict[str, float]] = {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            values = {key: _number(row.get(key)) for key in ("Open", "High", "Low", "Close")}
            if all(value is not None for value in values.values()):
                result[str(int(row.get("Machine", "0"))).zfill(3)] = values  # type: ignore[assignment]
    return result


def validate_wave_forward(date: str) -> tuple[bool, str]:
    """Validate the previous evaluated Forward and the current locked Forward."""
    previous = _business_date(date, -1)
    next_date = _business_date(date, 1)
    previous_path = ROOT / "docs/wave_lab/data/forward" / f"{previous}.json"
    current_path = ROOT / "docs/wave_lab/data/forward" / f"{date}.json"
    previous_forward = load_json(previous_path)
    current_forward = load_json(current_path)
    if not previous_forward or not current_forward:
        return False, "07 Forward JSON missing"
    if (previous_forward.get("signal_date"), previous_forward.get("target_date")) != (previous, date):
        return False, "07 previous Forward date mismatch"
    if str(previous_forward.get("evaluation_status", "")).lower() != "evaluated":
        return False, "07 previous Forward is not evaluated"
    if previous_forward.get("future_data_used") is not False:
        return False, "07 previous Forward future_data_used is not false"
    expected_source = f"csv/daily_ohlc/{date}/{date}_daily_ohlc.csv"
    if previous_forward.get("actual_source") != expected_source:
        return False, "07 previous Forward actual_source mismatch"
    ohlc = _load_ohlc(date)
    if not ohlc:
        return False, "07 canonical OHLC missing"
    previous_rows = {str(row.get("machine", "")).zfill(3): row for row in previous_forward.get("machine_signals", [])}
    if set(previous_rows) != set(machines_for_signal_date(previous)):
        return False, "07 previous machine universe mismatch"
    for machine, row in previous_rows.items():
        if str(row.get("evaluation_status", "")).lower() != "evaluated":
            return False, f"07 previous machine not evaluated: {machine}"
        actual = {key: _number(row.get(f"actual_{key.lower()}")) for key in ("Open", "High", "Low", "Close")}
        expected = ohlc.get(machine)
        if expected is None or any(actual[key] is None or actual[key] != expected[key] for key in actual):
            return False, f"07 previous actual OHLC mismatch: {machine}"
        actual_bullish = row.get("actual_bullish")
        if _bool_value(actual_bullish) is None or _bool_value(actual_bullish) != (expected["Close"] > expected["Open"]):
            return False, f"07 previous actual bullish mismatch: {machine}"
    history = load_json(ROOT / "docs/wave_lab/data/forward/history.json")
    history_row = next((row for row in history.get("rows", []) if row.get("signal_date") == previous and row.get("target_date") == date), None)
    if not history_row or str(history_row.get("evaluation_status", "")).lower() != "evaluated":
        return False, "07 previous evaluated record missing from history"
    index_path = ROOT / "docs/wave_lab/index.html"
    index_text = index_path.read_text(encoding="utf-8", errors="ignore") if index_path.exists() else ""
    if "./data/forward/${date}.json" not in index_text:
        return False, "07 previous Forward is not Web-addressable"
    if (current_forward.get("signal_date"), current_forward.get("target_date")) != (date, next_date):
        return False, "07 current Forward date mismatch"
    if current_forward.get("future_data_used") is not False or current_forward.get("max_input_date") > date:
        return False, "07 current Forward future-data guard mismatch"
    current_rows = {str(row.get("machine", "")).zfill(3): row for row in current_forward.get("machine_signals", [])}
    if set(current_rows) != set(machines_for_signal_date(date)):
        return False, "07 current machine universe mismatch"
    for machine, row in current_rows.items():
        if row.get("machine_status") == "insufficient_history" and str(row.get("evaluation_status", "")).lower() != "not_ready":
            return False, f"07 insufficient-history status mismatch: {machine}"
    return True, "07 previous evaluation and current Forward verified"


def weak_ma_needs_refresh(date: str) -> bool:
    """Return true only when 09 is stale or its current sample is absent."""
    summary = load_json(ROOT / "wave_lab/cross_machine_analysis/tracking/wave_weak_ma_summary.json")
    records = summary.get("records", [])
    if summary.get("processed_signal_date") != date:
        return True
    current = [row for row in records if str(row.get("signal_date")) == date]
    if not current:
        return True
    for row in records:
        target = str(row.get("target_date", ""))
        if not target or target > date:
            continue
        forward = load_json(ROOT / "docs/wave_lab/data/forward" / f"{row.get('signal_date')}.json")
        machine_rows = {
            str(item.get("machine", "")).zfill(3): item
            for item in forward.get("machine_signals", [])
        }
        source = machine_rows.get(str(row.get("machine", "")).zfill(3), {})
        if str(source.get("evaluation_status", "")).lower() == "evaluated" and str(row.get("evaluation_status", "")).lower() != "evaluated":
            return True
    return False


def validate_weak_ma(date: str) -> tuple[bool, str]:
    summary_path = ROOT / "wave_lab/cross_machine_analysis/tracking/wave_weak_ma_summary.json"
    csv_path = ROOT / "wave_lab/cross_machine_analysis/tracking/wave_weak_ma_prospective.csv"
    html_path = ROOT / "docs/wave_weak_ma/index.html"
    if not summary_path.exists() or not csv_path.exists() or not html_path.exists():
        return False, "09 artifact missing"
    summary = load_json(summary_path)
    html = html_path.read_text(encoding="utf-8", errors="ignore")
    if summary.get("processed_signal_date") != date or summary.get("prediction_use") is not False:
        return False, "09 summary date/prediction flag mismatch"
    import csv
    with csv_path.open(encoding="utf-8", newline="") as handle:
        records = list(csv.DictReader(handle))
    for row in records:
        signal = str(row.get("signal_date"))
        target = str(row.get("target_date"))
        if target <= date:
            forward = load_json(ROOT / "docs/wave_lab/data/forward" / f"{signal}.json")
            source = next((item for item in forward.get("machine_signals", []) if str(item.get("machine", "")).zfill(3) == str(row.get("machine", "")).zfill(3)), {})
            if str(source.get("evaluation_status", "")).lower() == "evaluated":
                if str(row.get("evaluation_status", "")).lower() != "evaluated":
                    return False, f"09 stale pending: {signal}/{row.get('machine')}"
                for field in ("actual_open", "actual_high", "actual_low", "actual_close", "actual_bullish"):
                    if row.get(field, "") in ("", None):
                        return False, f"09 missing actual: {signal}/{row.get('machine')}/{field}"
    current_pending = [row for row in records if str(row.get("signal_date")) == date and str(row.get("evaluation_status")).lower() != "evaluated"]
    if current_pending and date not in html:
        return False, "09 current pending sample absent from Web artifact"
    if not records or str(records[-1].get("machine")) not in html:
        return False, "09 concrete record absent from Web artifact"
    return True, "09 evaluated actuals and current pending sample verified"


def validate_pachi_history(date: str) -> tuple[bool, str]:
    prediction = load_json(ROOT / "pachi_agents/data/predictions" / f"prediction_{add_days(date, 1)}.json")
    web_prediction = load_json(ROOT / "docs/pachi_agents/data/latest_prediction.json")
    result = load_json(ROOT / "pachi_agents/data/results" / f"result_{date}.json")
    reflection = load_json(ROOT / "pachi_agents/data/reflection" / f"reflection_{date}.json")
    history_path = ROOT / "docs/pachi_agents/data/history.json"
    history = json.loads(history_path.read_text(encoding="utf-8")) if history_path.exists() else []
    history_entry = next((item for item in history if item.get("prediction_date") == date), None) if isinstance(history, list) else None
    expected = (date, add_days(date, 1))
    if not prediction or (prediction.get("cutoff_date"), prediction.get("prediction_date")) != expected:
        return False, "06 source prediction date/cutoff mismatch"
    if (web_prediction.get("cutoff_date"), web_prediction.get("prediction_date")) != expected:
        return False, "06 Web prediction date/cutoff mismatch"
    if not result or result.get("prediction_date") != date or not history_entry:
        return False, "06 result record mismatch"
    if not reflection or reflection.get("prediction_date") != date or not history_entry.get("reflection"):
        return False, "06 reflection record mismatch"
    if not prediction.get("agents") or not web_prediction.get("agents"):
        return False, "06 selected prediction agents missing"
    return True, "06 prediction/result/reflection source and Web dates verified"


def validate_tug_replay(date: str) -> tuple[bool, str]:
    path = ROOT / "docs/wave_lab/tug_replay/data" / date / "g1.json"
    source = ROOT / "csv/daily_ohlc" / date / f"{date}_daily_ohlc.csv"
    payload = load_json(path)
    if payload.get("date") != date or not payload.get("validation", {}).get("final_values"):
        return False, "08 g1 dataset/final validation missing"
    closes = {}
    import csv
    with source.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            closes[str(int(row.get("Machine", "0"))).zfill(3)] = int(float(row.get("Close", 0)))
    checked = payload["validation"]["final_values"]
    for row in checked:
        machine = str(int(row.get("machine", "0"))).zfill(3)
        if machine not in closes or int(row.get("svg_final")) != closes[machine]:
            return False, f"08 final mismatch: {machine}"
    return True, f"08 g1 final values verified ({len(checked)} machines)"


def append_wave_weak_ma_report(date: str) -> None:
    report_path = ROOT / "reports" / f"{date}_report.md"
    if not report_path.exists():
        return
    text = report_path.read_text(encoding="utf-8")
    if "## Wave + Weak MA Prospective Observation" in text:
        return
    from wave_lab.cross_machine_analysis.export_wave_weak_ma import report_section
    summary = load_json(ROOT / "wave_lab/cross_machine_analysis/tracking/wave_weak_ma_summary.json")
    report_path.write_text(text + report_section(summary), encoding="utf-8")


def elapsed_for_stage(name: str, child_elapsed: dict[str, Any]) -> float:
    """Map the existing runner's concrete timers to v1 logical stages."""
    aliases = {
        "01_analyze": ("analyze",),
        "02_canonical_ohlc": (),
        "03_ohlc_web": ("ohlc_chart",),
        "04_daily_ingest": ("daily_ingest",),
        "05_pachi_agents": (),
        "06_propagation": ("propagation_lookup",),
        "07_cycle_daytime": ("cyclewatch_page", "cyclewatch_top", "cycle_after_hit", "intraday_hit_regime"),
        "08_combined_signal": ("combined_signal",),
        "09_group_ranking": ("group_ranking",),
        "10_wave_forward": ("07_wave_forward_evaluate", "07_wave_forward_lock"),
        "11_signal_reliability": ("07_signal_reliability",),
        "12_state_snapshots": ("07_state_snapshots",),
        "13_group_flow": ("group_flow",),
        "14_transition": ("transition",),
        "15_transition_validation": ("transition_validation",),
        "16_ma_position_research": ("ma_position_research",),
        "17_tug_replay": tuple([f"08_tug_replay_g{i}" for i in range(1, 10)] + ["08_tug_replay_all"]),
        "18_wave_weak_ma": (),
        "final_validation": (),
    }
    return round(sum(float(child_elapsed.get(key, 0) or 0) for key in aliases.get(name, ())), 3)


def run_command(command: list[str]) -> tuple[int, str, float]:
    started = time.perf_counter()
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
    output = "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part)
    return result.returncode, output, time.perf_counter() - started


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the official repo-root Daily Runner v1")
    parser.add_argument("--date", required=True, type=validate_date)
    parser.add_argument("--capture-root", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    capture_root = capture_root_for(args.date, args.capture_root)
    command = daily_command(args.date, capture_root)
    if args.dry_run:
        print(f"repo_root={ROOT}")
        print(f"capture_root={capture_root}")
        print("command=" + " ".join(command))
        for stage in planned_stages(args.date):
            print(f"plan[{stage['name']}]=" + " ".join(stage["command"]))
        return 0
    if not capture_root.exists():
        print(f"ERROR capture root not found: {capture_root}", file=sys.stderr)
        return 2

    started = time.perf_counter()
    stages = planned_stages(args.date)
    warnings: list[str] = []
    public_detail: dict[str, Any] = {}
    code, output, elapsed = run_command(command)
    child_summary = load_json(capture_root / "pipeline_summary.json")
    child_elapsed = child_summary.get("elapsed_seconds", {})
    if code != 0:
        for stage in stages:
            stage.update(status="ERROR", elapsed_seconds=elapsed_for_stage(stage["name"], child_elapsed), error=f"delegated runner failed: exit={code}")
        stages[-1].update(status="ERROR", elapsed_seconds=round(elapsed, 3), error=output[-2000:])
        daily_complete = False
    else:
        for stage in stages:
            stage.update(status="OK", elapsed_seconds=elapsed_for_stage(stage["name"], child_elapsed), error="")
        pachi_report = child_summary.get("pachi_agents") or {}
        pachi_failed = str(pachi_report.get("status", "")).lower() == "error"
        stages[4].update(
            status="ERROR" if pachi_failed else "OK",
            elapsed_seconds=round(float(child_elapsed.get("pachi_agents", 0) or 0), 3),
            command=["python", "-m", "pachi_agents.daily_run", "--base-date", args.date],
            error=str(pachi_report.get("error", "")),
        )
        rc, refresh_output, refresh_elapsed = run_command([sys.executable, "wave_lab/cross_machine_analysis/export_wave_weak_ma.py", "--date", args.date])
        stages[-2].update(status="OK" if rc == 0 else "ERROR", elapsed_seconds=round(refresh_elapsed, 3), command=["export_wave_weak_ma.py", "--date", args.date], error=refresh_output[-2000:] if rc else "")
        if rc == 0:
            rc, refresh_output, refresh_elapsed = run_command([sys.executable, "wave_lab/cross_machine_analysis/export_wave_weak_ma_web.py"])
            if rc != 0:
                stages[-2].update(status="ERROR", error=refresh_output[-2000:])
            else:
                append_wave_weak_ma_report(args.date)
        checks = {}
        try:
            from run_pscube_morning_pipeline import validate_web_outputs
            checks = validate_web_outputs(args.date)
        except Exception as exc:
            checks = {"runner_validation": False, "error": str(exc)}
        wave_ok, wave_message = validate_wave_forward(args.date)
        checks["07_wave_lab"] = bool(checks.get("07_wave_lab")) and wave_ok
        checks["07_detail"] = wave_message
        weak_ok, weak_message = validate_weak_ma(args.date)
        checks["09_wave_weak_ma"] = bool(checks.get("09_wave_weak_ma")) and weak_ok
        checks["09_detail"] = weak_message
        pachi_ok, pachi_message = validate_pachi_history(args.date)
        tug_ok, tug_message = validate_tug_replay(args.date)
        checks["06_pachi_agents"] = bool(checks.get("06_pachi_agents")) and pachi_ok
        checks["06_detail"] = pachi_message
        checks["08_tug_replay"] = bool(checks.get("08_tug_replay")) and tug_ok
        checks["08_detail"] = tug_message
        check_values = [value for key, value in checks.items() if key not in {"06_detail", "07_detail", "08_detail", "09_detail", "error"}]
        public_ok, public_detail = validate_public_web_outputs(args.date)
        checks["public_output_status"] = "ALL READY" if public_ok else "INCOMPLETE"
        checks["public_output_detail"] = public_detail
        warnings = list(public_detail.get("warnings", []))
        daily_complete = not pachi_failed and all(check_values) and public_ok
        failure_messages = [message for message in (wave_message, pachi_message, tug_message, weak_message) if message]
        if not public_ok:
            failure_messages.append("PUBLIC_OUTPUT_INCOMPLETE")
        stages[-1].update(status="OK" if daily_complete else "ERROR", elapsed_seconds=round(time.perf_counter() - started, 3), error="" if daily_complete else "; ".join(failure_messages))

    report = {
        "processing_date": args.date,
        "repo_root": str(ROOT),
        "capture_root": str(capture_root),
        "stages": stages,
        "web_validation": checks if code == 0 else {},
        "public_output_status": checks.get("public_output_status", "NOT RUN") if code == 0 else "NOT RUN",
        "warnings": warnings,
        "errors": [] if code == 0 else [output[-2000:]],
        "daily_status": "DAILY COMPLETE" if daily_complete else "DAILY INCOMPLETE",
        "total_elapsed_seconds": round(time.perf_counter() - started, 3),
    }
    report_path = ROOT / "reports" / f"{args.date}_daily_runner.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if daily_complete else 1


if __name__ == "__main__":
    raise SystemExit(main())
