import json

with open('pair_history.json', encoding='utf-8') as f:
    data = json.load(f)

TARGET = set(str(i).zfill(3) for i in range(39, 78))

results = []
for key, p in data['pairs'].items():
    a = p['A'].zfill(3)
    b = p['B'].zfill(3)
    if a in TARGET and b in TARGET:
        results.append(p)

# 足切り: days_seen>=3 & total_count>=8
filtered = [p for p in results if p['days_seen'] >= 3 and p['total_count'] >= 8]
filtered.sort(key=lambda x: x['mean_lift'], reverse=True)

print(f'39-77番内ペア総数: {len(results)}')
print(f'足切り通過(days>=3 & count>=8): {len(filtered)}')
print()
print(f'{"A->B":<10} {"G":<4} {"日数":>4} {"count":>6} {"平均lift":>8} {"再現性":>6}')
print('-' * 50)
for p in filtered:
    print(f'{p["A"]}->{p["B"]:<6} G{p["group"]:<3} {p["days_seen"]:>4} {p["total_count"]:>6} {p["mean_lift"]:>8.2f} {p["reproducibility"]:>6.3f}')
