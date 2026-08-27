"""大海5固定3シグナルの翌日OHLC値幅分析。既存ロジック/出力は変更しない。"""
from __future__ import annotations
import csv, html, math, statistics, sys
from collections import defaultdict
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]; BASE=Path(__file__).resolve().parent
OUT=BASE/"output"; TRACK=BASE/"tracking"
MACHINES=[f"{i:03d}" for i in range(39,78)]; ORIGINAL={"049","056","075","077"}; HOLDOUT=[m for m in MACHINES if m not in ORIGINAL]
sys.path.insert(0,str(ROOT))
from wave_lab.fft_reconstruct import load_machine_rows  # noqa: E402
from wave_lab.cross_machine_analysis.oomi5_holdout_validation import build_machine  # noqa: E402

SIGNAL_KEYS={"UP_UP_UP":"signal_up_up_up","RIGHT":"signal_right","LOW_CONVERGENCE_RIGHT":"signal_low_convergence_right","DOWN_DOWN_DOWN":"signal_down_down_down"}
def read(path):
    with path.open(encoding="utf-8-sig",newline="") as f:return list(csv.DictReader(f))
def truth(v):return str(v).lower() in {"true","1","yes"}
def nums(values):return [float(v) for v in values if v is not None and math.isfinite(float(v))]
def q(v,p):
    if not v:return None
    x=sorted(v); pos=(len(x)-1)*p; lo=math.floor(pos); hi=math.ceil(pos)
    return x[lo] if lo==hi else x[lo]+(x[hi]-x[lo])*(pos-lo)
def summary(values):
    v=nums(values)
    if not v:return {"count":0,"min":"","max":"","mean":"","median":"","std":"","p25":"","p75":""}
    return {"count":len(v),"min":min(v),"max":max(v),"mean":statistics.mean(v),"median":statistics.median(v),"std":statistics.stdev(v) if len(v)>1 else 0.0,"p25":q(v,.25),"p75":q(v,.75)}
def write(path,rows):
    if not rows:return
    fields=[]
    for r in rows:
        for k in r:
            if k not in fields:fields.append(k)
    with path.open("w",encoding="utf-8-sig",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)
def event_stats(rows, event, scope, signal, bullish_only=False):
    rs=[r for r in rows if event(r)]
    if bullish_only:rs=[r for r in rs if r["next_day_bullish"]]
    out={"scope":scope,"signal":signal,"sample_type":"bullish_only" if bullish_only else "all_signal_occurrences","n":len(rs)}
    for key in ("next_open","next_high","next_low","next_close","intraday_range"):
        s=summary([r[key] for r in rs]);out.update({key+"_"+k:s[k] for k in ("min","max","mean","median","std","p25","p75")})
    return out
def threshold_rows(rows, event, scope, signal, field, thresholds):
    sig=[r for r in rows if event(r) and r["next_day_bullish"]]
    result=[]
    for threshold in thresholds:
        n=sum(1 for r in sig if r[field]>=threshold)
        result.append({"scope":scope,"signal":signal,"sample_type":"bullish_only","field":field,"threshold":threshold,"bullish_count":len(sig),"count":n,"rate":n/len(sig) if sig else None})
    return result
def main():
    OUT.mkdir(parents=True,exist_ok=True)
    track=read(TRACK/"signal_tracking_history.csv")
    ohlc={}
    quality=[]
    for m in MACHINES:
        rr=load_machine_rows(m,"2026-08-26");ohlc[m]={r["date"]:r for r in rr}
        quality.append({"machine":m,"first_date":rr[0]["date"] if rr else "","last_date":rr[-1]["date"] if rr else "","ohlc_count":len(rr)})
    rows=[]
    # Rebuild the same paired rows through the existing Wave Lab functions for
    # all 39 machines. The append-oriented tracking file is read as a source
    # cross-check for the original four, but it lacks DOWN and OHLC fields.
    for m in MACHINES:
        generated,_=build_machine(m)
        for g in generated:
            o=ohlc.get(m,{}).get(g["next_date"])
            if not o:continue
            r=dict(g);r.update({"next_open":o["open"],"next_high":o["high"],"next_low":o["low"],"next_close":o["close"],"intraday_range":o["high"]-o["low"]})
            rows.append(r)
    # Signal predicates come only from the precomputed tracking columns.
    predicates={name:(lambda key:lambda r:truth(r[key]))(key) for name,key in SIGNAL_KEYS.items() if key in (track[0] if track else {})}
    # DOWN-DOWN-DOWN is a fixed contrast signal; older tracking rows did not
    # carry its flag, so derive it directly from the existing direction field.
    predicates["DOWN_DOWN_DOWN"] = lambda r: r["wave_direction_pattern"] == "DOWN-DOWN-DOWN"
    groups=[("Original4",[r for r in rows if r["machine"] in ORIGINAL]),("Holdout35",[r for r in rows if r["machine"] in HOLDOUT]),("All39",rows)]
    all_summary=[]; all_occ=[]; close_thr=[]; high_thr=[]; machine=[]; overlap=[]
    for scope,rs in groups:
        for sig,pred in predicates.items():
            all_summary.append(event_stats(rs,pred,scope,sig,True));all_occ.append(event_stats(rs,pred,scope,sig,False))
            close_thr += threshold_rows(rs,pred,scope,sig,"next_close",[0,1000,3000,5000,10000,15000,20000])
            high_thr += threshold_rows(rs,pred,scope,sig,"next_high",[3000,5000,10000,15000,20000,30000])
        # overlap observations, with fixed signal flags and score.
        for label,pred in {
            "UP-UP-UP only":lambda r:truth(r[SIGNAL_KEYS["UP_UP_UP"]]) and not truth(r[SIGNAL_KEYS["RIGHT"]]) and not truth(r[SIGNAL_KEYS["LOW_CONVERGENCE_RIGHT"]]),
            "RIGHT only":lambda r:truth(r[SIGNAL_KEYS["RIGHT"]]) and not truth(r[SIGNAL_KEYS["UP_UP_UP"]]) and not truth(r[SIGNAL_KEYS["LOW_CONVERGENCE_RIGHT"]]),
            "low_convergence + RIGHT":predicates["LOW_CONVERGENCE_RIGHT"],
            "UP-UP-UP + RIGHT":lambda r:truth(r[SIGNAL_KEYS["UP_UP_UP"]]) and truth(r[SIGNAL_KEYS["RIGHT"]]),
            "UP-UP-UP + low_convergence + RIGHT":lambda r:truth(r[SIGNAL_KEYS["UP_UP_UP"]]) and truth(r[SIGNAL_KEYS["LOW_CONVERGENCE_RIGHT"]]),
            "3 signals all true":lambda r:all(truth(r[SIGNAL_KEYS[x]]) for x in ("UP_UP_UP","RIGHT","LOW_CONVERGENCE_RIGHT")),
            "score=1":lambda r:int(r["signal_score"])==1,"score=2":lambda r:int(r["signal_score"])==2,"score=3":lambda r:int(r["signal_score"])==3,
        }.items(): overlap.append(event_stats(rs,pred,scope,label,True))
    # machine-level bullish-success metrics for every machine and all main signals.
    for m in MACHINES:
        mr=[r for r in rows if r["machine"]==m]
        for sig in ("UP_UP_UP","RIGHT","LOW_CONVERGENCE_RIGHT"):
            s=event_stats(mr,predicates[sig],"All39",sig,True);s.update({"machine":m,"signal":sig});machine.append(s)
    write(OUT/"signal_bullish_move_summary.csv",all_summary);write(OUT/"signal_all_occurrence_stats.csv",all_occ)
    write(OUT/"signal_close_threshold_stats.csv",close_thr);write(OUT/"signal_high_threshold_stats.csv",high_thr)
    write(OUT/"signal_bullish_move_machine_stats.csv",machine);write(OUT/"signal_overlap_move_stats.csv",overlap)
    write(OUT/"signal_bullish_move_data_quality.csv",quality)
    make_html(groups,all_summary,all_occ,close_thr,high_thr,overlap,machine)
    print(f"paired_samples={len(rows)} machines={len(set(r['machine'] for r in rows))}")
    for scope,rs in groups:
        print(scope, len(rs), sum(r["next_day_bullish"] for r in rs))
    for sig in ("UP_UP_UP","RIGHT","LOW_CONVERGENCE_RIGHT"):
        x=next(r for r in all_summary if r["scope"]=="Holdout35" and r["signal"]==sig)
        y=next(r for r in all_occ if r["scope"]=="Holdout35" and r["signal"]==sig)
        print(sig,"bullish_n",x["n"],"close_mean",x["next_close_mean"],"close_median",x["next_close_median"],"all_close_mean",y["next_close_mean"])
def make_html(groups,success,occ,close_thr,high_thr,overlap,machine):
    def tab(rs,cols):
        z="<table><tr>"+"".join(f"<th>{html.escape(c)}</th>" for c in cols)+"</tr>"
        for r in rs:z+="<tr>"+"".join(f"<td>{html.escape(f'{r.get(c):.2f}' if isinstance(r.get(c),float) else str(r.get(c,'')))}</td>" for c in cols)+"</tr>"
        return z+"</table>"
    text="<!doctype html><meta charset='utf-8'><title>Wave Lab signal bullish move analysis</title><style>body{font-family:Arial;max-width:1500px;margin:25px auto}table{border-collapse:collapse;margin:8px 0 24px}th,td{border:1px solid #bbb;padding:5px 7px;font-size:12px}th{background:#eef}.note{background:#fff8dc;padding:12px}</style>"
    text+="<h1>大海5 39台：シグナル翌日陽線値幅分析</h1><div class='note'>signal_tracking_history.csvと既存daily OHLCを使用。シグナル定義・threshold・Wave Labロジックは変更なし。</div>"
    text+="<h2>Bullish success summary</h2>"+tab(success,["scope","signal","n","next_close_min","next_close_max","next_close_mean","next_close_median","next_high_mean","next_high_median","next_low_mean","next_low_median","intraday_range_mean"])
    text+="<h2>All signal occurrences</h2>"+tab(occ,["scope","signal","n","next_close_mean","next_close_median","next_close_min","next_close_max"])
    text+="<h2>Close thresholds</h2>"+tab(close_thr,["scope","signal","threshold","bullish_count","count","rate"])
    text+="<h2>High thresholds</h2>"+tab(high_thr,["scope","signal","threshold","bullish_count","count","rate"])
    text+="<h2>Overlap and fixed score</h2>"+tab(overlap,["scope","signal","n","next_close_mean","next_close_median","next_high_mean","next_low_mean","intraday_range_mean"])
    text+="<h2>Machine-level bullish success</h2>"+tab(machine,["machine","signal","n","next_close_mean","next_close_median","next_high_mean","next_low_mean"])
    (OUT/"signal_bullish_move_analysis.html").write_text(text,encoding="utf-8")
if __name__=="__main__":main()
