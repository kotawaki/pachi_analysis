"""
s3・s4の島図から複数の点火台を選べる伝播候補ルックアップHTMLを生成する。

候補率は未来予測ではなく、過去データ上の P(B点火 | A点火)。
"""

import argparse
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from propagation import extract_starts, load_snaps


ROOT = Path(__file__).parent
OUT = ROOT / "docs" / "propagation_lookup.html"
WINDOW_STEPS = 3
MIN_COUNT = 3


def z3(value):
    return str(value).strip().zfill(3)


def build_rows(snaps):
    co = defaultdict(int)
    a_fire = defaultdict(int)
    fire_count = defaultdict(int)
    machine_group = {}
    machine_island = {}
    n_steps = 0
    days = 0

    for snap in snaps.values():
        days += 1
        n_steps = len(snap["steps"])
        events = extract_starts(snap)
        for event in events:
            machine = z3(event["machine"])
            event["machine"] = machine
            machine_group[machine] = event["group"]
            machine_island[machine] = event["island"]
            fire_count[machine] += 1

        for source_event in events:
            source = source_event["machine"]
            a_fire[source] += 1
            for target_event in events:
                target = target_event["machine"]
                if source == target or source_event["group"] != target_event["group"]:
                    continue
                lag = target_event["step"] - source_event["step"]
                if 0 <= lag <= WINDOW_STEPS:
                    co[(source, target)] += 1

    total_obs_steps = n_steps * days
    by_source = defaultdict(list)
    for (source, target), count in co.items():
        if count < MIN_COUNT or not a_fire[source] or not total_obs_steps:
            continue
        p_cond = count / a_fire[source]
        p_base = min(
            1.0,
            (fire_count[target] / total_obs_steps) * (WINDOW_STEPS + 1),
        )
        if p_base <= 0:
            continue
        by_source[source].append(
            {
                "b": target,
                "group": machine_group.get(source, ""),
                "fromIsland": machine_island.get(source, ""),
                "toIsland": machine_island.get(target, ""),
                "pct": round(p_cond * 100, 1),
                "base": round(p_base * 100, 1),
                "lift": round(p_cond / p_base, 2),
                "count": count,
                "aFire": a_fire[source],
            }
        )

    for rows in by_source.values():
        rows.sort(key=lambda row: (row["pct"], row["lift"], row["count"]), reverse=True)
    return by_source


def html_escape_json(data):
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


def load_intraday_hits(target_date):
    path = ROOT / "data" / f"daytime_hits_{target_date}.json"
    if not path.exists():
        return {"date": target_date, "hits": [], "events": {}, "missing": True}
    data = json.loads(path.read_text(encoding="utf-8"))
    events = {
        str(int(machine)): values
        for machine, values in data.get("events", {}).items()
        if str(machine).strip().isdigit()
    }
    hits = data.get("hits") or events.keys()
    return {
        "date": data.get("date", path.stem.removeprefix("daytime_hits_")),
        "hits": sorted((int(machine) for machine in hits), key=int),
        "events": events,
        "missing": False,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--daytime-date",
        default=datetime.now().strftime("%Y%m%d"),
        help="Hマークに使う日中周期hit日付。未取得ならHは表示しない。",
    )
    args = parser.parse_args()

    snaps = load_snaps()
    if not snaps:
        raise SystemExit("snapshotがありません。daily_ingest.py を先に実行してください。")

    dates = sorted(snaps.keys())
    intraday = load_intraday_hits(args.daytime_date)
    intraday_label = f"{intraday['date']}（未取得）" if intraday.get("missing") else intraday["date"]
    payload = {
        "meta": {
            "from": dates[0],
            "to": dates[-1],
            "days": len(dates),
            "windowMin": WINDOW_STEPS * 10,
            "minCount": MIN_COUNT,
            "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        },
        "pairs": build_rows(snaps),
    }

    html = f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Propagation Lookup</title>
<style>
:root{{--bg:#07111d;--panel:#0d1b2a;--panel2:#10243a;--line:#28435d;--text:#edf6ff;--muted:#91a8bd;--source:#fb4b4b;--strong:#ffd43b;--mid:#38d9a9;--weak:#4dabf7}}
*{{box-sizing:border-box}}
body{{margin:0;background:radial-gradient(circle at 50% -20%,#173653 0,var(--bg) 48%);color:var(--text);font-family:Meiryo,'Segoe UI',sans-serif;font-size:14px;min-height:100vh}}
header{{padding:16px 20px;border-bottom:1px solid var(--line);background:rgba(7,17,29,.9)}}
h1{{font-size:20px;margin:0 0 6px;letter-spacing:.04em}}
.meta{{color:var(--muted);display:flex;gap:14px;flex-wrap:wrap;font-size:12px}}
main{{max-width:1120px;margin:auto;padding:18px 18px 40px}}
.guide{{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:14px}}
.guide strong{{font-size:16px}}
.guide p{{margin:4px 0 0;color:var(--muted)}}
button{{font:inherit}}
.clear{{border:1px solid var(--line);background:var(--panel);color:var(--text);border-radius:9px;padding:9px 14px;cursor:pointer}}
.layout{{display:grid;gap:18px;max-width:620px;margin:auto}}
.map-panel,.ranking{{background:rgba(13,27,42,.92);border:1px solid var(--line);border-radius:16px;padding:16px;box-shadow:0 18px 50px rgba(0,0,0,.2)}}
.islands{{display:grid;grid-template-columns:1fr 46px 1fr;align-items:start}}
.aisle{{height:100%;min-height:650px;display:grid;place-items:center;color:#52718d;font-size:11px;writing-mode:vertical-rl;letter-spacing:.35em}}
.island-title{{text-align:center;color:#b9d7ef;font-weight:800;margin-bottom:8px}}
.machine-list{{display:grid;gap:5px}}
.machine-row{{display:grid;grid-template-columns:24px 34px minmax(0,1fr);gap:4px}}
.chart-link{{display:grid;place-items:center;min-height:29px;border:1px solid #34546e;border-radius:6px;background:#091b2b;color:#80c7ff;text-decoration:none;font-size:13px;font-weight:800;transition:background .12s,border-color .12s,transform .12s}}
.chart-link:hover{{background:#173b58;border-color:#80c7ff;transform:scale(1.04)}}
.machine{{position:relative;width:100%;min-width:0;min-height:29px;border:1px solid #34546e;border-radius:6px;background:#11283d;color:#eaf6ff;cursor:pointer;font-weight:800;transition:transform .12s,background .12s,border-color .12s,box-shadow .12s}}
.machine-no{{font-weight:900}}
.machine:hover{{transform:scale(1.025);border-color:#80c7ff}}
.machine small{{position:absolute;right:7px;top:5px;color:#8fb0c9;font-size:10px;font-weight:500}}
.machine.source{{background:var(--source);border-color:#ff8787;color:white;box-shadow:0 0 0 2px rgba(251,75,75,.2),0 0 18px rgba(251,75,75,.35)}}
.machine.source small{{color:#fff}}
.machine.candidate-strong{{background:#6b5700;border-color:var(--strong);color:#fff4b8;box-shadow:0 0 12px rgba(255,212,59,.28)}}
.machine.candidate-mid{{background:#075c4a;border-color:var(--mid);color:#b7ffe9}}
.machine.candidate-weak{{background:#174e7b;border-color:var(--weak);color:#d5edff}}
.rank-badge{{position:absolute!important;left:5px;right:auto!important;top:4px!important;display:grid;place-items:center;width:19px;height:19px;border-radius:50%;background:#fff;color:#07111d!important;font-size:10px!important;font-weight:900!important}}
.rank-high{{background:#ef4444!important;color:#fff!important;box-shadow:0 0 10px rgba(239,68,68,.55)}}
.rank-mid{{background:#facc15!important;color:#2b2100!important;box-shadow:0 0 8px rgba(250,204,21,.35)}}
.rank-low{{background:#fff!important;color:#07111d!important}}
.legend{{display:flex;gap:12px;flex-wrap:wrap;margin-top:14px;color:var(--muted);font-size:11px}}
.dot{{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:4px}}
.selected-summary{{min-height:46px;padding:10px 12px;background:#091624;border-radius:10px;margin-bottom:12px;color:var(--muted)}}
.selected-summary b{{color:white}}
.ranking h2{{font-size:16px;margin:0 0 10px}}
.candidate-list{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}}
.candidate-card{{display:grid;grid-template-columns:42px 1fr auto;gap:9px;align-items:center;border:1px solid var(--line);background:var(--panel2);border-radius:10px;padding:9px}}
.candidate-card .no{{display:grid;place-items:center;width:32px;height:32px;margin:auto;border-radius:50%;font-size:18px;font-weight:900;text-align:center}}
.candidate-card b{{font-size:16px}}
.candidate-card p{{margin:2px 0 0;color:var(--muted);font-size:11px;line-height:1.45}}
.metrics{{text-align:right;font-size:12px}}
.metrics strong{{display:block;color:#fff;font-size:15px}}
.empty{{padding:28px 12px;text-align:center;color:var(--muted);border:1px dashed var(--line);border-radius:10px;line-height:1.8}}
.warning{{margin-top:12px;color:#829bb0;font-size:11px;line-height:1.6}}
.daily-badge{{display:grid;place-items:center;min-height:29px;border-radius:6px;font-size:11px;font-weight:900}}
.daily-empty{{background:transparent}}
.daily-hit{{background:#d29922;color:#fff}}
@media(max-width:760px){{main{{padding:12px}}.map-panel{{padding:12px}}.aisle{{min-height:620px}}.guide{{align-items:flex-start}}.candidate-list{{grid-template-columns:1fr}}}}
</style>
</head>
<body>
<header>
  <h1>Propagation Lookup</h1>
  <div class="meta">
    <span>{payload["meta"]["from"]} - {payload["meta"]["to"]}</span>
    <span>{payload["meta"]["days"]}日分</span>
    <span>伝播窓 {payload["meta"]["windowMin"]}分</span>
    <span>生成 {payload["meta"]["generated"]}</span>
    <span>日中周期hit {intraday_label}</span>
  </div>
</header>
<main>
  <div class="guide">
    <div><strong>いま当たっている台をタップ</strong><p>複数選択できます。もう一度タップすると解除します。</p></div>
    <button class="clear" id="clear">すべて解除</button>
  </div>
  <div class="layout">
    <section class="map-panel">
      <div class="islands">
        <div><div class="island-title">s4</div><div class="machine-list" id="s4"></div></div>
        <div class="aisle">通路</div>
        <div><div class="island-title">s3</div><div class="machine-list" id="s3"></div></div>
      </div>
      <div class="legend">
        <span><i class="dot" style="background:var(--source)"></i>当たり中</span>
        <span><i class="dot" style="background:var(--strong)"></i>候補</span>
        <span><i class="dot" style="background:var(--mid)"></i>参考</span>
        <span><i class="dot" style="background:var(--weak)"></i>件数不足</span>
      </div>
    </section>
    <aside class="ranking">
      <h2>伝播候補ランキング</h2>
      <div class="selected-summary" id="summary"></div>
      <div class="candidate-list" id="ranking"></div>
      <p class="warning">これは未来予測ではなく、過去の P(B点火｜A点火) です。候補率・lift・countを合わせて確認してください。count&lt;10は統計的に弱い参考表示です。</p>
    </aside>
  </div>
</main>
<script>
const DATA = {html_escape_json(payload)};
const selected = new Set();
const DAILY_HITS = new Set({html_escape_json(intraday["hits"])});
const HIT_EVENTS = {html_escape_json(intraday["events"])};
const machines = [
  ...Array.from({{length: 19}}, (_, i) => 77 - i),
  ...Array.from({{length: 19}}, (_, i) => 39 + i)
];
function z3(value) {{ return String(value).padStart(3, '0'); }}
function hitText(machine) {{
  const values = HIT_EVENTS[String(machine)] || [];
  return values.map(v => `${{String(Math.floor(v/60)).padStart(2,'0')}}:${{String(v%60).padStart(2,'0')}}`).join(' / ');
}}
function level(row) {{
  if (row.count >= 30 && row.lift >= 1.5 && row.pct >= 10) return 'strong';
  if (row.count >= 10 && row.lift >= 1.2) return 'mid';
  return 'weak';
}}
function rankClass(rank) {{
  if (rank <= 3) return 'rank-high';
  if (rank <= 6) return 'rank-mid';
  return 'rank-low';
}}
function buildIsland(id, values) {{
  document.getElementById(id).innerHTML = values.map(machine =>
    `<div class="machine-row"><span class="daily-badge ${{DAILY_HITS.has(machine) ? 'daily-hit' : 'daily-empty'}}">${{DAILY_HITS.has(machine) ? 'H' : ''}}</span><a class="chart-link" href="ohlc.html?machine=${{machine}}" aria-label="${{machine}}番台のチャートを表示" title="チャートを表示">↗</a><button class="machine" data-machine="${{machine}}" aria-pressed="false" title="${{DAILY_HITS.has(machine) ? '日中hit '+hitText(machine) : '日中hitなし'}}"><span class="machine-no">${{machine}}</span><small>G${{((machine - 1) % 9) + 1}}</small></button></div>`
  ).join('');
}}
function candidateRows() {{
  const grouped = new Map();
  for (const source of selected) {{
    for (const row of DATA.pairs[z3(source)] || []) {{
      const target = Number(row.b);
      if (target < 39 || target > 77 || selected.has(target)) continue;
      if (!grouped.has(target)) grouped.set(target, []);
      grouped.get(target).push({{...row, source}});
    }}
  }}
  return [...grouped.entries()].map(([machine, evidence]) => {{
    evidence.sort((a, b) => (b.pct-a.pct) || (b.lift-a.lift) || (b.count-a.count));
    return {{machine, evidence, best:evidence[0], supporters:evidence.length}};
  }}).sort((a, b) =>
    (b.supporters-a.supporters) || (b.best.pct-a.best.pct) ||
    (b.best.lift-a.best.lift) || (b.best.count-a.best.count)
  );
}}
function render() {{
  const rows = candidateRows();
  const rankByMachine = new Map(rows.slice(0, 9).map((row, i) => [row.machine, i + 1]));
  const rowByMachine = new Map(rows.map(row => [row.machine, row]));
  document.querySelectorAll('.machine').forEach(button => {{
    const machine = Number(button.dataset.machine);
    button.className = 'machine';
    button.setAttribute('aria-pressed', String(selected.has(machine)));
    button.querySelector('.rank-badge')?.remove();
    if (selected.has(machine)) {{
      button.classList.add('source');
      return;
    }}
    const row = rowByMachine.get(machine);
    if (!row) return;
    button.classList.add(`candidate-${{level(row.best)}}`);
    const rank = rankByMachine.get(machine);
    if (rank) button.insertAdjacentHTML('afterbegin', `<small class="rank-badge ${{rankClass(rank)}}">${{rank}}</small>`);
  }});
  const sources = [...selected].sort((a,b)=>a-b);
  document.getElementById('summary').innerHTML = sources.length
    ? `当たり中 <b>${{sources.join('・')}}</b><br>候補 ${{rows.length}}台（複数元からの支持を優先表示）`
    : '島図から、いま当たっている台を選んでください。';
  const ranking = document.getElementById('ranking');
  if (!sources.length) {{
    ranking.innerHTML = '<div class="empty">台をタップすると<br>伝播候補が島図上で光ります</div>';
    return;
  }}
  if (!rows.length) {{
    ranking.innerHTML = '<div class="empty">条件に合う候補がありません</div>';
    return;
  }}
  ranking.innerHTML = rows.slice(0, 15).map((row, index) => {{
    const best = row.best;
    const sourcesText = row.evidence.map(item => item.source).join('・');
    return `<div class="candidate-card">
      <div class="no ${{rankClass(index + 1)}}">${{index + 1}}</div>
      <div><b>${{row.machine}}番台</b><p>${{sourcesText}}番台から支持（${{row.supporters}}台）<br>最強ペア: ${{best.source}} → ${{row.machine}}</p></div>
      <div class="metrics"><strong>${{best.pct.toFixed(1)}}%</strong>lift ${{best.lift.toFixed(2)}}<br>count ${{best.count}}</div>
    </div>`;
  }}).join('');
}}
buildIsland('s4', Array.from({{length: 20}}, (_, i) => 77 - i));
buildIsland('s3', Array.from({{length: 19}}, (_, i) => 39 + i));
document.querySelectorAll('.machine').forEach(button => button.addEventListener('click', () => {{
  const machine = Number(button.dataset.machine);
  selected.has(machine) ? selected.delete(machine) : selected.add(machine);
  render();
}}));
document.getElementById('clear').addEventListener('click', () => {{ selected.clear(); render(); }});
render();
</script>
</body>
</html>
"""
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html, encoding="utf-8")
    print(OUT)
    print(f"sources={len(payload['pairs'])} days={len(dates)}")


if __name__ == "__main__":
    main()
