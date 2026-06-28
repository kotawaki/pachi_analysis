from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import analyze_pscube as pscube
import combined_signal_analysis as combined
import cycle_watch


def machine_key(value: str) -> str:
    number = int(str(value).strip())
    return f"{number:03d}" if number < 1000 else str(number)


def build(date: str, capture_root: Path, periods_path: Path, output_path: Path) -> dict:
    html_dir = capture_root / "html"
    periods_by_machine = combined.load_intraday_periods(periods_path)

    hits: list[int] = []
    events: dict[str, list[int]] = {}
    machines: dict[str, dict] = {}

    for html_path in sorted(html_dir.glob(f"{date}_*.html")):
        raw_machine = html_path.stem.split("_", 1)[1]
        key = machine_key(raw_machine)
        display_key = str(int(key))

        rows = pscube.parse_history_rows(html_path)
        parsed_events = pscube.events_from_history(rows, raw_machine, "initial")
        event_minutes = sorted({int(item["minute"]) for item in parsed_events})
        event_times = [item["time"] for item in parsed_events]
        periods = periods_by_machine.get(key, ())
        gaps, hit_periods = cycle_watch.event_status(key, periods, event_minutes)

        events[display_key] = event_minutes
        if hit_periods:
            hits.append(int(key))

        machines[display_key] = {
            "machine": raw_machine,
            "key": key,
            "history_rows": len(rows),
            "event_count": len(event_minutes),
            "event_times": event_times,
            "events": event_minutes,
            "periods": list(periods),
            "gaps": gaps,
            "hit_periods": sorted(set(hit_periods)),
            "hit": bool(hit_periods),
            "html": str(html_path),
        }

    payload = {
        "date": date,
        "source": str(capture_root),
        "periods_source": str(periods_path),
        "tolerance": cycle_watch.TOLERANCE,
        "machine_count": len(machines),
        "hits": sorted(set(hits)),
        "events": events,
        "machines": machines,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("date")
    parser.add_argument(
        "--capture-root",
        default=None,
        help="default: captures/pscube/YYYYMMDD/daytime",
    )
    parser.add_argument(
        "--periods",
        default=str(ROOT / "reports" / "cycle_sync_68_summary.md"),
    )
    parser.add_argument(
        "--output",
        default=None,
        help="default: data/daytime_hits_YYYYMMDD.json",
    )
    args = parser.parse_args()

    capture_root = Path(args.capture_root) if args.capture_root else ROOT / "captures" / "pscube" / args.date / "daytime"
    output_path = Path(args.output) if args.output else ROOT / "data" / f"daytime_hits_{args.date}.json"
    payload = build(args.date, capture_root, Path(args.periods), output_path)
    print(f"saved: {output_path}")
    print(f"machines: {payload['machine_count']}")
    print(f"hits: {len(payload['hits'])} {payload['hits']}")


if __name__ == "__main__":
    main()
