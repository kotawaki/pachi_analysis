"""Phase 4: パチこの統計・伝播ベース予測。"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from prediction_daily import cycle_forecast
from propagation import extract_starts

from .inputs import (
    available_snapshot_dates,
    load_daily_ohlc_rows,
    load_pair_history_as_of,
    load_snapshot,
    normalize_date,
)
from .predictions import PredictionStore, make_manifest_entry


LOGIC_VERSION = "pachi_agents_pachiko_v1"
MIN_CYCLE_HISTORY = 21


def _machine(value: Any) -> str:
    text = str(value or "").strip()
    try:
        number = int(text)
    except ValueError:
        return text
    return f"{number:03d}" if number < 1000 else str(number)


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(str(value).replace(",", "").replace("+", "").strip())
    except (TypeError, ValueError):
        return default


def _field(row: dict[str, Any], *names: str, default: str = "") -> str:
    lowered = {str(key).lower(): value for key, value in row.items()}
    for name in names:
        if name.lower() in lowered:
            return str(lowered[name.lower()]).strip()
    return default


def _group_strength(snapshots: Iterable[dict[str, Any]]) -> tuple[dict[str, float], dict[str, str]]:
    hits = defaultdict(int)
    active = defaultdict(set)
    machine_group: dict[str, str] = {}
    for snapshot in snapshots:
        for event in extract_starts(snapshot):
            group = str(event.get("group", ""))
            machine = _machine(event.get("machine"))
            hits[group] += 1
            machine_group[machine] = group
        for machine in snapshot.get("machines", []):
            if machine.get("active"):
                machine_group[_machine(machine.get("machine"))] = str(machine.get("group", ""))
                active[str(machine.get("group", ""))].add(_machine(machine.get("machine")))
    density = {group: hits[group] / max(1, len(active[group])) for group in set(hits) | set(active)}
    maximum = max(density.values(), default=0.0) or 1.0
    return {group: round(value / maximum, 4) for group, value in density.items()}, machine_group


def _daytime_hit_days(data_root: Path, cutoff: str) -> dict[str, int]:
    counts = defaultdict(int)
    paths = sorted(data_root.glob("daytime_hits_*.json")) if data_root.exists() else []
    selected = []
    for path in paths:
        try:
            day = normalize_date(path.stem.removeprefix("daytime_hits_"))
        except ValueError:
            continue
        if day <= cutoff:
            selected.append((day, path))
    for _day, path in selected[-7:]:
        with path.open(encoding="utf-8") as handle:
            payload = json.load(handle)
        for machine in payload.get("hits", []):
            counts[_machine(machine)] += 1
    return dict(counts)


def _cycle_signals(ohlc_rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_machine: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for row in ohlc_rows:
        machine = _machine(_field(row, "Machine", "machine"))
        day = str(row.get("date", ""))
        close = _number(_field(row, "Close", "close"))
        opening = _number(_field(row, "Open", "open"))
        if machine and day:
            by_machine[machine].append((day, round(close - opening)))
    result = {}
    for machine, values in by_machine.items():
        values.sort()
        if len(values) < MIN_CYCLE_HISTORY:
            continue
        forecast = cycle_forecast([value for _day, value in values])
        result[machine] = {
            "cycle_forecast": forecast,
            "cycle_direction": "positive" if forecast > 0 else "negative" if forecast < 0 else "flat",
            "cycle_history_days": len(values),
        }
    return result


def build_pachiko_agent(
    *,
    snapshots: list[dict[str, Any]],
    pair_history: dict[str, Any],
    daytime_hit_days: dict[str, int] | None = None,
    ohlc_rows: list[dict[str, Any]] | None = None,
    top_n: int = 5,
) -> dict[str, Any]:
    """伝播・グループ・日中hit・周期からパチこのpayloadを作る。"""
    daytime_hit_days = daytime_hit_days or {}
    cycle = _cycle_signals(ohlc_rows or [])
    strengths, machine_group = _group_strength(snapshots)
    candidates = set(machine_group) | set(daytime_hit_days) | set(cycle)
    propagation: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for pair in pair_history.get("pairs", {}).values():
        machine = _machine(pair.get("B"))
        daily = pair.get("daily", [])
        total_count = int(pair.get("total_count", sum(int(d.get("count", 0)) for d in daily)))
        days_seen = int(pair.get("days_seen", len(daily)))
        if machine and total_count >= 3 and days_seen >= 1:
            propagation[machine].append(pair)
            candidates.add(machine)

    scored = []
    for machine in candidates:
        pairs = propagation.get(machine, [])
        best_lift = max((float(pair.get("mean_lift", 0.0)) for pair in pairs), default=0.0)
        repeat_days = max((int(pair.get("days_seen", 0)) for pair in pairs), default=0)
        propagation_points = min(3.0, max(0.0, best_lift - 1.0) * 1.5) if pairs else 0.0
        group = machine_group.get(machine, "")
        group_points = strengths.get(group, 0.0) * 1.5
        hit_days = int(daytime_hit_days.get(machine, 0))
        daytime_points = min(1.5, hit_days * 0.5)
        cycle_info = cycle.get(machine, {})
        cycle_points = 0.75 if cycle_info.get("cycle_forecast", 0) > 0 else -0.25 if cycle_info else 0.0
        score = round(propagation_points + group_points + daytime_points + cycle_points, 4)

        reason_codes: list[str] = []
        if propagation_points > 0:
            reason_codes.append("PROPAGATION_REPEATED")
        if group_points >= 0.75:
            reason_codes.append("GROUP_STRENGTH_HIGH")
        if hit_days:
            reason_codes.append("DAYTIME_HIT_RECENT")
        if cycle_info.get("cycle_forecast", 0) > 0:
            reason_codes.append("CYCLE_POSITIVE")
        if not reason_codes:
            reason_codes.append("INSUFFICIENT_STATISTICAL_EVIDENCE")

        evidence_count = sum(bool(value) for value in (pairs, group, hit_days, cycle_info))
        confidence = round(min(0.9, max(0.05, 0.2 + evidence_count * 0.12 + score / 12)), 3)
        scored.append({
            "machine": machine,
            "score": score,
            "confidence": confidence,
            "signals": {
                "propagation_best_mean_lift": round(best_lift, 3),
                "propagation_repeat_days": repeat_days,
                "group": group,
                "group_strength": round(strengths.get(group, 0.0), 3),
                "daytime_hit_days_7": hit_days,
                "cycle_forecast": cycle_info.get("cycle_forecast"),
                "cycle_history_days": cycle_info.get("cycle_history_days", 0),
                "same_condition_records": repeat_days,
            },
            "reason_codes": reason_codes,
        })

    scored.sort(key=lambda item: (item["score"], item["machine"]), reverse=True)
    if not scored:
        return {
            "logic_version": LOGIC_VERSION,
            "primary_machine": None,
            "candidates": [],
            "confidence": 0.0,
            "comment": "統計入力が不足しているため、パチこの指名台を決定できません。",
            "signals": {"available_snapshots": len(snapshots), "available_candidates": 0},
            "reason_codes": ["INSUFFICIENT_STATISTICAL_EVIDENCE"],
        }

    top = scored[:top_n]
    primary = top[0]
    candidate_payload = [
        {key: item[key] for key in ("machine", "score", "confidence", "signals", "reason_codes")}
        for item in top
    ]
    return {
        "logic_version": LOGIC_VERSION,
        "primary_machine": primary["machine"],
        "candidates": candidate_payload,
        "confidence": primary["confidence"],
        "comment": f"{primary['machine']}番を、伝播・グループ強度・日中hit・周期の統計証拠で本命指名しました。",
        "signals": primary["signals"],
        "reason_codes": primary["reason_codes"],
    }


def generate_pachiko_prediction(
    store: PredictionStore,
    *,
    prediction_date: str,
    cutoff_date: str,
    snapshot_root: str | Path,
    pair_history_path: str | Path,
    daytime_data_root: str | Path | None = None,
    ohlc_root: str | Path | None = None,
    top_n: int = 5,
) -> Path:
    """D-1以前の入力から予測を作り、draft保存後にlocked化する。"""
    target = normalize_date(prediction_date)
    cutoff = normalize_date(cutoff_date)
    snapshot_root = Path(snapshot_root)
    snapshot_dates = [day for day in available_snapshot_dates(snapshot_root) if day <= cutoff][-7:]
    snapshots = [load_snapshot(snapshot_root, day, cutoff_date=cutoff) for day in snapshot_dates]
    history = load_pair_history_as_of(pair_history_path, cutoff)
    daytime = _daytime_hit_days(Path(daytime_data_root), cutoff) if daytime_data_root else {}
    ohlc_rows = load_daily_ohlc_rows(ohlc_root, cutoff_date=cutoff) if ohlc_root else []
    agent = build_pachiko_agent(
        snapshots=snapshots,
        pair_history=history,
        daytime_hit_days=daytime,
        ohlc_rows=ohlc_rows,
        top_n=top_n,
    )
    manifest = [
        make_manifest_entry(snapshot_root / f"{day}_snapshot.json", kind="snapshot", data_date=day)
        for day in snapshot_dates
    ]
    manifest.append(make_manifest_entry(pair_history_path, kind="propagation_history", data_date=cutoff))
    if daytime_data_root:
        for path in sorted(Path(daytime_data_root).glob("daytime_hits_*.json")):
            try:
                day = normalize_date(path.stem.removeprefix("daytime_hits_"))
            except ValueError:
                continue
            if day <= cutoff:
                manifest.append(make_manifest_entry(path, kind="daytime_hits", data_date=day))
    if ohlc_root:
        ohlc_root_path = Path(ohlc_root)
        for directory in sorted(ohlc_root_path.iterdir()) if ohlc_root_path.exists() else []:
            if not directory.is_dir() or len(directory.name) != 8 or not directory.name.isdigit():
                continue
            try:
                day = normalize_date(directory.name)
            except ValueError:
                continue
            if day <= cutoff:
                for path in sorted(directory.glob("*_daily_ohlc.csv")):
                    manifest.append(make_manifest_entry(path, kind="ohlc", data_date=day))
    payload = {
        "prediction_date": target,
        "cutoff_date": cutoff,
        "created_at": datetime.now().astimezone().isoformat(),
        "status": "draft",
        "logic_version": "pachi_agents_v1",
        "input_manifest": manifest,
        "agents": {
            "pachio": {},
            "pachiko": agent,
            "pachikamisama": {"agent_weights": {"pachio": None, "pachiko": None}},
        },
    }
    store.save(payload)
    return store.lock(target)
