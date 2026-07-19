"""
日足周期監視リストと日中周期hitの手入力ツール。

例:
  python cycle_watch.py list
  python cycle_watch.py add 69 1241
  python cycle_watch.py add 69 12:41
  python cycle_watch.py show

入力ログ:
  data/cycle_watch_YYYYMMDD.json
"""

import argparse
import csv
import html
import json
import math
import re
from datetime import datetime, timedelta
from pathlib import Path

import aggregate_cycle_sync_68 as agg
import daily_ohlc as daily_source
import machine_cycle_positive as intraday


ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
DOCS_DIR = ROOT / "docs"
TOLERANCE = 5

GRADE_SCORE = {"A": 3, "B": 2, "C+": 1, "C": 0}
CACHE_PATH = DATA_DIR / "cycle_watch_config.json"


def parse_time(value):
    text = str(value).strip()
    if ":" in text:
        h, m = text.split(":", 1)
    else:
        text = text.zfill(4)
        h, m = text[:-2], text[-2:]
    hour = int(h)
    minute = int(m)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise SystemExit(f"時刻が不正です: {value}")
    return hour * 60 + minute


def fmt_time(minutes):
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def fmt_periods(periods):
    return "/".join(str(p) for p in periods)


def signed(value):
    return f"{int(round(value)):+,}"


def log_path(date):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR / f"cycle_watch_{date}.json"


def cyclewatch_folder(date):
    return Path(r"C:\kota\BU_Sdrive") / date[:4] / date[4:6] / date[6:8] / "cyclewatch"


def analyze_csv_path(date):
    return ROOT / "csv" / "analyze" / date / f"{date}_analyze.csv"


def cycle_watch_page_path(date):
    return DOCS_DIR / f"cycle_watch_{date}.html"


def normalize_machine_key(value):
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        number = int(text)
    except ValueError:
        return text
    return str(number).zfill(3)


def load_ocr_summary(date):
    path = analyze_csv_path(date)
    if not path.exists():
        return {}
    daily, _meta = daily_source.load_daily_ohlc()
    by_machine = {}
    atari = {"当り", "大当り"}
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        next(reader, None)
        for row in reader:
            if len(row) < 11:
                continue
            machine = normalize_machine_key(row[1])
            by_machine.setdefault(machine, []).append(row)

    out = {}
    for machine, rows in by_machine.items():
        vals = [0]
        for row in rows:
            try:
                vals.append(int(float(row[6] or 0)))
                vals.append(int(float(row[8] or 0)))
            except ValueError:
                pass
        latest = max(rows, key=lambda row: row[7])
        try:
            final = int(float(latest[8] or 0))
        except ValueError:
            final = 0
        chart_row = daily.get(str(int(machine)), {}).get(date)
        if chart_row:
            final = chart_row["net"]
            vals.extend([chart_row["high"], chart_row["low"], chart_row["net"]])
        hits = [row for row in rows if row[4] in atari]
        out[machine] = {
            "atari": len(hits),
            "final": final,
            "high": max(vals) if vals else 0,
            "low": min(vals) if vals else 0,
            "latest": f"{latest[4]} {latest[5]}-{latest[7]}",
        }
    return out


def graph_shape(ocr_item):
    if not ocr_item:
        return {
            "label": "未OCR",
            "score": 0,
            "detail": "形状未判定",
            "advice": "グラフ形状は未反映。",
        }

    final = ocr_item["final"]
    high = ocr_item["high"]
    low = ocr_item["low"]
    width = max(high - low, 1)
    pos = (final - low) / width
    drawdown = high - final
    rebound = final - low

    if final > 0 and pos >= 0.82:
        return {
            "label": "上昇継続",
            "score": 5,
            "detail": f"終値が高値圏({pos:.0%})",
            "advice": "形状は強い。高値圏の押し目維持なら監視継続、崩れたら後追いは控えめ。",
        }
    if final > 0 and pos >= 0.58:
        return {
            "label": "上側維持",
            "score": 4,
            "detail": f"終値が上側({pos:.0%})",
            "advice": "形状は悪くない。直近安値を割らずに次窓へ入るなら確認対象。",
        }
    if final < 0 and pos <= 0.25:
        return {
            "label": "下側終了",
            "score": 0,
            "detail": f"安値寄り({pos:.0%})",
            "advice": "形状は弱い。周期hitだけで打たず、下降チャネル上抜けか強い反発を待つ。",
        }
    if final < 0 and rebound >= max(2500, width * 0.35):
        return {
            "label": "反発途中",
            "score": 3,
            "detail": f"安値から{signed(rebound)}戻し",
            "advice": "一発反発はあるがまだ水面下。戻りが続くか、次窓の反応だけ確認。",
        }
    if drawdown >= max(2500, width * 0.35):
        return {
            "label": "失速",
            "score": 1,
            "detail": f"高値から{signed(drawdown)}下落",
            "advice": "高値からの失速が大きい。新規は弱め、再浮上を見てから。",
        }
    return {
        "label": "レンジ",
        "score": 2,
        "detail": f"終値位置{pos:.0%}",
        "advice": "形状は中立。周期窓で上方向に反応するかだけ確認。",
    }


def load_log(date):
    path = log_path(date)
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
    else:
        data = {"date": date}
    data.setdefault("events", {})
    data.setdefault("reviews", {})
    return data


def save_log(data):
    path = log_path(data["date"])
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def esc(value):
    return html.escape(str(value), quote=True)


def nl2br(value):
    return esc(value).replace("\n", "<br>")


def outcome_label(final):
    if final > 0:
        return "陽線"
    if final < 0:
        return "陰線"
    return "±0"


def auto_result_text(ocr_item):
    if not ocr_item:
        return "-"
    return f"{outcome_label(ocr_item['final'])}<br>{signed(ocr_item['final'])} / 初当たり{ocr_item['atari']}回"


def latest_data_date(all_days):
    return max(date for date, _ in all_days.keys())


def default_watch_date(all_days):
    latest = latest_data_date(all_days)
    today = datetime.now().strftime("%Y%m%d")
    if today > latest:
        return today
    return latest


def date_offset_from_history(date, all_days):
    dates = sorted({d for d, _ in all_days.keys()})
    if date in dates:
        return dates.index(date)
    latest = dates[-1]
    if date > latest:
        # 日足周期は営業日インデックスなので、未来日は「次営業日」として扱う。
        return len(dates)
    earlier = [d for d in dates if d < date]
    return len(earlier)


def build_context():
    all_days = intraday.load_machine_days(set(agg.TARGET_MACHINES))
    candidates_by_machine, _, valid_dates = agg.prepare_intraday_candidates(all_days)
    results = {}
    for machine in agg.TARGET_MACHINES:
        result = agg.analyze_machine(machine, all_days, candidates_by_machine, valid_dates)
        if result:
            results[machine] = result
    return all_days, results


def make_component_cache(machine, result, all_days):
    daily = []
    for (day, day_machine), item in all_days.items():
        if day_machine == machine:
            daily.append((day, item["final_close"]))
    daily.sort()
    values = [net for _, net in daily]
    if not values:
        return []
    avg = sum(values) / len(values)
    centered = [value - avg for value in values]
    coeffs = agg.sync.fourier.dft(centered)
    out = []
    for comp in result["daily_components"][:5]:
        k = comp["k"]
        phase_sin = math.atan2(coeffs[k].imag, coeffs[k].real) + math.pi / 2
        out.append({
            "k": k,
            "period": comp["period"],
            "amplitude": comp["amplitude"],
            "phase_sin": phase_sin,
            "n": len(values),
        })
    return out


def write_config_cache(all_days, results):
    latest = latest_data_date(all_days)
    date_count = len({date for date, _ in all_days.keys()})
    machines = {}
    for machine, result in results.items():
        best = result["best"]
        if not best:
            continue
        machines[machine] = {
            "grade": agg.quality(result),
            "periods": list(result["periods"]),
            "best": {
                "zone": best["zone"],
                "hit_pos_rate": best["hit_pos_rate"],
                "no_pos_rate": best["no_pos_rate"],
                "hit_med": best["hit_med"],
                "no_med": best["no_med"],
            },
            "components": make_component_cache(machine, result, all_days),
        }
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "latest_data_date": latest,
        "date_count": date_count,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "machines": machines,
    }
    CACHE_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def load_config_cache(force=False):
    all_days = intraday.load_machine_days(set(agg.TARGET_MACHINES))
    latest = latest_data_date(all_days)
    if not force and CACHE_PATH.exists():
        payload = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        if payload.get("latest_data_date") == latest:
            return all_days, payload
    _, results = build_context()
    return all_days, write_config_cache(all_days, results)


def projected_zone(machine, machine_config, date, all_days):
    components = machine_config.get("components", [])
    if not components:
        return "unknown", 0

    t = date_offset_from_history(date, all_days)
    total = 0.0
    max_amp_sum = 0.0
    for comp in components:
        k = comp["k"]
        amplitude = comp["amplitude"]
        n = comp["n"]
        phase_sin = comp["phase_sin"]
        total += amplitude * math.sin(2 * math.pi * k * t / n + phase_sin)
        max_amp_sum += amplitude
    zone = agg.sync.wave_zone(total, max_amp_sum or 1)
    return zone, total


def watch_rows(date, grades, top):
    all_days, config = load_config_cache()
    rows = []
    for machine, machine_config in config["machines"].items():
        grade = machine_config["grade"]
        if grades and grade not in grades:
            continue
        best = machine_config["best"]
        zone, score = projected_zone(machine, machine_config, date, all_days)
        is_best = zone == best["zone"]
        rows.append({
            "machine": machine,
            "grade": grade,
            "periods": tuple(machine_config["periods"]),
            "zone": zone,
            "best_zone": best["zone"],
            "match": is_best,
            "score": score,
            "hit_rate": best["hit_pos_rate"],
            "no_rate": best["no_pos_rate"],
            "hit_med": best["hit_med"],
            "no_med": best["no_med"],
        })
    rows.sort(
        key=lambda r: (
            r["match"],
            GRADE_SCORE.get(r["grade"], 0),
            r["hit_rate"] - r["no_rate"],
            r["hit_med"] - r["no_med"],
        ),
        reverse=True,
    )
    return rows[:top] if top else rows


def row_from_config(machine, machine_config, date, all_days):
    best = machine_config["best"]
    zone, score = projected_zone(machine, machine_config, date, all_days)
    return {
        "machine": machine,
        "grade": machine_config["grade"],
        "periods": tuple(machine_config["periods"]),
        "zone": zone,
        "best_zone": best["zone"],
        "match": zone == best["zone"],
        "score": score,
        "hit_rate": best["hit_pos_rate"],
        "no_rate": best["no_pos_rate"],
        "hit_med": best["hit_med"],
        "no_med": best["no_med"],
    }


def include_ocr_rows(date, rows, ocr):
    if not ocr:
        return rows
    data = load_log(date)
    logged_machines = set(data.get("events", {})) | set(data.get("reviews", {}))
    existing = {row["machine"] for row in rows}
    missing = sorted((set(ocr) & logged_machines) - existing, key=lambda x: int(x))
    if not missing:
        return rows
    all_days, config = load_config_cache()
    extra = []
    for machine in missing:
        machine_config = config["machines"].get(machine)
        if machine_config:
            extra.append(row_from_config(machine, machine_config, date, all_days))
    return rows + extra


def cmd_list(args):
    all_days, _ = load_config_cache(force=args.refresh)
    date = args.date or default_watch_date(all_days)
    grades = set(args.grades.split(",")) if args.grades else {"A", "B"}
    rows = watch_rows(date, grades, args.top)
    print(f"監視日: {date}")
    print("台  評価  日足zone/期待zone  日中周期  期待陽線率  中央値")
    for row in rows:
        mark = "★" if row["match"] else " "
        print(
            f"{mark} {int(row['machine']):>4}  {row['grade']:<2}  "
            f"{row['zone']}/{row['best_zone']:<9}  {fmt_periods(row['periods']):<11}  "
            f"{row['hit_rate']:.1f}%/{row['no_rate']:.1f}%  "
            f"{signed(row['hit_med'])}/{signed(row['no_med'])}"
        )
    if args.write_page:
        out = write_watch_page(date, rows)
        print(f"page: {out}")


def cmd_add(args):
    machine = str(args.machine).zfill(3)
    time_min = parse_time(args.time)
    all_days, config = load_config_cache(force=args.refresh)
    date = args.date or default_watch_date(all_days)
    if machine not in config["machines"]:
        raise SystemExit(f"{int(machine)}番は現在の周期監視候補にありません。")
    machine_config = config["machines"][machine]
    data = load_log(date)
    events = data["events"].setdefault(machine, [])
    if time_min not in events:
        events.append(time_min)
        events.sort()
    if not args.dry_run:
        save_log(data)

    periods = tuple(machine_config["periods"])
    best = machine_config["best"]
    zone, score = projected_zone(machine, machine_config, date, all_days)
    prev = None
    for t in events:
        if t < time_min:
            prev = t
    hit_periods = []
    gap = None
    if prev is not None:
        gap = time_min - prev
        hit_periods = [p for p in periods if abs(gap - p) <= TOLERANCE]

    suffix = " 確認のみ" if args.dry_run else " 記録"
    print(f"{date} {int(machine)}番 {fmt_time(time_min)}{suffix}")
    print(f"日足zone: {zone} / 期待zone: {best['zone']} ({'一致' if zone == best['zone'] else '不一致'})")
    print(f"日中周期: {fmt_periods(periods)}分 ±{TOLERANCE}分")
    if prev is None:
        print("前回当たり: なし")
    else:
        print(f"前回当たり: {fmt_time(prev)} → 差分 {gap}分")
        if hit_periods:
            print(f"周期hit: {fmt_periods(hit_periods)}分")
        else:
            print("周期hit: なし")

    if hit_periods and zone == best["zone"]:
        print("判定: 条件一致。監視強化")
    elif hit_periods:
        print("判定: 日中hitあり。ただし日足zoneは期待zone外")
    else:
        print("判定: 継続記録")
    if not args.dry_run:
        rows = watch_rows(date, {"A", "B"}, 40)
        out = write_watch_page(date, rows)
        print(f"page: {out}")


def cmd_show(args):
    all_days, config = load_config_cache(force=args.refresh)
    date = args.date or default_watch_date(all_days)
    data = load_log(date)
    print(f"入力ログ: {date}")
    for machine in sorted(data["events"], key=lambda x: int(x)):
        machine_config = config["machines"].get(machine)
        periods = tuple(machine_config["periods"]) if machine_config else ()
        times = data["events"][machine]
        print(f"{int(machine)}番 ({fmt_periods(periods)}分): " + ", ".join(fmt_time(t) for t in times))
        for prev, cur in zip(times, times[1:]):
            gap = cur - prev
            hits = [p for p in periods if abs(gap - p) <= TOLERANCE]
            suffix = f" HIT {fmt_periods(hits)}分" if hits else ""
            print(f"  {fmt_time(prev)} -> {fmt_time(cur)} = {gap}分{suffix}")
    reviews = data.get("reviews", {})
    if reviews:
        print("レビュー:")
        for machine in sorted(reviews, key=lambda x: int(x)):
            item = reviews[machine]
            print(f"  {int(machine)}番 結果: {item.get('result', '')}")
            review = item.get("review", "")
            if review:
                print(f"    {review}")


def cmd_review(args):
    all_days, _ = load_config_cache(force=args.refresh)
    date = args.date or default_watch_date(all_days)
    machine = str(args.machine).zfill(3)
    data = load_log(date)
    reviews = data.setdefault("reviews", {})
    if args.clear:
        reviews.pop(machine, None)
        save_log(data)
        print(f"{date} {int(machine)}番 レビュー削除")
    else:
        current = reviews.get(machine, {})
        result = args.result if args.result is not None else current.get("result", "")
        review = args.review if args.review is not None else current.get("review", "")
        outcome = args.outcome if args.outcome is not None else current.get("outcome", "")
        reviews[machine] = {
            "outcome": outcome,
            "result": result,
            "review": review,
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        }
        save_log(data)
        print(f"{date} {int(machine)}番 レビュー保存")

    rows = watch_rows(date, {"A", "B"}, 40)
    out = write_watch_page(date, rows)
    print(f"page: {out}")


def event_status(machine, periods, events):
    rows = []
    hit_periods = []
    for prev, cur in zip(events, events[1:]):
        gap = cur - prev
        hits = [p for p in periods if abs(gap - p) <= TOLERANCE]
        if hits:
            hit_periods.extend(hits)
        rows.append({
            "prev": prev,
            "cur": cur,
            "gap": gap,
            "hits": hits,
        })
    return rows, hit_periods


def next_windows(events, periods):
    if not events:
        return []
    base = max(events)
    windows = []
    for period in periods:
        center = base + period
        windows.append({
            "period": period,
            "start": center - TOLERANCE,
            "center": center,
            "end": center + TOLERANCE,
        })
    return sorted(windows, key=lambda item: item["center"])


def fmt_window(window):
    return f"{fmt_time(window['start'])}〜{fmt_time(window['end'])} ({window['period']}分)"


def window_text(windows):
    return "<br>".join(fmt_window(window) for window in windows) if windows else "次回当たり待ち"


def advice_for(row, windows, current_minute):
    future = [window for window in windows if window["end"] >= current_minute]
    near = future[0] if future else None
    min_period = min(row["periods"]) if row["periods"] else 999
    if row["grade"] == "A":
        if near:
            return f"A評価。まず {fmt_window(near)} を本命窓として監視。再hitなら監視強化、外したら次の長め周期だけ見る。"
        return "A評価だが直近窓は通過気味。次の初当たりが出たら再判定。打ちっぱなしより待ち。"
    if min_period <= 30:
        if near:
            return f"短周期の勢い確認型。{fmt_window(near)} で再hitなら継続感あり。外したら勢い切れ寄り。"
        return "短周期の主要窓は通過気味。今からは後追い弱め、次の初当たりで再判定。"
    if near:
        return f"B評価。{fmt_window(near)} で再hitなら追跡、来なければ深追いせず次窓だけ確認。"
    return "主要窓は通過気味。今から新規で追うより、次の初当たりを待って再判定。"


def shape_adjusted_advice(base_advice, row, shape, has_hit, zone_match):
    if shape["label"] == "未OCR":
        return base_advice
    prefix = shape["advice"]
    if has_hit and zone_match and shape["score"] <= 1:
        return f"{prefix} 周期条件は一致しているが、形状優先では打たない寄り。見るなら {base_advice}"
    if has_hit and zone_match:
        return f"{prefix} 周期条件も一致。{base_advice}"
    if shape["score"] >= 4:
        return f"{prefix} ただし日足zone/日中hitの厳密条件は未成立なので、打つ根拠は形状寄り。"
    if has_hit:
        return f"{prefix} 日中hitはあるが日足zoneが期待zone外。候補ではなく補助監視。"
    return f"{prefix} 日中hit待ち。初当たり時刻を追加して判定。"


def priority_rows(rows, data, current_minute):
    out = []
    for row in rows:
        machine = row["machine"]
        events = sorted(data["events"].get(machine, []))
        gaps, hit_periods = event_status(machine, row["periods"], events)
        if not (hit_periods and row["zone"] == row["best_zone"]):
            continue
        windows = next_windows(events, row["periods"])
        active = any(window["end"] >= current_minute for window in windows)
        out.append({
            "row": row,
            "events": events,
            "gaps": gaps,
            "hit_periods": hit_periods,
            "windows": windows,
            "active": active,
            "advice": advice_for(row, windows, current_minute),
        })
    out.sort(
        key=lambda item: (
            GRADE_SCORE.get(item["row"]["grade"], 0),
            item["active"],
            item["row"]["hit_rate"] - item["row"]["no_rate"],
            item["row"]["hit_med"] - item["row"]["no_med"],
        ),
        reverse=True,
    )
    return out


def shape_focus_rows(rows, data, ocr, current_minute):
    out = []
    for row in rows:
        machine = row["machine"]
        if machine not in ocr:
            continue
        events = sorted(data["events"].get(machine, []))
        gaps, hit_periods = event_status(machine, row["periods"], events)
        windows = next_windows(events, row["periods"])
        shape = graph_shape(ocr.get(machine))
        has_hit = bool(hit_periods)
        zone_match = row["zone"] == row["best_zone"]
        if shape["score"] < 3 and not (has_hit and zone_match):
            continue
        base = advice_for(row, windows, current_minute) if has_hit and zone_match else "次の初当たりで再判定。"
        out.append({
            "row": row,
            "events": events,
            "gaps": gaps,
            "hit_periods": hit_periods,
            "windows": windows,
            "shape": shape,
            "advice": shape_adjusted_advice(base, row, shape, has_hit, zone_match),
            "strict": has_hit and zone_match,
        })
    out.sort(
        key=lambda item: (
            item["shape"]["score"],
            item["strict"],
            GRADE_SCORE.get(item["row"]["grade"], 0),
            item["row"]["hit_rate"] - item["row"]["no_rate"],
        ),
        reverse=True,
    )
    return out


def write_watch_page(date, rows):
    data = load_log(date)
    ocr = load_ocr_summary(date)
    rows = include_ocr_rows(date, rows, ocr)
    historical_strict = historical_strict_machines(date)
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    generated = now.strftime("%Y-%m-%d %H:%M")
    current_minute = now.hour * 60 + now.minute
    priority = priority_rows(rows, data, current_minute)
    if historical_strict:
        priority = [item for item in priority if item["row"]["machine"] in historical_strict]
    shape_focus = shape_focus_rows(rows, data, ocr, current_minute)
    priority_machines = {item["row"]["machine"] for item in priority}
    ordered_rows = [item["row"] for item in priority] + [row for row in rows if row["machine"] not in priority_machines]

    priority_html = ""
    if priority:
        items = []
        for rank, item in enumerate(priority, 1):
            row = item["row"]
            hit_periods = sorted(set(item["hit_periods"]))
            hit_counts = {
                period: sum(period in gap["hits"] for gap in item["gaps"])
                for period in hit_periods
            }
            hit_count_text = " / ".join(
                f"{period}分×{hit_counts[period]}" for period in hit_periods
            )
            item["advice"] = (
                f"hit周期: {fmt_periods(hit_periods)}分 / "
                f"hit回数: {sum(hit_counts.values())}回（{hit_count_text}） / "
                + item["advice"]
            )
            items.append(f"""
<tr data-title="{rank}. {int(row['machine'])}番">
  <td data-label="優先">{rank}</td>
  <td data-label="台">{int(row['machine'])}番</td>
  <td data-label="評価">{row['grade']}</td>
  <td data-label="周期">{fmt_periods(row['periods'])}分</td>
  <td data-label="次の見る時間">{window_text(item['windows'])}</td>
  <td data-label="立ち回り">{item['advice']}</td>
</tr>""")
        priority_html = f"""
<section class="priority">
  <h2>厳密一致(日足zone + 日中hit)</h2>
  <table>
    <thead><tr><th>優先</th><th>台</th><th>評価</th><th>周期</th><th>次の見る時間</th><th>立ち回り</th></tr></thead>
    <tbody>{''.join(items)}</tbody>
  </table>
</section>"""

    shape_html = ""
    if shape_focus:
        items = []
        for rank, item in enumerate(shape_focus, 1):
            row = item["row"]
            strict = "周期一致" if item["strict"] else "形状注目"
            items.append(f"""
<tr data-title="{rank}. {int(row['machine'])}番">
  <td data-label="優先">{rank}</td>
  <td data-label="台">{int(row['machine'])}番</td>
  <td data-label="分類">{strict}</td>
  <td data-label="形状">{item['shape']['label']}<br>{item['shape']['detail']}</td>
  <td data-label="周期">{fmt_periods(row['periods'])}分</td>
  <td data-label="次の見る時間">{window_text(item['windows'])}</td>
  <td data-label="立ち回り">{item['advice']}</td>
</tr>""")
        shape_html = f"""
<section class="priority">
  <h2>形状込み注目</h2>
  <table>
    <thead><tr><th>優先</th><th>台</th><th>分類</th><th>形状</th><th>周期</th><th>次の見る時間</th><th>立ち回り</th></tr></thead>
    <tbody>{''.join(items)}</tbody>
  </table>
</section>"""

    reviews = data.get("reviews", {})
    review_html = ""
    if reviews:
        items = []
        for machine in sorted(reviews, key=lambda x: int(x)):
            item = reviews[machine]
            outcome = item.get("outcome") or "-"
            result = item.get("result") or "-"
            review = item.get("review") or "-"
            updated = item.get("updated_at") or "-"
            items.append(f"""
<tr data-title="{int(machine)}番">
  <td data-label="台">{int(machine)}番</td>
  <td data-label="判定">{esc(outcome)}</td>
  <td data-label="結果">{nl2br(result)}</td>
  <td data-label="レビュー">{nl2br(review)}</td>
  <td data-label="更新">{esc(updated)}</td>
</tr>""")
        review_html = f"""
<section class="priority">
  <h2>結果レビュー</h2>
  <table>
    <thead><tr><th>台</th><th>判定</th><th>結果</th><th>レビュー</th><th>更新</th></tr></thead>
    <tbody>{''.join(items)}</tbody>
  </table>
</section>"""

    screen_dir = cyclewatch_folder(date)
    list_rows = []
    priority_by_machine = {item["row"]["machine"]: item for item in priority}
    screenshot_rows = sorted(
        [row for row in ordered_rows if row["match"] or row["machine"] in ocr],
        key=lambda r: int(r["machine"]),
    )
    for row in screenshot_rows:
        machine = row["machine"]
        events = sorted(data["events"].get(machine, []))
        gaps, hit_periods = event_status(machine, row["periods"], events)
        windows = next_windows(events, row["periods"])
        zone_match = row["zone"] == row["best_zone"]
        has_hit = bool(hit_periods)
        strict_match = machine in historical_strict if historical_strict else has_hit and zone_match
        status = "hit-match" if strict_match else ("hit" if has_hit else ("watch" if zone_match else "standby"))
        status_label = {
            "hit-match": "日足一致 + 日中hit",
            "hit": "日中hit",
            "watch": "日足一致",
            "standby": "監視",
        }[status]
        event_text = " / ".join(fmt_time(t) for t in events) if events else "未入力"
        ocr_item = ocr.get(machine)
        ocr_text = (
            f"{ocr_item['atari']}回 / {signed(ocr_item['final'])}<br>"
            f"H {signed(ocr_item['high'])} / L {signed(ocr_item['low'])}"
            if ocr_item else "未OCR"
        )
        shape = graph_shape(ocr_item)
        shape_text = f"{shape['label']}<br>{shape['detail']}"
        gap_text = "<br>".join(
            f"{fmt_time(g['prev'])}→{fmt_time(g['cur'])} = {g['gap']}分"
            + (f" <b>HIT {fmt_periods(g['hits'])}分</b>" if g["hits"] else "")
            for g in gaps
        ) or "当たり2回目から判定"
        advice = priority_by_machine.get(machine, {}).get("advice")
        if not advice and has_hit and zone_match:
            advice = advice_for(row, windows, current_minute)
        advice = shape_adjusted_advice(
            advice or "日中hit待ち。初当たり時刻を追加して判定。",
            row,
            shape,
            has_hit,
            zone_match,
        )
        review_item = reviews.get(machine, {})
        review_text = auto_result_text(ocr_item)
        if review_item:
            review_text = (
                f"{esc(review_item.get('outcome') or '-')}<br>"
                f"{nl2br(review_item.get('result') or auto_result_text(ocr_item))}<br>"
                f"{nl2br(review_item.get('review') or '-')}"
            )
        list_rows.append(f"""
<tr class="{status}" data-title="{int(machine)}番">
  <td data-label="台">{int(machine)}番</td>
  <td data-label="評価">{row['grade']}</td>
  <td data-label="状態">{status_label}</td>
  <td data-label="日中周期">{fmt_periods(row['periods'])}分</td>
  <td data-label="期待/中央値">{row['hit_rate']:.1f}% / {row['no_rate']:.1f}%<br>{signed(row['hit_med'])} / {signed(row['no_med'])}</td>
  <td data-label="OCR現在">{ocr_text}</td>
  <td data-label="形状">{shape_text}</td>
  <td data-label="入力">{event_text}</td>
  <td data-label="差分">{gap_text}</td>
  <td data-label="次見る時間">{window_text(windows)}</td>
  <td data-label="立ち回り">{advice}</td>
  <td data-label="結果/レビュー">{review_text}</td>
</tr>""")
    html = f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Cycle Watch {date}</title>
<style>
*{{box-sizing:border-box}}body{{margin:0;background:#0d1117;color:#c9d1d9;font-family:'Segoe UI',Meiryo,sans-serif}}
header,main{{max-width:1180px;margin:auto}}header{{padding:22px 18px 12px}}main{{padding:0 18px 36px}}
a{{color:#58a6ff}}h1{{margin:0 0 6px;color:#58a6ff;font-size:24px}}.meta{{color:#8b949e;font-size:13px;line-height:1.6;overflow-wrap:anywhere}}
.priority,.watch-list{{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:14px;margin-top:14px;overflow:auto}}.priority h2,.watch-list h2{{font-size:18px;margin:0 0 10px;color:#f0f6fc}}table{{width:100%;border-collapse:collapse;font-size:13px}}th,td{{border-bottom:1px solid #30363d;padding:9px 8px;text-align:left;vertical-align:top}}th{{color:#8b949e;white-space:nowrap}}td:first-child{{font-weight:700;color:#3fb950;white-space:nowrap}}tr.hit-match{{background:#12351f}}tr.hit{{background:#332909}}tr.watch{{background:#10263c}}b{{color:#3fb950}}.path{{color:#8b949e;font-family:Consolas,monospace;font-size:12px;margin:4px 0 10px;overflow-wrap:anywhere}}
.cmd{{background:#010409;border:1px solid #30363d;border-radius:8px;padding:10px 12px;margin-top:12px;color:#8b949e;font-family:Consolas,monospace;font-size:12px;overflow:auto}}
@media (max-width: 720px){{
  header{{padding:14px 12px 6px}}main{{padding:0 10px 24px}}h1{{font-size:21px}}.meta{{font-size:12px}}.cmd{{font-size:11px;padding:8px 10px}}
  .priority,.watch-list{{padding:10px;margin-top:10px;overflow:visible}}.priority h2,.watch-list h2{{font-size:16px}}
  table,tbody,tr,td{{display:block;width:100%}}thead{{display:none}}table{{font-size:12px;border-collapse:separate;border-spacing:0}}
  tr{{border:1px solid #30363d;border-radius:8px;margin:0 0 10px;padding:8px 10px;background:#0d1117}}
  tr.hit-match{{background:#12351f}}tr.hit{{background:#332909}}tr.watch{{background:#10263c}}
  tr::before{{content:attr(data-title);display:block;color:#3fb950;font-weight:700;font-size:18px;padding-bottom:6px;border-bottom:1px solid #30363d;margin-bottom:4px}}
  td{{border-bottom:0;padding:5px 0;display:grid;grid-template-columns:86px minmax(0,1fr);column-gap:8px;line-height:1.45;white-space:normal;overflow-wrap:anywhere}}
  td::before{{content:attr(data-label);color:#8b949e;font-weight:600;white-space:nowrap}}
  td:first-child{{color:#c9d1d9;font-size:12px;white-space:normal;font-weight:400}}
  td:first-child::before{{content:attr(data-label)}}
}}
</style></head><body>
<header>
  <a href="cycle_watch_top.html">← Cycle Watch Top</a>
  <h1>Cycle Watch {date}</h1>
  <div class="meta">日足周期で監視対象を絞り、手入力した当たり開始時刻から日中周期hitを判定します。generated {generated}</div>
  <div class="meta">スクショ対象フォルダ: <span class="path">{screen_dir}</span></div>
  <div class="cmd">python cycle_watch.py add 39 1241<br>python cycle_watch.py review 39 --outcome ○ --result "終日+3000" --review "形状通りに伸びた"<br>python cycle_watch.py show --date {date}</div>
</header>
<main>{priority_html}{shape_html}{review_html}
<section class="watch-list">
  <h2>スクショ対象(日足候補・台番順)</h2>
  <div class="path">リネーム先: {screen_dir}</div>
  <table>
    <thead><tr><th>台</th><th>評価</th><th>状態</th><th>日中周期</th><th>期待/中央値</th><th>OCR現在</th><th>形状</th><th>入力</th><th>差分</th><th>次見る時間</th><th>立ち回り</th><th>結果/レビュー</th></tr></thead>
    <tbody>{''.join(list_rows)}</tbody>
  </table>
</section></main>
</body></html>"""
    dated = DOCS_DIR / f"cycle_watch_{date}.html"
    dated.write_text(html, encoding="utf-8")
    current = DOCS_DIR / "cycle_watch.html"
    current.write_text(html, encoding="utf-8")
    write_cycle_watch_top()
    return current


def cycle_watch_dates():
    dates = set()
    for path in DOCS_DIR.glob("cycle_watch_*.html"):
        stem = path.stem.replace("cycle_watch_", "")
        if stem.isdigit() and len(stem) == 8:
            dates.add(stem)
    for path in DATA_DIR.glob("cycle_watch_*.json"):
        stem = path.stem.replace("cycle_watch_", "")
        if stem.isdigit() and len(stem) == 8:
            dates.add(stem)
    return sorted(dates)


def strict_performance_rows(date):
    if not analyze_csv_path(date).exists():
        return []
    historical = historical_strict_machines(date)
    try:
        rows = watch_rows(date, {"A", "B"}, 40)
    except Exception:
        return []
    data = load_log(date)
    ocr = load_ocr_summary(date)
    rows = include_ocr_rows(date, rows, ocr)
    out = []
    for row in rows:
        machine = row["machine"]
        events = sorted(data["events"].get(machine, []))
        _, hit_periods = event_status(machine, row["periods"], events)
        if historical:
            if machine not in historical:
                continue
        elif not (hit_periods and row["zone"] == row["best_zone"]):
            continue
        ocr_item = ocr.get(machine)
        if not ocr_item:
            continue
        shape = graph_shape(ocr_item)
        review = data.get("reviews", {}).get(machine, {})
        out.append({
            "date": date,
            "machine": machine,
            "grade": row["grade"],
            "periods": row["periods"],
            "hit_periods": sorted(set(hit_periods)),
            "final": ocr_item["final"],
            "positive": ocr_item["final"] > 0,
            "atari": ocr_item["atari"],
            "shape": shape["label"],
            "outcome": review.get("outcome", ""),
            "review": review.get("review", ""),
        })
    out.sort(key=lambda item: (item["final"], item["atari"]), reverse=True)
    return out


def historical_strict_machines(date):
    path = cycle_watch_page_path(date)
    if not path.exists():
        return set()
    text = path.read_text(encoding="utf-8", errors="ignore")
    machines = set()
    for match in re.finditer(r'<tr class="hit-match" data-title="(\d+)番"', text):
        machines.add(match.group(1).zfill(3))
    return machines


def aggregate_performance(performance):
    by_machine = {}
    for item in performance:
        machine = item["machine"]
        bucket = by_machine.setdefault(machine, {
            "machine": machine,
            "grade": item["grade"],
            "count": 0,
            "positive": 0,
            "total_final": 0,
            "dates": [],
            "best_final": None,
            "latest_review": "",
        })
        bucket["count"] += 1
        bucket["positive"] += 1 if item["positive"] else 0
        bucket["total_final"] += item["final"]
        bucket["dates"].append(item["date"])
        if bucket["best_final"] is None or item["final"] > bucket["best_final"]:
            bucket["best_final"] = item["final"]
        if item.get("review"):
            bucket["latest_review"] = item["review"]

    out = []
    for item in by_machine.values():
        item["positive_rate"] = item["positive"] / item["count"] if item["count"] else 0
        item["avg_final"] = item["total_final"] / item["count"] if item["count"] else 0
        item["dates"] = sorted(set(item["dates"]))
        out.append(item)
    out.sort(
        key=lambda item: (
            item["positive_rate"],
            item["count"],
            item["avg_final"],
            item["total_final"],
        ),
        reverse=True,
    )
    return out


def write_cycle_watch_top():
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y%m%d")
    dates = cycle_watch_dates()
    if not dates:
        return None

    latest = dates[-1]
    performance = []
    for date in dates:
        performance.extend(strict_performance_rows(date))
    aggregate = aggregate_performance(performance)

    perf_rows = []
    for item in aggregate[:10]:
        result = (
            f"{item['positive']}/{item['count']} 陽線 "
            f"({item['positive_rate']:.0%})<br>"
            f"平均{signed(item['avg_final'])} / 合計{signed(item['total_final'])}"
        )
        dates_text = " / ".join(
            f'<a href="cycle_watch_{date}.html">{date}</a>' for date in item["dates"]
        )
        review = item["latest_review"] or "-"
        perf_rows.append(f"""
<tr data-title="{int(item['machine'])}番">
  <td data-label="台">{int(item['machine'])}番</td>
  <td data-label="評価">{esc(item['grade'])}</td>
  <td data-label="対象日">{dates_text}</td>
  <td data-label="件数">{item['count']}件</td>
  <td data-label="陽線率/差玉">{result}</td>
  <td data-label="最高差玉">{signed(item['best_final'])}</td>
  <td data-label="レビュー">{nl2br(review)}</td>
</tr>""")
    if not perf_rows:
        perf_rows.append("""
<tr data-title="未集計">
  <td data-label="日付">-</td>
  <td data-label="台">-</td>
  <td data-label="評価">-</td>
  <td data-label="対象日">-</td>
  <td data-label="件数">0件</td>
  <td data-label="陽線率/差玉">日足一致 + 日中hit の結果CSV待ち</td>
  <td data-label="最高差玉">-</td>
  <td data-label="レビュー">-</td>
</tr>""")

    prediction_rows = []
    result_rows = []
    for date in sorted(dates, reverse=True):
        page = cycle_watch_page_path(date).name
        has_result = analyze_csv_path(date).exists()
        data = load_log(date)
        review_count = len(data.get("reviews", {}))
        strict_count = len(strict_performance_rows(date)) if has_result else 0
        label = "結果あり" if has_result and date < today else ("監視中" if date >= today else "結果待ち")
        row_html = f"""
<tr data-title="{date}">
  <td data-label="日付"><a href="{page}">{date}</a></td>
  <td data-label="状態">{label}</td>
  <td data-label="厳密hit結果">{strict_count}台</td>
  <td data-label="レビュー">{review_count}件</td>
</tr>"""
        if date >= today:
            prediction_rows.append(row_html)
        else:
            result_rows.append(row_html)

    html = f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Cycle Watch Top</title>
<style>
*{{box-sizing:border-box}}body{{margin:0;background:#0d1117;color:#c9d1d9;font-family:'Segoe UI',Meiryo,sans-serif}}
header,main{{max-width:1120px;margin:auto}}header{{padding:22px 18px 8px}}main{{padding:0 18px 36px}}
a{{color:#58a6ff}}h1{{margin:0 0 6px;color:#58a6ff;font-size:24px}}.meta{{color:#8b949e;font-size:13px;line-height:1.6}}
section{{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:14px;margin-top:14px;overflow:auto}}h2{{font-size:18px;margin:0 0 10px;color:#f0f6fc}}
table{{width:100%;border-collapse:collapse;font-size:13px}}th,td{{border-bottom:1px solid #30363d;padding:9px 8px;text-align:left;vertical-align:top}}th{{color:#8b949e;white-space:nowrap}}td:first-child{{font-weight:700;white-space:nowrap}}b{{color:#3fb950}}
@media (max-width:720px){{header{{padding:14px 12px 6px}}main{{padding:0 10px 24px}}h1{{font-size:21px}}section{{padding:10px;overflow:visible}}table,tbody,tr,td{{display:block;width:100%}}thead{{display:none}}tr{{border:1px solid #30363d;border-radius:8px;margin:0 0 10px;padding:8px 10px;background:#0d1117}}tr::before{{content:attr(data-title);display:block;color:#3fb950;font-weight:700;font-size:18px;padding-bottom:6px;border-bottom:1px solid #30363d;margin-bottom:4px}}td{{border-bottom:0;padding:5px 0;display:grid;grid-template-columns:88px minmax(0,1fr);gap:8px;line-height:1.45;white-space:normal;overflow-wrap:anywhere}}td::before{{content:attr(data-label);color:#8b949e;font-weight:600;white-space:nowrap}}}}
</style></head><body>
<header>
  <a href="index.html">← dashboard</a>
  <h1>Cycle Watch Top</h1>
  <div class="meta">予測日と過去結果を日別に確認します。成績上位は結果CSVが揃っている期間全体で集計します。最新: <a href="cycle_watch_{latest}.html">{latest}</a></div>
</header>
<main>
<section>
  <h2>日足周期 + 日中周期 成績上位(期間集計)</h2>
  <table>
    <thead><tr><th>台</th><th>評価</th><th>対象日</th><th>件数</th><th>陽線率/差玉</th><th>最高差玉</th><th>レビュー</th></tr></thead>
    <tbody>{''.join(perf_rows)}</tbody>
  </table>
</section>
<section>
  <h2>予測日</h2>
  <table>
    <thead><tr><th>日付</th><th>状態</th><th>厳密hit結果</th><th>レビュー</th></tr></thead>
    <tbody>{''.join(prediction_rows) if prediction_rows else '<tr data-title="なし"><td data-label="日付">-</td><td data-label="状態">予測日なし</td><td data-label="厳密hit結果">-</td><td data-label="レビュー">-</td></tr>'}</tbody>
  </table>
</section>
<section>
  <h2>過去の結果</h2>
  <table>
    <thead><tr><th>日付</th><th>状態</th><th>厳密hit結果</th><th>レビュー</th></tr></thead>
    <tbody>{''.join(result_rows) if result_rows else '<tr data-title="なし"><td data-label="日付">-</td><td data-label="状態">過去結果なし</td><td data-label="厳密hit結果">-</td><td data-label="レビュー">-</td></tr>'}</tbody>
  </table>
</section>
</main></body></html>"""
    out = DOCS_DIR / "cycle_watch_top.html"
    out.write_text(html, encoding="utf-8")
    return out


def cmd_page(args):
    all_days, _ = load_config_cache(force=args.refresh)
    date = args.date or default_watch_date(all_days)
    grades = set(args.grades.split(",")) if args.grades else {"A", "B"}
    rows = watch_rows(date, grades, args.top)
    out = write_watch_page(date, rows)
    print(out)


def cmd_top(args):
    load_config_cache(force=args.refresh)
    out = write_cycle_watch_top()
    print(out)


def cmd_folders(args):
    all_days, _ = load_config_cache(force=args.refresh)
    date = args.date or default_watch_date(all_days)
    folder = cyclewatch_folder(date)
    folder.mkdir(parents=True, exist_ok=True)
    print(folder)


def main():
    parser = argparse.ArgumentParser(description="日足監視リスト + 日中周期hit手入力ツール")
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="当日の監視リストを表示")
    p_list.add_argument("--date", help="YYYYMMDD。未指定なら今日または最新日")
    p_list.add_argument("--grades", default="A,B", help="表示評価。例: A,B,C+")
    p_list.add_argument("--top", type=int, default=30, help="表示件数。0で全件")
    p_list.add_argument("--refresh", action="store_true", help="監視設定キャッシュを再生成")
    p_list.add_argument("--write-page", action="store_true", help="docs/cycle_watch.html を更新")
    p_list.set_defaults(func=cmd_list)

    p_add = sub.add_parser("add", help="当たり時刻を記録")
    p_add.add_argument("machine", help="台番")
    p_add.add_argument("time", help="時刻。例: 1241 または 12:41")
    p_add.add_argument("--date", help="YYYYMMDD。未指定なら今日または最新日")
    p_add.add_argument("--dry-run", action="store_true", help="ログ保存せず判定だけ行う")
    p_add.add_argument("--refresh", action="store_true", help="監視設定キャッシュを再生成")
    p_add.set_defaults(func=cmd_add)

    p_show = sub.add_parser("show", help="入力ログを表示")
    p_show.add_argument("--date", help="YYYYMMDD。未指定なら今日または最新日")
    p_show.add_argument("--refresh", action="store_true", help="監視設定キャッシュを再生成")
    p_show.set_defaults(func=cmd_show)

    p_review = sub.add_parser("review", help="翌日の答え合わせ結果とレビューを記録")
    p_review.add_argument("machine", help="台番")
    p_review.add_argument("--date", help="YYYYMMDD。監視した日付")
    p_review.add_argument("--outcome", default=None, help="判定。例: ○/△/×/保留")
    p_review.add_argument("--result", default=None, help="結果メモ。例: 終日+3000、初当たり3回")
    p_review.add_argument("--review", default=None, help="振り返り。周期/形状/立ち回りの評価")
    p_review.add_argument("--clear", action="store_true", help="この台のレビューを削除")
    p_review.add_argument("--refresh", action="store_true", help="監視設定キャッシュを再生成")
    p_review.set_defaults(func=cmd_review)

    p_page = sub.add_parser("page", help="候補ページを生成")
    p_page.add_argument("--date", help="YYYYMMDD。未指定なら今日または最新日")
    p_page.add_argument("--grades", default="A,B", help="表示評価。例: A,B,C+")
    p_page.add_argument("--top", type=int, default=40, help="表示件数。0で全件")
    p_page.add_argument("--refresh", action="store_true", help="監視設定キャッシュを再生成")
    p_page.set_defaults(func=cmd_page)

    p_top = sub.add_parser("top", help="Cycle Watchの実績一覧トップを生成")
    p_top.add_argument("--refresh", action="store_true", help="監視設定キャッシュを再生成")
    p_top.set_defaults(func=cmd_top)

    p_folders = sub.add_parser("folders", help="Cycle Watchスクショ用フォルダを作成")
    p_folders.add_argument("--date", help="YYYYMMDD。未指定なら今日または最新日")
    p_folders.add_argument("--refresh", action="store_true", help="監視設定キャッシュを再生成")
    p_folders.set_defaults(func=cmd_folders)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
