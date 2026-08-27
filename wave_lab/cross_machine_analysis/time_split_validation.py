"""Wave Lab 4台横断候補の時系列分割検証。入力・既存Wave Lab出力は読み取り専用。"""
from __future__ import annotations
import csv, html, math
from collections import defaultdict
from pathlib import Path

BASE = Path(__file__).resolve().parent
OUT = BASE / "output"
MACHINES = ["049", "056", "075", "077"]
CANDIDATES = {
    "UP-UP-UP": lambda r: r["wave_direction_pattern"] == "UP-UP-UP",
    "RIGHT": lambda r: r["region"] == "RIGHT",
    "low_convergence + RIGHT": lambda r: r["convergence_bin"] == "low" and r["region"] == "RIGHT",
    "RIGHT + STABLE": lambda r: r["region"] == "RIGHT" and r["regime"] == "STABLE",
    "low_convergence + TRANSITION": lambda r: r["convergence_bin"] == "low" and r["regime"] == "TRANSITION",
    "DOWN-DOWN-DOWN": lambda r: r["wave_direction_pattern"] == "DOWN-DOWN-DOWN",
}
MAIN = ["UP-UP-UP", "RIGHT", "low_convergence + RIGHT"]

def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as f: return list(csv.DictReader(f))
def truth(v): return str(v).lower() in {"true", "1", "yes"}
def pct(x): return "" if x is None else f"{x:.4f}"
def basic(rows):
    n=len(rows); c=sum(1 for r in rows if truth(r["bullish"]))
    return n,c,c/n if n else None
def wilson(c,n):
    if not n:return (None,None)
    z=1.96;p=c/n;d=1+z*z/n;mid=(p+z*z/(2*n))/d
    h=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/d
    return mid-h,mid+h
def metric(rows, label, split, machine=""):
    n,c,p=basic(rows); lo,hi=wilson(c,n)
    return {"split":split,"machine":machine,"feature":label,"n":n,"bullish_count":c,"bullish_rate":p,
            "baseline":None,"lift":None,"wilson_low":lo,"wilson_high":hi,
            "non_bullish_count":n-c,"non_bullish_rate":((n-c)/n if n else None)}
def set_baseline(s,b):
    s["baseline"]=b; s["lift"]=(s["bullish_rate"]-b if s["bullish_rate"] is not None else None); return s
def write(name, rows):
    if not rows:return
    keys=[]
    for r in rows:
        for k in r:
            if k not in keys:keys.append(k)
    with (OUT/name).open("w",encoding="utf-8-sig",newline="") as f:
        w=csv.DictWriter(f,fieldnames=keys);w.writeheader();w.writerows(rows)
def table(rows, cols):
    z="<table><tr>"+"".join(f"<th>{html.escape(c)}</th>" for c in cols)+"</tr>"
    for r in rows:
        z+="<tr>"+"".join(f"<td>{html.escape(pct(r.get(c)) if isinstance(r.get(c),float) else str(r.get(c,'')))}</td>" for c in cols)+"</tr>"
    return z+"</table>"

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    rows=read_csv(OUT/"paired_samples.csv")
    by=defaultdict(list)
    for r in rows: by[r["machine"]].append(r)
    for m in by: by[m].sort(key=lambda r:r["date"])
    discovery=[];validation=[];machine=[]
    for m in MACHINES:
        rs=by[m][:60]; train=rs[:30]; test=rs[30:60]
        for split,part in [("discovery",train),("validation",test)]:
            bn=basic(part)[2]
            for name,pred in CANDIDATES.items():
                s=set_baseline(metric([r for r in part if pred(r)],name,split,m),bn)
                machine.append(s)
                (discovery if split=="discovery" else validation).append(s)
    pool_stats=[]
    for split in ["discovery","validation"]:
        part=[r for m in MACHINES for r in by[m][:30 if split=="discovery" else 60][0 if split=="discovery" else 30:]]
        # 上の式は各台を先に切り、poolすることを明示。
        bn=basic(part)[2]
        for name,pred in CANDIDATES.items(): pool_stats.append(set_baseline(metric([r for r in part if pred(r)],name,split,"POOLED"),bn))
    # baseline rows
    baselines=[]
    for split in ["discovery","validation"]:
        part=[r for m in MACHINES for r in (by[m][:30] if split=="discovery" else by[m][30:60])]
        n,c,p=basic(part);baselines.append({"split":split,"machine":"POOLED","feature":"baseline","n":n,"bullish_count":c,"bullish_rate":p,"baseline":p,"lift":0})
        for m in MACHINES:
            n,c,p=basic(by[m][:30] if split=="discovery" else by[m][30:60]);baselines.append({"split":split,"machine":m,"feature":"baseline","n":n,"bullish_count":c,"bullish_rate":p,"baseline":p,"lift":0})
    # simple fixed +1 score, main candidates only
    score=[]
    for split in ["discovery","validation"]:
        part=[r for m in MACHINES for r in (by[m][:30] if split=="discovery" else by[m][30:60])]
        bn=basic(part)[2]
        for r in part:
            r["simple_score"]=sum(1 for name in MAIN if CANDIDATES[name](r))
        for k in range(4): score.append(set_baseline(metric([r for r in part if int(r["simple_score"])==k],f"score{k}",split,"POOLED"),bn))
    # walk-forward: per machine train 30/40/50, evaluate next 10; conditions fixed.
    wf=[]
    for start,end in [(30,40),(40,50),(50,60)]:
        part=[r for m in MACHINES for r in by[m][start:end]]; bn=basic(part)[2]
        for name in MAIN:
            s=set_baseline(metric([r for r in part if CANDIDATES[name](r)],name,f"fold_{start}_{end}","POOLED"),bn);s.update({"train_end":start,"test_start":start,"test_end":end});wf.append(s)
    for name in MAIN:
        part=[r for m in MACHINES for r in by[m][30:60] if CANDIDATES[name](r)]; bn=basic([r for m in MACHINES for r in by[m][30:60]])[2]
        s=set_baseline(metric(part,name,"walk_forward_overall","POOLED"),bn);s.update({"train_end":"","test_start":30,"test_end":60});wf.append(s)
    write("time_split_summary.csv",baselines+pool_stats)
    write("time_split_machine_stats.csv",machine)
    write("validation_candidate_stats.csv",pool_stats)
    write("validation_score_stats.csv",score)
    write("walk_forward_stats.csv",wf)
    make_html(baselines,pool_stats,machine,score,wf)

def make_html(base,pool,machine,score,wf):
    cols=["split","feature","n","bullish_count","bullish_rate","baseline","lift","wilson_low","wilson_high","non_bullish_rate"]
    mcols=["split","machine","feature","n","bullish_count","bullish_rate","baseline","lift"]
    text="""<!doctype html><meta charset='utf-8'><title>Wave Lab time split validation</title><style>body{font-family:Arial;max-width:1250px;margin:28px auto}table{border-collapse:collapse;margin:8px 0 25px}th,td{border:1px solid #ccc;padding:5px 8px;font-size:13px}th{background:#eef}.note{background:#fff8dc;padding:12px}</style>"""
    text+="<h1>Wave Lab 4台 翌日陽線 時系列分割検証</h1><div class='note'>各台30件 discovery / 30件 validation。条件固定、shuffleなし。主評価はUP-UP-UP、RIGHT、low_convergence + RIGHT。</div>"
    text+="<h2>Baseline and pooled candidates</h2>"+table(base+pool,cols)
    text+="<h2>Machine comparison</h2>"+table(machine,mcols)
    text+="<h2>Simple score (+1 fixed)</h2>"+table(score,cols)
    text+="<h2>Walk-forward</h2>"+table(wf,cols+["train_end","test_start","test_end"])
    (OUT/"time_split_validation.html").write_text(text,encoding="utf-8")

if __name__=='__main__':main()
