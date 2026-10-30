"""随机场景压力测试：只用直线估算的通勤，不调用模型、不发网络请求，不产生费用。

两类检查：
- 走不走得通：崩溃、校验器报错、景点重复 / 丢失、时间重叠、白天超过每日景点数（子景点不算、短景点按时长折算）、点名景点被挤掉
- 合不合理：eval/plan_checks.py 的规则（折返进同一片区、天黑后还在户外、同一天两顿同类菜），外加午饭晚于 13:45、每日空档

用法（仓库根目录）：
  ./.venv/Scripts/python.exe eval/stress.py            # 种子 1，300 例
  ./.venv/Scripts/python.exe eval/stress.py 7 1000     # 种子 7，1000 例
改引擎前后各跑一次、用同一个种子对比；数字只在同一种子下可比。
"""
import collections, os, random, re, sys, time, traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from app.engine import TripScheduler, Spot, Restaurant, LocationPoint
from app.engine.renderer import render_dict
from app.engine.scheduler import PACE_PROFILES, cap_load
from app.engine.validator import InfeasiblePlanError
from eval.plan_checks import check_plan

CENTER = (118.78, 32.05)
SPOT_HOURS = ["", "", "08:30-17:00", "09:00-17:00；周一闭馆", "周二至周日 09:00-17:00", "10:00-22:00", "13:00-21:00",
              "00:00-24:00", "18:00-23:00", "09:00-16:30"]
EVENING_HOURS = ["", "10:00-22:00", "18:00-23:00", "00:00-24:00"]
MEAL_HOURS = ["", "10:00-21:00", "11:00-14:00,17:00-21:00", "06:00-10:00"]
# 餐厅分类：同一类出现多家，才测得出菜系去重；「中餐厅」是高德的笼统分类，靠店名认菜系
CUISINES = [("本帮菜", "餐饮服务;中餐厅;本帮江浙菜"), ("川菜", "餐饮服务;中餐厅;四川菜(川菜)"),
            ("粤菜", "餐饮服务;中餐厅;广东菜(粤菜)"), ("火锅", "餐饮服务;中餐厅;火锅店"),
            ("素菜馆", "餐饮服务;中餐厅;中式素菜馆"), ("素面馆", "餐饮服务;中餐厅;中餐厅"), ("家常菜", "餐饮服务;中餐厅;中餐厅")]


def _mm(slot):
    return [int(h) * 60 + int(m) for h, m in re.findall(r"(\d{2}):(\d{2})", slot)]


def scenario(rng):
    cx, cy = CENTER
    spread = rng.choice([0.02, 0.05, 0.1, 0.2])
    pt = lambda: (cx + rng.uniform(-spread, spread), cy + rng.uniform(-spread, spread))
    spots = []  # 编号带「号」：「S1号」不是「S10号」的前缀，免得被当成子景点
    for i in range(rng.randint(2, 13)):
        period = rng.choice(["", "", "", "morning", "evening"])
        indoor = period != "evening" and rng.random() < 0.25
        lng, lat = pt()
        spots.append(Spot(name=f"S{i}号" + ("博物馆" if indoor else "景区"), lng=lng, lat=lat, is_indoor=indoor,
                          duration_minutes=rng.choice([45, 60, 90, 120, 150, 180, 240, 300]),
                          open_time=rng.choice(EVENING_HOURS if period == "evening" else SPOT_HOURS),
                          best_period=period, must_visit=rng.random() < 0.5))
    if spots and rng.random() < 0.3:  # 子景点：名称含所属景区名、就在旁边（如中山陵景区里的音乐台）
        p = rng.choice([s for s in spots if not s.is_indoor] or spots)
        spots.append(Spot(name=f"{p.name}音乐台", lng=p.lng + 0.003, lat=p.lat + 0.002, duration_minutes=45,
                          open_time="06:30-18:30", best_period=rng.choice(["", "morning"]), must_visit=True))
    rs = []
    for i in range(rng.randint(0, 25)):
        lng, lat = pt()
        cuisine, category = rng.choice(CUISINES)
        rs.append(Restaurant(name=f"R{i}{cuisine}", lng=lng, lat=lat, category=category, cuisine=category.split(";")[-1],
                             meal_type=rng.choice(["meal", "meal", "snack", "night_market"]),
                             rating=rng.choice([None, 3.8, 4.2, 4.6]), cost=rng.choice([None, 30, 80, 200]),
                             open_time=rng.choice(MEAL_HOURS)))
    days = rng.randint(1, 4)
    kw = dict(hotel=LocationPoint(name="H", lng=cx, lat=cy), start_date=f"2026-11-{rng.randint(2, 28):02d}",
              pace=rng.choice(list(PACE_PROFILES)))
    if rng.random() < 0.4:
        kw.update(departure_point=LocationPoint(name="Station", lng=cx + 0.05, lat=cy - 0.05),
                  departure_time=rng.choice(["11:00", "14:30", "17:30", "20:00"]))
    backups = []
    for i in range(rng.randint(0, 3)):
        lng, lat = pt()
        indoor = rng.random() < 0.3
        backups.append(Spot(name=f"B{i}号" + ("纪念馆" if indoor else "公园"), lng=lng, lat=lat, duration_minutes=60,
                            is_indoor=indoor))
    return days, kw, spots, rs, backups


def check_feasibility(k, plan, kw, spots, stats, problems):
    placed = [it.location.name for d in plan.days for it in d.items if it.category == "spot"]
    if len(placed) != len(set(placed)):
        problems["dup_spot"].append((k, placed))
    unassigned = {u.split("（")[0] for u in plan.unassigned_spots}
    for s in spots:
        if s.name not in placed and s.name not in unassigned:
            problems["lost_spot"].append((k, s.name))
        if s.must_visit and s.name in unassigned and any(not x.must_visit and x.name in placed for x in spots):
            stats["must_dropped_while_optional_kept"] += 1
    cap = PACE_PROFILES[kw["pace"]][0]
    for d in plan.days:
        prev_end = 0
        for it in d.items:
            m = _mm(it.time_slot)
            if m and m[0] < prev_end - 1 and it.category != "hotel":
                problems["overlap"].append((k, d.day_number, it.time_slot, it.title))
                break
            if m:
                prev_end = m[-1]
        daytime = [it.location for it in d.items if it.category == "spot" and _mm(it.time_slot)[0] < 18 * 60]
        if cap_load(daytime) > cap + 1:  # 子景点不占名额、短景点按时长折算，与空档补点同一口径
            problems["over_cap"].append((k, d.day_number, len(daytime), cap))


def main(seed, n):
    rng = random.Random(seed)
    stats, problems, quality = collections.Counter(), collections.defaultdict(list), collections.defaultdict(list)
    times, idle, late_lunch, day_count = [], [], [], 0
    for k in range(n):
        days, kw, spots, rs, backups = scenario(rng)
        t0 = time.time()
        try:
            plan = TripScheduler("某市", days, **kw).schedule(spots, rs, backup_spots=backups)
        except InfeasiblePlanError as e:
            problems["validator_error"].append((k, str(e)[:200]))
            continue
        except ValueError:
            stats["value_error"] += 1  # 输入本身不可行（如车次太早），属预期
            continue
        except Exception:
            problems["crash"].append((k, traceback.format_exc()[-400:]))
            continue
        times.append((time.time() - t0, len(spots), days))
        stats["ok"] += 1
        check_feasibility(k, plan, kw, spots, stats, problems)
        for issue in check_plan(render_dict(plan), {r.name: r.category for r in rs}):
            quality[issue["type"]].append((k, issue["day"], issue["detail"]))
        for d in plan.days:
            day_count += 1
            lunch = [it for it in d.items if "午餐" in it.title]
            if lunch and _mm(lunch[0].time_slot)[0] > 13 * 60 + 45:
                late_lunch.append((k, d.day_number, lunch[0].time_slot))
            idle.append(sum((lambda m: m[1] - m[0])(_mm(it.time_slot)) for it in d.items if it.category == "free"))

    print(f"seed={seed} cases={n} days={day_count}", dict(stats))
    print("\n# 走不走得通（应全部为 0）")
    for key in ("crash", "validator_error", "dup_spot", "lost_spot", "overlap", "over_cap"):
        print(f"  {key}: {len(problems[key])}", problems[key][:2] if problems[key] else "")
    print("\n# 合不合理（越少越好，同一种子前后对比）")
    for key in ("reentry", "outdoor_after_sunset", "repeat_cuisine_day", "meal_too_close"):
        print(f"  {key}: {len(quality[key])}")
        for x in quality[key][:3]:
            print("     ", x)
    print(f"  late_lunch(>13:45): {len(late_lunch)}", late_lunch[:3])
    idle.sort()
    times.sort(reverse=True)
    if idle:
        print(f"  idle min/day p50 {idle[len(idle) // 2]}  p90 {idle[int(len(idle) * 0.9)]}  max {idle[-1]}")
    if times:
        print(f"\nruntime p50 {times[len(times) // 2][0]:.2f}s  p95 {times[len(times) // 20][0]:.2f}s  "
              f"max {times[0][0]:.2f}s (spots={times[0][1]} days={times[0][2]})")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 1, int(sys.argv[2]) if len(sys.argv) > 2 else 300)
