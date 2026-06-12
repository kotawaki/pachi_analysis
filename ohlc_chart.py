"""
日足OHLCチャート生成 (HTML / GitHub Pages 対応)
MA5/20/75 + フィボナッチ + ゴールデンクロス(MA5が75→20の順) + スイングH/L + R/R
"""

import csv, os, glob, json
from collections import defaultdict

CSV_DIR   = 'csv/analyze'
OUT_DIR   = 'docs'          # GitHub Pages は docs/ フォルダを認識する
OUT_FILE  = os.path.join(OUT_DIR, 'ohlc.html')   # トップ(index.html)から選択する1ページ

RANGES = [
    {'id':'r35_38',   'label':'35〜38番',  'machines':list(range(35,39)),   'start':'20250604'},
    {'id':'r39_77',   'label':'39〜77番',  'machines':list(range(39,78)),   'start':'20250517'},
    {'id':'r118_123', 'label':'118〜123番','machines':list(range(118,124)), 'start':'20251210'},
    {'id':'r148_153', 'label':'148〜153番','machines':list(range(148,154)), 'start':'20251114'},
    {'id':'r154_158', 'label':'154〜158番','machines':list(range(154,159)), 'start':'20260428'},
    {'id':'r1173_1180','label':'1173〜1180番','machines':list(range(1173,1181)),'start':'20260301'},
]

# -------------------------------------------------------
# CSV → 台別・日別 OHLC
# -------------------------------------------------------
def load_ohlc(csv_dir):
    sess = defaultdict(lambda: defaultdict(list))
    for path in sorted(glob.glob(os.path.join(csv_dir,'*','*_analyze.csv'))):
        date = os.path.basename(os.path.dirname(path))
        with open(path, encoding='utf-8-sig') as f:
            for row in csv.DictReader(f):
                kind = row.get('種別','')
                # ※「稼働なし」も当日の差玉位置情報として使う(当日の最終差玉を正確に取るため)。
                #   伝播分析では当たり判定から除外するが、OHLCではスランプ位置の記録として有効。
                if not kind.strip():
                    continue
                m = row.get('Machine', row.get('machine','')).strip()
                if not m:
                    continue
                m = m.zfill(3)
                try:
                    sb = int(row.get('開始差玉', 0) or 0)
                    eb = int(row.get('終了差玉', 0) or 0)
                    st = (row.get('開始時刻','') or '00:00').strip() or '00:00'
                    et = (row.get('終了時刻','') or st).strip() or st
                    sess[m][date].append((st, et, sb, eb))
                except:
                    pass
    # 台別・日別の「当日メトリクス」を作る
    #   net = 当日収支(その日の最終差玉。スランプグラフは毎朝0スタート)
    #   day_high / day_low = 当日の差玉レンジ(0始点を必ず含む)
    daily = {}
    for m, days in sess.items():
        daily[m] = {}
        for date, sl in days.items():
            sl.sort()                                  # 開始時刻順
            last = max(sl, key=lambda x: x[1])         # 終了時刻が最も遅い行
            net = last[3]                              # 当日の最終終了差玉 = 当日収支(0基準)
            pts = [0]                                  # 当日は必ず差玉0からスタート
            for st, et, a, b in sl:
                pts.append(a); pts.append(b)
            daily[m][date] = (net, max(pts), min(pts))
    return daily

def iso(d):
    return f"{d[:4]}-{d[4:6]}-{d[6:8]}"

def build_series(daily, machine_nums, start_date):
    """
    累積スランプ・ローソク足を生成。
      open  = 前日までの累積差玉（窓なし＝連続）
      close = open + 当日収支（net）
      → 色は「当日が勝ちか負けか」を正しく表す（前日比ではない）
      high/low = 当日レンジを累積値に乗せたもの
    """
    ms = [str(m).zfill(3) for m in machine_nums]
    dates = sorted({d for m in ms if m in daily for d in daily[m] if d >= start_date})
    result = []
    cum = 0                                  # 累積差玉
    for date in dates:
        active = [m for m in ms if m in daily and date in daily[m]]
        if not active:
            continue
        net = sum(daily[m][date][0] for m in active)   # 当日収支の合計
        dh  = sum(daily[m][date][1] for m in active)   # 当日高値(0基準)の合計
        dl  = sum(daily[m][date][2] for m in active)   # 当日安値(0基準)の合計
        o = cum
        c = cum + net
        h = max(cum + dh, o, c)
        l = min(cum + dl, o, c)
        result.append({'time': iso(date), 'open':o, 'high':h, 'low':l, 'close':c})
        cum = c                              # 翌日のopenに引き継ぐ（窓なし）
    return result

print("CSV読み込み中...")
ohlc_data = load_ohlc(CSV_DIR)
print(f"  {len(ohlc_data)} 台のデータ取得完了")

os.makedirs(OUT_DIR, exist_ok=True)

chart_data  = {}
ranges_info = []
for r in RANGES:
    rid, label, machines, start = r['id'], r['label'], r['machines'], r['start']
    agg = build_series(ohlc_data, machines, start)
    ind = {}
    for m in machines:
        s = build_series(ohlc_data, [m], start)
        if s:
            ind[str(m)] = s
    chart_data[rid] = {'label': label, 'aggregate': agg, 'machines': ind}
    ranges_info.append({'id': rid, 'label': label})
    print(f"  {label}: 全体{len(agg)}日 / 個別{len(ind)}台")

json_data   = json.dumps(chart_data,  ensure_ascii=False, separators=(',',':'))
ranges_json = json.dumps(ranges_info, ensure_ascii=False)

# -------------------------------------------------------
# HTML テンプレート
# -------------------------------------------------------
HTML = r"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>パチンコ 差玉チャート</title>
<script src="https://unpkg.com/lightweight-charts@4.1.7/dist/lightweight-charts.standalone.production.js"></script>
<style>
*{margin:0;padding:0;box-sizing:border-box}
html,body{height:100%;overflow:hidden}
body{background:#0d1117;color:#c9d1d9;font-family:'Segoe UI',Meiryo,sans-serif;font-size:13px;display:flex;flex-direction:column}
#header{background:#161b22;border-bottom:1px solid #30363d;padding:8px 16px;flex-shrink:0}
#header h1{font-size:13px;color:#58a6ff;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#tabs{display:flex;background:#161b22;border-bottom:1px solid #30363d;padding:0 8px;gap:2px;overflow-x:auto;flex-shrink:0}
.tab{padding:7px 13px;cursor:pointer;border-bottom:2px solid transparent;color:#8b949e;white-space:nowrap;transition:.15s;user-select:none;font-size:12px}
.tab.active{color:#58a6ff;border-bottom-color:#58a6ff}
.tab:hover{color:#c9d1d9}
#sub-tabs{display:flex;background:#0d1117;padding:5px 8px;gap:4px;overflow-x:auto;flex-shrink:0;border-bottom:1px solid #1c2128;min-height:32px;align-items:center}
.sub-tab{padding:3px 9px;cursor:pointer;border:1px solid #30363d;border-radius:10px;color:#8b949e;font-size:11px;transition:.15s;white-space:nowrap;user-select:none}
.sub-tab.active{background:#1f6feb;border-color:#1f6feb;color:#fff}
.sub-tab:hover{border-color:#58a6ff;color:#c9d1d9}
.sub-tab.fib-blue{color:#79c0ff;border-color:#1f6feb;background:#0d2742}
.sub-tab.fib-yellow{color:#ffd33d;border-color:#9e7b05;background:#2d2608}
.sub-tab.fib-red{color:#ff7b72;border-color:#da3633;background:#3b1214}
.sub-tab.fib-green{color:#56d364;border-color:#238636;background:#102d18}
.sub-tab.provisional{color:#7ee787;border-color:#56d364;background:#153b23;box-shadow:inset 0 0 0 1px #56d36455}
.sub-tab.fib-blue.active{color:#fff;background:#1f6feb}.sub-tab.fib-yellow.active{color:#161b22;background:#ffd33d}.sub-tab.fib-red.active{color:#fff;background:#da3633}.sub-tab.fib-green.active{color:#fff;background:#238636}
.sub-tab.provisional.active{color:#0d1117;background:#7ee787;border-color:#7ee787}
#analysis-controls{display:flex;gap:16px;align-items:center;background:#0d1117;border-bottom:1px solid #30363d;padding:7px 10px;flex-shrink:0;min-height:36px}
.analysis-toggle{display:flex;align-items:center;gap:7px;cursor:pointer;white-space:nowrap;font-size:12px;color:#c9d1d9}
.analysis-toggle input{margin:0;width:15px;height:15px}
#fourier-controls{display:flex;gap:7px 14px;align-items:center;flex-wrap:wrap;background:#161b22;border-bottom:1px solid #30363d;padding:6px 10px;flex-shrink:0;min-height:34px}
.fourier-toggle{display:flex;align-items:center;gap:6px;cursor:pointer;white-space:nowrap;font-size:11px;color:#c9d1d9}
.fourier-toggle input{margin:0}.fourier-swatch{width:17px;height:3px;display:inline-block}.fourier-empty{color:#6e7681;font-size:11px}
#main{display:flex;flex:1;min-height:0}
#chart-container{flex:1;min-width:0;position:relative}
.fib-line-label{position:absolute;z-index:8;pointer-events:none;transform:translateY(-50%);padding:2px 5px;border-radius:3px;background:#161b22e6;border:1px solid currentColor;font-size:10px;font-weight:700;white-space:nowrap;line-height:1.2}
#info-panel{width:215px;background:#161b22;border-left:1px solid #30363d;padding:9px 11px;overflow-y:auto;flex-shrink:0;font-size:11px}
.sec{margin-bottom:12px}
.sec-title{color:#58a6ff;font-size:10px;font-weight:700;margin-bottom:5px;text-transform:uppercase;letter-spacing:.5px;border-bottom:1px solid #21262d;padding-bottom:2px}
.row{display:flex;justify-content:space-between;align-items:center;margin:3px 0}
.lbl{color:#8b949e;flex-shrink:0}
.val{color:#c9d1d9;font-weight:600;text-align:right;word-break:break-all}
.badge{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:700}
.gc-on{background:#1a472a;color:#56d364;border:1px solid #56d364}
.gc-off{background:#21262d;color:#6e7681;border:1px solid #30363d}
.rr-g{color:#56d364 !important}
.rr-y{color:#e3b341 !important}
.rr-r{color:#f85149 !important}
.frow{display:flex;justify-content:space-between;margin:2px 0;font-size:11px}
.flbl{color:#8b949e}
.fext{color:#e91e63}
.fval{color:#c9d1d9}
.no-data{display:flex;align-items:center;justify-content:center;height:100%;color:#8b949e;flex-direction:column;gap:8px;font-size:14px}
.ma5c{color:#ffeb3b}
.ma20c{color:#42a5f5}
.ma75c{color:#ff7043}
@media(max-width:720px){
  html,body{height:auto;min-height:100%;overflow-x:hidden;overflow-y:auto}
  body{display:block;font-size:12px}
  #header{padding:7px 9px}
  #header h1{font-size:11px}
  #tabs,#sub-tabs,#analysis-controls,#fourier-controls{overflow-x:auto;flex-wrap:nowrap;scrollbar-width:thin}
  #tabs{padding:0 4px}.tab{padding:7px 10px}
  #sub-tabs{padding:5px 6px}.sub-tab{padding:4px 9px}
  #analysis-controls{padding:7px 8px;gap:14px}
  #fourier-controls{padding:6px 8px;min-height:33px}
  #main{display:flex;flex-direction:column;min-height:0}
  #chart-container{width:100%;height:58vh;min-height:430px;max-height:620px;flex:none;border-bottom:1px solid #30363d}
  #info-panel{width:100%;padding:0 10px 18px;border-left:0;border-top:1px solid #30363d;overflow:visible;font-size:12px}
  #info-panel .sec{margin:0;padding:12px 2px;border-bottom:1px solid #30363d}
  #info-panel .sec-title{font-size:11px;margin-bottom:8px}
  #info-panel .row{margin:6px 0}
  .fib-line-label{font-size:9px;padding:2px 4px}
}
</style>
</head>
<body>
<div id="header">
  <h1><a href="index.html" style="color:#8b949e;text-decoration:none">🏠 トップ</a> &nbsp;｜&nbsp; <a href="groups.html" style="color:#58a6ff;text-decoration:none">🏆 グループ強さランキング</a> &nbsp;｜&nbsp; 🎰 差玉チャート &nbsp;/&nbsp; 日足OHLC &nbsp;/&nbsp; MA5・20・75 &nbsp;/&nbsp; Fibonacci &nbsp;/&nbsp; Golden Cross (75→20) &nbsp;/&nbsp; Swing H/L &nbsp;/&nbsp; R/R</h1>
</div>
<div id="tabs"></div>
<div id="sub-tabs"></div>
<div id="analysis-controls">
  <label class="analysis-toggle"><input id="fib-local-toggle" type="checkbox"><span id="fib-local-label">SL2→SH2</span></label>
  <label class="analysis-toggle"><input id="fib-global-toggle" type="checkbox"><span>全期間フィボ</span></label>
</div>
<div id="fourier-controls"></div>
<div id="main">
  <div id="chart-container"></div>
  <div id="info-panel">
    <div class="sec">
      <div class="sec-title">Swing High / Low</div>
      <div class="row"><span class="lbl" style="color:#ef5350">▲ SH1</span><span class="val" id="sh1">-</span></div>
      <div class="row"><span class="lbl" style="color:#ef535099">▲ SH2</span><span class="val" id="sh2">-</span></div>
      <div class="row"><span class="lbl" style="color:#26a69a">▼ SL1</span><span class="val" id="sl1">-</span></div>
      <div class="row"><span class="lbl" style="color:#26a69a99">▼ SL2</span><span class="val" id="sl2">-</span></div>
    </div>
    <div class="sec">
      <div class="sec-title">Risk / Reward</div>
      <div class="row"><span class="lbl">エントリー</span><span class="val" id="rr-entry">-</span></div>
      <div class="row"><span class="lbl">ストップ(SL2)</span><span class="val" id="rr-stop">-</span></div>
      <div class="row"><span class="lbl">目標(Fib1.618)</span><span class="val" id="rr-target">-</span></div>
      <div class="row"><span class="lbl">R/R 比率</span><span class="val" id="rr-ratio">-</span></div>
    </div>
    <div class="sec">
      <div class="sec-title">Fibonacci</div>
      <div id="fib-panel"><span class="flbl">上昇構造の成立時に表示</span></div>
    </div>
    <div class="sec">
      <div class="sec-title">MA 最新値</div>
      <div class="row"><span class="lbl ma5c">■ MA5</span><span class="val" id="ma5v">-</span></div>
      <div class="row"><span class="lbl ma20c">■ MA20</span><span class="val" id="ma20v">-</span></div>
      <div class="row"><span class="lbl ma75c">■ MA75</span><span class="val" id="ma75v">-</span></div>
    </div>
    <div class="sec">
      <div class="sec-title">Golden Cross</div>
      <div id="gc-badge" class="badge gc-off" style="margin-bottom:5px">未検出</div>
      <div class="row"><span class="lbl">確定日</span><span class="val" id="gc-date">-</span></div>
      <div class="row"><span class="lbl">総検出回数</span><span class="val" id="gc-count">-</span></div>
    </div>
  </div>
</div>
<script>
const ALL_DATA    = __JSON_DATA__;
const RANGES_INFO = __RANGES_JSON__;

// =====================================================
// MA (単純移動平均)
// =====================================================
function calcMA(data, p) {
  if (data.length < p) return [];
  const r = []; let s = 0;
  for (let i = 0; i < data.length; i++) {
    s += data[i].close;
    if (i >= p) s -= data[i-p].close;
    if (i >= p-1) r.push({time: data[i].time, value: s/p});
  }
  return r;
}

// =====================================================
// Fourier overlay (daily net balance)
// =====================================================
const FOURIER_COLORS = ['#f59e0b','#58a6ff','#22c55e','#f85149','#d2a8ff'];
const FREQUENCY_COLOR = '#f0f6fc';

function analyzeFourier(data, topN) {
  const n = data.length;
  if (n < 20) return null;
  const values = data.map(d => d.close - d.open);
  const mean = values.reduce((a,b) => a+b, 0) / n;
  const centered = values.map(v => v - mean);
  const coeffs = [];
  for (let k = 0; k <= Math.floor(n/2); k++) {
    let re = 0, im = 0;
    for (let t = 0; t < n; t++) {
      const angle = 2 * Math.PI * k * t / n;
      re += centered[t] * Math.cos(angle);
      im -= centered[t] * Math.sin(angle);
    }
    coeffs.push({k,re,im,period:k ? n/k : Infinity,amplitude:2*Math.hypot(re,im)/n});
  }
  const candidates = coeffs.filter(c => c.k > 0 && c.period >= 2 && c.period <= n/2);
  const local = candidates.filter((c,i,a) =>
    c.amplitude >= (i ? a[i-1].amplitude : -1) &&
    c.amplitude >= (i+1<a.length ? a[i+1].amplitude : -1)
  );
  const peaks = local.sort((a,b) => b.amplitude-a.amplitude).slice(0,topN);
  const chartLow = Math.min(...data.map(d => d.low));
  const chartHigh = Math.max(...data.map(d => d.high));
  const chartMid = (chartHigh + chartLow) / 2;
  const chartHalfRange = (chartHigh - chartLow) / 2;
  const components = peaks.map((peak,index) => {
    const phase = Math.atan2(peak.im,peak.re) + Math.PI/2;
    const raw = data.map((_,t) => peak.amplitude*Math.sin(2*Math.PI*peak.k*t/n+phase));
    const rawLow = Math.min(...raw), rawHigh = Math.max(...raw), rawRange = rawHigh-rawLow || 1;
    return {
      rank:index+1, period:peak.period, color:FOURIER_COLORS[index],
      data:data.map((d,t) => ({time:d.time,value:chartLow+(raw[t]-rawLow)*(chartHigh-chartLow)/rawRange}))
    };
  });

  const spectrum = candidates.slice().sort((a,b) => a.period-b.period);
  const logMin = Math.log(spectrum[0].period), logMax = Math.log(spectrum[spectrum.length-1].period);
  const interpolated = [];
  let right = 1;
  for (let i = 0; i < n; i++) {
    const target = logMin + (logMax-logMin)*i/Math.max(n-1,1);
    while (right < spectrum.length-1 && Math.log(spectrum[right].period) < target) right++;
    const left = Math.max(right-1,0);
    const leftLog = Math.log(spectrum[left].period), rightLog = Math.log(spectrum[right].period);
    const fraction = rightLog===leftLog ? 0 : (target-leftLog)/(rightLog-leftLog);
    interpolated.push(spectrum[left].amplitude*(1-fraction)+spectrum[right].amplitude*fraction);
  }
  const spectrumMean = interpolated.reduce((a,b)=>a+b,0)/interpolated.length;
  const deviations = interpolated.map(v=>v-spectrumMean);
  const maxDeviation = Math.max(...deviations.map(Math.abs)) || 1;
  const frequency = data.map((d,i) => ({time:d.time,value:chartMid+chartHalfRange*deviations[i]/maxDeviation}));
  return {frequency,components};
}

function buildFourierControls(analysis, frequencySeries, componentSeries) {
  const controls = document.getElementById('fourier-controls');
  if (!analysis) {
    controls.innerHTML = '<span class="fourier-empty">Fourier: 20営業日以上のデータが必要です</span>';
    return;
  }
  controls.innerHTML = `<label class="fourier-toggle"><input type="checkbox" data-layer="frequency"><span class="fourier-swatch" style="background:${FREQUENCY_COLOR}"></span>Frequency spectrum</label>` +
    analysis.components.map(c => `<label class="fourier-toggle"><input type="checkbox" data-rank="${c.rank}"><span class="fourier-swatch" style="background:${c.color}"></span>#${c.rank} ${c.period.toFixed(2)} days</label>`).join('');
  controls.onchange = event => {
    const input = event.target.closest('input');
    if (!input) return;
    if (input.dataset.layer === 'frequency') {
      frequencySeries.setData(input.checked ? analysis.frequency : []);
      return;
    }
    const item = componentSeries.get(Number(input.dataset.rank));
    if (item) item.series.setData(input.checked ? item.data : []);
  };
}

// =====================================================
// スイング高値 / 安値 検出
// lookback 本前後より高い/低いバーをスイングとみなす
// =====================================================
function detectSwings(data, lb) {
  lb = lb || 5;
  const highs = [], lows = [];
  for (let i = lb; i < data.length - lb; i++) {
    let isH = true, isL = true;
    for (let j = 1; j <= lb; j++) {
      if (data[i].high  <= data[i-j].high  || data[i].high  <= data[i+j].high)  isH = false;
      if (data[i].low   >= data[i-j].low   || data[i].low   >= data[i+j].low)   isL = false;
    }
    if (isH) highs.push({time: data[i].time, price: data[i].high, idx: i});
    if (isL)  lows.push({time: data[i].time, price: data[i].low,  idx: i});
  }
  return {highs, lows};
}

function detectCloseSwings(data, left, right) {
  const highs=[], lows=[];
  for (let i=left;i<data.length-right;i++) {
    let isH=true,isL=true;
    for (let j=1;j<=left;j++) {
      if (data[i].close<=data[i-j].close) isH=false;
      if (data[i].close>=data[i-j].close) isL=false;
    }
    for (let j=1;j<=right;j++) {
      if (data[i].close<=data[i+j].close) isH=false;
      if (data[i].close>=data[i+j].close) isL=false;
    }
    if (isH) highs.push({time:data[i].time,price:data[i].high,close:data[i].close,idx:i});
    if (isL) lows.push({time:data[i].time,price:data[i].low,close:data[i].close,idx:i});
  }
  return {highs,lows};
}

function refineLow(data, pivot) {
  let best=pivot;
  for (let i=Math.max(0,pivot.idx-1);i<=Math.min(data.length-1,pivot.idx+1);i++) {
    if (data[i].low<best.price) best={time:data[i].time,price:data[i].low,idx:i};
  }
  return best;
}

function calendarGap(a, b) {
  return Math.round((Date.parse(`${b}T00:00:00Z`)-Date.parse(`${a}T00:00:00Z`))/86400000);
}

function isValidNWave(data, sl1, sh1, sl2, sh2) {
  if (!(sl1.idx<sh1.idx && sh1.idx<sl2.idx && sl2.idx<sh2.idx)) return false;
  if (!(sl2.price>sl1.price && sh2.price>sh1.price)) return false;
  const gaps=[calendarGap(sl1.time,sh1.time),calendarGap(sh1.time,sl2.time),calendarGap(sl2.time,sh2.time)];
  const minGap=Math.min(...gaps), maxGap=Math.max(...gaps);
  if (minGap<=0 || maxGap>90 || maxGap/minGap>5) return false;
  if (calendarGap(sh2.time,data[data.length-1].time)>21) return false;
  return !data.slice(sh2.idx+1).some(bar=>bar.low<sl2.price);
}

function detectMajorBullStructure(data) {
  const major=detectCloseSwings(data,10,10);
  const medium=detectCloseSwings(data,7,7);
  const recent=detectCloseSwings(data,7,5);
  for (let h=recent.highs.length-1;h>=0;h--) {
    const sh2=recent.highs[h];
    const sl2Raw=[...medium.lows].reverse().find(p=>p.idx<sh2.idx);
    if (!sl2Raw) continue;
    const sl2=refineLow(data,sl2Raw);
    const pairs=major.highs.filter(p=>p.idx<sl2Raw.idx).map(sh1=>{
      const sl1Raw=[...major.lows].reverse().find(p=>p.idx<sh1.idx);
      return sl1Raw ? {sl1:refineLow(data,sl1Raw),sh1} : null;
    }).filter(pair=>pair && isValidNWave(data,pair.sl1,pair.sh1,sl2,sh2));
    if (!pairs.length || data[data.length-1].close<sl2.price) continue;
    const best=pairs.reduce((a,b)=>b.sh1.close>a.sh1.close?b:a);
    return {sl1:best.sl1,sh1:best.sh1,sl2,sh2,major:true};
  }
  return null;
}

function detectBullStructure(data, lb) {
  const major=detectMajorBullStructure(data);
  if (major) return major;
  const {highs,lows} = detectSwings(data,lb||5);
  if (highs.length<2 || lows.length<2) return null;
  const sh2=highs[highs.length-1];
  const sl2=[...lows].reverse().find(p=>p.idx<sh2.idx);
  if (!sl2) return null;
  const sh1=[...highs].reverse().find(p=>p.idx<sl2.idx);
  if (!sh1) return null;
  const sl1=[...lows].reverse().find(p=>p.idx<sh1.idx);
  if (!sl1) return null;
  return isValidNWave(data,sl1,sh1,sl2,sh2) ? {sl1,sh1,sl2,sh2} : null;
}

function detectProvisionalBullStructure(data, lb) {
  const lookback=lb||5;
  const confirmed=detectBullStructure(data,lookback);
  const candidate=detectBullStructure(data,Math.max(2,lookback-1));
  if (!candidate || data[data.length-1].close<candidate.sl2.price) return null;
  if (confirmed && candidate.sh2.idx<=confirmed.sh2.idx) return null;
  return {...candidate,provisional:true};
}

function latestBullStructure(data, lb) {
  const confirmed=detectBullStructure(data,lb||5);
  const provisional=detectProvisionalBullStructure(data,lb||5);
  return {confirmed,provisional,active:provisional||confirmed};
}

function fibPositionClass(data, structure) {
  if (!structure || !data.length) return '';
  const current=data[data.length-1].close;
  const high=structure.sh2.price, low=structure.sl2.price;
  if (current>high) return 'fib-green';
  if (current<low) return '';
  const retracement=(high-current)/(high-low || 1);
  if (retracement<=0.382) return 'fib-blue';
  if (retracement<=0.618) return 'fib-yellow';
  return 'fib-red';
}

function signedFmt(value) {
  const rounded=Math.round(value);
  return (rounded>0?'+':'')+rounded.toLocaleString();
}

function addCalendarDays(isoDate, days) {
  const date=new Date(`${isoDate}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate()+days);
  return date.toISOString().slice(0,10);
}

function horizontalLineData(startTime, endTime, value) {
  const points=[];
  const start=new Date(`${startTime}T00:00:00Z`);
  const end=new Date(`${endTime}T00:00:00Z`);
  for (let date=start; date<=end; date=new Date(date.getTime()+86400000)) {
    points.push({time:date.toISOString().slice(0,10),value});
  }
  return points;
}

// =====================================================
// ゴールデンクロス検出
// MA5 が MA75 をGC → その後 MA5 が MA20 をGC (75→20 の順)
// =====================================================
function detectGC(ma5, ma20, ma75) {
  const events = [];
  const m5 = {}, m20 = {}, m75 = {};
  ma5.forEach(d  => m5[d.time]  = d.value);
  ma20.forEach(d => m20[d.time] = d.value);
  ma75.forEach(d => m75[d.time] = d.value);

  // MA75が存在する期間を基準にする
  const times = ma75.map(d => d.time);
  let cross75 = null;  // MA5×MA75GC済みフラグ

  for (let i = 1; i < times.length; i++) {
    const t = times[i], tp = times[i-1];
    const v5  = m5[t],  v5p  = m5[tp];
    const v20 = m20[t], v20p = m20[tp];
    const v75 = m75[t], v75p = m75[tp];
    if (v5==null||v5p==null||v20==null||v20p==null||v75==null||v75p==null) continue;

    // ① MA5 が MA75 を下→上へクロス (GC①)
    if (v5p <= v75p && v5 > v75) {
      cross75 = {time: t, price: v5};
    }
    // MA5 が MA75 を上→下へクロス (DC→シーケンスリセット)
    if (v5p >= v75p && v5 < v75) {
      cross75 = null;
    }
    // ② MA5 が MA20 を下→上へクロス (GC②) かつ①済み
    if (cross75 && v5p <= v20p && v5 > v20) {
      events.push({
        type:        'GC_FULL',
        time:        t,
        price:       v5,
        cross75Time: cross75.time,
      });
      cross75 = null;
    }
  }
  return events;
}

// =====================================================
// フィボナッチ水準 (retracement + extension)
// =====================================================
function calcFibs(low, high) {
  const r = high - low;
  return [
    {level:0,     price:high,           label:'0%',      ext:false},
    {level:0.236, price:high - r*0.236, label:'23.6%',   ext:false},
    {level:0.382, price:high - r*0.382, label:'38.2%',   ext:false},
    {level:0.5,   price:high - r*0.5,   label:'50.0%',   ext:false},
    {level:0.618, price:high - r*0.618, label:'61.8%',   ext:false},
    {level:0.786, price:high - r*0.786, label:'78.6%',   ext:false},
    {level:1,     price:low,            label:'100%',    ext:false},
    {level:1.272, price:high + r*0.272, label:'127.2%↑', ext:true},
    {level:1.618, price:high + r*0.618, label:'161.8%↑', ext:true},
  ];
}

// =====================================================
// 情報パネル更新
// =====================================================
function fmt(v) { return Math.round(v).toLocaleString(); }
function lastVal(arr) { return arr.length ? arr[arr.length-1].value : null; }
function swFmt(s) {
  if (!s) return '-';
  return fmt(s.price) + ' (' + s.time.slice(5).replace('-','/') + ')';
}

function updatePanel(data, ma5, ma20, ma75, gcEvents, swHigh, swLow, structure) {
  // MA最新値
  const lv5 = lastVal(ma5), lv20 = lastVal(ma20), lv75 = lastVal(ma75);
  document.getElementById('ma5v').textContent  = lv5  != null ? fmt(lv5)  : '-';
  document.getElementById('ma20v').textContent = lv20 != null ? fmt(lv20) : '-';
  document.getElementById('ma75v').textContent = lv75 != null ? fmt(lv75) : '-';

  // スイング
  const sh = swHigh.slice(-2), sl = swLow.slice(-2);
  document.getElementById('sh1').textContent = swFmt(structure?.sh1 || sh[sh.length-2]);
  document.getElementById('sh2').textContent = swFmt(structure?.sh2 || sh[sh.length-1]);
  document.getElementById('sl1').textContent = swFmt(structure?.sl1 || sl[sl.length-2]);
  document.getElementById('sl2').textContent = swFmt(structure?.sl2 || sl[sl.length-1]);

  // GC
  const badge  = document.getElementById('gc-badge');
  const gcDate = document.getElementById('gc-date');
  const gcCnt  = document.getElementById('gc-count');
  gcCnt.textContent = gcEvents.length;

  if (gcEvents.length > 0) {
    const lastGC = gcEvents[gcEvents.length-1];
    badge.className   = 'badge gc-on';
    badge.textContent = '✓ GC検出済み';
    gcDate.textContent = lastGC.time;

  } else {
    badge.className   = 'badge gc-off';
    badge.textContent = '未検出';
    gcDate.textContent = '-';
  }

  const fp = document.getElementById('fib-panel');
  const rrEl = document.getElementById('rr-ratio');
  if (structure) {
    const entry = data[data.length-1].close;
    const stop = structure.sl2.price;
    const fibs = calcFibs(stop,structure.sh2.price);
    const target = fibs.find(f=>f.level===1.618).price;
    const risk = entry-stop, reward=target-entry;
    const rr = risk>0 ? reward/risk : 0;
    document.getElementById('rr-entry').textContent=fmt(entry);
    document.getElementById('rr-stop').textContent=fmt(stop);
    document.getElementById('rr-target').textContent=fmt(target);
    rrEl.textContent=risk>0 ? rr.toFixed(2)+':1' : '算出不可';
    rrEl.className='val '+(rr>=2?'rr-g':rr>=1?'rr-y':'rr-r');
    fp.innerHTML=(structure.provisional?'<div class="badge gc-on" style="margin-bottom:5px">暫定構造</div>':'')+fibs.map(f=>`<div class="frow"><span class="${f.ext?'fext':'flbl'}">${f.label}</span><span class="fval">${fmt(f.price)}</span></div>`).join('');
  } else {
    ['rr-entry','rr-stop','rr-target'].forEach(id=>document.getElementById(id).textContent='-');
    rrEl.textContent='-'; rrEl.className='val';
    fp.innerHTML='<span class="flbl">上昇構造の成立時に表示</span>';
  }
}

// =====================================================
// チャート描画
// =====================================================
let currentChart = null;

function renderChart(seriesData) {
  const container = document.getElementById('chart-container');

  if (currentChart) {
    try { currentChart.remove(); } catch(e) {}
    currentChart = null;
  }
  container.innerHTML = '';

  if (!seriesData || seriesData.length === 0) {
    document.getElementById('fourier-controls').innerHTML = '<span class="fourier-empty">Fourier: データなし</span>';
    container.innerHTML = '<div class="no-data"><span>📭 データなし</span><small style="color:#6e7681">このレンジはまだ取込済みCSVがありません</small></div>';
    ['ma5v','ma20v','ma75v','sh1','sh2','sl1','sl2','gc-date','gc-count','rr-entry','rr-stop','rr-target','rr-ratio'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.textContent = '-';
    });
    document.getElementById('gc-badge').className   = 'badge gc-off';
    document.getElementById('gc-badge').textContent = '未検出';
    document.getElementById('gc-count').textContent = '-';
    document.getElementById('fib-panel').innerHTML  = '<span class="flbl">上昇構造の成立時に表示</span>';
    document.getElementById('fib-local-toggle').checked = false;
    document.getElementById('fib-local-toggle').disabled = true;
    document.getElementById('fib-global-toggle').checked = false;
    return;
  }

  const chart = LightweightCharts.createChart(container, {
    width:  container.clientWidth,
    height: container.clientHeight,
    layout: {background:{color:'#0d1117'}, textColor:'#8b949e'},
    grid:   {vertLines:{color:'#1c2128'}, horzLines:{color:'#1c2128'}},
    crosshair: {mode: LightweightCharts.CrosshairMode.Normal},
    rightPriceScale: {borderColor:'#30363d'},
    timeScale: {borderColor:'#30363d', timeVisible:false, secondsVisible:false},
  });
  currentChart = chart;

  new ResizeObserver(() => {
    if (currentChart) currentChart.applyOptions({
      width:  container.clientWidth,
      height: container.clientHeight,
    });
  }).observe(container);

  // ローソク足
  const candle = chart.addCandlestickSeries({
    upColor:'#26a69a', downColor:'#ef5350',
    borderVisible: false,
    wickUpColor:'#26a69a', wickDownColor:'#ef5350',
  });
  candle.setData(seriesData);

  // Frequency spectrum + dominant sine components
  const fourier = analyzeFourier(seriesData, 5);
  let frequencySeries = null;
  const componentSeries = new Map();
  if (fourier) {
    frequencySeries = chart.addLineSeries({
      color:FREQUENCY_COLOR, lineWidth:2, title:'Frequency spectrum',
      lastValueVisible:false, priceLineVisible:false, crosshairMarkerVisible:true,
    });
    frequencySeries.setData([]);
    fourier.components.forEach(component => {
      const series = chart.addLineSeries({
        color:component.color, lineWidth:2, title:`#${component.rank} ${component.period.toFixed(2)}d`,
        lastValueVisible:false, priceLineVisible:false, crosshairMarkerVisible:true,
      });
      series.setData([]);
      componentSeries.set(component.rank,{series,data:component.data});
    });
  }
  buildFourierControls(fourier, frequencySeries, componentSeries);

  // MA
  const ma5  = calcMA(seriesData, 5);
  const ma20 = calcMA(seriesData, 20);
  const ma75 = calcMA(seriesData, 75);

  const addLine = (data, color, lw, title) => {
    const s = chart.addLineSeries({
      color, lineWidth:lw, title,
      lastValueVisible:false, priceLineVisible:false, crosshairMarkerVisible:false,
    });
    s.setData(data);
    return s;
  };
  addLine(ma5,  '#ffeb3b', 1, 'MA5');
  addLine(ma20, '#42a5f5', 1, 'MA20');
  addLine(ma75, '#ff7043', 2, 'MA75');

  // GC 検出
  const gcEvents = detectGC(ma5, ma20, ma75);

  // スイング検出
  const {highs: swHigh, lows: swLow} = detectSwings(seriesData, 5);
  const structureState = latestBullStructure(seriesData, 5);
  const confirmedStructure = structureState.confirmed;
  const provisionalStructure = structureState.provisional;
  const structure = structureState.active;

  // マーカー
  const markers = [];
  gcEvents.forEach(gc => {
    markers.push({time:gc.cross75Time, position:'belowBar', color:'#ffd700', shape:'arrowUp',   text:'GC①×MA75', size:1});
    markers.push({time:gc.time,        position:'belowBar', color:'#00e676', shape:'arrowUp',   text:'◎GC②×MA20', size:2});
  });
  if (structure) {
    const suffix=structure.provisional?'?':'';
    markers.push({time:structure.sl1.time,position:'belowBar',color:'#26a69a',shape:'arrowUp',text:`SL1${suffix}`,size:1});
    markers.push({time:structure.sh1.time,position:'aboveBar',color:'#ef5350',shape:'arrowDown',text:`SH1${suffix}`,size:1});
    markers.push({time:structure.sl2.time,position:'belowBar',color:structure.provisional?'#7ee787':'#00e676',shape:'arrowUp',text:`SL2${suffix}`,size:2});
    markers.push({time:structure.sh2.time,position:'aboveBar',color:structure.provisional?'#7ee787':'#ff5252',shape:'arrowDown',text:`SH2${suffix}`,size:2});
  }
  markers.sort((a,b) => a.time<b.time ? -1 : a.time>b.time ? 1 : 0);
  candle.setMarkers(markers);

  if (provisionalStructure && confirmedStructure) {
    const oldLeg1=chart.addLineSeries({color:'#bc8cff66',lineWidth:1,lineStyle:2,title:'確定 SL1→SH1',lastValueVisible:false,priceLineVisible:false,crosshairMarkerVisible:false});
    oldLeg1.setData([{time:confirmedStructure.sl1.time,value:confirmedStructure.sl1.price},{time:confirmedStructure.sh1.time,value:confirmedStructure.sh1.price}]);
    const oldLeg2=chart.addLineSeries({color:'#ff8b3d66',lineWidth:1,lineStyle:2,title:'確定 SL2→SH2',lastValueVisible:false,priceLineVisible:false,crosshairMarkerVisible:false});
    oldLeg2.setData([{time:confirmedStructure.sl2.time,value:confirmedStructure.sl2.price},{time:confirmedStructure.sh2.time,value:confirmedStructure.sh2.price}]);
  }
  if (structure) {
    const legColor=structure.provisional?'#7ee787':'#bc8cff';
    const legStyle=structure.provisional?2:0;
    const leg1=chart.addLineSeries({color:legColor,lineWidth:3,lineStyle:legStyle,title:structure.provisional?'暫定 SL1→SH1':'SL1→SH1',lastValueVisible:false,priceLineVisible:false,crosshairMarkerVisible:false});
    leg1.setData([{time:structure.sl1.time,value:structure.sl1.price},{time:structure.sh1.time,value:structure.sh1.price}]);
    const leg2=chart.addLineSeries({color:structure.provisional?'#56d364':'#ff8b3d',lineWidth:3,lineStyle:legStyle,title:structure.provisional?'暫定 SL2→SH2':'SL2→SH2',lastValueVisible:false,priceLineVisible:false,crosshairMarkerVisible:false});
    leg2.setData([{time:structure.sl2.time,value:structure.sl2.price},{time:structure.sh2.time,value:structure.sh2.price}]);
    const entry=seriesData[seriesData.length-1].close;
    const target=calcFibs(structure.sl2.price,structure.sh2.price).find(f=>f.level===1.618).price;
    candle.createPriceLine({price:entry,color:'#ffd33d',lineWidth:1,lineStyle:2,axisLabelVisible:true,title:'Entry'});
    candle.createPriceLine({price:structure.sl2.price,color:'#00e676',lineWidth:2,lineStyle:0,axisLabelVisible:true,title:'Stop SL2'});
    candle.createPriceLine({price:target,color:'#f2cc60',lineWidth:2,lineStyle:2,axisLabelVisible:true,title:'Target 1.618'});
  }

  const fibColors=['#d2a8ff','#bc8cff','#a371f7','#8957e5','#6e40c9','#553098','#d2a8ff','#f778ba','#ff7b72'];
  const currentPrice=seriesData[seriesData.length-1].close;
  const fibEndTime=addCalendarDays(seriesData[seriesData.length-1].time,12);
  const makeFibLayers=(fibs,startTime,prefix)=>fibs.map((f,i)=>{
    const data=horizontalLineData(startTime,fibEndTime,f.price);
    const distance=f.price-currentPrice;
    const series=chart.addLineSeries({color:fibColors[i],lineWidth:(f.level===0||f.level===1)?2:1,lineStyle:(f.level===0||f.level===1)?0:2,title:'',lastValueVisible:false,priceLineVisible:false,crosshairMarkerVisible:false});
    const label=document.createElement('div');
    label.className='fib-line-label';
    label.style.color=fibColors[i];
    label.textContent=`${f.label} ${signedFmt(distance)}`;
    label.style.display='none';
    container.appendChild(label);
    return {series,data,f,color:fibColors[i],distance,endTime:fibEndTime,label,visible:false};
  });
  const positionFibLabels=layers=>layers.forEach(layer=>{
    if (!layer.visible) return;
    const x=chart.timeScale().timeToCoordinate(layer.endTime);
    const y=layer.series.priceToCoordinate(layer.f.price);
    if (x==null || y==null || x<0 || x>container.clientWidth || y<0 || y>container.clientHeight) {
      layer.label.style.display='none';
      return;
    }
    layer.label.style.display='block';
    const labelWidth=layer.label.offsetWidth || 90;
    layer.label.style.left=`${Math.max(4,Math.min(container.clientWidth-labelWidth-68,x+8))}px`;
    layer.label.style.top=`${y}px`;
  });
  const setFibVisibility=(layers,visible)=>{
    layers.forEach(layer=>{
      layer.series.setData(visible?layer.data:[]);
      layer.visible=visible;
      layer.label.style.display=visible?'block':'none';
    });
    requestAnimationFrame(()=>positionFibLabels(layers));
  };
  const localFibLayers=structure ? makeFibLayers(calcFibs(structure.sl2.price,structure.sh2.price),structure.sl2.time,'Local Fib') : [];
  setFibVisibility(localFibLayers,true);
  const globalLow=Math.min(...seriesData.map(d=>d.low)), globalHigh=Math.max(...seriesData.map(d=>d.high));
  const globalFibLayers=makeFibLayers(calcFibs(globalLow,globalHigh),seriesData[0].time,'All Fib');
  setFibVisibility(globalFibLayers,false);
  const localToggle=document.getElementById('fib-local-toggle');
  const globalToggle=document.getElementById('fib-global-toggle');
  document.getElementById('fib-local-label').textContent=structure?.provisional?'暫定 SL2→SH2':'SL2→SH2';
  localToggle.checked=!!structure; localToggle.disabled=!structure;
  globalToggle.checked=false; globalToggle.disabled=false;
  localToggle.onchange=()=>setFibVisibility(localFibLayers,localToggle.checked);
  globalToggle.onchange=()=>setFibVisibility(globalFibLayers,globalToggle.checked);

  updatePanel(seriesData, ma5, ma20, ma75, gcEvents, swHigh, swLow, structure);
  chart.timeScale().fitContent();
  chart.timeScale().applyOptions({rightOffset:4});
  const allFibLayers=[...localFibLayers,...globalFibLayers];
  const refreshFibLabels=()=>requestAnimationFrame(()=>positionFibLabels(allFibLayers));
  chart.timeScale().subscribeVisibleLogicalRangeChange(refreshFibLabels);
  new ResizeObserver(refreshFibLabels).observe(container);
  refreshFibLabels();
}

// =====================================================
// タブ / サブタブ 制御
// =====================================================
function buildMainTabs() {
  const el = document.getElementById('tabs');
  RANGES_INFO.forEach((r, i) => {
    const t = document.createElement('div');
    t.className   = 'tab' + (i===0 ? ' active':'');
    t.textContent = r.label;
    t.dataset.rid = r.id;
    t.addEventListener('click', () => selectRange(r.id));
    el.appendChild(t);
  });
  if (RANGES_INFO.length) selectRange(RANGES_INFO[0].id);
}

function selectRange(rid) {
  document.querySelectorAll('.tab').forEach(t =>
    t.classList.toggle('active', t.dataset.rid === rid)
  );
  const d = ALL_DATA[rid];
  if (!d) return;

  const subEl = document.getElementById('sub-tabs');
  subEl.innerHTML = '';

  const addSub = (key, label, isFirst) => {
    const t = document.createElement('div');
    const machineSeries = key==='aggregate' ? null : d.machines[key.replace('m_','')];
    const structureState = machineSeries ? latestBullStructure(machineSeries,5) : {active:null,provisional:null};
    const structure = structureState.active;
    const positionClass = machineSeries ? fibPositionClass(machineSeries,structure) : '';
    t.className   = 'sub-tab' + (structureState.provisional ? ' provisional' : positionClass ? ' '+positionClass:'') + (isFirst ? ' active':'');
    t.title = structureState.provisional ? '暫定上昇構造（左右4日スイング、5日確定待ち）' : positionClass==='fib-green' ? 'SH2（0%）を上抜け' : positionClass==='fib-blue' ? '0%〜38.2%' : positionClass==='fib-yellow' ? '38.2%〜61.8%' : positionClass==='fib-red' ? '61.8%〜100%' : structure ? 'SL2（100%）を下抜け：上昇構造失敗' : '';
    t.textContent = label;
    t.dataset.key = key;
    t.addEventListener('click', () => {
      document.querySelectorAll('.sub-tab').forEach(s => s.classList.remove('active'));
      t.classList.add('active');
      selectSeries(rid, key);
    });
    subEl.appendChild(t);
  };

  addSub('aggregate', '📊 全体合計', true);
  Object.keys(d.machines).sort((a,b)=>+a-+b).forEach(m =>
    addSub('m_'+m, m+'番', false)
  );

  selectSeries(rid, 'aggregate');
}

function selectSeries(rid, key) {
  const d = ALL_DATA[rid];
  if (!d) return;
  const series = (key==='aggregate') ? d.aggregate : d.machines[key.replace('m_','')];
  renderChart(series || []);
}

buildMainTabs();
</script>
</body>
</html>"""

html = HTML.replace('__JSON_DATA__', json_data).replace('__RANGES_JSON__', ranges_json)

with open(OUT_FILE, 'w', encoding='utf-8') as f:
    f.write(html)

size_kb = os.path.getsize(OUT_FILE) / 1024
print(f"\n✅ 生成完了: {OUT_FILE}")
print(f"   ファイルサイズ: {size_kb:.0f} KB")
print(f"\n【使い方】")
print(f"  ブラウザで開く : {os.path.abspath(OUT_FILE)}")
print(f"  GitHub Pages  : charts/ohlc_chart.html をリポジトリにプッシュ → GitHub Pages で公開")
print(f"\n【機能】")
print(f"  ローソク足 + MA5(黄)/MA20(青)/MA75(橙)")
print(f"  GC: MA5がMA75→MA20の順にゴールデンクロス → ◎マーカー")
print(f"  上昇構造: SL1→SH1→SL2→SH2（高値・安値切り上げ）を自動検出")
print(f"  局所フィボナッチ: 構造成立時に SL2〜SH2 ベースで自動描画")
print(f"  全期間フィボナッチ: 各系列の High / Low をチェック操作で描画")
print(f"  R/R: エントリー=最終終値 / ストップ=SL2 / 目標=Fib1.618")
