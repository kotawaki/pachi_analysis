"""Wave Lab 39台を既存グループ単位へ集約する探索分析。"""
from __future__ import annotations
import csv, math, statistics, sys
from collections import defaultdict
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]; BASE=Path(__file__).resolve().parent; OUT=BASE/"output"; TRACK=BASE/"tracking"
MACHINES=[f"{i:03d}" for i in range(39,78)]
GROUPS={"g1":["0046","0055","0064","0073"],"g2":["0047","0056","0065","0074"],"g3":["0039","0048","0057","0066","0075"],"g4":["0040","0049","0058","0067","0076"],"g5":["0041","0050","0059","0068","0077"],"g6":["0042","0051","0060","0069"],"g7":["0043","0052","0061","0070"],"g8":["0044","0053","0062","0071"],"g9":["0045","0054","0063","0072"]}
MACHINE_GROUP={str(int(m)).zfill(3):g for g,ms in GROUPS.items() for m in ms}; sys.path.insert(0,str(ROOT))
from wave_lab.cross_machine_analysis.oomi5_holdout_validation import build_machine  # noqa: E402
from wave_lab.fft_reconstruct import load_machine_rows  # noqa: E402
class FixedSignals(dict):
    def values(self):
        return ()
SIGNALS=FixedSignals({"UP_UP_UP":"signal_up_up_up","RIGHT":"signal_right","LOW_CONVERGENCE_RIGHT":"signal_low_convergence_right"})
SIGNAL_FIELDS=tuple(SIGNALS.values())
def avg(x):return statistics.mean(x) if x else None
def med(x):return statistics.median(x) if x else None
def sd(x):return statistics.stdev(x) if len(x)>1 else 0.0 if x else None
def pearson(x,y):
    if len(x)<2:return None
    a,b=avg(x),avg(y);den=math.sqrt(sum((u-a)**2 for u in x)*sum((v-b)**2 for v in y));return sum((u-a)*(v-b) for u,v in zip(x,y))/den if den else None
def ranks(x):
    o=sorted(range(len(x)),key=lambda i:x[i]);r=[0.0]*len(x);i=0
    while i<len(x):
        j=i
        while j+1<len(x) and x[o[j+1]]==x[o[i]]:j+=1
        z=(i+j)/2+1
        for k in range(i,j+1):r[o[k]]=z
        i=j+1
    return r
def spearman(x,y):return pearson(ranks(x),ranks(y)) if len(x)>=2 else None
def write(path,rows):
    if not rows:return
    fields=[]
    for r in rows:
        for k in r:
            if k not in fields:fields.append(k)
    with path.open("w",encoding="utf-8-sig",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)
def market_stats(rs,label,scope=""):
    n=len(rs);b=sum(r["next_bullish_count"] for r in rs);rates=[r["next_bullish_rate"] for r in rs];cl=[r["next_close_mean"] for r in rs]
    return {"scope":scope,"label":label,"n":n,"bullish_count":b,"bullish_rate":b/(n*1) if n else None,"rate_mean":avg(rates),"rate_median":med(rates),"close_mean":avg(cl),"close_median":med(cl),"any_close_gt_5000_rate":avg([r["next_any_close_gt_5000"] for r in rs]) if rs else None,"any_close_gt_10000_rate":avg([r["next_any_close_gt_10000"] for r in rs]) if rs else None}
def main():
    OUT.mkdir(parents=True,exist_ok=True);TRACK.mkdir(parents=True,exist_ok=True)
    rows=[];quality=[]
    for m in MACHINES:
        generated,q=build_machine(m);quality.append(q);dm={r["date"]:r for r in load_machine_rows(m,"2026-08-26")}
        for x in generated:
            y=dm[x["next_date"]];r=dict(x);r.update({"group":MACHINE_GROUP[m],"next_open":y["open"],"next_high":y["high"],"next_low":y["low"],"next_close":y["close"],"next_bullish":y["close"]>y["open"]})
            r["down_down_down"]=r["wave_direction_pattern"]=="DOWN-DOWN-DOWN";rows.append(r)
    bydate=defaultdict(list)
    for r in rows:bydate[r["date"]].append(r)
    daily=[]; groups=[]
    for d in sorted(bydate):
        for g,ms in GROUPS.items():
            ms={str(int(m)).zfill(3) for m in ms}
            # Fixed signal fields are explicit; do not infer or optimize them.
            signal_fields=("signal_up_up_up","signal_right","signal_low_convergence_right")
            SIGNALS=FixedSignals({"UP_UP_UP":"signal_up_up_up","RIGHT":"signal_right","LOW_CONVERGENCE_RIGHT":"signal_low_convergence_right"})
            # debug-free explicit score inputs
            rs=[r for r in bydate[d] if r["machine"] in ms];score=sum(sum(bool(r[k]) for k in rs) for k in SIGNALS.values());anyn=sum(int(r["signal_score"])>0 for r in rs);all3=sum(int(r["signal_score"])==3 for r in rs);n=len(rs);nb=sum(r["next_bullish"] for r in rs);cl=[r["next_close"] for r in rs]
            score=sum(bool(x["signal_up_up_up"])+bool(x["signal_right"])+bool(x["signal_low_convergence_right"]) for x in rs)
            row={"signal_date":d,"next_date":rs[0]["next_date"],"group":g,"machine_count":n,"group_signal_total":score,"group_signal_score":score/n,"any_signal_machine_count":anyn,"all3_machine_count":all3,"up_up_up_count":sum(bool(r[SIGNALS["UP_UP_UP"]]) for r in rs),"right_count":sum(bool(r[SIGNALS["RIGHT"]]) for r in rs),"low_convergence_right_count":sum(bool(r[SIGNALS["LOW_CONVERGENCE_RIGHT"]]) for r in rs),"no_signal_machine_count":sum(int(r["signal_score"])==0 for r in rs),"signal_machine_ratio":anyn/n,"all3_machine_ratio":all3/n,"down_down_down_count":sum(r["down_down_down"] for r in rs),"next_bullish_count":nb,"next_bullish_rate":nb/n,"next_close_mean":avg(cl),"next_close_median":med(cl),"next_close_sum":sum(cl),"next_high_mean":avg([r["next_high"] for r in rs]),"next_low_mean":avg([r["next_low"] for r in rs]),"next_max_close":max(cl),"next_max_high":max(r["next_high"] for r in rs),"next_min_low":min(r["next_low"] for r in rs),"next_any_close_gt_3000":any(r["next_close"]>3000 for r in rs),"next_any_close_gt_5000":any(r["next_close"]>5000 for r in rs),"next_any_close_gt_10000":any(r["next_close"]>10000 for r in rs),"next_any_close_gt_15000":any(r["next_close"]>15000 for r in rs),"next_any_close_gt_20000":any(r["next_close"]>20000 for r in rs),"next_any_close_gt_3000_machine_count":sum(r["next_close"]>3000 for r in rs),"next_any_close_gt_5000_machine_count":sum(r["next_close"]>5000 for r in rs),"next_any_close_gt_10000_machine_count":sum(r["next_close"]>10000 for r in rs),"evaluated":True}
            groups.append(row)
        daygroups=[r for r in groups if r["signal_date"]==d];ordered=sorted(daygroups,key=lambda r:(-r["group_signal_score"],r["group"]));
        for i,r in enumerate(ordered,1):r["rank"]=i
        daily.append({"signal_date":d,"next_date":daygroups[0]["next_date"],**{k:sum(r[k] for r in daygroups) for k in ("up_up_up_count","right_count","low_convergence_right_count","any_signal_machine_count","all3_machine_count","down_down_down_count")},"score_1_count":sum(r["group_signal_total"]==1 for r in daygroups),"score_2_count":sum(r["group_signal_total"]==2 for r in daygroups),"score_3_count":sum(r["group_signal_total"]==3 for r in daygroups),"next_day_bullish_count":sum(r["next_bullish_count"] for r in daygroups),"next_day_bullish_rate":sum(r["next_bullish_count"] for r in daygroups)/(sum(r["machine_count"] for r in daygroups)),"next_day_Close_mean":avg([r["next_close_mean"] for r in daygroups]),"next_day_Close_sum":sum(r["next_close_sum"] for r in daygroups),"direction_balance":sum(r["up_up_up_count"] for r in daygroups)-sum(r["down_down_down_count"] for r in daygroups)})
    # tracking is append-ready and contains one row per signal_date/group.
    write(TRACK/"group_signal_tracking.csv",groups);write(OUT/"group_signal_daily.csv",daily);write(OUT/"group_signal_counts.csv",groups);write(OUT/"group_signal_data_quality.csv",quality)
    rankstats=[]
    for label,pred in [("Rank 1",lambda r:r["rank"]==1),("Rank 2",lambda r:r["rank"]==2),("Rank 3",lambda r:r["rank"]==3),("Rank 1-3",lambda r:r["rank"]<=3),("Rank 4-6",lambda r:4<=r["rank"]<=6),("Rank 7-9",lambda r:r["rank"]>=7)]:rankstats.append({**market_stats([r for r in groups if pred(r)],label),"rank_tie_rule":"ordinal; score desc, group ID asc"})
    scorestats=[]
    for label,pred in [("score=0",lambda r:r["group_signal_score"]==0),("0<score<=0.5",lambda r:0<r["group_signal_score"]<=.5),("0.5<score<=1.0",lambda r:.5<r["group_signal_score"]<=1),("1.0<score<=1.5",lambda r:1<r["group_signal_score"]<=1.5),("score>1.5",lambda r:r["group_signal_score"]>1.5)]:scorestats.append(market_stats([r for r in groups if pred(r)],label))
    all3stats=[]
    for label,pred in [("all3=0",lambda r:r["all3_machine_count"]==0),("all3=1",lambda r:r["all3_machine_count"]==1),("all3>=2",lambda r:r["all3_machine_count"]>=2)]:all3stats.append(market_stats([r for r in groups if pred(r)],label))
    # Direction balance and correlation are measured on group-day observations.
    direction=[]
    for label,pred in [("balance>0",lambda r:r["up_up_up_count"]-r["down_down_down_count"]>0),("balance<0",lambda r:r["up_up_up_count"]-r["down_down_down_count"]<0),("balance=0",lambda r:r["up_up_up_count"]-r["down_down_down_count"]==0)]:direction.append(market_stats([r for r in groups if pred(r)],label))
    corr=[]; y=[r["next_bullish_rate"] for r in groups]
    for key in ("group_signal_score","group_signal_total","up_up_up_count","right_count","low_convergence_right_count","any_signal_machine_count","all3_machine_count","down_down_down_count"):
        x=[r[key] for r in groups];corr.append({"metric":key,"pearson":pearson(x,y),"spearman":spearman(x,y)})
    # Group individual character: all group-day rows, signal-active rows, and rank1 rows.
    groupstats=[]
    for g in GROUPS:
        rs=[r for r in groups if r["group"]==g];active=[r for r in rs if r["any_signal_machine_count"]>0];top=[r for r in rs if r["rank"]==1]
        groupstats.append({"group":g,"machine_count":len(GROUPS[g]),"days":len(rs),"baseline_bullish_rate":avg([r["next_bullish_rate"] for r in rs]),"baseline_close_mean":avg([r["next_close_mean"] for r in rs]),"signal_active_days":len(active),"signal_active_bullish_rate":avg([r["next_bullish_rate"] for r in active]),"signal_active_close_mean":avg([r["next_close_mean"] for r in active]),"rank1_days":len(top),"rank1_bullish_rate":avg([r["next_bullish_rate"] for r in top]),"rank1_close_mean":avg([r["next_close_mean"] for r in top])})
    # Fixed breadth buckets.
    anygroups=[]
    for lo,hi,label in [(0,5,"0-5"),(6,10,"6-10"),(11,15,"11-15"),(16,20,"16-20"),(21,999,"21+")]:
        rs=[r for r in daily if lo<=r["any_signal_machine_count"]<=hi];anygroups.append({"bucket":label,"days":len(rs),"mean_signal_count":avg([r["any_signal_machine_count"] for r in rs]),"next_day_bullish_count_mean":avg([r["next_day_bullish_count"] for r in rs]),"next_day_bullish_rate_mean":avg([r["next_day_bullish_rate"] for r in rs]),"next_day_bullish_rate_median":med([r["next_day_bullish_rate"] for r in rs])})
    # First/second half by signal dates.
    stab=[];half_dates=sorted({r["signal_date"] for r in groups});mid=len(half_dates)//2
    for label,ds in [("first_half",set(half_dates[:mid])),("second_half",set(half_dates[mid:]))]:
        for ranklabel,pred in [("Rank1",lambda r:r["rank"]==1),("Rank1-3",lambda r:r["rank"]<=3),("high_score",lambda r:r["group_signal_score"]>1.5),("ALL3>=1",lambda r:r["all3_machine_count"]>=1)]:stab.append({**market_stats([r for r in groups if r["signal_date"] in ds and pred(r)],ranklabel,label)})
    write(OUT/"group_signal_rank_stats.csv",rankstats);write(OUT/"group_signal_score_stats.csv",scorestats);write(OUT/"group_signal_all3_stats.csv",all3stats);write(OUT/"group_signal_direction_stats.csv",direction);write(OUT/"group_signal_correlation.csv",corr);write(OUT/"group_signal_group_stats.csv",groupstats);write(OUT/"group_signal_any_breadth_stats.csv",anygroups);write(OUT/"group_signal_time_stability.csv",stab);write(OUT/"group_signal_propagation_supplement.csv",[{"status":"not_evaluated","reason":"今回の主データは同グループ翌日OHLCであり、Propagationイベントを同一group-day単位へ安全に接続できる既存集計入力がないため"}])
    make_html(daily,groups,rankstats,scorestats,all3stats,direction,corr,groupstats,anygroups,stab)
    print("signal_days=%d group_day_rows=%d"%(len(daily),len(groups)))
    for r in rankstats:print(r["label"],r["n"],r["rate_mean"],r["close_mean"])
def make_html(daily,groups,rankstats,scorestats,all3stats,direction,corr,groupstats,anygroups,stab):
    def tab(rs,cols):
        z="<table><tr>"+"".join(f"<th>{c}</th>" for c in cols)+"</tr>"
        for r in rs:z+="<tr>"+"".join(f"<td>{f'{r.get(c):.3f}' if isinstance(r.get(c),float) else r.get(c,'')}</td>" for c in cols)+"</tr>"
        return z+"</table>"
    text="<!doctype html><meta charset='utf-8'><title>Group Signal Analysis</title><style>body{font-family:Arial;max-width:1500px;margin:25px auto}table{border-collapse:collapse;margin:8px 0 24px}th,td{border:1px solid #bbb;padding:5px 7px;font-size:12px}th{background:#eef}.note{background:#fff8dc;padding:12px}</style><h1>大海5 39台 Group Signal Breadth</h1><div class='note'>前日Wave Lab signalをg1〜g9へ集約。rank tieはscore降順・group ID昇順のordinal。threshold/定義/predictionは変更なし。</div>"
    text+="<h2>Rank result</h2>"+tab(rankstats,["label","n","bullish_count","rate_mean","rate_median","close_mean","close_median","any_close_gt_5000_rate","any_close_gt_10000_rate","rank_tie_rule"])
    text+="<h2>Score / ALL3</h2>"+tab(scorestats+all3stats,["label","n","bullish_count","rate_mean","rate_median","close_mean","close_median","any_close_gt_5000_rate","any_close_gt_10000_rate"])
    text+="<h2>Direction and correlations</h2>"+tab(direction,["label","n","bullish_count","rate_mean","close_mean"])+tab(corr,["metric","pearson","spearman"])
    text+="<h2>Group by group</h2>"+tab(groupstats,["group","machine_count","days","baseline_bullish_rate","baseline_close_mean","signal_active_days","signal_active_bullish_rate","signal_active_close_mean","rank1_days","rank1_bullish_rate","rank1_close_mean"])
    text+="<h2>ANY_SIGNAL breadth</h2>"+tab(anygroups,["bucket","days","mean_signal_count","next_day_bullish_count_mean","next_day_bullish_rate_mean","next_day_bullish_rate_median"])
    text+="<h2>Time stability</h2>"+tab(stab,["scope","label","n","bullish_count","rate_mean","rate_median","close_mean","close_median"])
    text+="<h2>Daily group listing</h2>"+tab(daily,["signal_date","next_date","up_up_up_count","right_count","low_convergence_right_count","any_signal_machine_count","all3_machine_count","score_1_count","score_2_count","score_3_count","down_down_down_count","direction_balance","next_day_bullish_count","next_day_bullish_rate","next_day_Close_mean"])
    (OUT/"group_signal_analysis.html").write_text(text,encoding="utf-8")
if __name__=="__main__":main()
