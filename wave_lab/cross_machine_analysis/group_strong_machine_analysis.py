"""固定Group Signal条件後の同グループ強台出現率を探索するread-only分析。"""
from __future__ import annotations
import csv, statistics
from collections import Counter, defaultdict
from pathlib import Path
import sys

BASE=Path(__file__).resolve().parent; OUT=BASE/"output"; TRACK=BASE/"tracking"
ROOT=BASE.parents[1]; sys.path.insert(0,str(ROOT))
from wave_lab.cross_machine_analysis.oomi5_holdout_validation import build_machine
from wave_lab.fft_reconstruct import load_machine_rows

GROUPS={"g1":["0046","0055","0064","0073"],"g2":["0047","0056","0065","0074"],"g3":["0039","0048","0057","0066","0075"],"g4":["0040","0049","0058","0067","0076"],"g5":["0041","0050","0059","0068","0077"],"g6":["0042","0051","0060","0069"],"g7":["0043","0052","0061","0070"],"g8":["0044","0053","0062","0071"],"g9":["0045","0054","0063","0072"]}
MACHINE_GROUP={str(int(m)).zfill(3):g for g,ms in GROUPS.items() for m in ms}
MACHINES=[f"{i:03d}" for i in range(39,78)]

def read(path):
    with path.open(encoding="utf-8-sig",newline="") as f:return list(csv.DictReader(f))
def write(path,rows):
    if not rows:return
    fields=[]
    for r in rows:
        for k in r:
            if k not in fields:fields.append(k)
    with path.open("w",encoding="utf-8-sig",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)
def vals(rs,key):return [float(r[key]) for r in rs]
def mean(rs,key):
    v=vals(rs,key);return statistics.mean(v) if v else None
def median(rs,key):
    v=vals(rs,key);return statistics.median(v) if v else None
def rate(rs,key):return sum(bool(r[key]) for r in rs)/len(rs) if rs else None
def stat(rs,label):
    return {"condition":label,"n":len(rs),"strong3000_rate":rate(rs,"strong3000"),"strong5000_rate":rate(rs,"strong5000"),"strong10000_rate":rate(rs,"strong10000"),"strong15000_rate":rate(rs,"strong15000"),"strong20000_rate":rate(rs,"strong20000"),"very_high_rate":rate(rs,"very_high"),"next_bullish_rate":mean(rs,"next_bullish_rate"),"next_close_mean":mean(rs,"next_close_mean"),"next_max_close_mean":mean(rs,"next_max_close"),"next_max_close_median":median(rs,"next_max_close"),"avg_bullish_machine_count":mean(rs,"next_bullish_count"),"avg_close_gt5000_count":mean(rs,"close_gt_5000_count"),"avg_close_gt10000_count":mean(rs,"close_gt_10000_count")}
def main():
    OUT.mkdir(parents=True,exist_ok=True);TRACK.mkdir(parents=True,exist_ok=True)
    base_rows=read(TRACK/"group_signal_tracking.csv")
    # Existing paired outputs and OHLC only; no Wave Lab output files are written.
    machine_rows=[]
    for m in MACHINES:
        generated,_=build_machine(m);dm={r["date"]:r for r in load_machine_rows(m,"2026-08-26")}
        for x in generated:
            y=dm[x["next_date"]];machine_rows.append({"machine":m,"group":MACHINE_GROUP[m],"date":x["date"],"next_bullish":int(bool(x["next_day_bullish"])),"next_close":float(y["close"]),"next_high":float(y["high"])})
    bykey=defaultdict(list)
    for r in machine_rows:bykey[(r["date"],r["group"])].append(r)
    groups=[]
    for r in base_rows:
        key=(r["signal_date"],r["group"]); ms=bykey[key]
        close=[x["next_close"] for x in ms]; high=[x["next_high"] for x in ms]
        z=dict(r); z.update({"strong3000":any(v>=3000 for v in close),"strong5000":any(v>=5000 for v in close),"strong10000":any(v>=10000 for v in close),"strong15000":any(v>=15000 for v in close),"strong20000":any(v>=20000 for v in close),"very_high":any(v>=20000 for v in high),"close_gt_3000_count":sum(v>=3000 for v in close),"close_gt_5000_count":sum(v>=5000 for v in close),"close_gt_10000_count":sum(v>=10000 for v in close),"close_gt_15000_count":sum(v>=15000 for v in close),"close_gt_20000_count":sum(v>=20000 for v in close),"next_bullish_count":sum(x["next_bullish"] for x in ms)})
        z["condition_rank_top3"]=int(int(r["rank"])<=3);z["condition_all3"]=int(int(r["all3_machine_count"])>=1);z["condition_direction_positive"]=int(int(r["up_up_up_count"])-int(r["down_down_down_count"])>0);z["condition_all3_combined"]=int(z["condition_rank_top3"] and z["condition_all3"] and z["condition_direction_positive"]);groups.append(z)
    tracking=[]
    for r in groups:
        tracking.append({"signal_date":r["signal_date"],"next_date":r["next_date"],"group":r["group"],"condition_rank_top3":r["condition_rank_top3"],"condition_all3":r["condition_all3"],"condition_direction_positive":r["condition_direction_positive"],"condition_all3_combined":r["condition_all3_combined"],"next_strong3000":r["strong3000"],"next_strong5000":r["strong5000"],"next_strong10000":r["strong10000"],"next_strong15000":r["strong15000"],"next_strong20000":r["strong20000"],"next_strong5000_count":r["close_gt_5000_count"],"next_strong10000_count":r["close_gt_10000_count"],"next_max_close":r["next_max_close"],"next_max_high":r["next_max_high"],"evaluated":True})
    write(TRACK/"group_strong_machine_tracking.csv",tracking);write(OUT/"group_strong_machine_summary.csv",[stat(groups,"ALL_GROUP_DAYS")])
    conditions={"A_RANK_TOP3":lambda r:r["condition_rank_top3"],"B_ALL3_GE1":lambda r:r["condition_all3"],"C_DIRECTION_POSITIVE":lambda r:r["condition_direction_positive"],"A+B+C":lambda r:r["condition_all3_combined"]}
    cstats=[stat([r for r in groups if p(r)],k) for k,p in conditions.items()];write(OUT/"group_strong_machine_condition_stats.csv",cstats)
    overlap={"A only":lambda r:r["condition_rank_top3"] and not r["condition_all3"] and not r["condition_direction_positive"],"B only":lambda r:r["condition_all3"] and not r["condition_rank_top3"] and not r["condition_direction_positive"],"C only":lambda r:r["condition_direction_positive"] and not r["condition_rank_top3"] and not r["condition_all3"],"A+B":lambda r:r["condition_rank_top3"] and r["condition_all3"] and not r["condition_direction_positive"],"A+C":lambda r:r["condition_rank_top3"] and r["condition_direction_positive"] and not r["condition_all3"],"B+C":lambda r:r["condition_all3"] and r["condition_direction_positive"] and not r["condition_rank_top3"],"A+B+C":lambda r:r["condition_all3_combined"]}
    ostats=[stat([r for r in groups if p(r)],k) for k,p in overlap.items()];write(OUT/"group_strong_machine_overlap_stats.csv",ostats)
    dist=[]
    for label,p in {"A+B+C":lambda r:r["condition_all3_combined"]}.items():
        rs=[r for r in groups if p(r)]
        for field,title in [("close_gt_5000_count","Close>=5000"),("close_gt_10000_count","Close>=10000")]:
            c=Counter("0" if int(r[field])==0 else "1" if int(r[field])==1 else "2" if int(r[field])==2 else "3+" for r in rs)
            for bucket in ["0","1","2","3+"]:dist.append({"condition":label,"metric":title,"bucket":bucket,"days":c[bucket],"rate":c[bucket]/len(rs) if rs else None})
    write(OUT/"group_strong_machine_count_distribution.csv",dist)
    gst=[]
    for g in GROUPS:
        rs=[r for r in groups if r["group"]==g and r["condition_all3_combined"]];gst.append({"group":g,"n":len(rs),"strong5000_rate":rate(rs,"strong5000"),"strong10000_rate":rate(rs,"strong10000"),"strong20000_rate":rate(rs,"strong20000"),"next_max_close_mean":mean(rs,"next_max_close")})
    write(OUT/"group_strong_machine_group_stats.csv",gst)
    stab=[];dates=sorted({r["signal_date"] for r in groups});mid=len(dates)//2
    for label,ds in [("first_half",set(dates[:mid])),("second_half",set(dates[mid:]))]:
        for name,p in conditions.items():stab.append({"period":label,**stat([r for r in groups if r["signal_date"] in ds and p(r)],name)})
    write(OUT/"group_strong_machine_time_stability.csv",stab)
    make_html(groups,cstats,ostats,dist,gst,stab)
    print("group_days=%d A+B+C=%d"%(len(groups),sum(r["condition_all3_combined"] for r in groups)))
    for r in cstats:print(r["condition"],r["n"],r["strong5000_rate"],r["strong10000_rate"],r["strong20000_rate"])
def make_html(groups,cstats,ostats,dist,gst,stab):
    def table(rs):
        if not rs:return "<p>none</p>"
        fs=list(rs[0]);s="<table><tr>"+"".join(f"<th>{x}</th>" for x in fs)+"</tr>"
        for r in rs:s+="<tr>"+"".join(f"<td>{r.get(x,'')}</td>" for x in fs)+"</tr>"
        return s+"</table>"
    text="<!doctype html><meta charset='utf-8'><title>Group Strong Machine Analysis</title><style>body{font-family:Arial;max-width:1600px;margin:24px}table{border-collapse:collapse;margin:8px 0 24px}th,td{border:1px solid #aaa;padding:4px 6px;font-size:12px}th{background:#eef}</style><h1>Group Signal → Strong Machine</h1><p>固定条件A=Rank1-3、B=ALL3>=1、C=direction balance>0。既存Wave Lab/OHLC read-only。</p>"
    for title,rs in [("Condition stats",cstats),("Overlap",ostats),("A+B+C count distribution",dist),("Group",gst),("Time stability",stab)]:text+=f"<h2>{title}</h2>"+table(rs)
    (OUT/"group_strong_machine_analysis.html").write_text(text,encoding="utf-8")
if __name__=="__main__":main()
