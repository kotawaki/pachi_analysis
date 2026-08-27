"""固定Wave Labシグナル翌日のLow帯・陽線・反発幅分析（read-only）。"""
from __future__ import annotations
import csv, math, statistics, sys
from collections import defaultdict
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]; BASE=Path(__file__).resolve().parent; OUT=BASE/"output"
MACHINES=[f"{i:03d}" for i in range(39,78)]; ORIGINAL={"049","056","075","077"}; HOLDOUT=[m for m in MACHINES if m not in ORIGINAL]
sys.path.insert(0,str(ROOT))
from wave_lab.fft_reconstruct import load_machine_rows  # noqa: E402
from wave_lab.cross_machine_analysis.oomi5_holdout_validation import build_machine  # noqa: E402

SIGNALS={"UP_UP_UP":"signal_up_up_up","RIGHT":"signal_right","LOW_CONVERGENCE_RIGHT":"signal_low_convergence_right","DOWN_DOWN_DOWN":"signal_down_down_down"}
MAIN=["UP_UP_UP","RIGHT","LOW_CONVERGENCE_RIGHT"]
BANDS=["A: Low >= -3000","B: -5000 <= Low < -3000","C: -10000 <= Low < -5000","D: Low < -10000"]
def read(path):
    with path.open(encoding="utf-8-sig",newline="") as f:return list(csv.DictReader(f))
def truth(v):return str(v).lower() in {"true","1","yes"}
def values(rs,key):return [float(r[key]) for r in rs if r.get(key) not in (None,"")]
def quant(v,p):
    if not v:return None
    x=sorted(v);z=(len(x)-1)*p;a=math.floor(z);b=math.ceil(z)
    return x[a] if a==b else x[a]+(x[b]-x[a])*(z-a)
def avg(v):return statistics.mean(v) if v else None
def med(v):return statistics.median(v) if v else None
def pearson(x,y):
    if len(x)<2:return None
    mx,my=avg(x),avg(y);a=sum((u-mx)*(v-my) for u,v in zip(x,y));b=math.sqrt(sum((u-mx)**2 for u in x)*sum((v-my)**2 for v in y))
    return a/b if b else None
def spearman(x,y):
    if len(x)<2:return None
    def rank(v):
        order=sorted(range(len(v)),key=lambda i:v[i]);out=[0.0]*len(v);i=0
        while i<len(v):
            j=i
            while j+1<len(v) and v[order[j+1]]==v[order[i]]:j+=1
            r=(i+j)/2+1
            for k in range(i,j+1):out[order[k]]=r
            i=j+1
        return out
    return pearson(rank(x),rank(y))
def band(r):
    low=r["next_low"]
    if low>=-3000:return BANDS[0]
    if low>=-5000:return BANDS[1]
    if low>=-10000:return BANDS[2]
    return BANDS[3]
def write(path,rows):
    if not rows:return
    fields=[]
    for r in rows:
        for k in r:
            if k not in fields:fields.append(k)
    with path.open("w",encoding="utf-8-sig",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)
def stat_row(rs,scope,signal,band_name="ALL",sample_type="all_signal"):
    n=len(rs); bull=sum(r["next_day_bullish"] for r in rs)
    out={"scope":scope,"signal":signal,"low_band":band_name,"sample_type":sample_type,"n":n,"bullish_count":bull,"bullish_rate":bull/n if n else None,"non_bullish_count":n-bull,"non_bullish_rate":(n-bull)/n if n else None}
    for key in ("next_close","next_high","next_low","intraday_range","rebound_from_low"):
        v=values(rs,key)
        for suffix,fn in (("mean",avg),("median",med),("min",min),("max",max)):
            out[key+"_"+suffix]=fn(v) if v else None
    return out
def main():
    OUT.mkdir(parents=True,exist_ok=True)
    # Read prior tracking artifact for provenance; paired features are rebuilt
    # with the exact existing Wave Lab functions because the old tracking CSV
    # does not contain next-day High/Low or the DOWN contrast flag.
    tracking=read(BASE/"tracking"/"signal_tracking_history.csv")
    rows=[];quality=[]
    for m in MACHINES:
        generated,q=build_machine(m);quality.append(q)
        daily_by_date={r["date"]:r for r in load_machine_rows(m,"2026-08-26")}
        for r in generated:
            g=dict(r)
            g["next_day_bullish"]=bool(g["next_day_bullish"])
            target=daily_by_date[g["next_date"]]
            g.update({"next_open":target["open"],"next_high":target["high"],"next_low":target["low"],"next_close":target["close"]})
            g["intraday_range"]=float(g["next_high"])-float(g["next_low"])
            g["rebound_from_low"]=float(g["next_close"])-float(g["next_low"])
            g["low_band"]=band(g)
            rows.append(g)
    groups=[("Original4",[r for r in rows if r["machine"] in ORIGINAL]),("Holdout35",[r for r in rows if r["machine"] in HOLDOUT]),("All39",rows)]
    summary=[]; wall=[]; deep=[]; dist=[]; corr=[]; machine=[]; overlap=[]
    for scope,rs in groups:
        for sig,key in SIGNALS.items():
            sigrows=[r for r in rs if (r["wave_direction_pattern"]=="DOWN-DOWN-DOWN" if sig=="DOWN_DOWN_DOWN" else truth(r[key]))]
            for b in BANDS: wall.append(stat_row([r for r in sigrows if r["low_band"]==b],scope,sig,b,"all_signal"))
            wall.append(stat_row([r for r in sigrows if r["next_low"]>=-5000],scope,sig,"GROUP_1: Low >= -5000","wall_group"))
            wall.append(stat_row([r for r in sigrows if r["next_low"]<-5000],scope,sig,"GROUP_2: Low < -5000","wall_group"))
            s=stat_row([r for r in sigrows if r["next_low"]<-10000],scope,sig,"Low < -10000","deep")
            recovered=[r for r in sigrows if r["next_low"]<-10000 and r["next_close"]>0]
            s.update({"deep_recovery_count":len(recovered),"deep_recovery_rate":len(recovered)/s["n"] if s["n"] else None,"deep_recovery_close_mean":avg(values(recovered,"next_close")),"deep_recovery_close_median":med(values(recovered,"next_close")),"deep_recovery_high_mean":avg(values(recovered,"next_high")),"deep_recovery_low_mean":avg(values(recovered,"next_low")),"deep_recovery_rebound_mean":avg(values(recovered,"rebound_from_low"))});deep.append(s)
            bullish=[r for r in sigrows if r["next_day_bullish"]]
            for b in BANDS:dist.append({"scope":scope,"signal":sig,"low_band":b,"bullish_total":len(bullish),"count":sum(r["low_band"]==b for r in bullish),"rate":sum(r["low_band"]==b for r in bullish)/len(bullish) if bullish else None})
            x=values(sigrows,"next_low");y=values(sigrows,"next_close");corr.append({"scope":scope,"signal":sig,"n":len(x),"low_vs_close_pearson":pearson(x,y),"low_vs_close_spearman":spearman(x,y)})
            summary.append(stat_row(bullish,scope,sig,"ALL","bullish_success"));summary.append(stat_row(sigrows,scope,sig,"ALL","all_signal"))
        # overlaps are observation-only and evaluated on bullish successes.
        preds={"UP-UP-UP only":lambda r:truth(r["signal_up_up_up"]) and not truth(r["signal_right"]) and not truth(r["signal_low_convergence_right"]),"RIGHT only":lambda r:truth(r["signal_right"]) and not truth(r["signal_up_up_up"]) and not truth(r["signal_low_convergence_right"]),"low_convergence + RIGHT":lambda r:truth(r["signal_low_convergence_right"]),"UP-UP-UP + RIGHT":lambda r:truth(r["signal_up_up_up"]) and truth(r["signal_right"]),"UP-UP-UP + low_convergence + RIGHT":lambda r:truth(r["signal_up_up_up"]) and truth(r["signal_low_convergence_right"]),"3 signals all true":lambda r:all(truth(r[k]) for k in ("signal_up_up_up","signal_right","signal_low_convergence_right"))}
        for name,p in preds.items():overlap.append(stat_row([r for r in rs if p(r) and r["next_day_bullish"]],scope,name,"ALL","bullish_success"))
    # Direct -5000 difference table for concise downstream use.
    for scope,rs in groups:
        for sig,key in SIGNALS.items():
            sr=[r for r in rs if (r["wave_direction_pattern"]=="DOWN-DOWN-DOWN" if sig=="DOWN_DOWN_DOWN" else truth(r[key]))]
            a=stat_row([r for r in sr if r["next_low"]>=-5000],scope,sig,"GROUP_1: Low >= -5000","wall_group");b=stat_row([r for r in sr if r["next_low"]<-5000],scope,sig,"GROUP_2: Low < -5000","wall_group")
            wall.append({"scope":scope,"signal":sig,"low_band":"DIFFERENCE","bullish_rate_difference":a["bullish_rate"]-b["bullish_rate"] if a["bullish_rate"] is not None and b["bullish_rate"] is not None else None,"close_mean_difference":a["next_close_mean"]-b["next_close_mean"] if a["next_close_mean"] is not None and b["next_close_mean"] is not None else None})
    for m in MACHINES:
        mr=[r for r in rows if r["machine"]==m]
        for sig,key in SIGNALS.items():
            sr=[r for r in mr if (r["wave_direction_pattern"]=="DOWN-DOWN-DOWN" if sig=="DOWN_DOWN_DOWN" else truth(r[key]))]
            for b in ("GROUP_1: Low >= -5000","GROUP_2: Low < -5000"):
                s=stat_row([r for r in sr if (r["next_low"]>=-5000 if b.startswith("GROUP_1") else r["next_low"]<-5000)],"All39",sig,b,"machine_wall");s["machine"]=m;machine.append(s)
    write(OUT/"signal_low_rebound_summary.csv",summary);write(OUT/"signal_low_band_stats.csv",wall);write(OUT/"signal_deep_recovery_stats.csv",deep);write(OUT/"signal_bullish_low_distribution.csv",dist);write(OUT/"signal_low_correlation_stats.csv",corr);write(OUT/"signal_low_machine_stats.csv",machine);write(OUT/"signal_low_overlap_stats.csv",overlap);write(OUT/"signal_low_data_quality.csv",quality)
    make_html(groups,summary,wall,deep,dist,corr,machine,overlap)
    for sig in MAIN:
        x=next(r for r in summary if r["scope"]=="Holdout35" and r["signal"]==sig and r["sample_type"]=="bullish_success")
        a=next(r for r in wall if r["scope"]=="Holdout35" and r["signal"]==sig and r["low_band"].startswith("GROUP_1"));b=next(r for r in wall if r["scope"]=="Holdout35" and r["signal"]==sig and r["low_band"].startswith("GROUP_2"))
        print(sig,"bullish_n",x["n"],"close_mean",x["next_close_mean"],"low>=-5000",a["bullish_rate"],"low<-5000",b["bullish_rate"])
def make_html(groups,summary,wall,deep,dist,corr,machine,overlap):
    def tab(rs,cols):
        z="<table><tr>"+"".join(f"<th>{c}</th>" for c in cols)+"</tr>"
        for r in rs:z+="<tr>"+"".join(f"<td>{f'{r.get(c):.2f}' if isinstance(r.get(c),float) else r.get(c,'')}</td>" for c in cols)+"</tr>"
        return z+"</table>"
    text="<!doctype html><meta charset='utf-8'><title>Signal Low Rebound Analysis</title><style>body{font-family:Arial;max-width:1500px;margin:25px auto}table{border-collapse:collapse;margin:8px 0 24px}th,td{border:1px solid #bbb;padding:5px 7px;font-size:12px}th{background:#eef}.note{background:#fff8dc;padding:12px}</style><h1>大海5 39台 Signal翌日Low・反発分析</h1><div class='note'>Lowは翌営業日の確定OHLC。予測条件には使用しない。Low帯境界は固定。</div>"
    text+="<h2>Bullish success summary</h2>"+tab([r for r in summary if r["sample_type"]=="bullish_success"],["scope","signal","n","next_close_min","next_close_max","next_close_mean","next_close_median","next_high_mean","next_low_mean","intraday_range_mean","rebound_from_low_mean"])
    text+="<h2>Low bands and -5000 wall</h2>"+tab(wall,["scope","signal","low_band","n","bullish_count","bullish_rate","next_close_mean","next_close_median","next_high_mean","next_low_mean","intraday_range_mean","rebound_from_low_mean","bullish_rate_difference","close_mean_difference"])
    text+="<h2>Deep recovery</h2>"+tab(deep,["scope","signal","n","bullish_count","bullish_rate","next_close_mean","next_close_median","next_high_mean","next_low_mean","deep_recovery_count","deep_recovery_rate","deep_recovery_rebound_mean"])
    text+="<h2>Bullish-success Low distribution</h2>"+tab(dist,["scope","signal","low_band","bullish_total","count","rate"])
    text+="<h2>Low vs Close correlation</h2>"+tab(corr,["scope","signal","n","low_vs_close_pearson","low_vs_close_spearman"])
    text+="<h2>Overlap</h2>"+tab(overlap,["scope","signal","n","next_close_mean","next_close_median","next_high_mean","next_low_mean","intraday_range_mean","rebound_from_low_mean"])
    text+="<h2>Machine wall results</h2>"+tab(machine,["machine","signal","low_band","n","bullish_count","bullish_rate","next_close_mean","next_close_median"])
    (OUT/"signal_low_rebound_analysis.html").write_text(text,encoding="utf-8")
if __name__=="__main__":main()
