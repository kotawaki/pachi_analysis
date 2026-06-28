"""
台別の当たり間隔周期が陽線日に偏るかを検証する。

目的:
  - 当否予測ではなく、当り/大当りの連続開始間隔が陽線日で増えるかを見る。
  - 周期候補は固定し、前半期間で候補抽出、後半期間で検証する。

例:
  python machine_cycle_positive.py
  python machine_cycle_positive.py --machines 39-77 --top 30
"""

import argparse
import csv
import math
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).parent
CSV_DIR = ROOT / "csv" / "analyze"
REPORT_DIR = ROOT / "reports"

ATARI_KINDS = {"当り", "大当り"}
DEFAULT_PERIODS = tuple(range(20, 181, 10))


def normalize_machine(value):
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        number = int(text)
    except ValueError:
        return text
    return f"{number:03d}" if number < 1000 else str(number)


def parse_time(value):
    h, m = str(value).strip().split(":")
    return int(h) * 60 + int(m)


def to_int(value, default=0):
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


def parse_machine_spec(spec):
    if not spec:
        return None
    machines = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            left, right = part.split("-", 1)
            start = int(left)
            end = int(right)
            machines.update(f"{n:03d}" for n in range(start, end + 1))
        else:
            machines.add(normalize_machine(part))
    return machines


def iter_csv_paths():
    for path in sorted(CSV_DIR.glob("*/*_analyze.csv")):
        yield path


def load_machine_days(machine_filter=None):
    days = {}
    for path in iter_csv_paths():
        date = path.parent.name[:8]
        per_machine = defaultdict(list)
        with path.open(encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                machine = normalize_machine(row.get("Machine", ""))
                if machine_filter and machine not in machine_filter:
                    continue
                per_machine[machine].append(row)

        for machine, rows in per_machine.items():
            rows.sort(key=lambda r: parse_time(r["開始時刻"]))
            events = [
                parse_time(r["開始時刻"])
                for r in rows
                if str(r.get("種別", "")).strip() in ATARI_KINDS
            ]
            intervals = [b - a for a, b in zip(events, events[1:]) if b > a]
            if not rows:
                continue
            latest = max(rows, key=lambda r: parse_time(r["終了時刻"]))
            final_close = to_int(latest.get("終了差玉"))
            first = rows[0]
            days[(date, machine)] = {
                "date": date,
                "machine": machine,
                "group": str(first.get("Group", "")).strip(),
                "island": str(first.get("Island", "")).strip(),
                "final_close": final_close,
                "positive": final_close > 0,
                "event_count": len(events),
                "intervals": intervals,
            }
    return days


def split_dates(days, split_date=None):
    dates = sorted({item["date"] for item in days.values()})
    if not dates:
        return set(), set()
    if split_date:
        train = {d for d in dates if d <= split_date}
        valid = {d for d in dates if d > split_date}
        return train, valid
    mid = max(1, len(dates) // 2)
    if mid >= len(dates):
        mid = len(dates) - 1
    return set(dates[:mid]), set(dates[mid:])


def empty_bucket():
    return {
        "pos_hits": 0,
        "pos_total": 0,
        "neg_hits": 0,
        "neg_total": 0,
        "pos_days": set(),
        "neg_days": set(),
        "hit_days": set(),
    }


def collect_stats(days, date_set, periods, tolerance):
    stats = defaultdict(empty_bucket)
    machine_meta = {}
    machine_day_counts = defaultdict(lambda: {"pos": 0, "neg": 0})
    for item in days.values():
        if item["date"] not in date_set:
            continue
        machine = item["machine"]
        machine_meta[machine] = {"group": item["group"], "island": item["island"]}
        if item["positive"]:
            machine_day_counts[machine]["pos"] += 1
        else:
            machine_day_counts[machine]["neg"] += 1
        for period in periods:
            key = (machine, period)
            bucket = stats[key]
            hits = sum(1 for gap in item["intervals"] if abs(gap - period) <= tolerance)
            total = len(item["intervals"])
            if item["positive"]:
                bucket["pos_hits"] += hits
                bucket["pos_total"] += total
                bucket["pos_days"].add(item["date"])
            else:
                bucket["neg_hits"] += hits
                bucket["neg_total"] += total
                bucket["neg_days"].add(item["date"])
            if hits:
                bucket["hit_days"].add(item["date"])
    return stats, machine_meta, machine_day_counts


def score_bucket(bucket):
    pos_total = bucket["pos_total"]
    neg_total = bucket["neg_total"]
    all_hits = bucket["pos_hits"] + bucket["neg_hits"]
    all_total = pos_total + neg_total
    pos_rate = bucket["pos_hits"] / pos_total if pos_total else 0.0
    neg_rate = bucket["neg_hits"] / neg_total if neg_total else 0.0
    base_rate = all_hits / all_total if all_total else 0.0
    lift = pos_rate / base_rate if base_rate else 0.0
    diff = pos_rate - neg_rate
    # 2x2の簡易z。探索順位用で、最終判断は検証期間の再現を見る。
    if pos_total and neg_total and all_total:
        pooled = base_rate
        se = math.sqrt(max(pooled * (1 - pooled) * (1 / pos_total + 1 / neg_total), 1e-12))
        z = diff / se
    else:
        z = 0.0
    return {
        "pos_rate": pos_rate,
        "neg_rate": neg_rate,
        "base_rate": base_rate,
        "lift": lift,
        "diff": diff,
        "z": z,
        "total": all_total,
        "hits": all_hits,
    }


def build_rows(stats, machine_meta, day_counts, min_intervals):
    rows = []
    for (machine, period), bucket in stats.items():
        scored = score_bucket(bucket)
        if scored["total"] < min_intervals:
            continue
        meta = machine_meta.get(machine, {})
        counts = day_counts[machine]
        rows.append({
            "machine": machine,
            "period": period,
            "group": meta.get("group", ""),
            "island": meta.get("island", ""),
            "positive_days": counts["pos"],
            "negative_days": counts["neg"],
            "pos_hits": bucket["pos_hits"],
            "pos_total": bucket["pos_total"],
            "neg_hits": bucket["neg_hits"],
            "neg_total": bucket["neg_total"],
            "hit_days": len(bucket["hit_days"]),
            **scored,
        })
    rows.sort(key=lambda r: (r["z"], r["lift"], r["hits"], r["total"]), reverse=True)
    return rows


def index_rows(rows):
    return {(r["machine"], r["period"]): r for r in rows}


def fmt_pct(value):
    return f"{value * 100:.1f}%"


def markdown_table(rows, limit, include_validation=False):
    if include_validation:
        header = (
            "|順位|台|G|島|周期|学習lift|学習 陽線/陰線|検証lift|検証 陽線/陰線|検証件数|検証hit日|\n"
            "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"
        )
    else:
        header = (
            "|順位|台|G|島|周期|lift|陽線率|陰線率|hit/件数|陽線日/陰線日|hit日|\n"
            "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"
        )
    lines = [header]
    for i, row in enumerate(rows[:limit], 1):
        if include_validation:
            valid = row.get("valid")
            if valid:
                valid_lift = f"{valid['lift']:.2f}"
                valid_rates = f"{fmt_pct(valid['pos_rate'])}/{fmt_pct(valid['neg_rate'])}"
                valid_total = f"{valid['hits']}/{valid['total']}"
                valid_hit_days = str(valid["hit_days"])
            else:
                valid_lift = "-"
                valid_rates = "-"
                valid_total = "-"
                valid_hit_days = "-"
            lines.append(
                f"|{i}|{int(row['machine'])}|{row['group']}|{row['island']}|{row['period']}分|"
                f"{row['lift']:.2f}|{fmt_pct(row['pos_rate'])}/{fmt_pct(row['neg_rate'])}|"
                f"{valid_lift}|{valid_rates}|{valid_total}|{valid_hit_days}|"
            )
        else:
            lines.append(
                f"|{i}|{int(row['machine'])}|{row['group']}|{row['island']}|{row['period']}分|"
                f"{row['lift']:.2f}|{fmt_pct(row['pos_rate'])}|{fmt_pct(row['neg_rate'])}|"
                f"{row['hits']}/{row['total']}|{row['positive_days']}/{row['negative_days']}|{row['hit_days']}|"
            )
    return "\n".join(lines)


def replicated_rows(rows, args):
    out = []
    for row in rows:
        valid = row.get("valid")
        if not valid:
            continue
        if row["lift"] < args.min_train_lift:
            continue
        if valid["lift"] < args.min_valid_lift:
            continue
        if valid["hits"] < args.min_valid_hits:
            continue
        out.append(row)
    out.sort(
        key=lambda r: (
            r["valid"]["lift"],
            r["valid"]["hits"],
            r["lift"],
        ),
        reverse=True,
    )
    return out


def make_report(args, days, train_dates, valid_dates, train_rows, valid_index):
    combined = []
    for row in train_rows:
        copy = dict(row)
        copy["valid"] = valid_index.get((row["machine"], row["period"]))
        combined.append(copy)
    replicated = replicated_rows(combined, args)

    all_dates = sorted({item["date"] for item in days.values()})
    target = args.machines or "全台"
    lines = [
        "# 台別周期と陽線日の関係",
        "",
        "## 定義",
        "",
        "- 陽線日: その台の日次最終差玉が 0 より大きい日",
        "- イベント: 種別が「当り」または「大当り」の開始時刻",
        "- 間隔: 同一台・同一日の連続イベント開始時刻差",
        f"- 周期候補: {', '.join(str(p) for p in args.periods)} 分",
        f"- hit判定: 周期 ±{args.tolerance} 分以内",
        f"- 対象台: {target}",
        f"- 最小間隔数: {args.min_intervals}",
        "",
        "## 期間分割",
        "",
        f"- 全期間: {all_dates[0]} 〜 {all_dates[-1]} ({len(all_dates)}日)",
        f"- 学習期間: {min(train_dates)} 〜 {max(train_dates)} ({len(train_dates)}日)",
        f"- 検証期間: {min(valid_dates)} 〜 {max(valid_dates)} ({len(valid_dates)}日)",
        "",
        "## 学習上位と検証結果",
        "",
        markdown_table(combined, args.top, include_validation=True),
        "",
        "## 再現候補",
        "",
        f"条件: 学習lift >= {args.min_train_lift}, 検証lift >= {args.min_valid_lift}, 検証hit数 >= {args.min_valid_hits}",
        "",
        markdown_table(replicated, args.top, include_validation=True) if replicated else "該当なし",
        "",
        "## 読み方",
        "",
        "- 学習liftが高くても、検証liftと検証件数が伴わないものは候補扱いしない。",
        "- hit/件数が少ない台は周期っぽく見えても偶然の可能性が高い。",
        "- この分析は当否予測ではなく、陽線日に増えた間隔帯の偏りを見るもの。",
        "",
        "## 検証期間のみの上位",
        "",
        markdown_table(sorted(valid_index.values(), key=lambda r: (r["z"], r["lift"], r["hits"]), reverse=True), args.top),
        "",
    ]
    return "\n".join(lines)


def parse_args():
    parser = argparse.ArgumentParser(description="台別の当たり間隔周期と陽線日の関係を検証")
    parser.add_argument("--machines", default=None, help="対象台。例: 39-77 または 39,40,41")
    parser.add_argument("--periods", default=",".join(str(p) for p in DEFAULT_PERIODS), help="周期候補(分), カンマ区切り")
    parser.add_argument("--tolerance", type=int, default=5, help="周期hitの許容幅(分)")
    parser.add_argument("--min-intervals", type=int, default=20, help="候補に残す最小間隔数")
    parser.add_argument("--split-date", default=None, help="学習期間の最終日 YYYYMMDD。未指定なら日数で前後半分割")
    parser.add_argument("--top", type=int, default=25, help="表示件数")
    parser.add_argument("--min-train-lift", type=float, default=1.3, help="再現候補に必要な学習lift")
    parser.add_argument("--min-valid-lift", type=float, default=1.2, help="再現候補に必要な検証lift")
    parser.add_argument("--min-valid-hits", type=int, default=20, help="再現候補に必要な検証hit数")
    parser.add_argument("--out", default=str(REPORT_DIR / "machine_cycle_positive_report.md"), help="レポート出力先")
    return parser.parse_args()


def main():
    args = parse_args()
    args.periods = tuple(int(p.strip()) for p in args.periods.split(",") if p.strip())
    machine_filter = parse_machine_spec(args.machines)
    days = load_machine_days(machine_filter)
    if not days:
        raise SystemExit("対象データがありません。")

    train_dates, valid_dates = split_dates(days, args.split_date)
    if not train_dates or not valid_dates:
        raise SystemExit("学習期間と検証期間を分けられません。データ日数を確認してください。")

    train_stats, train_meta, train_day_counts = collect_stats(days, train_dates, args.periods, args.tolerance)
    valid_stats, valid_meta, valid_day_counts = collect_stats(days, valid_dates, args.periods, args.tolerance)
    train_rows = build_rows(train_stats, train_meta, train_day_counts, args.min_intervals)
    valid_rows = build_rows(valid_stats, valid_meta, valid_day_counts, args.min_intervals)
    valid_index = index_rows(valid_rows)

    report = make_report(args, days, train_dates, valid_dates, train_rows, valid_index)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")

    print(out)
    print(f"days={len({item['date'] for item in days.values()})} machine_days={len(days)}")
    print(f"train={min(train_dates)}..{max(train_dates)} valid={min(valid_dates)}..{max(valid_dates)}")
    print("top candidates:")
    for i, row in enumerate(train_rows[: min(args.top, 10)], 1):
        valid = valid_index.get((row["machine"], row["period"]))
        valid_text = f" valid_lift={valid['lift']:.2f} valid={valid['hits']}/{valid['total']}" if valid else " valid=-"
        print(
            f"{i:>2}. machine={row['machine']} G{row['group']} period={row['period']} "
            f"train_lift={row['lift']:.2f} train={row['hits']}/{row['total']}{valid_text}"
        )


if __name__ == "__main__":
    main()
