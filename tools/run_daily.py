"""Official Daily Runner v1.

This is intentionally a small orchestration layer.  The existing
``run_pscube_morning_pipeline.py`` remains the implementation of the daily
steps; this entry point supplies a repo-root/capture convention, records the
stage result, and performs the final source-to-web validation.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
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
        weak_ok, weak_message = validate_weak_ma(args.date)
        checks["09_wave_weak_ma"] = bool(checks.get("09_wave_weak_ma")) and weak_ok
        checks["09_detail"] = weak_message
        pachi_ok, pachi_message = validate_pachi_history(args.date)
        tug_ok, tug_message = validate_tug_replay(args.date)
        checks["06_pachi_agents"] = bool(checks.get("06_pachi_agents")) and pachi_ok
        checks["06_detail"] = pachi_message
        checks["08_tug_replay"] = bool(checks.get("08_tug_replay")) and tug_ok
        checks["08_detail"] = tug_message
        check_values = [value for key, value in checks.items() if key not in {"06_detail", "08_detail", "09_detail", "error"}]
        daily_complete = not pachi_failed and all(check_values)
        stages[-1].update(status="OK" if daily_complete else "ERROR", elapsed_seconds=round(time.perf_counter() - started, 3), error="" if daily_complete else "; ".join(message for message in (pachi_message, tug_message, weak_message) if message))

    report = {
        "processing_date": args.date,
        "repo_root": str(ROOT),
        "capture_root": str(capture_root),
        "stages": stages,
        "web_validation": checks if code == 0 else {},
        "warnings": [],
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
