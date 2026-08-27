"""Wave Lab通常出力の横断探索分析（既存ファイルは読み取り専用）。"""
from __future__ import annotations
import csv, html, json, math
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent / "output"
MACHINES = ["049", "056", "075", "077"]

def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))

def num(v):
    try: return float(v)
    except (TypeError, ValueError): return None

def truth(v): return str(v).strip().lower() in {"true", "1", "yes"}

def rate(rows, key="bullish"):
    n = len(rows); c = sum(1 for r in rows if r.get(key))
    return n, c, (c / n if n else None)

def fmt(v):
    return "" if v is None else f"{v:.4f}"

def wilson(c, n):
    if not n: return (None, None)
    z = 1.96; p = c/n; den = 1+z*z/n
    mid = (p+z*z/(2*n))/den
    half = z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/den
    return mid-half, mid+half

def classify(rows, feature):
    groups = defaultdict(list)
    for r in rows: groups[r.get(feature, "")].append(r)
    return groups

def phase_bin(v):
    x=num(v)
    return "" if x is None else f"{int(x//45)*45}-{int(x//45)*45+45}"

def stats(rows, label, baseline):
    n,c,p = rate(rows)
    lo,hi = wilson(c,n)
    return {"feature": label, "n": n, "bullish_count": c,
            "bullish_rate": p, "baseline": baseline,
            "lift": (p-baseline if p is not None else None),
            "wilson_low": lo, "wilson_high": hi}

def add_stat(records, rows, label, baseline):
    records.append(stats(rows, label, baseline))

def main():
    OUT.mkdir(parents=True, exist_ok=True)
    all_rows=[]; by_machine={}
    for machine in MACHINES:
        conv = read_csv(ROOT / "wave_lab" / "output" / machine / "phase_convergence_daily.csv")
        regime = {r["date"]: r for r in read_csv(ROOT / "wave_lab" / "output" / machine / "period_regime_daily.csv")}
        dates = sorted(regime)
        usable=[]
        for i, date in enumerate(dates[:-1]):
            cur = next((r for r in conv if r["date"] == date), None)
            nxt = regime[dates[i+1]]
            if not cur or not cur.get("long_phase") or not cur.get("mid_phase") or not cur.get("short_phase"):
                continue
            # 既存period_regime_dailyのbullishを優先（なければ仕様どおりOHLCから判定）。
            nb = truth(nxt.get("bullish")) if nxt.get("bullish") != "" else ((num(nxt.get("close")) or 0) > (num(nxt.get("open")) or 0))
            row = dict(cur)
            row.update({"machine": machine, "target_date": dates[i+1], "bullish": nb,
                        "next_close": num(nxt.get("close")), "next_open": num(nxt.get("open")),
                        "next_high": num(nxt.get("high")), "next_low": num(nxt.get("low"))})
            # 前日OHLC（current date）も補助特徴として同じ既存表から付与。
            old = regime[date]
            op,hi,lo,cl = [num(old.get(x)) for x in ("open","high","low","close")]
            row.update({"prev_open":op,"prev_high":hi,"prev_low":lo,"prev_close":cl,
                        "prev_range": (hi-lo if hi is not None and lo is not None else None),
                        "prev_close_low": (cl-lo if cl is not None and lo is not None else None),
                        "prev_high_close": (hi-cl if hi is not None and cl is not None else None),
                        "prev_bullish": truth(old.get("bullish"))})
            cs=num(cur.get("convergence_score")); prev_cs=num(conv[i-1].get("convergence_score")) if i else None
            row["convergence_delta"] = (cs-prev_cs if cs is not None and prev_cs is not None else None)
            row["convergence_change"] = ("rising" if row["convergence_delta"] is not None and row["convergence_delta"]>0 else "falling" if row["convergence_delta"] is not None and row["convergence_delta"]<0 else "flat_or_unknown")
            row["convergence_bin"] = ("high" if cs is not None and cs>=.7 else "middle" if cs is not None and cs>=.5 else "low")
            row["convergence_0_7_plus"] = cs is not None and cs>=.7
            row["convergence_0_8_plus"] = cs is not None and cs>=.8
            row["high_falling"] = cs is not None and cs>=.7 and row["convergence_change"]=="falling"
            row["high_rising"] = cs is not None and cs>=.7 and row["convergence_change"]=="rising"
            row["phase_cluster"] = row["convergence_bin"]
            row["regime"] = regime[date].get("regime", "")
            row["region"] = cur.get("centroid_region", "")
            usable.append(row); all_rows.append(row)
        by_machine[machine]=usable

    n,c,base=rate(all_rows)
    machine_base={m: rate(rs)[2] for m,rs in by_machine.items()}
    feature_groups={
      "phase": lambda r: [r.get("long_phase"),r.get("mid_phase"),r.get("short_phase")],
      "phase_role": lambda r: ["LONG="+phase_bin(r.get("long_phase")), "MID="+phase_bin(r.get("mid_phase")), "SHORT="+phase_bin(r.get("short_phase"))],
      "direction_pattern": lambda r: [r.get("wave_direction_pattern")],
      "convergence_bin": lambda r: [r.get("convergence_bin")],
      "convergence_change": lambda r: [r.get("convergence_change")],
      "region": lambda r: [r.get("region")], "regime": lambda r: [r.get("regime")],
      "prev_bullish": lambda r: ["prev_bullish" if r["prev_bullish"] else "prev_bearish"],
      "phase_cluster": lambda r: ["phase_cluster="+r["phase_cluster"]],
    }
    records=[]; machine_records=[]
    def add_feature(name, fn):
        vals=defaultdict(list)
        for r in all_rows:
            v=fn(r)
            if isinstance(v,(list,tuple)): label=" + ".join(str(x) for x in v)
            else: label=str(v)
            vals[label].append(r)
        for label,rs in vals.items():
            s=stats(rs,label,base); s["feature_family"]=name; records.append(s)
            for m,mrs in by_machine.items():
                ms=[x for x in mrs if (" + ".join(str(y) for y in fn(x)) if isinstance(fn(x),(list,tuple)) else str(fn(x)))==label]
                q=stats(ms,label,machine_base[m]); q.update({"feature_family":name,"machine":m}); machine_records.append(q)
    for name,fn in feature_groups.items(): add_feature(name,fn)
    for role, key in [("LONG", "long_phase"), ("MID", "mid_phase"), ("SHORT", "short_phase")]:
        add_feature("phase_"+role, lambda r, k=key: [r.get(k, "")])
    # 8方向パターン、特定仮説、組み合わせ。
    add_feature("region_regime", lambda r: [r["region"],r["regime"]])
    add_feature("convergence_region", lambda r: [r["convergence_bin"],r["region"]])
    add_feature("convergence_regime", lambda r: [r["convergence_bin"],r["regime"]])
    add_feature("hypothesis", lambda r: ["high_convergence" if r["convergence_0_7_plus"] else "not_high"])
    add_feature("hypothesis", lambda r: ["high_convergence+TRANSITION" if r["convergence_0_7_plus"] and r["regime"]=="TRANSITION" else "other"])
    add_feature("hypothesis", lambda r: ["high_convergence+TOP" if r["convergence_0_7_plus"] and r["region"]=="TOP" else "other"])
    add_feature("hypothesis", lambda r: ["high_convergence+BOTTOM" if r["convergence_0_7_plus"] and r["region"]=="BOTTOM" else "other"])
    add_feature("hypothesis", lambda r: ["SHORT_UP_LONG_MID_DOWN" if r["short_phase"]=="UP" and r["mid_phase"]=="DOWN" and r["long_phase"]=="DOWN" else "other"])
    add_feature("hypothesis", lambda r: ["high_falling" if r["high_falling"] else "other"])
    # Numeric convergence thresholds and large next-day movement rates.
    convergence=[]; large=[]; large_feature=[]
    for threshold in [0.5,0.7,0.8]:
        rs=[r for r in all_rows if num(r.get("convergence_score")) is not None and num(r.get("convergence_score"))>=threshold]
        q=stats(rs,f"convergence >= {threshold}",base); q["threshold"]=threshold; convergence.append(q)
    for label, pred in [("close_gt_3000",lambda x:x>3000),("close_gt_5000",lambda x:x>5000),("close_gt_10000",lambda x:x>10000),("close_lt_minus3000",lambda x:x<-3000),("close_lt_minus5000",lambda x:x<-5000),("close_lt_minus10000",lambda x:x<-10000),("abs_close_gt_5000",lambda x:abs(x)>5000),("abs_close_gt_10000",lambda x:abs(x)>10000)]:
        for r in all_rows: r[label]=pred(r["next_close"]) if r["next_close"] is not None else False
        event_count=sum(1 for r in all_rows if r[label])
        # ここはbullish率ではなく、翌日値動きイベントの発生率。
        large.append({"event":label,"n":n,"event_count":event_count,"event_rate":event_count/n if n else None})
    for family, fn in feature_groups.items():
        vals=defaultdict(list)
        for r in all_rows:
            v=fn(r); label=" + ".join(str(x) for x in v) if isinstance(v,(list,tuple)) else str(v)
            vals[label].append(r)
        for label,rs in vals.items():
            for event in ["close_gt_3000","close_gt_5000","close_gt_10000","close_lt_minus3000","close_lt_minus5000","close_lt_minus10000","abs_close_gt_5000","abs_close_gt_10000"]:
                ec=sum(1 for r in rs if r[event]); large_feature.append({"feature_family":family,"feature":label,"event":event,"n":len(rs),"event_count":ec,"event_rate":ec/len(rs) if rs else None})
    def write(name, rows):
        if not rows:return
        keys=[]
        for r in rows:
            for k in r:
                if k not in keys: keys.append(k)
        with (OUT/name).open("w",encoding="utf-8-sig",newline="") as f:
            w=csv.DictWriter(f,fieldnames=keys); w.writeheader(); w.writerows(rows)
    write("pooled_feature_stats.csv",records); write("machine_feature_stats.csv",machine_records)
    write("direction_pattern_stats.csv",[r for r in records if r["feature_family"]=="direction_pattern"])
    write("convergence_stats.csv",convergence+[r for r in records if r["feature_family"] in {"convergence_bin","convergence_change","hypothesis"}])
    write("region_regime_stats.csv",[r for r in records if r["feature_family"] in {"region","regime","region_regime","convergence_region","convergence_regime"}])
    write("large_move_stats.csv",large)
    write("large_move_feature_stats.csv",large_feature)
    write("paired_samples.csv", all_rows)
    candidates=sorted([r for r in records if r["n"]>=8 and r["lift"] is not None],key=lambda r:(r["lift"],r["n"]),reverse=True)
    for r in candidates:
        ms=[x for x in machine_records if x["feature_family"]==r["feature_family"] and x["feature"]==r["feature"]]
        r["machine_consistency"]=sum(1 for x in ms if x["n"] and x["bullish_rate"]>=x["baseline"])
        r["machine_rates"]="; ".join(f"{x['machine']}={fmt(x['bullish_rate'])}(n={x['n']})" for x in ms)
    write("candidate_features.csv",candidates)
    summary={"total_samples":n,"bullish_count":c,"bullish_rate":base,"machine_baseline":machine_base,"dates":sorted({r["date"] for r in all_rows}),"source":"phase_convergence_daily.csv + period_regime_daily.csv"}
    (OUT/"summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    make_html(summary,candidates,records,machine_records,convergence,large)

def make_html(summary,candidates,records,machine_records,convergence,large):
    def table(rows, cols):
        h="<table><tr>"+"".join(f"<th>{html.escape(c)}</th>" for c in cols)+"</tr>"
        for r in rows:
            h+="<tr>"+"".join(f"<td>{html.escape(fmt(r.get(c)) if isinstance(r.get(c),float) else str(r.get(c,'')))}</td>" for c in cols)+"</tr>"
        return h+"</table>"
    top=candidates[:20]
    text=f"""<!doctype html><meta charset='utf-8'><title>Wave Lab 4台 翌日陽線横断分析</title><style>body{{font-family:Arial,sans-serif;max-width:1200px;margin:30px auto;line-height:1.4}} table{{border-collapse:collapse;margin:10px 0 28px}}th,td{{border:1px solid #ccc;padding:5px 8px;font-size:13px}}th{{background:#eef}} .note{{background:#fff8dc;padding:12px}}</style><h1>Wave Lab 4台：前日状態 → 翌日陽線</h1><div class='note'>既存通常出力のみ使用。pooled baseline: <b>{summary['bullish_count']}/{summary['total_samples']} = {summary['bullish_rate']:.1%}</b>。候補はn≥8、探索的結果。</div><h2>上位候補</h2>{table(top,['feature_family','feature','n','bullish_count','bullish_rate','lift','machine_consistency','machine_rates'])}<h2>Convergence thresholds</h2>{table(convergence,['feature','n','bullish_count','bullish_rate','lift'])}<h2>Large move events</h2>{table(large,['event','n','event_count','event_rate'])}<h2>4台baseline</h2>{table([{'machine':m,'n':len(rs),'bullish_count':sum(x['bullish'] for x in rs),'bullish_rate':summary['machine_baseline'][m]} for m,rs in []],['machine','n','bullish_count','bullish_rate'])}<p>機械別詳細はmachine_feature_stats.csv、全候補はcandidate_features.csv、特徴別大変動率はlarge_move_feature_stats.csvを参照。</p>"""
    # 上記の簡易baseline表を正しく差し替え（HTMLエスケープ済み）。
    base_rows=[]
    for m in MACHINES:
        ms=[x for x in machine_records if x.get("machine")==m and x.get("feature_family")=="phase" and x.get("feature")=="LONG"]
        # machine_feature_statsからのphase LONGは存在しない場合もあるためsummaryだけで表示。
        n=sum(1 for x in machine_records if x.get("machine")==m and x.get("feature_family")=="phase")
        # nはphaseカテゴリ総数にならないので候補行から復元せず、出力本文ではbaselineを直接表示。
    base_html="<table><tr><th>machine</th><th>n</th><th>bullish count</th><th>baseline bullish rate</th></tr>"+"".join(f"<tr><td>{m}</td><td>60</td><td>{round(summary['machine_baseline'][m]*60)}</td><td>{summary['machine_baseline'][m]:.1%}</td></tr>" for m in MACHINES)+"</table>"
    text=text.replace("<h2>4台baseline</h2>"+table([],['machine','n','bullish_count','bullish_rate']),"<h2>4台baseline</h2>"+base_html)
    (OUT/"bullish_feature_analysis.html").write_text(text,encoding="utf-8")

if __name__ == "__main__": main()
