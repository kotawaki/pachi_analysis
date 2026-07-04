"""日次predictionの実績検証と翌日ページ生成。"""

from __future__ import annotations

import argparse
import ast
import cmath
import csv
import html
import json
import math
import re
from datetime import datetime, timedelta
from pathlib import Path

import daily_ohlc as daily_source


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
ISLANDS = {
    "s2": [38, 37, 36, 35],
    "s3": list(range(39, 58)),
    "s4": list(range(77, 57, -1)),
    "s7": list(range(118, 124)),
    "s8": list(range(153, 147, -1)),
    "s9": list(range(154, 159)),
    "sl1": list(range(1173, 1181)),
}
WEIGHTS = (0.30, 0.20, 0.15, 0.10, 0.10, 0.05, 0.10)
_DAILY_NET_CACHE: dict[int, list[tuple[str, int]]] | None = None


def machine_id(value: object) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def load_all_daily_net() -> dict[int, list[tuple[str, int]]]:
    source = daily_source.load_daily_net({str(machine) for machine in MACHINES})
    if source:
        return {
            machine: source.get(str(machine), [])
            for machine in MACHINES
        }

    daily: dict[int, list[tuple[str, int]]] = {machine: [] for machine in MACHINES}
    for path in sorted(CSV_DIR.glob("*/*_analyze.csv")):
        latest_by_machine: dict[int, tuple[str, int]] = {}
        with path.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                machine = machine_id(row.get("Machine", ""))
                if machine not in daily:
                    continue
                end_time = str(row.get("終了時刻", "")).strip()
                if not end_time:
                    continue
                try:
                    end_ball = int(row.get("終了差玉", 0) or 0)
                except (TypeError, ValueError):
                    continue
                current = latest_by_machine.get(machine)
                if current is None or end_time > current[0]:
                    latest_by_machine[machine] = (end_time, end_ball)
        for machine, (_end_time, end_ball) in latest_by_machine.items():
            daily[machine].append((path.parent.name, end_ball))
    return daily


def load_daily_net(machine: int) -> list[tuple[str, int]]:
    global _DAILY_NET_CACHE
    if _DAILY_NET_CACHE is None:
        _DAILY_NET_CACHE = load_all_daily_net()
    return _DAILY_NET_CACHE.get(int(machine), [])


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
    cycle_match = re.search(r"const cycleIslands=\{(.*?)\};", text, re.S)
    if not rows_match or not cycle_match:
        number = lambda value: float(value.replace(",", "").replace("+", ""))
        row_pattern = re.compile(
            r"<tr><td><a href='ohlc\.html\?machine=(\d+)'[^>]*>\d+</a></td>"
            r"<td>([^<]+)</td><td>(本命|次点|監視)</td><td>([-+.\d]+)</td>"
            r"<td>([-+,\d]+)</td><td>([-+,\d]+)</td><td>(\d+)%</td>"
            r"<td>([-+,\d]+)</td>"
        )
        rows = [{
            "machine": int(m.group(1)), "range": m.group(2), "rank": m.group(3),
            "score": number(m.group(4)), "a3": number(m.group(5)),
            "a5": number(m.group(6)), "win_rate": number(m.group(7)) / 100,
            "forecast": int(number(m.group(8))),
        } for m in row_pattern.finditer(text)]
        cycle_section = text.split("<h2>全68台 周期推定</h2>", 1)[-1]
        cycle_pattern = re.compile(
            r"<tr><td><a href='ohlc\.html\?machine=(\d+)'[^>]*>\d+</a></td>"
            r"<td>[^<]+</td><td>([-+,\d]+)</td>"
        )
        cycles = {int(m.group(1)): int(number(m.group(2))) for m in cycle_pattern.finditer(cycle_section)}
        if len(rows) == 13 and len(cycles) == len(MACHINES):
            return rows, cycles
        raise ValueError(f"固定予測値を読み取れません: {path}")
    rows = []
    if rows_match.group(1).lstrip().startswith('[{"'):
        source_rows = json.loads(rows_match.group(1))
        rows = [{
            "machine": int(row["m"]), "range": row["g"], "rank": row["rank"],
            "score": float(row["s"]), "a3": float(row["a3"]), "a5": float(row["a5"]),
            "win_rate": float(row["wr"]) / 100, "forecast": int(row["f"]),
        } for row in source_rows]
    else:
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
    if cycle_match.group(1).lstrip().startswith('"'):
        source_cycles = json.loads("{" + cycle_match.group(1) + "}")
        cycles = {int(item[0]): int(item[1]) for values in source_cycles.values() for item in values}
    else:
        cycles = {
            int(machine): int(forecast)
            for machine, forecast in re.findall(r"\[(\d+),(-?\d+)(?:,-?\d+)?\]", cycle_match.group(1))
        }
    return rows, cycles


def actuals(date: str) -> dict[int, int | None]:
    out = {}
    for machine in MACHINES:
        out[machine] = dict(load_daily_net(machine)).get(date)
    return out


def fmt(value: float) -> str:
    return f"{value:+,.0f}"


def render_detail(date: str, cutoff: str, rows: list[dict], cycles: dict[int, int],
                  actual: dict[int, int] | None, all_rows: dict[int, dict]) -> str:
    candidates = [row for row in rows if row.get("rank")]
    settled = actual is not None
    counts = {rank: sum(row["rank"] == rank for row in candidates) for rank in ("本命", "次点", "監視")}
    candidate_known = [row for row in candidates if settled and actual.get(row["machine"]) is not None]
    candidate_hits = sum(actual[row["machine"]] > 0 for row in candidate_known) if settled else 0
    cycle_positive = [machine for machine, value in cycles.items() if value > 0]
    cycle_positive_known = [machine for machine in cycle_positive if settled and actual.get(machine) is not None]
    cycle_hits = sum(actual[machine] > 0 for machine in cycle_positive_known) if settled else 0
    direction_known = [machine for machine in MACHINES if settled and actual.get(machine) is not None]
    direction_hits = sum((cycles[machine] > 0) == (actual[machine] > 0) for machine in direction_known) if settled else 0
    conclusion = "".join(
        f'<p><strong class="{css}">{rank}:</strong> ' + "、".join(
            str(row["machine"]) for row in candidates if row["rank"] == rank
        ) + "</p>"
        for rank, css in (("本命", "positive"), ("次点", "warning"), ("監視", ""))
    )
    aggregate_rows = []
    for label, machines in RANGES:
        group = [all_rows[machine] for machine in machines]
        score = sum(row["score"] for row in group) / len(group)
        a3 = sum(row["a3"] for row in group)
        a5 = sum(row["a5"] for row in group)
        forecast = sum(cycles[machine] for machine in machines)
        if score >= 0.15:
            judgement, css = "上向き", "positive"
        elif score >= 0:
            judgement, css = "やや上向き", "warning"
        elif score > -0.35:
            judgement, css = "弱い", "negative"
        else:
            judgement, css = "下向き", "negative"
        aggregate_rows.append(
            f'<tr><td>{label}番</td><td>{score:+.3f}</td><td>{fmt(a3)}</td>'
            f'<td>{fmt(a5)}</td><td>{fmt(forecast)}</td><td class="{css}">{judgement}</td></tr>'
        )
    row_data = [{
        "m": row["machine"], "g": row["range"], "rank": row["rank"],
        "s": round(row["score"], 3), "a3": round(row["a3"]), "a5": round(row["a5"]),
        "wr": round(row["win_rate"] * 100), "f": row["forecast"],
        "actual": actual[row["machine"]] if settled else None,
    } for row in candidates]
    cycle_data = {
        island: [[machine, cycles[machine], actual[machine] if settled else None] for machine in machines]
        for island, machines in ISLANDS.items()
    }
    date_label = f"{date[:4]}年{int(date[4:6])}月{int(date[6:])}日"
    short_date = f"{int(date[4:6])}/{int(date[6:])}"
    missing_candidates = len(candidates) - len(candidate_known)
    answer = f"{candidate_hits}/{len(candidate_known)}" + (f" / 未取得{missing_candidates}" if missing_candidates else "") if settled else "実績待ち"
    answer_css = "warning" if settled else "muted"
    legend = (
        f'<span class="positive">緑枠: 方向一致 {direction_hits}台</span>'
        f'<span class="negative">赤枠: 方向不一致 {len(direction_known) - direction_hits}台</span>'
        f'<span>周期プラス群の陽線: {cycle_hits}/{len(cycle_positive_known)}台（{cycle_hits / len(cycle_positive_known) * 100:.1f}%）</span>'
        if settled else f'<span>周期推定プラス: {len(cycle_positive)}/{len(MACHINES)}台（{len(cycle_positive) / len(MACHINES) * 100:.1f}%） / 実績待ち</span>'
    )
    template = """<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>__DATE__ 陽線候補・翌日答え合わせ</title><style>
*{box-sizing:border-box}body{margin:0;background:#0d1117;color:#c9d1d9;font-family:"Segoe UI",Meiryo,sans-serif;line-height:1.55;font-size:14px}header,main{max-width:1180px;margin:auto}header{padding:24px 16px 14px}main{padding:0 16px 44px;display:grid;gap:14px}h1{margin:4px 0 0;color:#58a6ff;font-size:24px}h2{margin:0 0 12px;color:#58a6ff;font-size:17px}.back,.meta,.note,.muted{color:#8b949e}.back{text-decoration:none}.panel{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:15px;overflow:auto}.summary{display:grid;grid-template-columns:repeat(4,1fr);gap:9px}.stat{background:#0d1117;border:1px solid #30363d;border-radius:7px;padding:11px}.stat span{display:block;color:#8b949e;font-size:12px}.stat b{font-size:21px}.positive{color:#3fb950}.negative{color:#f85149}.warning{color:#d29922}table{width:100%;border-collapse:collapse;min-width:850px}th,td{padding:8px 9px;border-bottom:1px solid #30363d;text-align:right;white-space:nowrap}th:first-child,td:first-child{text-align:left}th{color:#8b949e;font-size:12px;position:sticky;top:0;background:#161b22}.badge{display:inline-block;padding:2px 7px;border-radius:10px;font-size:11px;font-weight:700}.primary{background:#238636}.secondary{background:#9e6a03}.watch{background:#30363d}.machine-link,.cycle-table-link{color:#58a6ff;text-decoration:none;font-weight:800}input{width:110px;padding:6px;background:#0d1117;color:#c9d1d9;border:1px solid #30363d;border-radius:5px;text-align:right}button{padding:9px 15px;border:0;border-radius:6px;background:#238636;color:#fff;font-weight:700;cursor:pointer}.actions,.cycle-switch,.cycle-legend{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:10px}.formula{background:#0d1117;border:1px solid #30363d;padding:12px;border-radius:7px;font-family:Consolas,monospace}.small{font-size:12px}.judge-hit,.direction-hit{color:#3fb950;font-weight:700}.judge-miss,.direction-miss{color:#f85149;font-weight:700}.cycle-switch button{background:#21262d;color:#8b949e;border:1px solid #30363d;padding:6px 13px}.cycle-switch button.active{background:#1f6feb;color:#fff;border-color:#58a6ff}.cycle-view[hidden]{display:none}.sortable{cursor:pointer;user-select:none}.cycle-floor{min-width:900px}.floor-top{display:flex;align-items:flex-start;gap:7px;position:relative}.island-pair,.island-single{display:flex;gap:4px}.sl1-offset{position:absolute;left:0;top:744px}.island-column{width:112px}.island-title{text-align:center;color:#8b949e;font-size:12px;font-weight:800;margin-bottom:5px}.island-stack{display:grid;gap:4px}.cycle-card{display:block;height:58px;padding:5px 7px;border:1px solid #30363d;border-radius:6px;background:#0d1117;color:#c9d1d9;text-decoration:none}.cycle-card.wait{border-left:4px solid #58a6ff}.cycle-card.hit{border-left:4px solid #3fb950}.cycle-card.miss{border-left:4px solid #f85149}.cycle-machine{font-weight:800;color:#58a6ff}.cycle-values{display:grid;font-size:10px}.cycle-up{color:#3fb950}.cycle-down{color:#f85149}@media(max-width:720px){.summary{grid-template-columns:1fr 1fr}.cycle-floor{min-width:820px}.island-column{width:100px}}
</style></head><body><header><a class="back" href="prediction_top.html">← 日次実績一覧</a><h1>__DATE_LABEL__ 陽線候補</h1><div class="meta">予測固定: __DATE_LABEL__ / 学習データ終端: __CUTOFF__ / 対象: 6台番範囲・全68台</div></header><main>
<section class="summary"><div class="stat"><span>本命候補</span><b class="positive">__PRIMARY__台</b></div><div class="stat"><span>次点候補</span><b class="warning">__SECONDARY__台</b></div><div class="stat"><span>監視</span><b>__WATCH__台</b></div><div class="stat"><span>全13台の方向一致</span><b class="__ANSWER_CSS__">__ANSWER__</b></div></section>
<section class="panel"><h2>結論</h2>__CONCLUSION__<p class="note">本命・次点・監視は予測固定時の順位です。実績反映後も候補と周期推定は変更しません。</p></section>
<section class="panel"><h2>6つの全体合計</h2><table><thead><tr><th>範囲</th><th>全体スコア</th><th>直近3日平均</th><th>直近5日平均</th><th>周期推定</th><th>判定</th></tr></thead><tbody>__AGGREGATES__</tbody></table></section>
<section class="panel"><h2>候補台の固定記録・答え合わせ</h2><div class="actions"><button id="save">入力値を保存して再集計</button><button id="clear" style="background:#30363d">入力を実績値へ戻す</button><span id="overall" class="muted">集計中</span></div><p class="small">__RESULT_NOTE__</p><table id="candidate-table"><thead><tr><th>台</th><th>範囲</th><th>区分</th><th>スコア</th><th>直近3日平均</th><th>直近5日平均</th><th>10日陽線率</th><th>周期推定</th><th>__SHORT_DATE__実績差玉</th><th>答え</th></tr></thead><tbody></tbody></table></section>
<section class="panel"><h2>全68台 周期推定・答え合わせ</h2><div class="cycle-switch"><button type="button" data-cycle-view="map">島図</button><button type="button" data-cycle-view="list" class="active">リスト</button></div><div class="cycle-legend">__LEGEND__</div><div class="cycle-view" id="cycle-map" hidden><div class="cycle-floor" id="cycle-floor"></div></div><div class="cycle-view" id="cycle-list"></div></section>
<section class="panel"><h2>採点方法</h2><div class="formula">score = 周期推定×30% + 3日平均×20% + 5日平均×15% + 10日平均×10% + MA5傾き×10% + MA20傾き×5% + 10日陽線率×10%</div><p class="small">各特徴量は対象68台内で標準化しています。スコアは陽線確率ではなく、__CUTOFF__時点で上向き条件がどれだけ重なったかを表す比較値です。</p></section>
<section class="panel note"><strong>注意:</strong> 当否そのものは予測不能です。このページは翌日の検証を後付けなしで行うための事前記録です。</section></main><script>
const rows=__ROWS__;
const cycleIslands=__CYCLES__;
const settled=__SETTLED__,fmt=n=>(n>=0?'+':'')+Math.round(n).toLocaleString();
const hasActual=a=>a!==null&&a!==undefined;
const cycleCard=([m,f,a])=>{const known=hasActual(a),hit=settled&&known&&(f>0)===(a>0);return `<a class="cycle-card ${settled?(known?(hit?'hit':'miss'):'wait'):'wait'}" href="ohlc.html?machine=${m}"><div class="cycle-machine">${m}番</div><div class="cycle-values"><span class="${f>=0?'cycle-up':'cycle-down'}">周期 ${fmt(f)}</span><span class="${settled&&known?(a>=0?'cycle-up':'cycle-down'):'muted'}">${settled?(known?'実績 '+fmt(a):'未取得'):'実績待ち'}</span></div></a>`};
const island=name=>`<div class="island-column"><div class="island-title">${name.toUpperCase()}</div><div class="island-stack">${cycleIslands[name].map(cycleCard).join('')}</div></div>`;
document.getElementById('cycle-floor').innerHTML=`<div class="floor-top"><div class="island-pair">${island('s9')}${island('s8')}</div><div class="island-single">${island('s7')}</div><div class="island-single sl1-offset">${island('sl1')}</div><div class="island-single">${island('s4')}</div><div class="island-pair">${island('s3')}${island('s2')}</div></div>`;
const listGroups=[['35〜38番',['s2']],['39〜77番',['s3','s4']],['118〜123番',['s7']],['148〜153番',['s8']],['154〜158番',['s9']],['1173〜1180番',['sl1']]];
const cycleRows=listGroups.flatMap(([range,names])=>names.flatMap(name=>cycleIslands[name]).map(row=>[range,...row]));let cycleSort={key:'machine',dir:1};
function renderCycleTable(){const labels={machine:'台',range:'範囲',forecast:'周期推定',forecastDir:'周期方向',actual:'__SHORT_DATE__実績差玉',actualDir:'実績',hit:'方向判定'},keys=Object.keys(labels),sorted=cycleRows.slice().sort((x,y)=>{const value=r=>({machine:r[1],range:r[0],forecast:r[2],forecastDir:r[2]>0?1:0,actual:r[3]??0,actualDir:hasActual(r[3])?(r[3]>0?1:0):-1,hit:settled&&hasActual(r[3])&&((r[2]>0)===(r[3]>0))?1:0}[cycleSort.key]);const a=value(x),b=value(y);return(typeof a==='string'?a.localeCompare(b,'ja'):a-b)*cycleSort.dir});document.getElementById('cycle-list').innerHTML=`<table><thead><tr>${keys.map(key=>`<th class="sortable" data-sort="${key}">${labels[key]}${cycleSort.key===key?(cycleSort.dir>0?' ▲':' ▼'):''}</th>`).join('')}</tr></thead><tbody>${sorted.map(([range,m,f,a])=>{const known=hasActual(a),hit=settled&&known&&(f>0)===(a>0);return `<tr><td><a class="cycle-table-link" href="ohlc.html?machine=${m}">${m}</a></td><td>${range}</td><td class="${f>=0?'positive':'negative'}">${fmt(f)}</td><td>${f>0?'陽線方向':'陰線方向'}</td><td class="${settled&&known?(a>=0?'positive':'negative'):'muted'}">${settled?(known?fmt(a):'未取得'):'実績待ち'}</td><td>${settled?(known?(a>0?'陽線':'陰線'):'未取得'):'実績待ち'}</td><td class="${settled&&known?(hit?'direction-hit':'direction-miss'):'muted'}">${settled?(known?(hit?'一致':'不一致'):'未取得'):'実績待ち'}</td></tr>`}).join('')}</tbody></table>`;document.querySelectorAll('#cycle-list [data-sort]').forEach(th=>th.onclick=()=>{cycleSort=cycleSort.key===th.dataset.sort?{key:cycleSort.key,dir:-cycleSort.dir}:{key:th.dataset.sort,dir:1};renderCycleTable()})}renderCycleTable();
function setCycleView(view){document.getElementById('cycle-map').hidden=view!=='map';document.getElementById('cycle-list').hidden=view!=='list';document.querySelectorAll('[data-cycle-view]').forEach(button=>button.classList.toggle('active',button.dataset.cycleView===view));localStorage.setItem('prediction-__DATE__-cycle-view-v2',view)}document.querySelectorAll('[data-cycle-view]').forEach(button=>button.onclick=()=>setCycleView(button.dataset.cycleView));setCycleView(localStorage.getItem('prediction-__DATE__-cycle-view-v2')==='map'?'map':'list');
const body=document.querySelector('#candidate-table tbody');body.innerHTML=rows.map(r=>`<tr data-machine="${r.m}"><td><a class="machine-link" href="ohlc.html?machine=${r.m}">${r.m}</a></td><td>${r.g}</td><td><span class="badge ${r.rank==='本命'?'primary':r.rank==='次点'?'secondary':'watch'}">${r.rank}</span></td><td class="${r.s>=0?'positive':'negative'}">${r.s>=0?'+':''}${r.s.toFixed(3)}</td><td>${fmt(r.a3)}</td><td>${fmt(r.a5)}</td><td>${r.wr}%</td><td class="${r.f>=0?'positive':'negative'}">${fmt(r.f)}</td><td><input type="number" step="1" value="${r.actual??''}" aria-label="${r.m}番の実績差玉"></td><td class="answer muted">未入力</td></tr>`).join('');
const key='prediction-__DATE__-results';function evaluate(){let entered=0,hits=0;document.querySelectorAll('#candidate-table tbody tr').forEach(tr=>{const input=tr.querySelector('input'),ans=tr.querySelector('.answer');if(input.value===''){ans.className='answer muted';ans.textContent='未入力';return}entered++;const value=Number(input.value),hit=value>0;if(hit)hits++;ans.className='answer '+(hit?'judge-hit':'judge-miss');ans.textContent=hit?'陽線・一致':value<0?'陰線・不一致':'同値・不一致'});document.getElementById('overall').textContent=entered?`入力 ${entered}台 / 陽線 ${hits}台 / 方向一致率 ${(hits/entered*100).toFixed(1)}%`:'実績待ち'}
document.getElementById('save').onclick=()=>{const values={};document.querySelectorAll('#candidate-table tbody tr').forEach(tr=>{const value=tr.querySelector('input').value;if(value!=='')values[tr.dataset.machine]=value});localStorage.setItem(key,JSON.stringify(values));evaluate()};document.getElementById('clear').onclick=()=>{localStorage.removeItem(key);document.querySelectorAll('#candidate-table tbody tr').forEach((tr,index)=>tr.querySelector('input').value=rows[index].actual??'');evaluate()};try{const saved=JSON.parse(localStorage.getItem(key)||'{}');document.querySelectorAll('#candidate-table tbody tr').forEach(tr=>{if(saved[tr.dataset.machine]!==undefined)tr.querySelector('input').value=saved[tr.dataset.machine]})}catch(e){}evaluate();
</script></body></html>"""
    replacements = {
        "__DATE__": date, "__DATE_LABEL__": date_label, "__CUTOFF__": cutoff,
        "__PRIMARY__": str(counts["本命"]), "__SECONDARY__": str(counts["次点"]),
        "__WATCH__": str(counts["監視"]), "__ANSWER__": answer,
        "__ANSWER_CSS__": answer_css, "__CONCLUSION__": conclusion,
        "__AGGREGATES__": "".join(aggregate_rows), "__SHORT_DATE__": short_date,
        "__RESULT_NOTE__": f"{date_label}のCSVから、各台の最終終了差玉を実績値として入力済みです。" if settled else "実績差玉は翌日のCSV取込後に反映します。",
        "__LEGEND__": legend, "__ROWS__": json.dumps(row_data, ensure_ascii=False, separators=(",", ":")),
        "__CYCLES__": json.dumps(cycle_data, ensure_ascii=False, separators=(",", ":")),
        "__SETTLED__": "true" if settled else "false",
    }
    for token, value in replacements.items():
        template = template.replace(token, value)
    return template


def render_top(actual_date: str, prediction_date: str, settled_rows: list[dict],
               settled_cycles: dict[int, int], actual: dict[int, int | None], next_positive: int) -> str:
    candidates = [row for row in settled_rows if row.get("rank")]
    candidate_known = [row for row in candidates if actual.get(row["machine"]) is not None]
    hits = sum(actual[row["machine"]] > 0 for row in candidate_known)
    positives = [machine for machine, value in settled_cycles.items() if value > 0]
    positives_known = [machine for machine in positives if actual.get(machine) is not None]
    cycle_hits = sum(actual[machine] > 0 for machine in positives_known)
    actual_known = [value for value in actual.values() if value is not None]
    all_hits = sum(value > 0 for value in actual_known)
    rank_hits = {rank: sum(actual[row["machine"]] > 0 for row in candidate_known if row["rank"] == rank) for rank in ("本命", "次点", "監視")}
    rank_counts = {rank: sum(row["rank"] == rank for row in candidate_known) for rank in rank_hits}
    prior_hits, prior_count = 6, 13
    return f"""<!doctype html><html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>日次陽線候補 実績一覧</title><style>
*{{box-sizing:border-box}}body{{margin:0;background:#0d1117;color:#c9d1d9;font-family:"Segoe UI",Meiryo,sans-serif;line-height:1.6}}header,main{{max-width:1040px;margin:auto}}header{{padding:26px 18px 15px}}main{{padding:0 18px 44px;display:grid;gap:14px}}h1{{margin:0;color:#58a6ff;font-size:24px}}.meta,.note,.muted{{color:#8b949e;font-size:13px}}.summary{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}}.stat,.panel{{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:15px}}.stat span{{display:block;color:#8b949e;font-size:12px}}.stat b{{font-size:22px}}.warning{{color:#d29922}}table{{width:100%;border-collapse:collapse}}th,td{{padding:12px 10px;border-bottom:1px solid #30363d;text-align:right;white-space:nowrap}}th:first-child,td:first-child{{text-align:left}}th{{color:#8b949e;font-size:12px}}.cycle{{color:#79c0ff}}tbody tr{{cursor:pointer}}tbody tr:hover{{background:#1f2937}}.date-link{{color:#58a6ff;font-weight:700;text-decoration:none}}.rate{{font-weight:700}}.arrow,.back{{color:#8b949e}}.back{{text-decoration:none}}@media(max-width:720px){{.summary{{grid-template-columns:1fr}}.panel{{overflow-x:auto}}table{{min-width:720px}}}}
</style></head><body><header><a class="back" href="index.html">← ダッシュボード</a><h1>日次陽線候補 実績一覧</h1><div class="meta">同じ採点ルールによる予測と翌日の答え合わせを日付別に蓄積します。</div></header><main>
<section class="summary"><div class="stat"><span>予測記録</span><b>3日</b></div><div class="stat"><span>累計方向一致</span><b class="warning">{hits + prior_hits}/{len(candidate_known) + prior_count}</b></div><div class="stat"><span>累計一致率</span><b class="warning">{(hits + prior_hits) / (len(candidate_known) + prior_count) * 100:.1f}%</b></div></section>
<section class="panel"><table><thead><tr><th>予測日</th><th>本命</th><th>次点</th><th>監視</th><th>全体</th><th>周期＋候補</th><th>周期＋陽線</th><th>全台実陽線</th><th></th></tr></thead><tbody>
<tr data-href="prediction_{prediction_date}.html"><td><a class="date-link" href="prediction_{prediction_date}.html">{prediction_date[:4]}年{int(prediction_date[4:6])}月{int(prediction_date[6:])}日</a></td><td class="muted" colspan="4">実績待ち</td><td class="cycle"><span class="rate">{next_positive}/68</span>（{next_positive / 68 * 100:.1f}%）</td><td class="muted">実績待ち</td><td class="muted">実績待ち</td><td class="arrow">›</td></tr>
<tr data-href="prediction_{actual_date}.html"><td><a class="date-link" href="prediction_{actual_date}.html">{actual_date[:4]}年{int(actual_date[4:6])}月{int(actual_date[6:])}日</a></td><td>{rank_hits['本命']}/{rank_counts['本命']}</td><td>{rank_hits['次点']}/{rank_counts['次点']}</td><td>{rank_hits['監視']}/{rank_counts['監視']}</td><td class="warning">{hits}/{len(candidate_known)}（{hits / len(candidate_known) * 100:.1f}%）</td><td class="cycle">{len(positives)}/68</td><td class="cycle">{cycle_hits}/{len(positives_known)}（{cycle_hits / len(positives_known) * 100:.1f}%）</td><td>{all_hits}/{len(actual_known)}（{all_hits / len(actual_known) * 100:.1f}%）</td><td class="arrow">›</td></tr>
<tr data-href="prediction_20260613.html"><td><a class="date-link" href="prediction_20260613.html">2026年6月13日</a></td><td>4/8</td><td>1/2</td><td>1/3</td><td class="warning">6/13（46.2%）</td><td class="cycle">31/68</td><td class="cycle">18/31（58.1%）</td><td>33/68（48.5%）</td><td class="arrow">›</td></tr>
</tbody></table></section><section class="panel note">「周期＋候補」は主要5周期の翌日合成値がプラスだった台数です。標本数が少ない間は結論を急がず、同じ条件で記録を継続します。</section></main><script>document.querySelectorAll('tr[data-href]').forEach(row=>row.onclick=event=>{{if(!event.target.closest('a'))location.href=row.dataset.href}});</script></body></html>"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--actual-date", required=True)
    parser.add_argument("--prediction-date", required=True)
    args = parser.parse_args()
    actual_path = DOCS_DIR / f"prediction_{args.actual_date}.html"
    locked_rows, locked_cycles = parse_locked_forecasts(actual_path)
    day_actuals = actuals(args.actual_date)
    cutoff = (datetime.strptime(args.actual_date, "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d")
    actual_features = feature_rows(cutoff)
    actual_path.write_text(
        render_detail(args.actual_date, cutoff, locked_rows, locked_cycles, day_actuals, actual_features),
        encoding="utf-8",
    )
    features = feature_rows(args.actual_date)
    new_rows = sorted(features.values(), key=lambda row: row["score"], reverse=True)
    new_cycles = {machine: row["forecast"] for machine, row in features.items()}
    prediction_path = DOCS_DIR / f"prediction_{args.prediction_date}.html"
    prediction_path.write_text(
        render_detail(args.prediction_date, args.actual_date, new_rows, new_cycles, None, features), encoding="utf-8"
    )
    (DOCS_DIR / "prediction_top.html").write_text(
        render_top(
            args.actual_date, args.prediction_date, locked_rows, locked_cycles, day_actuals,
            sum(value > 0 for value in new_cycles.values()),
        ),
        encoding="utf-8",
    )
    candidates = [row for row in locked_rows if row.get("rank")]
    candidate_known = [row for row in candidates if day_actuals.get(row["machine"]) is not None]
    hits = sum(day_actuals[row["machine"]] > 0 for row in candidate_known)
    positives = [machine for machine, value in locked_cycles.items() if value > 0]
    positive_known = [machine for machine in positives if day_actuals.get(machine) is not None]
    cycle_hits = sum(day_actuals[machine] > 0 for machine in positive_known)
    actual_known = [value for value in day_actuals.values() if value is not None]
    print(f"actual={args.actual_date} candidates={hits}/{len(candidate_known)} cycle={cycle_hits}/{len(positive_known)} all={sum(v > 0 for v in actual_known)}/{len(actual_known)}")
    print(f"prediction={args.prediction_date} candidates=13")


if __name__ == "__main__":
    main()
