"""
指定台の日足フーリエ周期と当日中の当たり間隔周期が噛み合うかを検証する。

日足側:
  machine_fourier.py と同じ DFT で日次終値差玉の上位周期を抽出し、
  各日について合成波・主要波の位置を trough/mid/rise/peak に分類する。

当日中側:
  指定した当たり開始間隔 (±5分) の hit を使う。
"""

import argparse
import math
from pathlib import Path

import machine_cycle_positive as intraday
import machine_fourier as fourier


ROOT = Path(__file__).parent
REPORT_DIR = ROOT / "reports"

TOLERANCE = 5
TOP_N = 5


def median(values):
    values = sorted(values)
    if not values:
        return 0
    mid = len(values) // 2
    if len(values) % 2:
        return int(round(values[mid]))
    return int(round((values[mid - 1] + values[mid]) / 2))


def mean(values):
    return sum(values) / len(values) if values else 0.0


def pct(part, total):
    return part / total * 100 if total else 0.0


def signed(value):
    return f"{int(round(value)):+,}"


def wave_zone(value, amplitude):
    if amplitude <= 0:
        return "mid"
    ratio = value / amplitude
    if ratio >= 0.5:
        return "peak"
    if ratio <= -0.5:
        return "trough"
    if value >= 0:
        return "upper-mid"
    return "lower-mid"


def build_daily_wave(machine, top_n):
    daily = fourier.load_daily_net(machine)
    dates = [date for date, _, _, _ in daily]
    values = [net for _, net, _, _ in daily]
    avg = mean(values)
    centered = [value - avg for value in values]
    coeffs = fourier.dft(centered)
    peaks = fourier.select_peaks(coeffs, top_n)
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
            "composite_zone": wave_zone(composite_values[i], composite_amp),
            "primary": components[0]["values"][i],
            "primary_zone": wave_zone(components[0]["values"][i], primary_amp),
        })
    return rows, components


def attach_intraday(rows, machine, periods, tolerance):
    days = intraday.load_machine_days({machine})
    by_date = {row["date"]: row for row in rows}
    for item in days.values():
        row = by_date.get(item["date"])
        if not row:
            continue
        hits = {
            period: sum(1 for gap in item["intervals"] if abs(gap - period) <= tolerance)
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


def summarize(rows):
    return {
        "n": len(rows),
        "pos": sum(1 for row in rows if row["positive"]),
        "med": median([row["net"] for row in rows]),
        "avg": mean([row["net"] for row in rows]),
        "events": mean([row["events"] for row in rows]),
    }


def summary_line(label, rows):
    s = summarize(rows)
    if not rows:
        return f"|{label}|0|-|-|-|-|"
    return (
        f"|{label}|{s['n']}|{s['pos']}/{s['n']} ({pct(s['pos'], s['n']):.1f}%)|"
        f"{signed(s['med'])}|{signed(s['avg'])}|{s['events']:.2f}|"
    )


def table_by_zone(rows, zone_key):
    lines = [
        "|日足ゾーン|全日|日中hitあり|日中hitなし|hitあり中央値|hitなし中央値|",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for zone in ("trough", "lower-mid", "upper-mid", "peak"):
        zone_rows = [row for row in rows if row[zone_key] == zone]
        hit_rows = [row for row in zone_rows if row["intraday_hit_any"]]
        no_rows = [row for row in zone_rows if not row["intraday_hit_any"]]
        zs = summarize(zone_rows)
        hs = summarize(hit_rows)
        ns = summarize(no_rows)
        lines.append(
            f"|{zone}|{zs['pos']}/{zs['n']} ({pct(zs['pos'], zs['n']):.1f}%)|"
            f"{hs['pos']}/{hs['n']} ({pct(hs['pos'], hs['n']):.1f}%)|"
            f"{ns['pos']}/{ns['n']} ({pct(ns['pos'], ns['n']):.1f}%)|"
            f"{signed(hs['med']) if hit_rows else '-'}|{signed(ns['med']) if no_rows else '-'}|"
        )
    return "\n".join(lines)


def synergy_table(rows, zone_key):
    lines = [
        "|日足ゾーン|日中周期数|日数|陽線率|中央値|平均|平均当り数|",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for zone in ("trough", "lower-mid", "upper-mid", "peak"):
        for count in range(0, 4):
            sub = [row for row in rows if row[zone_key] == zone and row["intraday_distinct"] == count]
            if len(sub) < 3:
                continue
            s = summarize(sub)
            lines.append(
                f"|{zone}|{count}|{s['n']}|{s['pos']}/{s['n']} ({pct(s['pos'], s['n']):.1f}%)|"
                f"{signed(s['med'])}|{signed(s['avg'])}|{s['events']:.2f}|"
            )
    return "\n".join(lines)


def period_by_zone_table(rows, zone_key, periods):
    lines = [
        "|周期|日足ゾーン|hit日|hit陽線率|hit中央値|no-hit陽線率|no-hit中央値|",
        "|---:|---|---:|---:|---:|---:|---:|",
    ]
    for period in periods:
        for zone in ("trough", "lower-mid", "upper-mid", "peak"):
            zone_rows = [row for row in rows if row[zone_key] == zone]
            hit_rows = [row for row in zone_rows if row["intraday_hits"][period] > 0]
            no_rows = [row for row in zone_rows if row["intraday_hits"][period] == 0]
            if len(hit_rows) < 3:
                continue
            hs = summarize(hit_rows)
            ns = summarize(no_rows)
            lines.append(
                f"|{period}分|{zone}|{hs['n']}|{hs['pos']}/{hs['n']} ({pct(hs['pos'], hs['n']):.1f}%)|"
                f"{signed(hs['med'])}|{ns['pos']}/{ns['n']} ({pct(ns['pos'], ns['n']):.1f}%)|"
                f"{signed(ns['med']) if no_rows else '-'}|"
            )
    return "\n".join(lines)


def recent_examples(rows):
    lines = [
        "|日付|日足合成ゾーン|17.48日ゾーン|日中hit|当り数|日足差玉|",
        "|---:|---|---|---|---:|---:|",
    ]
    for row in rows[-30:]:
        hit = ",".join(f"{p}分" for p, c in row["intraday_hits"].items() if c) or "-"
        lines.append(
            f"|{row['date']}|{row['composite_zone']}|{row['primary_zone']}|{hit}|"
            f"{row['events']}|{signed(row['net'])}|"
        )
    return "\n".join(lines)


def parse_periods(value):
    return tuple(int(part.strip()) for part in value.split(",") if part.strip())


def detect_top_intraday_periods(machine, top_n, tolerance):
    days = intraday.load_machine_days({machine})
    train_dates, valid_dates = intraday.split_dates(days)
    periods = tuple(range(20, 181, 10))
    train_stats, train_meta, train_counts = intraday.collect_stats(days, train_dates, periods, tolerance)
    valid_stats, valid_meta, valid_counts = intraday.collect_stats(days, valid_dates, periods, tolerance)
    train_rows = intraday.build_rows(train_stats, train_meta, train_counts, 20)
    valid_index = intraday.index_rows(intraday.build_rows(valid_stats, valid_meta, valid_counts, 20))
    rows = []
    for row in train_rows:
        valid = valid_index.get((row["machine"], row["period"]))
        if not valid:
            continue
        rows.append({
            "period": row["period"],
            "train_lift": row["lift"],
            "valid_lift": valid["lift"],
            "valid_hits": valid["hits"],
            "valid_total": valid["total"],
            "hit_days": valid["hit_days"],
            "score": valid["lift"] * math.log(valid["hits"] + 1),
        })
    rows.sort(key=lambda r: (r["score"], r["valid_lift"], r["valid_hits"]), reverse=True)
    return rows[:top_n], train_dates, valid_dates


def main():
    parser = argparse.ArgumentParser(description="日足周期 x 当日中周期の噛み合い検証")
    parser.add_argument("--machine", default="039", help="台番号")
    parser.add_argument("--periods", default=None, help="当日中周期。例: 50,60,70。未指定なら上位3本を自動採用")
    parser.add_argument("--top-intraday", type=int, default=3, help="自動採用する当日中周期数")
    parser.add_argument("--top-daily", type=int, default=TOP_N, help="日足フーリエ上位数")
    parser.add_argument("--tolerance", type=int, default=TOLERANCE, help="当日中周期hitの許容幅")
    parser.add_argument("--out", default=None, help="レポート出力先")
    args = parser.parse_args()

    machine = str(args.machine).zfill(3)
    detected, train_dates, valid_dates = detect_top_intraday_periods(machine, args.top_intraday, args.tolerance)
    periods = parse_periods(args.periods) if args.periods else tuple(row["period"] for row in detected)

    rows, components = build_daily_wave(machine, args.top_daily)
    rows = attach_intraday(rows, machine, periods, args.tolerance)
    cycle_days = intraday.load_machine_days({machine})
    _, valid_dates = intraday.split_dates(cycle_days)
    valid_rows = [row for row in rows if row["date"] in valid_dates]

    sections = [
        f"# {int(machine)}番 日足周期 x 当日中周期の噛み合い検証",
        "",
        f"- 対象: {int(machine)}番",
        f"- 検証期間: {min(valid_dates)} ～ {max(valid_dates)} ({len(valid_dates)}日)",
        f"- 日足周期: 日次終値差玉のDFT上位{args.top_daily}本",
        f"- 当日中周期: 当たり開始間隔 {'/'.join(str(p) for p in periods)}分 ±{args.tolerance}分",
        "",
        "## 当日中周期 採用候補",
        "",
        "|rank|周期|学習lift|検証lift|検証hit|hit日|",
        "|---:|---:|---:|---:|---:|---:|",
    ]
    for i, row in enumerate(detected, 1):
        mark = "*" if row["period"] in periods else ""
        sections.append(
            f"|{i}{mark}|{row['period']}分|{row['train_lift']:.2f}|{row['valid_lift']:.2f}|"
            f"{row['valid_hits']}/{row['valid_total']}|{row['hit_days']}|"
        )
    sections.extend([
        "",
        "## 日足フーリエ上位",
        "",
        "|rank|周期(営業日)|振幅|",
        "|---:|---:|---:|",
    ])
    for i, comp in enumerate(components, 1):
        sections.append(f"|{i}|{comp['period']:.2f}|{comp['amplitude']:.1f}|")

    hit_rows = [row for row in valid_rows if row["intraday_hit_any"]]
    no_rows = [row for row in valid_rows if not row["intraday_hit_any"]]
    sections.extend([
        "",
        "## 全体比較",
        "",
        "|条件|日数|陽線率|中央値|平均|平均当り数|",
        "|---|---:|---:|---:|---:|---:|",
        summary_line(f"日中{'/'.join(str(p) for p in periods)}分いずれかhit", hit_rows),
        summary_line("日中hitなし", no_rows),
        "",
        "## 日足合成波ゾーン別",
        "",
        table_by_zone(valid_rows, "composite_zone"),
        "",
        f"## {components[0]['period']:.2f}日主周期ゾーン別",
        "",
        table_by_zone(valid_rows, "primary_zone"),
        "",
        "## 噛み合い: 合成波ゾーン x 日中hit周期数",
        "",
        synergy_table(valid_rows, "composite_zone"),
        "",
        "## 周期別 x 合成波ゾーン",
        "",
        period_by_zone_table(valid_rows, "composite_zone", periods),
        "",
        "## 直近30日",
        "",
        recent_examples(rows),
        "",
    ])

    out = Path(args.out) if args.out else REPORT_DIR / f"machine{int(machine):03d}_daily_intraday_cycle_sync.md"
    out.write_text("\n".join(sections), encoding="utf-8")
    print(out)
    print("\n".join(sections[:40]))


if __name__ == "__main__":
    main()
