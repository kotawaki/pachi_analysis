"""Build intraday cycle-hit candidates from saved P'sCUBE daytime HTML."""

from __future__ import annotations

import argparse
import csv
import json
import re
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).parent
DEFAULT_PERIOD_REPORT = ROOT / "reports" / "cycle_sync_68_summary.md"
T_START = 10 * 60
T_END = 22 * 60 + 30


class HistoryTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_history = False
        self.in_td = False
        self.history_tag = None
        self.current_row = []
        self.rows = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = dict(attrs)
        if attr.get("id") == "tblHISTb":
            self.in_history = True
            self.history_tag = tag
        elif self.in_history and tag == "tr":
            self.current_row = []
        elif self.in_history and tag == "td":
            self.in_td = True

    def handle_endtag(self, tag: str) -> None:
        if self.in_history and self.history_tag == tag:
            self.in_history = False
            self.history_tag = None
        elif self.in_history and tag == "td":
            self.in_td = False
        elif self.in_history and tag == "tr":
            if self.current_row:
                self.rows.append(self.current_row)
            self.current_row = []

    def handle_data(self, data: str) -> None:
        if self.in_td:
            text = data.strip()
            if text:
                self.current_row.append(text)


def norm_machine(value: str) -> str:
    text = str(value).strip()
    if not text:
        return ""
    number = int(text)
    return f"{number:03d}" if number < 1000 else str(number)


def display_machine(value: str) -> str:
    return str(int(value)) if str(value).strip().isdigit() else str(value)


def time_text(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def parse_time(text: str) -> int:
    hour, minute = text.split(":", 1)
    return int(hour) * 60 + int(minute)


def read_html_text(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8", "cp932", "shift_jis"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def parse_history_rows(html_path: Path) -> list[list[str]]:
    if html_path.suffix.lower() == ".csv" or html_path.name.endswith("_history.csv"):
        with html_path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            return [
                [
                    str(row.get("bonus_id", "")),
                    str(row.get("time", "")),
                    str(row.get("start", "")),
                    str(row.get("status", "")),
                ]
                for row in reader
            ]
    parser = HistoryTableParser()
    parser.feed(read_html_text(html_path))
    return [
        row
        for row in parser.rows
        if len(row) >= 4 and row[1].count(":") == 1 and row[2].strip().isdigit()
    ]


def is_continuation_status(status: str) -> bool:
    text = str(status)
    return (
        "継" in text
        or "邯" in text
        or "�p" in text
        or text in {"継続", "邯咲ｶ・"}
    )


def events_from_history(rows: list[list[str]]) -> list[dict]:
    has_slot_reg = any(row[3] == "REG" for row in rows)
    events = []
    for row in rows:
        number, time_value, start_value, status = row[:4]
        if has_slot_reg:
            if status != "REG":
                continue
            event_kind = "slot_reg_initial" if int(start_value) >= 10 else "slot_reg_continue"
        else:
            event_kind = "pachinko_bonus" if is_continuation_status(status) else "pachinko_initial"

        minute = parse_time(time_value)
        if minute < T_START or minute > T_END:
            continue
        events.append(
            {
                "no": number,
                "time": time_value,
                "minute": minute,
                "start_count": int(start_value),
                "status": status,
                "event_kind": event_kind,
            }
        )
    return sorted(events, key=lambda item: (item["minute"], int(item["no"])))


def load_intraday_periods(path: Path) -> dict[str, tuple[int, ...]]:
    periods = {}
    if not path.exists():
        return periods

    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|") or line.startswith("|---"):
            continue
        if "譌･荳ｭ蜻ｨ譛毫" in line:
            continue
        parts = [part.strip() for part in line.strip().strip("|").split("|")]
        if len(parts) < 3:
            continue
        try:
            machine = norm_machine(parts[1])
        except ValueError:
            continue
        nums = [int(value) for value in re.findall(r"\d+", parts[2])]
        if nums:
            periods[machine] = tuple(nums[:3])
    return periods


def build_daytime_hits(capture_root: Path, date: str, period_report: Path, tolerance: int) -> dict:
    periods_by_machine = load_intraday_periods(period_report)
    html_dir = capture_root / "html"
    history_dir = capture_root / "history"
    input_dir = history_dir if history_dir.exists() else html_dir
    machines = {}
    hits = []

    pattern = "*_history.csv" if input_dir == history_dir else f"{date}_*.html"
    for html_path in sorted(input_dir.glob(pattern)):
        stem = html_path.stem.removesuffix("_history")
        machine = norm_machine(stem.rsplit("_", 1)[-1])
        if not machine:
            continue

        rows = parse_history_rows(html_path)
        events = events_from_history(rows)
        event_minutes = [
            event["minute"]
            for event in events
            if event["event_kind"] in {"pachinko_initial", "slot_reg_initial", "slot_reg_continue"}
        ]
        event_minutes = sorted(dict.fromkeys(event_minutes))
        gaps = []
        hit_periods = set()
        periods = periods_by_machine.get(machine, ())
        for prev, cur in zip(event_minutes, event_minutes[1:]):
            gap = cur - prev
            gap_hits = [period for period in periods if abs(gap - period) <= tolerance]
            if gap_hits:
                hit_periods.update(gap_hits)
            gaps.append(
                {
                    "prev": prev,
                    "cur": cur,
                    "gap": gap,
                    "hits": gap_hits,
                }
            )

        machine_key = display_machine(machine)
        is_hit = bool(hit_periods)
        if is_hit:
            hits.append(int(machine_key))
        machines[machine_key] = {
            "machine": machine,
            "key": machine,
            "history_rows": len(rows),
            "event_count": len(event_minutes),
            "event_times": [time_text(value) for value in event_minutes],
            "events": event_minutes,
            "periods": list(periods),
            "gaps": gaps,
            "hit_periods": sorted(hit_periods),
            "hit": is_hit,
            "html": str(html_path.resolve().relative_to(ROOT)),
        }

    return {
        "date": date,
        "source": str(capture_root.resolve().relative_to(ROOT)),
        "periods_source": str(period_report),
        "tolerance": tolerance,
        "machine_count": len(machines),
        "hits": sorted(hits),
        "events": {machine: item["events"] for machine, item in machines.items()},
        "machines": machines,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True, help="Target date, YYYYMMDD")
    parser.add_argument("--capture-root", required=True, help="captures/pscube/YYYYMMDD/daytime")
    parser.add_argument("--period-report", default=str(DEFAULT_PERIOD_REPORT))
    parser.add_argument("--tolerance", type=int, default=5)
    parser.add_argument("--out", default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    capture_root = Path(args.capture_root)
    period_report = Path(args.period_report)
    output = Path(args.out) if args.out else ROOT / "data" / f"daytime_hits_{args.date}.json"
    data = build_daytime_hits(capture_root, args.date, period_report, args.tolerance)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(output)
    print(f"machines={data['machine_count']} hits={len(data['hits'])}: {data['hits']}")


if __name__ == "__main__":
    main()
