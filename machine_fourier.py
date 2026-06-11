"""Generate a Fourier analysis page for one machine's daily net balance."""

import argparse
import cmath
import csv
import glob
import json
import math
import os
from pathlib import Path


ROOT = Path(__file__).parent
CSV_DIR = ROOT / "csv" / "analyze"
DOCS_DIR = ROOT / "docs"


def load_daily_net(machine):
    target = str(machine).zfill(3)
    daily = []
    for path in sorted(glob.glob(str(CSV_DIR / "*" / "*_analyze.csv"))):
        date = os.path.basename(os.path.dirname(path))
        rows = []
        with open(path, encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                if str(row.get("Machine", "")).strip().zfill(3) != target:
                    continue
                kind = str(row.get("種別", "")).strip()
                start_time = str(row.get("開始時刻", "")).strip()
                end_time = str(row.get("終了時刻", "")).strip()
                if not kind or not end_time:
                    continue
                try:
                    start_ball = int(row.get("開始差玉", 0) or 0)
                    end_ball = int(row.get("終了差玉", 0) or 0)
                except (TypeError, ValueError):
                    continue
                rows.append((start_time, end_time, start_ball, end_ball))
        if rows:
            rows.sort(key=lambda x: x[0])
            latest = max(rows, key=lambda x: x[1])
            points = [0]
            for _, _, start_ball, end_ball in rows:
                points.extend((start_ball, end_ball))
            daily.append((date, latest[3], max(points), min(points)))
    return daily


def dft(values):
    n = len(values)
    return [
        sum(value * cmath.exp(-2j * math.pi * k * t / n) for t, value in enumerate(values))
        for k in range(n)
    ]


def select_peaks(coeffs, top_n):
    n = len(coeffs)
    candidates = []
    for k in range(1, n // 2 + 1):
        period = n / k
        if period < 2 or period > n / 2:
            continue
        amplitude = 2 * abs(coeffs[k]) / n
        candidates.append({"k": k, "period": period, "amplitude": amplitude})

    local = []
    for i, item in enumerate(candidates):
        left = candidates[i - 1]["amplitude"] if i else -1
        right = candidates[i + 1]["amplitude"] if i + 1 < len(candidates) else -1
        if item["amplitude"] >= left and item["amplitude"] >= right:
            local.append(item)
    return sorted(local, key=lambda x: x["amplitude"], reverse=True)[:top_n]


def reconstruct(coeffs, ks, mean_value):
    n = len(coeffs)
    out = []
    for t in range(n):
        value = mean_value
        for k in ks:
            value += 2 * (coeffs[k] * cmath.exp(2j * math.pi * k * t / n)).real / n
        out.append(round(value, 2))
    return out


def generate(machine, top_n):
    daily = load_daily_net(machine)
    if len(daily) < 20:
        raise SystemExit(f"Not enough daily data for machine {machine}: {len(daily)} days")

    dates = [f"{d[:4]}-{d[4:6]}-{d[6:8]}" for d, _, _, _ in daily]
    values = [v for _, v, _, _ in daily]
    candles = []
    cumulative = 0
    for (_, net, day_high, day_low), iso_date in zip(daily, dates):
        open_value = cumulative
        close_value = cumulative + net
        candles.append({
            "time": iso_date,
            "open": open_value,
            "high": max(open_value + day_high, open_value, close_value),
            "low": min(open_value + day_low, open_value, close_value),
            "close": close_value,
        })
        cumulative = close_value
    avg = sum(values) / len(values)
    n = len(values)
    centered = [v - avg for v in values]
    coeffs = dft(centered)
    peaks = select_peaks(coeffs, top_n)
    chart_low = min(candle["low"] for candle in candles)
    chart_high = max(candle["high"] for candle in candles)
    chart_mid = (chart_high + chart_low) / 2
    chart_half_range = (chart_high - chart_low) / 2
    components = []
    for rank, peak in enumerate(peaks, 1):
        k = peak["k"]
        phase_sin = cmath.phase(coeffs[k]) + math.pi / 2
        component_values = [
            round(peak["amplitude"] * math.sin(2 * math.pi * k * t / n + phase_sin), 2)
            for t in range(n)
        ]
        component_low = min(component_values)
        component_high = max(component_values)
        component_range = component_high - component_low
        overlay_values = [
            {
                "time": iso_date,
                "value": round(
                    chart_low + (value - component_low) * (chart_high - chart_low) / component_range,
                    2,
                ),
            }
            for iso_date, value in zip(dates, component_values)
        ]
        phase_degrees = math.degrees(phase_sin)
        phase_degrees = ((phase_degrees + 180) % 360) - 180
        components.append({
            "rank": rank,
            "period": round(peak["period"], 2),
            "amplitude": round(peak["amplitude"], 1),
            "phase_degrees": round(phase_degrees, 1),
            "values": component_values,
            "overlay_values": overlay_values,
        })

    spectrum = []
    for k in range(1, n // 2 + 1):
        period = n / k
        if 2 <= period <= n / 2:
            spectrum.append({"k": k, "period": round(period, 3), "amplitude": round(2 * abs(coeffs[k]) / n, 2)})
    spectrum.sort(key=lambda x: x["period"])

    frequency_bins = spectrum
    log_periods = [math.log(item["period"]) for item in frequency_bins]
    log_min = log_periods[0]
    log_max = log_periods[-1]
    interpolated_frequency = []
    right = 1
    for index in range(n):
        target_log = log_min + (log_max - log_min) * index / max(n - 1, 1)
        while right < len(log_periods) - 1 and log_periods[right] < target_log:
            right += 1
        left = max(right - 1, 0)
        span = log_periods[right] - log_periods[left]
        fraction = (target_log - log_periods[left]) / span if span else 0
        amplitude = (
            frequency_bins[left]["amplitude"] * (1 - fraction)
            + frequency_bins[right]["amplitude"] * fraction
        )
        interpolated_frequency.append(amplitude)
    frequency_mean = sum(interpolated_frequency) / len(interpolated_frequency)
    centered_frequency = [value - frequency_mean for value in interpolated_frequency]
    max_frequency_deviation = max(abs(value) for value in centered_frequency) or 1
    frequency_overlay = []
    for iso_date, deviation in zip(dates, centered_frequency):
        frequency_overlay.append({
            "time": iso_date,
            "value": round(chart_mid + chart_half_range * deviation / max_frequency_deviation, 2),
        })

    payload = {
        "machine": int(machine),
        "dates": dates,
        "values": values,
        "candles": candles,
        "components": components,
        "frequency_overlay": frequency_overlay,
        "spectrum": spectrum,
        "peaks": [
            {"rank": i + 1, "period": round(p["period"], 2), "amplitude": round(p["amplitude"], 1)}
            for i, p in enumerate(peaks)
        ],
        "mean": round(avg, 1),
    }
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    out = DOCS_DIR / f"machine{int(machine)}_fourier.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(HTML.replace("__DATA__", data), encoding="utf-8")
    print(out)
    print(f"days={len(values)} mean={avg:.1f}")
    for p in payload["peaks"]:
        print(f"rank={p['rank']} period={p['period']:.2f} operating-days amplitude={p['amplitude']:.1f}")


HTML = r'''<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Machine 39 Fourier Analysis</title>
<script src="https://unpkg.com/lightweight-charts@4.1.7/dist/lightweight-charts.standalone.production.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
*{box-sizing:border-box}body{margin:0;background:#0d1117;color:#c9d1d9;font-family:'Segoe UI',Meiryo,sans-serif;font-size:13px}
header{padding:14px 18px;background:#161b22;border-bottom:1px solid #30363d}h1{font-size:17px;color:#58a6ff;margin:0 0 6px}.meta{color:#8b949e;font-size:12px}
main{padding:14px;display:grid;gap:14px}.panel{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:12px}.panel h2{font-size:13px;color:#58a6ff;margin:0 0 10px}
.ohlc{height:390px}.chart{height:340px}.spectrum{height:300px}.summary{display:grid;grid-template-columns:repeat(3,minmax(120px,1fr));gap:8px}.stat{background:#0d1117;border:1px solid #30363d;padding:9px}.stat b{display:block;font-size:18px;margin-top:3px}
.layer-controls{display:flex;gap:7px 14px;align-items:center;flex-wrap:wrap;margin:0 0 10px;padding:8px 10px;background:#0d1117;border:1px solid #30363d}.layer-toggle{display:flex;align-items:center;gap:6px;cursor:pointer;white-space:nowrap}.layer-toggle input{margin:0}.swatch{width:18px;height:3px;display:inline-block}
.wave{padding:12px 0;border-bottom:1px solid #30363d}.wave:last-child{border-bottom:0}.wave-head{display:flex;gap:18px;align-items:baseline;flex-wrap:wrap;margin-bottom:7px}.wave-title{color:#c9d1d9;font-weight:700}.formula{color:#8b949e;font-family:Consolas,monospace}.wave-chart{height:145px}
table{width:100%;border-collapse:collapse}th,td{padding:7px 9px;border-bottom:1px solid #30363d;text-align:right}th:first-child,td:first-child{text-align:left}th{color:#8b949e}.note{color:#8b949e;line-height:1.7}
@media(max-width:720px){.summary{grid-template-columns:1fr}.ohlc,.chart,.spectrum{height:280px}}
</style></head><body>
<header><h1>Machine 39 Fourier Analysis</h1><div class="meta" id="meta"></div></header>
<main>
<section class="panel"><h2>Machine 39 daily OHLC + separated Fourier cycles</h2><div class="layer-controls" id="layer-controls"></div><div class="ohlc" id="ohlc"></div></section>
<section class="summary" id="summary"></section>
<section class="panel"><h2>Frequency spectrum</h2><div class="spectrum"><canvas id="spectrum"></canvas></div></section>
<section class="panel"><h2>Dominant periods</h2><table><thead><tr><th>Rank</th><th>Period (operating days)</th><th>Amplitude</th></tr></thead><tbody id="peaks"></tbody></table></section>
<section class="panel"><h2>Separated sine components</h2><div id="components"></div></section>
<section class="panel note">The transform uses daily final balance, removes the mean, and applies a discrete Fourier transform. Periods are measured in operating days because holidays are omitted. Peaks describe repeated components in historical data; they do not predict the next win.</section>
</main>
<script>
const D=__DATA__;
document.getElementById('meta').textContent=`${D.dates[0]} to ${D.dates.at(-1)} / ${D.dates.length} operating days`;
document.getElementById('summary').innerHTML=`<div class="stat">Machine<b>${D.machine}</b></div><div class="stat">Daily mean<b>${Math.round(D.mean).toLocaleString()}</b></div><div class="stat">Top period<b>${D.peaks[0]?.period ?? '-'} days</b></div>`;
document.getElementById('peaks').innerHTML=D.peaks.map(p=>`<tr><td>${p.rank}</td><td>${p.period.toFixed(2)}</td><td>${Math.round(p.amplitude).toLocaleString()}</td></tr>`).join('');
document.getElementById('components').innerHTML=D.components.map(c=>`<div class="wave"><div class="wave-head"><span class="wave-title">#${c.rank} / ${c.period.toFixed(2)} operating days</span><span class="formula">y(t) = ${c.amplitude.toLocaleString()} sin(2πt / ${c.period.toFixed(2)} + ${c.phase_degrees.toFixed(1)}°)</span></div><div class="wave-chart"><canvas id="component-${c.rank}"></canvas></div></div>`).join('');
const componentColors=['#f59e0b','#58a6ff','#22c55e','#f85149','#d2a8ff'];
const frequencyColor='#f0f6fc';
document.getElementById('layer-controls').innerHTML=`<label class="layer-toggle"><input type="checkbox" data-layer="frequency" checked><span class="swatch" style="background:${frequencyColor}"></span><span>Frequency spectrum</span></label>`+D.components.map((c,i)=>`<label class="layer-toggle"><input type="checkbox" data-rank="${c.rank}"><span class="swatch" style="background:${componentColors[i%componentColors.length]}"></span><span>#${c.rank} ${c.period.toFixed(2)} days</span></label>`).join('');
const ohlcContainer=document.getElementById('ohlc');
const ohlcChart=LightweightCharts.createChart(ohlcContainer,{width:ohlcContainer.clientWidth,height:ohlcContainer.clientHeight,layout:{background:{color:'#161b22'},textColor:'#8b949e'},grid:{vertLines:{color:'#21262d'},horzLines:{color:'#21262d'}},crosshair:{mode:LightweightCharts.CrosshairMode.Normal},rightPriceScale:{borderColor:'#30363d'},timeScale:{borderColor:'#30363d',timeVisible:false}});
const candles=ohlcChart.addCandlestickSeries({upColor:'#26a69a',downColor:'#ef5350',borderVisible:false,wickUpColor:'#26a69a',wickDownColor:'#ef5350'});
candles.setData(D.candles);
const frequencySeries=ohlcChart.addLineSeries({color:frequencyColor,lineWidth:2,title:'Frequency spectrum',lastValueVisible:false,priceLineVisible:false,crosshairMarkerVisible:true});
frequencySeries.setData(D.frequency_overlay);
const overlaySeries=new Map();
D.components.forEach((c,i)=>{
  const series=ohlcChart.addLineSeries({color:componentColors[i%componentColors.length],lineWidth:2,title:`#${c.rank} ${c.period.toFixed(2)}d`,lastValueVisible:false,priceLineVisible:false,crosshairMarkerVisible:true});
  series.setData([]);
  overlaySeries.set(c.rank,{series,data:c.overlay_values});
});
document.getElementById('layer-controls').addEventListener('change',event=>{
  const input=event.target.closest('input');
  if(!input)return;
  if(input.dataset.layer==='frequency'){
    frequencySeries.setData(input.checked?D.frequency_overlay:[]);
    return;
  }
  if(!input.dataset.rank)return;
  const layer=overlaySeries.get(Number(input.dataset.rank));
  layer.series.setData(input.checked?layer.data:[]);
});
ohlcChart.timeScale().fitContent();
new ResizeObserver(()=>ohlcChart.applyOptions({width:ohlcContainer.clientWidth,height:ohlcContainer.clientHeight})).observe(ohlcContainer);
const common={responsive:true,maintainAspectRatio:false,interaction:{mode:'index',intersect:false},plugins:{legend:{labels:{color:'#c9d1d9'}}},scales:{x:{ticks:{color:'#8b949e',maxTicksLimit:14},grid:{color:'#21262d'}},y:{ticks:{color:'#8b949e'},grid:{color:'#21262d'}}}};
new Chart(document.getElementById('spectrum'),{type:'line',data:{labels:D.spectrum.map(x=>x.period),datasets:[{label:'Amplitude',data:D.spectrum.map(x=>x.amplitude),borderColor:'#22c55e',backgroundColor:'#22c55e22',fill:true,pointRadius:0,borderWidth:1.5}]},options:{...common,scales:{x:{type:'logarithmic',ticks:{color:'#8b949e'},grid:{color:'#21262d'},title:{display:true,text:'Period (operating days)',color:'#8b949e'}},y:{ticks:{color:'#8b949e'},grid:{color:'#21262d'}}}}});
D.components.forEach((c,i)=>new Chart(document.getElementById(`component-${c.rank}`),{type:'line',data:{labels:D.dates,datasets:[{label:`${c.period.toFixed(2)} days`,data:c.values,borderColor:componentColors[i%componentColors.length],borderWidth:1.5,pointRadius:0,fill:false}]},options:{...common,plugins:{legend:{display:false}},scales:{x:{ticks:{color:'#8b949e',maxTicksLimit:10},grid:{color:'#21262d'}},y:{suggestedMin:-c.amplitude,suggestedMax:c.amplitude,ticks:{color:'#8b949e',maxTicksLimit:5},grid:{color:'#21262d'}}}}}));
</script></body></html>'''


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--machine", type=int, default=39)
    parser.add_argument("--top", type=int, default=5)
    args = parser.parse_args()
    generate(args.machine, args.top)
