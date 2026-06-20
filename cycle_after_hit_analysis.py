"""
周期hitが出た時点までの伸びと、hit後の伸びを分けて検証する。

対象は machine_cycle_positive.py で再現候補になった4件:
  52番 70分, 45番 50分, 69番 80分, 39番 50分

hit確認時刻は「周期に合った2回目の当たり開始時刻」とする。
hitなし日の比較は、同じ台の検証期間におけるhit確認時刻の中央値をアンカーにする。
"""

import csv
import statistics
from pathlib import Path

import machine_cycle_positive as cycle


ROOT = Path(__file__).parent
CSV_DIR = ROOT / "csv" / "analyze"
REPORT_DIR = ROOT / "reports"

ATARI = {"\u5f53\u308a", "\u5927\u5f53\u308a"}
CANDIDATES = (("052", 70), ("045", 50), ("069", 80), ("039", 50))


def parse_time(value):
    h, m = str(value).strip().split(":")
    return int(h) * 60 + int(m)


def format_time(minutes):
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def to_int(value):
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return 0


def median(values):
    return int(round(statistics.median(values))) if values else 0


def mean(values):
    return sum(values) / len(values) if values else 0.0


def pct(part, total):
    return part / total * 100 if total else 0.0


def ball_at(rows, minute):
    if not rows:
        return 0
    rows = sorted(rows, key=lambda r: parse_time(r[5]))
    for row in rows:
        start = parse_time(row[5])
        end = parse_time(row[7])
        start_ball = to_int(row[6])
        end_ball = to_int(row[8])
        if start <= minute <= end:
            if end <= start:
                return end_ball
            ratio = (minute - start) / (end - start)
            return int(round(start_ball + (end_ball - start_ball) * ratio))
    if minute < parse_time(rows[0][5]):
        return 0
    return to_int(rows[-1][8])


def load_days(machine_set):
    loaded = {}
    for path in sorted(CSV_DIR.glob("*/*_analyze.csv")):
        date = path.parent.name[:8]
        by_machine = {}
        with path.open(encoding="utf-8-sig", newline="") as f:
            reader = csv.reader(f)
            next(reader, None)
            for row in reader:
                if len(row) < 11:
                    continue
                machine = str(row[1]).zfill(3)
                if machine not in machine_set:
                    continue
                by_machine.setdefault(machine, []).append(row)
        for machine, rows in by_machine.items():
            rows.sort(key=lambda r: parse_time(r[5]))
            events = [(parse_time(r[5]), r[5], r[4]) for r in rows if r[4] in ATARI]
            latest = max(rows, key=lambda r: parse_time(r[7]))
            loaded[(date, machine)] = {
                "date": date,
                "machine": machine,
                "group": rows[0][2],
                "island": rows[0][3],
                "rows": rows,
                "events": events,
                "final": to_int(latest[8]),
            }
    return loaded


def first_hit(events, period, tolerance):
    for left, right in zip(events, events[1:]):
        gap = right[0] - left[0]
        if abs(gap - period) <= tolerance:
            return {
                "first_time": left[0],
                "confirm_time": right[0],
                "gap": gap,
            }
    return None


def analyze_candidate(days, valid_dates, machine, period, tolerance):
    hit_rows = []
    nohit_rows = []
    for (date, day_machine), day in sorted(days.items()):
        if day_machine != machine or date not in valid_dates:
            continue
        hit = first_hit(day["events"], period, tolerance)
        if hit:
            at_hit = ball_at(day["rows"], hit["confirm_time"])
            hit_rows.append({
                **day,
                **hit,
                "at_hit": at_hit,
                "post_delta": day["final"] - at_hit,
            })
        else:
            nohit_rows.append(day)

    anchor = median([row["confirm_time"] for row in hit_rows])
    nohit_anchor_rows = []
    for day in nohit_rows:
        at_anchor = ball_at(day["rows"], anchor)
        nohit_anchor_rows.append({
            **day,
            "anchor_time": anchor,
            "at_anchor": at_anchor,
            "post_delta": day["final"] - at_anchor,
        })
    return hit_rows, nohit_anchor_rows, anchor


def summarize_hit(rows):
    finals = [r["final"] for r in rows]
    at_hits = [r["at_hit"] for r in rows]
    post = [r["post_delta"] for r in rows]
    return {
        "n": len(rows),
        "final_pos": sum(1 for r in rows if r["final"] > 0),
        "at_hit_pos": sum(1 for r in rows if r["at_hit"] > 0),
        "post_pos": sum(1 for r in rows if r["post_delta"] > 0),
        "final_avg": mean(finals),
        "final_med": median(finals),
        "at_hit_avg": mean(at_hits),
        "at_hit_med": median(at_hits),
        "post_avg": mean(post),
        "post_med": median(post),
        "early_neg_to_pos": sum(1 for r in rows if r["at_hit"] <= 0 and r["final"] > 0),
        "early_pos_to_neg": sum(1 for r in rows if r["at_hit"] > 0 and r["final"] <= 0),
    }


def summarize_anchor(rows):
    finals = [r["final"] for r in rows]
    anchors = [r["at_anchor"] for r in rows]
    post = [r["post_delta"] for r in rows]
    return {
        "n": len(rows),
        "final_pos": sum(1 for r in rows if r["final"] > 0),
        "anchor_pos": sum(1 for r in rows if r["at_anchor"] > 0),
        "post_pos": sum(1 for r in rows if r["post_delta"] > 0),
        "final_avg": mean(finals),
        "final_med": median(finals),
        "anchor_avg": mean(anchors),
        "anchor_med": median(anchors),
        "post_avg": mean(post),
        "post_med": median(post),
    }


def signed(value):
    return f"{int(round(value)):+,}"


def make_detail_rows(rows, limit=15):
    ordered = sorted(rows, key=lambda r: r["date"])
    lines = [
        "|日付|gap|確認時刻|hit時点|hit後|終値|",
        "|---:|---:|---:|---:|---:|---:|",
    ]
    for row in ordered[:limit]:
        lines.append(
            f"|{row['date']}|{row['gap']}分|{format_time(row['confirm_time'])}|"
            f"{signed(row['at_hit'])}|{signed(row['post_delta'])}|{signed(row['final'])}|"
        )
    if len(ordered) > limit:
        lines.append(f"|...|...|...|...|...|残り{len(ordered) - limit}日|")
    return "\n".join(lines)


def main():
    machine_set = {machine for machine, _ in CANDIDATES}
    cycle_days = cycle.load_machine_days(machine_set)
    _, valid_dates = cycle.split_dates(cycle_days)
    days = load_days(machine_set)

    sections = [
        "# 周期hit後の伸び検証",
        "",
        "- 対象期間: 検証期間のみ",
        f"- 検証期間: {min(valid_dates)} ～ {max(valid_dates)} ({len(valid_dates)}日)",
        "- hit確認時刻: 周期に合った2回目の当たり開始時刻",
        "- hit後差分: 最終差玉 - hit確認時点差玉",
        "- hitなし比較: 同じ台のhit確認時刻中央値をアンカーにした終値差分",
        "",
        "## サマリ",
        "",
        "|台|周期|hit日|hit終値陽線|hit時点陽線|hit後プラス|hit時点中央値|hit後中央値|終値中央値|hitなし後プラス|hitなし後中央値|",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]

    details = []
    for machine, period in CANDIDATES:
        hit_rows, nohit_rows, anchor = analyze_candidate(days, valid_dates, machine, period, 5)
        hs = summarize_hit(hit_rows)
        ns = summarize_anchor(nohit_rows)
        sections.append(
            f"|{int(machine)}|{period}分|{hs['n']}|"
            f"{hs['final_pos']}/{hs['n']} ({pct(hs['final_pos'], hs['n']):.1f}%)|"
            f"{hs['at_hit_pos']}/{hs['n']} ({pct(hs['at_hit_pos'], hs['n']):.1f}%)|"
            f"{hs['post_pos']}/{hs['n']} ({pct(hs['post_pos'], hs['n']):.1f}%)|"
            f"{signed(hs['at_hit_med'])}|{signed(hs['post_med'])}|{signed(hs['final_med'])}|"
            f"{ns['post_pos']}/{ns['n']} ({pct(ns['post_pos'], ns['n']):.1f}%)|{signed(ns['post_med'])}|"
        )
        details.extend([
            "",
            f"## {int(machine)}番 {period}分",
            "",
            f"- 比較アンカー時刻: {format_time(anchor)}",
            f"- hit日: 終値陽線 {hs['final_pos']}/{hs['n']}、hit時点陽線 {hs['at_hit_pos']}/{hs['n']}、hit後プラス {hs['post_pos']}/{hs['n']}",
            f"- hit日の中央値: hit時点 {signed(hs['at_hit_med'])}、hit後 {signed(hs['post_med'])}、終値 {signed(hs['final_med'])}",
            f"- hitなし日の同時刻比較: 後半プラス {ns['post_pos']}/{ns['n']}、後半中央値 {signed(ns['post_med'])}、終値中央値 {signed(ns['final_med'])}",
            f"- hit時点では非陽線だが終値陽線: {hs['early_neg_to_pos']}/{hs['n']}",
            f"- hit時点では陽線だが終値陰線: {hs['early_pos_to_neg']}/{hs['n']}",
            "",
            make_detail_rows(hit_rows),
        ])

    report = "\n".join(sections + details) + "\n"
    out = REPORT_DIR / "cycle_after_hit_analysis.md"
    out.write_text(report, encoding="utf-8")
    print(out)
    print("\n".join(sections[:15]))


if __name__ == "__main__":
    main()
