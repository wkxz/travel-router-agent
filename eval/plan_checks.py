"""行程合理性检查：查「走得通但不合理」的排法，输入是 render_dict 输出的行程（plan JSON）。

压力测试（eval/stress.py）只查走不走得通，2026-10-30 用户实测指出的问题一个也没查出来，这里把它们写成确定性规则：

- reentry：同一天先去 A、中间去了远处的 B、再回到 A 旁边的 C（老门东 → 总统府 → 夫子庙；中山陵 → 南博 → 明孝陵）
- outdoor_after_sunset：白天的户外景点在日落后半小时还没游完（11 月底 16:38 进明孝陵，17:05 天黑）
- repeat_cuisine_day：同一天午餐、晚餐是同一类菜（百味斋素菜馆 + 鸡鸣寺素面馆）
- meal_too_close：两顿吃的（正餐 / 小吃）间隔太短（午饭 14:25 吃完、14:35 又排小吃）。由 eval/review.py 的模型点评发现，
  第 2 轮测评 7 个城市都有，核实后写成规则

用法：
  ./.venv/Scripts/python.exe eval/plan_checks.py eval/results/round2     # 检查测评结果里每轮最后一份行程，不产生费用
  from eval.plan_checks import check_plan
  issues = check_plan(plan, poi_types)   # poi_types：poi_id 或名称 -> 高德分类，可省略（只按店名认菜系）
"""
import glob
import json
import os
import re
import sys
from datetime import date
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app._data.trip_input import INDOOR_WORDS  # noqa: E402
from app.engine import Restaurant, Spot  # noqa: E402
from app.engine.calendar_pack import is_sub_spot  # noqa: E402
from app.engine.geo import haversine_distance, sunset_minutes  # noqa: E402
from app.engine.scheduler import cuisine_key  # noqa: E402

REENTRY_NEAR_KM = 1.5   # 两个点相距不超过它算同一片区（比分天时的 1.2km 稍宽：这里没有门区坐标，只有中心点）
REENTRY_AWAY_KM = 1.5   # 中间那个点离两边都超过它才算「离开了这片区」（南博离明孝陵 1.8km，取 2km 会漏掉）
SUNSET_GRACE_MIN = 30   # 日落后这么久内结束不算：天还没全黑
MEAL_GAP_MIN = 90       # 上一顿吃完到下一顿开始至少隔这么久


def _minutes(slot: str) -> List[int]:
    return [int(h) * 60 + int(m) for h, m in re.findall(r"(\d{2}):(\d{2})", slot or "")]


def _km(a: dict, b: dict) -> float:
    return haversine_distance(a["lng"], a["lat"], b["lng"], b["lat"])


def _same_area(a: dict, b: dict) -> bool:
    sa, sb = (Spot(name=x["name"], lng=x["lng"], lat=x["lat"]) for x in (a, b))
    return _km(a, b) <= REENTRY_NEAR_KM or is_sub_spot(sa, sb) or is_sub_spot(sb, sa)


def _reentry(day: dict) -> List[dict]:
    spots = [it["location"] for it in day["items"] if it["category"] == "spot" and it.get("location")]
    issues = []
    for i in range(len(spots)):
        for k in range(i + 2, len(spots)):
            if not _same_area(spots[i], spots[k]):
                continue
            away = [spots[j] for j in range(i + 1, k)
                    if _km(spots[j], spots[i]) > REENTRY_AWAY_KM and _km(spots[j], spots[k]) > REENTRY_AWAY_KM]
            if away:
                issues.append({"type": "reentry", "day": day["day_number"],
                               "detail": f"{spots[i]['name']} → {away[0]['name']} → {spots[k]['name']}："
                                         f"首尾相距 {_km(spots[i], spots[k]):.1f}km，中间去了 {_km(spots[i], away[0]):.1f}km 外"})
    return issues


def _category(loc: dict, poi_types: Dict[str, str]) -> str:
    return poi_types.get(loc.get("poi_id") or "") or poi_types.get(loc["name"], "")


def _is_indoor(name: str, category: str) -> bool:
    return any(w in f"{name} {category}" for w in INDOOR_WORDS)


def _outdoor_after_sunset(day: dict, poi_types: Dict[str, str]) -> List[dict]:
    if not day.get("date"):
        return []
    d = date.fromisoformat(day["date"])
    issues = []
    for it in day["items"]:
        m = _minutes(it["time_slot"])
        if it["category"] == "meal" and m and m[0] >= 17 * 60:
            break  # 晚饭（晚餐 / 夜市逛吃 / 告别餐）之后的是有意安排的夜游
        loc = it.get("location")
        if it["category"] != "spot" or not loc or _is_indoor(loc["name"], _category(loc, poi_types)):
            continue
        if any("日落前后" in w for w in it.get("warnings") or []):
            continue  # 有意去看日落的（best_period="sunset"）
        sunset = sunset_minutes(loc["lat"], loc["lng"], d)
        if len(m) == 2 and m[1] > sunset + SUNSET_GRACE_MIN:
            issues.append({"type": "outdoor_after_sunset", "day": day["day_number"],
                           "detail": f"{loc['name']} {it['time_slot']}，当天日落约 {sunset // 60:02d}:{sunset % 60:02d}"})
    return issues


def _repeat_cuisine(day: dict, poi_types: Dict[str, str]) -> List[dict]:
    seen: Dict[str, str] = {}
    issues = []
    for it in day["items"]:
        loc = it.get("location")
        if it["category"] != "meal" or not loc:
            continue
        category = _category(loc, poi_types)
        key = cuisine_key(Restaurant(name=loc["name"], lng=0, lat=0, category=category,
                                     cuisine=category.split(";")[-1] if category else ""))
        if key and key in seen:
            issues.append({"type": "repeat_cuisine_day", "day": day["day_number"],
                           "detail": f"{seen[key]}、{loc['name']} 都是{key}"})
        elif key:
            seen[key] = loc["name"]
    return issues


def _meal_too_close(day: dict) -> List[dict]:
    issues, prev = [], None
    for it in day["items"]:
        if it["category"] not in ("meal", "snack"):
            continue
        m = _minutes(it["time_slot"])
        if len(m) != 2:
            continue
        if prev and m[0] - prev[1] < MEAL_GAP_MIN:
            issues.append({"type": "meal_too_close", "day": day["day_number"],
                           "detail": f"{prev[0]} 吃完 {m[0] - prev[1]} 分钟后又排了 {it['title']}（{it['time_slot']}）"})
        prev = (it["title"], m[1])
    return issues


def check_plan(plan: dict, poi_types: Optional[Dict[str, str]] = None) -> List[dict]:
    """返回问题列表，每项 {type, day, detail}；没有问题时为空。"""
    poi_types = poi_types or {}
    issues = []
    for day in plan.get("days", []):
        issues += (_reentry(day) + _outdoor_after_sunset(day, poi_types) + _repeat_cuisine(day, poi_types)
                   + _meal_too_close(day))
    return issues


def poi_types_from_tools(tool_calls: List[dict]) -> Dict[str, str]:
    """从测评记录的检索工具返回里收集 poi_id -> 高德分类，供菜系 / 室内判断用。"""
    types = {}
    for c in tool_calls:
        if c.get("tool") not in ("search_scenic_spots", "search_food_and_snacks"):
            continue
        try:
            out = json.loads(c.get("output") or "{}")
        except ValueError:
            continue
        for r in out.get("spots", []) + out.get("restaurants", []):
            if r.get("id"):
                types[r["id"]] = r.get("type", "")
    return types


def last_plans(record: dict) -> List[tuple]:
    """测评记录里每轮最后一份行程：[(轮次序号, plan, poi_types)]。poi_types 累积前面各轮的检索结果。"""
    out, calls = [], []
    for i, turn in enumerate(record.get("turns", [])):
        calls += turn.get("tools", [])
        if turn.get("plans"):
            out.append((i + 1, turn["plans"][-1], poi_types_from_tools(calls)))
    return out


def main(results_dir: str) -> None:
    total = {}
    for path in sorted(glob.glob(os.path.join(results_dir, "*.json"))):
        with open(path, encoding="utf-8") as f:
            record = json.load(f)
        for turn_no, plan, types in last_plans(record):
            issues = check_plan(plan, types)
            print(f"{record.get('id', os.path.basename(path))} 第{turn_no}轮：{len(issues)} 个问题")
            for it in issues:
                total[it["type"]] = total.get(it["type"], 0) + 1
                print(f"  第{it['day']}天 [{it['type']}] {it['detail']}")
    print("\n合计：", total or "无")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "eval/results")
