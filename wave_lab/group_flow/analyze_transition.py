"""Exploratory Wave Lab -> Group Flow stage comparison.

Reads locked previous-day forward JSON and existing Group Flow Phase 2/Ignition
artifacts. It makes no Wave Lab recalculation, prediction rule, or threshold.
Ranks are deterministic ordinal ranks; ties are resolved by group name.
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FORWARD = ROOT / "docs" / "wave_lab" / "data" / "forward"
GROUP_FLOW = ROOT / "wave_lab" / "group_flow" / "output"
GROUPS = [f"g{i}" for i in range(1, 10)]


def read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def num(value):
    if value in (None, "", "null"):
        return None
    return float(value) if "." in str(value) else int(value)


def mean(values):
    return round(sum(values) / len(values), 4) if values else None


def spearman(a: list[float], b: list[float]) -> float | None:
    if len(a) != len(b) or len(a) < 2:
        return None
    ma, mb = mean(a), mean(b)
    da = sum((x - ma) ** 2 for x in a)
    db = sum((y - mb) ** 2 for y in b)
    if not da or not db:
        return None
    return round(sum((x - ma) * (y - mb) for x, y in zip(a, b)) / math.sqrt(da * db), 4)


def ordinal(rows: list[dict], value_key: str, descending: bool) -> list[dict]:
    ordered = sorted(rows, key=lambda row: ((-row[value_key] if descending else row[value_key]) if row[value_key] is not None else (float("inf") if descending else float("inf")), row["group"]))
    for rank, row in enumerate(ordered, 1):
        row["rank"] = rank
    return ordered


def find_previous(target: str) -> str:
    candidates = sorted(path.stem for path in FORWARD.glob("*.json") if path.stem.isdigit() and path.stem < target)
    if not candidates:
        raise FileNotFoundError(f"previous forward JSON not found for {target}")
    return candidates[-1]


def build_html(summary: dict, group_rows: list[dict], overlaps: list[dict], timeline_rows: list[dict]) -> str:
    previous = "".join(f"<tr><td>{r['previous_wavelab_rank']}</td><td>{r['group']}</td><td>{r['previous_wavelab_score']}</td><td>{r['previous_strong_group']}</td></tr>" for r in group_rows)
    final = "".join(f"<tr><td>{r['final_rank']}</td><td>{r['group']}</td><td>{r['final_close']}</td></tr>" for r in sorted(group_rows, key=lambda r: r["final_rank"]))
    matrix = "".join(f"<tr><td>{r['group']}</td><td>{r['previous_wavelab_rank']}</td><td>{r['morning_first_hit_rank']}</td><td>{r['activity_start_rank']}</td><td>{r['peak_activity_rank']}</td><td>{r['peak_delta_rank']}</td><td>{r['final_rank']}</td></tr>" for r in group_rows)
    overlap = "".join(f"<tr><td>{r['stage']}</td><td>{r['overlap_count']}</td><td>{r['overlap_groups']}</td></tr>" for r in overlaps)
    stage_rows = "".join(f"<tr><td>{r['time']}</td><td>{r['activity_top3']}</td><td>{r['delta_top3']}</td></tr>" for r in timeline_rows)
    return f"""<!doctype html><html lang='ja'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Wave Lab → Group Flow Transition</title><style>
body{{font-family:system-ui,sans-serif;background:#0f172a;color:#e5e7eb;margin:20px}}h1,h2{{color:#93c5fd}}.note{{background:#172033;border-left:4px solid #60a5fa;padding:12px;line-height:1.6}}table{{border-collapse:collapse;margin:12px 0 28px;font-size:13px}}th,td{{border:1px solid #374151;padding:6px 9px;text-align:right}}th{{background:#1f2937}}td:first-child,th:first-child{{text-align:left}}pre{{white-space:pre-wrap;background:#111827;padding:12px;max-width:1100px;overflow:auto}}
</style></head><body><h1>Wave Lab → Group Flow Transition</h1><div class='note'>前日Wave Lab JSONと20260829 Group Flow実測を同一group軸で比較する探索分析です。時間的な先行・重複は因果関係を意味しません。Wave Labは再計算していません。</div><h2>Summary</h2><pre>{html.escape(json.dumps(summary, ensure_ascii=False, indent=2))}</pre><h2>Previous Wave Lab</h2><table><tr><th>rank</th><th>group</th><th>score</th><th>strong_group</th></tr>{previous}</table><h2>Final Payout</h2><table><tr><th>rank</th><th>group</th><th>final close</th></tr>{final}</table><h2>Rank Transition Matrix</h2><table><tr><th>group</th><th>WaveLab</th><th>Morning</th><th>Activity Start</th><th>Peak Activity</th><th>Peak Delta</th><th>Final</th></tr>{matrix}</table><h2>Top3 Overlap</h2><table><tr><th>stage</th><th>overlap</th><th>groups</th></tr>{overlap}</table><h2>Timeline Top3 (5分)</h2><table><tr><th>time</th><th>activity top3</th><th>delta top3</th></tr>{stage_rows}</table></body></html>"""


def analyze(target: str, previous: str | None = None) -> dict:
    previous = previous or find_previous(target)
    prev_obj = json.loads((FORWARD / f"{previous}.json").read_text(encoding="utf-8"))
    out = GROUP_FLOW / target
    phase2 = json.loads((out / "phase2_summary.json").read_text(encoding="utf-8"))
    group_counts = {row["group"]: row for row in read_csv(out / "group_event_counts.csv")}
    initial_events = read_csv(out / "all_initial_hits.csv")
    activity_rows = read_csv(out / "group_activity_timeline.csv")
    payout_rows = read_csv(out / "group_timeline.csv")
    delta_rows = read_csv(out / "group_delta_timeline.csv")
    density_rows = read_csv(out / "island_hit_density.csv")

    wave_rows = {row["group"]: row for row in prev_obj["group_signals"]}
    previous_rows = [{"group": group, "value": num(wave_rows[group]["group_signal_rank"]), "score": num(wave_rows[group]["group_signal_score"]), "strong": wave_rows[group].get("STRONG_GROUP", False)} for group in GROUPS]
    previous_rows = ordinal(previous_rows, "value", False)

    morning = []
    for group in GROUPS:
        events = sorted((event for event in initial_events if event["group"] == group), key=lambda row: (row["time"], row["machine"]))
        first = events[0] if events else None
        morning.append({"group": group, "value": int(first["time"].split(":")[0]) * 60 + int(first["time"].split(":")[1]) if first else None, "first_initial_time": first["time"] if first else "", "first_initial_machine": first["machine"] if first else ""})
    morning_ranked = ordinal(morning, "value", False)

    activity_start = []
    for group in GROUPS:
        rows = sorted((row for row in activity_rows if row["group"] == group), key=lambda row: int(row["minute"]))
        first = next((row for row in rows if int(row["total_hit_count"]) > 0), None)
        activity_start.append({"group": group, "value": int(first["minute"]) if first else None, "activity_start_time": first["time"] if first else "", "activity_start_initial": int(first["initial_count"]) if first else 0, "activity_start_continuation": int(first["continuation_count"]) if first else 0, "activity_start_total": int(first["total_hit_count"]) if first else 0})
    activity_start_ranked = ordinal(activity_start, "value", False)

    peak_time = 14 * 60 + 40
    peak_density = next(row for row in density_rows if int(row["minute"]) == peak_time)
    peak_activity = []
    for group in GROUPS:
        rows = [row for row in activity_rows if row["group"] == group and peak_time <= int(row["minute"]) < peak_time + 30]
        peak_activity.append({"group": group, "value": sum(int(row["total_hit_count"]) for row in rows), "initial": sum(int(row["initial_count"]) for row in rows), "continuation": sum(int(row["continuation_count"]) for row in rows)})
    peak_activity_ranked = ordinal(peak_activity, "value", True)
    peak_payout = next(row for row in payout_rows if int(row["minute"]) == peak_time)
    peak_delta = next(row for row in delta_rows if int(row["minute"]) == peak_time)
    peak_delta_rows = [{"group": group, "value": num(peak_delta.get(f"{group}_30min_delta", ""))} for group in GROUPS]
    peak_delta_ranked = ordinal(peak_delta_rows, "value", True)
    final_rows = [{"group": group, "value": num(group_counts[group]["final_group_close"])} for group in GROUPS]
    final_ranked = ordinal(final_rows, "value", True)

    rank_by = lambda rows: {row["group"]: row for row in rows}
    previous_by, morning_by, start_by = rank_by(previous_rows), rank_by(morning_ranked), rank_by(activity_start_ranked)
    activity_by, delta_by, final_by = rank_by(peak_activity_ranked), rank_by(peak_delta_ranked), rank_by(final_ranked)
    group_summary = []
    for group in GROUPS:
        group_summary.append({
            "group": group,
            "previous_wavelab_rank": previous_by[group]["rank"], "previous_wavelab_score": previous_by[group]["score"], "previous_strong_group": previous_by[group]["strong"],
            "morning_first_hit_time": morning_by[group]["first_initial_time"], "morning_first_hit_machine": morning_by[group]["first_initial_machine"], "morning_first_hit_rank": morning_by[group]["rank"],
            "activity_start_time": start_by[group]["activity_start_time"], "activity_start_rank": start_by[group]["rank"], "activity_start_initial": start_by[group]["activity_start_initial"], "activity_start_continuation": start_by[group]["activity_start_continuation"], "activity_start_total": start_by[group]["activity_start_total"],
            "peak_initial_count": activity_by[group]["initial"], "peak_continuation_count": activity_by[group]["continuation"], "peak_total_activity": activity_by[group]["value"], "peak_activity_rank": activity_by[group]["rank"], "peak_group_delta": delta_by[group]["value"], "peak_delta_rank": delta_by[group]["rank"],
            "final_close": final_by[group]["value"], "final_rank": final_by[group]["rank"],
        })
    matrix = [{"group": row["group"], "wavelab_rank": row["previous_wavelab_rank"], "morning_rank": row["morning_first_hit_rank"], "activity_start_rank": row["activity_start_rank"], "peak_activity_rank": row["peak_activity_rank"], "peak_delta_rank": row["peak_delta_rank"], "final_rank": row["final_rank"]} for row in group_summary]
    top3 = {"WaveLab": [row["group"] for row in previous_rows[:3]], "Morning": [row["group"] for row in morning_ranked[:3]], "ActivityStart": [row["group"] for row in activity_start_ranked[:3]], "PeakActivity": [row["group"] for row in peak_activity_ranked[:3]], "PeakDelta": [row["group"] for row in peak_delta_ranked[:3]], "Final": [row["group"] for row in final_ranked[:3]]}
    overlaps = []
    for stage in ("Morning", "ActivityStart", "PeakActivity", "PeakDelta", "Final"):
        common = sorted(set(top3["WaveLab"]) & set(top3[stage]))
        overlaps.append({"stage": stage, "overlap_count": len(common), "overlap_groups": ",".join(common)})

    timeline = []
    for row in payout_rows:
        current = int(row["minute"])
        activities = [{"group": group, "value": int(next(x["total_hit_count"] for x in activity_rows if x["group"] == group and int(x["minute"]) == current))} for group in GROUPS]
        deltas = [{"group": group, "value": num(next(x for x in delta_rows if int(x["minute"]) == current).get(f"{group}_5min_delta", ""))} for group in GROUPS]
        activities.sort(key=lambda x: (-x["value"], x["group"])); deltas.sort(key=lambda x: (-(x["value"] if x["value"] is not None else -10**18), x["group"]))
        timeline.append({"time": row["time"], "minute": current, "activity_top3": ",".join(x["group"] for x in activities[:3]), "delta_top3": ",".join(x["group"] for x in deltas[:3]), "activity_rank": ";".join(f"{i+1}:{x['group']}" for i, x in enumerate(activities)), "delta_rank": ";".join(f"{i+1}:{x['group']}" for i, x in enumerate(deltas))})

    corr = []
    for stage, key in (("morning", "morning_rank"), ("activity_start", "activity_start_rank"), ("peak_activity", "peak_activity_rank"), ("peak_delta", "peak_delta_rank"), ("final", "final_rank")):
        corr.append({"stage": stage, "spearman_vs_previous_wavelab": spearman([row["wavelab_rank"] for row in matrix], [row[key] for row in matrix])})

    summary = {"signal_date": prev_obj.get("signal_date"), "target_date": target, "previous_business_day": previous, "wavelab_source": str(FORWARD / f"{previous}.json"), "group_flow_source": str(out), "recalculated_wavelab": False, "peak_time": "14:40", "peak_density": {key: peak_density.get(key) for key in ("initial_30m", "continuation_30m", "total_30m")}, "top3": top3, "spearman": corr, "exploratory_label": "single-day stage comparison; no formal transition class"}
    fields_summary = list(group_summary[0])
    fields_matrix = list(matrix[0])
    fields_overlap = list(overlaps[0])
    out.mkdir(parents=True, exist_ok=True)
    write_csv(out / "transition_group_summary.csv", group_summary, fields_summary)
    write_csv(out / "transition_rank_matrix.csv", matrix, fields_matrix)
    write_csv(out / "transition_top3_overlap.csv", overlaps, fields_overlap)
    write_csv(out / "transition_rank_correlation.csv", corr, ["stage", "spearman_vs_previous_wavelab"])
    write_csv(out / "transition_timeline.csv", timeline, ["time", "minute", "activity_top3", "delta_top3", "activity_rank", "delta_rank"])
    (out / "transition_analysis_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "transition_analysis.html").write_text(build_html(summary, group_summary, overlaps, timeline), encoding="utf-8")
    print(json.dumps({"summary": summary, "group_summary": group_summary, "output": str(out)}, ensure_ascii=False, indent=2))
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default="20260829")
    parser.add_argument("--previous-date", default=None)
    args = parser.parse_args()
    analyze(args.date, args.previous_date)
