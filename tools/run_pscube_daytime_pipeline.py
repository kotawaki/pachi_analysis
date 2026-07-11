from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from run_pscube_morning_pipeline import PipelineError, ROOT, run_step, write_summary


def machine_key(value: str) -> str:
    text = str(value).strip()
    return text if len(text) >= 4 else text.zfill(4)


def enabled_machines() -> list[str]:
    payload = json.loads((ROOT / "pscube_targets.json").read_text(encoding="utf-8"))
    machines = [
        machine_key(machine)
        for target in payload.get("targets", [])
        if target.get("enabled", True)
        for machine in target.get("machines", [])
    ]
    return list(dict.fromkeys(machines))


def propagation_hits() -> list[int]:
    text = (ROOT / "docs" / "propagation_lookup.html").read_text(encoding="utf-8")
    match = re.search(r"const DAILY_HITS = new Set\(\[([^\]]*)\]\)", text)
    return [int(value) for value in re.findall(r"\d+", match.group(1))] if match else []


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run P'sCUBE daytime candidate updates with compact output."
    )
    parser.add_argument("--date", required=True, help="Daytime date, YYYYMMDD")
    parser.add_argument("--capture-root", type=Path, required=True)
    parser.add_argument("--combined-start", default="20260613")
    parser.add_argument("--validate-only", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    capture_root = args.capture_root.resolve()
    expected = enabled_machines()
    html_dir = capture_root / "html"
    available = {
        path.stem.split("_", 1)[1]
        for path in html_dir.glob(f"{args.date}_*.html")
    }
    missing = [machine for machine in expected if machine not in available]
    if missing:
        summary = {
            "status": "error",
            "step": "capture_validation",
            "expected": len(expected),
            "complete": len(expected) - len(missing),
            "missing_html": missing,
        }
        write_summary(capture_root, summary, "daytime_pipeline_summary.json")
        return 1
    if args.validate_only:
        summary = {
            "status": "ok",
            "step": "capture_validation",
            "expected": len(expected),
            "complete": len(available),
            "missing_html": [],
        }
        write_summary(capture_root, summary, "daytime_pipeline_summary.json")
        return 0

    output_path = ROOT / "data" / f"daytime_hits_{args.date}.json"
    combined_report = (
        ROOT / "reports" /
        f"combined_signal_analysis_{args.combined_start}_{args.date}.md"
    )
    try:
        run_step(
            "daytime_hits",
            [
                "tools/build_daytime_hits.py",
                args.date,
                "--capture-root", str(capture_root),
                "--output", str(output_path),
            ],
        )
        payload = json.loads(output_path.read_text(encoding="utf-8"))
        hits = sorted(int(machine) for machine in payload.get("hits", []))

        run_step(
            "propagation_lookup",
            ["propagation_lookup_html.py", "--daytime-date", args.date],
        )
        run_step(
            "combined_signal",
            [
                "combined_signal_analysis.py",
                "--start", args.combined_start,
                "--end", args.date,
                "--period-report", str(ROOT / "reports" / "cycle_sync_68_summary.md"),
                "--backfill",
                "--out", str(combined_report),
                "--html-out", str(ROOT / "docs" / "combined_signal_analysis.html"),
                "--daytime-date", args.date,
            ],
        )

        displayed_hits = sorted(propagation_hits())
        if displayed_hits != hits:
            raise ValueError(
                f"PropagationLookup hit mismatch: data={hits} display={displayed_hits}"
            )

        summary = {
            "status": "ok",
            "date": args.date,
            "machines": payload.get("machine_count", 0),
            "hits": hits,
            "hit_count": len(hits),
            "candidate_charts": hits,
            "commit_files": [
                str(output_path.relative_to(ROOT)),
                "docs/propagation_lookup.html",
                "docs/combined_signal_analysis.html",
                str(combined_report.relative_to(ROOT)),
            ],
        }
        write_summary(capture_root, summary, "daytime_pipeline_summary.json")
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
        write_summary(capture_root, summary, "daytime_pipeline_summary.json")
        return error.result.returncode or 1
    except Exception as error:
        summary = {
            "status": "error",
            "step": "pipeline",
            "error": f"{type(error).__name__}: {error}",
        }
        write_summary(capture_root, summary, "daytime_pipeline_summary.json")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
