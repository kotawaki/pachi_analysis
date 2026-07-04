"""
MA / Fibo / SL / GC を翌営業日の陽線化シグナルとして検証する。

チャート表示用の ohlc_chart.py と同じ考え方で日足OHLCを作り、
各日終了時点で見えている状態だけを使って「翌営業日が陽線か」を見る。
"""

from __future__ import annotations

import argparse
import csv
import math
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import daily_ohlc as daily_source


ROOT = Path(__file__).parent
CSV_DIR = ROOT / "csv" / "analyze"
REPORT_DIR = ROOT / "reports"


def normalize_machine(value):
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        number = int(text)
    except ValueError:
        return text
    return f"{number:03d}" if number < 1000 else str(number)


@dataclass(frozen=True)
class Pivot:
    time: str
    price: int
    close: int
    idx: int


def to_int(value, default=0):
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


def parse_time(value):
    h, m = str(value).strip().split(":")
    return int(h) * 60 + int(m)


def parse_machine_spec(spec):
    machines = set()
    for part in str(spec).split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            left, right = part.split("-", 1)
            machines.update(f"{n:03d}" for n in range(int(left), int(right) + 1))
        else:
            machines.add(normalize_machine(part))
    return machines


def iso(date):
    return f"{date[:4]}-{date[4:6]}-{date[6:8]}"


def load_daily_ohlc(machine_filter):
    source, meta = daily_source.load_daily_ohlc(machine_filter)
    if source:
        out = {}
        for machine, days in source.items():
            cum = 0
            series = []
            for date, row in sorted(days.items()):
                net = row["net"]
                open_value = cum
                close = cum + net
                series.append({
                    "date": date,
                    "time": iso(date),
                    "open": open_value,
                    "high": max(open_value + row["high"], open_value, close),
                    "low": min(open_value + row["low"], open_value, close),
                    "close": close,
                    "net": net,
                    "positive": net > 0,
                })
                cum = close
            out[machine] = series
        return out, meta

    sessions = defaultdict(lambda: defaultdict(list))
    meta = {}
    for path in sorted(CSV_DIR.glob("*/*_analyze.csv")):
        date = path.parent.name[:8]
        with path.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                machine = normalize_machine(row.get("Machine", ""))
                if machine not in machine_filter:
                    continue
                kind = str(row.get("種別", "")).strip()
                if not kind:
                    continue
                try:
                    start_time = str(row.get("開始時刻", "")).strip() or "00:00"
                    end_time = str(row.get("終了時刻", "")).strip() or start_time
                    start_ball = to_int(row.get("開始差玉"))
                    end_ball = to_int(row.get("終了差玉"))
                except (TypeError, ValueError):
                    continue
                sessions[machine][date].append((start_time, end_time, start_ball, end_ball))
                meta.setdefault(machine, {
                    "group": str(row.get("Group", "")).strip(),
                    "island": str(row.get("Island", "")).strip(),
                })

    out = {}
    for machine, days in sessions.items():
        cum = 0
        series = []
        for date in sorted(days):
            rows = sorted(days[date], key=lambda item: parse_time(item[0]))
            latest = max(rows, key=lambda item: parse_time(item[1]))
            net = latest[3]
            points = [0]
            for _, _, start_ball, end_ball in rows:
                points.append(start_ball)
                points.append(end_ball)
            day_high = max(points)
            day_low = min(points)
            open_value = cum
            close = cum + net
            series.append({
                "date": date,
                "time": iso(date),
                "open": open_value,
                "high": max(open_value + day_high, open_value, close),
                "low": min(open_value + day_low, open_value, close),
                "close": close,
                "net": net,
                "positive": net > 0,
            })
            cum = close
        out[machine] = series
    return out, meta


def calc_ma(data, period):
    values = [row["close"] for row in data]
    out = [None] * len(values)
    rolling = 0
    for i, value in enumerate(values):
        rolling += value
        if i >= period:
            rolling -= values[i - period]
        if i >= period - 1:
            out[i] = rolling / period
    return out


def detect_close_swings(data, left, right):
    highs, lows = [], []
    for i in range(left, len(data) - right):
        is_high = True
        is_low = True
        for j in range(1, left + 1):
            if data[i]["close"] <= data[i - j]["close"]:
                is_high = False
            if data[i]["close"] >= data[i - j]["close"]:
                is_low = False
        for j in range(1, right + 1):
            if data[i]["close"] <= data[i + j]["close"]:
                is_high = False
            if data[i]["close"] >= data[i + j]["close"]:
                is_low = False
        if is_high:
            highs.append(Pivot(data[i]["time"], data[i]["high"], data[i]["close"], i))
        if is_low:
            lows.append(Pivot(data[i]["time"], data[i]["low"], data[i]["close"], i))
    return highs, lows


def detect_swings(data, lb=5):
    highs, lows = [], []
    for i in range(lb, len(data) - lb):
        is_high = True
        is_low = True
        for j in range(1, lb + 1):
            if data[i]["high"] <= data[i - j]["high"] or data[i]["high"] <= data[i + j]["high"]:
                is_high = False
            if data[i]["low"] >= data[i - j]["low"] or data[i]["low"] >= data[i + j]["low"]:
                is_low = False
        if is_high:
            highs.append(Pivot(data[i]["time"], data[i]["high"], data[i]["close"], i))
        if is_low:
            lows.append(Pivot(data[i]["time"], data[i]["low"], data[i]["close"], i))
    return highs, lows


def refine_low(data, pivot):
    best = pivot
    for i in range(max(0, pivot.idx - 1), min(len(data) - 1, pivot.idx + 1) + 1):
        if data[i]["low"] < best.price:
            best = Pivot(data[i]["time"], data[i]["low"], data[i]["close"], i)
    return best


def calendar_gap(a, b):
    # ISO日付なので営業日ではなくカレンダー日でJS側と同じ制約にする。
    from datetime import date
    ad = date.fromisoformat(a)
    bd = date.fromisoformat(b)
    return (bd - ad).days


def valid_n_wave(data, sl1, sh1, sl2, sh2):
    if not (sl1.idx < sh1.idx < sl2.idx < sh2.idx):
        return False
    if not (sl2.price > sl1.price and sh2.price > sh1.price):
        return False
    gaps = [calendar_gap(sl1.time, sh1.time), calendar_gap(sh1.time, sl2.time), calendar_gap(sl2.time, sh2.time)]
    if min(gaps) <= 0 or max(gaps) > 90 or max(gaps) / min(gaps) > 5:
        return False
    if calendar_gap(sh2.time, data[-1]["time"]) > 21:
        return False
    return not any(row["low"] < sl2.price for row in data[sh2.idx + 1:])


def detect_major_bull_structure(data):
    major_highs, major_lows = detect_close_swings(data, 10, 10)
    _, medium_lows = detect_close_swings(data, 7, 7)
    recent_highs, _ = detect_close_swings(data, 7, 5)
    for sh2 in reversed(recent_highs):
        sl2_raw = next((p for p in reversed(medium_lows) if p.idx < sh2.idx), None)
        if not sl2_raw:
            continue
        sl2 = refine_low(data, sl2_raw)
        pairs = []
        for sh1 in [p for p in major_highs if p.idx < sl2_raw.idx]:
            sl1_raw = next((p for p in reversed(major_lows) if p.idx < sh1.idx), None)
            if not sl1_raw:
                continue
            sl1 = refine_low(data, sl1_raw)
            if valid_n_wave(data, sl1, sh1, sl2, sh2):
                pairs.append((sl1, sh1))
        if pairs and data[-1]["close"] >= sl2.price:
            sl1, sh1 = max(pairs, key=lambda pair: pair[1].close)
            return {"sl1": sl1, "sh1": sh1, "sl2": sl2, "sh2": sh2, "provisional": False}
    return None


def detect_bull_structure(data, lb=5):
    major = detect_major_bull_structure(data)
    if major:
        return major
    highs, lows = detect_swings(data, lb)
    if len(highs) < 2 or len(lows) < 2:
        return None
    sh2 = highs[-1]
    sl2 = next((p for p in reversed(lows) if p.idx < sh2.idx), None)
    sh1 = next((p for p in reversed(highs) if sl2 and p.idx < sl2.idx), None)
    sl1 = next((p for p in reversed(lows) if sh1 and p.idx < sh1.idx), None)
    if sl1 and sh1 and sl2 and valid_n_wave(data, sl1, sh1, sl2, sh2):
        return {"sl1": sl1, "sh1": sh1, "sl2": sl2, "sh2": sh2, "provisional": False}
    return None


def latest_bull_structure(data, lb=5):
    confirmed = detect_bull_structure(data, lb)
    candidate = detect_bull_structure(data, max(2, lb - 1))
    provisional = None
    if candidate and data[-1]["close"] >= candidate["sl2"].price:
        if not confirmed or candidate["sh2"].idx > confirmed["sh2"].idx:
            provisional = dict(candidate)
            provisional["provisional"] = True
    return provisional or confirmed


def fib_class(data, structure):
    if not structure:
        return "none"
    current = data[-1]["close"]
    high = structure["sh2"].price
    low = structure["sl2"].price
    if current > high:
        return "green"
    if current < low:
        return "broken"
    retracement = (high - current) / (high - low or 1)
    if retracement <= 0.382:
        return "blue"
    if retracement <= 0.618:
        return "yellow"
    return "red"


def detect_gc_events(ma5, ma20, ma75, data):
    events = []
    cross75 = None
    for i in range(1, len(data)):
        if None in (ma5[i], ma5[i - 1], ma20[i], ma20[i - 1], ma75[i], ma75[i - 1]):
            continue
        if ma5[i - 1] <= ma75[i - 1] and ma5[i] > ma75[i]:
            cross75 = i
        if ma5[i - 1] >= ma75[i - 1] and ma5[i] < ma75[i]:
            cross75 = None
        if cross75 is not None and ma5[i - 1] <= ma20[i - 1] and ma5[i] > ma20[i]:
            events.append({"idx": i, "date": data[i]["date"], "cross75_idx": cross75})
            cross75 = None
    return events


def median(values):
    values = sorted(values)
    if not values:
        return 0
    mid = len(values) // 2
    if len(values) % 2:
        return int(round(values[mid]))
    return int(round((values[mid - 1] + values[mid]) / 2))


def pct(part, total):
    return part / total * 100 if total else 0.0


def signed(value):
    return f"{int(round(value)):+,}"


def feature_names(ma5, ma20, ma75, gc_events, gc_index_by_idx, data, i):
    names = set()
    if ma5[i] is not None and ma20[i] is not None:
        if ma5[i] > ma20[i]:
            names.add("MA5>MA20")
        if i and ma5[i - 1] is not None and ma5[i] > ma5[i - 1]:
            names.add("MA5上向き")
    if ma20[i] is not None:
        if i and ma20[i - 1] is not None and ma20[i] > ma20[i - 1]:
            names.add("MA20上向き")
    if ma5[i] is not None and ma20[i] is not None and ma75[i] is not None:
        if ma5[i] > ma20[i] > ma75[i]:
            names.add("MA強気配列")
        if ma5[i] > ma75[i]:
            names.add("MA5>MA75")

    last_gc = max((event["idx"] for event in gc_events if event["idx"] <= i), default=None)
    if i in gc_index_by_idx:
        names.add("GC当日")
    if last_gc is not None and i - last_gc <= 20:
        names.add("GC20日以内")
    if last_gc is not None and i - last_gc <= 40:
        names.add("GC40日以内")

    structure = latest_bull_structure(data[: i + 1], 5)
    fib = fib_class(data[: i + 1], structure)
    if structure:
        names.add("SL上昇構造")
        names.add("暫定SL構造" if structure.get("provisional") else "確定SL構造")
        names.add(f"Fibo_{fib}")
        current = data[i]["close"]
        high = structure["sh2"].price
        low = structure["sl2"].price
        risk = current - low
        reward = high + (high - low) * 0.618 - current
        if risk > 0:
            rr = reward / risk
            if rr >= 1:
                names.add("RR>=1")
            if rr >= 2:
                names.add("RR>=2")
        if fib in {"green", "blue"}:
            names.add("Fibo浅押し以上")
        if fib in {"blue", "yellow"}:
            names.add("Fibo押し目帯")

    if "MA5上向き" in names and "Fibo浅押し以上" in names:
        names.add("MA上向き+Fibo浅押し")
    if "MA強気配列" in names and "SL上昇構造" in names:
        names.add("MA強気配列+SL構造")
    if "GC20日以内" in names and "Fibo浅押し以上" in names:
        names.add("GC20日以内+Fibo浅押し")
    if "MA5上向き" in names and "MA20上向き" in names and "SL上昇構造" in names:
        names.add("MA5/20上向き+SL構造")
    return names


def build_observations(machine_series, meta):
    observations = []
    for machine, data in machine_series.items():
        if len(data) < 80:
            continue
        ma5 = calc_ma(data, 5)
        ma20 = calc_ma(data, 20)
        ma75 = calc_ma(data, 75)
        gc_events = detect_gc_events(ma5, ma20, ma75, data)
        gc_index_by_idx = {event["idx"]: event for event in gc_events}
        for i in range(75, len(data) - 1):
            names = feature_names(ma5, ma20, ma75, gc_events, gc_index_by_idx, data, i)
            target = data[i + 1]
            observations.append({
                "machine": machine,
                "group": meta.get(machine, {}).get("group", ""),
                "island": meta.get(machine, {}).get("island", ""),
                "signal_date": data[i]["date"],
                "target_date": target["date"],
                "target_net": target["net"],
                "positive": target["positive"],
                "features": names,
            })
    return observations


def split_dates(observations, split_date=None):
    dates = sorted({row["target_date"] for row in observations})
    if split_date:
        return {d for d in dates if d <= split_date}, {d for d in dates if d > split_date}
    mid = max(1, len(dates) // 2)
    if mid >= len(dates):
        mid = len(dates) - 1
    return set(dates[:mid]), set(dates[mid:])


def summarize(values):
    total = len(values)
    positives = sum(row["positive"] for row in values)
    return {
        "total": total,
        "positive": positives,
        "rate": positives / total if total else 0.0,
        "median": median([row["target_net"] for row in values]),
        "avg": sum(row["target_net"] for row in values) / total if total else 0.0,
    }


def collect_feature_stats(observations, date_set, min_count):
    rows = [row for row in observations if row["target_date"] in date_set]
    base = summarize(rows)
    features = sorted({feature for row in rows for feature in row["features"]})
    out = {}
    for feature in features:
        hit = [row for row in rows if feature in row["features"]]
        no_hit = [row for row in rows if feature not in row["features"]]
        if len(hit) < min_count:
            continue
        hit_s = summarize(hit)
        no_s = summarize(no_hit)
        lift = hit_s["rate"] / base["rate"] if base["rate"] else 0.0
        diff = hit_s["rate"] - no_s["rate"]
        pooled = base["rate"]
        if hit_s["total"] and no_s["total"]:
            se = math.sqrt(max(pooled * (1 - pooled) * (1 / hit_s["total"] + 1 / no_s["total"]), 1e-12))
            z = diff / se
        else:
            z = 0.0
        out[feature] = {
            "feature": feature,
            "hit": hit_s,
            "no_hit": no_s,
            "base": base,
            "lift": lift,
            "diff": diff,
            "z": z,
        }
    return out, base


def table(rows, limit):
    lines = [
        "|順位|シグナル|学習lift|学習 陽線率|学習件数|検証lift|検証 陽線率|検証件数|検証中央値|",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for i, row in enumerate(rows[:limit], 1):
        valid = row.get("valid")
        if valid:
            valid_text = (
                f"{valid['lift']:.2f}|"
                f"{valid['hit']['positive']}/{valid['hit']['total']} ({pct(valid['hit']['positive'], valid['hit']['total']):.1f}%)|"
                f"{valid['hit']['total']}|{signed(valid['hit']['median'])}"
            )
        else:
            valid_text = "-|-|-|-"
        lines.append(
            f"|{i}|{row['feature']}|{row['lift']:.2f}|"
            f"{row['hit']['positive']}/{row['hit']['total']} ({pct(row['hit']['positive'], row['hit']['total']):.1f}%)|"
            f"{row['hit']['total']}|{valid_text}|"
        )
    return "\n".join(lines)


def make_report(args, observations, train_dates, valid_dates, train_stats, valid_stats, train_base, valid_base):
    combined = []
    for feature, row in train_stats.items():
        copy = dict(row)
        copy["valid"] = valid_stats.get(feature)
        combined.append(copy)
    combined.sort(key=lambda row: (row["z"], row["lift"], row["hit"]["total"]), reverse=True)
    replicated = [
        row for row in combined
        if row.get("valid")
        and row["lift"] >= args.min_train_lift
        and row["valid"]["lift"] >= args.min_valid_lift
        and row["valid"]["hit"]["total"] >= args.min_valid_count
    ]
    replicated.sort(key=lambda row: (row["valid"]["lift"], row["valid"]["hit"]["total"]), reverse=True)

    lines = [
        "# MA/Fibo/SL/GC 陽線化シグナル検証",
        "",
        "## 定義",
        "",
        "- シグナル日: 各台の日足終了時点で見えているチャート状態",
        "- 判定対象: シグナル日の翌営業日が陽線(日次最終差玉 > 0)になるか",
        "- MA: MA5/20/75、傾き、強気配列",
        "- GC: MA5がMA75を上抜けた後、MA5がMA20を上抜ける順序",
        "- SL/Fibo: 差玉チャートの上昇N波構造(SL1→SH1→SL2→SH2)と現在位置",
        "- 注意: 当日CSVの結果を使って同日を判定せず、翌営業日だけを見る",
        "",
        "## 期間分割",
        "",
        f"- 対象台: {args.machines}",
        f"- 観測数: {len(observations)} machine-days",
        f"- 学習期間: {min(train_dates)} 〜 {max(train_dates)} ({len(train_dates)}日)",
        f"- 検証期間: {min(valid_dates)} 〜 {max(valid_dates)} ({len(valid_dates)}日)",
        f"- 学習ベース陽線率: {train_base['positive']}/{train_base['total']} ({pct(train_base['positive'], train_base['total']):.1f}%)",
        f"- 検証ベース陽線率: {valid_base['positive']}/{valid_base['total']} ({pct(valid_base['positive'], valid_base['total']):.1f}%)",
        "",
        "## 学習上位と検証結果",
        "",
        table(combined, args.top),
        "",
        "## 再現候補",
        "",
        f"条件: 学習lift >= {args.min_train_lift}, 検証lift >= {args.min_valid_lift}, 検証件数 >= {args.min_valid_count}",
        "",
        table(replicated, args.top) if replicated else "該当なし",
        "",
        "## 読み方",
        "",
        "- liftは、そのシグナルが出た翌営業日の陽線率 / 全体陽線率。",
        "- 検証件数が少ないシグナルは候補扱いしない。",
        "- Fibo/SL/GCはチャート構造の状態であり、当否そのものの予測ではない。",
        "",
    ]
    return "\n".join(lines)


def parse_args():
    parser = argparse.ArgumentParser(description="MA/Fibo/SL/GCの翌営業日陽線化シグナルを検証")
    parser.add_argument("--machines", default="39-77", help="対象台。例: 39-77 または 39,45,52,69")
    parser.add_argument("--split-date", default=None, help="学習期間の最終日 YYYYMMDD。未指定なら前後半分割")
    parser.add_argument("--min-count", type=int, default=20, help="学習上位に残す最小シグナル件数")
    parser.add_argument("--min-train-lift", type=float, default=1.1, help="再現候補に必要な学習lift")
    parser.add_argument("--min-valid-lift", type=float, default=1.1, help="再現候補に必要な検証lift")
    parser.add_argument("--min-valid-count", type=int, default=20, help="再現候補に必要な検証件数")
    parser.add_argument("--top", type=int, default=30, help="表示件数")
    parser.add_argument("--out", default=str(REPORT_DIR / "chart_signal_positive_report.md"), help="レポート出力先")
    return parser.parse_args()


def main():
    args = parse_args()
    machine_filter = parse_machine_spec(args.machines)
    machine_series, meta = load_daily_ohlc(machine_filter)
    observations = build_observations(machine_series, meta)
    if not observations:
        raise SystemExit("対象データがありません。")

    train_dates, valid_dates = split_dates(observations, args.split_date)
    train_stats, train_base = collect_feature_stats(observations, train_dates, args.min_count)
    valid_stats, valid_base = collect_feature_stats(observations, valid_dates, 1)
    report = make_report(args, observations, train_dates, valid_dates, train_stats, valid_stats, train_base, valid_base)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")

    print(out)
    print(f"observations={len(observations)} train={min(train_dates)}..{max(train_dates)} valid={min(valid_dates)}..{max(valid_dates)}")
    print(f"train_base={train_base['positive']}/{train_base['total']} ({pct(train_base['positive'], train_base['total']):.1f}%)")
    print(f"valid_base={valid_base['positive']}/{valid_base['total']} ({pct(valid_base['positive'], valid_base['total']):.1f}%)")


if __name__ == "__main__":
    main()
