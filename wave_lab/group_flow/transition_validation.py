"""Multi-day validation wrapper for the fixed Wave Lab -> Group Flow stages.

This deliberately consumes existing per-day transition artifacts. It does not
recalculate Wave Lab or rewrite the existing 20260829 transition output.
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FORWARD = ROOT / "docs" / "wave_lab" / "data" / "forward"
FLOW = ROOT / "wave_lab" / "group_flow" / "output"
OUT = ROOT / "wave_lab" / "group_flow" / "transition_validation"
GROUPS = [f"g{i}" for i in range(1, 10)]
STAGES = [("Morning", "Morning", "morning_rank"), ("Activity Start", "ActivityStart", "activity_start_rank"), ("Peak Activity", "PeakActivity", "peak_activity_rank"), ("Peak Delta", "PeakDelta", "peak_delta_rank"), ("Final", "Final", "final_rank")]


def rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write(path: Path, data: list[dict], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        out = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        out.writeheader()
        out.writerows(data)


def n(value):
    return float(value) if value not in (None, "") and "." in str(value) else (int(value) if value not in (None, "") else None)


def mean(values):
    return round(sum(values) / len(values), 4) if values else None


def median(values):
    if not values:
        return None
    values = sorted(values)
    mid = len(values) // 2
    return round(values[mid], 4) if len(values) % 2 else round((values[mid - 1] + values[mid]) / 2, 4)


def spearman(a: list[float], b: list[float]) -> float | None:
    if len(a) < 2:
        return None
    ma, mb = mean(a), mean(b)
    da, db = sum((x - ma) ** 2 for x in a), sum((y - mb) ** 2 for y in b)
    return round(sum((x - ma) * (y - mb) for x, y in zip(a, b)) / math.sqrt(da * db), 4) if da and db else None


def previous_forward(target: str) -> str | None:
    dates = sorted(path.stem for path in FORWARD.glob("*.json") if path.stem.isdigit() and path.stem < target)
    return dates[-1] if dates else None


def candidate_dates() -> list[str]:
    return sorted(path.stem for path in FORWARD.glob("*.json") if path.stem.isdigit())


def load_day(target: str, previous: str) -> dict:
    out = FLOW / target
    group_summary = rows(out / "transition_group_summary.csv")
    matrix = rows(out / "transition_rank_matrix.csv")
    overlaps = {row["stage"]: row for row in rows(out / "transition_top3_overlap.csv")}
    correlations = {row["stage"].strip().lower().replace(" ", "_"): n(row["spearman_vs_previous_wavelab"]) for row in rows(out / "transition_rank_correlation.csv")}
    prev_obj = json.loads((FORWARD / f"{previous}.json").read_text(encoding="utf-8"))
    wtop = [row["group"] for row in sorted(prev_obj["group_signals"], key=lambda row: int(row["group_signal_rank"]))[:3]]
    final_rows = sorted(matrix, key=lambda row: int(row["final_rank"]))
    final_top3 = [row["group"] for row in final_rows[:3]]
    top3_final_ranks = [int(next(row["final_rank"] for row in matrix if row["group"] == group)) for group in wtop]
    day = {"target_date": target, "previous_business_date": previous, "wavelab_top3": ",".join(wtop), "morning_top3": ",".join(next(row["group"] for row in matrix if int(row["morning_rank"]) == rank) for rank in range(1, 4)), "activity_start_top3": ",".join(next(row["group"] for row in matrix if int(row["activity_start_rank"]) == rank) for rank in range(1, 4)), "peak_activity_top3": ",".join(next(row["group"] for row in matrix if int(row["peak_activity_rank"]) == rank) for rank in range(1, 4)), "peak_delta_top3": ",".join(next(row["group"] for row in matrix if int(row["peak_delta_rank"]) == rank) for rank in range(1, 4)), "final_top3": ",".join(final_top3), "wavelab_rank1_group": wtop[0], "wavelab_rank1_final_rank": top3_final_ranks[0], "wavelab_top3_final_rank_mean": mean(top3_final_ranks)}
    for stage, overlap_key, _rank_key in STAGES:
        day[f"{stage.lower().replace(' ', '_')}_overlap_count"] = int(overlaps[overlap_key]["overlap_count"])
        day[f"spearman_{stage.lower().replace(' ', '_')}"] = correlations.get(stage.lower().replace(" ", "_"))
    day["final_overlap_direction"] = "FINAL_OVERLAP_UP" if day["final_overlap_count"] > day["morning_overlap_count"] else ("FINAL_OVERLAP_DOWN" if day["final_overlap_count"] < day["morning_overlap_count"] else "FINAL_OVERLAP_SAME")
    rank1_groups = [next(row["group"] for row in matrix if int(row[key]) == 1) for _stage, _overlap, key in STAGES]
    day["morning_rank1_group"], day["peak_activity_rank1_group"], day["peak_delta_rank1_group"], day["final_rank1_group"] = rank1_groups[0], rank1_groups[2], rank1_groups[3], rank1_groups[4]
    day["rank1_same_all_stages"] = len(set([wtop[0], *rank1_groups])) == 1
    day["rank1_group_changed"] = not day["rank1_same_all_stages"]
    day["source_transition_output"] = str(out)
    return day


def html_report(summary: dict, overlap: list[dict], corr: list[dict], daily: list[dict], ranks: list[dict]) -> str:
    ov = "".join(f"<tr><td>{r['stage']}</td><td>{r['days']}</td><td>{r['mean_overlap']}</td><td>{r['median_overlap']}</td><td>{r['overlap_at_least_1_rate']}</td><td>{r['overlap_at_least_2_rate']}</td><td>{r['full_overlap_rate']}</td></tr>" for r in overlap)
    co = "".join(f"<tr><td>{r['stage']}</td><td>{r['days']}</td><td>{r['mean']}</td><td>{r['median']}</td><td>{r['positive_days']}</td><td>{r['negative_days']}</td><td>{r['zero_days']}</td></tr>" for r in corr)
    ds = "".join(f"<tr><td>{r['target_date']}</td><td>{r['wavelab_top3']}</td><td>{r['morning_top3']}</td><td>{r['peak_activity_top3']}</td><td>{r['peak_delta_top3']}</td><td>{r['final_top3']}</td></tr>" for r in daily)
    rs = "".join(f"<tr><td>{r['wavelab_rank']}</td><td>{r['samples']}</td><td>{r['mean_final_rank']}</td><td>{r['median_final_rank']}</td><td>{r['final_top3_rate']}</td></tr>" for r in ranks)
    return f"""<!doctype html><html lang='ja'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Transition Validation</title><style>body{{font-family:system-ui,sans-serif;background:#0f172a;color:#e5e7eb;margin:20px}}h1,h2{{color:#93c5fd}}.note{{background:#172033;border-left:4px solid #60a5fa;padding:12px;line-height:1.6}}table{{border-collapse:collapse;margin:12px 0 28px;font-size:13px}}th,td{{border:1px solid #374151;padding:6px 9px;text-align:right}}th{{background:#1f2937}}td:first-child,th:first-child{{text-align:left}}pre{{background:#111827;padding:12px;white-space:pre-wrap}}</style></head><body><h1>Wave Lab → Group Flow Transition Validation</h1><div class='note'>20260829版の定義・順位・tie rule・Top3 overlap・Spearmanを固定し、既存日次outputを集計した探索検証です。因果関係や予測性能は判定しません。</div><h2>Overview</h2><pre>{html.escape(json.dumps(summary,ensure_ascii=False,indent=2))}</pre><h2>Stage Top3 Overlap</h2><table><tr><th>stage</th><th>days</th><th>mean</th><th>median</th><th>>=1</th><th>>=2</th><th>=3</th></tr>{ov}</table><h2>Spearman</h2><table><tr><th>stage</th><th>days</th><th>mean</th><th>median</th><th>positive</th><th>negative</th><th>zero</th></tr>{co}</table><h2>WaveLab Rank → Final Rank</h2><table><tr><th>rank</th><th>samples</th><th>mean final</th><th>median final</th><th>final top3</th></tr>{rs}</table><h2>Daily Transition</h2><table><tr><th>date</th><th>WaveLab Top3</th><th>Morning</th><th>Peak Activity</th><th>Peak Delta</th><th>Final</th></tr>{ds}</table></body></html>"""


def analyze() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    daily, skipped = [], []
    for target in candidate_dates():
        previous = previous_forward(target)
        reasons = []
        if previous is None:
            reasons.append("previous business-day Wave Lab forward JSON missing")
        out = FLOW / target
        for required in ("transition_group_summary.csv", "transition_rank_matrix.csv", "transition_top3_overlap.csv", "transition_rank_correlation.csv"):
            if not (out / required).exists():
                reasons.append(f"existing transition output missing: {required}")
        if reasons:
            skipped.append({"target_date": target, "previous_business_day": previous or "", "reason": "; ".join(reasons)})
        else:
            daily.append(load_day(target, previous))
    write(OUT / "daily_transition_summary.csv", daily, list(daily[0]) if daily else ["target_date", "previous_business_date"])
    write(OUT / "transition_validation_skips.csv", skipped, ["target_date", "previous_business_day", "reason"])
    overlap_rows = []
    for stage, _overlap_key, _rank_key in STAGES:
        day_key = f"{stage.lower().replace(' ', '_')}_overlap_count"
        values = [row[day_key] for row in daily]
        overlap_rows.append({"stage": stage, "days": len(values), "mean_overlap": mean(values), "median_overlap": median(values), "overlap_0_days": values.count(0), "overlap_1_days": values.count(1), "overlap_2_days": values.count(2), "overlap_3_days": values.count(3), "overlap_at_least_1_rate": round(sum(value >= 1 for value in values) / len(values), 4) if values else None, "overlap_at_least_2_rate": round(sum(value >= 2 for value in values) / len(values), 4) if values else None, "full_overlap_rate": round(sum(value == 3 for value in values) / len(values), 4) if values else None})
    write(OUT / "stage_overlap_summary.csv", overlap_rows, list(overlap_rows[0]) if overlap_rows else ["stage"])
    corr_rows = []
    for stage, _overlap_key, _rank_key in STAGES:
        key = f"spearman_{stage.lower().replace(' ', '_')}"
        values = [row[key] for row in daily if row[key] is not None]
        corr_rows.append({"stage": stage, "days": len(values), "mean": mean(values), "median": median(values), "min": min(values) if values else None, "max": max(values) if values else None, "positive_days": sum(value > 0 for value in values), "negative_days": sum(value < 0 for value in values), "zero_days": sum(value == 0 for value in values)})
    write(OUT / "stage_rank_correlation_summary.csv", corr_rows, list(corr_rows[0]) if corr_rows else ["stage"])
    rank_rows = []
    for rank in range(1, 10):
        values = []
        for day in daily:
            matrix = rows(FLOW / day["target_date"] / "transition_rank_matrix.csv")
            group = next(row["group"] for row in matrix if int(row["wavelab_rank"]) == rank)
            values.append(int(next(row["final_rank"] for row in matrix if row["group"] == group)))
        rank_rows.append({"wavelab_rank": rank, "samples": len(values), "mean_final_rank": mean(values), "median_final_rank": median(values), "final_top1_rate": round(sum(value == 1 for value in values) / len(values), 4) if values else None, "final_top3_rate": round(sum(value <= 3 for value in values) / len(values), 4) if values else None, "final_top5_rate": round(sum(value <= 5 for value in values) / len(values), 4) if values else None})
    write(OUT / "wavelab_top3_final_distribution.csv", rank_rows, list(rank_rows[0]))
    random_baseline = {"group_count": 9, "top_n": 3, "expected_overlap": 1.0, "P(overlap=0)": 20 / 84, "P(overlap=1)": 45 / 84, "P(overlap=2)": 18 / 84, "P(overlap=3)": 1 / 84}
    summary = {"candidate_dates": candidate_dates(), "analyzed_dates": [row["target_date"] for row in daily], "skipped_dates": skipped, "analyzed_day_count": len(daily), "skipped_day_count": len(skipped), "random_top3_baseline": random_baseline, "final_overlap_direction_counts": dict(Counter(row["final_overlap_direction"] for row in daily)), "rank1_same_all_stages_days": sum(row["rank1_same_all_stages"] for row in daily), "rank1_changed_days": sum(row["rank1_group_changed"] for row in daily), "definition_source": "20260829 analyze_transition.py and existing transition artifacts", "definition_changed": False, "threshold_optimized": False, "20260829_reference_included": "20260829" in [row["target_date"] for row in daily]}
    (OUT / "transition_validation_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "transition_validation.html").write_text(html_report(summary, overlap_rows, corr_rows, daily, rank_rows), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.parse_args()
    analyze()
