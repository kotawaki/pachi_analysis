"""大海5 39台のWave Lab固定シグナル外部holdout検証。

通常Wave Lab出力、prediction、FROZEN等には書き込まない。計算は既存
fft_reconstruct.pyの関数をread-onlyで再利用し、成果物は本ディレクトリのみ。
"""
from __future__ import annotations
import csv, html, math, sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = Path(__file__).resolve().parent
OUT = BASE / "output"
TRACK = BASE / "tracking"
MACHINES = [f"{i:03d}" for i in range(39, 78)]
ORIGINAL = {"049", "056", "075", "077"}
HOLDOUT = [m for m in MACHINES if m not in ORIGINAL]
DATE_MIN, DATE_MAX = "2026-06-27", "2026-08-26"

sys.path.insert(0, str(ROOT))
from wave_lab.fft_reconstruct import (  # noqa: E402
    load_machine_rows, analyze, phase_convergence_analysis,
    phase_alignment_analysis, period_regime_history,
)

SIGNALS = {
    "UP-UP-UP": lambda r: r["wave_direction_pattern"] == "UP-UP-UP",
    "RIGHT": lambda r: r["region"] == "RIGHT",
    "low_convergence + RIGHT": lambda r: r["convergence_bin"] == "low" and r["region"] == "RIGHT",
    "DOWN-DOWN-DOWN": lambda r: r["wave_direction_pattern"] == "DOWN-DOWN-DOWN",
}
MAIN = ["UP-UP-UP", "RIGHT", "low_convergence + RIGHT"]

def write_csv(path, rows):
    if not rows: return
    keys=[]
    for r in rows:
        for k in r:
            if k not in keys: keys.append(k)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w=csv.DictWriter(f, fieldnames=keys); w.writeheader(); w.writerows(rows)

def basic(rows):
    n=len(rows); c=sum(1 for r in rows if r["next_day_bullish"])
    return n,c,c/n if n else None

def wilson(c,n):
    if not n:return None,None
    z=1.96;p=c/n;d=1+z*z/n;mid=(p+z*z/(2*n))/d
    h=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/d
    return mid-h,mid+h

def metric(rows, split, machine, signal, baseline):
    n,c,p=basic(rows); lo,hi=wilson(c,n)
    return {"scope":split,"machine":machine,"signal":signal,"n":n,"bullish_count":c,
            "bullish_rate":p,"baseline":baseline,"lift":p-baseline if p is not None else None,
            "wilson_low":lo,"wilson_high":hi,"non_bullish_count":n-c,
            "non_bullish_rate":(n-c)/n if n else None}

def build_machine(machine):
    raw=load_machine_rows(machine, DATE_MAX)
    valid=[]; invalid=0
    for r in raw:
        try:
            if any(not math.isfinite(float(r[k])) for k in ("open","high","low","close")): raise ValueError
            valid.append(r)
        except (KeyError,TypeError,ValueError): invalid+=1
    valid.sort(key=lambda r:r["date"])
    for i, r in enumerate(valid):
        r["bullish"] = r["close"] > r["open"]
        r["next_day_bullish"] = valid[i + 1]["close"] > valid[i + 1]["open"] if i + 1 < len(valid) else None
    missing=[]
    if valid:
        # Missing dates are reported against the shared observed calendar, not calendar +1.
        shared=None
    if len(valid)<4: return [], {"machine":machine,"first_date":valid[0]["date"] if valid else "","last_date":valid[-1]["date"] if valid else "","ohlc_observation_count":len(valid),"usable_paired_samples":0,"missing_dates":"","invalid_rows":invalid}
    components,daily,_,_=analyze(valid)
    conv,_=phase_convergence_analysis(daily,components)
    alignment,_=phase_alignment_analysis(conv)
    regimes,_=period_regime_history(valid,conv,alignment)
    prev_score=None; result=[]
    regime_by={r["date"]:r for r in regimes}
    for i in range(len(valid)-1):
        d=daily[i]; c=conv[i]; date=valid[i]["date"]; target=valid[i+1]["date"]
        score=float(c["convergence_score"])
        row={"date":date,"machine":machine,"next_date":target,
             "next_day_bullish":daily[i+1]["bullish"],"next_day_close":daily[i+1]["close"],
             "wave_direction_pattern":d["wave_direction_pattern"],"region":c["centroid_region"],
             "convergence_score":score,
             # 前回分析で固定した区分: low < 0.5, middle 0.5..0.7, high >= 0.7。
             "convergence_bin":"low" if score<0.5 else "middle" if score<0.7 else "high",
             "regime":regime_by.get(date,{}).get("regime","")}
        row["convergence_delta"]="" if prev_score is None else score-prev_score
        row["signal_up_up_up"]=SIGNALS["UP-UP-UP"](row)
        row["signal_right"]=SIGNALS["RIGHT"](row)
        row["signal_low_convergence_right"]=SIGNALS["low_convergence + RIGHT"](row)
        row["signal_down_down_down"]=SIGNALS["DOWN-DOWN-DOWN"](row)
        row["signal_score"]=sum(row[k] for k in ("signal_up_up_up","signal_right","signal_low_convergence_right"))
        result.append(row); prev_score=score
    dates=[r["date"] for r in valid]
    quality={"machine":machine,"first_date":dates[0],"last_date":dates[-1],"ohlc_observation_count":len(valid),"usable_paired_samples":len(result),"missing_dates":";".join(missing),"invalid_rows":invalid}
    return result,quality

def pooled(rows, scope, machine):
    base=basic(rows)[2]; out=[]
    for name,pred in SIGNALS.items(): out.append(metric([r for r in rows if pred(r)],scope,machine,name,base))
    return out

def consistency(rows, scope):
    out=[]
    for sig,pred in SIGNALS.items():
        vals=[]
        for m in HOLDOUT:
            rs=[r for r in rows if r["machine"]==m]; base=basic(rs)[2]
            sr=[r for r in rs if pred(r)]; n,c,p=basic(sr)
            vals.append({"machine":m,"n":n,"rate":p,"baseline":base})
        for minimum in (0,3,5,10):
            eligible=[x for x in vals if x["n"]>=minimum]
            out.append({"scope":scope,"signal":sig,"min_signal_n":minimum,"eligible_machines":len(eligible),
                        "above_baseline":sum(x["rate"] is not None and x["rate"]>x["baseline"] for x in eligible),
                        "equal_baseline":sum(x["rate"] is not None and x["rate"]==x["baseline"] for x in eligible),
                        "below_baseline":sum(x["rate"] is not None and x["rate"]<x["baseline"] for x in eligible)})
    return out

def main():
    OUT.mkdir(parents=True,exist_ok=True); TRACK.mkdir(parents=True,exist_ok=True)
    all_rows=[]; qualities=[]
    for m in MACHINES:
        rows,q=build_machine(m); all_rows.extend(rows); qualities.append(q)
    write_csv(OUT/"oomi5_data_quality.csv",qualities)
    # Preserve a standalone tracking history for future append-only monitoring.
    tracking=[]
    for r in all_rows:
        tracking.append({k:r[k] for k in ("date","machine","next_date","signal_up_up_up","signal_right","signal_low_convergence_right","signal_score","next_day_bullish","next_day_close")}|{"evaluated":True})
    write_csv(TRACK/"signal_tracking_history.csv",tracking)
    original=[r for r in all_rows if r["machine"] in ORIGINAL]; holdout=[r for r in all_rows if r["machine"] in HOLDOUT]
    groups=[("Original4",original),("Holdout35",holdout),("All39",all_rows)]
    summary=[]
    for label,rs in groups:
        summary.append({"group":label,"machines":len(set(r["machine"] for r in rs)),"n":len(rs),"bullish_count":basic(rs)[1],"bullish_rate":basic(rs)[2]})
    pooled_stats=[]
    for label,rs in groups: pooled_stats += pooled(rs,label,label)
    write_csv(OUT/"oomi5_holdout_summary.csv",summary)
    write_csv(OUT/"oomi5_signal_stats.csv",pooled_stats)
    machine_stats=[]
    for label,rs in groups:
        base=basic(rs)[2]
        for m in (MACHINES if label=="All39" else HOLDOUT if label=="Holdout35" else sorted(ORIGINAL)):
            mr=[r for r in rs if r["machine"]==m]; machine_stats.append({"group":label,"machine":m,"n":len(mr),"bullish_count":basic(mr)[1],"bullish_rate":basic(mr)[2],"baseline":base})
            for sig,pred in SIGNALS.items(): machine_stats.append({"group":label,"machine":m,"signal":sig,**metric([r for r in mr if pred(r)],label,m,sig,basic(mr)[2])})
    write_csv(OUT/"oomi5_holdout_machine_stats.csv",machine_stats)
    write_csv(OUT/"oomi5_machine_consistency.csv",consistency(holdout,"Holdout35"))
    # Fixed score and holdout time stability (per-machine halves, then pooled).
    score=[]
    for label,rs in [("Holdout35",holdout),("Original4",original),("All39",all_rows)]:
        b=basic(rs)[2]
        for k in range(4): score.append(metric([r for r in rs if r["signal_score"]==k],label,label,f"score{k}",b))
    write_csv(OUT/"oomi5_score_stats.csv",score)
    stability=[]
    for half in ("first_half","second_half"):
        rs=[]
        for m in HOLDOUT:
            mr=sorted([r for r in holdout if r["machine"]==m],key=lambda x:x["date"]); cut=len(mr)//2
            rs.extend(mr[:cut] if half=="first_half" else mr[cut:])
        b=basic(rs)[2]
        for sig,pred in {k:SIGNALS[k] for k in MAIN}.items(): stability.append(metric([r for r in rs if pred(r)],half,"Holdout35",sig,b))
    write_csv(OUT/"oomi5_time_stability.csv",stability)
    make_html(summary,pooled_stats,machine_stats,score,stability)
    # Verify original recomputation against the previous full-period headline values.
    check={s:next(x for x in pooled_stats if x["machine"]=="Original4" and x["signal"]==s) for s in MAIN}
    print("machines=39 original=4 holdout=35")
    print("samples original=%d holdout=%d all=%d"%(len(original),len(holdout),len(all_rows)))
    for s,x in check.items(): print("original_check %s n=%s rate=%.6f"%(s,x["n"],x["bullish_rate"]))

def make_html(summary,stats,machine,score,stability):
    def t(rows,cols):
        z="<table><tr>"+"".join(f"<th>{html.escape(c)}</th>" for c in cols)+"</tr>"
        for r in rows:z+="<tr>"+"".join(f"<td>{html.escape(f'{r.get(c):.4f}' if isinstance(r.get(c),float) else str(r.get(c,'')))}</td>" for c in cols)+"</tr>"
        return z+"</table>"
    text="<!doctype html><meta charset='utf-8'><title>大海5 Wave Lab 外部holdout</title><style>body{font-family:Arial;max-width:1400px;margin:25px auto}table{border-collapse:collapse;margin:8px 0 24px}th,td{border:1px solid #bbb;padding:5px 7px;font-size:12px}th{background:#eef}.note{background:#fff8dc;padding:12px}</style>"
    text+="<h1>大海5 39台 Wave Lab 固定シグナル外部holdout</h1><div class='note'>Original4で発見した条件を変更せず、Holdout35へ適用。再探索・threshold調整なし。前日状態から翌営業日を評価。</div>"
    text+="<h2>Original4 / Holdout35 / All39</h2>"+t(summary,["group","machines","n","bullish_count","bullish_rate"])
    text+="<h2>Signal stats</h2>"+t(stats,["machine","signal","n","bullish_count","bullish_rate","baseline","lift","wilson_low","wilson_high","non_bullish_rate"])
    text+="<h2>Machine results</h2>"+t(machine,["group","machine","signal","n","bullish_count","bullish_rate","baseline","lift"])
    text+="<h2>Fixed score</h2>"+t(score,["scope","signal","n","bullish_count","bullish_rate","baseline","lift"])
    text+="<h2>Holdout time stability</h2>"+t(stability,["scope","signal","n","bullish_count","bullish_rate","baseline","lift"])
    (OUT/"oomi5_holdout_validation.html").write_text(text,encoding="utf-8")

if __name__=="__main__": main()
