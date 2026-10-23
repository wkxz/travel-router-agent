"""对比两轮测评（改动前后）：python eval/compare.py <旧结果目录> <新结果目录>"""
import glob, json, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from analyze import plan_metrics


def summary(path):
    r = json.load(open(path))
    rows = []
    for t in r["turns"]:
        gens = sum(c["tool"] == "generate_trip_itinerary" for c in t["tools"])
        m = plan_metrics(t["plans"][-1]) if t["plans"] else None
        det = [x for d in (m["days"] if m else []) for x in d["meal_detours"]]
        rows.append({
            "s": t["seconds"], "tools": len(t["tools"]), "gens": gens,
            "unassigned": len(m["unassigned"]) if m else "-", "unmet": len(m["unmet"]) if m else "-",
            "detour>1km": len(det), "max_detour": max((x[0] for x in det), default=0),
            "selfpick": sum(d["self_pick_meals"] for d in m["days"]) if m else "-",
            "idle_h": round(sum(d["idle_min"] for d in m["days"]) / 60, 1) if m else "-",
            "closed_note": sum("闭馆" in n for n in m["notes"]) if m else "-",
        })
    return rows


old, new = sys.argv[1], sys.argv[2]
keys = ["s", "tools", "gens", "unassigned", "unmet", "detour>1km", "max_detour", "selfpick", "idle_h", "closed_note"]
print("用例/轮".ljust(26), " | ".join(k.rjust(10) for k in keys))
for f in sorted(glob.glob(f"{new}/*.json")):
    name = os.path.basename(f)[:-5]
    o = summary(f"{old}/{name}.json") if os.path.exists(f"{old}/{name}.json") else []
    n = summary(f)
    for i, nr in enumerate(n):
        orow = o[i] if i < len(o) else {}
        print(f"{name}#{i + 1}".ljust(26), " | ".join(f"{orow.get(k, '?')}→{nr[k]}".rjust(10) for k in keys))
