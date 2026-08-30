"""Exploratory 20260829 ignition-timeline analysis.

This reads Phase 2 artifacts only. It ranks observed raw deltas and does not
create a causal, predictive, tug, or thresholded ignition score.
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "wave_lab" / "group_flow" / "output" / "20260829"
MACHINES = [f"{n:04d}" for n in range(39, 78)]
FOCUS_START, FOCUS_END = 13 * 60, 15 * 60
CONTEXT_START, CONTEXT_END = 12 * 60 + 30, 15 * 60 + 30
PEAK_MINUTE = 14 * 60 + 40


def read_csv(name: str) -> list[dict]:
    with (OUT / name).open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def number(value):
    if value in (None, "", "null"):
        return None
    return float(value) if "." in str(value) else int(value)


def minute(text: str) -> int:
    hour, minute_value = text.split(":")
    return int(hour) * 60 + int(minute_value)


def fmt(value):
    return "" if value is None else round(value, 2)


def write_csv(name: str, rows: list[dict], fields: list[str]) -> None:
    with (OUT / name).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def build_html(summary: dict, replay_rows: list[dict], machine_summary: list[dict]) -> str:
    replay = "".join(
        f"<tr><td>{row['time']}</td><td>{row['island_total']}</td><td>{row['top_machine']}</td><td>{row['top_group']}</td><td>{row['top_delta_5m']}</td></tr>"
        for row in replay_rows
    )
    machines = "".join(
        f"<tr><td>{row['machine']}</td><td>{row['group']}</td><td>{row['max_5m_time']}</td><td>{row['max_5m']}</td><td>{row['max_10m_time']}</td><td>{row['max_10m']}</td><td>{row['max_30m_time']}</td><td>{row['max_30m']}</td></tr>"
        for row in machine_summary
    )
    return f"""<!doctype html><html lang='ja'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>20260829 Ignition Timeline</title><style>
body{{font-family:system-ui,sans-serif;background:#0f172a;color:#e5e7eb;margin:20px}}h1,h2{{color:#93c5fd}}.note{{background:#172033;border-left:4px solid #60a5fa;padding:12px;line-height:1.6}}table{{border-collapse:collapse;margin:12px 0 28px;font-size:13px}}th,td{{border:1px solid #374151;padding:5px 8px;text-align:right}}th{{background:#1f2937}}td:first-child,th:first-child{{text-align:left}}pre{{white-space:pre-wrap;background:#111827;padding:12px}}
</style></head><body><h1>20260829 Ignition Timeline Analysis</h1><div class='note'>13:00–15:00のdeltaを全39台で比較する探索表示です。先行観測は因果や起点を意味しません。deltaは既存causal previous-value hold済みtimelineから取得しています。</div><h2>Summary</h2><pre>{html.escape(json.dumps(summary, ensure_ascii=False, indent=2))}</pre><h2>13:00–15:00 Event Replay</h2><table><tr><th>time</th><th>island total events</th><th>top machine</th><th>group</th><th>delta 5m</th></tr>{replay}</table><h2>Machine maxima</h2><table><tr><th>machine</th><th>group</th><th>max5 time</th><th>max5</th><th>max10 time</th><th>max10</th><th>max30 time</th><th>max30</th></tr>{machines}</table></body></html>"""


def analyze() -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    machine_map = {}
    with (ROOT / "machine_master.csv").open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            machine = f"{int(row['machine']):04d}"
            if machine in MACHINES:
                machine_map[machine] = {"group": f"g{int(row['group'])}", "island": row["island"]}

    machine_rows = read_csv("machine_timeline.csv")
    by_machine = defaultdict(list)
    for row in machine_rows:
        by_machine[row["machine"]].append({"minute": minute(row["time"]), "time": row["time"], "value": number(row["value"])})
    delta_rows = []
    machine_summary = []
    for machine in MACHINES:
        series = sorted(by_machine.get(machine, []), key=lambda item: item["minute"])
        previous = {item["minute"]: item["value"] for item in series}
        for item in series:
            row = {"time": item["time"], "minute": item["minute"], "machine": machine, "group": machine_map.get(machine, {}).get("group", ""), "value": item["value"]}
            for window in (5, 10, 30):
                prior = previous.get(item["minute"] - window)
                row[f"delta_{window}m"] = fmt(item["value"] - prior) if prior is not None else ""
            delta_rows.append(row)

        focus = [row for row in delta_rows if row["machine"] == machine and FOCUS_START <= row["minute"] <= FOCUS_END]
        summary_row = {"machine": machine, "group": machine_map.get(machine, {}).get("group", "")}
        for window in (5, 10, 30):
            key = f"delta_{window}m"
            positive = [row for row in focus if row[key] != "" and row[key] > 0]
            maximum = max((row for row in focus if row[key] != ""), key=lambda row: row[key], default=None)
            summary_row[f"first_positive_{window}m_time"] = positive[0]["time"] if positive else ""
            summary_row[f"max_{window}m"] = maximum[key] if maximum else ""
            summary_row[f"max_{window}m_time"] = maximum["time"] if maximum else ""
        machine_summary.append(summary_row)

    write_csv(OUT / "machine_delta_5m_timeline.csv", delta_rows, ["time", "minute", "machine", "group", "value", "delta_5m", "delta_10m", "delta_30m"])
    write_csv(OUT / "machine_move_summary.csv", machine_summary, ["machine", "group", "first_positive_5m_time", "max_5m", "max_5m_time", "first_positive_10m_time", "max_10m", "max_10m_time", "first_positive_30m_time", "max_30m", "max_30m_time"])

    group_delta = {int(row["minute"]): row for row in read_csv("group_delta_timeline.csv")}
    density = {int(row["minute"]): row for row in read_csv("island_hit_density.csv")}
    replay_rows = []
    for current in range(FOCUS_START, FOCUS_END + 1, 5):
        candidates = [row for row in delta_rows if row["minute"] == current and row["delta_5m"] != ""]
        top = max(candidates, key=lambda row: row["delta_5m"], default={"machine": "", "group": "", "delta_5m": ""})
        row = {"time": f"{current // 60:02d}:{current % 60:02d}", "minute": current, "island_initial": density.get(current, {}).get("initial_5m", ""), "island_continuation": density.get(current, {}).get("continuation_5m", ""), "island_total": density.get(current, {}).get("total_5m", ""), "top_machine": top.get("machine", ""), "top_group": top.get("group", ""), "top_delta_5m": top.get("delta_5m", "")}
        for group in [f"g{i}" for i in range(1, 10)]:
            source = group_delta.get(current, {})
            row[f"{group}_delta_5m"] = source.get(f"{group}_5min_delta", "")
            row[f"{group}_delta_10m"] = source.get(f"{group}_10min_delta", "")
            row[f"{group}_delta_30m"] = source.get(f"{group}_30min_delta", "")
        replay_rows.append(row)
    write_csv(OUT / "ignition_timeline_5m.csv", replay_rows, ["time", "minute", "island_initial", "island_continuation", "island_total", "top_machine", "top_group", "top_delta_5m"] + [f"g{i}_delta_{window}m" for i in range(1, 10) for window in (5, 10, 30)])

    ranking_rows = []
    for current in range(FOCUS_START, FOCUS_END + 1, 5):
        current_rows = [row for row in delta_rows if row["minute"] == current]
        for window in (5, 10, 30):
            key = f"delta_{window}m"
            ranked = sorted((row for row in current_rows if row[key] != ""), key=lambda row: (-row[key], row["machine"]))
            ranking_rows.extend({"time": row["time"], "minute": current, "window_minutes": window, "rank": rank, "machine": row["machine"], "group": row["group"], "value": row["value"], "delta": row[key]} for rank, row in enumerate(ranked, 1))
    write_csv(OUT / "machine_delta_rankings.csv", ranking_rows, ["time", "minute", "window_minutes", "rank", "machine", "group", "value", "delta"])

    def earliest_positive(rows, key):
        positives = [row for row in rows if row.get(key, "") != "" and row[key] > 0]
        if not positives:
            return None
        earliest = min(row["minute"] for row in positives)
        return [row for row in positives if row["minute"] == earliest]

    all_focus = [row for row in delta_rows if FOCUS_START <= row["minute"] <= FOCUS_END]
    early_5 = earliest_positive(all_focus, "delta_5m")
    early_10 = earliest_positive(all_focus, "delta_10m")
    before_peak = [row for row in all_focus if row["minute"] < PEAK_MINUTE and row["delta_30m"] != ""]
    max30_before = max(before_peak, key=lambda row: (row["delta_30m"], -row["minute"]), default=None)
    group_delta_rows = read_csv("group_delta_timeline.csv")
    group_candidates = []
    for row in group_delta_rows:
        current = int(row["minute"])
        if FOCUS_START <= current <= FOCUS_END:
            for group in machine_map.values():
                pass
            for group in [f"g{i}" for i in range(1, 10)]:
                value = number(row.get(f"{group}_5min_delta", ""))
                if value is not None and value > 0:
                    group_candidates.append({"group": group, "minute": current, "time": row["time"], "delta_5m": value})
    earliest_group_minute = min((row["minute"] for row in group_candidates), default=None)
    early_groups = [row for row in group_candidates if row["minute"] == earliest_group_minute]

    summary = {
        "date": "20260829", "window": "13:00-15:00", "context_window": "12:30-15:30", "machines": 39, "groups": 9,
        "island_peak": {"initial_30m": "14:20=18", "continuation_30m": "14:40=52", "total_30m": "14:40=68"},
        "definition": "raw deltas from existing 5-minute causal previous-value hold timeline; first positive means delta > 0; no ignition threshold",
        "early_movers": {"five_min": early_5, "ten_min": early_10, "max_30m_before_14_40": max30_before, "earliest_positive_group_5m": early_groups},
        "g7_g8": [row for row in machine_summary if row["group"] in {"g7", "g8"}],
        "peak_reference_minute": PEAK_MINUTE,
        "canonical_or_phase2_inputs_changed": False,
    }
    (OUT / "ignition_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "ignition_timeline.html").write_text(build_html(summary, replay_rows, machine_summary), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default="20260829", choices=["20260829"])
    parser.parse_args()
    analyze()
