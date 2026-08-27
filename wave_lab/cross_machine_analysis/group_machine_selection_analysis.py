"""A+B+C強グループ内で、翌日強台を個別Wave Lab状態別に調べるread-only分析。"""
from __future__ import annotations
import csv, statistics
from collections import defaultdict
from pathlib import Path
import sys
BASE=Path(__file__).resolve().parent;OUT=BASE/"output";TRACK=BASE/"tracking";ROOT=BASE.parents[1];sys.path.insert(0,str(ROOT))
from wave_lab.cross_machine_analysis.oomi5_holdout_validation import build_machine
from wave_lab.fft_reconstruct import load_machine_rows
GROUPS={"g1":["0046","0055","0064","0073"],"g2":["0047","0056","0065","0074"],"g3":["0039","0048","0057","0066","0075"],"g4":["0040","0049","0058","0067","0076"],"g5":["0041","0050","0059","0068","0077"],"g6":["0042","0051","0060","0069"],"g7":["0043","0052","0061","0070"],"g8":["0044","0053","0062","0071"],"g9":["0045","0054","0063","0072"]}
def read(p):
 with p.open(encoding="utf-8-sig",newline="") as f:return list(csv.DictReader(f))
def write(p,rs):
 if not rs:return
 fs=[]
 for r in rs:
  for k in r:
   if k not in fs:fs.append(k)
 with p.open("w",encoding="utf-8-sig",newline="") as f: csv.DictWriter(f,fieldnames=fs).writeheader();csv.DictWriter(f,fieldnames=fs).writerows(rs)
def mean(rs,k):
 v=[float(r[k]) for r in rs];return statistics.mean(v) if v else None
def med(rs,k):
 v=[float(r[k]) for r in rs];return statistics.median(v) if v else None
def rate(rs,k):return sum(bool(r[k]) for r in rs)/len(rs) if rs else None
def stats(rs,label):
 return {"label":label,"n":len(rs),"bullish_rate":mean(rs,"next_bullish"),"close_mean":mean(rs,"next_close"),"close_median":med(rs,"next_close"),"high_mean":mean(rs,"next_high"),"low_mean":mean(rs,"next_low"),"close_ge_3000":rate(rs,"ge3000"),"close_ge_5000":rate(rs,"ge5000"),"close_ge_10000":rate(rs,"ge10000"),"close_ge_15000":rate(rs,"ge15000"),"close_ge_20000":rate(rs,"ge20000")}
def main():
 OUT.mkdir(parents=True,exist_ok=True);TRACK.mkdir(parents=True,exist_ok=True)
 strong_groups=[r for r in read(TRACK/"group_signal_tracking.csv") if int(r["rank"])<=3 and int(r["all3_machine_count"])>=1 and int(r["up_up_up_count"])-int(r["down_down_down_count"])>0]
 selected={(r["signal_date"],r["group"]):r for r in strong_groups}; machine=[]
 for g,ms in GROUPS.items():
  for m in ms:
   m=str(int(m)).zfill(3);gen,_=build_machine(m);dm={r["date"]:r for r in load_machine_rows(m,"2026-08-26")}
   for x in gen:
    key=(x["date"],g)
    if key not in selected:continue
    y=dm[x["next_date"]];score=int(bool(x["signal_up_up_up"]))+int(bool(x["signal_right"]))+int(bool(x["signal_low_convergence_right"]))
    machine.append({"signal_date":x["date"],"next_date":x["next_date"],"group":g,"machine":m,"up_up_up":int(bool(x["signal_up_up_up"])),"right":int(bool(x["signal_right"])),"low_conv_right":int(bool(x["signal_low_convergence_right"])),"all3":int(score==3),"score":score,"down_down_down":int(x["wave_direction_pattern"]=="DOWN-DOWN-DOWN"),"next_bullish":int(y["close"]>y["open"]),"next_close":float(y["close"]),"next_high":float(y["high"]),"next_low":float(y["low"]),"ge3000":float(y["close"])>=3000,"ge5000":float(y["close"])>=5000,"ge10000":float(y["close"])>=10000,"ge15000":float(y["close"])>=15000,"ge20000":float(y["close"])>=20000})
 bygd=defaultdict(list)
 for r in machine:bygd[(r["signal_date"],r["group"])].append(r)
 for rs in bygd.values():
  for rank,r in enumerate(sorted(rs,key=lambda z:(-z["next_close"],z["machine"])),1):r["next_close_rank"]=rank;r["top1"]=rank==1;r["top2"]=rank<=2;r["top3"]=rank<=3
 sigstats=[]
 for label,p in [("ALL_3",lambda r:r["all3"]),("score=2",lambda r:r["score"]==2),("score=1",lambda r:r["score"]==1),("score=0",lambda r:r["score"]==0),("DOWN_DOWN_DOWN",lambda r:r["down_down_down"]),("score>=2",lambda r:r["score"]>=2)]:sigstats.append(stats([r for r in machine if p(r)],label))
 rankstats=[]
 for label,p in [("score3",lambda r:r["all3"]),("score2",lambda r:r["score"]==2),("score1",lambda r:r["score"]==1),("score0",lambda r:r["score"]==0)]:
  rs=[r for r in machine if p(r)];rankstats.append({"label":label,"n":len(rs),"close_rank1_rate":rate(rs,"top1"),"close_top2_rate":rate(rs,"top2"),"close_top3_rate":rate(rs,"top3")})
 all3days=[(k,rs) for k,rs in bygd.items() if sum(r["all3"] for r in rs)==1];two=[(k,rs) for k,rs in bygd.items() if sum(r["all3"] for r in rs)>=2]
 a3=[]
 for label,items in [("ALL3_exactly1",all3days),("ALL3_ge2",two)]:
  rs=[r for _,x in items for r in x if label!="ALL3_exactly1" or r["all3"]==1];a3.append({**stats(rs,label),"group_days":len(items),"top1_rate":rate(rs,"top1"),"top2_rate":rate(rs,"top2")})
 capture=[]
 for label,k in [("UP_UP_UP","up_up_up"),("RIGHT","right"),("LOW_CONV_RIGHT","low_conv_right"),("ALL_3","all3"),("score>=2","score")]:
  for threshold,field in [(3000,"ge3000"),(5000,"ge5000"),(10000,"ge10000"),(15000,"ge15000"),(20000,"ge20000")]:
   sig=[r for r in machine if (r[k]>=2 if k=="score" else r[k])]; ev=[r for r in machine if r[field]];capture.append({"signal":label,"threshold":threshold,"signal_n":len(sig),"strong_n":len(ev),"precision":rate(sig,field),"capture_rate":sum(bool(r[k]>=2 if k=="score" else r[k]) for r in ev)/len(ev) if ev else None})
 write(OUT/"group_machine_selection_summary.csv",[stats(machine,"A+B+C group machines")]);write(OUT/"group_machine_selection_signal_stats.csv",sigstats);write(OUT/"group_machine_selection_rank_stats.csv",rankstats);write(OUT/"group_machine_selection_capture_stats.csv",capture);write(OUT/"group_machine_selection_all3_stats.csv",a3)
 gst=[]
 for g in GROUPS:
  rs=[r for r in machine if r["group"]==g];gst.append({"group":g,"n":len(rs),"all3_n":sum(r["all3"] for r in rs),"all3_strong5000":rate([r for r in rs if r["all3"]],"ge5000"),"all3_strong10000":rate([r for r in rs if r["all3"]],"ge10000"),"all3_close_mean":mean([r for r in rs if r["all3"]],"next_close")})
 write(OUT/"group_machine_selection_group_stats.csv",gst)
 mst=[]
 for m in sorted({r["machine"] for r in machine}):
  rs=[r for r in machine if r["machine"]==m];a=[r for r in rs if r["all3"]];mst.append({"machine":m,"n":len(rs),"all3_n":len(a),"all3_strong5000":rate(a,"ge5000"),"all3_strong10000":rate(a,"ge10000"),"all3_close_mean":mean(a,"next_close")})
 write(OUT/"group_machine_selection_machine_stats.csv",mst)
 dates=sorted({r["signal_date"] for r in machine});mid=len(dates)//2;ts=[]
 for period,ds in [("first_half",set(dates[:mid])),("second_half",set(dates[mid:]))]:
  for label,p in [("ALL_3",lambda r:r["all3"]),("score>=2",lambda r:r["score"]>=2)]:
   rs=[r for r in machine if r["signal_date"] in ds and p(r)];ts.append({"period":period,"condition":label,"n":len(rs),"bullish_rate":mean(rs,"next_bullish"),"strong5000":rate(rs,"ge5000"),"strong10000":rate(rs,"ge10000")})
 write(OUT/"group_machine_selection_time_stability.csv",ts);write(TRACK/"group_machine_selection_tracking.csv",machine);html(machine,sigstats,rankstats,capture,a3,gst,mst,ts)
 print("selected_group_days=%d machine_samples=%d"%(len(selected),len(machine)))
 for r in sigstats:print(r["label"],r["n"],r["close_ge_5000"],r["close_ge_10000"])
def html(machine,*tables):
 def t(rs):
  if not rs:return "<p>none</p>"
  fs=list(rs[0]);s="<table><tr>"+"".join("<th>"+x+"</th>" for x in fs)+"</tr>"
  for r in rs:s+="<tr>"+"".join("<td>"+str(r.get(x,""))+"</td>" for x in fs)+"</tr>"
  return s+"</table>"
 text="<!doctype html><meta charset='utf-8'><title>Group Machine Selection</title><style>body{font-family:Arial;max-width:1600px;margin:24px}table{border-collapse:collapse;margin:8px 0 24px}th,td{border:1px solid #aaa;padding:4px 6px;font-size:12px}th{background:#eef}</style><h1>A+B+C Group → Individual Strong Machine</h1><p>既存固定条件のみ。A=Rank1-3、B=ALL3>=1、C=direction_balance>0。</p>"
 for title,rs in zip(["Signal stats","Rank stats","Capture","ALL3 days","Group","Machine","Time stability"],tables):text+=f"<h2>{title}</h2>"+t(rs)
 (OUT/"group_machine_selection_analysis.html").write_text(text,encoding="utf-8")
if __name__=="__main__":main()
