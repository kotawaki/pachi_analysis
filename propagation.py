"""
propagation.py
===============
伝播ペア抽出エンジン

「台Aが点火した後、N分以内に台Bが点火する」傾向を統計的に測る。

核心指標: リフト値 (lift)
  lift = P(window内にB点火 | 直前にA点火) / P(Bがランダムwindowで点火)
  - lift > 1 : AはBを引っ張る (正の相関)
  - lift = 1 : 無関係
  - lift < 1 : 抑制

罠の回避:
  - Bが元々当たりやすいだけ、を除外するためにベースライン比で割る
  - 観測回数が少ないペアは足切り (min_count)
  - 同一台ペア(A=B)は除外
  - 同時点火(同一step)はラグ0として別集計

出力:
  - グループ内ペアのリフトランキング
  - グループ「番台レンジ」単位の伝播 (例: 39-50番→51-60番)
  - 9グループ間の伝播も測定 (Gをまたぐ引っ張り合いがあるか)
"""

import json
import sys
from pathlib import Path
from collections import defaultdict
from itertools import product

ROOT = Path(__file__).parent
SNAP_DIR = ROOT / "csv" / "replay"

ATARI = ("当り", "大当り")

def load_snaps(dates=None):
    snaps = {}
    for jp in sorted(SNAP_DIR.glob("*_snapshot.json")):
        date = jp.stem.replace("_snapshot", "")
        if dates and date not in dates:
            continue
        with open(jp, encoding="utf-8") as f:
            snaps[date] = json.load(f)
    return snaps

def extract_starts(snap):
    """当たり開始イベント [(machine, group, island, step)]"""
    events = []
    for m in snap["machines"]:
        if not m["active"]:
            continue
        for i, k in enumerate(m["kind"]):
            is_a = k in ATARI
            prev_a = (i > 0) and m["kind"][i-1] in ATARI
            if is_a and not prev_a:
                events.append({
                    "machine": m["machine"], "group": m["group"],
                    "island": m["island"], "step": i
                })
    return events

def active_machines_by_group(snap):
    g = defaultdict(list)
    for m in snap["machines"]:
        if m["active"]:
            g[m["group"]].append(m["machine"])
    return g


class PairCounter:
    """
    台ペア (A,B) の伝播をカウント。
    - co[(A,B)]   : Aの点火後 window_steps以内 に Bが点火した回数
    - a_fire[A]   : Aの総点火回数
    - b_fire[B]   : Bの総点火回数
    - total_windows : 全(イベント×他台)機会の分母計算用
    """
    def __init__(self, window_steps=3, same_group_only=True):
        self.window = window_steps
        self.same_group_only = same_group_only
        self.co = defaultdict(int)
        self.a_fire = defaultdict(int)
        self.fire_count = defaultdict(int)   # 台ごとの総点火
        self.machine_group = {}
        self.machine_island = {}
        self.n_steps = 0
        self.day_count = 0

    def add_day(self, snap):
        self.day_count += 1
        self.n_steps = len(snap["steps"])
        events = extract_starts(snap)
        for e in events:
            self.machine_group[e["machine"]] = e["group"]
            self.machine_island[e["machine"]] = e["island"]
            self.fire_count[e["machine"]] += 1

        # 台ごとの点火step一覧
        fires_by_machine = defaultdict(list)
        for e in events:
            fires_by_machine[e["machine"]].append(e["step"])

        # A点火 → window内のB点火 をカウント
        for ea in events:
            A = ea["machine"]
            self.a_fire[A] += 1
            for eb in events:
                B = eb["machine"]
                if A == B:
                    continue
                if self.same_group_only and ea["group"] != eb["group"]:
                    continue
                lag = eb["step"] - ea["step"]
                if 0 <= lag <= self.window:
                    self.co[(A, B)] += 1

    def lift_table(self, min_count=3):
        """
        各ペアのリフト値を計算。
        ベースライン: Bがランダムなwindowで点火する確率
          ≈ (Bの総点火回数 / 全step) * window長
        条件付き確率: P(B点火 | A点火直後window) = co[(A,B)] / a_fire[A]
        lift = 条件付き / ベースライン
        """
        total_obs_steps = self.n_steps * self.day_count
        rows = []
        for (A, B), c in self.co.items():
            if c < min_count:
                continue
            if self.a_fire[A] == 0:
                continue
            p_cond = c / self.a_fire[A]
            # ベースライン: Bが任意のwindow(=window+1 step)で点火する確率
            b_rate_per_step = self.fire_count[B] / total_obs_steps
            p_base = min(1.0, b_rate_per_step * (self.window + 1))
            if p_base <= 0:
                continue
            lift = p_cond / p_base
            rows.append({
                "A": A, "B": B,
                "gA": self.machine_group.get(A,"?"),
                "gB": self.machine_group.get(B,"?"),
                "islandA": self.machine_island.get(A,"?"),
                "islandB": self.machine_island.get(B,"?"),
                "count": c,
                "a_fire": self.a_fire[A],
                "p_cond": round(p_cond, 3),
                "p_base": round(p_base, 3),
                "lift": round(lift, 2),
            })
        rows.sort(key=lambda r: r["lift"], reverse=True)
        return rows


def analyze_group_internal(snaps, window_steps=3, min_count=3):
    """グループ内の台ペア伝播"""
    pc = PairCounter(window_steps=window_steps, same_group_only=True)
    for date, snap in snaps.items():
        pc.add_day(snap)
    return pc.lift_table(min_count=min_count)


def analyze_cross_group(snaps, window_steps=3, min_count=3):
    """
    グループ間の伝播 (G単位).
    正しい測り方: 「Aグループのある台が点火した直後window内に、
    Bグループの台が"少なくとも1台"点火したか」をイベント単位で測る。
    分母 = Aグループの点火イベント総数
    分子 = そのうちwindow内にBグループ点火が1回以上あったイベント数
    ベースライン = Bグループがランダムwindowで点火する確率
    """
    co = defaultdict(int)       # (gA,gB) -> Aイベントのうちwindow内Bあり数
    a_events = defaultdict(int) # gA -> Aの点火イベント総数
    # ベースライン用: 各stepでGが点火していたか
    g_active_steps = defaultdict(set)  # g -> {(date,step)} 点火があったstep
    n_steps = 0; days = 0

    for date, snap in snaps.items():
        days += 1
        n_steps = len(snap["steps"])
        events = extract_starts(snap)
        # step別・group別の点火有無
        fire_at = defaultdict(set)   # (group) -> set(step)
        for e in events:
            fire_at[e["group"]].add(e["step"])
            g_active_steps[e["group"]].add((date, e["step"]))
        # 各Aイベントについて、window内に各Gが点火したか
        for ea in events:
            gA = ea["group"]
            a_events[gA] += 1
            for gB in fire_at.keys():
                # window内 (lag 0..window) に gB の点火があるか
                hit = any((ea["step"]+lag) in fire_at[gB] and not (gB==gA and lag==0 and len(fire_at[gB])==1)
                          for lag in range(0, window_steps+1))
                # gB点火が「自分自身のイベントだけ」でないことを軽くチェック
                if hit:
                    co[(gA, gB)] += 1

    total_steps = n_steps * days
    rows = []
    for (gA, gB), c in co.items():
        if a_events[gA] == 0: continue
        p_cond = c / a_events[gA]
        # ベースライン: gBがランダムなwindowで「1回以上点火」する確率
        b_active = len(g_active_steps[gB])
        b_rate = b_active / total_steps  # 1stepあたりgB点火がある確率
        # window内に1回以上 ≈ 1-(1-b_rate)^(window+1)
        p_base = 1 - (1 - b_rate)**(window_steps+1)
        if p_base <= 0: continue
        lift = p_cond / p_base
        rows.append({"gA":gA,"gB":gB,"count":c,"a_events":a_events[gA],
                     "lift":round(lift,2),
                     "p_cond":round(p_cond,3),"p_base":round(p_base,3)})
    rows.sort(key=lambda r:r["lift"], reverse=True)
    return rows


def machine_range(m):
    """台番号 → レンジラベル (10台刻み)"""
    n = int(m)
    lo = ((n-1)//10)*10 + 1
    hi = lo + 9
    return f"{lo:03d}-{hi:03d}"


def analyze_range_transition(snaps, window_steps=3, min_count=2):
    """グループ内で 番台レンジA → レンジB の伝播"""
    co = defaultdict(int)
    range_fire = defaultdict(int)
    n_steps = 0; days = 0
    for date, snap in snaps.items():
        days += 1
        n_steps = len(snap["steps"])
        events = extract_starts(snap)
        for e in events:
            e["range"] = machine_range(e["machine"])
            range_fire[(e["group"], e["range"])] += 1
        for ea in events:
            for eb in events:
                if ea is eb: continue
                if ea["group"] != eb["group"]: continue
                lag = eb["step"] - ea["step"]
                if 0 <= lag <= window_steps:
                    co[(ea["group"], ea["range"], eb["range"])] += 1
    total_steps = n_steps * days
    rows = []
    for (g, rA, rB), c in co.items():
        if c < min_count: continue
        base_a = range_fire[(g, rA)]
        if base_a == 0: continue
        p_cond = c / base_a
        b_rate = range_fire[(g, rB)] / total_steps
        p_base = min(1.0, b_rate*(window_steps+1))
        if p_base <= 0: continue
        lift = p_cond/p_base
        rows.append({"group":g,"fromRange":rA,"toRange":rB,
                     "count":c,"lift":round(lift,2)})
    rows.sort(key=lambda r:r["lift"], reverse=True)
    return rows


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", type=int, default=3, help="伝播窓 (step数, 1step=10分)")
    ap.add_argument("--min-count", type=int, default=3, help="ペア足切り回数")
    ap.add_argument("--dates", nargs="*", default=None)
    ap.add_argument("--top", type=int, default=25)
    args = ap.parse_args()

    snaps = load_snaps(args.dates)
    if not snaps:
        print("⚠ スナップショットがありません"); sys.exit(1)

    win_min = args.window * 10
    print("="*78)
    print(f"🔗 伝播ペア分析  (窓={win_min}分以内 / 足切り={args.min_count}回 / {len(snaps)}日分)")
    print(f"   対象日: {', '.join(snaps.keys())}")
    print("="*78)

    # 1. グループ内 台ペア
    print(f"\n■ グループ内 台→台 伝播 リフトTOP{args.top}")
    print(f"  (lift>1.5 かつ count多 が注目ペア)")
    rows = analyze_group_internal(snaps, args.window, args.min_count)
    print(f"  {'A台':>5}→{'B台':<5} {'G':>3} {'島A→島B':>10} {'回数':>4} {'P(B|A)':>7} {'基準':>6} {'lift':>6}")
    for r in rows[:args.top]:
        isl = f"{r['islandA']}→{r['islandB']}"
        print(f"  {r['A']:>5}→{r['B']:<5} G{r['gA']:>2} {isl:>10} {r['count']:>4} {r['p_cond']:>7} {r['p_base']:>6} {r['lift']:>6}")

    # 2. グループ間
    print(f"\n■ グループ間 G→G 伝播 (9グループも引っ張り合うか?)")
    cross = analyze_cross_group(snaps, args.window, args.min_count)
    print(f"  {'GA':>3}→{'GB':<3} {'回数':>5} {'P(B|A)':>7} {'基準':>6} {'lift':>6}")
    for r in cross[:20]:
        same = "★同一G" if r["gA"]==r["gB"] else ""
        print(f"  G{r['gA']:>1}→G{r['gB']:<1} {r['count']:>6} {r['p_cond']:>7} {r['p_base']:>6} {r['lift']:>6} {same}")

    # 3. 番台レンジ伝播
    print(f"\n■ グループ内 番台レンジ伝播 TOP20")
    rng = analyze_range_transition(snaps, args.window, max(2,args.min_count-1))
    print(f"  {'G':>3} {'fromレンジ':>10}→{'toレンジ':<10} {'回数':>4} {'lift':>6}")
    for r in rng[:20]:
        print(f"  G{r['group']:>2} {r['fromRange']:>10}→{r['toRange']:<10} {r['count']:>4} {r['lift']:>6}")

    print("\n" + "="*78)
    print("📌 注意: 2日分のプロトタイプなので件数は少なめ。lift高でもcount<5は要警戒。")
    print("   3ヶ月分そろえば同じスクリプトで信頼度が上がる。")

if __name__ == "__main__":
    main()
