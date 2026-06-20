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
import json
import math
from datetime import datetime, timedelta
from pathlib import Path

import aggregate_cycle_sync_68 as agg
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


def load_log(date):
    path = log_path(date)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"date": date, "events": {}}


def save_log(data):
    path = log_path(data["date"])
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


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


def write_watch_page(date, rows):
    data = load_log(date)
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    cards = []
    for row in rows:
        machine = row["machine"]
        events = sorted(data["events"].get(machine, []))
        gaps, hit_periods = event_status(machine, row["periods"], events)
        zone_match = row["zone"] == row["best_zone"]
        has_hit = bool(hit_periods)
        status = "hit-match" if has_hit and zone_match else ("hit" if has_hit else ("watch" if zone_match else "standby"))
        status_label = {
            "hit-match": "日足一致 + 日中hit",
            "hit": "日中hit",
            "watch": "日足一致",
            "standby": "監視",
        }[status]
        event_text = " / ".join(fmt_time(t) for t in events) if events else "未入力"
        gap_text = "<br>".join(
            f"{fmt_time(g['prev'])}→{fmt_time(g['cur'])} = {g['gap']}分"
            + (f" <b>HIT {fmt_periods(g['hits'])}分</b>" if g["hits"] else "")
            for g in gaps
        ) or "当たり2回目から判定"
        cards.append(f"""
<article class="card {status}">
  <div class="head"><h2>{int(machine)}番</h2><span>{row['grade']}</span></div>
  <div class="status">{status_label}</div>
  <dl>
    <dt>日足</dt><dd>{row['zone']} / 期待 {row['best_zone']}</dd>
    <dt>日中周期</dt><dd>{fmt_periods(row['periods'])}分 ±{TOLERANCE}</dd>
    <dt>期待</dt><dd>{row['hit_rate']:.1f}% / no-hit {row['no_rate']:.1f}%</dd>
    <dt>中央値</dt><dd>{signed(row['hit_med'])} / {signed(row['no_med'])}</dd>
    <dt>入力</dt><dd>{event_text}</dd>
    <dt>差分</dt><dd>{gap_text}</dd>
  </dl>
</article>""")
    html = f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Cycle Watch {date}</title>
<style>
*{{box-sizing:border-box}}body{{margin:0;background:#0d1117;color:#c9d1d9;font-family:'Segoe UI',Meiryo,sans-serif}}
header,main{{max-width:1180px;margin:auto}}header{{padding:22px 18px 12px}}main{{padding:0 18px 36px}}
a{{color:#58a6ff}}h1{{margin:0 0 6px;color:#58a6ff;font-size:24px}}.meta{{color:#8b949e;font-size:13px;line-height:1.6}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px;margin-top:14px}}
.card{{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:13px}}
.card.hit-match{{border-color:#3fb950;box-shadow:0 0 0 1px #23863655}}.card.hit{{border-color:#d29922}}.card.watch{{border-color:#58a6ff}}
.head{{display:flex;align-items:center;justify-content:space-between;gap:8px}}h2{{font-size:20px;margin:0}}.head span{{color:#8b949e;border:1px solid #30363d;padding:2px 8px;border-radius:999px;font-size:12px}}
.status{{margin:8px 0 10px;font-weight:700;color:#f0f6fc}}dl{{display:grid;grid-template-columns:78px 1fr;gap:6px 8px;margin:0;font-size:13px}}dt{{color:#8b949e}}dd{{margin:0}}b{{color:#3fb950}}
.cmd{{background:#010409;border:1px solid #30363d;border-radius:8px;padding:10px 12px;margin-top:12px;color:#8b949e;font-family:Consolas,monospace;font-size:12px;overflow:auto}}
</style></head><body>
<header>
  <a href="index.html">← top</a>
  <h1>Cycle Watch {date}</h1>
  <div class="meta">日足周期で監視対象を絞り、手入力した当たり開始時刻から日中周期hitを判定します。generated {generated}</div>
  <div class="cmd">python cycle_watch.py add 39 1241<br>python cycle_watch.py show --date {date}</div>
</header>
<main><section class="grid">{''.join(cards)}</section></main>
</body></html>"""
    dated = DOCS_DIR / f"cycle_watch_{date}.html"
    dated.write_text(html, encoding="utf-8")
    current = DOCS_DIR / "cycle_watch.html"
    current.write_text(html, encoding="utf-8")
    return current


def cmd_page(args):
    all_days, _ = load_config_cache(force=args.refresh)
    date = args.date or default_watch_date(all_days)
    grades = set(args.grades.split(",")) if args.grades else {"A", "B"}
    rows = watch_rows(date, grades, args.top)
    out = write_watch_page(date, rows)
    print(out)


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

    p_page = sub.add_parser("page", help="候補ページを生成")
    p_page.add_argument("--date", help="YYYYMMDD。未指定なら今日または最新日")
    p_page.add_argument("--grades", default="A,B", help="表示評価。例: A,B,C+")
    p_page.add_argument("--top", type=int, default=40, help="表示件数。0で全件")
    p_page.add_argument("--refresh", action="store_true", help="監視設定キャッシュを再生成")
    p_page.set_defaults(func=cmd_page)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
