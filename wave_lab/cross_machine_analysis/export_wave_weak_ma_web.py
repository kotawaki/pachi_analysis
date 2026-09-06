#!/usr/bin/env python3
"""Build the small Web view for Wave + Weak MA observations."""
from __future__ import annotations

import csv
import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TRACKING = ROOT / "wave_lab/cross_machine_analysis/tracking/wave_weak_ma_prospective.csv"
SUMMARY = ROOT / "wave_lab/cross_machine_analysis/tracking/wave_weak_ma_summary.json"
OUTPUT = ROOT / "docs/wave_weak_ma/index.html"


def rows():
    if not TRACKING.exists():
        return []
    with TRACKING.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def esc(value):
    return html.escape(str(value if value not in (None, "") else "—"))


def build() -> str:
    data = json.loads(SUMMARY.read_text(encoding="utf-8")) if SUMMARY.exists() else {}
    records = rows()
    summary = data or {}
    cards = []
    for row in records:
        actual = row.get("actual_bullish")
        result = "○" if actual == "True" or actual is True else "×" if actual == "False" or actual is False else "—"
        detail = "<details><summary>詳細</summary><dl>" + "".join(
            f"<dt>{esc(label)}</dt><dd>{esc(row.get(field))}</dd>"
            for label, field in (("score", "score"), ("signal close", "signal_close"),
                                 ("MA5", "ma5"), ("MA20", "ma20"), ("MA75", "ma75"),
                                 ("alignment", "alignment"), ("convergence score", "convergence_score"))
        ) + "</dl></details>"
        cards.append(f"<article><h2>{esc(row.get('machine'))} <small>{esc(row.get('group'))}</small></h2>"
                     f"<p>{esc(row.get('signal_date'))} → {esc(row.get('target_date'))} · <b>{esc(row.get('evaluation_status','PENDING')).upper()}</b></p>"
                     f"<p>Wave: UP-UP-UP={esc(row.get('UP_UP_UP'))} / RIGHT={esc(row.get('RIGHT'))} / LOW CONV + RIGHT={esc(row.get('LOW_CONVERGENCE_RIGHT'))}</p>"
                     f"<p>MA direction: {esc(row.get('ma5_direction'))} / {esc(row.get('ma20_direction'))} / {esc(row.get('ma75_direction'))}</p>"
                     f"<p>Close vs MA: {esc(row.get('close_vs_ma5'))} / {esc(row.get('close_vs_ma20'))} / {esc(row.get('close_vs_ma75'))}</p>"
                     f"<p>Actual: {esc(row.get('actual_open'))} / {esc(row.get('actual_high'))} / {esc(row.get('actual_low'))} / {esc(row.get('actual_close'))} · <strong>{result}</strong></p>{detail}</article>")
    return f"""<!doctype html><html lang='ja'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Wave + Weak MA Prospective Observation</title><style>
*{{box-sizing:border-box}}body{{margin:0;background:#0d1117;color:#c9d1d9;font-family:system-ui,Meiryo,sans-serif;padding:24px}}main{{max-width:900px;margin:auto}}a{{color:#58a6ff}}h1{{color:#58a6ff}}h2{{margin:0;color:#f0f6fc}}small{{color:#8b949e}}.note{{background:#172033;border-left:4px solid #58a6ff;padding:14px;margin:16px 0}}.summary,article{{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:15px;margin:12px 0}}.grid{{display:grid;grid-template-columns:repeat(2,1fr);gap:8px}}p{{line-height:1.55;margin:8px 0}}dt{{color:#8b949e;float:left;clear:left;width:150px}}dd{{margin-left:160px}}summary{{cursor:pointer;color:#58a6ff}}@media(max-width:600px){{body{{padding:14px}}.grid{{grid-template-columns:1fr}}dt{{float:none;width:auto}}dd{{margin-left:0}}}}</style></head><body><main>
<p><a href='../index.html'>← pachi_analyze</a></p><h1>Wave + Weak MA</h1><div class='note'><b>Research / validation only</b><br>prediction_use=false<br>Signal/MA state is prospective; actual is attached after evaluation.</div>
<section class='summary'><h2>Summary</h2><div class='grid'><div>Total samples: {esc(summary.get('total_samples'))}</div><div>Evaluated: {esc(summary.get('evaluated_samples'))}</div><div>Pending: {esc(summary.get('pending_samples'))}</div><div>Bullish count: {esc(summary.get('bullish_count'))}</div><div>Bullish rate: {esc(summary.get('bullish_rate'))}</div></div></section>
<h2>Prospective Samples</h2>{''.join(cards) or '<p>データなし</p>'}</main></body></html>"""


def update():
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(build(), encoding="utf-8")
    return OUTPUT


if __name__ == "__main__":
    print(update())
