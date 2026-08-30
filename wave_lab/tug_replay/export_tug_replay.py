"""PSCUBE SVG -> Tug Replay JSON exporter.

Uses the existing analyze_pscube SVG axis/point parser and analyze.py coordinate
conversion. It does not modify canonical OHLC or Wave Lab outputs.
"""
from __future__ import annotations
import argparse, csv, json, math, re
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
import analyze
from analyze_pscube import build_axes_and_points_from_svg, parse_svg_labels, read_html_text, t2m

GROUPS={
 "g1":["0046","0055","0064","0073"],"g2":["0047","0056","0065","0074"],
 "g3":["0039","0048","0057","0066","0075"],"g4":["0040","0049","0058","0067","0076"],
 "g5":["0041","0050","0059","0068","0077"],"g6":["0042","0051","0060","0069"],
 "g7":["0043","0052","0061","0070"],"g8":["0044","0053","0062","0071"],
 "g9":["0045","0054","0063","0072"],
}
CAPTURE=ROOT/"data"/"local_capture"; OUT=ROOT/"docs"/"wave_lab"/"tug_replay"/"data"

def time_mapper(svg_path:Path, axes:dict):
    labels=[]
    for x,y,label in parse_svg_labels(read_html_text(svg_path)):
        label=label.strip()
        if re.fullmatch(r"\d{1,2}:\d{2}",label): labels.append((t2m(label),x))
    labels=sorted(set(labels))
    if len(labels)<2: raise ValueError("SVG time labels are insufficient")
    def f(x):
        if x<=labels[0][1]: a,b=labels[0],labels[1]
        elif x>=labels[-1][1]: a,b=labels[-2],labels[-1]
        else:
            for a,b in zip(labels,labels[1:]):
                if a[1]<=x<=b[1]: break
        return a[0]+(x-a[1])*(b[0]-a[0])/(b[1]-a[1])
    return f,labels

def load_state(date:str):
    p=ROOT/"docs"/"wave_lab"/"data"/"forward"/f"{date}.json"
    if not p.exists(): return {}
    obj=json.loads(p.read_text(encoding="utf-8"));return {str(int(x["machine"])).zfill(4):x for x in obj.get("machine_signals",[])}

def load_canonical(date:str):
    p=ROOT/"csv"/"daily_ohlc"/date/f"{date}_daily_ohlc.csv"
    out={}
    with p.open(encoding="utf-8-sig",newline="") as f:
        for r in csv.DictReader(f): out[str(r["Machine"]).zfill(4)]={k:r.get(k) for k in ("Open","High","Low","Close")}
    return out

def export(date:str,group:str):
    machines=GROUPS[group]; states=load_state(date); canonical=load_canonical(date)
    tracks=[]; finals=[]; common=[]
    for m in machines:
        path=CAPTURE/date/"morning"/"svg"/f"{m}.svg"
        axes,points=build_axes_and_points_from_svg(path,date); mapper,labels=time_mapper(path,axes)
        source=[]
        for x,y in points:
            source.append({"minute":max(0,round(mapper(x))),"value":analyze.px_to_val(y,axes),"x_px":x,"y_px":y})
        source.sort(key=lambda z:(z["minute"],z["x_px"]))
        first=max(9*60,source[0]["minute"]); last=source[-1]["minute"]
        end=math.ceil(last/5)*5; grid=list(range((first//5)*5,end+1,5))
        if not grid or grid[0]>first: grid.insert(0,first)
        if grid[-1]!=last: grid.append(last)
        values=[];j=0;current=source[0]["value"]
        for minute in sorted(set(grid)):
            while j<len(source) and source[j]["minute"]<=minute: current=source[j]["value"];j+=1
            values.append({"time":f"{minute//60:02d}:{minute%60:02d}","minute":minute,"value":current})
        st=states.get(m,{})
        finals.append({"machine":m[-3:],"svg_final":values[-1]["value"],"canonical_close":int(canonical[m]["Close"]) if canonical.get(m) and canonical[m].get("Close") else None})
        tracks.append({"machine":m[-3:],"values":values,"source_point_count":len(points),"svg_axes":{"source":"existing analyze_pscube","labels":[f"{x//60:02d}:{x%60:02d}" for x,_ in labels]},"state":{"signal_date":date,"machine":m[-3:],"UP_UP_UP":bool(st.get("UP_UP_UP",False)),"RIGHT":bool(st.get("RIGHT",False)),"LOW_CONVERGENCE_RIGHT":bool(st.get("LOW_CONVERGENCE_RIGHT",False)),"ALL_3":bool(st.get("ALL_3",False)),"DOWN_DOWN_DOWN":bool(st.get("DOWN_DOWN_DOWN",False)),"score":int(st.get("score",0))}})
        common.extend(v["minute"] for v in values)
    timeline=sorted(set(common))
    # Normalize every machine onto one common timeline using a causal previous-value hold.
    for track in tracks:
        source=track["values"]; j=0; current=source[0]["value"]; normalized=[]
        for minute in timeline:
            while j+1<len(source) and source[j+1]["minute"]<=minute: j+=1; current=source[j]["value"]
            normalized.append({"time":f"{minute//60:02d}:{minute%60:02d}","minute":minute,"value":current})
        track["values"]=normalized
    lookup={m:{v["minute"]:v["value"] for v in x["values"]} for m,x in zip(machines,tracks)}
    # All machine series now share the same timeline; last point may be the SVG endpoint.
    total=[]
    for minute in timeline:
        total.append({"time":f"{minute//60:02d}:{minute%60:02d}","minute":minute,"value":sum(lookup[m].get(minute,0) for m in machines)})
    obj={"date":date,"group":group,"machines":tracks,"time_range":{"start":min(t["minute"] for t in total),"end":max(t["minute"] for t in total),"start_time":total[0]["time"],"end_time":total[-1]["time"],"step_minutes":5,"interpolation":"previous-value hold (causal; no future value is used for an earlier point)"},"time_points":[{"time":t["time"],"minute":t["minute"]} for t in total],"group_total":total,"events":[],"validation":{"final_values":finals,"source":"SVG path coordinates via existing analyze_pscube.py + analyze.py px_to_val","canonical_ohlc":str(ROOT/"csv"/"daily_ohlc"/date/f"{date}_daily_ohlc.csv")}}
    dest=OUT/date;dest.mkdir(parents=True,exist_ok=True);(dest/f"{group}.json").write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding="utf-8")
    index=OUT/"index.json";index.write_text(json.dumps({"datasets":[{"date":date,"group":group,"path":f"data/{date}/{group}.json","machines":[m[-3:] for m in machines]}]},ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"date":date,"group":group,"machines":[m[-3:] for m in machines],"points":len(total),"final_values":finals},ensure_ascii=False,indent=2))

if __name__=="__main__":
    ap=argparse.ArgumentParser();ap.add_argument("--date",required=True);ap.add_argument("--group",required=True,choices=sorted(GROUPS));a=ap.parse_args();export(a.date,a.group)
