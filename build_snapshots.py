"""
build_snapshots.py
===================
analyze.csv → 10分刻みスナップショットJSON

各台×日付の「区間データ」を解釈して、10:00〜22:30の10分刻みで
各台の状態 (差玉, 当たり中フラグ, 稼働状況) を計算しJSONに固める。

出力: csv/replay/YYYYMMDD_snapshot.json

JSONスキーマ:
{
  "date": "2026/05/01",
  "steps": ["10:00", "10:10", ..., "22:30"],     # 76個
  "machines": [
    {
      "machine": "001",
      "group": "海物語",
      "island": "A島",
      "active": true,                            # 全行NS/NC/NEなら false
      "atari_total": 8,                          # その日の総当り数
      "final_close": 7041,                       # 最終差玉
      "high": 9924, "low": -861,
      # 各stepの状態 (steps配列と対応)
      "ball": [0, 0, -120, ...],                 # 差玉 (補間)
      "kind": ["通常", "通常", "当り", ...],     # その時刻にいる区間の種別
      "atari_count": [0, 0, 0, 1, 1, 1, 2, ...], # その時刻までの累積当り回数
    },
    ...
  ]
}
"""

import csv, json, os, sys
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).parent
ANALYZE_DIR = ROOT / "csv" / "analyze"
OUT_DIR     = ROOT / "csv" / "replay"
MASTER_PATH = ROOT / "machine_master.csv"

OUT_DIR.mkdir(parents=True, exist_ok=True)

T_START = 10*60       # 10:00
T_END   = 22*60+30    # 22:30
STEP_MIN = 10
N_STEPS = (T_END - T_START) // STEP_MIN + 1   # 76

ATARI_KINDS = {"当り", "大当り"}
# 実データは「稼働なし(NS)」「稼働なし(NE)」「稼働なし(NC1)」のように
# 種別文字列で「稼働なし」を含む。判定は部分一致でおこなう。
def is_idle_kind(k: str) -> bool:
    return "稼働なし" in k or k in {"NS","NC","NE"}

def t2m(t):
    h, m = map(int, t.split(":"))
    return h * 60 + m

def m2t(m):
    return f"{m//60:02d}:{m%60:02d}"

STEPS = [m2t(T_START + i*STEP_MIN) for i in range(N_STEPS)]

def load_master():
    m = {}
    if not MASTER_PATH.exists():
        return m
    with open(MASTER_PATH, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            key = str(r["machine"]).strip().lstrip("0") or "0"
            m[key] = (r.get("group","").strip(), r.get("island","").strip())
    return m

def to_int(v, default=0):
    try: return int(float(str(v).strip()))
    except: return default

def interp(t_now, t1, v1, t2, v2):
    """t_now が [t1,t2] 内のとき v1,v2 の線形補間"""
    if t2 <= t1: return v1
    if t_now <= t1: return v1
    if t_now >= t2: return v2
    ratio = (t_now - t1) / (t2 - t1)
    return v1 + (v2 - v1) * ratio

def build_machine_timeline(rows, machine, master):
    """1台分のスナップショットを構築"""
    # 開始時刻順にソート
    rs = sorted(rows, key=lambda r: t2m(r["開始時刻"]))
    kinds = [r["種別"] for r in rs]
    is_idle = all(is_idle_kind(k) for k in kinds)

    # 実データには Group/Island カラムが入っているのでそれを優先する
    # 無ければ master をフォールバック
    group  = rs[0].get("Group","").strip()
    island = rs[0].get("Island","").strip()
    if (not group or not island) and machine in master:
        m_group, m_island = master[machine]
        group  = group  or m_group
        island = island or m_island

    if is_idle:
        return {
            "machine": machine, "group": group, "island": island,
            "active": False, "atari_total": 0,
            "final_close": 0, "high": 0, "low": 0,
            "ball":        [0]*N_STEPS,
            "kind":        ["稼働なし"]*N_STEPS,
            "atari_count": [0]*N_STEPS,
        }

    atari_total = sum(1 for k in kinds if k in ATARI_KINDS)

    # high/low/final
    vals = [0]
    for r in rs:
        vals.append(to_int(r["開始差玉"]))
        vals.append(to_int(r["終了差玉"]))
    high = max(vals)
    low  = min(vals)
    final_close = to_int(rs[-1]["終了差玉"])

    ball = []
    kind_at = []
    atari_count = []
    running_atari = 0
    # 区間iの当たり判定済み? を管理
    counted_atari_idx = set()

    for s_idx in range(N_STEPS):
        t_now = T_START + s_idx * STEP_MIN
        v = 0
        cur_kind = "稼働なし"
        # どの区間に該当するか
        # ルール: 区間 [start, end] において start <= t_now <= end
        # 同じt_nowが2区間にまたがる場合は「直前に終わった区間と次に始まる区間」のうち
        # その時刻を含む方を優先 (= start <= t_now < end を満たす最初の区間)
        matched = None
        for i, r in enumerate(rs):
            st = t2m(r["開始時刻"])
            et = t2m(r["終了時刻"])
            if st <= t_now < et:
                matched = (i, r, st, et)
                break
            if t_now == et and i == len(rs)-1:
                # 終端ピッタリは最終区間に含める
                matched = (i, r, st, et)
                break
        if matched is None:
            # スタート前 or 最初の区間より前 → 0
            # データの最初の開始時刻より前なら ball=0
            first_st = t2m(rs[0]["開始時刻"])
            if t_now < first_st:
                v = 0; cur_kind = "未開始"
            else:
                # 全区間より後 → 最終値
                v = final_close; cur_kind = rs[-1]["種別"]
        else:
            i, r, st, et = matched
            v_start = to_int(r["開始差玉"])
            v_end   = to_int(r["終了差玉"])
            v = int(round(interp(t_now, st, v_start, et, v_end)))
            cur_kind = r["種別"]
            # この区間が当たり区間で、まだカウントしてなければカウント
            if r["種別"] in ATARI_KINDS and i not in counted_atari_idx:
                # 当たりの開始時刻に達したらカウントアップ
                counted_atari_idx.add(i)
                running_atari += 1

        ball.append(v)
        kind_at.append(cur_kind)
        atari_count.append(running_atari)

    return {
        "machine": machine, "group": group, "island": island,
        "active": True, "atari_total": atari_total,
        "final_close": final_close, "high": high, "low": low,
        "ball":        ball,
        "kind":        kind_at,
        "atari_count": atari_count,
    }

def process_day(day_csv, master):
    rows = []
    with open(day_csv, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            rows.append(r)
    if not rows:
        return None

    date = rows[0]["Date"]
    by_machine = defaultdict(list)
    for r in rows:
        m_key = str(r["Machine"]).strip().lstrip("0") or "0"
        by_machine[m_key].append(r)

    machines = []
    for m_key in sorted(by_machine.keys(), key=lambda x: int(x) if x.isdigit() else 0):
        machines.append(build_machine_timeline(by_machine[m_key], m_key, master))

    return {
        "date":     date,
        "steps":    STEPS,
        "machines": machines,
    }

def main():
    print("="*65)
    print("📸 10分刻みスナップショット構築")
    print("="*65)
    master = load_master()
    print(f"📋 master: {len(master)}台")

    day_dirs = sorted([d for d in ANALYZE_DIR.iterdir() if d.is_dir()])
    if not day_dirs:
        print("⚠ csv/analyze/ にデータがありません"); sys.exit(1)

    for day_dir in day_dirs:
        csv_files = list(day_dir.glob("*_analyze.csv"))
        if not csv_files: continue
        day_csv = csv_files[0]
        date_str = day_dir.name
        out_path = OUT_DIR / f"{date_str}_snapshot.json"
        print(f"\n▶ {day_csv}")
        data = process_day(day_csv, master)
        if data is None:
            print("  ⚠ データなし"); continue
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        print(f"  💾 {out_path}  ({len(data['machines'])}台 × {N_STEPS}step)")
    print("\n✓ 完了")

if __name__ == "__main__":
    main()
