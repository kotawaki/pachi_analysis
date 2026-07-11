from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path

import combined_signal_analysis as combined


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
REPORT_DIR = ROOT / "reports"
DOCS_DIR = ROOT / "docs"
SPECIAL_DAYS = {1, 3, 9, 13, 19, 23, 29}


def pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def signed(value: float) -> str:
    return f"{value:+.3f}"


def correlation(left: list[float], right: list[float]) -> float:
    if len(left) < 2 or len(left) != len(right):
        return 0.0
    mean_left = statistics.mean(left)
    mean_right = statistics.mean(right)
    numerator = sum(
        (x - mean_left) * (y - mean_right) for x, y in zip(left, right)
    )
    denominator = math.sqrt(
        sum((x - mean_left) ** 2 for x in left)
        * sum((y - mean_right) ** 2 for y in right)
    )
    return numerator / denominator if denominator else 0.0


def autocorrelations(values: list[float], max_lag: int = 10) -> list[dict[str, float]]:
    return [
        {"lag": lag, "value": correlation(values[:-lag], values[lag:])}
        for lag in range(1, min(max_lag, len(values) - 2) + 1)
    ]


def period_power(values: list[float], periods: tuple[int, ...]) -> list[dict[str, float]]:
    if not values:
        return []
    mean = statistics.mean(values)
    centered = [value - mean for value in values]
    results = []
    for period in periods:
        real = sum(
            value * math.cos(2 * math.pi * index / period)
            for index, value in enumerate(centered)
        )
        imag = sum(
            value * math.sin(2 * math.pi * index / period)
            for index, value in enumerate(centered)
        )
        results.append({"period": period, "power": real * real + imag * imag})
    return sorted(results, key=lambda item: item["power"], reverse=True)


def frozen_daytime(date: str) -> dict | None:
    path = DATA_DIR / f"daytime_hits_{date}.json"
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not payload.get("machines"):
        return None
    return payload


def build_rows(start: str, end: str, period_report: Path) -> list[dict]:
    rows = combined.load_prediction_rows(start, end, True, "asof")
    periods = combined.load_intraday_periods(period_report)
    combined.build_intraday_hits(rows, periods, 5)

    by_date: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_date[row["date"]].append(row)

    for date, date_rows in by_date.items():
        frozen = frozen_daytime(date)
        if not frozen:
            for row in date_rows:
                row["hit_source"] = "structural_backfill"
            continue
        hit_machines = {str(int(machine)) for machine in frozen.get("hits", [])}
        machine_data = frozen.get("machines", {})
        for row in date_rows:
            display = str(int(row["machine"]))
            row["intraday_hit"] = display in hit_machines
            row["event_count"] = int(
                machine_data.get(display, {}).get("event_count", row.get("event_count", 0))
            )
            row["hit_source"] = "frozen_daytime"
    return rows


def summarize_days(rows: list[dict], prior_strength: int) -> tuple[list[dict], dict]:
    by_date: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_date[row["date"]].append(row)

    all_hits = [row for row in rows if row["intraday_hit"]]
    prior_rate = (
        sum(row["positive"] for row in all_hits) / len(all_hits) if all_hits else 0.5
    )
    days = []
    for date, date_rows in sorted(by_date.items()):
        eligible = [row for row in date_rows if row.get("event_count", 0) >= 2]
        hits = [row for row in date_rows if row["intraday_hit"]]
        no_hits = [row for row in date_rows if not row["intraday_hit"]]
        hit_positive = sum(row["positive"] for row in hits)
        no_hit_positive = sum(row["positive"] for row in no_hits)
        raw_quality = hit_positive / len(hits) if hits else 0.0
        adjusted_quality = (
            (hit_positive + prior_rate * prior_strength) / (len(hits) + prior_strength)
        )
        days.append({
            "date": date,
            "special": int(date[6:8]) in SPECIAL_DAYS,
            "source": (
                "frozen_daytime"
                if any(row.get("hit_source") == "frozen_daytime" for row in date_rows)
                else "structural_backfill"
            ),
            "eligible": len(eligible),
            "activity_rate": len(eligible) / len(date_rows) if date_rows else 0.0,
            "hits": len(hits),
            "hit_density": len(hits) / len(eligible) if eligible else 0.0,
            "hit_positive": hit_positive,
            "hit_quality": raw_quality,
            "adjusted_quality": adjusted_quality,
            "no_hit_positive": no_hit_positive,
            "no_hit_count": len(no_hits),
            "no_hit_quality": no_hit_positive / len(no_hits) if no_hits else 0.0,
            "overall_quality": sum(row["positive"] for row in date_rows) / len(date_rows),
        })

    density_median = statistics.median(day["hit_density"] for day in days)
    activity_q1 = statistics.quantiles(
        [day["activity_rate"] for day in days], n=4, method="inclusive"
    )[0]
    for day in days:
        high_density = day["hit_density"] >= density_median
        high_quality = day["adjusted_quality"] >= prior_rate
        if high_density and high_quality:
            regime = "強い拡散"
        elif high_density:
            regime = "弱い拡散"
        elif high_quality:
            regime = "集中"
        else:
            regime = "休止"
        day["regime"] = regime
        day["low_activity"] = day["activity_rate"] <= activity_q1

    density = [day["hit_density"] for day in days]
    quality = [day["hit_quality"] for day in days]
    adjusted = [day["adjusted_quality"] for day in days]
    metrics = {
        "prior_rate": prior_rate,
        "prior_strength": prior_strength,
        "density_median": density_median,
        "activity_q1": activity_q1,
        "density_quality_correlation": correlation(density, quality),
        "density_adjusted_quality_correlation": correlation(density, adjusted),
        "density_acf": autocorrelations(density),
        "quality_acf": autocorrelations(adjusted),
        "density_periods": period_power(density, (2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 14)),
        "quality_periods": period_power(adjusted, (2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 14)),
    }
    return days, metrics


def make_report(days: list[dict], metrics: dict, start: str, end: str) -> str:
    frozen_count = sum(day["source"] == "frozen_daytime" for day in days)
    lines = [
        "# 日中周期hit 日次レジーム追跡",
        "",
        f"- 期間: {start} ～ {end} ({len(days)}日)",
        f"- 判定固定済み: {frozen_count}日 / 構造再計算: {len(days) - frozen_count}日",
        f"- 全期間hit台陽線率: {pct(metrics['prior_rate'])}",
        f"- hit密度中央値: {pct(metrics['density_median'])}",
        f"- hit密度と生陽線率の相関: {signed(metrics['density_quality_correlation'])}",
        f"- hit密度と補正陽線率の相関: {signed(metrics['density_adjusted_quality_correlation'])}",
        "",
        "補正陽線率は全期間hit台陽線率を10台分の事前値として加え、少数日の振れを抑えています。",
        "",
        "|日付|区分|データ|判定可能|hit|hit密度|hit陽線|生陽線率|補正陽線率|非hit陽線率|状態|",
        "|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for day in reversed(days):
        lines.append(
            f"|{day['date']}|{'特日' if day['special'] else '通常'}|"
            f"{'固定' if day['source'] == 'frozen_daytime' else '再計算'}|"
            f"{day['eligible']}|{day['hits']}|{pct(day['hit_density'])}|"
            f"{day['hit_positive']}/{day['hits']}|{pct(day['hit_quality'])}|"
            f"{pct(day['adjusted_quality'])}|{pct(day['no_hit_quality'])}|"
            f"{day['regime']}{'・低稼働' if day['low_activity'] else ''}|"
        )
    lines.extend([
        "",
        "## 周期候補（探索値）",
        "",
        "|系列|候補周期|強度|",
        "|---|---:|---:|",
    ])
    for label, key in (("hit密度", "density_periods"), ("補正陽線率", "quality_periods")):
        for item in metrics[key][:3]:
            lines.append(f"|{label}|{item['period']}日|{item['power']:.4f}|")
    lines.extend([
        "",
        "> 周期候補は日数が少ない探索値です。判定固定日の蓄積後に再評価します。",
    ])
    return "\n".join(lines) + "\n"


def make_html(days: list[dict], metrics: dict, start: str, end: str) -> str:
    regime_class = {
        "強い拡散": "strong",
        "弱い拡散": "diffuse",
        "集中": "focus",
        "休止": "rest",
    }
    rows = []
    for day in reversed(days):
        rows.append(f"""
        <tr>
          <td><b>{day['date']}</b><small>{'特日' if day['special'] else '通常日'}</small></td>
          <td><span class="source {'fixed' if day['source'] == 'frozen_daytime' else ''}">{'固定' if day['source'] == 'frozen_daytime' else '再計算'}</span></td>
          <td>{day['eligible']}{'<small>低稼働</small>' if day['low_activity'] else ''}</td>
          <td>{day['hits']}</td>
          <td><div class="bar"><i style="width:{day['hit_density'] * 100:.1f}%"></i></div>{pct(day['hit_density'])}</td>
          <td>{day['hit_positive']}/{day['hits']}<small>{pct(day['hit_quality'])}</small></td>
          <td>{pct(day['adjusted_quality'])}</td>
          <td>{pct(day['no_hit_quality'])}</td>
          <td><span class="regime {regime_class[day['regime']]}">{day['regime']}</span></td>
        </tr>""")

    latest = days[-1]
    density_periods = " / ".join(
        f"{item['period']}日" for item in metrics["density_periods"][:3]
    )
    quality_periods = " / ".join(
        f"{item['period']}日" for item in metrics["quality_periods"][:3]
    )
    return f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>日中周期hit 日次レジーム</title>
<style>
*{{box-sizing:border-box}}body{{margin:0;background:#0d1117;color:#c9d1d9;font-family:"Segoe UI",Meiryo,sans-serif}}header,main{{max-width:1120px;margin:auto;padding:22px 16px}}header{{border-bottom:1px solid #30363d}}a{{color:#58a6ff;text-decoration:none}}h1{{margin:12px 0 6px;color:#f0f6fc;font-size:27px}}p{{color:#8b949e;line-height:1.6}}.cards{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:18px 0}}.card{{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:14px}}.card span,small{{display:block;color:#8b949e;font-size:11px}}.card b{{display:block;margin-top:5px;font-size:21px;color:#f0f6fc}}.panel{{background:#161b22;border:1px solid #30363d;border-radius:8px;overflow:auto}}table{{width:100%;border-collapse:collapse;min-width:880px}}th,td{{padding:10px;border-bottom:1px solid #30363d;text-align:right}}th{{color:#8b949e;font-size:12px}}td:first-child,th:first-child{{text-align:left}}.source,.regime{{display:inline-block;padding:3px 8px;border-radius:999px;border:1px solid #30363d;font-size:11px}}.source.fixed{{color:#3fb950;border-color:#238636}}.strong{{color:#3fb950;border-color:#238636}}.focus{{color:#58a6ff;border-color:#1f6feb}}.diffuse{{color:#d29922;border-color:#9e6a03}}.rest{{color:#8b949e}}.bar{{display:inline-block;width:70px;height:6px;background:#30363d;border-radius:5px;margin-right:7px;vertical-align:middle}}.bar i{{display:block;height:100%;background:#58a6ff;border-radius:5px}}.note{{margin-top:14px;padding:12px;border-left:3px solid #d29922;background:#161b22}}@media(max-width:720px){{.cards{{grid-template-columns:1fr 1fr}}h1{{font-size:22px}}}}
</style></head><body><header><a href="combined_signal_analysis.html">← 複合シグナル検証</a><h1>日中周期hit 日次レジーム</h1><p>{start}～{end}。hitの多さと、hit台の陽線品質を分離して追跡します。</p></header><main>
<section class="cards">
<div class="card"><span>最新状態 {latest['date']}</span><b>{latest['regime']}</b></div>
<div class="card"><span>hit密度 / 補正品質</span><b>{pct(latest['hit_density'])} / {pct(latest['adjusted_quality'])}</b></div>
<div class="card"><span>密度の周期候補</span><b>{density_periods}</b></div>
<div class="card"><span>品質の周期候補</span><b>{quality_periods}</b></div>
</section>
<section class="panel"><table><thead><tr><th>日付</th><th>判定</th><th>判定可能</th><th>hit</th><th>hit密度</th><th>hit陽線</th><th>補正品質</th><th>非hit陽線</th><th>状態</th></tr></thead><tbody>{''.join(rows)}</tbody></table></section>
<div class="note">固定は当日のdaytime保存値、再計算は最新周期による参考値です。周期候補は固定日の蓄積後に再評価します。</div>
</main></body></html>"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="日中周期hitの日別密度と品質を追跡")
    parser.add_argument("--start", default="20260613")
    parser.add_argument("--end", required=True)
    parser.add_argument("--period-report", default=str(REPORT_DIR / "cycle_sync_68_summary.md"))
    parser.add_argument("--prior-strength", type=int, default=10)
    parser.add_argument("--json-out", default=str(DATA_DIR / "intraday_hit_regime.json"))
    parser.add_argument("--report-out", default=str(REPORT_DIR / "intraday_hit_regime_analysis.md"))
    parser.add_argument("--html-out", default=str(DOCS_DIR / "intraday_hit_regime.html"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    rows = build_rows(args.start, args.end, Path(args.period_report))
    days, metrics = summarize_days(rows, args.prior_strength)
    payload = {
        "start": args.start,
        "end": args.end,
        "days": days,
        "metrics": metrics,
    }
    json_out = Path(args.json_out)
    report_out = Path(args.report_out)
    html_out = Path(args.html_out)
    for path in (json_out, report_out, html_out):
        path.parent.mkdir(parents=True, exist_ok=True)
    json_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    report_out.write_text(make_report(days, metrics, args.start, args.end), encoding="utf-8")
    html_out.write_text(make_html(days, metrics, args.start, args.end), encoding="utf-8")
    latest = days[-1]
    print(json.dumps({
        "days": len(days),
        "latest": latest["date"],
        "regime": latest["regime"],
        "density": round(latest["hit_density"], 4),
        "adjusted_quality": round(latest["adjusted_quality"], 4),
        "fixed_days": sum(day["source"] == "frozen_daytime" for day in days),
    }, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
