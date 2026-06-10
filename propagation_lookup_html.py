"""
伝播候補ルックアップHTMLを生成する。

HTMLは静的ファイルとして動き、台番入力だけで候補率を表示する。
候補率は未来予測ではなく、過去データ上の P(B点火 | A点火)。
"""

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
        for e in events:
            machine = z3(e["machine"])
            e["machine"] = machine
            machine_group[machine] = e["group"]
            machine_island[machine] = e["island"]
            fire_count[machine] += 1

        for ea in events:
            a = ea["machine"]
            a_fire[a] += 1
            for eb in events:
                b = eb["machine"]
                if a == b or ea["group"] != eb["group"]:
                    continue
                lag = eb["step"] - ea["step"]
                if 0 <= lag <= WINDOW_STEPS:
                    co[(a, b)] += 1

    total_obs_steps = n_steps * days
    by_source = defaultdict(list)
    for (a, b), count in co.items():
        if count < MIN_COUNT or not a_fire[a] or not total_obs_steps:
            continue
        p_cond = count / a_fire[a]
        p_base = min(1.0, (fire_count[b] / total_obs_steps) * (WINDOW_STEPS + 1))
        if p_base <= 0:
            continue
        lift = p_cond / p_base
        by_source[a].append(
            {
                "b": b,
                "group": machine_group.get(a, ""),
                "fromIsland": machine_island.get(a, ""),
                "toIsland": machine_island.get(b, ""),
                "pct": round(p_cond * 100, 1),
                "base": round(p_base * 100, 1),
                "lift": round(lift, 2),
                "count": count,
                "aFire": a_fire[a],
            }
        )

    for rows in by_source.values():
        rows.sort(key=lambda r: (r["pct"], r["lift"], r["count"]), reverse=True)
    return by_source


def html_escape_json(data):
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


def main():
    snaps = load_snaps()
    if not snaps:
        raise SystemExit("snapshotがありません。daily_ingest.py を先に実行してください。")

    dates = sorted(snaps.keys())
    by_source = build_rows(snaps)
    payload = {
        "meta": {
            "from": dates[0],
            "to": dates[-1],
            "days": len(dates),
            "windowMin": WINDOW_STEPS * 10,
            "minCount": MIN_COUNT,
            "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        },
        "pairs": by_source,
    }

    html = f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Propagation Lookup</title>
<style>
:root{{color-scheme:dark;--bg:#0b0f14;--panel:#121821;--line:#263241;--text:#d7dee8;--muted:#8c98a8;--blue:#60a5fa;--good:#22c55e;--warn:#f59e0b;--bad:#64748b}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--text);font-family:Meiryo,'Segoe UI',sans-serif;font-size:14px}}
header{{padding:18px 22px;border-bottom:1px solid var(--line);background:#0f141c}}
h1{{font-size:18px;margin:0 0 8px}}
.meta{{color:var(--muted);display:flex;gap:16px;flex-wrap:wrap;font-size:12px}}
main{{padding:18px 22px 34px;max-width:1180px}}
.controls{{display:grid;grid-template-columns:160px 150px 150px 120px 1fr;gap:10px;align-items:end;margin-bottom:16px}}
label{{display:grid;gap:5px;color:var(--muted);font-size:12px}}
input,select{{background:#0f1722;color:var(--text);border:1px solid var(--line);border-radius:6px;padding:9px 10px;font-size:14px}}
button{{background:#2563eb;color:white;border:0;border-radius:6px;padding:10px 14px;font-weight:700;cursor:pointer}}
.note{{color:var(--muted);line-height:1.7;margin:8px 0 16px}}
.cards{{display:grid;grid-template-columns:repeat(4,minmax(130px,1fr));gap:10px;margin:0 0 14px}}
.card{{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:10px}}
.card b{{display:block;font-size:18px;margin-top:4px}}
table{{width:100%;border-collapse:collapse;background:var(--panel);border:1px solid var(--line)}}
th,td{{border-bottom:1px solid var(--line);padding:8px 10px;text-align:right;white-space:nowrap}}
th:first-child,td:first-child{{text-align:left}}
th{{color:var(--muted);font-weight:600;background:#101722;position:sticky;top:0}}
.pill{{display:inline-block;border-radius:999px;padding:3px 8px;font-size:12px;font-weight:700}}
.strong{{background:#14532d;color:#86efac}}
.mid{{background:#422006;color:#fcd34d}}
.weak{{background:#1f2937;color:#cbd5e1}}
.empty{{padding:24px;background:var(--panel);border:1px solid var(--line);color:var(--muted)}}
@media(max-width:760px){{.controls{{grid-template-columns:1fr 1fr}}.cards{{grid-template-columns:1fr 1fr}}main{{padding:14px}}}}
</style>
</head>
<body>
<header>
  <h1>Propagation Lookup</h1>
  <div class="meta">
    <span>Range: {payload["meta"]["from"]} to {payload["meta"]["to"]}</span>
    <span>Days: {payload["meta"]["days"]}</span>
    <span>Window: {payload["meta"]["windowMin"]} min</span>
    <span>Generated: {payload["meta"]["generated"]}</span>
  </div>
</header>
<main>
  <div class="controls">
    <label>Current machine<input id="machine" inputmode="numeric" placeholder="39 or 039" value="39"></label>
    <label>Target low<input id="lo" inputmode="numeric" value="39"></label>
    <label>Target high<input id="hi" inputmode="numeric" value="77"></label>
    <label>Top<select id="top"><option>10</option><option selected>15</option><option>25</option><option>50</option></select></label>
    <button id="run">Show Candidates</button>
  </div>
  <p class="note">Percent is not a prediction. It is historical P(B fired within 30 minutes after A fired). Use percent, lift, and count together.</p>
  <div class="cards" id="cards"></div>
  <div id="result"></div>
</main>
<script>
const DATA = {html_escape_json(payload)};
const $ = (id) => document.getElementById(id);
function z3(v) {{
  const n = String(v || '').trim();
  return n.padStart(3, '0');
}}
function asNum(v, fallback) {{
  const n = Number(String(v || '').trim());
  return Number.isFinite(n) ? n : fallback;
}}
function labelFor(r) {{
  if (r.count >= 30 && r.lift >= 1.5 && r.pct >= 10) return ['Candidate', 'strong'];
  if (r.count >= 10 && r.lift >= 1.2) return ['Reference', 'mid'];
  return ['Low sample', 'weak'];
}}
function render() {{
  const src = z3($('machine').value);
  const lo = asNum($('lo').value, -Infinity);
  const hi = asNum($('hi').value, Infinity);
  const top = asNum($('top').value, 15);
  let rows = [...(DATA.pairs[src] || [])].filter(r => {{
    const b = Number(r.b);
    return b >= lo && b <= hi;
  }});
  rows.sort((a,b) => (b.pct-a.pct) || (b.lift-a.lift) || (b.count-a.count));
  const shown = rows.slice(0, top);
  const avg = rows.length ? rows.reduce((s,r)=>s+r.pct,0)/rows.length : 0;
  $('cards').innerHTML = `
    <div class="card">Source<b>${{Number(src)}}</b></div>
    <div class="card">Candidates<b>${{rows.length}}</b></div>
    <div class="card">Avg percent<b>${{avg.toFixed(1)}}%</b></div>
    <div class="card">A fires<b>${{rows[0]?.aFire ?? 0}}</b></div>`;
  if (!shown.length) {{
    $('result').innerHTML = `<div class="empty">No candidates. Try widening the target range or use another machine.</div>`;
    return;
  }}
  $('result').innerHTML = `<table>
    <thead><tr><th>Candidate B</th><th>G</th><th>Island</th><th>Percent</th><th>Base</th><th>Lift</th><th>Count</th><th>A fires</th><th>Label</th></tr></thead>
    <tbody>${{shown.map(r => {{
      const [name, cls] = labelFor(r);
      return `<tr><td>${{Number(r.b)}}</td><td>G${{r.group}}</td><td>${{r.fromIsland}}-&gt;${{r.toIsland}}</td><td>${{r.pct.toFixed(1)}}%</td><td>${{r.base.toFixed(1)}}%</td><td>${{r.lift.toFixed(2)}}</td><td>${{r.count}}</td><td>${{r.aFire}}</td><td><span class="pill ${{cls}}">${{name}}</span></td></tr>`;
    }}).join('')}}</tbody></table>`;
}}
$('run').addEventListener('click', render);
['machine','lo','hi','top'].forEach(id => $(id).addEventListener('keydown', e => {{ if (e.key === 'Enter') render(); }}));
render();
</script>
</body>
</html>
"""
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html, encoding="utf-8")
    print(OUT)
    print(f"sources={len(by_source)} days={len(dates)}")


if __name__ == "__main__":
    main()
