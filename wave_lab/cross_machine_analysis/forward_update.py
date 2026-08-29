"""固定条件を未来へ適用するWave Lab forward validation更新。

既存historical trackingや通常Wave Lab出力は変更せず、指定cutoffまでの
as-of特徴量からprospective trackingだけを作成する。
"""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from wave_lab.fft_reconstruct import analyze, load_machine_rows, phase_convergence_analysis


TRACK = Path(__file__).resolve().parent / "tracking"
SIGNAL_DATE = "2026-08-28"
TARGET_DATE = "2026-08-29"
MACHINES = [f"{n:03d}" for n in range(39, 78)]
GROUPS = {
    "g1": ["046", "055", "064", "073"],
    "g2": ["047", "056", "065", "074"],
    "g3": ["039", "048", "057", "066", "075"],
    "g4": ["040", "049", "058", "067", "076"],
    "g5": ["041", "050", "059", "068", "077"],
    "g6": ["042", "051", "060", "069"],
    "g7": ["043", "052", "061", "070"],
    "g8": ["044", "053", "062", "071"],
    "g9": ["045", "054", "063", "072"],
}
MACHINE_GROUP = {machine: group for group, machines in GROUPS.items() for machine in machines}


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        raise ValueError(f"no rows: {path}")
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def machine_signal(machine: str) -> dict:
    rows = load_machine_rows(machine, SIGNAL_DATE)
    if not rows:
        raise ValueError(f"no OHLC rows for {machine}")
    components, daily, _centered, _comparison = analyze(rows)
    convergence_rows, _threshold = phase_convergence_analysis(daily, components)
    current_daily = daily[-1]
    current_convergence = convergence_rows[-1]
    pattern = current_daily["wave_direction_pattern"]
    region = current_convergence["centroid_region"]
    convergence = float(current_convergence["convergence_score"])
    up_up_up = pattern == "UP-UP-UP"
    right = region == "RIGHT"
    low_convergence_right = convergence < 0.5 and right
    down_down_down = pattern == "DOWN-DOWN-DOWN"
    score = int(up_up_up) + int(right) + int(low_convergence_right)
    return {
        "signal_date": SIGNAL_DATE,
        "target_date": TARGET_DATE,
        "machine": machine,
        "group": MACHINE_GROUP[machine],
        "wave_direction_pattern": pattern,
        "region": region,
        "convergence_score": convergence,
        "UP_UP_UP": up_up_up,
        "RIGHT": right,
        "LOW_CONVERGENCE_RIGHT": low_convergence_right,
        "DOWN_DOWN_DOWN": down_down_down,
        "ALL_3": score == 3,
        "score": score,
        "evaluation_status": "pending",
        "actual_bullish": "",
        "actual_open": "",
        "actual_high": "",
        "actual_low": "",
        "actual_close": "",
    }


def main() -> int:
    TRACK.mkdir(parents=True, exist_ok=True)
    machines = [machine_signal(machine) for machine in MACHINES]

    machine_fields = [
        "signal_date", "target_date", "machine", "group",
        "wave_direction_pattern", "region", "convergence_score",
        "UP_UP_UP", "RIGHT", "LOW_CONVERGENCE_RIGHT", "DOWN_DOWN_DOWN",
        "ALL_3", "score", "evaluation_status", "actual_bullish",
        "actual_open", "actual_high", "actual_low", "actual_close",
    ]
    write_csv(TRACK / "forward_machine_signal_tracking.csv", [
        {field: row[field] for field in machine_fields} for row in machines
    ])

    counts = {
        "UP_UP_UP_count": sum(row["UP_UP_UP"] for row in machines),
        "RIGHT_count": sum(row["RIGHT"] for row in machines),
        "LOW_CONVERGENCE_RIGHT_count": sum(row["LOW_CONVERGENCE_RIGHT"] for row in machines),
        "ANY_SIGNAL_count": sum(row["score"] > 0 for row in machines),
        "ALL_3_count": sum(row["ALL_3"] for row in machines),
        "DOWN_DOWN_DOWN_count": sum(row["DOWN_DOWN_DOWN"] for row in machines),
    }
    daily = {
        "signal_date": SIGNAL_DATE,
        "target_date": TARGET_DATE,
        **counts,
        "direction_balance": counts["UP_UP_UP_count"] - counts["DOWN_DOWN_DOWN_count"],
        "evaluation_status": "pending",
    }
    write_csv(TRACK / "forward_daily_signal_tracking.csv", [daily])

    groups = []
    for group, group_machines in GROUPS.items():
        rows = [row for row in machines if row["machine"] in group_machines]
        signal_total = sum(row["score"] for row in rows)
        groups.append({
            "signal_date": SIGNAL_DATE,
            "target_date": TARGET_DATE,
            "group": group,
            "machine_count": len(rows),
            "UP_UP_UP_count": sum(row["UP_UP_UP"] for row in rows),
            "RIGHT_count": sum(row["RIGHT"] for row in rows),
            "LOW_CONVERGENCE_RIGHT_count": sum(row["LOW_CONVERGENCE_RIGHT"] for row in rows),
            "ALL_3_count": sum(row["ALL_3"] for row in rows),
            "DOWN_DOWN_DOWN_count": sum(row["DOWN_DOWN_DOWN"] for row in rows),
            "direction_balance": sum(row["UP_UP_UP"] for row in rows) - sum(row["DOWN_DOWN_DOWN"] for row in rows),
            "group_signal_total": signal_total,
            "group_signal_score": signal_total / len(rows),
            "evaluation_status": "pending",
        })
    ranked = sorted(groups, key=lambda row: (-row["group_signal_score"], row["group"]))
    for rank, row in enumerate(ranked, 1):
        row["group_signal_rank"] = rank
        row["A_rank_top3"] = rank <= 3
        row["B_all3_ge1"] = row["ALL_3_count"] >= 1
        row["C_direction_positive"] = row["direction_balance"] > 0
        row["STRONG_GROUP"] = row["A_rank_top3"] and row["B_all3_ge1"] and row["C_direction_positive"]
    write_csv(TRACK / "forward_group_signal_tracking.csv", ranked)

    strong = []
    for row in ranked:
        if not row["STRONG_GROUP"]:
            continue
        candidates = [machine for machine in machines if machine["group"] == row["group"] and machine["ALL_3"]]
        strong.append({
            "signal_date": SIGNAL_DATE,
            "target_date": TARGET_DATE,
            "group": row["group"],
            "group_signal_rank": row["group_signal_rank"],
            "group_signal_score": row["group_signal_score"],
            "direction_balance": row["direction_balance"],
            "ALL_3_count": len(candidates),
            "ALL_3_machines": ",".join(machine["machine"] for machine in candidates),
            "single_ALL3_in_strong_group": len(candidates) == 1,
            "candidate_machine": candidates[0]["machine"] if len(candidates) == 1 else "",
            "evaluation_status": "pending",
        })
    write_csv(TRACK / "forward_strong_group_tracking.csv", strong or [{
        "signal_date": SIGNAL_DATE, "target_date": TARGET_DATE,
        "evaluation_status": "pending", "strong_group_count": 0,
    }])

    summary = {
        "signal_date": SIGNAL_DATE,
        "target_date": TARGET_DATE,
        "mode": "forward/prospective",
        "max_input_date": SIGNAL_DATE,
        "future_data_used": False,
        "machines": len(machines),
        "groups": len(groups),
        "machine_counts": counts,
        "direction_balance": daily["direction_balance"],
        "score_distribution": {str(score): sum(row["score"] == score for row in machines) for score in range(4)},
        "strong_groups": strong,
        "evaluation_status": "pending",
    }
    (TRACK / "forward_validation_20260828_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
