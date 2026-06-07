"""
regime_analysis.py
==================
仮説検証: 「ある期間は特定グループが強く引っ張り合い、期間が変わると主役グループが推移する」

検証の作法:
  1. 期間を連続ブロックに分割 (--block-days)
  2. 各ブロック×各グループで「グループ内の点火の時間クラスタリング量 M」を測る
        M = Σ_{d=0..W} Σ_s cnt[s]·cnt[(s+d)%76]
        cnt[s] = そのグループでstep s に点火した台数
     → 同じグループの台が時間的に固まって点火する(=引っ張り合う)ほど M は大きい
  3. ヌルモデル: 各台の点火時刻を「円環シフト(ランダムδだけ回す)」して台間の同期を壊す。
     台ごとの点火回数・自己内ラグ構造は保持されるので、純粋に「台をまたいだ同期」だけ消える。
     → シャッフルK回で M の帰無分布を作り、z = (M_obs - mean) / std
  4. z値ヒートマップで主役グループの推移を可視化
  5. ★持続性検定: ブロックNのzとブロックN+1のzに正の自己相関があるか?
        - 相関>0 かつ 並べ替え検定で有意 → レジームは実在し「予測可能」
        - 相関≒0 → 主役交代はランダム = ノイズと区別できない(仮説棄却)

使い方:
  python regime_analysis.py --block-days 14 --window 3 --shuffles 300
"""

import sys, random, argparse
from collections import defaultdict
import propagation as prop

N_STEPS = 76
GROUPS = [str(i) for i in range(1, 10)]
MIN_FIRES = 30   # (ブロック×グループ)でこれ未満の点火数なら z 算出をスキップ


# ---------------------------------------------------------------
# 各日・各グループの「台ごとの点火step列」を作る
# ---------------------------------------------------------------
def build_day_group_fires(snaps):
    out = {}  # date -> group -> [ [steps of machine1], [steps of machine2], ... ]
    for date, snap in snaps.items():
        ev = prop.extract_starts(snap)
        gm = defaultdict(lambda: defaultdict(list))
        for e in ev:
            gm[e["group"]][e["machine"]].append(e["step"])
        out[date] = {g: list(mh.values()) for g, mh in gm.items()}
    return out


def cnt_from_fires(fire_lists, shift=False):
    cnt = [0] * N_STEPS
    if shift:
        for fl in fire_lists:
            d = random.randrange(N_STEPS)
            for s in fl:
                cnt[(s + d) % N_STEPS] += 1
    else:
        for fl in fire_lists:
            for s in fl:
                cnt[s] += 1
    return cnt


def metric(cnt, W):
    M = 0
    for d in range(0, W + 1):
        acc = 0
        for s in range(N_STEPS):
            acc += cnt[s] * cnt[(s + d) % N_STEPS]
        M += acc
    return M


def block_z(dgf, block_dates, group, W, K):
    """ブロック内のあるグループの z 値とブロック観測Mを返す"""
    fires_per_day = [dgf[d].get(group, []) for d in block_dates]
    total_fires = sum(len(fl) for day in fires_per_day for fl in day)
    if total_fires < MIN_FIRES:
        return None, total_fires

    # 観測 M (ブロック合計)
    M_obs = sum(metric(cnt_from_fires(day, shift=False), W) for day in fires_per_day)

    # ヌル分布
    nulls = []
    for _ in range(K):
        M_sh = sum(metric(cnt_from_fires(day, shift=True), W) for day in fires_per_day)
        nulls.append(M_sh)
    mean = sum(nulls) / K
    var = sum((x - mean) ** 2 for x in nulls) / K
    std = var ** 0.5
    if std == 0:
        return None, total_fires
    z = (M_obs - mean) / std
    return z, total_fires


# ---------------------------------------------------------------
# 統計ユーティリティ
# ---------------------------------------------------------------
def pearson(xs, ys):
    n = len(xs)
    if n < 2:
        return 0.0
    mx = sum(xs) / n; my = sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx == 0 or syy == 0:
        return 0.0
    return sxy / (sxx * syy) ** 0.5


def lag1_pairs(zmat):
    """各グループの連続ブロック間 (z_t, z_{t+1}) ペアを全プール"""
    xs, ys = [], []
    for g in GROUPS:
        seq = zmat[g]
        for t in range(len(seq) - 1):
            a, b = seq[t], seq[t + 1]
            if a is not None and b is not None:
                xs.append(a); ys.append(b)
    return xs, ys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--block-days", type=int, default=14)
    ap.add_argument("--window", type=int, default=3, help="伝播窓 (step, 1step=10分)")
    ap.add_argument("--shuffles", type=int, default=300)
    ap.add_argument("--perm", type=int, default=2000, help="持続性検定の並べ替え回数")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    random.seed(args.seed)

    snaps = prop.load_snaps()
    if not snaps:
        print("⚠ スナップショットがありません"); sys.exit(1)
    dates = sorted(snaps.keys())
    dgf = build_day_group_fires(snaps)

    # ブロック分割
    blocks = [dates[i:i + args.block_days] for i in range(0, len(dates), args.block_days)]
    # 末尾が極端に短いブロック(半分未満)は前ブロックに吸収
    if len(blocks) >= 2 and len(blocks[-1]) < args.block_days / 2:
        blocks[-2] += blocks[-1]
        blocks.pop()

    win_min = args.window * 10
    print("=" * 88)
    print(f"🔁 レジーム検出  (ブロック={args.block_days}日 / 窓={win_min}分 / シャッフル={args.shuffles}回)")
    print(f"   全{len(dates)}日 → {len(blocks)}ブロック")
    print("=" * 88)

    # z 行列計算
    zmat = {g: [] for g in GROUPS}      # group -> [z per block]
    fmat = {g: [] for g in GROUPS}
    for bi, bdates in enumerate(blocks):
        for g in GROUPS:
            z, f = block_z(dgf, bdates, g, args.window, args.shuffles)
            zmat[g].append(z); fmat[g].append(f)

    # ヒートマップ表示
    print(f"\n■ グループ内クラスタリング z値 (各ブロック)")
    print(f"  z>=2 は「偶然では起きにくい引っ張り合い」=★、最大グループに◎")
    hdr = "  ブロック        期間             " + "".join(f"  G{g:>1}  " for g in GROUPS)
    print(hdr)
    print("  " + "-" * (len(hdr)))
    for bi, bdates in enumerate(blocks):
        span = f"{bdates[0][4:]}-{bdates[-1][4:]}"
        # その行の最大z
        zr = [zmat[g][bi] for g in GROUPS]
        valid = [z for z in zr if z is not None]
        zmax = max(valid) if valid else None
        cells = []
        for g in GROUPS:
            z = zmat[g][bi]
            if z is None:
                cells.append("  --  ")
            else:
                mark = "◎" if (zmax is not None and z == zmax and z >= 2) else ("★" if z >= 2 else " ")
                cells.append(f"{z:>5.1f}{mark}")
        print(f"  B{bi+1:<2} {span:>17}  " + "".join(cells))

    # 各ブロックの主役グループ
    print(f"\n■ 各ブロックの主役グループ (最大z)")
    top_seq = []
    for bi, bdates in enumerate(blocks):
        zr = [(g, zmat[g][bi]) for g in GROUPS if zmat[g][bi] is not None]
        if not zr:
            top_seq.append(None); continue
        g_top, z_top = max(zr, key=lambda x: x[1])
        top_seq.append(g_top)
        span = f"{bdates[0][4:]}-{bdates[-1][4:]}"
        flag = "  ← z>=2 (有意)" if z_top >= 2 else "  (z<2: 弱い)"
        print(f"  B{bi+1:<2} {span:>17}  主役 G{g_top}  (z={z_top:.1f}){flag}")

    # ---- 持続性検定 ----
    print("\n" + "=" * 88)
    print("★ 持続性検定: ブロック間で同じグループの強さが続くか (z_t vs z_{t+1})")
    print("=" * 88)
    xs, ys = lag1_pairs(zmat)
    if len(xs) < 3:
        print("  ブロック数が不足。--block-days を小さくして再実行してください。")
        return
    r_obs = pearson(xs, ys)

    # 並べ替えヌル: 各グループのブロック列を独立にシャッフル
    cnt_ge = 0
    for _ in range(args.perm):
        zsh = {}
        for g in GROUPS:
            seq = zmat[g][:]
            # None を保持したままシャッフル
            idx = list(range(len(seq)))
            random.shuffle(idx)
            zsh[g] = [seq[i] for i in idx]
        xs2, ys2 = lag1_pairs(zsh)
        if len(xs2) >= 2 and pearson(xs2, ys2) >= r_obs:
            cnt_ge += 1
    p_val = (cnt_ge + 1) / (args.perm + 1)

    print(f"  ラグ1自己相関 r = {r_obs:+.3f}   (n={len(xs)}ペア)")
    print(f"  並べ替え検定 p = {p_val:.3f}")
    print()
    if r_obs > 0 and p_val < 0.05:
        print("  → 正の持続性が有意。主役グループは数ブロック続く傾向あり。")
        print("     『期間ごとに強いグループが推移』仮説を支持し、かつ予測可能性の余地あり。")
    elif r_obs > 0:
        print("  → 弱い正の相関だが有意ではない。持続性は確認できず。")
        print("     主役交代はランダムと区別できない(=後出しでしか分からない)。")
    else:
        print("  → 持続性なし(相関≦0)。主役グループの推移はノイズと区別できない。")
        print("     仮説は支持されない: 強いグループが翌期も強い保証はない。")

    # トップグループの遷移持続(おまけ)
    trans_same = sum(1 for t in range(len(top_seq) - 1)
                     if top_seq[t] is not None and top_seq[t] == top_seq[t + 1])
    trans_tot = sum(1 for t in range(len(top_seq) - 1)
                    if top_seq[t] is not None and top_seq[t + 1] is not None)
    if trans_tot:
        print(f"\n  参考: 主役グループが翌ブロックも同じ = {trans_same}/{trans_tot}回 "
              f"(ランダム期待 ≈ {trans_tot/9:.1f}回)")


if __name__ == "__main__":
    main()
