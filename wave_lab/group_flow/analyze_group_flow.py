"""Validate 20260829 大海5 group-flow inputs without changing production logic.

The script uses structured PSCUBE history for initial-hit events and reuses the
existing SVG axis/point parser plus ``analyze.py.px_to_val`` for intraday values.
All synchronized values use a causal previous-value hold; no future point is
used to fill an earlier timestamp.
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import math
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import analyze  # noqa: E402
from analyze_pscube import (  # noqa: E402
    build_axes_and_points_from_svg,
    parse_history_rows,
    parse_svg_labels,
    read_html_text,
    t2m,
)

DATE = "20260829"
MACHINES = [f"{n:04d}" for n in range(39, 78)]
CAPTURE = ROOT / "data" / "local_capture" / DATE / "morning"
OHLC = ROOT / "csv" / "daily_ohlc" / DATE / f"{DATE}_daily_ohlc.csv"
OUT = ROOT / "wave_lab" / "group_flow" / "output" / DATE


def load_groups() -> dict[str, list[str]]:
    path = ROOT / "machine_master.csv"
    groups: dict[str, list[str]] = defaultdict(list)
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            machine = f"{int(row['machine']):04d}"
            if machine in MACHINES:
                groups[f"g{int(row['group'])}"].append(machine)
    return {key: sorted(value) for key, value in sorted(groups.items(), key=lambda item: int(item[0][1:]))}


def load_ohlc() -> dict[str, dict[str, int | None]]:
    result: dict[str, dict[str, int | None]] = {}
    with OHLC.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            machine = f"{int(row['Machine']):04d}"
            if machine in MACHINES:
                result[machine] = {
                    key: int(float(row[key])) if row.get(key, "").strip() else None
                    for key in ("Open", "High", "Low", "Close")
                }
    return result


def load_initial_hits(machine: str) -> list[dict]:
    path = CAPTURE / "history" / f"{machine}_history.csv"
    rows = parse_history_rows(path) if path.exists() else []
    hits = []
    for row in rows:
        if len(row) < 4 or row[3].strip() != "初当り":
            continue
        if not re.fullmatch(r"\d{1,2}:\d{2}", row[1].strip()):
            continue
        hits.append({
            "date": DATE,
            "time": row[1].strip(),
            "minute": t2m(row[1].strip()),
            "machine": machine,
            "event_type": "initial_hit",
            "source": "PSCUBE history CSV status=初当り",
            "confidence": "source_status_exact",
            "ambiguity": "none_for_status_classification",
            "hit_sequence_id": row[0],
            "start_count": row[2],
        })
    return sorted(hits, key=lambda item: (item["minute"], item["hit_sequence_id"]))


def load_history_events(machine: str) -> list[dict]:
    """Read every time-valid history row, preserving the original status."""
    path = CAPTURE / "history" / f"{machine}_history.csv"
    rows = parse_history_rows(path) if path.exists() else []
    events = []
    for row in rows:
        if len(row) < 4 or not re.fullmatch(r"\d{1,2}:\d{2}", row[1].strip()):
            continue
        status = row[3].strip()
        event_type = {"初当り": "INITIAL", "継続": "CONTINUATION"}.get(status, "OTHER")
        events.append({
            "date": DATE,
            "time": row[1].strip(),
            "minute": t2m(row[1].strip()),
            "machine": machine,
            "event_type": event_type,
            "source_status": status,
            "source": "PSCUBE history CSV",
            "confidence": "source_status_exact" if event_type != "OTHER" else "status_not_in_phase2_classes",
            "hit_sequence_id": row[0],
            "start_count": row[2],
        })
    return sorted(events, key=lambda item: (item["minute"], item["machine"], item["hit_sequence_id"]))


def time_mapper(svg_path: Path):
    labels = []
    for x, _y, label in parse_svg_labels(read_html_text(svg_path)):
        label = label.strip()
        if re.fullmatch(r"\d{1,2}:\d{2}", label):
            labels.append((t2m(label), x))
    labels = sorted(set(labels))
    if len(labels) < 2:
        raise ValueError("SVG time labels are insufficient")

    def mapper(x: float) -> float:
        if x <= labels[0][1]:
            left, right = labels[0], labels[1]
        elif x >= labels[-1][1]:
            left, right = labels[-2], labels[-1]
        else:
            for left, right in zip(labels, labels[1:]):
                if left[1] <= x <= right[1]:
                    break
        return left[0] + (x - left[1]) * (right[0] - left[0]) / (right[1] - left[1])

    return mapper, labels


def read_svg_series(machine: str) -> tuple[list[dict], list[dict]]:
    path = CAPTURE / "svg" / f"{machine}.svg"
    axes, points = build_axes_and_points_from_svg(path, DATE)
    mapper, labels = time_mapper(path)
    raw = []
    for x, y in points:
        minute = max(0, round(mapper(x)))
        raw.append({"minute": minute, "time": minute_to_time(minute), "value": analyze.px_to_val(y, axes), "x_px": x, "y_px": y})
    raw.sort(key=lambda item: (item["minute"], item["x_px"]))
    return raw, [{"time": minute_to_time(minute), "minute": minute} for minute, _x in labels]


def minute_to_time(minute: int) -> str:
    return f"{minute // 60:02d}:{minute % 60:02d}"


def sync_series(raw: list[dict], timeline: list[int]) -> list[dict]:
    if not raw:
        return []
    index = 0
    current = raw[0]["value"]
    synced = []
    for minute in timeline:
        while index < len(raw) and raw[index]["minute"] <= minute:
            current = raw[index]["value"]
            index += 1
        synced.append({"time": minute_to_time(minute), "minute": minute, "value": current})
    return synced


def mean(values):
    return round(sum(values) / len(values), 2) if values else None


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def density_rows(hits: list[dict], window: int) -> list[dict]:
    counts = Counter((hit["minute"] // window) * window for hit in hits)
    if not counts:
        return []
    start, end = min(counts), max(counts)
    return [{"time": minute_to_time(minute), "minute": minute, "window_minutes": window, "hit_count": counts.get(minute, 0)} for minute in range(start, end + 1, window)]


def fixed_density(events: list[dict], timeline: list[int]) -> list[dict]:
    """Return fixed forward bins from each 5-minute cursor, without leakage."""
    result = []
    for minute in timeline:
        row = {"time": minute_to_time(minute), "minute": minute}
        for window in (5, 10, 30):
            selected = [event for event in events if minute <= event["minute"] < minute + window]
            row[f"initial_{window}m"] = sum(event["event_type"] == "INITIAL" for event in selected)
            row[f"continuation_{window}m"] = sum(event["event_type"] == "CONTINUATION" for event in selected)
            row[f"total_{window}m"] = row[f"initial_{window}m"] + row[f"continuation_{window}m"]
        result.append(row)
    return result


def phase2_html(summary: dict, density: list[dict], activity: list[dict], payout: list[dict], groups: list[str]) -> str:
    data = json.dumps({"summary": summary, "density": density, "activity": activity, "payout": payout}, ensure_ascii=False).replace("</", "<\\/")
    return f"""<!doctype html><html lang='ja'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>大海5 日中Group Flow Phase 2</title><style>
body{{font-family:system-ui,sans-serif;background:#0f172a;color:#e5e7eb;margin:18px}}h1,h2{{color:#93c5fd}}.note{{background:#172033;border-left:4px solid #60a5fa;padding:10px;line-height:1.55}}.controls{{position:sticky;top:0;background:#111827;padding:12px;z-index:2}}input{{width:min(760px,90vw)}}table{{border-collapse:collapse;margin:12px 0 26px;font-size:13px}}th,td{{border:1px solid #374151;padding:5px 8px;text-align:right}}th{{background:#1f2937}}td:first-child,th:first-child{{text-align:left}}svg{{width:100%;height:300px;background:#111827;border-radius:8px}}.legend span{{display:inline-block;margin-right:12px}}.dot{{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:4px}}#selected{{background:#172033;padding:10px;min-height:30px}}
</style></head><body><h1>大海5 日中Group Flow / Phase 2</h1><div class='note'>初当りと継続をPSCUBE historyのstatusで分離した探索表示です。Hit ActivityとPayout Movementは別概念であり、因果・綱引き・エネルギー移動は判定していません。</div><div class='controls'><label>Selected time: <b id='time'>--</b></label><br><input id='slider' type='range' min='0' max='0' value='0'></div><h2>Island Hit Activity</h2><div id='activityChart'></div><div id='selected'></div><h2>Group Hit Activity</h2><div id='activityTable'></div><h2>Group Payout Flow</h2><div id='payoutChart'></div><h2>All39 Total</h2><div id='all39'></div><h2>Fixed 30-minute bins</h2><div id='densityTable'></div><script>
const DATA={data}; const groups={json.dumps(groups)}; const slider=document.getElementById('slider');
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
function lineSvg(rows, keys, colors, label){{if(!rows.length)return '';let vals=rows.flatMap(r=>keys.map(k=>Number(r[k]||0))),lo=Math.min(...vals),hi=Math.max(...vals),span=Math.max(1,hi-lo),w=1100,h=280;let xy=(i,v)=>`${{40+i*(w-60)/Math.max(1,rows.length-1)}},${{20+(hi-v)*(h-45)/span}}`;let lines=keys.map((k,j)=>`<polyline fill='none' stroke='${{colors[j]}}' stroke-width='${{k==='all39_total'?3:1.5}}' points='${{rows.map((r,i)=>xy(i,Number(r[k]||0))).join(' ')}}'/>`).join('');return `<svg viewBox='0 0 ${{w}} ${{h}}' aria-label='${{label}}'>${{lines}}</svg>`}}
 function render(i){{let d=DATA.density[i]||DATA.density[0],a=DATA.activity.filter(x=>x.minute===d.minute),p=DATA.payout[i]||DATA.payout[0];document.getElementById('time').textContent=d.time;document.getElementById('selected').innerHTML=`<b>${{esc(d.time)}}</b>　initial=${{d.initial_30m}}　continuation=${{d.continuation_30m}}　total=${{d.total_30m}}　all39=${{p.all39_total}}`;document.getElementById('activityChart').innerHTML=lineSvg(DATA.density,['initial_30m','continuation_30m','total_30m'],['#60a5fa','#f472b6','#f8fafc'],'island activity');document.getElementById('payoutChart').innerHTML=lineSvg(DATA.payout,['g1','g2','g3','g4','g5','g6','g7','g8','g9'],['#60a5fa','#f472b6','#34d399','#fbbf24','#a78bfa','#fb7185','#2dd4bf','#c084fc','#f97316'],'group payout');document.getElementById('all39').innerHTML=lineSvg(DATA.payout,['all39_total'],['#f8fafc'],'all39 total');document.getElementById('activityTable').innerHTML='<table><tr><th>group</th><th>initial</th><th>continuation</th><th>total</th><th>group_delta</th></tr>'+a.map(x=>`<tr><td>${{x.group}}</td><td>${{x.initial_count}}</td><td>${{x.continuation_count}}</td><td>${{x.total_hit_count}}</td><td>${{x.group_delta}}</td></tr>`).join('')+'</table>';let rows=DATA.density.slice(Math.max(0,i-2),i+3);document.getElementById('densityTable').innerHTML='<table><tr><th>time</th><th>initial30</th><th>continuation30</th><th>total30</th></tr>'+rows.map(x=>`<tr><td>${{x.time}}</td><td>${{x.initial_30m}}</td><td>${{x.continuation_30m}}</td><td>${{x.total_30m}}</td></tr>`).join('')+'</table>';}}
slider.max=Math.max(0,DATA.density.length-1);slider.addEventListener('input',()=>render(Number(slider.value)));render(0);
</script></body></html>"""


def build_html(summary: dict, group_rows: list[dict], hit_rows: list[dict], density: list[dict], group_timeline: list[dict], event_rows: list[dict]) -> str:
    group_table = "".join(
        f"<tr><td>{html.escape(row['group'])}</td><td>{row['machine_count']}</td><td>{row['initial_hit_count']}</td><td>{row['unique_machines_with_initial_hit']}</td><td>{row['final_total']}</td></tr>"
        for row in group_rows
    )
    hit_table = "".join(f"<tr><td>{i}</td><td>{h['time']}</td><td>{h['machine']}</td><td>{h['group']}</td></tr>" for i, h in enumerate(hit_rows, 1))
    density_table = "".join(f"<tr><td>{row['time']}</td><td>{row['hit_count']}</td></tr>" for row in density)
    chart_width, chart_height = 1100, 360
    groups = [f"g{i}" for i in range(1, 10)]
    chart_values = [value for row in group_timeline for value in [row.get(group, 0) for group in groups] + [row.get("all39_total", 0)]]
    low, high = min(chart_values or [0]), max(chart_values or [1])
    span = max(1, high - low)
    def point(index: int, value: int) -> str:
        x = 40 + index * (chart_width - 60) / max(1, len(group_timeline) - 1)
        y = 20 + (high - value) * (chart_height - 50) / span
        return f"{x:.1f},{y:.1f}"
    colors = ["#60a5fa", "#f472b6", "#34d399", "#fbbf24", "#a78bfa", "#fb7185", "#2dd4bf", "#c084fc", "#f97316"]
    chart_lines = []
    for group, color in zip(groups, colors):
        coords = " ".join(point(i, row[group]) for i, row in enumerate(group_timeline))
        chart_lines.append(f"<polyline fill='none' stroke='{color}' stroke-width='1.5' points='{coords}'/><text x='8' y='{24 + len(chart_lines)*16}' fill='{color}'>{group}</text>")
    all_coords = " ".join(point(i, row["all39_total"]) for i, row in enumerate(group_timeline))
    chart_lines.append(f"<polyline fill='none' stroke='#f8fafc' stroke-width='3' points='{all_coords}'/><text x='8' y='180' fill='#f8fafc'>all39</text>")
    event_marks = "".join(
        f"<circle cx='{40 + max(0, (hit['minute'] - group_timeline[0]['minute'])) * (chart_width - 60) / max(1, group_timeline[-1]['minute'] - group_timeline[0]['minute']):.1f}' cy='12' r='3' fill='#ef4444'><title>{hit['time']} {hit['machine']}</title></circle>"
        for hit in event_rows
        if group_timeline and group_timeline[0]['minute'] <= hit['minute'] <= group_timeline[-1]['minute']
    )
    timeline_chart = f"<svg viewBox='0 0 {chart_width} {chart_height}' role='img' aria-label='group timeline'>{''.join(chart_lines)}{event_marks}</svg>"
    return f"""<!doctype html><html lang='ja'><head><meta charset='utf-8'><title>大海5 Group Flow Phase 1</title><style>
body{{font-family:system-ui,sans-serif;background:#101827;color:#e5e7eb;margin:24px}}h1,h2{{color:#93c5fd}}table{{border-collapse:collapse;margin:12px 0 28px}}th,td{{border:1px solid #374151;padding:6px 10px;text-align:right}}th{{background:#1f2937}}td:first-child,th:first-child{{text-align:left}}.note{{background:#172033;border-left:4px solid #60a5fa;padding:12px;max-width:900px;line-height:1.6}}code{{color:#bfdbfe}}
</style></head><body><h1>大海5 日中Group Flow / Phase 1</h1><div class='note'>探索用の事後検証です。初当たりは構造化historyの <code>status=初当り</code> を使用。差玉は既存SVG軸変換と因果的previous-value holdによる5分同期値です。因果・エネルギー移動・綱引きは判定していません。</div>
<h2>検証サマリー</h2><pre>{html.escape(json.dumps(summary, ensure_ascii=False, indent=2))}</pre>
<h2>Group合計差玉 / 初当たりmarker</h2>{timeline_chart}
<h2>Group別初当たり</h2><table><tr><th>group</th><th>台数</th><th>初当たり</th><th>初当たり台数</th><th>最終合計</th></tr>{group_table}</table>
<h2>島全体 初当たり順</h2><table><tr><th>order</th><th>time</th><th>machine</th><th>group</th></tr>{hit_table}</table>
<h2>30分 初当たり密度</h2><table><tr><th>time</th><th>hit_count</th></tr>{density_table}</table></body></html>"""


def run_phase2(events: list[dict], groups: dict[str, list[str]], ohlc: dict, timeline: list[int], group_timeline: list[dict], group_rows: list[dict], phase1_summary: dict) -> dict:
    """Generate the status-separated Phase 2 artifacts beside Phase 1 output."""
    for event in events:
        event["group"] = next((group for group, machines in groups.items() if event["machine"] in machines), "")
    status_values = Counter(event["source_status"] for event in events)
    valid_events = [event for event in events if event["event_type"] in {"INITIAL", "CONTINUATION"}]

    machine_rows = []
    for machine in MACHINES:
        machine_events = [event for event in valid_events if event["machine"] == machine]
        initial = sum(event["event_type"] == "INITIAL" for event in machine_events)
        continuation = sum(event["event_type"] == "CONTINUATION" for event in machine_events)
        row = {"machine": machine, "group": next((group for group, members in groups.items() if machine in members), ""), "initial_count": initial, "continuation_count": continuation, "total_hit_count": initial + continuation, "continuation_per_initial": round(continuation / initial, 4) if initial else None}
        row.update({key.lower(): ohlc.get(machine, {}).get(key) for key in ("High", "Low", "Close")})
        machine_rows.append(row)

    group_count_rows = []
    group_band_rows = []
    for group, machines in groups.items():
        group_events = [event for event in valid_events if event["group"] == group]
        initial = sum(event["event_type"] == "INITIAL" for event in group_events)
        continuation = sum(event["event_type"] == "CONTINUATION" for event in group_events)
        group_count_rows.append({"group": group, "machine_count": len(machines), "machines": ",".join(machine[-3:] for machine in machines), "initial_count": initial, "continuation_count": continuation, "total_hit_count": initial + continuation, "continuation_per_initial": round(continuation / initial, 4) if initial else None, "final_group_close": next((row[group] for row in reversed(group_timeline)), None)})
        for band, start, end in (("09-12", 540, 720), ("12-15", 720, 900), ("15-18", 900, 1080), ("18-close", 1080, 1440)):
            initial_band = sum(event["event_type"] == "INITIAL" and start <= event["minute"] < end for event in group_events)
            cont_band = sum(event["event_type"] == "CONTINUATION" and start <= event["minute"] < end for event in group_events)
            group_band_rows.append({"group": group, "time_bin": band, "start_time": minute_to_time(start), "end_time": minute_to_time(end), "initial_count": initial_band, "continuation_count": cont_band, "total_hit_count": initial_band + cont_band, "continuation_per_initial": round(cont_band / initial_band, 4) if initial_band else None})

    density = fixed_density(valid_events, timeline)
    activity_rows = []
    for minute, payout in zip(timeline, group_timeline):
        for group in groups:
            bucket = [event for event in valid_events if event["group"] == group and minute <= event["minute"] < minute + 5]
            activity_rows.append({"time": minute_to_time(minute), "minute": minute, "group": group, "initial_count": sum(event["event_type"] == "INITIAL" for event in bucket), "continuation_count": sum(event["event_type"] == "CONTINUATION" for event in bucket), "total_hit_count": len(bucket), "group_value": payout[group]})

    vs_delta_rows = []
    step = 30 // 5
    for index in range(0, len(timeline), step):
        minute = timeline[index]
        end_index = min(len(timeline) - 1, index + step)
        end_minute = timeline[end_index]
        for group, machines in groups.items():
            bucket = [event for event in valid_events if event["group"] == group and minute <= event["minute"] < minute + 30]
            start_value = group_timeline[index][group]
            end_value = group_timeline[end_index][group]
            initial = sum(event["event_type"] == "INITIAL" for event in bucket)
            continuation = sum(event["event_type"] == "CONTINUATION" for event in bucket)
            vs_delta_rows.append({"time_bin": minute_to_time(minute), "start_minute": minute, "end_time": minute_to_time(end_minute), "group": group, "initial_count": initial, "continuation_count": continuation, "total_hit_count": initial + continuation, "group_value_start": start_value, "group_value_end": end_value, "group_delta": end_value - start_value})

    event_rows = [{key: event.get(key, "") for key in ("date", "time", "machine", "group", "event_type", "source_status", "source", "confidence", "hit_sequence_id", "start_count")} for event in events]
    fields_events = ["date", "time", "machine", "group", "event_type", "source_status", "source", "confidence", "hit_sequence_id", "start_count"]
    write_csv(OUT / "all_hit_events.csv", event_rows, fields_events)
    write_csv(OUT / "machine_event_counts.csv", machine_rows, ["machine", "group", "initial_count", "continuation_count", "total_hit_count", "continuation_per_initial", "high", "low", "close"])
    write_csv(OUT / "group_event_counts.csv", group_count_rows, ["group", "machine_count", "machines", "initial_count", "continuation_count", "total_hit_count", "continuation_per_initial", "final_group_close"])
    write_csv(OUT / "group_event_time_counts.csv", group_band_rows, ["group", "time_bin", "start_time", "end_time", "initial_count", "continuation_count", "total_hit_count", "continuation_per_initial"])
    write_csv(OUT / "island_hit_density.csv", density, ["time", "minute", *[f"{kind}_{window}m" for window in (5, 10, 30) for kind in ("initial", "continuation", "total")]])
    write_csv(OUT / "group_activity_timeline.csv", activity_rows, ["time", "minute", "group", "initial_count", "continuation_count", "total_hit_count", "group_value"])
    write_csv(OUT / "group_activity_vs_delta.csv", vs_delta_rows, ["time_bin", "start_minute", "end_time", "group", "initial_count", "continuation_count", "total_hit_count", "group_value_start", "group_value_end", "group_delta"])

    def maxima(kind: str, window: int) -> dict:
        key = f"{kind}_{window}m"
        maximum = max((int(row[key]) for row in density), default=0)
        return {"max": maximum, "times": [row["time"] for row in density if int(row[key]) == maximum]}

    phase1_groups = {row["group"]: row for row in group_rows}
    phase1_match = {
        "initial_count": len([event for event in valid_events if event["event_type"] == "INITIAL"]),
        "group_initial_counts": all(next((row["initial_hit_count"] for row in group_rows if row["group"] == group), None) == next((row["initial_count"] for row in group_count_rows if row["group"] == group), None) for group in groups),
        "group_final_close": all(phase1_groups[group]["final_total"] == next(row["final_group_close"] for row in group_count_rows if row["group"] == group) for group in groups),
        "all39_final": phase1_summary.get("all39_final"),
    }
    summary = {
        "date": DATE, "machines": len(MACHINES), "history_files": len(MACHINES), "status_values": dict(status_values),
        "initial_definition": "source_status=初当り -> INITIAL",
        "continuation_definition": "source_status=継続 -> CONTINUATION",
        "ambiguity": "status is separated exactly as labeled; the label alone does not establish physical causality",
        "initial_total": sum(event["event_type"] == "INITIAL" for event in valid_events),
        "continuation_total": sum(event["event_type"] == "CONTINUATION" for event in valid_events),
        "total_hit_activity": len(valid_events), "machines_with_initial": len({event["machine"] for event in valid_events if event["event_type"] == "INITIAL"}),
        "machines_with_continuation": len({event["machine"] for event in valid_events if event["event_type"] == "CONTINUATION"}),
        "timeline": {"start": minute_to_time(timeline[0]), "end": minute_to_time(timeline[-1]), "step_minutes": 5, "points": len(timeline), "interpolation": "previous-value hold"},
        "density_maxima": {kind: {str(window): maxima(kind, window) for window in (5, 10, 30)} for kind in ("initial", "continuation", "total")},
        "phase1_high_density_comparison": {time: next((row for row in density if row["time"] == time), None) for time in ("11:00", "12:30", "13:30", "14:00", "14:30", "19:00")},
        "phase1_match": phase1_match,
        "group_final_close": {row["group"]: row["final_group_close"] for row in group_count_rows},
        "all39_final": phase1_summary.get("all39_final"),
        "sum_group_equals_all39": sum(row["final_group_close"] or 0 for row in group_count_rows) == phase1_summary.get("all39_final"),
    }
    (OUT / "phase2_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "group_flow_phase2.html").write_text(phase2_html(summary, density, activity_rows, group_timeline, list(groups)), encoding="utf-8")
    return summary


def analyze_date() -> dict:
    groups = load_groups()
    ohlc = load_ohlc()
    group_of = {machine: group for group, machines in groups.items() for machine in machines}
    all_hits = []
    all_events = []
    first_hits = []
    raw_series: dict[str, list[dict]] = {}
    svg_labels: dict[str, list[dict]] = {}
    quality = []
    for machine in MACHINES:
        hits = load_initial_hits(machine)
        all_hits.extend(hits)
        all_events.extend(load_history_events(machine))
        if hits:
            first_hits.append(hits[0])
        try:
            raw, labels = read_svg_series(machine)
            raw_series[machine] = raw
            svg_labels[machine] = labels
            svg_status = "success"
        except Exception as exc:  # preserve failure details; do not substitute data
            svg_status = "failed"
            quality.append({"machine": machine, "svg_status": svg_status, "error": str(exc)})
            continue
        quality.append({"machine": machine, "svg_status": svg_status, "raw_points": len(raw), "history_initial_hits": len(hits)})

    all_hits.sort(key=lambda item: (item["minute"], item["machine"], item["hit_sequence_id"]))
    for order, hit in enumerate(first_hits, 1):
        hit["first_hit_order"] = order
    first_hits.sort(key=lambda item: (item["minute"], item["machine"]))
    for order, hit in enumerate(first_hits, 1):
        hit["first_hit_order"] = order
    for hit in all_hits:
        hit["group"] = group_of.get(hit["machine"], "")

    raw_minutes = {point["minute"] for series in raw_series.values() for point in series}
    # The display timeline starts at the documented 09:00 boundary. Any SVG
    # points before that boundary remain available in raw_svg_points.csv but do
    # not silently extend the synchronized analysis window.
    last_minute = max(raw_minutes or [9 * 60])
    timeline = sorted(set([minute for minute in range(9 * 60, math.ceil(last_minute / 5) * 5 + 1, 5)] + [last_minute]))
    synced = {machine: sync_series(raw_series[machine], timeline) for machine in raw_series}
    values = {machine: {item["minute"]: item["value"] for item in series} for machine, series in synced.items()}
    group_rows = []
    time_bands = (("09-12", 9 * 60, 12 * 60), ("12-15", 12 * 60, 15 * 60), ("15-18", 15 * 60, 18 * 60), ("18-close", 18 * 60, 24 * 60))
    group_time_rows = []
    group_timeline = []
    for minute in timeline:
        row = {"time": minute_to_time(minute), "minute": minute}
        for group, machines in groups.items():
            row[group] = sum(values.get(machine, {}).get(minute, 0) for machine in machines)
        row["all39_total"] = sum(row[group] for group in groups)
        group_timeline.append(row)
    for group, machines in groups.items():
        group_hits = [hit for hit in all_hits if hit["group"] == group]
        final_total = group_timeline[-1][group] if group_timeline else None
        row = {"group": group, "machines": ",".join(machine[-3:] for machine in machines), "machine_count": len(machines), "initial_hit_count": len(group_hits), "unique_machines_with_initial_hit": len({hit["machine"] for hit in group_hits}), "final_total": final_total}
        for band, start, end in time_bands:
            row[f"{band}_initial_hit_count"] = sum(1 for hit in group_hits if start <= hit["minute"] < end)
        group_rows.append(row)
        group_time_rows.extend({"group": group, "time_band": band, "start_time": minute_to_time(start), "end_time": minute_to_time(end if end < 24 * 60 else 23 * 59), "initial_hit_count": sum(1 for hit in group_hits if start <= hit["minute"] < end)} for band, start, end in time_bands)

    delta_rows = []
    for index, row in enumerate(group_timeline):
        out = {"time": row["time"], "minute": row["minute"]}
        for group in groups:
            for window in (5, 10, 30):
                prior = group_timeline[index - window // 5][group] if index >= window // 5 else None
                out[f"{group}_{window}min_delta"] = row[group] - prior if prior is not None else None
        delta_rows.append(out)

    raw_rows = []
    for machine, series in raw_series.items():
        for point in series:
            raw_rows.append({"machine": machine, **point})
    machine_rows = [{"machine": machine, **point} for machine, series in synced.items() for point in series]
    first_rows = []
    for order, hit in enumerate(first_hits, 1):
        first_rows.append({"order": order, "time": hit["time"], "machine": hit["machine"], "group": hit["group"], "source": hit["source"]})
    first_by_machine = {hit["machine"]: hit for hit in first_hits}
    machine_first_rows = []
    for machine in MACHINES:
        hit = first_by_machine.get(machine)
        machine_first_rows.append({
            "machine": machine,
            "group": group_of.get(machine, ""),
            "first_hit_time": hit["time"] if hit else "",
            "first_hit_order": hit["first_hit_order"] if hit else "",
            "source": hit["source"] if hit else "no qualifying history event",
        })
    initial_rows = [{key: hit.get(key, "") for key in ("date", "time", "machine", "group", "event_type", "source", "confidence", "ambiguity", "hit_sequence_id", "start_count")} for hit in all_hits]

    exact = []
    mismatch = []
    for machine in MACHINES:
        svg_final = values.get(machine, {}).get(timeline[-1]) if machine in values and timeline else None
        canonical = ohlc.get(machine, {}).get("Close")
        item = {"machine": machine, "svg_final": svg_final, "canonical_close": canonical, "difference": svg_final - canonical if svg_final is not None and canonical is not None else None}
        (exact if item["difference"] == 0 else mismatch).append(item)

    summary = {
        "date": DATE, "machines": len(MACHINES), "groups": len(groups), "svg_success": len(raw_series), "svg_failed": len(MACHINES) - len(raw_series),
        "history_initial_hit_total": len(all_hits), "machines_with_initial_hit": len({hit["machine"] for hit in all_hits}),
        "initial_hit_definition": "history CSV status=初当り; 継続 is not counted as a new initial hit",
        "ambiguity": "history status distinguishes 初当り/継続; it cannot independently prove the physical causal meaning of the label",
        "time_mapping": "SVG embedded time labels, linearly mapped between adjacent labels",
        "sync": "common timeline from parsed SVG points, 5-minute grid, causal previous-value hold",
        "time_start": minute_to_time(timeline[0]) if timeline else None, "time_end": minute_to_time(timeline[-1]) if timeline else None, "time_points": len(timeline),
        "canonical_check": {"checked": len(MACHINES), "exact_match": len(exact), "mismatch": len(mismatch), "details": mismatch},
        "group_final_sum": sum(row["final_total"] or 0 for row in group_rows), "all39_final": group_timeline[-1]["all39_total"] if group_timeline else None,
        "sum_group_equals_all39": sum(row["final_total"] or 0 for row in group_rows) == (group_timeline[-1]["all39_total"] if group_timeline else None),
        "quality": quality,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    write_csv(OUT / "first_hit_order.csv", first_rows, ["order", "time", "machine", "group", "source"])
    write_csv(OUT / "machine_first_hits.csv", machine_first_rows, ["machine", "group", "first_hit_time", "first_hit_order", "source"])
    write_csv(OUT / "all_initial_hits.csv", initial_rows, ["date", "time", "machine", "group", "event_type", "source", "confidence", "ambiguity", "hit_sequence_id", "start_count"])
    write_csv(OUT / "group_hit_counts.csv", group_rows, ["group", "machines", "machine_count", "initial_hit_count", "unique_machines_with_initial_hit", "09-12_initial_hit_count", "12-15_initial_hit_count", "15-18_initial_hit_count", "18-close_initial_hit_count", "final_total"])
    write_csv(OUT / "group_hit_time_counts.csv", group_time_rows, ["group", "time_band", "start_time", "end_time", "initial_hit_count"])
    density = density_rows(all_hits, 30)
    density_all = [{"window": window, "time": row["time"], "minute": row["minute"], "hit_count": row["hit_count"]} for window in (5, 10, 30) for row in density_rows(all_hits, window)]
    write_csv(OUT / "hit_density.csv", density_all, ["window", "time", "minute", "hit_count"])
    write_csv(OUT / "raw_svg_points.csv", raw_rows, ["machine", "time", "minute", "value", "x_px", "y_px"])
    write_csv(OUT / "machine_timeline.csv", machine_rows, ["machine", "time", "minute", "value"])
    write_csv(OUT / "group_timeline.csv", group_timeline, ["time", "minute", *groups.keys(), "all39_total"])
    write_csv(OUT / "group_delta_timeline.csv", delta_rows, ["time", "minute", *[f"{group}_{window}min_delta" for group in groups for window in (5, 10, 30)]])
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "group_flow_phase1.html").write_text(build_html(summary, group_rows, first_rows, density, group_timeline, all_hits), encoding="utf-8")
    phase2_summary = run_phase2(all_events, groups, ohlc, timeline, group_timeline, group_rows, summary)
    print(json.dumps({"summary": summary, "first_hit_order": first_rows, "output": str(OUT)}, ensure_ascii=False, indent=2))
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=DATE)
    args = parser.parse_args()
    DATE = args.date
    CAPTURE = ROOT / "data" / "local_capture" / DATE / "morning"
    OHLC = ROOT / "csv" / "daily_ohlc" / DATE / f"{DATE}_daily_ohlc.csv"
    OUT = ROOT / "wave_lab" / "group_flow" / "output" / DATE
    analyze_date()
