from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).parent
EVENT_CSV_DIR = ROOT / "csv" / "analyze"
DAILY_OHLC_DIR = ROOT / "csv" / "daily_ohlc"


def machine_id(value: object) -> str:
    text = str(value or "").strip()
    try:
        return str(int(text))
    except ValueError:
        return text.lstrip("0") or text


def machine_zfill(value: object) -> str:
    text = str(value or "").strip()
    try:
        return str(int(text)).zfill(3)
    except ValueError:
        return text.zfill(3)


def to_int(value: object, default: int = 0) -> int:
    try:
        return int(float(str(value).replace(",", "").strip()))
    except (TypeError, ValueError):
        return default


def get_value(row: dict, index: int, *keys: str, default: str = "") -> str:
    for key in keys:
        if key in row:
            return row.get(key, default)
    fields = list(row.keys())
    if 0 <= index < len(fields):
        return row.get(fields[index], default)
    return default


def parse_time(value: object) -> int:
    text = str(value or "").strip()
    if not text or ":" not in text:
        return 0
    hour, minute = text.split(":", 1)
    return int(hour) * 60 + int(minute)


def normalize_filter(machine_filter: set[str] | None) -> set[str] | None:
    if machine_filter is None:
        return None
    return {machine_id(machine) for machine in machine_filter}


def load_chart_daily_ohlc(machine_filter: set[str] | None = None) -> tuple[dict[str, dict[str, dict]], dict[str, dict]]:
    machine_filter = normalize_filter(machine_filter)
    daily: dict[str, dict[str, dict]] = defaultdict(dict)
    meta: dict[str, dict] = {}
    for path in sorted(DAILY_OHLC_DIR.glob("*/*_daily_ohlc.csv")):
        date = path.parent.name[:8]
        with path.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                machine = machine_id(get_value(row, 1, "Machine", "machine"))
                if machine_filter and machine not in machine_filter:
                    continue
                open_value = to_int(get_value(row, 4, "Open", "open"))
                high = to_int(get_value(row, 5, "High", "high"))
                low = to_int(get_value(row, 6, "Low", "low"))
                close = to_int(get_value(row, 7, "Close", "close"))
                daily[machine][date] = {
                    "open": open_value,
                    "high": high,
                    "low": low,
                    "close": close,
                    "net": close - open_value,
                    "source": get_value(row, 8, "Source", "source", default="chart"),
                    "point_count": to_int(get_value(row, 9, "PointCount", "point_count")),
                }
                meta.setdefault(machine, {
                    "group": str(get_value(row, 2, "Group", "group")).strip(),
                    "island": str(get_value(row, 3, "Island", "island")).strip(),
                })
    return daily, meta


def load_event_daily_ohlc(machine_filter: set[str] | None = None) -> tuple[dict[str, dict[str, dict]], dict[str, dict]]:
    machine_filter = normalize_filter(machine_filter)
    sessions: dict[str, dict[str, list[tuple[str, str, int, int]]]] = defaultdict(lambda: defaultdict(list))
    meta: dict[str, dict] = {}
    for path in sorted(EVENT_CSV_DIR.glob("*/*_analyze.csv")):
        date = path.parent.name[:8]
        with path.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                machine = machine_id(get_value(row, 1, "Machine", "machine"))
                if machine_filter and machine not in machine_filter:
                    continue
                kind = str(get_value(row, 4)).strip()
                if not kind:
                    continue
                start_time = str(get_value(row, 5)).strip() or "00:00"
                end_time = str(get_value(row, 7)).strip() or start_time
                start_ball = to_int(get_value(row, 6))
                end_ball = to_int(get_value(row, 8))
                sessions[machine][date].append((start_time, end_time, start_ball, end_ball))
                meta.setdefault(machine, {
                    "group": str(get_value(row, 2, "Group", "group")).strip(),
                    "island": str(get_value(row, 3, "Island", "island")).strip(),
                })

    daily: dict[str, dict[str, dict]] = defaultdict(dict)
    for machine, days in sessions.items():
        for date, rows in days.items():
            latest = max(rows, key=lambda item: parse_time(item[1]))
            points = [0]
            for _start_time, _end_time, start_ball, end_ball in rows:
                points.append(start_ball)
                points.append(end_ball)
            close = latest[3]
            daily[machine][date] = {
                "open": 0,
                "high": max(points),
                "low": min(points),
                "close": close,
                "net": close,
                "source": "events",
                "point_count": len(points),
            }
    return daily, meta


def load_daily_ohlc(machine_filter: set[str] | None = None) -> tuple[dict[str, dict[str, dict]], dict[str, dict]]:
    chart_daily, chart_meta = load_chart_daily_ohlc(machine_filter)
    event_daily, event_meta = load_event_daily_ohlc(machine_filter)
    merged: dict[str, dict[str, dict]] = defaultdict(dict)
    meta = {**event_meta, **chart_meta}
    for machine, days in event_daily.items():
        merged[machine].update(days)
    for machine, days in chart_daily.items():
        merged[machine].update(days)
    return merged, meta


def load_daily_net(machine_filter: set[str] | None = None) -> dict[str, list[tuple[str, int]]]:
    daily, _meta = load_daily_ohlc(machine_filter)
    return {
        machine: [(date, row["net"]) for date, row in sorted(days.items())]
        for machine, days in daily.items()
    }
