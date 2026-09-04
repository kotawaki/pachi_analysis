#!/usr/bin/env python3
"""Export machine x signal-date Wave Lab and MA prospective snapshots.

The Forward JSON files are the locked source for Wave Lab state and actual
evaluation.  MA values are calculated from the existing business-day Close
series through signal_date only.  This file is a derived research export and
is intentionally not part of prediction generation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
FORWARD_DIR = ROOT / "docs" / "wave_lab" / "data" / "forward"
OUTPUT = FORWARD_DIR / "prospective_state_snapshots.json"
START_DATE = "20260828"
MACHINES = {f"{n:03d}" for n in range(39, 78)}

sys.path.insert(0, str(ROOT))
from chart_signal_positive import calc_ma, load_daily_ohlc  # noqa: E402


def compact(value: Any) -> str:
    return str(value or "").replace("-", "")


def as_bool(value: Any) -> bool:
    return value is True or str(value).lower() == "true"


def as_number(value: Any) -> Any:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return value
    return int(number) if number.is_integer() else number


def relation(left: Any, right: Any) -> str | None:
    if left is None or right is None:
        return None
    return "ABOVE" if left > right else "BELOW" if left < right else "EQUAL"


def slope_label(value: Any) -> str:
    if value is None:
        return "UNAVAILABLE"
    return "UP" if value > 0 else "DOWN" if value < 0 else "FLAT"


def alignment(ma5: Any, ma20: Any, ma75: Any) -> str:
    if None in (ma5, ma20, ma75):
        return "UNAVAILABLE"
    if ma5 > ma20 > ma75:
        return "BULLISH_ALIGNMENT"
    if ma5 < ma20 < ma75:
        return "BEARISH_ALIGNMENT"
    return "MIXED_ALIGNMENT"


def distance(close: Any, ma: Any) -> Any:
    if close is None or ma in (None, 0):
        return None
    return (close - ma) / abs(ma)


def feature_hash(row: dict[str, Any]) -> str:
    fields = {key: row[key] for key in row if key not in {
        "evaluation_status", "actual_bullish", "actual_open", "actual_high",
        "actual_low", "actual_close", "actual_source",
    }}
    raw = json.dumps(fields, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def load_forward_files() -> list[dict[str, Any]]:
    result = []
    for path in sorted(FORWARD_DIR.glob("20*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        signal_date = compact(data.get("signal_date"))
        if signal_date < START_DATE or not signal_date.isdigit():
            continue
        if data.get("mode") != "forward/prospective":
            continue
        if compact(data.get("max_input_date")) > signal_date:
            raise ValueError(f"future max_input_date: {path.name}")
        if as_bool(data.get("future_data_used")):
            raise ValueError(f"future_data_used=true: {path.name}")
        result.append(data)
    return sorted(result, key=lambda item: compact(item.get("signal_date")))


def build_snapshot(signal_date: str, data: dict[str, Any], row: dict[str, Any], series: list[dict[str, Any]]) -> dict[str, Any]:
    machine = str(row.get("machine", ""))
    asof = [item for item in series if compact(item.get("date")) <= signal_date]
    if not asof or compact(asof[-1].get("date")) != signal_date:
        raise ValueError(f"{machine}: no canonical Close at {signal_date}")
    ma5s, ma20s, ma75s = calc_ma(asof, 5), calc_ma(asof, 20), calc_ma(asof, 75)
    i, close = len(asof) - 1, asof[-1]["close"]
    ma5, ma20, ma75 = ma5s[i], ma20s[i], ma75s[i]
    previous = (
        ma5s[i - 1] if i else None,
        ma20s[i - 1] if i else None,
        ma75s[i - 1] if i else None,
    )
    slopes = tuple(
        None if current is None or prior is None else current - prior
        for current, prior in zip((ma5, ma20, ma75), previous)
    )
    status = str(data.get("evaluation_status") or "pending").lower()
    evaluated = status == "evaluated" and row.get("actual_bullish") not in (None, "")
    snapshot = {
        "signal_date": signal_date,
        "target_date": compact(data.get("target_date")),
        "machine": machine,
        "group": row.get("group"),
        "max_input_date": compact(data.get("max_input_date")),
        "ma_max_input_date": signal_date,
        "future_data_used_for_ma": False,
        "wave_state_source": f"docs/wave_lab/data/forward/{signal_date}.json",
        "UP_UP_UP": as_bool(row.get("UP_UP_UP")),
        "RIGHT": as_bool(row.get("RIGHT")),
        "LOW_CONVERGENCE_RIGHT": as_bool(row.get("LOW_CONVERGENCE_RIGHT")),
        "DOWN_DOWN_DOWN": as_bool(row.get("DOWN_DOWN_DOWN")),
        "ALL_3": as_bool(row.get("ALL_3")),
        "score": as_number(row.get("score")),
        "wave_direction_pattern": row.get("wave_direction_pattern"),
        "region": row.get("region"),
        "convergence_score": as_number(row.get("convergence_score")),
        "signal_close": close,
        "ma5": ma5,
        "ma20": ma20,
        "ma75": ma75,
        "ma5_slope": slopes[0],
        "ma20_slope": slopes[1],
        "ma75_slope": slopes[2],
        "ma5_direction": slope_label(slopes[0]),
        "ma20_direction": slope_label(slopes[1]),
        "ma75_direction": slope_label(slopes[2]),
        "ma5_slope_label": slope_label(slopes[0]),
        "ma20_slope_label": slope_label(slopes[1]),
        "ma75_slope_label": slope_label(slopes[2]),
        "close_vs_ma5": relation(close, ma5),
        "close_vs_ma20": relation(close, ma20),
        "close_vs_ma75": relation(close, ma75),
        "ma75_position": {"ABOVE": "BELOW_PRICE", "BELOW": "ABOVE_PRICE", "EQUAL": "AT_PRICE"}.get(relation(close, ma75), "UNAVAILABLE"),
        "ma5_distance": distance(close, ma5),
        "ma20_distance": distance(close, ma20),
        "ma75_distance": distance(close, ma75),
        "alignment": alignment(ma5, ma20, ma75),
        "ma_alignment": alignment(ma5, ma20, ma75),
        "evaluation_status": status,
        "actual_bullish": as_bool(row.get("actual_bullish")) if evaluated else None,
        "actual_open": as_number(row.get("actual_open")) if evaluated else None,
        "actual_high": as_number(row.get("actual_high")) if evaluated else None,
        "actual_low": as_number(row.get("actual_low")) if evaluated else None,
        "actual_close": as_number(row.get("actual_close")) if evaluated else None,
        "actual_source": data.get("actual_source"),
    }
    snapshot["feature_hash"] = feature_hash(snapshot)
    return snapshot


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=str(OUTPUT))
    args = parser.parse_args()
    forwards = load_forward_files()
    if not forwards:
        raise SystemExit("no eligible Forward JSON")
    loaded, _ = load_daily_ohlc(MACHINES)
    series_by_machine = {f"{int(key):03d}": value for key, value in loaded.items()}
    rows = []
    for data in forwards:
        signal_date = compact(data.get("signal_date"))
        signals = {str(row.get("machine", "")).zfill(3): row for row in data.get("machine_signals", [])}
        missing = sorted(MACHINES - set(signals))
        if missing:
            raise ValueError(f"{signal_date}: missing Forward machines {missing}")
        for machine in sorted(MACHINES):
            rows.append(build_snapshot(signal_date, data, signals[machine], series_by_machine[machine]))
    evaluated_dates = sorted({row["signal_date"] for row in rows if row["evaluation_status"] == "evaluated"})
    pending_dates = sorted({row["signal_date"] for row in rows if row["evaluation_status"] != "evaluated"})
    payload = {
        "metadata": {
            "source_type": "derived prospective snapshot; locked Forward + existing MA calculation",
            "prospective_start_date": START_DATE,
            "signal_recalculation_used": False,
            "historical_backfill_used": False,
            "prediction_use": False,
            "machines_per_date": len(MACHINES),
            "dates": sorted({row["signal_date"] for row in rows}),
            "evaluated_dates": evaluated_dates,
            "pending_dates": pending_dates,
            "record_count": len(rows),
            "ma_definition_source": "chart_signal_positive.calc_ma: business-day Close rolling average",
            "slope_definition": "current MA minus previous business-day MA",
            "distance_definition": "(Close - MA) / abs(MA)",
        },
        "records": rows,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"dates": len(payload["metadata"]["dates"]), "records": len(rows), "output": str(output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
