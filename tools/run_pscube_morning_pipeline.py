from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STEP_TIMES: dict[str, float] = {}


class PipelineError(RuntimeError):
    def __init__(self, step: str, command: list[str], result: subprocess.CompletedProcess[str]):
        super().__init__(step)
        self.step = step
        self.command = command
        self.result = result


def ymd_add(value: str, days: int) -> str:
    parsed = dt.datetime.strptime(value, "%Y%m%d").date()
    return (parsed + dt.timedelta(days=days)).strftime("%Y%m%d")


def run_step(step: str, args: list[str]) -> str:
    started = time.perf_counter()
    command = [sys.executable, *args]
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        STEP_TIMES[step] = time.perf_counter() - started
        raise PipelineError(step, command, result)
    STEP_TIMES[step] = time.perf_counter() - started
    print(f"elapsed[{step}]={STEP_TIMES[step]:.3f}s")
    return "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part)


def validate_web_outputs(date: str) -> dict[str, bool]:
    """Lightweight source-to-web checks for the nine published dashboard cards."""
    iso = f"{date[:4]}-{date[4:6]}-{date[6:]}"
    def text(path: Path) -> str:
        return path.read_text(encoding="utf-8", errors="ignore") if path.exists() else ""

    def embedded(path: Path, name: str) -> dict:
        match = re.search(rf"const {name}\s*=\s*(\{{.*?\}});", text(path), re.S)
        return json.loads(match.group(1)) if match else {}

    def source_ohlc() -> list[dict[str, str]]:
        return csv_rows(ROOT / "csv" / "daily_ohlc" / date / f"{date}_daily_ohlc.csv")

    ohlc_rows = source_ohlc() if (ROOT / "csv" / "daily_ohlc" / date / f"{date}_daily_ohlc.csv").exists() else []
    ohlc_web = embedded(ROOT / "docs" / "ohlc.html", "ALL_DATA")
    web_machine = ohlc_web.get("r39_77", {}).get("machines", {}).get("39", [])
    web_ohlc = next((row for row in web_machine if str(row.get("time", "")).replace("-", "") == date), None)
    previous_iso = f"{date[:4]}-{date[4:6]}-{int(date[6:]) - 1:02d}"
    web_previous = next((row for row in web_machine if row.get("time") == previous_iso), None)
    source_0039 = next((row for row in ohlc_rows if str(int(row.get("Machine", "0"))).zfill(3) == "039"), None)
    expected_chart_close = None
    if source_0039 and web_previous:
        expected_chart_close = int(web_previous["close"]) + int(source_0039["Close"]) - int(source_0039["Open"])

    pair_web = embedded(ROOT / "docs" / "propagation_lookup.html", "DATA")
    pair_key, pair_value = next(iter(pair_web.get("pairs", {}).items()), (None, [None]))
    pair_web_value = pair_value[0] if pair_value else None
    pair_source = json.loads((ROOT / "pair_history.json").read_text(encoding="utf-8")) if (ROOT / "pair_history.json").exists() else {}

    combined = text(ROOT / "docs" / "combined_signal_analysis.html")
    combined_report = text(ROOT / "reports" / f"combined_signal_analysis_20260613_{date}.md")
    groups_web = embedded(ROOT / "docs" / "groups.html", "DATA")
    group_index = groups_web.get("dates", []).index(iso) if iso in groups_web.get("dates", []) else -1
    group_value = groups_web.get("z", {}).get("1", [None])[group_index] if group_index >= 0 else None

    cycle_source_path = ROOT / "data" / "cycle_watch_config.json"
    cycle_docs_path = ROOT / "docs" / "data" / "cycle_watch_config.json"
    cycle_source = json.loads(cycle_source_path.read_text(encoding="utf-8")) if cycle_source_path.exists() else {}
    cycle_docs = json.loads(cycle_docs_path.read_text(encoding="utf-8")) if cycle_docs_path.exists() else {}

    pachi_path = ROOT / "docs" / "pachi_agents" / "data" / "latest_prediction.json"
    pachi_text = pachi_path.read_text(encoding="utf-8") if pachi_path.exists() else ""
    pachi_payload = json.loads(pachi_text or "null")
    pachi_source_path = ROOT / "pachi_agents" / "data" / "predictions" / f"prediction_{ymd_add(date, 1)}.json"
    pachi_source = json.loads(pachi_source_path.read_text(encoding="utf-8")) if pachi_source_path.exists() else {}
    forward_path = ROOT / "docs" / "wave_lab" / "data" / "forward" / f"{date}.json"
    forward_payload = json.loads(forward_path.read_text(encoding="utf-8")) if forward_path.exists() else {}
    latest_forward_path = ROOT / "docs" / "wave_lab" / "data" / "forward" / "latest.json"
    latest_forward = json.loads(latest_forward_path.read_text(encoding="utf-8")) if latest_forward_path.exists() else {}
    tug_path = ROOT / "docs" / "wave_lab" / "tug_replay" / "data" / date / "g1.json"
    tug_payload = json.loads(tug_path.read_text(encoding="utf-8")) if tug_path.exists() else {}
    weak_summary_path = ROOT / "wave_lab" / "cross_machine_analysis" / "tracking" / "wave_weak_ma_summary.json"
    weak_summary = json.loads(weak_summary_path.read_text(encoding="utf-8")) if weak_summary_path.exists() else {}
    weak_html = text(ROOT / "docs" / "wave_weak_ma" / "index.html")
    checks = {
        "01_ohlc": bool(source_0039 and web_ohlc and expected_chart_close == web_ohlc.get("close")),
        "02_propagation": bool(pair_web_value and date in pair_source.get("meta", {}).get("ingested_dates", []) and pair_web.get("meta", {}).get("to") == date and pair_web_value.get("count") is not None and pair_web_value.get("lift") is not None),
        "03_combined": date in combined and date in combined_report and ("prediction unavailable" in combined.lower() or "prediction" in combined.lower()),
        "04_groups": group_index >= 0 and group_value is not None,
        "05_cycle": cycle_source.get("latest_data_date") == date and cycle_docs.get("latest_data_date") == date and cycle_source.get("machines", {}).get("046", {}).get("periods") == cycle_docs.get("machines", {}).get("046", {}).get("periods"),
        "06_pachi_agents": bool(pachi_payload and pachi_source) and pachi_payload.get("prediction_date") == pachi_source.get("prediction_date") and pachi_payload.get("cutoff_date") == date and "046" in pachi_text,
        "07_wave_lab": bool(forward_payload and latest_forward) and latest_forward.get("signal_date") == date and latest_forward.get("target_date") == forward_payload.get("target_date") and latest_forward.get("machine_counts") == forward_payload.get("machine_counts"),
        "08_tug_replay": bool(tug_payload) and tug_payload.get("date") == date and str(tug_payload.get("machines", [{}])[0].get("machine", "")) in text(tug_path),
        "09_wave_weak_ma": bool(weak_summary and weak_html) and weak_summary.get("processed_signal_date") == date and weak_summary.get("prediction_use") is False and date in weak_html,
    }
    for name, ok in checks.items():
        print(f"{name}={'OK' if ok else 'NG'}")
    return checks


def execution_plan(date: str, previous_date: str, next_date: str) -> list[tuple[str, list[str]]]:
    """Return the existing date-scoped post-daily commands in runner order."""
    return [
        ("07_wave_forward_web", ["wave_lab/cross_machine_analysis/export_forward_web.py"]),
        ("07_signal_reliability", ["wave_lab/cross_machine_analysis/export_signal_reliability.py"]),
        ("07_state_snapshots", ["wave_lab/cross_machine_analysis/export_state_snapshots.py"]),
        ("group_flow", ["wave_lab/group_flow/analyze_group_flow.py", "--date", date]),
        ("transition", ["wave_lab/group_flow/analyze_transition.py", "--date", date, "--previous-date", previous_date]),
        ("transition_validation", ["wave_lab/group_flow/transition_validation.py"]),
        ("ma_position_research", ["wave_lab/ma_position_research/track_ma_position.py", "--max-signal-date", date]),
        ("08_tug_replay", ["wave_lab/tug_replay/export_tug_replay.py", "--date", date, "--group", "<g1..g9>", "--state-date", previous_date]),
        ("08_tug_replay_all", ["wave_lab/tug_replay/export_tug_replay.py", "--date", date, "--group", "all", "--state-date", previous_date]),
        ("09_wave_weak_ma", ["daily_ingest.py", "--date", date, "(already runs in daily_ingest)"]),
        ("final_validation", ["validate_web_outputs", date]),
    ]


def wave_forward_plan(date: str, previous_date: str, next_date: str) -> dict[str, object]:
    """Inspect Forward state without changing any locked or evaluated file."""
    forward_dir = ROOT / "docs" / "wave_lab" / "data" / "forward"
    previous_path = forward_dir / f"{previous_date}.json"
    current_path = forward_dir / f"{date}.json"
    next_path = forward_dir / f"{next_date}.json"
    previous = json.loads(previous_path.read_text(encoding="utf-8")) if previous_path.exists() else None
    current = json.loads(current_path.read_text(encoding="utf-8")) if current_path.exists() else None
    previous_ok = bool(previous and previous.get("signal_date") == previous_date and previous.get("target_date") == date)
    canonical_ok = (ROOT / "csv" / "daily_ohlc" / date / f"{date}_daily_ohlc.csv").exists()
    evaluation = "SKIP already evaluated" if previous and str(previous.get("evaluation_status", "")).lower() == "evaluated" else "EVALUATE pending Forward"
    lock = "SKIP already locked" if current_path.exists() else "LOCK current-date Forward"
    return {
        "previous_forward": str(previous_path),
        "previous_forward_valid": previous_ok,
        "canonical_ohlc_exists": canonical_ok,
        "evaluation": evaluation,
        "lock": lock,
        "overwrite": False,
        "evaluation_script": "wave_lab/cross_machine_analysis/forward_evaluate.py",
        "lock_script": "wave_lab/cross_machine_analysis/forward_update.py / existing Forward LOCK step",
        "evaluation_needed": bool(previous_ok and previous and str(previous.get("evaluation_status", "")).lower() == "pending" and canonical_ok),
        "lock_needed": bool(current is None),
    }


def csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def prediction_summary(output: str) -> dict[str, str | int]:
    match = re.search(
        r"actual=(\d+) candidates=(\d+/\d+) cycle=(\d+/\d+) all=(\d+/\d+)",
        output,
    )
    if not match:
        return {"raw": output.splitlines()[-1] if output else ""}
    return {
        "date": match.group(1),
        "candidates": match.group(2),
        "cycle": match.group(3),
        "all": match.group(4),
    }


def propagation_summary(daytime_date: str) -> dict[str, object]:
    path = ROOT / "docs" / "propagation_lookup.html"
    text = path.read_text(encoding="utf-8")
    hit_match = re.search(r"const DAILY_HITS = new Set\(\[([^\]]*)\]\)", text)
    hits = re.findall(r"\d+", hit_match.group(1)) if hit_match else []
    return {
        "daytime_date": daytime_date,
        "acquired": f"日中周期hit {daytime_date}（未取得）" not in text,
        "hit_count": len(hits),
    }


def pair_history_summary() -> dict[str, int]:
    payload = json.loads((ROOT / "pair_history.json").read_text(encoding="utf-8"))
    return {
        "days": len(payload.get("meta", {}).get("ingested_dates", [])),
        "pairs": len(payload.get("pairs", {})),
    }


def write_summary(
    capture_root: Path,
    summary: dict[str, object],
    filename: str = "pipeline_summary.json",
) -> None:
    text = json.dumps(summary, ensure_ascii=False, separators=(",", ":"))
    (capture_root / filename).write_text(text + "\n", encoding="utf-8")
    print(text)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run P'sCUBE morning steps 2-10 with compact output."
    )
    parser.add_argument("--date", required=True, help="Actual date, YYYYMMDD")
    parser.add_argument("--capture-root", type=Path, required=True)
    parser.add_argument("--daytime-date", help="Default: date + 1 day")
    parser.add_argument("--combined-start", default="20260613")
    parser.add_argument(
        "--pachi-agents",
        action="store_true",
        help="Run the production Pachi Agents step after the existing analysis steps.",
    )
    parser.add_argument(
        "--pachi-agents-dry-run",
        action="store_true",
        help="Plan the Pachi Agents step without writing Pachi Agents data.",
    )
    parser.add_argument(
        "--skip-pachi-agents", action="store_true",
        help="Explicitly skip the default Pachi Agents step.",
    )
    parser.add_argument(
        "--allow-missing-html",
        action="store_true",
        help="Run chart-only analysis for captures where HTML was intentionally omitted.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print the fixed execution plan without running or writing data.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    pipeline_started = time.perf_counter()
    target_date = args.date
    next_date = args.daytime_date or ymd_add(target_date, 1)
    previous_date = ymd_add(target_date, -1)
    capture_root = args.capture_root.resolve()

    if not capture_root.exists():
        raise SystemExit(f"capture root not found: {capture_root}")

    if args.dry_run:
        wave = wave_forward_plan(target_date, previous_date, next_date)
        print(f"wave_forward[previous]={wave['previous_forward']} valid={wave['previous_forward_valid']}")
        print(f"wave_forward[canonical]={wave['canonical_ohlc_exists']}")
        print(f"wave_forward[evaluation]={wave['evaluation']}")
        print(f"wave_forward[lock]={wave['lock']}")
        print("wave_forward[overwrite]=false")
        for step, command in execution_plan(target_date, previous_date, next_date):
            print(f"plan[{step}]={' '.join(command)}")
        return 0

    try:
        analyze_args = ["analyze_pscube.py", str(capture_root), "--overlay"]
        if args.allow_missing_html:
            analyze_args.append("--allow-missing-html")
        run_step("analyze", analyze_args)

        analyze_dir = ROOT / "csv" / "pscube_analyze" / target_date
        event_source = analyze_dir / f"{target_date}_analyze.csv"
        ohlc_source = analyze_dir / f"{target_date}_daily_ohlc.csv"
        if not event_source.exists() or not ohlc_source.exists():
            raise FileNotFoundError(f"analyze output missing: {analyze_dir}")

        daily_dir = ROOT / "csv" / "daily_ohlc" / target_date
        daily_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ohlc_source, daily_dir / ohlc_source.name)

        analyze_target = ROOT / "csv" / "analyze" / target_date
        analyze_target.mkdir(parents=True, exist_ok=True)
        shutil.copy2(event_source, analyze_target / event_source.name)

        run_step("ohlc_chart", ["ohlc_chart.py"])

        legacy_prediction_status: dict[str, object] = {"status": "not_run"}
        prediction_output = ""
        locked_prediction = ROOT / "docs" / f"prediction_{target_date}.html"
        if not locked_prediction.exists():
            previous_prediction = ROOT / "docs" / f"prediction_{previous_date}.html"
            if not previous_prediction.exists():
                missing = [str(previous_prediction), str(locked_prediction)]
                legacy_prediction_status = {
                    "status": "skipped_missing_html",
                    "missing_files": missing,
                    "reason": "legacy prediction HTML is optional for current Forward/Pachi Agents flow",
                }
                print(f"legacy_prediction_status=skipped_missing_html missing_files={missing}")
            else:
                run_step(
                    "prediction_lock",
                    [
                        "prediction_daily.py",
                        "--actual-date", previous_date,
                        "--prediction-date", target_date,
                    ],
                )
                prediction_output = run_step(
                    "prediction",
                    [
                        "prediction_daily.py",
                        "--actual-date", target_date,
                        "--prediction-date", next_date,
                    ],
                )
                legacy_prediction_status = {"status": "completed"}
        else:
            prediction_output = run_step(
                "prediction",
                [
                    "prediction_daily.py",
                    "--actual-date", target_date,
                    "--prediction-date", next_date,
                ],
            )
            legacy_prediction_status = {"status": "completed"}

        run_step("daily_ingest", ["daily_ingest.py", "--date", target_date])
        wave = wave_forward_plan(target_date, previous_date, next_date)
        if not wave["previous_forward_valid"]:
            raise RuntimeError("previous Forward is missing or date-mismatched")
        if wave["evaluation_needed"]:
            run_step("07_wave_forward_evaluate", [
                "wave_lab/cross_machine_analysis/forward_evaluate.py",
                "--signal-date", previous_date, "--target-date", target_date,
            ])
        if wave["lock_needed"]:
            run_step("07_wave_forward_lock", [
                "wave_lab/cross_machine_analysis/forward_update.py",
                "--signal-date", target_date, "--target-date", next_date,
                "--append", "--lock-json",
            ])
        run_step(
            "propagation_lookup",
            ["propagation_lookup_html.py", "--daytime-date", target_date],
        )

        combined_report = (
            ROOT / "reports" /
            f"combined_signal_analysis_{args.combined_start}_{target_date}.md"
        )
        run_step(
            "combined_signal",
            [
                "combined_signal_analysis.py",
                "--start", args.combined_start,
                "--end", target_date,
                "--period-report", str(ROOT / "reports" / "cycle_sync_68_summary.md"),
                "--out", str(combined_report),
                "--html-out", str(ROOT / "docs" / "combined_signal_analysis.html"),
                "--daytime-date", target_date,
            ],
        )
        run_step("group_ranking", ["group_ranking.py"])
        run_step(
            "cyclewatch_page",
            ["cycle_watch.py", "page", "--date", next_date, "--refresh"],
        )
        run_step("cyclewatch_top", ["cycle_watch.py", "top", "--refresh"])
        docs_data_dir = ROOT / "docs" / "data"
        docs_data_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(
            ROOT / "data" / "cycle_watch_config.json",
            docs_data_dir / "cycle_watch_config.json",
        )
        run_step("cycle_after_hit", ["cycle_after_hit_analysis.py"])
        run_step(
            "intraday_hit_regime",
            [
                "intraday_hit_regime_analysis.py",
                "--start", args.combined_start,
                "--end", target_date,
            ],
        )

        pachi_agents_report: dict[str, object] | None = None
        if not args.skip_pachi_agents or args.pachi_agents or args.pachi_agents_dry_run:
            try:
                from pachi_agents.daily_run import run_daily

                pachi_agents_report = run_daily(
                    ROOT,
                    base_date=target_date,
                    dry_run=args.pachi_agents_dry_run,
                )
            except Exception as error:
                # Pachi Agents is an optional post-processing step. Keep the
                # completed pachi_analyze outputs intact and expose the error
                # in the pipeline summary for later retry.
                pachi_agents_report = {
                    "status": "error",
                    "error": f"{type(error).__name__}: {error}",
                }

        for step, command in execution_plan(target_date, previous_date, next_date)[0:9]:
            if step == "08_tug_replay":
                for group in [f"g{index}" for index in range(1, 10)]:
                    run_step(step + "_" + group, [
                        "wave_lab/tug_replay/export_tug_replay.py",
                        "--date", target_date, "--group", group, "--state-date", previous_date,
                    ])
                continue
            if step == "08_tug_replay_all":
                run_step(step, [
                    "wave_lab/tug_replay/export_tug_replay.py",
                    "--date", target_date, "--group", "all", "--state-date", previous_date,
                ])
                continue
            run_step(step, command)

        final_validation_started = time.perf_counter()
        web_validation = validate_web_outputs(target_date)
        STEP_TIMES["final_validation"] = time.perf_counter() - final_validation_started
        print(f"elapsed[final_validation]={STEP_TIMES['final_validation']:.3f}s")
        daily_complete = all(web_validation.values())
        print("DAILY COMPLETE" if daily_complete else "DAILY INCOMPLETE")

        event_rows = csv_rows(event_source)
        ohlc_rows = csv_rows(ohlc_source)
        source_counts: dict[str, int] = {}
        for row in ohlc_rows:
            source = row.get("Source", "unknown")
            source_counts[source] = source_counts.get(source, 0) + 1

        regime_payload = json.loads(
            (ROOT / "data" / "intraday_hit_regime.json").read_text(encoding="utf-8")
        )
        latest_regime = regime_payload["days"][-1]
        summary = {
            "status": "ok" if daily_complete else "incomplete",
            "date": target_date,
            "daytime_date": next_date,
            "analyze": {
                "event_rows": len(event_rows),
                "ohlc_rows": len(ohlc_rows),
                "ohlc_sources": source_counts,
                "overlays": len(list((analyze_dir / "overlay").glob("*.png"))),
            },
            "prediction": prediction_summary(prediction_output),
            "legacy_prediction": legacy_prediction_status,
            "propagation": propagation_summary(next_date),
            "intraday_regime": {
                "date": latest_regime["date"],
                "regime": latest_regime["regime"],
                "hit_density": round(latest_regime["hit_density"], 4),
                "adjusted_quality": round(latest_regime["adjusted_quality"], 4),
                "source": latest_regime["source"],
            },
            "history": pair_history_summary(),
            "external_cyclewatch": (
                f"python cycle_watch.py folders --date {target_date} --refresh"
            ),
            "pachi_agents": pachi_agents_report,
            "web_validation": web_validation,
            "elapsed_seconds": {key: round(value, 3) for key, value in STEP_TIMES.items()},
            "total_elapsed_seconds": round(time.perf_counter() - pipeline_started, 3),
        }
        write_summary(capture_root, summary)
        return 0
    except PipelineError as error:
        output = "\n".join(
            part for part in (error.result.stdout, error.result.stderr) if part
        ).splitlines()[-20:]
        summary = {
            "status": "error",
            "step": error.step,
            "command": error.command,
            "output_tail": output,
        }
        write_summary(capture_root, summary)
        return error.result.returncode or 1
    except Exception as error:
        summary = {
            "status": "error",
            "step": "pipeline",
            "error": f"{type(error).__name__}: {error}",
        }
        write_summary(capture_root, summary)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
