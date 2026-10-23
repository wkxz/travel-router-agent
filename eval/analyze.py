"""测评结果分析：python eval/analyze.py <结果目录>

逐用例输出：耗时、工具调用分布、重复调用、排程参数与次数、空结果检索；每天的景点/路程/最短顺序对比/空档/
最长单程/周边自选餐数/估算路段/绕路 >1km 的餐点；未安排与未满足的必吃；回复中在工具数据里找不到的店名（疑似编造）。
"""
import glob, itertools, json, math, re, sys
from collections import Counter


def hv(a, b):
    R = 6371.0088
    p1, p2 = math.radians(a["lat"]), math.radians(b["lat"])
    dp, dl = p2 - p1, math.radians(b["lng"] - a["lng"])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def minutes(slot):
    m = re.findall(r"(\d{2}):(\d{2})", slot)
    return [int(h) * 60 + int(mm) for h, mm in m]


def leg_minutes(info):
    m = re.search(r"约 (\d+) 分钟", info or "")
    return int(m.group(1)) if m else 0


def plan_metrics(plan):
    out = {"days": [], "unassigned": plan.get("unassigned_spots", []), "unmet": plan.get("unmet_must_eat", []),
           "notes": plan.get("notes", [])}
    H = plan.get("hotel")
    for d in plan["days"]:
        items = d["items"]
        spots = [it["location"] for it in items if it["category"] == "spot"]
        idle = sum((lambda m: m[1] - m[0] if len(m) == 2 else 0)(minutes(it["time_slot"])) for it in items if it["category"] == "free")
        def tour(seq):
            pts = [H, *seq, H]
            return sum(hv(a, b) for a, b in zip(pts, pts[1:]))
        best = min((tour(p) for p in itertools.permutations(spots)), default=0) if H and len(spots) <= 7 else None
        locs = [it for it in items if it["location"]]
        detours = []
        for i in range(1, len(locs) - 1):
            if locs[i]["category"] in ("meal", "snack"):
                a, m, b = locs[i - 1]["location"], locs[i]["location"], locs[i + 1]["location"]
                ex = hv(a, m) + hv(m, b) - hv(a, b)
                if ex > 1.0:
                    detours.append((round(ex, 1), locs[i]["title"][:24]))
        out["days"].append({
            "date": d["date"], "spots": [s["name"] for s in spots], "km": d["estimated_distance_km"],
            "order_km": round(tour(spots), 1) if H else None, "best_km": round(best, 1) if best is not None else None,
            "idle_min": idle, "max_leg_min": max((leg_minutes(it["transit_info"]) for it in items), default=0),
            "self_pick_meals": sum(1 for it in items if it["category"] in ("meal", "snack") and not it["location"]),
            "estimated_legs": sum(1 for it in items if "估算" in (it["transit_info"] or "")),
            "meal_detours": detours,
            "meals": [(it["title"][:30], it.get("cost")) for it in items if it["category"] in ("meal", "snack")],
            "end": items[-1]["time_slot"],
        })
    return out


def fabricated(text, tool_outputs):
    blob = "\n".join(tool_outputs)
    names = set(re.findall(r"【([^】]{2,40})】", text))
    names |= set(re.findall(r"([一-龥A-Za-z·]{2,20}[（(][^）)]{1,15}店[）)])", text))
    return sorted(n for n in names if n not in blob and re.sub(r"[（(].*", "", n) not in blob)


def main(d):
    for f in sorted(glob.glob(f"{d}/*.json")):
        r = json.load(open(f))
        print("=" * 100, "\n", r["id"])
        for ti, t in enumerate(r["turns"]):
            tools = Counter(c["tool"] for c in t["tools"])
            dup = sum(v - 1 for v in Counter((c["tool"], c["args"]) for c in t["tools"]).values() if v > 1)
            gens = [c for c in t["tools"] if c["tool"] == "generate_trip_itinerary"]
            print(f" turn{ti + 1}: {t['seconds']}s llm_turns={t['llm_turns']} tools={dict(tools)} dup={dup} err={t['error']}")
            for g in gens:
                a = json.loads(g["args"] or "{}")
                ok = json.loads(g.get("output") or "{}")
                print(f"   gen: days={a.get('total_days')} start={a.get('start_date')!r} pace={a.get('pace')!r} "
                      f"dep={a.get('departure_id')!r}@{a.get('departure_time')!r} must_eat={a.get('must_eat')!r} "
                      f"n_spots={len(json.loads(a.get('spots') or '[]'))} n_foods={len(json.loads(a.get('foods') or '[]'))} "
                      f"backups={len(json.loads(a.get('backup_spots') or '[]'))} success={ok.get('success')} {ok.get('error', '')[:120]}")
            failed_search = [(c["tool"], json.loads(c["args"]).get("keywords"), (c.get("output") or "")[:80])
                             for c in t["tools"] if c["tool"].startswith("search") and ('"count": 0' in (c.get("output") or "") or '"error"' in (c.get("output") or ""))]
            if failed_search:
                print("   empty/err searches:", failed_search[:6])
            if t["plans"]:
                m = plan_metrics(t["plans"][-1])
                for day in m["days"]:
                    print(f"   {day['date']} spots={day['spots']} km={day['km']} order/best={day['order_km']}/{day['best_km']} "
                          f"idle={day['idle_min']}m maxleg={day['max_leg_min']}m selfpick={day['self_pick_meals']} est={day['estimated_legs']} end={day['end']}")
                    print(f"      meals={day['meals']}")
                    if day["meal_detours"]:
                        print(f"      DETOUR>1km {day['meal_detours']}")
                print("   unassigned:", m["unassigned"], "| unmet:", m["unmet"])
                print("   notes:", m["notes"])
            outs = [c.get("output") or "" for c in t["tools"]] + [json.dumps(p, ensure_ascii=False) for p in t["plans"]]
            fab = fabricated(t["text"], outs)
            print("   names in reply not in tool data:", fab)
            print("   reply:", t["text"][:260].replace("\n", " "), "...")


if __name__ == "__main__":
    main(sys.argv[1])
