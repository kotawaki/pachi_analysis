"""Evaluate a locked prospective Wave Lab record without recalculating its signals."""
from __future__ import annotations

import csv
import json
import os
import tempfile
import argparse
from pathlib import Path
from statistics import mean, median

ROOT = Path(__file__).resolve().parents[2]
TRACK = Path(__file__).resolve().parent / "tracking"
OHLC = ROOT / "csv" / "daily_ohlc" / "20260829" / "20260829_daily_ohlc.csv"
SIGNAL_DATE = "2026-08-28"
TARGET_DATE = "2026-08-29"
SUMMARY = TRACK / "forward_validation_20260828_summary.json"


def _atomic_json_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    except Exception:
        try:
            os.unlink(name)
        except FileNotFoundError:
            pass
        raise


def _file_sha256(path: Path) -> str:
    import hashlib
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _atomic_csv_write(path: Path, rows: list[dict]) -> None:
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    os.close(fd)
    try:
        write_csv(Path(name), rows)
        os.replace(name, path)
    except Exception:
        try:
            os.unlink(name)
        except FileNotFoundError:
            pass
        raise


def _sync_tracking(payload: dict, root: Path) -> None:
    """Reflect only the newly-known actual fields into existing tracking rows."""
    track = root / "wave_lab" / "cross_machine_analysis" / "tracking"
    machine_path = track / "forward_machine_signal_tracking.csv"
    if not machine_path.exists():
        return
    locked = payload["machine_signals"]
    by_machine = {str(row["machine"]).zfill(3): row for row in locked}
    machine_rows = read_csv(machine_path)
    evaluated = []
    for row in machine_rows:
        if row.get("signal_date", "").replace("-", "") != payload["signal_date"]:
            continue
        source = by_machine.get(str(row.get("machine", "")).zfill(3))
        if source:
            for key in ("evaluation_status", "actual_bullish", "actual_open", "actual_high", "actual_low", "actual_close"):
                value = source.get(key)
                row[key] = "" if value is None else str(value)
            evaluated.append(row)
    _atomic_csv_write(machine_path, machine_rows)
    if not evaluated:
        return
    daily_path = track / "forward_daily_signal_tracking.csv"
    if daily_path.exists():
        daily_rows = read_csv(daily_path)
        for row in daily_rows:
            if row.get("signal_date", "").replace("-", "") == payload["signal_date"]:
                row.update(stats(evaluated))
                row["evaluation_status"] = "evaluated"
        _atomic_csv_write(daily_path, daily_rows)
    group_path = track / "forward_group_signal_tracking.csv"
    if group_path.exists():
        group_rows = read_csv(group_path)
        for row in group_rows:
            if row.get("signal_date", "").replace("-", "") != payload["signal_date"]:
                continue
            members = [item for item in evaluated if item.get("group") == row.get("group")]
            row.update(stats(members)); row["evaluation_status"] = "evaluated"
        _atomic_csv_write(group_path, group_rows)
    summary_path = track / f"forward_validation_{payload['signal_date']}_summary.json"
    if summary_path.exists():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        summary["evaluation_status"] = "evaluated"
        summary["actual_source"] = f"csv/daily_ohlc/{payload['target_date']}/{payload['target_date']}_daily_ohlc.csv"
        summary["evaluation"] = stats(evaluated)
        _atomic_json_write(summary_path, summary)


def evaluate_forward(signal_date: str, target_date: str, *, root: Path = ROOT,
                     overwrite: bool = False) -> dict[str, object]:
    """Evaluate one locked Forward JSON; never recompute its prediction fields."""
    signal_date = signal_date.replace("-", "")
    target_date = target_date.replace("-", "")
    forward_path = root / "docs" / "wave_lab" / "data" / "forward" / f"{signal_date}.json"
    if not forward_path.exists():
        return {"status": "error", "reason": "locked_forward_missing", "path": str(forward_path)}
    with forward_path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if payload.get("signal_date") != signal_date or payload.get("target_date") != target_date:
        return {"status": "error", "reason": "forward_date_mismatch"}
    status = str(payload.get("evaluation_status", "")).lower()
    before = _file_sha256(forward_path)
    if status == "evaluated":
        return {"status": "skipped", "reason": "already_evaluated", "sha256": before}
    if status != "pending":
        return {"status": "error", "reason": f"unsupported_evaluation_status:{status}"}
    ohlc_path = root / "csv" / "daily_ohlc" / target_date / f"{target_date}_daily_ohlc.csv"
    if not ohlc_path.exists():
        return {"status": "skipped", "reason": "target_ohlc_missing", "sha256": before}
    actual_rows = {str(int(row["Machine"])).zfill(3): row for row in read_csv(ohlc_path)}
    machines = payload.get("machine_signals", [])
    if not machines or any(str(row.get("machine", "")).zfill(3) not in actual_rows for row in machines):
        return {"status": "error", "reason": "locked_machine_rows_or_actual_missing"}
    updated = json.loads(json.dumps(payload))
    for row in updated["machine_signals"]:
        actual = actual_rows[str(row["machine"]).zfill(3)]
        row.update({
            "evaluation_status": "evaluated",
            "actual_bullish": num(actual["Close"]) > num(actual["Open"]),
            "actual_open": actual["Open"], "actual_high": actual["High"],
            "actual_low": actual["Low"], "actual_close": actual["Close"],
        })
    updated["evaluation_status"] = "evaluated"
    updated["actual_source"] = f"csv/daily_ohlc/{target_date}/{target_date}_daily_ohlc.csv"
    _atomic_json_write(forward_path, updated)
    _sync_tracking(updated, root)
    return {"status": "evaluated", "path": str(forward_path), "sha256": _file_sha256(forward_path),
            "overwrite": overwrite}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict]) -> None:
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def num(value: str) -> float:
    return float(value)


def fmt(value: float) -> str:
    return str(int(value)) if value.is_integer() else str(value)


def enrich(row: dict[str, str], actual: dict[str, str]) -> dict[str, str]:
    result = dict(row)
    close = num(actual["Close"])
    result.update({
        "evaluation_status": "evaluated",
        "actual_bullish": str(num(actual["Close"]) > num(actual["Open"])),
        "actual_open": actual["Open"], "actual_high": actual["High"],
        "actual_low": actual["Low"], "actual_close": actual["Close"],
        "actual_close_ge_5000": str(close >= 5000),
        "actual_close_ge_10000": str(close >= 10000),
        "actual_close_ge_15000": str(close >= 15000),
        "actual_close_ge_20000": str(close >= 20000),
    })
    return result


def stats(rows: list[dict[str, str]]) -> dict[str, object]:
    closes = [num(r["actual_close"]) for r in rows]
    highs = [num(r["actual_high"]) for r in rows]
    lows = [num(r["actual_low"]) for r in rows]
    bullish = [r for r in rows if r["actual_bullish"] == "True"]
    return {
        "actual_bullish_count": len(bullish),
        "actual_bullish_rate": len(bullish) / len(rows) if rows else None,
        "actual_close_mean": mean(closes) if closes else None,
        "actual_close_median": median(closes) if closes else None,
        "actual_high_mean": mean(highs) if highs else None,
        "actual_low_mean": mean(lows) if lows else None,
        **{f"actual_close_ge_{threshold}_count": sum(c >= threshold for c in closes)
           for threshold in (5000, 10000, 15000, 20000)},
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--signal-date")
    parser.add_argument("--target-date")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    if args.signal_date or args.target_date:
        if not (args.signal_date and args.target_date):
            raise SystemExit("--signal-date and --target-date must be supplied together")
        result = evaluate_forward(args.signal_date, args.target_date, overwrite=args.overwrite)
        print(json.dumps(result, ensure_ascii=False))
        return 2 if result.get("status") == "error" else 0
    actual_rows = {str(int(row["Machine"])).zfill(3): row for row in read_csv(OHLC)}
    machine_path = TRACK / "forward_machine_signal_tracking.csv"
    machine_rows = read_csv(machine_path)
    target = [r for r in machine_rows if r["signal_date"] == SIGNAL_DATE and r["target_date"] == TARGET_DATE]
    if len(target) != 39:
        raise ValueError(f"expected 39 locked rows, got {len(target)}")
    evaluated = [enrich(row, actual_rows[row["machine"].zfill(3)]) for row in target]
    all_rows = [r for r in machine_rows if not (r["signal_date"] == SIGNAL_DATE and r["target_date"] == TARGET_DATE)] + evaluated
    write_csv(machine_path, all_rows)

    daily_path = TRACK / "forward_daily_signal_tracking.csv"
    daily_rows = read_csv(daily_path)
    daily = next(r for r in daily_rows if r["signal_date"] == SIGNAL_DATE and r["target_date"] == TARGET_DATE)
    daily.update(stats(evaluated)); daily["evaluation_status"] = "evaluated"
    write_csv(daily_path, daily_rows)

    group_path = TRACK / "forward_group_signal_tracking.csv"
    group_rows = read_csv(group_path)
    for group in group_rows:
        if group["signal_date"] != SIGNAL_DATE or group["target_date"] != TARGET_DATE:
            continue
        members = [r for r in evaluated if r["group"] == group["group"]]
        group.update(stats(members)); group["evaluation_status"] = "evaluated"
    write_csv(group_path, group_rows)

    strong_path = TRACK / "forward_strong_group_tracking.csv"
    strong_rows = read_csv(strong_path)
    for strong in strong_rows:
        if strong.get("signal_date") != SIGNAL_DATE or strong.get("target_date") != TARGET_DATE:
            continue
        members = [r for r in evaluated if r["group"] == strong["group"]]
        candidate = next((r for r in members if r["machine"] == strong.get("candidate_machine")), None)
        closes = sorted(((num(r["actual_close"]), r["machine"]) for r in members), reverse=True)
        strong.update(stats(members)); strong["evaluation_status"] = "evaluated"
        if candidate:
            rank = next(i for i, (_close, machine) in enumerate(closes, 1) if machine == candidate["machine"])
            strong.update({"candidate_actual_bullish": candidate["actual_bullish"],
                           "candidate_actual_open": candidate["actual_open"],
                           "candidate_actual_high": candidate["actual_high"],
                           "candidate_actual_low": candidate["actual_low"],
                           "candidate_actual_close": candidate["actual_close"],
                           "candidate_group_close_rank": rank,
                           "candidate_group_top2": str(rank <= 2),
                           "candidate_group_top3": str(rank <= 3),
                           "candidate_group_max_close_diff": fmt(closes[0][0] - num(candidate["actual_close"]))})
    write_csv(strong_path, strong_rows)

    with SUMMARY.open(encoding="utf-8") as handle:
        summary = json.load(handle)
    summary["evaluation_status"] = "evaluated"
    summary["actual_source"] = (
        f"csv/daily_ohlc/{TARGET_DATE.replace('-', '')}/"
        f"{TARGET_DATE.replace('-', '')}_daily_ohlc.csv"
    )
    summary["evaluation"] = stats(evaluated)
    for strong_group in summary.get("strong_groups", []):
        strong_group["evaluation_status"] = "evaluated"
    summary["future_outcome"] = {"bullish_evaluated": True, "close_thresholds_evaluated": True,
                                  "group_result_evaluated": True, "all3_result_evaluated": True}
    SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    candidate = next(r for r in evaluated if r["machine"] == "046")
    g1 = [r for r in evaluated if r["group"] == "g1"]
    ordered = sorted(g1, key=lambda r: num(r["actual_close"]), reverse=True)
    rank = next(i for i, r in enumerate(ordered, 1) if r["machine"] == "046")
    print(json.dumps({"candidate_046": candidate, "group_close_rank": rank, "group_stats": stats(g1)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
