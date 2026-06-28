"""
周期推定 ±、特日/通常日、当日中周期hit、チャート状態を結合して検証する。

入力:
  - docs/prediction_YYYYMMDD.html の固定周期推定と実績
  - reports/cycle_sync_68_summary.md の台別日中周期
  - CSV由来の日足チャート状態(MA/Fibo/SL/GC)

出力:
  - reports/combined_signal_analysis_YYYYMMDD_YYYYMMDD.md
"""

from __future__ import annotations

import argparse
import ast
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

import chart_signal_positive as chart
import daily_intraday_cycle_sync as cycle_sync
import machine_cycle_positive as intraday
import prediction_daily as prediction


ROOT = Path(__file__).parent
DOCS_DIR = ROOT / "docs"
REPORT_DIR = ROOT / "reports"
SPECIAL_DAYS = {1, 3, 9, 13, 19, 23, 29}


def signed(value):
    return f"{int(round(value)):+,}"


def signed_plain(value):
    return f"{int(round(value)):+,}"


def pct(part, total):
    return part / total * 100 if total else 0.0


def median(values):
    values = sorted(values)
    if not values:
        return 0
    mid = len(values) // 2
    if len(values) % 2:
        return int(round(values[mid]))
    return int(round((values[mid - 1] + values[mid]) / 2))


def parse_js_object(source):
    try:
        return json.loads(source)
    except json.JSONDecodeError:
        quoted = re.sub(r"([,{])\s*([A-Za-z_][A-Za-z0-9_]*)\s*:", r'\1"\2":', source)
        return ast.literal_eval(quoted)


def csv_dates():
    return sorted(path.name for path in prediction.CSV_DIR.iterdir() if path.is_dir() and re.fullmatch(r"\d{8}", path.name))


def previous_date(date, dates):
    prev = [item for item in dates if item < date]
    return prev[-1] if prev else None


def prediction_html_rows(date):
    rows = []
    path = DOCS_DIR / f"prediction_{date}.html"
    if not path.exists():
        return rows
    text = path.read_text(encoding="utf-8")
    match = re.search(r"const cycleIslands=(\{.*?\});", text, re.S)
    if not match:
        return rows
    data = parse_js_object(match.group(1))
    for island, values in data.items():
        for item in values:
            if len(item) < 3 or item[2] is None:
                continue
            machine, forecast, actual = item[:3]
            rows.append({
                "date": date,
                "machine": f"{int(machine):03d}",
                "forecast": int(forecast),
                "actual": int(actual),
                "positive": int(actual) > 0,
                "cycle_plus": int(forecast) > 0,
                "island": island,
                "special": int(date[6:8]) in SPECIAL_DAYS,
                "source": "html",
            })
    return rows


def load_daily_cache():
    return {machine: prediction.load_daily_net(machine) for machine in prediction.MACHINES}


def backfill_prediction_rows(date, cutoff, daily_cache):
    rows = []
    for machine in prediction.MACHINES:
        daily = daily_cache[machine]
        history = [value for day, value in daily if day <= cutoff]
        actual_by_date = dict(daily)
        if len(history) < 21 or date not in actual_by_date:
            continue
        forecast = prediction.cycle_forecast(history)
        actual = actual_by_date[date]
        rows.append({
            "date": date,
            "machine": f"{machine:03d}",
            "forecast": int(forecast),
            "actual": int(actual),
            "positive": int(actual) > 0,
            "cycle_plus": int(forecast) > 0,
            "island": "",
            "special": int(date[6:8]) in SPECIAL_DAYS,
            "source": "backfill",
        })
    return rows


def load_structural_cycle_cache():
    cache = {}
    for machine in prediction.MACHINES:
        key = f"{machine:03d}"
        try:
            wave_rows, _ = cycle_sync.build_daily_wave(key, 5)
        except Exception:
            continue
        cache[key] = {row["date"]: row["composite"] for row in wave_rows}
    return cache


def structural_backfill_rows(date, daily_cache, structural_cache):
    rows = []
    for machine in prediction.MACHINES:
        key = f"{machine:03d}"
        actual_by_date = dict(daily_cache[machine])
        if date not in actual_by_date or date not in structural_cache.get(key, {}):
            continue
        forecast = round(structural_cache[key][date])
        actual = actual_by_date[date]
        rows.append({
            "date": date,
            "machine": key,
            "forecast": int(forecast),
            "actual": int(actual),
            "positive": int(actual) > 0,
            "cycle_plus": int(forecast) > 0,
            "island": "",
            "special": int(date[6:8]) in SPECIAL_DAYS,
            "source": "structural",
        })
    return rows


def load_prediction_rows(start_date, end_date, backfill=False, backfill_mode="asof"):
    rows = []
    dates = csv_dates()
    daily_cache = load_daily_cache() if backfill else None
    structural_cache = load_structural_cycle_cache() if backfill and backfill_mode == "structural" else None
    for date in dates:
        if not (start_date <= date <= end_date):
            continue
        html_rows = prediction_html_rows(date)
        if html_rows:
            rows.extend(html_rows)
            continue
        if not backfill:
            continue
        if backfill_mode == "structural":
            rows.extend(structural_backfill_rows(date, daily_cache, structural_cache))
        else:
            cutoff = previous_date(date, dates)
            if not cutoff:
                continue
            rows.extend(backfill_prediction_rows(date, cutoff, daily_cache))
    return rows


def load_intraday_periods(path):
    periods = {}
    if not path.exists():
        return periods
    in_table = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("|台|評価|日中周期|"):
            in_table = True
            continue
        if in_table and (not line.startswith("|") or line.startswith("|---")):
            continue
        if in_table:
            parts = [part.strip() for part in line.strip().strip("|").split("|")]
            if len(parts) < 3:
                continue
            try:
                machine = f"{int(parts[0]):03d}"
            except ValueError:
                continue
            nums = [int(value) for value in re.findall(r"\d+", parts[2])]
            if nums:
                periods[machine] = tuple(nums[:3])
    return periods


def build_intraday_hits(rows, periods_by_machine, tolerance):
    machines = {row["machine"] for row in rows}
    days = intraday.load_machine_days(machines)
    by_key = {(item["date"], item["machine"]): item for item in days.values()}
    for row in rows:
        periods = periods_by_machine.get(row["machine"], ())
        day = by_key.get((row["date"], row["machine"]))
        hit_periods = []
        if day and periods:
            for period in periods:
                if any(abs(gap - period) <= tolerance for gap in day["intervals"]):
                    hit_periods.append(period)
        row["intraday_periods"] = periods
        row["intraday_hit_periods"] = tuple(hit_periods)
        row["intraday_hit"] = bool(hit_periods)
        row["event_count"] = day["event_count"] if day else 0


def build_chart_features(rows):
    machines = {row["machine"] for row in rows}
    series, meta = chart.load_daily_ohlc(machines)
    observations = chart.build_observations(series, meta)
    by_key = {(item["target_date"], item["machine"]): item["features"] for item in observations}
    for row in rows:
        features = by_key.get((row["date"], row["machine"]), set())
        row["chart_features"] = features
        row["chart_state"] = classify_chart(features)


def classify_chart(features):
    good_markers = {
        "MA強気配列",
        "MA上向き+Fibo浅押し",
        "MA強気配列+SL構造",
        "GC20日以内+Fibo浅押し",
        "MA5/20上向き+SL構造",
    }
    if features & good_markers:
        return "good"
    if "MA5上向き" in features and ("Fibo浅押し以上" in features or "SL上昇構造" in features):
        return "good"
    if {"MA5上向き", "MA20上向き"} <= features:
        return "neutral"
    if "SL上昇構造" in features or "Fibo押し目帯" in features or "GC20日以内" in features:
        return "neutral"
    return "weak"


def summarize(rows):
    total = len(rows)
    positives = sum(row["positive"] for row in rows)
    return {
        "total": total,
        "positive": positives,
        "rate": positives / total if total else 0,
        "median": median([row["actual"] for row in rows]),
        "avg": sum(row["actual"] for row in rows) / total if total else 0,
    }


def summary_cell(rows):
    s = summarize(rows)
    if not s["total"]:
        return "-"
    return f"{s['positive']}/{s['total']} ({pct(s['positive'], s['total']):.1f}%)"


def compact_rate(rows):
    s = summarize(rows)
    if not s["total"]:
        return ("-", "0/0", 0.0)
    return (f"{pct(s['positive'], s['total']):.1f}%", f"{s['positive']}/{s['total']}", s["rate"])


def jp_bool(value):
    return "特日" if value is True else "通常日" if value is False else str(value)


def jp_cycle(value):
    return "周期+" if value is True else "周期-" if value is False else str(value)


def jp_hit(value):
    return "日中hitあり" if value is True else "日中hitなし" if value is False else str(value)


def table_by_keys(rows, keys, min_count=1):
    grouped = defaultdict(list)
    for row in rows:
        grouped[tuple(row[key] for key in keys)].append(row)
    lines = [
        "|" + "|".join(keys) + "|件数|陽線率|中央値|平均|",
        "|" + "|".join("---" for _ in keys) + "|---:|---:|---:|---:|",
    ]
    sort_key = lambda item: (item[0],)
    for key_values, sub in sorted(grouped.items(), key=sort_key):
        if len(sub) < min_count:
            continue
        s = summarize(sub)
        lines.append(
            "|" + "|".join(str(value) for value in key_values) +
            f"|{s['total']}|{s['positive']}/{s['total']} ({pct(s['positive'], s['total']):.1f}%)|"
            f"{signed(s['median'])}|{signed(s['avg'])}|"
        )
    return "\n".join(lines)


def daily_table(rows):
    lines = [
        "|日付|区分|周期+ 陽線率|周期- 陽線率|差|全体陽線率|方向一致|",
        "|---:|---|---:|---:|---:|---:|---:|",
    ]
    for date in sorted({row["date"] for row in rows}):
        sub = [row for row in rows if row["date"] == date]
        plus = [row for row in sub if row["cycle_plus"]]
        minus = [row for row in sub if not row["cycle_plus"]]
        pp = summarize(plus)
        mm = summarize(minus)
        all_s = summarize(sub)
        direction = sum((row["cycle_plus"] and row["positive"]) or ((not row["cycle_plus"]) and (not row["positive"])) for row in sub)
        label = "特日" if int(date[6:8]) in SPECIAL_DAYS else "通常"
        lines.append(
            f"|{date}|{label}|{summary_cell(plus)}|{summary_cell(minus)}|"
            f"{(pp['rate'] - mm['rate']) * 100:+.1f}pt|{summary_cell(sub)}|"
            f"{direction}/{len(sub)} ({pct(direction, len(sub)):.1f}%)|"
        )
    return "\n".join(lines)


def reverse_pattern_section(rows):
    reverse = [row for row in rows if not row["cycle_plus"] and row["positive"]]
    negative_all = [row for row in rows if not row["cycle_plus"]]
    lines = [
        "## マイナス周期からの陽線化",
        "",
        f"- 対象: 周期推定マイナス {len(negative_all)}件",
        f"- 陽線化: {len(reverse)}/{len(negative_all)} ({pct(len(reverse), len(negative_all)):.1f}%)",
        "",
        "### 条件別",
        "",
        table_by_keys(negative_all, ["special", "intraday_hit", "chart_state"], min_count=1),
        "",
        "### 陽線化した側の頻出特徴",
        "",
    ]
    feature_counts = Counter()
    for row in reverse:
        feature_counts.update(row["chart_features"])
        if row["intraday_hit"]:
            feature_counts["日中周期hit"] += 1
        if row["special"]:
            feature_counts["特日"] += 1
    lines.extend(["|特徴|件数|", "|---|---:|"])
    for feature, count in feature_counts.most_common(20):
        lines.append(f"|{feature}|{count}|")

    lines.extend([
        "",
        "### 20260621のマイナス周期陽線",
        "",
        "|台|実績差玉|日中hit|チャート状態|主な特徴|",
        "|---:|---:|---|---|---|",
    ])
    for row in sorted([r for r in reverse if r["date"] == "20260621"], key=lambda item: item["actual"], reverse=True):
        features = " / ".join(sorted(row["chart_features"])[:5]) or "-"
        hit = ",".join(f"{p}分" for p in row["intraday_hit_periods"]) or "-"
        lines.append(f"|{int(row['machine'])}|{signed(row['actual'])}|{hit}|{row['chart_state']}|{features}|")
    return "\n".join(lines)


def make_report(rows, args):
    all_s = summarize(rows)
    plus = [row for row in rows if row["cycle_plus"]]
    minus = [row for row in rows if not row["cycle_plus"]]
    pp = summarize(plus)
    mm = summarize(minus)
    direction = sum((row["cycle_plus"] and row["positive"]) or ((not row["cycle_plus"]) and (not row["positive"])) for row in rows)
    source_counts = Counter(row.get("source", "unknown") for row in rows)
    lines = [
        f"# 複合シグナル検証 {args.start}〜{args.end}",
        "",
        "## 要約",
        "",
        f"- 対象件数: {len(rows)} machine-days",
        f"- データ元: " + ", ".join(f"{key} {value}件" for key, value in sorted(source_counts.items())),
        f"- 全体陽線率: {all_s['positive']}/{all_s['total']} ({pct(all_s['positive'], all_s['total']):.1f}%)",
        f"- 周期プラス陽線率: {pp['positive']}/{pp['total']} ({pct(pp['positive'], pp['total']):.1f}%)",
        f"- 周期マイナス陽線率: {mm['positive']}/{mm['total']} ({pct(mm['positive'], mm['total']):.1f}%)",
        f"- 差: {(pp['rate'] - mm['rate']) * 100:+.1f}pt",
        f"- 方向一致: {direction}/{len(rows)} ({pct(direction, len(rows)):.1f}%)",
        "",
        "## 日別",
        "",
        daily_table(rows),
        "",
        "## 特日/通常日 x 周期推定",
        "",
        table_by_keys(rows, ["special", "cycle_plus"], min_count=1),
        "",
        "## 特日/通常日 x 周期推定 x 日中周期hit",
        "",
        table_by_keys(rows, ["special", "cycle_plus", "intraday_hit"], min_count=1),
        "",
        "## 特日/通常日 x 周期推定 x 日中周期hit x チャート状態",
        "",
        table_by_keys(rows, ["special", "cycle_plus", "intraday_hit", "chart_state"], min_count=args.min_count),
        "",
        reverse_pattern_section(rows),
        "",
        "## 注意",
        "",
        "- 周期推定はpredictionページに固定された値を使う。",
        "- predictionページがない過去日は、--backfill指定時のみ前日までのCSVから周期推定を再計算する。",
        "- --backfill-mode structural は全履歴の日足周期構造を使うため、予測ではなく構造分析として読む。",
        "- 日中周期hitは当日中に初めて確認できる条件なので、朝時点の予測ではなく当日途中の確認シグナル。",
        "- チャート状態は前日終了時点から翌日を判定する特徴量。",
        "- 日数が少ないため、細分条件は件数不足になりやすい。",
        "",
    ]
    return "\n".join(lines)


def make_html(rows, args):
    all_s = summarize(rows)
    plus = [row for row in rows if row["cycle_plus"]]
    minus = [row for row in rows if not row["cycle_plus"]]
    plus_hit = [row for row in plus if row["intraday_hit"]]
    plus_no = [row for row in plus if not row["intraday_hit"]]
    minus_hit = [row for row in minus if row["intraday_hit"]]
    minus_no = [row for row in minus if not row["intraday_hit"]]
    reverse = [row for row in minus if row["positive"]]
    direction = sum((row["cycle_plus"] and row["positive"]) or ((not row["cycle_plus"]) and (not row["positive"])) for row in rows)
    source_counts = Counter(row.get("source", "unknown") for row in rows)
    source_text = " / ".join(f"{key}: {value}件" for key, value in sorted(source_counts.items()))

    def metric_card(title, sub, group, tone=""):
        rate, count, _ = compact_rate(group)
        s = summarize(group)
        return (
            f'<article class="metric {tone}"><span>{title}</span><b>{rate}</b>'
            f'<p>{count} / 中央値 {signed_plain(s["median"])}</p><small>{sub}</small></article>'
        )

    top_cards = "\n".join([
        metric_card("全体陽線率", f"{len(rows)}件 / 方向一致 {direction}/{len(rows)} ({pct(direction, len(rows)):.1f}%)", rows, "base"),
        metric_card("周期プラス", "朝時点の上向き候補", plus, "plus"),
        metric_card("周期マイナス", "朝時点の下向き候補", minus, "minus"),
        metric_card("周期+ × 日中hit", "上向き候補の当日確認", plus_hit, "hit"),
        metric_card("周期- × 日中hit", "下向きからの反転候補", minus_hit, "hit"),
        metric_card("周期- × hitなし", "反転材料なし", minus_no, "weak"),
    ])

    condition_rows = []
    for special in (False, True):
        for cycle_plus in (False, True):
            for intraday_hit in (False, True):
                sub = [
                    row for row in rows
                    if row["special"] == special
                    and row["cycle_plus"] == cycle_plus
                    and row["intraday_hit"] == intraday_hit
                ]
                if not sub:
                    continue
                rate, count, _ = compact_rate(sub)
                s = summarize(sub)
                condition_rows.append(
                    f"<tr><td>{jp_bool(special)}</td><td>{jp_cycle(cycle_plus)}</td>"
                    f"<td>{jp_hit(intraday_hit)}</td><td>{count}</td><td>{rate}</td>"
                    f"<td>{signed_plain(s['median'])}</td><td>{signed_plain(s['avg'])}</td></tr>"
                )

    daily_cards = []
    for date in sorted({row["date"] for row in rows}):
        sub = [row for row in rows if row["date"] == date]
        day_plus = [row for row in sub if row["cycle_plus"]]
        day_minus = [row for row in sub if not row["cycle_plus"]]
        day_plus_hit = [row for row in day_plus if row["intraday_hit"]]
        day_minus_hit = [row for row in day_minus if row["intraday_hit"]]
        day_reverse = [row for row in day_minus if row["positive"]]
        plus_rate, plus_count, plus_float = compact_rate(day_plus)
        minus_rate, minus_count, minus_float = compact_rate(day_minus)
        all_rate, all_count, _ = compact_rate(sub)
        hit_rate, hit_count, _ = compact_rate([row for row in sub if row["intraday_hit"]])
        diff = (plus_float - minus_float) * 100
        label = "特日" if int(date[6:8]) in SPECIAL_DAYS else "通常日"
        reverse_top = sorted(day_reverse, key=lambda row: row["actual"], reverse=True)[:5]
        reverse_html = "".join(
            f"<li><b>{int(row['machine'])}</b><span>{signed_plain(row['actual'])}</span>"
            f"<em>{','.join(str(p) + '分' for p in row['intraday_hit_periods']) or 'hitなし'} / {row['chart_state']}</em></li>"
            for row in reverse_top
        ) or "<li><em>該当なし</em></li>"
        daily_cards.append(f"""
<section class="day-card">
  <div class="day-head">
    <h3>{date}</h3><span class="badge {'special' if label == '特日' else ''}">{label}</span>
  </div>
  <div class="day-grid">
    <div><span>周期+</span><b>{plus_rate}</b><small>{plus_count}</small></div>
    <div><span>周期-</span><b>{minus_rate}</b><small>{minus_count}</small></div>
    <div><span>差</span><b class="{ 'pos' if diff >= 0 else 'neg' }">{diff:+.1f}pt</b><small>周期+ - 周期-</small></div>
    <div><span>全体</span><b>{all_rate}</b><small>{all_count}</small></div>
    <div><span>日中hit全体</span><b>{hit_rate}</b><small>{hit_count}</small></div>
    <div><span>周期-陽線</span><b>{len(day_reverse)}台</b><small>うちhit {sum(row['intraday_hit'] for row in day_reverse)}台</small></div>
  </div>
  <div class="mini-split">
    <p>周期+×hit: <b>{summary_cell(day_plus_hit)}</b></p>
    <p>周期-×hit: <b>{summary_cell(day_minus_hit)}</b></p>
  </div>
  <h4>周期マイナスから陽線化 上位</h4>
  <ul class="reverse-list">{reverse_html}</ul>
</section>""")

    html = f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>複合シグナル検証</title>
<style>
*{{box-sizing:border-box}}body{{margin:0;background:#0d1117;color:#c9d1d9;font-family:"Segoe UI",Meiryo,sans-serif;line-height:1.55}}
header,main{{max-width:1180px;margin:auto}}header{{padding:26px 16px 12px}}main{{padding:0 16px 48px;display:grid;gap:16px}}
a{{color:#58a6ff;text-decoration:none}}h1{{margin:6px 0 4px;color:#58a6ff;font-size:24px}}h2{{font-size:18px;margin:0 0 12px;color:#58a6ff}}h3{{margin:0;font-size:18px}}h4{{margin:12px 0 8px;color:#8b949e;font-size:13px}}
.meta,.note,small{{color:#8b949e}}.summary{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}}.metric,.panel,.day-card{{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:14px}}
.metric span{{display:block;color:#8b949e;font-size:12px}}.metric b{{display:block;font-size:28px;margin:3px 0}}.metric p{{margin:0;color:#c9d1d9;font-size:13px}}.metric small{{display:block;margin-top:5px}}
.metric.plus{{border-color:#238636}}.metric.minus{{border-color:#6e7681}}.metric.hit{{border-color:#d29922}}.metric.weak{{border-color:#da3633}}.pos{{color:#3fb950}}.neg{{color:#f85149}}
table{{width:100%;border-collapse:collapse}}th,td{{padding:9px 10px;border-bottom:1px solid #30363d;text-align:right;white-space:nowrap}}th:first-child,td:first-child{{text-align:left}}th{{color:#8b949e;font-size:12px}}
.day-list{{display:grid;gap:12px}}.day-head{{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px}}.badge{{font-size:12px;border:1px solid #30363d;border-radius:999px;padding:3px 10px;color:#8b949e}}.badge.special{{color:#d29922;border-color:#9e6a03;background:#2d2608}}
.day-grid{{display:grid;grid-template-columns:repeat(6,1fr);gap:8px}}.day-grid div{{background:#0d1117;border:1px solid #30363d;border-radius:7px;padding:9px}}.day-grid span{{display:block;color:#8b949e;font-size:11px}}.day-grid b{{font-size:18px}}.day-grid small{{display:block;font-size:11px}}
.mini-split{{display:flex;gap:12px;flex-wrap:wrap;margin-top:10px;color:#8b949e}}.mini-split p{{margin:0}}.reverse-list{{list-style:none;margin:0;padding:0;display:grid;gap:6px}}.reverse-list li{{display:grid;grid-template-columns:52px 90px 1fr;gap:8px;align-items:center;background:#0d1117;border:1px solid #30363d;border-radius:6px;padding:7px 9px}}.reverse-list span{{color:#3fb950;font-weight:700}}.reverse-list em{{color:#8b949e;font-style:normal;font-size:12px}}
@media(max-width:860px){{.summary{{grid-template-columns:1fr 1fr}}.day-grid{{grid-template-columns:1fr 1fr}}.panel{{overflow:auto}}table{{min-width:760px}}}}
@media(max-width:560px){{.summary{{grid-template-columns:1fr}}.reverse-list li{{grid-template-columns:44px 82px 1fr}}}}
</style>
</head>
<body>
<header>
  <a href="index.html">← ダッシュボード</a>
  <h1>複合シグナル検証</h1>
  <div class="meta">周期推定 ± × 特日/通常日 × 日中周期hit × チャート状態。日中周期hitは当日途中の確認シグナルとして扱います。データ元: {source_text}</div>
</header>
<main>
  <section class="panel">
    <h2>全期間 {args.start}〜{args.end}</h2>
    <div class="summary">{top_cards}</div>
  </section>
  <section class="panel">
    <h2>条件別リスト</h2>
    <table><thead><tr><th>日区分</th><th>周期</th><th>日中周期</th><th>陽線</th><th>陽線率</th><th>中央値</th><th>平均</th></tr></thead><tbody>
      {''.join(condition_rows)}
    </tbody></table>
  </section>
  <section>
    <h2>日別リスト</h2>
    <div class="day-list">{''.join(daily_cards)}</div>
  </section>
  <section class="panel note">
    更新時は <code>python combined_signal_analysis.py --start 20260613 --end 最新日</code> を実行します。過去分を補完する場合は <code>--backfill</code> を付けます。全期間を高速に見る場合は <code>--backfill-mode structural</code> を使います。
  </section>
</main>
</body>
</html>"""
    return html


def parse_args():
    parser = argparse.ArgumentParser(description="周期推定・日中周期・チャート状態の複合集計")
    parser.add_argument("--start", default="20260613", help="開始日 YYYYMMDD")
    parser.add_argument("--end", default="20260621", help="終了日 YYYYMMDD")
    parser.add_argument("--period-report", default=str(REPORT_DIR / "cycle_sync_68_summary.md"), help="日中周期一覧レポート")
    parser.add_argument("--tolerance", type=int, default=5, help="日中周期hit許容幅")
    parser.add_argument("--min-count", type=int, default=5, help="細分表に出す最小件数")
    parser.add_argument("--backfill", action="store_true", help="prediction HTMLがない過去日を前日までのCSVから再計算する")
    parser.add_argument("--backfill-mode", choices=("asof", "structural"), default="asof", help="asof=前日までで再計算、structural=全履歴周期構造で高速補完")
    parser.add_argument("--out", default=None, help="出力先")
    parser.add_argument("--html-out", default=str(ROOT / "docs" / "combined_signal_analysis.html"), help="HTML出力先")
    return parser.parse_args()


def main():
    args = parse_args()
    rows = load_prediction_rows(args.start, args.end, args.backfill, args.backfill_mode)
    if not rows:
        raise SystemExit("対象のprediction実績がありません。")
    periods = load_intraday_periods(Path(args.period_report))
    build_intraday_hits(rows, periods, args.tolerance)
    build_chart_features(rows)
    out = Path(args.out) if args.out else REPORT_DIR / f"combined_signal_analysis_{args.start}_{args.end}.md"
    out.write_text(make_report(rows, args), encoding="utf-8")
    html_out = Path(args.html_out)
    html_out.parent.mkdir(parents=True, exist_ok=True)
    html_out.write_text(make_html(rows, args), encoding="utf-8")
    print(out)
    print(html_out)
    print(f"rows={len(rows)} dates={min(row['date'] for row in rows)}..{max(row['date'] for row in rows)}")


if __name__ == "__main__":
    main()
