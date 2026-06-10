"""
台番から伝播候補を表示する簡易ルックアップ。

表示する「候補率」は予測確率ではなく、過去データ上の
P(30分以内にB点火 | A点火) です。
"""

import argparse
from statistics import mean

from collections import defaultdict

from propagation import extract_starts, load_snaps


def z3(value):
    return str(value).strip().zfill(3)


def in_range(machine, lo, hi):
    try:
        n = int(machine)
    except ValueError:
        return False
    return lo <= n <= hi


def confidence_label(row):
    count = row["count"]
    lift = row["lift"]
    pct = row["p_cond"] * 100
    if count >= 30 and lift >= 1.5 and pct >= 10:
        return "候補"
    if count >= 10 and lift >= 1.2:
        return "参考"
    return "件数不足"


def lookup_source(snaps, source, window_steps, min_count):
    co = defaultdict(int)
    fire_count = defaultdict(int)
    a_fire = 0
    machine_group = {}
    machine_island = {}
    n_steps = 0
    days = 0

    for snap in snaps.values():
        days += 1
        n_steps = len(snap["steps"])
        events = extract_starts(snap)
        for e in events:
            machine = z3(e["machine"])
            e["machine"] = machine
            machine_group[machine] = e["group"]
            machine_island[machine] = e["island"]
            fire_count[machine] += 1

        source_events = [e for e in events if e["machine"] == source]
        if not source_events:
            continue

        for ea in source_events:
            a_fire += 1
            for eb in events:
                b = eb["machine"]
                if b == source:
                    continue
                if ea["group"] != eb["group"]:
                    continue
                lag = eb["step"] - ea["step"]
                if 0 <= lag <= window_steps:
                    co[b] += 1

    total_obs_steps = n_steps * days
    rows = []
    for b, count in co.items():
        if count < min_count or not a_fire or not total_obs_steps:
            continue
        p_cond = count / a_fire
        p_base = min(1.0, (fire_count[b] / total_obs_steps) * (window_steps + 1))
        if p_base <= 0:
            continue
        rows.append(
            {
                "A": source,
                "B": b,
                "gA": machine_group.get(source, "?"),
                "islandA": machine_island.get(source, "?"),
                "islandB": machine_island.get(b, "?"),
                "count": count,
                "a_fire": a_fire,
                "p_cond": p_cond,
                "p_base": p_base,
                "lift": p_cond / p_base,
            }
        )
    return rows


def main():
    parser = argparse.ArgumentParser(
        description="台番を入力して、過去データ上の30分以内伝播候補を表示します。"
    )
    parser.add_argument("machine", help="現在当たっている台番。例: 39 / 039")
    parser.add_argument("--top", type=int, default=15, help="表示件数")
    parser.add_argument("--window", type=int, default=3, help="10分step数。3=30分")
    parser.add_argument("--min-count", type=int, default=3, help="最低同時発生count")
    parser.add_argument("--target-lo", type=int, default=None, help="候補Bの下限台番")
    parser.add_argument("--target-hi", type=int, default=None, help="候補Bの上限台番")
    parser.add_argument(
        "--dates",
        nargs="*",
        default=None,
        help="対象日をYYYYMMDDで指定。省略時は全snapshot。",
    )
    args = parser.parse_args()

    source = z3(args.machine)
    snaps = load_snaps(args.dates)
    if not snaps:
        print("snapshotがありません。daily_ingest.py を先に実行してください。")
        return

    rows = lookup_source(snaps, source, args.window, args.min_count)
    if args.target_lo is not None and args.target_hi is not None:
        rows = [r for r in rows if in_range(r["B"], args.target_lo, args.target_hi)]

    rows.sort(key=lambda r: (r["p_cond"], r["lift"], r["count"]), reverse=True)

    print("=" * 78)
    print(f"台 {int(source)} が点火した後の伝播候補")
    print(f"期間: {min(snaps.keys())} - {max(snaps.keys())} / {len(snaps)}日")
    print(f"window: {args.window * 10}分 / min_count: {args.min_count}")
    if args.target_lo is not None and args.target_hi is not None:
        print(f"候補B範囲: {args.target_lo}-{args.target_hi}")
    print("-" * 78)
    print("注意: %は未来予測ではなく、過去データの P(B点火|A点火) です。")
    print("=" * 78)

    if not rows:
        print("条件に合う候補がありません。min_countや対象範囲を緩めてください。")
        return

    pcts = [r["p_cond"] * 100 for r in rows]
    print(f"候補数: {len(rows)} / 平均候補率: {mean(pcts):.1f}%")
    print()
    print(f"{'候補B':>6} {'G':>3} {'島':>9} {'候補率':>8} {'base':>8} {'lift':>7} {'count':>6} {'A点火':>6}  判定")
    print("-" * 78)
    for r in rows[: args.top]:
        island = f"{r['islandA']}->{r['islandB']}"
        print(
            f"{int(r['B']):>6} "
            f"G{r['gA']:>2} "
            f"{island:>9} "
            f"{r['p_cond'] * 100:>7.1f}% "
            f"{r['p_base'] * 100:>7.1f}% "
            f"{r['lift']:>6.2f} "
            f"{r['count']:>6} "
            f"{r['a_fire']:>6}  "
            f"{confidence_label(r)}"
        )


if __name__ == "__main__":
    main()
