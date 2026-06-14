"""日次predictionの実績検証と翌日ページ生成。"""

from __future__ import annotations

import argparse
import ast
import cmath
import csv
import html
import math
import re
from datetime import datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).parent
CSV_DIR = ROOT / "csv" / "analyze"
DOCS_DIR = ROOT / "docs"
RANGES = [
    ("35〜38", range(35, 39)),
    ("39〜77", range(39, 78)),
    ("118〜123", range(118, 124)),
    ("148〜153", range(148, 154)),
    ("154〜158", range(154, 159)),
    ("1173〜1180", range(1173, 1181)),
]
MACHINES = [machine for _, machines in RANGES for machine in machines]
WEIGHTS = (0.30, 0.20, 0.15, 0.10, 0.10, 0.05, 0.10)


def load_daily_net(machine: int) -> list[tuple[str, int]]:
    target = str(machine).zfill(3)
    daily = []
    for path in sorted(CSV_DIR.glob("*/*_analyze.csv")):
        rows = []
        with path.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                if str(row.get("Machine", "")).strip().zfill(3) != target:
                    continue
                end_time = str(row.get("終了時刻", "")).strip()
                if not end_time:
                    continue
                try:
                    end_ball = int(row.get("終了差玉", 0) or 0)
                except (TypeError, ValueError):
                    continue
                rows.append((end_time, end_ball))
        if rows:
            daily.append((path.parent.name, max(rows)[1]))
    return daily


def dft(values: list[float]) -> list[complex]:
    n = len(values)
    return [
        sum(value * cmath.exp(-2j * math.pi * k * t / n) for t, value in enumerate(values))
        for k in range(n)
    ]


def cycle_forecast(values: list[int], top_n: int = 5) -> int:
    n = len(values)
    mean = sum(values) / n
    coeffs = dft([value - mean for value in values])
    candidates = []
    for k in range(1, n // 2 + 1):
        period = n / k
        if 2 <= period <= n / 2:
            candidates.append((k, 2 * abs(coeffs[k]) / n))
    peaks = []
    for index, item in enumerate(candidates):
        left = candidates[index - 1][1] if index else -1
        right = candidates[index + 1][1] if index + 1 < len(candidates) else -1
        if item[1] >= left and item[1] >= right:
            peaks.append(item)
    peaks.sort(key=lambda item: item[1], reverse=True)
    value = mean + sum(2 * coeffs[k].real / n for k, _ in peaks[:top_n])
    return round(value)


def mean_tail(values: list[int], size: int) -> float:
    return sum(values[-size:]) / min(size, len(values))


def feature_rows(cutoff: str) -> dict[int, dict]:
    rows = {}
    for machine in MACHINES:
        daily = [(date, value) for date, value in load_daily_net(machine) if date <= cutoff]
        values = [value for _, value in daily]
        if len(values) < 21:
            raise ValueError(f"{machine}: prediction計算に必要な履歴が不足しています")
        ma5_now = mean_tail(values, 5)
        ma5_prev = sum(values[-6:-1]) / 5
        ma20_now = mean_tail(values, 20)
        ma20_prev = sum(values[-21:-1]) / 20
        rows[machine] = {
            "machine": machine,
            "range": next(label for label, machines in RANGES if machine in machines),
            "a3": mean_tail(values, 3),
            "a5": mean_tail(values, 5),
            "a10": mean_tail(values, 10),
            "ma5_slope": ma5_now - ma5_prev,
            "ma20_slope": ma20_now - ma20_prev,
            "win_rate": sum(value > 0 for value in values[-10:]) / 10,
            "forecast": cycle_forecast(values),
        }
    columns = [
        "forecast", "a3", "a5", "a10", "ma5_slope", "ma20_slope", "win_rate"
    ]
    for column, weight in zip(columns, WEIGHTS):
        values = [row[column] for row in rows.values()]
        mean = sum(values) / len(values)
        std = (sum((value - mean) ** 2 for value in values) / len(values)) ** 0.5 or 1
        for row in rows.values():
            row["score"] = row.get("score", 0.0) + weight * (row[column] - mean) / std
    ranked = sorted(rows.values(), key=lambda row: row["score"], reverse=True)
    for index, row in enumerate(ranked):
        row["rank"] = "本命" if index < 8 else "次点" if index < 10 else "監視" if index < 13 else ""
    return rows


def parse_locked_forecasts(path: Path) -> tuple[list[dict], dict[int, int]]:
    text = path.read_text(encoding="utf-8")
    rows_match = re.search(r"const rows=(\[.*?\]);", text, re.S)
    cycle_match = re.search(r"const cycleIslands=\{(.*?)\};\s*const cycleCard", text, re.S)
    if not rows_match or not cycle_match:
        raise ValueError(f"固定予測値を読み取れません: {path}")
    rows = []
    for body in re.findall(r"\{(.*?)\}", rows_match.group(1)):
        def field(name, default=""):
            match = re.search(rf"\b{name}:('[^']*'|-?\.\d+|-?\d+)", body)
            return ast.literal_eval(match.group(1)) if match else default
        rows.append({
            "machine": int(field("m")), "range": field("g"), "rank": field("rank"),
            "score": float(field("s", 0)), "a3": float(field("a3", 0)),
            "a5": float(field("a5", 0)), "win_rate": float(field("wr", 0)) / 100,
            "forecast": int(field("f", 0)),
        })
    cycles = {
        int(machine): int(forecast)
        for machine, forecast in re.findall(r"\[(\d+),(-?\d+)\]", cycle_match.group(1))
    }
    return rows, cycles


def actuals(date: str) -> dict[int, int]:
    out = {}
    for machine in MACHINES:
        out[machine] = dict(load_daily_net(machine)).get(date, 0)
    return out


def fmt(value: float) -> str:
    return f"{value:+,.0f}"


def render_detail(date: str, cutoff: str, rows: list[dict], cycles: dict[int, int],
                  actual: dict[int, int] | None) -> str:
    candidates = [row for row in rows if row.get("rank")]
    settled = actual is not None
    candidate_hits = sum(actual[row["machine"]] > 0 for row in candidates) if settled else 0
    cycle_positive = [machine for machine, value in cycles.items() if value > 0]
    cycle_hits = sum(actual[machine] > 0 for machine in cycle_positive) if settled else 0
    direction_hits = sum((cycles[machine] > 0) == (actual[machine] > 0) for machine in MACHINES) if settled else 0
    group_totals = {
        label: sum(actual[machine] for machine in machines) for label, machines in RANGES
    } if settled else {}
    candidate_rows = "".join(
        f"<tr><td><a href='ohlc.html?machine={r['machine']}'>{r['machine']}</a></td>"
        f"<td>{r['range']}</td><td>{r['rank']}</td><td>{r['score']:.3f}</td>"
        f"<td>{fmt(r['a3'])}</td><td>{fmt(r['a5'])}</td><td>{r['win_rate']*100:.0f}%</td>"
        f"<td>{fmt(r['forecast'])}</td>"
        + (f"<td>{fmt(actual[r['machine']])}</td><td>{'陽線' if actual[r['machine']] > 0 else '陰線'}</td>" if settled else "<td colspan='2'>実績待ち</td>")
        + "</tr>" for r in candidates
    )
    cycle_rows = "".join(
        f"<tr><td><a href='ohlc.html?machine={machine}'>{machine}</a></td>"
        f"<td>{next(label for label, machines in RANGES if machine in machines)}</td>"
        f"<td>{fmt(cycles[machine])}</td>"
        + (f"<td>{fmt(actual[machine])}</td><td>{'一致' if (cycles[machine] > 0) == (actual[machine] > 0) else '不一致'}</td>" if settled else "<td colspan='2'>実績待ち</td>")
        + "</tr>" for machine in MACHINES
    )
    group_html = "" if not settled else "<section><h2>範囲別実績差玉</h2><ul>" + "".join(
        f"<li>{label}: {fmt(value)}</li>" for label, value in group_totals.items()
    ) + "</ul></section>"
    summary = (
        f"候補陽線 {candidate_hits}/{len(candidates)} ({candidate_hits/len(candidates)*100:.1f}%) / "
        f"周期プラス陽線 {cycle_hits}/{len(cycle_positive)} ({cycle_hits/len(cycle_positive)*100:.1f}%) / "
        f"全台方向一致 {direction_hits}/{len(MACHINES)} ({direction_hits/len(MACHINES)*100:.1f}%)"
        if settled else "実績待ち"
    )
    return f"""<!doctype html><html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{date} prediction</title><style>
body{{margin:auto;max-width:1180px;padding:18px;background:#0d1117;color:#c9d1d9;font-family:Meiryo,sans-serif}}a{{color:#58a6ff}}section{{background:#161b22;border:1px solid #30363d;border-radius:9px;padding:14px;margin:12px 0;overflow:auto}}h1,h2{{color:#58a6ff}}.note{{color:#8b949e}}table{{width:100%;border-collapse:collapse;min-width:850px}}th,td{{padding:7px;border-bottom:1px solid #30363d;text-align:right}}th:first-child,td:first-child{{text-align:left}}
</style></head><body><a href="prediction_top.html">← 日次実績一覧</a><h1>{date[:4]}年{int(date[4:6])}月{int(date[6:])}日 prediction</h1>
<p class="note">学習データ終端: {cutoff}。当否予測ではなく、差玉系列の周期・移動平均を合成した参考スコアです。</p>
<section><h2>検証結果</h2><p>{summary}</p></section>{group_html}
<section><h2>候補13台</h2><table><thead><tr><th>台</th><th>範囲</th><th>区分</th><th>score</th><th>3日平均</th><th>5日平均</th><th>10日陽線率</th><th>周期推定</th><th>実績</th><th>判定</th></tr></thead><tbody>{candidate_rows}</tbody></table></section>
<section><h2>全68台 周期推定</h2><table><thead><tr><th>台</th><th>範囲</th><th>周期推定</th><th>実績</th><th>方向</th></tr></thead><tbody>{cycle_rows}</tbody></table></section>
<section class="note">score = 周期推定×30% + 3日平均×20% + 5日平均×15% + 10日平均×10% + MA5傾き×10% + MA20傾き×5% + 10日陽線率×10%。各特徴量は対象68台内で標準化しています。</section>
</body></html>"""


def render_top(actual_date: str, prediction_date: str, settled_rows: list[dict],
               settled_cycles: dict[int, int], actual: dict[int, int]) -> str:
    candidates = [row for row in settled_rows if row.get("rank")]
    hits = sum(actual[row["machine"]] > 0 for row in candidates)
    positives = [machine for machine, value in settled_cycles.items() if value > 0]
    cycle_hits = sum(actual[machine] > 0 for machine in positives)
    all_hits = sum(value > 0 for value in actual.values())
    return f"""<!doctype html><html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>日次prediction</title>
<style>body{{margin:auto;max-width:1000px;padding:18px;background:#0d1117;color:#c9d1d9;font-family:Meiryo,sans-serif}}a{{color:#58a6ff}}section{{background:#161b22;border:1px solid #30363d;border-radius:9px;padding:14px}}table{{width:100%;border-collapse:collapse}}th,td{{padding:11px;border-bottom:1px solid #30363d;text-align:right}}th:first-child,td:first-child{{text-align:left}}</style></head><body><a href="index.html">← トップ</a><h1>日次prediction</h1><section><table><thead><tr><th>予測日</th><th>候補陽線</th><th>周期プラス陽線</th><th>全台陽線</th></tr></thead><tbody>
<tr><td><a href="prediction_{prediction_date}.html">{prediction_date}</a></td><td colspan="3">実績待ち</td></tr>
<tr><td><a href="prediction_{actual_date}.html">{actual_date}</a></td><td>{hits}/{len(candidates)} ({hits/len(candidates)*100:.1f}%)</td><td>{cycle_hits}/{len(positives)} ({cycle_hits/len(positives)*100:.1f}%)</td><td>{all_hits}/{len(actual)} ({all_hits/len(actual)*100:.1f}%)</td></tr>
<tr><td><a href="prediction_20260613.html">20260613</a></td><td>6/13 (46.2%)</td><td>18/31 (58.1%)</td><td>33/68 (48.5%)</td></tr>
</tbody></table></section></body></html>"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--actual-date", required=True)
    parser.add_argument("--prediction-date", required=True)
    args = parser.parse_args()
    actual_path = DOCS_DIR / f"prediction_{args.actual_date}.html"
    locked_rows, locked_cycles = parse_locked_forecasts(actual_path)
    day_actuals = actuals(args.actual_date)
    cutoff = (datetime.strptime(args.actual_date, "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d")
    actual_path.write_text(
        render_detail(args.actual_date, cutoff, locked_rows, locked_cycles, day_actuals),
        encoding="utf-8",
    )
    features = feature_rows(args.actual_date)
    new_rows = sorted(features.values(), key=lambda row: row["score"], reverse=True)
    new_cycles = {machine: row["forecast"] for machine, row in features.items()}
    prediction_path = DOCS_DIR / f"prediction_{args.prediction_date}.html"
    prediction_path.write_text(
        render_detail(args.prediction_date, args.actual_date, new_rows, new_cycles, None), encoding="utf-8"
    )
    (DOCS_DIR / "prediction_top.html").write_text(
        render_top(args.actual_date, args.prediction_date, locked_rows, locked_cycles, day_actuals),
        encoding="utf-8",
    )
    candidates = [row for row in locked_rows if row.get("rank")]
    hits = sum(day_actuals[row["machine"]] > 0 for row in candidates)
    positives = [machine for machine, value in locked_cycles.items() if value > 0]
    cycle_hits = sum(day_actuals[machine] > 0 for machine in positives)
    print(f"actual={args.actual_date} candidates={hits}/{len(candidates)} cycle={cycle_hits}/{len(positives)} all={sum(v > 0 for v in day_actuals.values())}/{len(day_actuals)}")
    print(f"prediction={args.prediction_date} candidates=13")


if __name__ == "__main__":
    main()
