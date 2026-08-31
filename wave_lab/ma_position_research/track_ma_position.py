"""MA-position observation tracker for locked Wave Lab Forward candidates.

This is intentionally outside the prediction pipeline.  MA state is computed
with the existing chart series through signal_date only; later canonical OHLC
is outcome data.
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FORWARD = ROOT / "docs/wave_lab/data/forward"
CANONICAL = ROOT / "csv/daily_ohlc"
OUT = ROOT / "wave_lab/ma_position_research/output"
FORWARD_DATES = ["20260828", "20260829", "20260830", "20260831"]
sys.path.insert(0, str(ROOT))
from chart_signal_positive import calc_ma, load_daily_ohlc  # noqa: E402


def compact(value):
    text = str(value).strip()
    if "/" in text or "-" in text:
        parts = text.replace("-", "/").split("/")
        if len(parts) >= 3 and all(part.isdigit() for part in parts[:3]):
            return f"{int(parts[0]):04d}{int(parts[1]):02d}{int(parts[2]):02d}"
    return text[:8]


def forward_obj(date):
    return json.loads((FORWARD / f"{date}.json").read_text(encoding="utf-8"))


def candidate(obj):
    for row in obj.get("strong_groups", []):
        value = str(row.get("candidate_machine", "")).strip()
        if value:
            return f"{int(value):03d}" if value.isdigit() else value
    return None


def rel(left, right):
    if left is None or right is None:
        return ""
    return "ABOVE" if left > right else "BELOW" if left < right else "EQUAL"


def dist(close, ma):
    return None if close is None or ma in (None, 0) else (close - ma) / abs(ma)


def slope_label(value):
    return "UNAVAILABLE" if value is None else "UP" if value > 0 else "DOWN" if value < 0 else "FLAT"


def align(a, b, c):
    if None in (a, b, c):
        return "UNAVAILABLE"
    return "BULLISH_ALIGNMENT" if a > b > c else "BEARISH_ALIGNMENT" if a < b < c else "MIXED_ALIGNMENT"


def outcome(series, date, offset):
    wanted = compact(date)
    dates = [row["date"] for row in series]
    try:
        row = series[dates.index(wanted) + offset]
    except (ValueError, IndexError):
        return {"date": "", "status": "PENDING"}
    return {"date": compact(row["date"]), "open": row["open"], "high": row["high"], "low": row["low"],
            "close": row["close"], "bullish": row["close"] > row["open"], "status": "AVAILABLE"}


def load_canonical_outcomes(machines):
    """Read-only raw daily OHLC for outcomes; never used for MA calculation."""
    result = {machine: [] for machine in machines}
    for path in sorted(CANONICAL.glob("*/*_daily_ohlc.csv")):
        with path.open(encoding="utf-8-sig", newline="") as handle:
            for raw in csv.DictReader(handle):
                machine = str(raw.get("Machine", "")).strip()
                if machine.isdigit():
                    machine = f"{int(machine):03d}"
                if machine not in result:
                    continue
                try:
                    result[machine].append({
                        "date": compact(raw.get("Date", "")),
                        "open": int(float(raw.get("Open", "0"))),
                        "high": int(float(raw.get("High", "0"))),
                        "low": int(float(raw.get("Low", "0"))),
                        "close": int(float(raw.get("Close", "0"))),
                    })
                except (TypeError, ValueError):
                    continue
    for machine in result:
        result[machine].sort(key=lambda row: row["date"])
    return result


def build(signal_date, target_date, machine, series, outcome_series, obj):
    asof = [row for row in series if compact(row["date"]) <= signal_date]
    if not asof or compact(asof[-1]["date"]) != signal_date:
        raise ValueError(f"{machine}: signal_date is missing from canonical OHLC")
    ma5s, ma20s, ma75s = calc_ma(asof, 5), calc_ma(asof, 20), calc_ma(asof, 75)
    i, close = len(asof) - 1, asof[-1]["close"]
    ma5, ma20, ma75 = ma5s[i], ma20s[i], ma75s[i]
    prev = (ma5s[i - 1] if i else None, ma20s[i - 1] if i else None, ma75s[i - 1] if i else None)
    rel75 = rel(close, ma75)
    outs = [outcome(outcome_series, target_date, n) for n in (0, 1, 2)]
    ordered = "UNAVAILABLE" if None in (ma5, ma20, ma75) else ">".join(name for _, name in sorted(((ma5, "MA5"), (ma20, "MA20"), (ma75, "MA75")), reverse=True))
    row = {
        "forward_number": FORWARD_DATES.index(signal_date) + 1, "signal_date": signal_date, "target_date": target_date, "machine": machine,
        "close_asof": close, "ma5": ma5, "ma20": ma20, "ma75": ma75,
        "close_minus_ma5": None if ma5 is None else close - ma5, "close_minus_ma20": None if ma20 is None else close - ma20, "close_minus_ma75": None if ma75 is None else close - ma75,
        "close_vs_ma5": rel(close, ma5), "close_vs_ma20": rel(close, ma20), "close_vs_ma75": rel75,
        "ma75_position": {"ABOVE": "BELOW_PRICE", "BELOW": "ABOVE_PRICE", "EQUAL": "AT_PRICE"}.get(rel75, "UNAVAILABLE"),
        "ma5_distance": dist(close, ma5), "ma20_distance": dist(close, ma20), "ma75_distance": dist(close, ma75), "ma_alignment": align(ma5, ma20, ma75), "ma_order": ordered,
        "ma5_slope": None if ma5 is None or prev[0] is None else ma5 - prev[0], "ma20_slope": None if ma20 is None or prev[1] is None else ma20 - prev[1], "ma75_slope": None if ma75 is None or prev[2] is None else ma75 - prev[2],
        "ma5_slope_label": slope_label(None if ma5 is None or prev[0] is None else ma5 - prev[0]), "ma20_slope_label": slope_label(None if ma20 is None or prev[1] is None else ma20 - prev[1]), "ma75_slope_label": slope_label(None if ma75 is None or prev[2] is None else ma75 - prev[2]),
        "target_open": outs[0].get("open", ""), "target_high": outs[0].get("high", ""), "target_low": outs[0].get("low", ""), "target_close": outs[0].get("close", ""), "target_bullish": outs[0].get("bullish", ""), "target_status": outs[0]["status"],
        "plus1_date": outs[1].get("date", ""), "plus1_open": outs[1].get("open", ""), "plus1_high": outs[1].get("high", ""), "plus1_low": outs[1].get("low", ""), "plus1_close": outs[1].get("close", ""), "plus1_bullish": outs[1].get("bullish", ""), "plus1_status": outs[1]["status"],
        "plus2_date": outs[2].get("date", ""), "plus2_open": outs[2].get("open", ""), "plus2_high": outs[2].get("high", ""), "plus2_low": outs[2].get("low", ""), "plus2_close": outs[2].get("close", ""), "plus2_bullish": outs[2].get("bullish", ""), "plus2_status": outs[2]["status"],
        "outcome_status": outs[0]["status"], "ma_input_last_date": signal_date, "future_data_used_for_ma": False, "wave_state_source": str(FORWARD / f"{signal_date}.json"), "wave_state_recomputed": False, "source_forward_status": obj.get("evaluation_status", "pending"),
    }
    return row


def write_csv(path, rows):
    fields = list(rows[0]) if rows else []
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore"); writer.writeheader(); writer.writerows(rows)


def html_page(rows, daily, summary):
    def cells(values): return "<tr>" + "".join(f"<td>{html.escape(str(v if v not in (None, '') else '—'))}</td>" for v in values) + "</tr>"
    body = "".join(cells([r["forward_number"], r["signal_date"], r["target_date"], r["machine"], r["close_asof"], r["ma5"], r["ma20"], r["ma75"], r["close_vs_ma75"], r["ma_alignment"], r["target_close"], r["plus1_close"], r["plus2_close"], r["outcome_status"]]) for r in rows)
    daily_body = "".join(cells([r["forward_number"], r["signal_date"], r["target_date"], r["candidate_machine"] or "—", r["target_status"]]) for r in daily)
    return f"<!doctype html><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>MA Position Research</title><style>body{{font-family:system-ui;background:#0f172a;color:#e5e7eb;margin:24px}}h1,h2{{color:#93c5fd}}.note{{background:#172033;padding:14px;border-left:4px solid #60a5fa}}.wrap{{overflow:auto}}table{{border-collapse:collapse;min-width:900px;margin:12px 0 28px}}th,td{{border:1px solid #374151;padding:7px;font-size:13px}}th{{background:#1f2937}}</style><h1>Wave Lab MA Position Research</h1><p class='note'>Research only / Not used for prediction. MAはsignal_dateまでで計算。candidate samples={summary['candidate_samples']}。現時点ではサンプル数が少ないため結論不可。</p><h2>Candidate observations</h2><div class='wrap'><table><tr><th>Forward</th><th>Signal</th><th>Target</th><th>Machine</th><th>Close</th><th>MA5</th><th>MA20</th><th>MA75</th><th>Close vs MA75</th><th>Alignment</th><th>Target Close</th><th>+1 Close</th><th>+2 Close</th><th>Status</th></tr>{body}</table></div><h2>Daily Forward coverage</h2><div class='wrap'><table><tr><th>Forward</th><th>Signal</th><th>Target</th><th>Candidate</th><th>Target status</th></tr>{daily_body}</table></div><p>MA source: chart_signal_positive.calc_ma. Slope=current MA−previous business-day MA. Distance=(Close−MA)/abs(MA).</p>"


def run():
    parser = argparse.ArgumentParser(); parser.add_argument("--max-signal-date", default="20260831"); args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    forwards = [(d, forward_obj(d)) for d in FORWARD_DATES if d <= args.max_signal_date]
    candidates = {candidate(obj) for _, obj in forwards}; candidates.discard(None)
    loaded_series, _ = load_daily_ohlc(candidates)
    # daily_ohlc's loader may return integer-like keys; research output uses
    # the project's canonical three-digit machine identifiers.
    series_by_machine = {f"{int(machine):03d}": series for machine, series in loaded_series.items()}
    outcome_by_machine = load_canonical_outcomes(set(series_by_machine))
    rows, daily = [], []
    for signal_date, obj in forwards:
        target_date, machine = compact(obj["target_date"]), candidate(obj)
        daily.append({"forward_number": FORWARD_DATES.index(signal_date) + 1, "signal_date": signal_date, "target_date": target_date, "candidate_machine": machine or "", "no_candidate": machine is None, "target_status": "AVAILABLE" if target_date <= args.max_signal_date else "PENDING"})
        if machine:
            rows.append(build(signal_date, target_date, machine, series_by_machine[machine], outcome_by_machine[machine], obj))
    write_csv(OUT / "ma_position_tracking.csv", rows); write_csv(OUT / "ma_position_daily_outcomes.csv", daily)
    summary = {"research_only": True, "candidate_samples": len(rows), "forward_days": len(daily), "ma_definition_source": "chart_signal_positive.calc_ma: rolling average of daily Close", "slope_definition": "current MA minus previous business-day MA", "distance_definition": "(Close - MA) / abs(MA)", "future_data_used_for_ma": False, "target_data_used_for_ma": False, "20260827_generated": False, "rows": rows, "daily_outcomes": daily}
    (OUT / "ma_position_tracking.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "ma_position_summary.html").write_text(html_page(rows, daily, summary), encoding="utf-8")
    print(json.dumps({"candidate_samples": len(rows), "daily_rows": len(daily), "output": str(OUT)}, ensure_ascii=False))


if __name__ == "__main__":
    run()
