"""Phase 3: パチおのルールベース予測。

既存のOHLC入力をcutoff以前に限定して読み、既存のチャート指標実装
(``chart_signal_positive.py``)と周期推定実装(``prediction_daily.py``)を
純粋な計算関数として再利用する。パチこ・パチ神様の判断は行わない。
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from chart_signal_positive import calc_ma, detect_gc_events, latest_bull_structure, fib_class
from prediction_daily import cycle_forecast

from .inputs import load_daily_ohlc_rows, normalize_date
from .predictions import PredictionStore, make_manifest_entry


LOGIC_VERSION = "pachi_agents_pachio_v1"
MIN_HISTORY_DAYS = 21


def _value(row: dict[str, Any], *names: str, default: str = "") -> str:
    lowered = {str(key).lower(): value for key, value in row.items()}
    for name in names:
        if name.lower() in lowered:
            return str(lowered[name.lower()]).strip()
    return default


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(str(value).replace(",", "").replace("+", "").strip())
    except (TypeError, ValueError):
        return default


def _machine(value: Any) -> str:
    text = str(value or "").strip()
    try:
        number = int(text)
    except ValueError:
        return text
    return f"{number:03d}" if number < 1000 else str(number)


def _as_chart_row(row: dict[str, Any]) -> dict[str, Any]:
    day = normalize_date(str(row["date"]))
    return {
        "date": f"{day[:4]}-{day[4:6]}-{day[6:]}",
        "time": f"{day[:4]}-{day[4:6]}-{day[6:]}",
        "open": _number(_value(row, "Open", "open")),
        "high": _number(_value(row, "High", "high")),
        "low": _number(_value(row, "Low", "low")),
        "close": _number(_value(row, "Close", "close")),
    }


def _recent_gc(ma5: list[float | None], ma20: list[float | None], ma75: list[float | None], data: list[dict]) -> bool:
    events = detect_gc_events(ma5, ma20, ma75, data)
    return bool(events and events[-1]["idx"] >= len(data) - 5)


def _score(data: list[dict[str, Any]]) -> dict[str, Any]:
    closes = [row["close"] for row in data]
    ma5 = calc_ma(data, 5)
    ma20 = calc_ma(data, 20)
    ma75 = calc_ma(data, 75)
    ma5_slope = (ma5[-1] - ma5[-2]) if ma5[-1] is not None and ma5[-2] is not None else None
    ma20_slope = (ma20[-1] - ma20[-2]) if ma20[-1] is not None and ma20[-2] is not None else None
    cycle = cycle_forecast([int(round(value)) for value in closes])
    structure = latest_bull_structure(data, lb=5)
    fib = fib_class(data, structure)
    gc_recent = _recent_gc(ma5, ma20, ma75, data) if ma75[-1] is not None else False

    score = 0.0
    reason_codes: list[str] = []
    if ma5_slope is not None:
        code = "MA5_UP" if ma5_slope > 0 else "MA5_DOWN"
        score += 2.0 if ma5_slope > 0 else -1.0
        reason_codes.append(code)
    if ma20_slope is not None:
        code = "MA20_UP" if ma20_slope > 0 else "MA20_DOWN"
        score += 1.5 if ma20_slope > 0 else -1.0
        reason_codes.append(code)
    if cycle > 0:
        score += 1.0
        reason_codes.append("CYCLE_POSITIVE")
    elif cycle < 0:
        score -= 1.0
        reason_codes.append("CYCLE_NEGATIVE")
    if structure:
        score += 2.0
        reason_codes.append("BULL_STRUCTURE")
    fib_points = {"green": 1.0, "blue": 1.0, "yellow": 0.5, "red": -0.5, "broken": -2.0, "none": 0.0}
    if fib != "none":
        score += fib_points[fib]
        reason_codes.append(f"FIB_{fib.upper()}")
    if gc_recent:
        score += 0.75
        reason_codes.append("GC_RECENT")

    normalized_score = round(score, 4)
    confidence = round(min(0.95, max(0.05, 0.5 + normalized_score / 12)), 3)
    signals = {
        "history_days": len(data),
        "last_date": data[-1]["date"].replace("-", ""),
        "ma5_slope": None if ma5_slope is None else round(ma5_slope, 3),
        "ma20_slope": None if ma20_slope is None else round(ma20_slope, 3),
        "cycle_forecast": cycle,
        "bullish_structure": bool(structure),
        "fibonacci_class": fib,
        "golden_cross_recent": gc_recent,
    }
    return {
        "score": normalized_score,
        "confidence": confidence,
        "signals": signals,
        "reason_codes": reason_codes,
    }


def _manifest(ohlc_root: Path, cutoff_date: str) -> list[dict[str, Any]]:
    entries = []
    for directory in sorted(ohlc_root.iterdir()) if ohlc_root.exists() else []:
        if not directory.is_dir() or not directory.name.isdigit() or len(directory.name) != 8:
            continue
        day = normalize_date(directory.name)
        if day > cutoff_date:
            continue
        for path in sorted(directory.glob("*_daily_ohlc.csv")):
            entries.append(make_manifest_entry(path, kind="ohlc", data_date=day))
    return entries


def build_pachio_agent(ohlc_rows: list[dict[str, Any]], *, top_n: int = 5) -> dict[str, Any]:
    """OHLC行からパチおの出力payloadを作る。"""
    by_machine: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for source in ohlc_rows:
        machine = _machine(_value(source, "Machine", "machine"))
        if not machine:
            continue
        try:
            by_machine[machine].append(_as_chart_row(source))
        except (KeyError, ValueError):
            continue

    scored = []
    for machine, rows in by_machine.items():
        rows.sort(key=lambda row: row["date"])
        if len(rows) < MIN_HISTORY_DAYS:
            continue
        result = _score(rows)
        scored.append({"machine": machine, **result})

    if not scored:
        return {
            "logic_version": LOGIC_VERSION,
            "primary_machine": None,
            "candidates": [],
            "confidence": 0.0,
            "comment": "利用可能なOHLC履歴が不足しているため、パチおの指名台を決定できません。",
            "signals": {"eligible_machines": 0, "minimum_history_days": MIN_HISTORY_DAYS},
            "reason_codes": ["INSUFFICIENT_HISTORY"],
        }

    scored.sort(key=lambda row: (row["score"], row["machine"]), reverse=True)
    candidates = scored[:top_n]
    primary = candidates[0]
    candidate_payload = [
        {
            "machine": row["machine"],
            "score": row["score"],
            "confidence": row["confidence"],
            "signals": row["signals"],
            "reason_codes": row["reason_codes"],
        }
        for row in candidates
    ]
    return {
        "logic_version": LOGIC_VERSION,
        "primary_machine": primary["machine"],
        "candidates": candidate_payload,
        "confidence": primary["confidence"],
        "comment": f"{primary['machine']}番を、MA・周期・チャート構造の合算スコアで本命指名しました。",
        "signals": primary["signals"],
        "reason_codes": primary["reason_codes"],
    }


def generate_pachio_prediction(
    store: PredictionStore,
    ohlc_root: str | Path,
    *,
    prediction_date: str,
    cutoff_date: str,
    top_n: int = 5,
) -> Path:
    """D-1までのOHLCから予測を作り、draft保存後にlocked化する。"""
    target = normalize_date(prediction_date)
    cutoff = normalize_date(cutoff_date)
    rows = load_daily_ohlc_rows(ohlc_root, cutoff_date=cutoff)
    agent = build_pachio_agent(rows, top_n=top_n)
    payload = {
        "prediction_date": target,
        "cutoff_date": cutoff,
        "created_at": datetime.now().astimezone().isoformat(),
        "status": "draft",
        "logic_version": "pachi_agents_v1",
        "input_manifest": _manifest(Path(ohlc_root), cutoff),
        "agents": {
            "pachio": agent,
            "pachiko": {},
            "pachikamisama": {"agent_weights": {"pachio": None, "pachiko": None}},
        },
    }
    path = store.save(payload)
    return store.lock(target)
