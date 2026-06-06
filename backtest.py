"""
バックテスト
学習期間(前40日)で特定したペアが、検証期間(後20日)で実際に機能するか検証
"""
import csv, os, glob, datetime, json
from collections import defaultdict

CSV_DIR = 'csv/analyze'
WINDOW_MIN = 30
LEARN_DAYS = 40   # 学習期間
TARGET = set(str(i).zfill(3) for i in range(39, 78))

# =============================================
# 全CSVを読み込んで当たりイベントを収集
# =============================================
def load_events(csv_dir):
    events_by_date = defaultdict(list)  # date -> [(time, machine, group)]
    csv_files = sorted(glob.glob(os.path.join(csv_dir, '*', '*_analyze.csv')))
    for csv_path in csv_files:
        date_str = os.path.basename(os.path.dirname(csv_path))
        with open(csv_path, encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                kind = row.get('種別', '')
                if '稼働なし' in kind or kind.strip() == '':
                    continue
                group   = row.get('Group', row.get('group', '')).strip()
                machine = row.get('Machine', row.get('machine', '')).strip()
                end_time = row.get('終了時刻', '').strip()
                if not group or not machine or not end_time:
                    continue
                try:
                    dt = datetime.datetime.strptime(f"{date_str} {end_time}", "%Y%m%d %H:%M")
                    events_by_date[date_str].append((dt, machine.zfill(3), group))
                except:
                    pass
    return events_by_date

print("CSVを読み込み中...")
events_by_date = load_events(CSV_DIR)
all_dates = sorted(events_by_date.keys())
print(f"総日数: {len(all_dates)}日 ({all_dates[0]} 〜 {all_dates[-1]})")

# 学習 / 検証 期間を分割
learn_dates = all_dates[:LEARN_DAYS]
test_dates  = all_dates[LEARN_DAYS:]
print(f"学習期間: {learn_dates[0]} 〜 {learn_dates[-1]} ({len(learn_dates)}日)")
print(f"検証期間: {test_dates[0]}  〜 {test_dates[-1]}  ({len(test_dates)}日)")

# =============================================
# 学習期間: pair_history.jsonから候補ペアを抽出
# =============================================
with open('pair_history.json', encoding='utf-8') as f:
    history = json.load(f)

# 学習期間のデータだけ使って再集計
learn_pair_stats = defaultdict(lambda: {
    'count': 0, 'days': 0, 'lift_sum': 0, 'lift_over': 0, 'group': '', 'A': '', 'B': ''
})

for key, p in history['pairs'].items():
    learn_days_data = [d for d in p['daily'] if d['date'] in set(learn_dates)]
    if not learn_days_data:
        continue
    total_count = sum(d['count'] for d in learn_days_data)
    days_seen   = len(learn_days_data)
    mean_lift   = sum(d['lift'] * d['count'] for d in learn_days_data) / total_count if total_count else 0
    lift_over   = sum(1 for d in learn_days_data if d['lift'] >= 1.5)
    repro       = lift_over / days_seen if days_seen else 0

    learn_pair_stats[key] = {
        'count': total_count, 'days': days_seen,
        'mean_lift': mean_lift, 'reproducibility': repro,
        'group': p['group'], 'A': p['A'].zfill(3), 'B': p['B'].zfill(3)
    }

# 39-77番内部ペアで足切り: days>=5 & count>=10 & repro>=0.8
candidates = {
    k: v for k, v in learn_pair_stats.items()
    if v['A'] in TARGET and v['B'] in TARGET
    and v['days'] >= 5 and v['count'] >= 10 and v['reproducibility'] >= 0.8
}

# 安定性スコアで上位30ペアを選択
for k, v in candidates.items():
    v['stability'] = v['days'] * v['reproducibility'] * v['mean_lift']
top_pairs = sorted(candidates.items(), key=lambda x: x[1]['stability'], reverse=True)[:30]

print(f"\n学習期間で選定した候補ペア: {len(top_pairs)}ペア")
print(f"  条件: 39-77番内 / days>=5 / count>=10 / 再現性>=0.8")

# =============================================
# ベースライン計算: 検証期間の各台の「30分以内に当たる確率」
# =============================================
# 全スロット数(10:00〜22:30, 10分刻み) = 76
# 1日の稼働時間 = 12.5h = 750分
# 30分ウィンドウ = 30/750 = 0.04 (4%)
# より正確に: 検証期間にBが1日平均何回当たるかから計算

machine_hit_days = defaultdict(lambda: defaultdict(int))  # machine -> date -> count
for date in test_dates:
    for dt, machine, group in events_by_date[date]:
        machine_hit_days[machine][date] += 1

def baseline_prob(machine, test_dates, window_min=30, day_min=600):
    """検証期間でのBの平均発火率(30分あたり)"""
    total_hits = sum(machine_hit_days[machine][d] for d in test_dates)
    avg_per_day = total_hits / len(test_dates) if test_dates else 0
    # 1日=600分として30分窓の期待ヒット数
    return avg_per_day * window_min / day_min

# =============================================
# 検証: 各候補ペアのA発火後にBが30分以内に当たるか
# =============================================
print("\n" + "=" * 70)
print("【バックテスト結果】学習40日 → 検証20日")
print("=" * 70)

results = []
for key, p in top_pairs:
    A, B = p['A'], p['B']
    g = p['group']

    chances  = 0  # Aが当たった回数
    successes = 0  # その後30分以内にBが当たった回数

    for date in test_dates:
        day_events = sorted(events_by_date[date], key=lambda x: x[0])
        a_times = [dt for dt, mac, grp in day_events if mac == A]
        b_times = [dt for dt, mac, grp in day_events if mac == B]

        for a_dt in a_times:
            chances += 1
            # 30分以内にBが当たったか（A自身の直後のみ）
            hit = any(
                0 < (b_dt - a_dt).total_seconds() / 60 <= WINDOW_MIN
                for b_dt in b_times
            )
            if hit:
                successes += 1

    success_rate = successes / chances * 100 if chances > 0 else 0
    base = baseline_prob(B, test_dates)
    base_pct = base * 100
    actual_lift = (successes / chances) / base if chances > 0 and base > 0 else 0

    if actual_lift >= 2.0:
        judge = "◎ 有効"
    elif actual_lift >= 1.5:
        judge = "○ やや有効"
    elif actual_lift >= 1.0:
        judge = "△ 微弱"
    else:
        judge = "✗ 無効"

    results.append({
        'pair': f"{A}→{B}", 'group': g,
        'learn_lift': p['mean_lift'], 'learn_repro': p['reproducibility'],
        'learn_stability': p['stability'],
        'chances': chances, 'successes': successes,
        'success_rate': success_rate, 'baseline': base_pct,
        'actual_lift': actual_lift, 'judge': judge
    })

# 実戦liftで並べ替え
results.sort(key=lambda x: x['actual_lift'], reverse=True)

print(f"\n{'ペア':>8}  G  {'学習lift':>8}  {'学習再現':>8}  {'チャンス':>8}  {'成功':>5}  {'成功率':>7}  {'ベース':>7}  {'実戦lift':>8}  {'判定':>10}")
print("-" * 95)
for r in results:
    if r['actual_lift'] >= 2.0:
        judge = "◎ 有効"
    elif r['actual_lift'] >= 1.5:
        judge = "○ やや有効"
    elif r['actual_lift'] >= 1.0:
        judge = "△ 微弱"
    else:
        judge = "✗ 無効"
    print(f"  {r['pair']:>8}  G{r['group']}  {r['learn_lift']:>8.2f}  {r['learn_repro']:>8.3f}  "
          f"{r['chances']:>8}  {r['successes']:>5}  {r['success_rate']:>6.1f}%  "
          f"{r['baseline']:>6.1f}%  {r['actual_lift']:>8.2f}x  {r['judge']}")

# =============================================
# サマリー
# =============================================
valid   = [r for r in results if r['actual_lift'] >= 1.5]
invalid = [r for r in results if r['actual_lift'] < 1.0]

print("\n" + "=" * 70)
print("【サマリー】")
print("=" * 70)
print(f"  検証ペア数: {len(results)}")
print(f"  ◎○ 有効(lift>=1.5): {len(valid)}ペア ({len(valid)/len(results)*100:.0f}%)")
print(f"  ✗  無効(lift<1.0):  {len(invalid)}ペア ({len(invalid)/len(results)*100:.0f}%)")

total_chances = sum(r['chances'] for r in results)
total_success = sum(r['successes'] for r in results)
print(f"\n  全ペア合計チャンス数: {total_chances}回")
print(f"  全ペア合計成功数:     {total_success}回")
print(f"  全体成功率:           {total_success/total_chances*100:.1f}%" if total_chances else "")

print(f"\n  有効ペア TOP5:")
for r in results[:5]:
    print(f"    {r['pair']} G{r['group']}  チャンス{r['chances']}回 → 成功{r['successes']}回"
          f"  ({r['success_rate']:.1f}%)  実戦lift={r['actual_lift']:.2f}x")
