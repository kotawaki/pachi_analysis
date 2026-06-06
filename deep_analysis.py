"""
39-77番内部ペアの深掘り分析
人が目視では気づけない構造を抽出する
"""
import json
from collections import defaultdict

with open('pair_history.json', encoding='utf-8') as f:
    data = json.load(f)

TARGET = set(str(i).zfill(3) for i in range(39, 78))

# 対象ペアだけ抽出（足切りなし）
pairs = {}
for key, p in data['pairs'].items():
    a = p['A'].zfill(3)
    b = p['B'].zfill(3)
    if a in TARGET and b in TARGET:
        pairs[key] = p

# 足切り済みペア
filtered = {k: v for k, v in pairs.items()
            if v['days_seen'] >= 3 and v['total_count'] >= 8}

# =============================================
# 1. ハブ分析: 出現頻度が多い台（送り手・受け手）
# =============================================
print("=" * 60)
print("【1】ハブ台分析（足切り通過ペア内）")
print("  「誰が当たると他台が連鎖しやすいか」")
print("=" * 60)

out_count = defaultdict(int)   # Aとして何ペアに登場
in_count  = defaultdict(int)   # Bとして何ペアに登場
out_lift  = defaultdict(list)
in_lift   = defaultdict(list)

for p in filtered.values():
    a, b = p['A'].zfill(3), p['B'].zfill(3)
    out_count[a] += 1
    in_count[b]  += 1
    out_lift[a].append(p['mean_lift'])
    in_lift[b].append(p['mean_lift'])

# 送り手ランキング
print("\n--- 送り手ランキング（A側: この台が当たると複数台が連動）---")
senders = sorted(out_count.items(), key=lambda x: x[1], reverse=True)[:10]
print(f"  {'台番':>5}  {'送りペア数':>6}  {'平均lift':>8}")
for mac, cnt in senders:
    avg = sum(out_lift[mac]) / len(out_lift[mac])
    print(f"  {mac}    {cnt:>6}        {avg:>6.2f}")

# 受け手ランキング
print("\n--- 受け手ランキング（B側: よく「引っ張られる」台）---")
receivers = sorted(in_count.items(), key=lambda x: x[1], reverse=True)[:10]
print(f"  {'台番':>5}  {'受けペア数':>6}  {'平均lift':>8}")
for mac, cnt in receivers:
    avg = sum(in_lift[mac]) / len(in_lift[mac])
    print(f"  {mac}    {cnt:>6}        {avg:>6.2f}")

# =============================================
# 2. 双方向ペア: A→B かつ B→A が両方成立
# =============================================
print("\n" + "=" * 60)
print("【2】双方向ペア分析")
print("  「互いに引っ張り合っている台ペア」")
print("=" * 60)

pair_set = {}
for p in filtered.values():
    a, b = p['A'].zfill(3), p['B'].zfill(3)
    pair_set[(a, b)] = p

bidirectional = []
seen = set()
for (a, b), p_ab in pair_set.items():
    if (b, a) in pair_set and (b, a) not in seen:
        p_ba = pair_set[(b, a)]
        seen.add((a, b))
        bidirectional.append({
            'pair': f"{a}<->{b}",
            'group': p_ab['group'],
            'lift_ab': p_ab['mean_lift'],
            'lift_ba': p_ba['mean_lift'],
            'lift_avg': (p_ab['mean_lift'] + p_ba['mean_lift']) / 2,
            'days_ab': p_ab['days_seen'],
            'days_ba': p_ba['days_seen'],
        })

bidirectional.sort(key=lambda x: x['lift_avg'], reverse=True)
print(f"\n  {'ペア':>10}  G   {'A→B lift':>8}  {'B→A lift':>8}  {'平均':>6}  {'非対称比':>6}")
for b in bidirectional:
    asym = max(b['lift_ab'], b['lift_ba']) / min(b['lift_ab'], b['lift_ba'])
    print(f"  {b['pair']:>10}  G{b['group']}  {b['lift_ab']:>8.2f}  {b['lift_ba']:>8.2f}  {b['lift_avg']:>6.2f}  {asym:>6.2f}x")

# =============================================
# 3. 連鎖（A→B→C）パターン
# =============================================
print("\n" + "=" * 60)
print("【3】3台連鎖パターン（A→B→C）")
print("  「Aが当たると→Bが当たり→さらにCが当たりやすい」")
print("=" * 60)

chains = []
for (a, b), p_ab in pair_set.items():
    for (b2, c), p_bc in pair_set.items():
        if b == b2 and a != c:
            chains.append({
                'chain': f"{a}->{b}->{c}",
                'group_ab': p_ab['group'],
                'group_bc': p_bc['group'],
                'lift_ab': p_ab['mean_lift'],
                'lift_bc': p_bc['mean_lift'],
                'score': p_ab['mean_lift'] * p_bc['mean_lift'],
                'same_group': p_ab['group'] == p_bc['group'],
            })

chains.sort(key=lambda x: x['score'], reverse=True)
print(f"\n  {'連鎖':>16}  {'G':>3}  {'A→B':>6}  {'B→C':>6}  {'スコア':>7}")
for c in chains[:15]:
    g = f"G{c['group_ab']}" if c['same_group'] else f"G{c['group_ab']}->G{c['group_bc']}"
    print(f"  {c['chain']:>16}  {g:>5}  {c['lift_ab']:>6.2f}  {c['lift_bc']:>6.2f}  {c['score']:>7.2f}")

# =============================================
# 4. 方向性の非対称ペア（一方通行が強い）
# =============================================
print("\n" + "=" * 60)
print("【4】強い一方通行ペア")
print("  「A→Bは強いが、B→Aはほぼ無反応」")
print("=" * 60)

one_way = []
for (a, b), p_ab in pair_set.items():
    if (b, a) not in pair_set:  # 逆方向が足切り未通過
        one_way.append(p_ab)

one_way.sort(key=lambda x: x['mean_lift'], reverse=True)
print(f"\n  {'ペア':>10}  G   {'日数':>4}  {'count':>5}  {'lift':>6}  {'再現性':>6}")
for p in one_way[:10]:
    print(f"  {p['A']}->{p['B']:<4}  G{p['group']}  {p['days_seen']:>4}  {p['total_count']:>5}  {p['mean_lift']:>6.2f}  {p['reproducibility']:>6.3f}")

# =============================================
# 5. 再現性が高く安定している上位ペア
# =============================================
print("\n" + "=" * 60)
print("【5】安定性スコア上位（日数×再現性×lift）")
print("  「蓄積・再現性・強度が全部揃っているペア」")
print("=" * 60)

for p in filtered.values():
    p['stability'] = p['days_seen'] * p['reproducibility'] * p['mean_lift']

stable = sorted(filtered.values(), key=lambda x: x['stability'], reverse=True)[:15]
print(f"\n  {'ペア':>10}  G   {'日数':>4}  {'count':>5}  {'lift':>6}  {'再現性':>6}  {'安定スコア':>8}")
for p in stable:
    print(f"  {p['A']}->{p['B']:<4}  G{p['group']}  {p['days_seen']:>4}  {p['total_count']:>5}  {p['mean_lift']:>6.2f}  {p['reproducibility']:>6.3f}  {p['stability']:>8.2f}")
