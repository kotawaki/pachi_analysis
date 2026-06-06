"""
カスケード分析
「グループ内で連続発火が起きるとき、何台続くか・どう展開するか」
"""
import csv, os, glob, datetime
from collections import defaultdict

CSV_DIR = 'csv/analyze'
WINDOW_MIN = 30  # 伝播ウィンドウ(分)

def parse_time(date_str, time_str):
    """YYYYMMDD + HH:MM → datetime"""
    try:
        return datetime.datetime.strptime(f"{date_str} {time_str}", "%Y%m%d %H:%M")
    except:
        return None

def is_hit(kind):
    return '稼働なし' not in kind and kind.strip() != ''

# 全CSVから当たりイベントを収集
all_events = []  # (datetime, machine, group, kind)

csv_files = sorted(glob.glob(os.path.join(CSV_DIR, '*', '*_analyze.csv')))
dates_processed = []

for csv_path in csv_files:
    date_str = os.path.basename(os.path.dirname(csv_path))
    dates_processed.append(date_str)

    with open(csv_path, encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            kind = row.get('種別', '')
            if not is_hit(kind):
                continue
            group = row.get('Group', row.get('group', '')).strip()
            machine = row.get('Machine', row.get('machine', '')).strip()
            end_time = row.get('終了時刻', '').strip()
            if not group or not machine or not end_time:
                continue
            dt = parse_time(date_str, end_time)
            if dt:
                all_events.append((dt, machine, group, kind, date_str))

all_events.sort(key=lambda x: x[0])
print(f"処理日数: {len(dates_processed)}日 / 総当たりイベント数: {len(all_events):,}")

# =============================================
# カスケード検出
# グループ内で30分以内に連続発火する「連鎖」を抽出
# =============================================
def find_cascades(events, window_min=30):
    """
    events: (datetime, machine, group) のリスト（時刻順）
    同一グループ内で window_min 以内の連続発火を「カスケード」として検出
    """
    # 日付×グループごとにイベントをまとめる
    day_group_events = defaultdict(list)
    for dt, machine, group, kind, date in events:
        day_group_events[(date, group)].append((dt, machine))

    cascades = []  # 各カスケード = [(dt, machine), ...]

    for (date, group), evts in day_group_events.items():
        evts_sorted = sorted(evts, key=lambda x: x[0])
        # グリーディに連鎖を構築
        used = [False] * len(evts_sorted)
        for i in range(len(evts_sorted)):
            if used[i]:
                continue
            chain = [evts_sorted[i]]
            used[i] = True
            last_dt = evts_sorted[i][0]
            for j in range(i+1, len(evts_sorted)):
                if used[j]:
                    continue
                gap = (evts_sorted[j][0] - last_dt).total_seconds() / 60
                if 0 < gap <= window_min:
                    chain.append(evts_sorted[j])
                    used[j] = True
                    last_dt = evts_sorted[j][0]
                elif gap > window_min:
                    break
            if len(chain) >= 2:
                cascades.append({'date': date, 'group': group, 'chain': chain})

    return cascades

cascades = find_cascades(all_events)

# =============================================
# 1. カスケード長さの分布
# =============================================
print("\n" + "=" * 60)
print("【1】カスケード長さの分布（全グループ合計）")
print(f"  ウィンドウ: {WINDOW_MIN}分 / 対象: {len(dates_processed)}日分")
print("=" * 60)

length_dist = defaultdict(int)
for c in cascades:
    length_dist[len(c['chain'])] += 1

total_cascades = sum(length_dist.values())
print(f"\n  総カスケード数: {total_cascades:,}")
print(f"\n  {'長さ':>4}  {'件数':>6}  {'割合':>7}  {'バー'}")
print("  " + "-" * 45)
for length in sorted(length_dist.keys()):
    cnt = length_dist[length]
    pct = cnt / total_cascades * 100
    bar = "█" * min(int(pct), 40)
    print(f"  {length:>3}台  {cnt:>6,}  {pct:>6.1f}%  {bar}")

# =============================================
# 2. グループ別カスケード傾向
# =============================================
print("\n" + "=" * 60)
print("【2】グループ別カスケード傾向")
print("=" * 60)

group_stats = defaultdict(lambda: {'count': 0, 'total_len': 0, 'max_len': 0, 'long': 0})
for c in cascades:
    g = c['group']
    l = len(c['chain'])
    group_stats[g]['count'] += 1
    group_stats[g]['total_len'] += l
    group_stats[g]['max_len'] = max(group_stats[g]['max_len'], l)
    if l >= 4:
        group_stats[g]['long'] += 1

print(f"\n  {'G':>3}  {'総数':>6}  {'平均長':>6}  {'最大長':>6}  {'4台以上':>7}  {'1日平均':>8}")
print("  " + "-" * 50)
for g in sorted(group_stats.keys()):
    s = group_stats[g]
    avg_len = s['total_len'] / s['count'] if s['count'] > 0 else 0
    per_day = s['count'] / len(dates_processed)
    print(f"  G{g}  {s['count']:>6,}  {avg_len:>6.2f}台  {s['max_len']:>5}台  {s['long']:>6,}件  {per_day:>7.2f}回/日")

# =============================================
# 3. カスケード内の発火間隔分布
# =============================================
print("\n" + "=" * 60)
print("【3】カスケード内 発火間隔の分布")
print("  「前の台が当たってから何分後に次が当たるか」")
print("=" * 60)

intervals = []
for c in cascades:
    chain = c['chain']
    for i in range(1, len(chain)):
        gap = (chain[i][0] - chain[i-1][0]).total_seconds() / 60
        if 0 < gap <= WINDOW_MIN:
            intervals.append(gap)

if intervals:
    buckets = [(0,5), (5,10), (10,15), (15,20), (20,25), (25,30)]
    print(f"\n  総インターバル数: {len(intervals):,}")
    print(f"  平均: {sum(intervals)/len(intervals):.1f}分 / 中央値: {sorted(intervals)[len(intervals)//2]:.1f}分\n")
    print(f"  {'区間':>8}  {'件数':>6}  {'割合':>7}  {'バー'}")
    print("  " + "-" * 45)
    for lo, hi in buckets:
        cnt = sum(1 for x in intervals if lo <= x < hi)
        pct = cnt / len(intervals) * 100
        bar = "█" * min(int(pct / 2), 30)
        print(f"  {lo:>2}〜{hi:>2}分  {cnt:>6,}  {pct:>6.1f}%  {bar}")

# =============================================
# 4. カスケード後の「残り台」発火確率
# =============================================
print("\n" + "=" * 60)
print("【4】カスケード後の残り台 発火確率")
print("  「N台のカスケードが終わった後、同グループで")
print("   さらに30分以内に追加発火する確率」")
print("=" * 60)

# カスケード終了後30分以内に同グループで発火があるか
# 日×グループのイベントリストを再構築
day_group_events = defaultdict(list)
for dt, machine, group, kind, date in all_events:
    day_group_events[(date, group)].append(dt)
for key in day_group_events:
    day_group_events[key].sort()

cascade_follow = defaultdict(lambda: {'total': 0, 'followed': 0})

for c in cascades:
    length = len(c['chain'])
    last_dt = c['chain'][-1][0]
    date = c['date']
    group = c['group']
    # カスケード終了後30分以内に同グループで発火があるか
    next_events = [dt for dt in day_group_events[(date, group)]
                   if 0 < (dt - last_dt).total_seconds() / 60 <= WINDOW_MIN]
    # カスケード内の台を除外
    cascade_machines_times = {dt for dt, mac in c['chain']}
    next_events = [dt for dt in next_events if dt not in cascade_machines_times]

    cascade_follow[length]['total'] += 1
    if next_events:
        cascade_follow[length]['followed'] += 1

print(f"\n  {'カスケ長':>6}  {'観測数':>6}  {'追加発火':>8}  {'確率':>7}")
print("  " + "-" * 35)
for length in sorted(cascade_follow.keys()):
    s = cascade_follow[length]
    prob = s['followed'] / s['total'] * 100 if s['total'] > 0 else 0
    print(f"  {length:>4}台後   {s['total']:>5,}   {s['followed']:>7,}   {prob:>6.1f}%")

# =============================================
# 5. 39-77番限定のカスケード
# =============================================
print("\n" + "=" * 60)
print("【5】39〜77番限定 カスケード分析")
print("=" * 60)

TARGET = set(str(i).zfill(3) for i in range(39, 78))

target_cascades = []
for c in cascades:
    target_chain = [(dt, mac) for dt, mac in c['chain'] if mac.zfill(3) in TARGET]
    if len(target_chain) >= 2:
        target_cascades.append({'date': c['date'], 'group': c['group'], 'chain': target_chain})

t_length_dist = defaultdict(int)
for c in target_cascades:
    t_length_dist[len(c['chain'])] += 1

print(f"\n  39〜77番が2台以上絡むカスケード: {len(target_cascades):,}件")
print(f"\n  {'長さ':>4}  {'件数':>6}  {'割合':>7}")
print("  " + "-" * 30)
total_t = sum(t_length_dist.values())
for length in sorted(t_length_dist.keys()):
    cnt = t_length_dist[length]
    pct = cnt / total_t * 100
    print(f"  {length:>3}台  {cnt:>6,}  {pct:>6.1f}%")

# グループ別
t_group = defaultdict(int)
for c in target_cascades:
    t_group[c['group']] += 1
print(f"\n  グループ別件数:")
for g, cnt in sorted(t_group.items(), key=lambda x: x[1], reverse=True):
    bar = "█" * (cnt // 5)
    print(f"    G{g}: {cnt:>4}件  {bar}")
