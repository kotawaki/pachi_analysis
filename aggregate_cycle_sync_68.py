"""
68台について、日足周期 x 当日中周期の噛み合いを横断集計する。

対象:
  35-77, 118-123, 148-158, 1173-1180

各台:
  - 日足周期: DFT上位5本
  - 当日中周期: 陽線絡みスコア上位3本
  - 検証期間のみで、日足合成波ゾーン別に日中hitあり/なしを比較
"""

from pathlib import Path
import math

import daily_intraday_cycle_sync as sync
import machine_cycle_positive as intraday


REPORT_DIR = Path(__file__).parent / "reports"
TARGET_RANGES = (
    range(35, 78),
    range(118, 124),
    range(148, 159),
    range(1173, 1181),
)
TARGET_MACHINES = tuple(f"{n:03d}" for r in TARGET_RANGES for n in r)


def pct(part, total):
    return part / total * 100 if total else 0.0


def mean(values):
    return sum(values) / len(values) if values else 0.0


def signed(value):
    return sync.signed(value)


def summarize(rows):
    return sync.summarize(rows)


def fmt_periods(periods):
    return "/".join(str(p) for p in periods)


def fmt_daily_periods(components):
    return ", ".join(f"{c['period']:.2f}" for c in components[:5])


def prepare_intraday_candidates(all_days):
    train_dates, valid_dates = intraday.split_dates(all_days)
    periods = tuple(range(20, 181, 10))
    train_stats, train_meta, train_counts = intraday.collect_stats(all_days, train_dates, periods, sync.TOLERANCE)
    valid_stats, valid_meta, valid_counts = intraday.collect_stats(all_days, valid_dates, periods, sync.TOLERANCE)
    train_rows = intraday.build_rows(train_stats, train_meta, train_counts, 20)
    valid_rows = intraday.build_rows(valid_stats, valid_meta, valid_counts, 20)
    valid_index = intraday.index_rows(valid_rows)
    by_machine = {}
    for order, row in enumerate(train_rows):
        valid = valid_index.get((row["machine"], row["period"]))
        if not valid:
            continue
        item = {
            "order": order,
            "period": row["period"],
            "train_lift": row["lift"],
            "valid_lift": valid["lift"],
            "valid_hits": valid["hits"],
            "valid_total": valid["total"],
            "hit_days": valid["hit_days"],
            "score": row["lift"] * math.log(row["hits"] + 1),
        }
        by_machine.setdefault(row["machine"], []).append(item)
    for rows in by_machine.values():
        rows.sort(key=lambda r: r["order"])
    return by_machine, train_dates, valid_dates


def build_daily_wave_from_days(machine, all_days, top_n):
    daily = []
    for (date, day_machine), item in all_days.items():
        if day_machine == machine:
            daily.append((date, item["final_close"]))
    daily.sort()
    dates = [date for date, _ in daily]
    values = [net for _, net in daily]
    avg = mean(values)
    centered = [value - avg for value in values]
    coeffs = sync.fourier.dft(centered)
    peaks = sync.fourier.select_peaks(coeffs, top_n)
    rows = []
    components = []
    n = len(values)
    for peak in peaks:
        k = peak["k"]
        phase_sin = math.atan2(coeffs[k].imag, coeffs[k].real) + math.pi / 2
        vals = [
            peak["amplitude"] * math.sin(2 * math.pi * k * t / n + phase_sin)
            for t in range(n)
        ]
        components.append({
            "k": k,
            "period": peak["period"],
            "amplitude": peak["amplitude"],
            "values": vals,
        })
    composite_values = [sum(c["values"][i] for c in components) for i in range(n)]
    composite_amp = max(abs(v) for v in composite_values) or 1
    primary_amp = components[0]["amplitude"] if components else 1
    for i, date in enumerate(dates):
        rows.append({
            "date": date,
            "net": values[i],
            "positive": values[i] > 0,
            "composite": composite_values[i],
            "composite_zone": sync.wave_zone(composite_values[i], composite_amp),
            "primary": components[0]["values"][i],
            "primary_zone": sync.wave_zone(components[0]["values"][i], primary_amp),
        })
    return rows, components


def attach_intraday_from_days(rows, machine, periods, all_days):
    by_date = {row["date"]: row for row in rows}
    for (date, day_machine), item in all_days.items():
        if day_machine != machine:
            continue
        row = by_date.get(date)
        if not row:
            continue
        hits = {
            period: sum(1 for gap in item["intervals"] if abs(gap - period) <= sync.TOLERANCE)
            for period in periods
        }
        row["events"] = item["event_count"]
        row["intraday_hits"] = hits
        row["intraday_hit_any"] = any(hits.values())
        row["intraday_distinct"] = sum(1 for count in hits.values() if count > 0)
    for row in rows:
        row.setdefault("events", 0)
        row.setdefault("intraday_hits", {period: 0 for period in periods})
        row.setdefault("intraday_hit_any", False)
        row.setdefault("intraday_distinct", 0)
    return rows


def zone_lift_summary(rows, zone):
    zone_rows = [row for row in rows if row["composite_zone"] == zone]
    hit_rows = [row for row in zone_rows if row["intraday_hit_any"]]
    no_rows = [row for row in zone_rows if not row["intraday_hit_any"]]
    hs = summarize(hit_rows)
    ns = summarize(no_rows)
    return zone_rows, hit_rows, no_rows, hs, ns


def analyze_machine(machine, all_days, candidates_by_machine, valid_dates):
    detected = candidates_by_machine.get(machine, [])[:3]
    periods = tuple(row["period"] for row in detected)
    if not periods:
        return None

    rows, components = build_daily_wave_from_days(machine, all_days, 5)
    rows = attach_intraday_from_days(rows, machine, periods, all_days)
    valid_rows = [row for row in rows if row["date"] in valid_dates]
    if not valid_rows:
        return None

    hit_rows = [row for row in valid_rows if row["intraday_hit_any"]]
    no_rows = [row for row in valid_rows if not row["intraday_hit_any"]]
    overall_hit = summarize(hit_rows)
    overall_no = summarize(no_rows)

    best = None
    zone_details = []
    for zone in ("trough", "lower-mid", "upper-mid", "peak"):
        zone_rows, zh, zn, hs, ns = zone_lift_summary(valid_rows, zone)
        if not zone_rows:
            continue
        hit_rate = pct(hs["pos"], hs["n"])
        no_rate = pct(ns["pos"], ns["n"])
        edge = hit_rate - no_rate
        med_edge = hs["med"] - ns["med"]
        detail = {
            "zone": zone,
            "zone_n": len(zone_rows),
            "hit_n": hs["n"],
            "no_n": ns["n"],
            "hit_pos_rate": hit_rate,
            "no_pos_rate": no_rate,
            "edge": edge,
            "hit_med": hs["med"],
            "no_med": ns["med"],
            "med_edge": med_edge,
        }
        zone_details.append(detail)
        if hs["n"] >= 8 and ns["n"] >= 8:
            score = edge + min(max(med_edge, -10000), 20000) / 1000
            if best is None or score > best["score"]:
                best = {**detail, "score": score}

    if best is None:
        candidates = [d for d in zone_details if d["hit_n"] >= 5 and d["no_n"] >= 5]
        if candidates:
            best = max(
                candidates,
                key=lambda d: (d["edge"], d["med_edge"], d["hit_n"]),
            )
            best = {**best, "score": best["edge"] + best["med_edge"] / 1000}

    return {
        "machine": machine,
        "periods": periods,
        "detected": detected,
        "daily_periods": tuple(c["period"] for c in components[:5]),
        "daily_components": components,
        "valid_n": len(valid_rows),
        "hit_n": overall_hit["n"],
        "no_n": overall_no["n"],
        "hit_pos_rate": pct(overall_hit["pos"], overall_hit["n"]),
        "no_pos_rate": pct(overall_no["pos"], overall_no["n"]),
        "edge": pct(overall_hit["pos"], overall_hit["n"]) - pct(overall_no["pos"], overall_no["n"]),
        "hit_med": overall_hit["med"],
        "no_med": overall_no["med"],
        "med_edge": overall_hit["med"] - overall_no["med"],
        "best": best,
        "zones": zone_details,
    }


def quality(row):
    best = row["best"]
    if not best:
        return "C"
    if best["hit_n"] >= 10 and best["edge"] >= 35 and best["hit_med"] > 0 and best["no_med"] < 0:
        return "A"
    if best["hit_n"] >= 8 and best["edge"] >= 25 and best["hit_med"] > 0:
        return "B"
    if row["edge"] >= 20 and row["hit_med"] > 0:
        return "C+"
    return "C"


def make_report(results):
    ranked = sorted(
        results,
        key=lambda r: (
            {"A": 3, "B": 2, "C+": 1, "C": 0}[quality(r)],
            r["best"]["edge"] if r["best"] else -999,
            r["best"]["med_edge"] if r["best"] else -999999,
            r["edge"],
        ),
        reverse=True,
    )

    lines = [
        "# 68台 日足周期 x 当日中周期 横断検証",
        "",
        "- 対象: 35-77, 118-123, 148-158, 1173-1180",
        "- 日足周期: 各台の日次終値差玉DFT上位5本",
        "- 当日中周期: 各台の陽線絡み上位3本",
        "- 判定: 検証期間のみで、日足合成波ゾーン別に日中hitあり/なしを比較",
        "",
        "## 上位候補",
        "",
        "|評価|台|日中周期|日足周期上位5|全体 hit/なし|全体中央値|最良ゾーン|ゾーン hit/なし|ゾーン中央値|",
        "|---|---:|---|---|---:|---:|---|---:|---:|",
    ]
    for row in ranked:
        q = quality(row)
        if q == "C":
            continue
        best = row["best"]
        lines.append(
            f"|{q}|{int(row['machine'])}|{fmt_periods(row['periods'])}分|"
            f"{', '.join(f'{p:.2f}' for p in row['daily_periods'])}|"
            f"{row['hit_pos_rate']:.1f}%/{row['no_pos_rate']:.1f}%|"
            f"{signed(row['hit_med'])}/{signed(row['no_med'])}|"
            f"{best['zone']}|{best['hit_pos_rate']:.1f}%/{best['no_pos_rate']:.1f}%|"
            f"{signed(best['hit_med'])}/{signed(best['no_med'])}|"
        )

    lines.extend([
        "",
        "## 全台一覧",
        "",
        "|台|評価|日中周期|全体hit日|全体hit陽線率|全体no-hit陽線率|全体中央値差|最良ゾーン|ゾーンhit日|ゾーン陽線率差|ゾーン中央値差|",
        "|---:|---|---|---:|---:|---:|---:|---|---:|---:|---:|",
    ])
    for row in ranked:
        best = row["best"]
        if best:
            best_zone = best["zone"]
            best_hit_n = str(best["hit_n"])
            best_edge = f"{best['edge']:.1f}%"
            best_med_edge = signed(best["med_edge"])
        else:
            best_zone = "-"
            best_hit_n = "-"
            best_edge = "-"
            best_med_edge = "-"
        lines.append(
            f"|{int(row['machine'])}|{quality(row)}|{fmt_periods(row['periods'])}分|"
            f"{row['hit_n']}|{row['hit_pos_rate']:.1f}%|{row['no_pos_rate']:.1f}%|"
            f"{signed(row['med_edge'])}|{best_zone}|{best_hit_n}|{best_edge}|{best_med_edge}|"
        )

    return "\n".join(lines) + "\n"


def main():
    all_days = intraday.load_machine_days(set(TARGET_MACHINES))
    candidates_by_machine, _, valid_dates = prepare_intraday_candidates(all_days)
    results = []
    for machine in TARGET_MACHINES:
        result = analyze_machine(machine, all_days, candidates_by_machine, valid_dates)
        if result:
            results.append(result)
            print(
                f"{int(machine):>4}: periods={fmt_periods(result['periods'])} "
                f"edge={result['edge']:.1f}% best={result['best']['zone'] if result['best'] else '-'}"
            )
    out = REPORT_DIR / "cycle_sync_68_summary.md"
    out.write_text(make_report(results), encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
