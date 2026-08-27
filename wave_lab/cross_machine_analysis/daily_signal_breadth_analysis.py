"""大海5 39台のシグナルbreadthと翌日全体陽線率の探索分析。"""
from __future__ import annotations
import csv, math, statistics, sys
from collections import Counter, defaultdict
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]; BASE=Path(__file__).resolve().parent; OUT=BASE/"output"; TRACK=BASE/"tracking"
MACHINES=[f"{i:03d}" for i in range(39,78)]
sys.path.insert(0,str(ROOT))
from wave_lab.cross_machine_analysis.oomi5_holdout_validation import build_machine  # noqa: E402
from wave_lab.fft_reconstruct import load_machine_rows  # noqa: E402
SIGNALS={"UP_UP_UP":"signal_up_up_up","RIGHT":"signal_right","LOW_CONVERGENCE_RIGHT":"signal_low_convergence_right","DOWN_DOWN_DOWN":"signal_down_down_down"}
MAIN=list(SIGNALS)
def write(path,rows):
    if not rows:return
    fields=[]
    for r in rows:
        for k in r:
            if k not in fields:fields.append(k)
    with path.open("w",encoding="utf-8-sig",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)
def avg(x):return statistics.mean(x) if x else None
def med(x):return statistics.median(x) if x else None
def stdev(x):return statistics.stdev(x) if len(x)>1 else 0.0 if x else None
def q(x,p):
    if not x:return None
    a=sorted(x);z=(len(a)-1)*p;i=math.floor(z);j=math.ceil(z)
    return a[i] if i==j else a[i]+(a[j]-a[i])*(z-i)
def pearson(x,y):
    if len(x)<2:return None
    a=avg(x);b=avg(y);den=math.sqrt(sum((u-a)**2 for u in x)*sum((v-b)**2 for v in y));return sum((u-a)*(v-b) for u,v in zip(x,y))/den if den else None
def spearman(x,y):
    def ranks(a):
        o=sorted(range(len(a)),key=lambda i:a[i]);r=[0.0]*len(a);i=0
        while i<len(a):
            j=i
            while j+1<len(a) and a[o[j+1]]==a[o[i]]:j+=1
            z=(i+j)/2+1
            for k in range(i,j+1):r[o[k]]=z
            i=j+1
        return r
    return pearson(ranks(x),ranks(y)) if len(x)>=2 else None
def stats_row(rs,scope,label):
    n=len(rs);b=sum(bool(r["next_day_bullish"]) for r in rs);cl=[r["next_close"] for r in rs]
    return {"scope":scope,"label":label,"n":n,"bullish_count":b,"bullish_rate":b/n if n else None,"close_mean":avg(cl),"close_median":med(cl),"close_min":min(cl) if cl else None,"close_max":max(cl) if cl else None,"high_mean":avg([r["next_high"] for r in rs]),"low_mean":avg([r["next_low"] for r in rs])}
def main():
    OUT.mkdir(parents=True,exist_ok=True);TRACK.mkdir(parents=True,exist_ok=True)
    rows=[];quality=[]
    for m in MACHINES:
        rs,qinfo=build_machine(m);quality.append(qinfo)
        daily_by_date={r["date"]:r for r in load_machine_rows(m,"2026-08-26")}
        for r in rs:
            rr=dict(r);target=daily_by_date[rr["next_date"]]
            rr["next_day_bullish"]=bool(rr["next_day_bullish"])
            rr.update({"next_open":target["open"],"next_high":target["high"],"next_low":target["low"],"next_close":target["close"]})
            rows.append(rr)
    dates=sorted({r["date"] for r in rows});bydate=defaultdict(list)
    for r in rows:bydate[r["date"]].append(r)
    daily=[];tracking=[]
    for d in dates:
        rs=bydate[d]; first=rs[0]; nd=first["next_date"]
        counts={s:sum(1 for r in rs if (r["wave_direction_pattern"]=="DOWN-DOWN-DOWN" if s=="DOWN_DOWN_DOWN" else bool(r[SIGNALS[s]]))) for s in MAIN+['DOWN_DOWN_DOWN']}
        scores=Counter(int(r["signal_score"]) for r in rs);next_b=sum(r["next_day_bullish"] for r in rs); closes=[r["next_close"] for r in rs]
        row={"signal_date":d,"next_date":nd,**{k+"_count":v for k,v in counts.items()},"ANY_SIGNAL_count":sum(1 for r in rs if int(r["signal_score"])>0),"ALL_3_count":scores[3],"SCORE_1_count":scores[1],"SCORE_2_count":scores[2],"SCORE_3_count":scores[3],"NO_SIGNAL_count":scores[0],"next_day_bullish_count":next_b,"next_day_bullish_rate":next_b/len(rs),"next_day_Close_mean":avg(closes),"next_day_Close_median":med(closes),"direction_balance":counts["UP_UP_UP"]-counts["DOWN_DOWN_DOWN"]}
        daily.append(row);tracking.append({**row,"evaluated":True})
    write(OUT/"daily_signal_counts.csv",daily);write(TRACK/"daily_signal_tracking.csv",tracking);write(OUT/"daily_signal_data_quality.csv",quality)
    breadth=[]
    for name in ["UP_UP_UP","RIGHT","LOW_CONVERGENCE_RIGHT","ANY_SIGNAL","ALL_3"]:
        key=name+"_count";v=[r[key] for r in daily];counts=Counter(v);breadth.append({"metric":name,"days":len(v),"min_count":min(v),"max_count":max(v),"mean_count":avg(v),"median_count":med(v),"std_count":stdev(v),"p25":q(v,.25),"p75":q(v,.75),"mode_count":counts.most_common(1)[0][0]})
        for lo,hi,label in [(0,2,"0-2"),(3,4,"3-4"),(5,6,"5-6"),(7,9,"7-9"),(10,999,"10+")]:
            if name=="UP_UP_UP" or name=="ALL_3": breadth.append({"metric":name,"band":label,"days":sum(lo<=x<=hi for x in v),"rate":sum(lo<=x<=hi for x in v)/len(v)})
    # Daily market strength distribution.
    rates=[r["next_day_bullish_rate"] for r in daily];market={"metric":"next_day_bullish_rate","min":min(rates),"max":max(rates),"mean":avg(rates),"median":med(rates),"std":stdev(rates),"p25":q(rates,.25),"p75":q(rates,.75)}
    for lo,hi,label in [(0,.2,"<20%"),(.2,.3,"20-30%"),(.3,.4,"30-40%"),(.4,.5,"40-50%"),(.5,.6,"50-60%"),(.6,1.01,"60%+")]:market[label+"_days"]=sum(lo<=x<hi for x in rates);market[label+"_rate"]=market[label+"_days"]/len(rates)
    # Correlations, fixed group comparisons, and count-group summaries.
    corr=[]
    for key in ["UP_UP_UP_count","RIGHT_count","LOW_CONVERGENCE_RIGHT_count","ANY_SIGNAL_count","ALL_3_count","SCORE_2_count","SCORE_3_count","direction_balance","DOWN_DOWN_DOWN_count"]:
        x=[r[key] for r in daily];corr.append({"metric":key,"pearson":pearson(x,rates),"spearman":spearman(x,rates)})
    group=[]
    def daily_group(rows,label):
        return {"group":label,"days":len(rows),"mean_signal_count":avg([r["ANY_SIGNAL_count"] for r in rows]),"next_day_bullish_count_mean":avg([r["next_day_bullish_count"] for r in rows]),"next_day_bullish_rate_mean":avg([r["next_day_bullish_rate"] for r in rows]),"next_day_bullish_rate_median":med([r["next_day_bullish_rate"] for r in rows])}
    for lo,hi,label in [(0,5,"0-5"),(6,10,"6-10"),(11,15,"11-15"),(16,20,"16-20"),(21,999,"21+")]:group.append(daily_group([r for r in daily if lo<=r["ANY_SIGNAL_count"]<=hi],label))
    upgroups=[]
    for lo,hi,label in [(0,2,"0-2"),(3,4,"3-4"),(5,6,"5-6"),(7,9,"7-9"),(10,999,"10+")]:
        ds=[r for r in daily if lo<=r["UP_UP_UP_count"]<=hi];upgroups.append({"group":label,"days":len(ds),"all39_bullish_rate_mean":avg([r["next_day_bullish_rate"] for r in ds]),"all39_bullish_rate_median":med([r["next_day_bullish_rate"] for r in ds]),"all39_close_mean":avg([r["next_day_Close_mean"] for r in ds])})
    pooled=[]
    for label,ds in [("Original4",[r for r in rows if r["machine"] in {"049","056","075","077"}]),("Holdout35",[r for r in rows if r["machine"] not in {"049","056","075","077"}]),("All39",rows)]:
        for s,key in [("ANY_SIGNAL","signal_score"),("NO_SIGNAL","signal_score")]:
            rr=[r for r in ds if (int(r[key])>0 if s=="ANY_SIGNAL" else int(r[key])==0)];pooled.append(stats_row(rr,label,s))
    score=[]
    for k in range(4):score.append(stats_row([r for r in rows if int(r["signal_score"])==k],"All39",f"score{k}"))
    # Strong/weak days, plus ALL_3 1-2 day observation.
    extremes=[]
    for label,p in [("bullish_rate>=50%",lambda x:x>=.5),("bullish_rate>=60%",lambda x:x>=.6),("bullish_rate>=70%",lambda x:x>=.7),("bullish_rate<=30%",lambda x:x<=.3),("bullish_rate<=20%",lambda x:x<=.2)]:
        ds=[r for r in daily if p(r["next_day_bullish_rate"])]
        extremes.append({"group":label,"days":len(ds),"UP_UP_UP_mean":avg([r["UP_UP_UP_count"] for r in ds]),"RIGHT_mean":avg([r["RIGHT_count"] for r in ds]),"LOW_CONVERGENCE_RIGHT_mean":avg([r["LOW_CONVERGENCE_RIGHT_count"] for r in ds]),"ANY_SIGNAL_mean":avg([r["ANY_SIGNAL_count"] for r in ds]),"ALL_3_mean":avg([r["ALL_3_count"] for r in ds]),"DOWN_DOWN_DOWN_mean":avg([r["DOWN_DOWN_DOWN_count"] for r in ds])})
    all3days=[r for r in daily if 1<=r["ALL_3_count"]<=2];target=[r for r in rows if any(d["signal_date"]==r["date"] and 1<=d["ALL_3_count"]<=2 for d in daily) and int(r["signal_score"])==3]
    all3summary=stats_row(target,"All39","ALL_3 on days count 1-2");all3summary["days"]=len(all3days);all3summary["all3_count_total"]=sum(r["ALL_3_count"] for r in all3days)
    write(OUT/"daily_signal_breadth_stats.csv",breadth);write(OUT/"daily_signal_market_strength.csv",[market]);write(OUT/"daily_signal_correlation.csv",corr);write(OUT/"daily_signal_any_breadth_groups.csv",group);write(OUT/"daily_signal_up_breadth_groups.csv",upgroups);write(OUT/"daily_signal_pooled_signal_vs_nosignal.csv",pooled);write(OUT/"daily_signal_score_stats.csv",score);write(OUT/"daily_signal_strong_weak_days.csv",extremes);write(OUT/"daily_signal_all3_1to2.csv",[all3summary])
    make_html(daily,breadth,market,corr,group,upgroups,pooled,score,extremes,all3summary)
    print("signal_days=%d paired_samples=%d"%(len(daily),len(rows)))
    print("all3_1to2_days=%d target_samples=%d"%(len(all3days),len(target)))
def make_html(daily,breadth,market,corr,group,upgroups,pooled,score,extremes,all3):
    def tab(rs,cols):
        z="<table><tr>"+"".join(f"<th>{c}</th>" for c in cols)+"</tr>"
        for r in rs:z+="<tr>"+"".join(f"<td>{f'{r.get(c):.3f}' if isinstance(r.get(c),float) else r.get(c,'')}</td>" for c in cols)+"</tr>"
        return z+"</table>"
    text="<!doctype html><meta charset='utf-8'><title>Daily Signal Breadth</title><style>body{font-family:Arial;max-width:1500px;margin:25px auto}table{border-collapse:collapse;margin:8px 0 24px}th,td{border:1px solid #bbb;padding:5px 7px;font-size:12px}th{background:#eef}.note{background:#fff8dc;padding:12px}</style><h1>大海5 39台 日別Signal Breadth</h1><div class='note'>前日signal点灯台数と翌営業日39台全体OHLCの探索分析。signal定義・threshold・predictionは変更なし。</div>"
    text+="<h2>Daily signal counts</h2>"+tab(breadth,["metric","band","days","min_count","max_count","mean_count","median_count","std_count","p25","p75","mode_count","rate"])
    text+="<h2>Market strength</h2>"+tab([market],["metric","min","max","mean","median","std","p25","p75","30-40%_days","30-40%_rate"])
    text+="<h2>Correlations</h2>"+tab(corr,["metric","pearson","spearman"])
    text+="<h2>ANY_SIGNAL breadth</h2>"+tab(group,["group","days","mean_signal_count","next_day_bullish_count_mean","next_day_bullish_rate_mean","next_day_bullish_rate_median"])
    text+="<h2>UP_UP_UP breadth</h2>"+tab(upgroups,["group","days","all39_bullish_rate_mean","all39_bullish_rate_median","all39_close_mean"])
    text+="<h2>Signal vs no signal / score</h2>"+tab(pooled+score,["scope","label","n","bullish_count","bullish_rate","close_mean","close_median","high_mean","low_mean"])
    text+="<h2>Strong and weak days</h2>"+tab(extremes,["group","days","UP_UP_UP_mean","RIGHT_mean","LOW_CONVERGENCE_RIGHT_mean","ANY_SIGNAL_mean","ALL_3_mean","DOWN_DOWN_DOWN_mean"])
    text+="<h2>ALL_3 count 1-2</h2>"+tab([all3],["days","all3_count_total","n","bullish_count","bullish_rate","close_mean","close_median","high_mean","low_mean"])
    text+="<h2>Daily listing</h2>"+tab(daily,["signal_date","next_date","UP_UP_UP_count","RIGHT_count","LOW_CONVERGENCE_RIGHT_count","ANY_SIGNAL_count","ALL_3_count","SCORE_2_count","SCORE_3_count","DOWN_DOWN_DOWN_count","direction_balance","next_day_bullish_count","next_day_bullish_rate","next_day_Close_mean","next_day_Close_median"])
    (OUT/"daily_signal_breadth_analysis.html").write_text(text,encoding="utf-8")
if __name__=="__main__":main()
