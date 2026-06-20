"""
周期hitを実戦ルールに落とすための条件分岐を作る。

見る軸:
  - hit確認時刻
  - hit時点差玉
  - hit後差分

対象:
  52番 70分, 45番 50分, 69番 80分, 39番 50分
"""

from pathlib import Path

import cycle_after_hit_analysis as after_hit
import machine_cycle_positive as cycle


ROOT = Path(__file__).parent
REPORT_DIR = ROOT / "reports"

CANDIDATES = (("052", 70), ("045", 50), ("069", 80), ("039", 50))

TIME_BUCKETS = (
    ("午前-13時台", None, 14 * 60),
    ("14-15時台", 14 * 60, 16 * 60),
    ("16-17時台", 16 * 60, 18 * 60),
    ("18時以降", 18 * 60, None),
)

BALL_BUCKETS = (
    ("-5000以下", None, -5000),
    ("-5000～0", -5000, 0),
    ("0～5000", 0, 5000),
    ("5000超", 5000, None),
)


def in_range(value, low, high):
    if low is not None and value < low:
        return False
    if high is not None and value >= high:
        return False
    return True


def median(values):
    return after_hit.median(values)


def signed(value):
    return after_hit.signed(value)


def pct(part, total):
    return after_hit.pct(part, total)


def summarize(rows):
    post = [row["post_delta"] for row in rows]
    finals = [row["final"] for row in rows]
    at_hit = [row["at_hit"] for row in rows]
    n = len(rows)
    return {
        "n": n,
        "final_pos": sum(1 for row in rows if row["final"] > 0),
        "post_pos": sum(1 for row in rows if row["post_delta"] > 0),
        "post_med": median(post),
        "post_avg": sum(post) / n if n else 0,
        "final_med": median(finals),
        "at_hit_med": median(at_hit),
    }


def table_for_buckets(rows, buckets, field, title):
    lines = [
        f"### {title}",
        "",
        "|条件|件数|終値陽線|hit後プラス|hit時点中央値|hit後中央値|hit後平均|終値中央値|",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for label, low, high in buckets:
        sub = [row for row in rows if in_range(row[field], low, high)]
        s = summarize(sub)
        if not sub:
            lines.append(f"|{label}|0|-|-|-|-|-|-|")
            continue
        lines.append(
            f"|{label}|{s['n']}|"
            f"{s['final_pos']}/{s['n']} ({pct(s['final_pos'], s['n']):.1f}%)|"
            f"{s['post_pos']}/{s['n']} ({pct(s['post_pos'], s['n']):.1f}%)|"
            f"{signed(s['at_hit_med'])}|{signed(s['post_med'])}|"
            f"{signed(s['post_avg'])}|{signed(s['final_med'])}|"
        )
    return "\n".join(lines)


def combined_rule_table(rows):
    lines = [
        "### 時刻 x 差玉",
        "",
        "|時刻|差玉|件数|hit後プラス|hit後中央値|終値陽線|",
        "|---|---|---:|---:|---:|---:|",
    ]
    for time_label, time_low, time_high in TIME_BUCKETS:
        for ball_label, ball_low, ball_high in BALL_BUCKETS:
            sub = [
                row for row in rows
                if in_range(row["confirm_time"], time_low, time_high)
                and in_range(row["at_hit"], ball_low, ball_high)
            ]
            if not sub:
                continue
            s = summarize(sub)
            lines.append(
                f"|{time_label}|{ball_label}|{s['n']}|"
                f"{s['post_pos']}/{s['n']} ({pct(s['post_pos'], s['n']):.1f}%)|"
                f"{signed(s['post_med'])}|"
                f"{s['final_pos']}/{s['n']} ({pct(s['final_pos'], s['n']):.1f}%)|"
            )
    return "\n".join(lines)


def rule_candidates(rows, min_count=3):
    candidates = []
    for time_label, time_low, time_high in TIME_BUCKETS:
        for ball_label, ball_low, ball_high in BALL_BUCKETS:
            sub = [
                row for row in rows
                if in_range(row["confirm_time"], time_low, time_high)
                and in_range(row["at_hit"], ball_low, ball_high)
            ]
            if len(sub) < min_count:
                continue
            s = summarize(sub)
            candidates.append((time_label, ball_label, s))
    candidates.sort(key=lambda item: (item[2]["post_pos"] / item[2]["n"], item[2]["post_med"], item[2]["n"]), reverse=True)
    return candidates


def main():
    machine_set = {machine for machine, _ in CANDIDATES}
    cycle_days = cycle.load_machine_days(machine_set)
    _, valid_dates = cycle.split_dates(cycle_days)
    days = after_hit.load_days(machine_set)

    sections = [
        "# 周期hitの実戦条件分析",
        "",
        f"- 対象期間: {min(valid_dates)} ～ {max(valid_dates)} ({len(valid_dates)}日)",
        "- hit確認時刻: 周期に合った2回目の当たり開始時刻",
        "- 評価: hit確認時点から閉店までの差玉",
        "- 件数が3未満の細分条件は参考外",
        "",
        "## 推奨ルール候補",
        "",
        "|台|周期|条件|件数|hit後プラス|hit後中央値|終値陽線|",
        "|---:|---:|---|---:|---:|---:|---:|",
    ]

    details = []
    for machine, period in CANDIDATES:
        hit_rows, _, _ = after_hit.analyze_candidate(days, valid_dates, machine, period, 5)
        for time_label, ball_label, s in rule_candidates(hit_rows):
            if s["post_pos"] / s["n"] < 0.70 or s["post_med"] <= 0:
                continue
            sections.append(
                f"|{int(machine)}|{period}分|{time_label} / {ball_label}|{s['n']}|"
                f"{s['post_pos']}/{s['n']} ({pct(s['post_pos'], s['n']):.1f}%)|"
                f"{signed(s['post_med'])}|"
                f"{s['final_pos']}/{s['n']} ({pct(s['final_pos'], s['n']):.1f}%)|"
            )

        details.extend([
            "",
            f"## {int(machine)}番 {period}分",
            "",
            table_for_buckets(hit_rows, TIME_BUCKETS, "confirm_time", "hit確認時刻別"),
            "",
            table_for_buckets(hit_rows, BALL_BUCKETS, "at_hit", "hit時点差玉別"),
            "",
            combined_rule_table(hit_rows),
        ])

    out = REPORT_DIR / "cycle_entry_rule_analysis.md"
    out.write_text("\n".join(sections + details) + "\n", encoding="utf-8")
    print(out)
    print("\n".join(sections[:30]))


if __name__ == "__main__":
    main()
