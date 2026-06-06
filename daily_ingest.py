"""
daily_ingest.py
================
日次取り込みパイプライン

新しい YYYYMMDD_analyze.csv が追加されるたびに実行すると:
  1. まだ snapshot 化されていない日を検出して build_snapshots を呼ぶ
  2. まだ履歴に取り込まれていない日について、その日単独の伝播liftを計算
  3. pair_history.json にペアごとの日次liftを追記
  4. 累積で再現性スコア (reproducibility) を再計算
  5. reports/YYYYMMDD_report.md に当日レポートを出力

履歴の蓄積方式 (重要な設計):
  - ペアキーは "G{group}|{A}|{B}" (グループ内ペアのみ。グループ間は無相関と判明済み)
  - 各ペアについて日次の {date, count, lift} を配列で保持
  - 再現性スコア = (lift>=THRESHOLD だった日数) / (そのペアが観測された日数)
    → 「たまたま1日だけ高lift」と「毎回高lift」を区別する
  - mean_lift は count による加重平均 (件数の多い日を信頼)

これを3ヶ月回すと、reproducibility が高く observed_days も多いペアが
「安定した伝播」の候補として浮上する。

使い方:
  python daily_ingest.py                  # 未取り込みの全日を処理
  python daily_ingest.py --rebuild        # 履歴を破棄して全日再構築
  python daily_ingest.py --window 3 --lift-threshold 1.5 --min-count 2
  python daily_ingest.py --summary        # 取り込みせず現在の履歴サマリだけ表示
"""

import json
import sys
import argparse
import subprocess
from pathlib import Path
from collections import defaultdict
from datetime import datetime

ROOT = Path(__file__).parent
ANALYZE_DIR = ROOT / "csv" / "analyze"
SNAP_DIR = ROOT / "csv" / "replay"
REPORT_DIR = ROOT / "reports"
HISTORY_PATH = ROOT / "pair_history.json"

# propagation.py の関数を再利用
from propagation import load_snaps, analyze_group_internal, machine_range, extract_starts

REPORT_DIR.mkdir(parents=True, exist_ok=True)


# ---------- snapshot 同期 ----------
def find_analyze_dates():
    """csv/analyze/ にある日付一覧"""
    if not ANALYZE_DIR.exists():
        return []
    out = []
    for d in sorted(ANALYZE_DIR.iterdir()):
        if d.is_dir() and list(d.glob("*_analyze.csv")):
            out.append(d.name)
    return out

def find_snapshot_dates():
    if not SNAP_DIR.exists():
        return set()
    return {p.stem.replace("_snapshot", "") for p in SNAP_DIR.glob("*_snapshot.json")}

def ensure_snapshots(verbose=True):
    """analyze にあって snapshot に無い日があれば build_snapshots を実行"""
    analyze_dates = set(find_analyze_dates())
    snap_dates = find_snapshot_dates()
    missing = analyze_dates - snap_dates
    if missing:
        if verbose:
            print(f"📸 未スナップショットの日を検出: {sorted(missing)}")
            print(f"   build_snapshots.py を実行します...")
        r = subprocess.run([sys.executable, str(ROOT / "build_snapshots.py")],
                           cwd=str(ROOT))
        if r.returncode != 0:
            print("⚠ build_snapshots.py がエラー終了しました")
            sys.exit(1)
    elif verbose:
        print("📸 スナップショットは最新です")


# ---------- 履歴 ----------
def load_history():
    if HISTORY_PATH.exists():
        with open(HISTORY_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {"meta": {"ingested_dates": [], "params": {}}, "pairs": {}}

def save_history(hist):
    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(hist, f, ensure_ascii=False, indent=1)

def pair_key(group, A, B):
    return f"G{group}|{A}|{B}"


def ingest_day(hist, date, window_steps, min_count):
    """1日分の単独lift を計算して履歴に追記"""
    snaps = load_snaps([date])
    if date not in snaps:
        print(f"  ⚠ {date}: snapshot が見つかりません"); return 0
    rows = analyze_group_internal(snaps, window_steps=window_steps, min_count=min_count)
    added = 0
    for r in rows:
        key = pair_key(r["gA"], r["A"], r["B"])
        if key not in hist["pairs"]:
            hist["pairs"][key] = {
                "group": r["gA"], "A": r["A"], "B": r["B"],
                "islandA": r["islandA"], "islandB": r["islandB"],
                "daily": [],
            }
        # 同じ日を二重登録しない
        if any(d["date"] == date for d in hist["pairs"][key]["daily"]):
            continue
        hist["pairs"][key]["daily"].append({
            "date": date, "count": r["count"], "lift": r["lift"],
        })
        added += 1
    return added


def recompute_scores(hist, lift_threshold):
    """全ペアの集計指標を再計算"""
    for key, p in hist["pairs"].items():
        daily = p["daily"]
        days_seen = len(daily)
        total_count = sum(d["count"] for d in daily)
        days_over = sum(1 for d in daily if d["lift"] >= lift_threshold)
        # count加重平均lift
        if total_count > 0:
            mean_lift = sum(d["lift"] * d["count"] for d in daily) / total_count
        else:
            mean_lift = 0.0
        p["total_count"] = total_count
        p["days_seen"] = days_seen
        p["days_lift_over_threshold"] = days_over
        p["mean_lift"] = round(mean_lift, 2)
        p["reproducibility"] = round(days_over / days_seen, 3) if days_seen else 0.0


# ---------- レポート ----------
def confidence_label(p, min_days, min_total_count):
    """現データ量に応じた確度ラベル"""
    if p["days_seen"] < min_days or p["total_count"] < min_total_count:
        return "候補(件数不足)"
    if p["reproducibility"] >= 0.6 and p["mean_lift"] >= 1.5:
        return "★確度高"
    if p["reproducibility"] >= 0.4:
        return "○再現傾向"
    return "△不安定"

def write_report(hist, date, window_steps, lift_threshold,
                 min_days, min_total_count, target_lo=39, target_hi=77):
    snaps = load_snaps([date])
    snap = snaps.get(date)

    # 当日の単独lift上位 (件数順)
    today_rows = analyze_group_internal(snaps, window_steps=window_steps, min_count=2)
    today_rows.sort(key=lambda r: (r["count"], r["lift"]), reverse=True)

    # 累積でのペアランキング (確度高 → 件数)
    pairs = list(hist["pairs"].values())
    def in_target(m):
        try: return target_lo <= int(m) <= target_hi
        except: return False

    # 累積ランキング: 件数十分なものを再現性×平均liftで
    ranked = [p for p in pairs if p["days_seen"] >= min_days and p["total_count"] >= min_total_count]
    ranked.sort(key=lambda p: (p["reproducibility"], p["mean_lift"], p["total_count"]), reverse=True)

    target_pairs = [p for p in pairs if in_target(p["A"]) or in_target(p["B"])]
    target_pairs.sort(key=lambda p: (p["total_count"], p["mean_lift"]), reverse=True)

    n_active = len([m for m in snap["machines"] if m["active"]]) if snap else 0
    n_events = len(extract_starts(snap)) if snap else 0

    lines = []
    lines.append(f"# 日次伝播レポート {date}")
    lines.append("")
    lines.append(f"- 窓: {window_steps*10}分 / lift閾値: {lift_threshold}")
    lines.append(f"- 当日: 稼働 {n_active}台 / 当たり開始 {n_events}件")
    lines.append(f"- 累積取込日数: {len(hist['meta']['ingested_dates'])}日 ({', '.join(hist['meta']['ingested_dates'])})")
    lines.append(f"- 履歴ペア総数: {len(pairs)}")
    lines.append("")

    lines.append("## 当日の伝播ペア (件数順 TOP15)")
    lines.append("")
    lines.append("| A→B | G | 島 | 当日count | 当日lift |")
    lines.append("|---|---|---|---|---|")
    for r in today_rows[:15]:
        lines.append(f"| {r['A']}→{r['B']} | G{r['gA']} | {r['islandA']}→{r['islandB']} | {r['count']} | {r['lift']} |")
    lines.append("")

    lines.append(f"## 累積ランキング (days≥{min_days} & count≥{min_total_count})")
    lines.append("")
    if ranked:
        lines.append("| A→B | G | 観測日数 | 累計count | 平均lift | 再現性 | 確度 |")
        lines.append("|---|---|---|---|---|---|---|")
        for p in ranked[:20]:
            lbl = confidence_label(p, min_days, min_total_count)
            lines.append(f"| {p['A']}→{p['B']} | G{p['group']} | {p['days_seen']} | "
                        f"{p['total_count']} | {p['mean_lift']} | {p['reproducibility']} | {lbl} |")
    else:
        lines.append(f"_まだ days≥{min_days} かつ count≥{min_total_count} を満たすペアがありません。データ蓄積待ち。_")
    lines.append("")

    lines.append(f"## 最終ターゲット {target_lo}-{target_hi}番 が関わるペア")
    lines.append("")
    if target_pairs:
        lines.append("| A→B | G | 観測日数 | 累計count | 平均lift | 再現性 | 対象 |")
        lines.append("|---|---|---|---|---|---|---|")
        for p in target_pairs[:15]:
            tag = []
            if in_target(p["A"]): tag.append("A")
            if in_target(p["B"]): tag.append("B")
            lines.append(f"| {p['A']}→{p['B']} | G{p['group']} | {p['days_seen']} | "
                        f"{p['total_count']} | {p['mean_lift']} | {p['reproducibility']} | {'/'.join(tag)} |")
    else:
        lines.append("_該当ペアなし_")
    lines.append("")
    lines.append("---")
    lines.append(f"_生成: {datetime.now().strftime('%Y-%m-%d %H:%M')}_")

    report_path = REPORT_DIR / f"{date}_report.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path, ranked, target_pairs


def print_summary(hist, min_days, min_total_count, lift_threshold):
    pairs = list(hist["pairs"].values())
    ranked = [p for p in pairs if p["days_seen"] >= min_days and p["total_count"] >= min_total_count]
    ranked.sort(key=lambda p:(p["reproducibility"], p["mean_lift"], p["total_count"]), reverse=True)
    print(f"\n{'='*72}")
    print(f"📊 履歴サマリ  (取込 {len(hist['meta']['ingested_dates'])}日 / ペア {len(pairs)})")
    print(f"{'='*72}")
    print(f"足切り: 観測≥{min_days}日 & 累計count≥{min_total_count}  → {len(ranked)}ペアが基準クリア")
    if ranked:
        print(f"\n{'A→B':>12} {'G':>3} {'日数':>4} {'count':>6} {'平均lift':>8} {'再現性':>7}")
        for p in ranked[:20]:
            print(f"{p['A']:>5}→{p['B']:<5} G{p['group']:>2} {p['days_seen']:>4} "
                  f"{p['total_count']:>6} {p['mean_lift']:>8} {p['reproducibility']:>7}")
    else:
        print("  まだ基準を満たすペアなし。データ蓄積を続けてください。")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", type=int, default=3, help="伝播窓 (step, 1=10分)")
    ap.add_argument("--lift-threshold", type=float, default=1.5, help="再現性カウントのlift閾値")
    ap.add_argument("--min-count", type=int, default=2, help="単日ペア足切り")
    ap.add_argument("--min-days", type=int, default=3, help="ランキング対象の最小観測日数")
    ap.add_argument("--min-total-count", type=int, default=8, help="ランキング対象の最小累計count")
    ap.add_argument("--rebuild", action="store_true", help="履歴を破棄して全日再構築")
    ap.add_argument("--summary", action="store_true", help="取込せず履歴サマリのみ")
    args = ap.parse_args()

    if args.summary:
        hist = load_history()
        recompute_scores(hist, args.lift_threshold)
        print_summary(hist, args.min_days, args.min_total_count, args.lift_threshold)
        return

    print("="*72)
    print("⚡ 日次取り込みパイプライン")
    print("="*72)

    # 1. snapshot 同期
    ensure_snapshots()

    # 2. 履歴ロード
    if args.rebuild and HISTORY_PATH.exists():
        print("🗑  --rebuild: 既存履歴を破棄")
        HISTORY_PATH.unlink()
    hist = load_history()
    hist["meta"]["params"] = {
        "window": args.window, "lift_threshold": args.lift_threshold,
        "min_count": args.min_count,
    }

    # 3. 未取込の日を処理
    snap_dates = sorted(find_snapshot_dates())
    ingested = set(hist["meta"]["ingested_dates"])
    todo = [d for d in snap_dates if d not in ingested]
    if not todo:
        print("✓ 新しく取り込む日はありません")
    else:
        print(f"📥 取り込み対象: {todo}")
        for date in todo:
            added = ingest_day(hist, date, args.window, args.min_count)
            hist["meta"]["ingested_dates"].append(date)
            print(f"  ✓ {date}: {added}ペア追記")

    # 4. スコア再計算
    recompute_scores(hist, args.lift_threshold)
    save_history(hist)
    print(f"💾 {HISTORY_PATH}")

    # 5. レポート (最新日)
    if snap_dates:
        latest = snap_dates[-1]
        report_path, ranked, target_pairs = write_report(
            hist, latest, args.window, args.lift_threshold,
            args.min_days, args.min_total_count)
        print(f"📄 {report_path}")

    # 6. サマリ表示
    print_summary(hist, args.min_days, args.min_total_count, args.lift_threshold)

    # データ量に応じた案内
    n_days = len(hist["meta"]["ingested_dates"])
    print(f"\n{'─'*72}")
    if n_days < 14:
        print(f"ℹ  現在{n_days}日。2週間(14日)を超えると足切り後のペアが安定し始めます。")
    elif n_days < 60:
        print(f"ℹ  現在{n_days}日。再現性スコアの分布が見え始める頃。60日で本格判断へ。")
    else:
        print(f"ℹ  現在{n_days}日。バックテスト(学習/検証分割)を始められる量です。")

if __name__ == "__main__":
    main()
