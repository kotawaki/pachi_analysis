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
    {'id':'r34_38',   'label':'34〜38番',  'machines':list(range(34,39)),   'start':'20250604'},
    {'id':'r39_77',   'label':'39〜77番',  'machines':list(range(39,78)),   'start':'20250517'},
    {'id':'r118_123', 'label':'118〜123番','machines':list(range(118,124)), 'start':'20251210'},
    {'id':'r146_153', 'label':'146〜153番','machines':list(range(146,154)), 'start':'20251114'},
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
#main{display:flex;flex:1;min-height:0}
#chart-container{flex:1;min-width:0;position:relative}
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
</style>
</head>
<body>
<div id="header">
  <h1><a href="index.html" style="color:#8b949e;text-decoration:none">🏠 トップ</a> &nbsp;｜&nbsp; <a href="groups.html" style="color:#58a6ff;text-decoration:none">🏆 グループ強さランキング</a> &nbsp;｜&nbsp; 🎰 差玉チャート &nbsp;/&nbsp; 日足OHLC &nbsp;/&nbsp; MA5・20・75 &nbsp;/&nbsp; Fibonacci &nbsp;/&nbsp; Golden Cross (75→20) &nbsp;/&nbsp; Swing H/L &nbsp;/&nbsp; R/R</h1>
</div>
<div id="tabs"></div>
<div id="sub-tabs"></div>
<div id="main">
  <div id="chart-container"></div>
  <div id="info-panel">
    <div class="sec">
      <div class="sec-title">Golden Cross</div>
      <div id="gc-badge" class="badge gc-off" style="margin-bottom:5px">未検出</div>
      <div class="row"><span class="lbl">確定日</span><span class="val" id="gc-date">-</span></div>
      <div class="row"><span class="lbl">総検出回数</span><span class="val" id="gc-count">-</span></div>
    </div>
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
      <div class="row"><span class="lbl">ストップ(SL1)</span><span class="val" id="rr-stop">-</span></div>
      <div class="row"><span class="lbl">目標(Fib1.618)</span><span class="val" id="rr-target">-</span></div>
      <div class="row"><span class="lbl">R/R 比率</span><span class="val" id="rr-ratio">-</span></div>
    </div>
    <div class="sec">
      <div class="sec-title">Fibonacci</div>
      <div id="fib-panel"><span class="flbl">GC後に自動表示</span></div>
    </div>
    <div class="sec">
      <div class="sec-title">MA 最新値</div>
      <div class="row"><span class="lbl ma5c">■ MA5</span><span class="val" id="ma5v">-</span></div>
      <div class="row"><span class="lbl ma20c">■ MA20</span><span class="val" id="ma20v">-</span></div>
      <div class="row"><span class="lbl ma75c">■ MA75</span><span class="val" id="ma75v">-</span></div>
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

function updatePanel(data, ma5, ma20, ma75, gcEvents, swHigh, swLow) {
  // MA最新値
  const lv5 = lastVal(ma5), lv20 = lastVal(ma20), lv75 = lastVal(ma75);
  document.getElementById('ma5v').textContent  = lv5  != null ? fmt(lv5)  : '-';
  document.getElementById('ma20v').textContent = lv20 != null ? fmt(lv20) : '-';
  document.getElementById('ma75v').textContent = lv75 != null ? fmt(lv75) : '-';

  // スイング
  const sh = swHigh.slice(-2), sl = swLow.slice(-2);
  document.getElementById('sh1').textContent = swFmt(sh[sh.length-1]);
  document.getElementById('sh2').textContent = swFmt(sh[sh.length-2]);
  document.getElementById('sl1').textContent = swFmt(sl[sl.length-1]);
  document.getElementById('sl2').textContent = swFmt(sl[sl.length-2]);

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

    // R/R 計算
    const entry = data[data.length-1].close;
    const sl1   = sl.length ? sl[sl.length-1].price : null;
    const sh1   = sh.length ? sh[sh.length-1].price : null;

    if (sl1 != null && sh1 != null && sl1 < entry) {
      const fibs   = calcFibs(sl1, sh1);
      const f1618  = fibs.find(f => f.level === 1.618);
      const target = f1618 ? f1618.price : entry + (entry - sl1) * 2;
      const risk   = entry - sl1;
      const reward = target - entry;
      const rr     = risk > 0 ? reward / risk : 0;

      document.getElementById('rr-entry').textContent  = fmt(entry);
      document.getElementById('rr-stop').textContent   = fmt(sl1);
      document.getElementById('rr-target').textContent = fmt(target);
      const rrEl = document.getElementById('rr-ratio');
      rrEl.textContent = rr.toFixed(2) + ':1';
      rrEl.className   = 'val ' + (rr >= 2 ? 'rr-g' : rr >= 1 ? 'rr-y' : 'rr-r');

      // Fibパネル
      const fp = document.getElementById('fib-panel');
      fp.innerHTML = fibs.map(f =>
        `<div class="frow">
          <span class="${f.ext ? 'fext' : 'flbl'}">${f.label}</span>
          <span class="fval">${fmt(f.price)}</span>
        </div>`
      ).join('');
    }
  } else {
    badge.className   = 'badge gc-off';
    badge.textContent = '未検出';
    gcDate.textContent = '-';
    ['rr-entry','rr-stop','rr-target'].forEach(id => {
      document.getElementById(id).textContent = '-';
    });
    const rrEl = document.getElementById('rr-ratio');
    rrEl.textContent = '-';
    rrEl.className   = 'val';
    document.getElementById('fib-panel').innerHTML = '<span class="flbl">GC後に自動表示</span>';
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
    container.innerHTML = '<div class="no-data"><span>📭 データなし</span><small style="color:#6e7681">このレンジはまだ取込済みCSVがありません</small></div>';
    ['ma5v','ma20v','ma75v','sh1','sh2','sl1','sl2','gc-date','gc-count','rr-entry','rr-stop','rr-target','rr-ratio'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.textContent = '-';
    });
    document.getElementById('gc-badge').className   = 'badge gc-off';
    document.getElementById('gc-badge').textContent = '未検出';
    document.getElementById('gc-count').textContent = '-';
    document.getElementById('fib-panel').innerHTML  = '<span class="flbl">GC後に自動表示</span>';
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

  // マーカー
  const markers = [];
  gcEvents.forEach(gc => {
    markers.push({time:gc.cross75Time, position:'belowBar', color:'#ffd700', shape:'arrowUp',   text:'GC①×MA75', size:1});
    markers.push({time:gc.time,        position:'belowBar', color:'#00e676', shape:'arrowUp',   text:'◎GC②×MA20', size:2});
  });
  const sh2 = swHigh.slice(-2), sl2 = swLow.slice(-2);
  sh2.forEach((h, i, a) =>
    markers.push({time:h.time, position:'aboveBar', color:'#ef5350', shape:'arrowDown',
                  text: i===a.length-1 ? 'SH1':'SH2', size:1})
  );
  sl2.forEach((l, i, a) =>
    markers.push({time:l.time, position:'belowBar', color:'#26a69a', shape:'arrowUp',
                  text: i===a.length-1 ? 'SL1':'SL2', size:1})
  );
  markers.sort((a,b) => a.time<b.time ? -1 : a.time>b.time ? 1 : 0);
  candle.setMarkers(markers);

  // フィボナッチ描画 (GC検出後のみ)
  if (gcEvents.length > 0 && swHigh.length > 0 && swLow.length > 0) {
    const sl1 = swLow[swLow.length-1];
    const sh1 = swHigh[swHigh.length-1];
    const fibs = calcFibs(sl1.price, sh1.price);
    const fibColors = ['#ce93d8','#ab47bc','#9c27b0','#7b1fa2','#6a1b9a','#4a148c','#ce93d8','#f48fb1','#e91e63'];
    fibs.forEach((f, i) => {
      candle.createPriceLine({
        price:            f.price,
        color:            fibColors[i] || '#888',
        lineWidth:        (f.level===0||f.level===1) ? 2 : 1,
        lineStyle:        (f.level===0||f.level===1) ? 0 : 2,
        axisLabelVisible: true,
        title:            'Fib ' + f.label,
      });
    });
  }

  updatePanel(seriesData, ma5, ma20, ma75, gcEvents, swHigh, swLow);
  chart.timeScale().fitContent();
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
    t.className   = 'sub-tab' + (isFirst ? ' active':'');
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
print(f"  スイング高値(SH1/SH2) / 安値(SL1/SL2) 自動検出")
print(f"  フィボナッチ (GC後に SL1〜SH1 ベースで自動描画)")
print(f"  R/R: エントリー=最終終値 / ストップ=SL1 / 目標=Fib1.618")
