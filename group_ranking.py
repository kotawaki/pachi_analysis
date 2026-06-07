"""
group_ranking.py
================
日次「グループ内クラスタリング強度(z値)」を算出し、
その日ごとの強さランキングの推移を可視化するHTMLを生成する。

  - z値: 各台の点火時刻を円環シフトしたヌルに対する観測クラスタリング量の標準化スコア
          (regime_analysis.py と同じ指標を1日単位で計算)
  - 出力: docs/groups.html
      * バンプチャート: 各グループの日次ランク(1=最強)の推移
      * ヒートマップ: グループ×日 を日次ランクで色分け

注意: z値の絶対量はホール全体の時間帯リズムにも影響される。
      ここで見せるのは「その日どのグループが相対的に固まっていたか」の推移であり、
      予測力を主張するものではない(検証結果は reports/NEGATIVE_propagation_predictability.md 参照)。

使い方:
  python group_ranking.py --window 3 --shuffles 200
"""

import os, sys, json, argparse
import regime_analysis as ra

OUT_DIR  = 'docs'
OUT_FILE = os.path.join(OUT_DIR, 'groups.html')
MIN_FIRES_DAY = 8   # 1日でこの点火数未満のグループは z=null


def day_group_z(day_fires, W, K):
    total = sum(len(fl) for fl in day_fires)
    if total < MIN_FIRES_DAY:
        return None
    M_obs = ra.metric(ra.cnt_from_fires(day_fires, shift=False), W)
    nulls = [ra.metric(ra.cnt_from_fires(day_fires, shift=True), W) for _ in range(K)]
    mean = sum(nulls) / K
    std = (sum((x - mean) ** 2 for x in nulls) / K) ** 0.5
    if std == 0:
        return None
    return round((M_obs - mean) / std, 2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", type=int, default=3)
    ap.add_argument("--shuffles", type=int, default=200)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    import random; random.seed(args.seed)

    snaps = ra.prop.load_snaps()
    if not snaps:
        print("⚠ スナップショットがありません"); sys.exit(1)
    dates = sorted(snaps.keys())
    dgf = ra.build_day_group_fires(snaps)

    print(f"日次z値を計算中... ({len(dates)}日 × {len(ra.GROUPS)}グループ × {args.shuffles}シャッフル)")
    zser = {g: [] for g in ra.GROUPS}
    for date in dates:
        for g in ra.GROUPS:
            zser[g].append(day_group_z(dgf[date].get(g, []), args.window, args.shuffles))
    print("  完了")

    iso = [f"{d[:4]}-{d[4:6]}-{d[6:8]}" for d in dates]
    payload = json.dumps({"dates": iso, "z": zser}, ensure_ascii=False, separators=(',', ':'))

    os.makedirs(OUT_DIR, exist_ok=True)
    html = HTML.replace('__DATA__', payload)
    with open(OUT_FILE, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"\n✅ 生成完了: {OUT_FILE}  ({os.path.getsize(OUT_FILE)/1024:.0f} KB)")
    print(f"   GitHub Pages: https://kotawaki.github.io/pachi_analysis/groups.html")


HTML = r"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>グループ強さランキング推移</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0d1117;color:#c9d1d9;font-family:'Segoe UI',Meiryo,sans-serif;font-size:13px;padding:14px}
h1{font-size:15px;color:#58a6ff;margin-bottom:4px}
.sub{color:#8b949e;font-size:11px;margin-bottom:12px;line-height:1.5}
.controls{display:flex;gap:14px;align-items:center;margin-bottom:12px;flex-wrap:wrap}
.controls label{color:#8b949e}
select,button{background:#161b22;color:#c9d1d9;border:1px solid #30363d;border-radius:6px;padding:5px 9px;font-size:12px;cursor:pointer}
button.active{background:#1f6feb;border-color:#1f6feb;color:#fff}
.card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:12px;margin-bottom:16px}
.card h2{font-size:12px;color:#58a6ff;margin-bottom:8px;text-transform:uppercase;letter-spacing:.5px}
#bumpWrap{position:relative;height:360px}
#heat{overflow-x:auto}
table{border-collapse:collapse;font-size:10px}
th,td{text-align:center;padding:0;border:1px solid #0d1117}
th{color:#8b949e;font-weight:600;padding:2px 3px;white-space:nowrap}
td.gl{color:#c9d1d9;font-weight:700;padding:3px 7px;background:#161b22;position:sticky;left:0}
td.cell{width:15px;height:20px;color:#0d1117;font-weight:700}
.legend{display:flex;gap:10px;align-items:center;margin-top:8px;font-size:11px;color:#8b949e;flex-wrap:wrap}
.lg{display:inline-flex;align-items:center;gap:4px}
.sw{width:14px;height:14px;border-radius:3px;display:inline-block}
.note{color:#6e7681;font-size:10px;margin-top:6px}
</style>
</head>
<body>
<div style="margin-bottom:10px;font-size:12px">
  <a href="index.html" style="color:#8b949e;text-decoration:none">🏠 トップ</a> &nbsp;｜&nbsp;
  <a href="ohlc.html" style="color:#58a6ff;text-decoration:none">🎰 差玉チャート(OHLC)</a>
</div>
<h1>🏆 グループ強さランキング推移</h1>
<div class="sub">
  各グループの「グループ内クラスタリング強度(z値)」の日次ランク(1=その日いちばん引っ張り合っていた)。<br>
  ※相対的な強さの推移を見るためのもの。翌日の強グループを予測できるという意味ではない（検証結果はネガティブ）。
</div>

<div class="controls">
  <label>平滑化(①の折れ線のみ):</label>
  <button data-roll="1" class="active">なし</button>
  <button data-roll="3">3日</button>
  <button data-roll="7">7日</button>
  <span style="margin-left:14px;color:#8b949e">表示:</span>
  <button data-view="rank" class="active">ランク</button>
  <button data-view="z">z値</button>
</div>

<div class="card">
  <h2>① ランク推移（バンプチャート）</h2>
  <div id="bumpWrap"><canvas id="bump"></canvas></div>
</div>

<div class="card">
  <h2>② ヒートマップ（日次ランク・生データ）</h2>
  <div id="heat"></div>
  <div class="legend">
    <span class="lg"><span class="sw" style="background:#f85149"></span>1位(強)</span>
    <span class="lg"><span class="sw" style="background:#d29922"></span>中位</span>
    <span class="lg"><span class="sw" style="background:#2f81f7"></span>9位(弱)</span>
    <span class="lg"><span class="sw" style="background:#30363d"></span>データ不足</span>
  </div>
  <div class="note">
    セル内数字 = その日の順位。横スクロールで全期間。<br>
    ⚠ <b>このヒートマップは常に生データ（平滑化なし）で表示</b>します（上の平滑化ボタンは①の折れ線だけに効きます）。
    7日移動平均をかけると強グループが「数日維持」して見えますが、それは<b>移動平均が隣接日のデータを共有することで生じる見せかけ</b>です
    （持続性ゼロのデータでも同じ縞模様が出ることを検証済み）。実際の日次トップはほぼ毎日入れ替わり、翌日の予測には使えません。
  </div>
</div>

<script>
const DATA = __DATA__;
const GROUPS = ["1","2","3","4","5","6","7","8","9"];
const GCOLOR = {
  "1":"#f85149","2":"#db61a2","3":"#d29922","4":"#3fb950","5":"#2ea043",
  "6":"#1f6feb","7":"#58a6ff","8":"#a371f7","9":"#ff7b72"
};
let roll = 1, view = "rank";

// 移動平均(null無視)
function smooth(arr, w){
  if (w<=1) return arr.slice();
  const out = [];
  for (let i=0;i<arr.length;i++){
    let s=0,c=0;
    for (let j=Math.max(0,i-w+1);j<=i;j++){ if(arr[j]!=null){s+=arr[j];c++;} }
    out.push(c? s/c : null);
  }
  return out;
}
// 各日でランク付け(1=最大z)
function ranksByDay(zsm){
  const n = DATA.dates.length;
  const rank = {}; GROUPS.forEach(g=>rank[g]=new Array(n).fill(null));
  for (let i=0;i<n;i++){
    const row = GROUPS.map(g=>({g, z: zsm[g][i]})).filter(o=>o.z!=null);
    row.sort((a,b)=>b.z-a.z);
    row.forEach((o,idx)=>rank[o.g][i]=idx+1);
  }
  return rank;
}

let chart=null;
function render(){
  const zsm = {}; GROUPS.forEach(g=>zsm[g]=smooth(DATA.z[g], roll));
  const rank = ranksByDay(zsm);          // バンプチャート用(平滑化を反映)
  const rankRaw = ranksByDay(DATA.z);    // ヒートマップ用(常に生データ=事実)

  // バンプチャート
  const datasets = GROUPS.map(g=>({
    label: "G"+g,
    data: (view==="rank"? rank[g] : zsm[g]),
    borderColor: GCOLOR[g], backgroundColor: GCOLOR[g],
    borderWidth: 2, pointRadius: 2, pointHoverRadius: 5,
    tension: 0.3, spanGaps: true
  }));
  if (chart) chart.destroy();
  chart = new Chart(document.getElementById('bump'), {
    type:'line',
    data:{ labels: DATA.dates.map(d=>d.slice(5)), datasets },
    options:{
      responsive:true, maintainAspectRatio:false,
      interaction:{mode:'nearest',intersect:false},
      plugins:{ legend:{labels:{color:'#c9d1d9',boxWidth:14,font:{size:11}}},
        tooltip:{callbacks:{label:c=>` G${c.dataset.label.slice(1)}: ${view==='rank'?(c.parsed.y+'位'):c.parsed.y.toFixed(1)}`}}},
      scales:{
        x:{ ticks:{color:'#8b949e',maxTicksLimit:16,font:{size:10}}, grid:{color:'#1c2128'}},
        y: view==='rank'
            ? { reverse:true, min:1, max:9, ticks:{color:'#8b949e',stepSize:1,callback:v=>v+'位'}, grid:{color:'#1c2128'}}
            : { ticks:{color:'#8b949e'}, grid:{color:'#1c2128'}, title:{display:true,text:'z値',color:'#8b949e'}}
      }
    }
  });

  // ヒートマップ
  const heatColor = r => {
    if (r==null) return '#30363d';
    const t=(r-1)/8;                          // 0(1位)→1(9位)
    const hue = 0 + t*210;                     // 赤→青
    return `hsl(${hue},65%,${48-t*8}%)`;
  };
  let html='<table><thead><tr><th>G\\日</th>';
  DATA.dates.forEach(d=>html+=`<th>${d.slice(5).replace('-','/')}</th>`);
  html+='</tr></thead><tbody>';
  GROUPS.forEach(g=>{
    html+=`<tr><td class="gl" style="color:${GCOLOR[g]}">G${g}</td>`;
    rankRaw[g].forEach(r=>{
      html+=`<td class="cell" style="background:${heatColor(r)}">${r!=null?r:''}</td>`;
    });
    html+='</tr>';
  });
  html+='</tbody></table>';
  document.getElementById('heat').innerHTML=html;
}

document.querySelectorAll('[data-roll]').forEach(b=>b.addEventListener('click',()=>{
  document.querySelectorAll('[data-roll]').forEach(x=>x.classList.remove('active'));
  b.classList.add('active'); roll=+b.dataset.roll; render();
}));
document.querySelectorAll('[data-view]').forEach(b=>b.addEventListener('click',()=>{
  document.querySelectorAll('[data-view]').forEach(x=>x.classList.remove('active'));
  b.classList.add('active'); view=b.dataset.view; render();
}));
render();
</script>
</body>
</html>"""


if __name__ == "__main__":
    main()
