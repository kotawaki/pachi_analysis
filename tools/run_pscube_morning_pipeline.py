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
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


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
        raise PipelineError(step, command, result)
    return "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part)


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
        "--allow-missing-html",
        action="store_true",
        help="Run chart-only analysis for captures where HTML was intentionally omitted.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    target_date = args.date
    next_date = args.daytime_date or ymd_add(target_date, 1)
    previous_date = ymd_add(target_date, -1)
    capture_root = args.capture_root.resolve()

    if not capture_root.exists():
        raise SystemExit(f"capture root not found: {capture_root}")

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
        run_step(
            "propagation_lookup",
            ["propagation_lookup_html.py", "--daytime-date", next_date],
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
                "--backfill",
                "--out", str(combined_report),
                "--html-out", str(ROOT / "docs" / "combined_signal_analysis.html"),
                "--daytime-date", next_date,
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
        if args.pachi_agents or args.pachi_agents_dry_run:
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
            "status": "ok",
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
