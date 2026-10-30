"""把模型给出的 id 引用转换成排程引擎的输入。

模型能决定的只有「选哪些 id」和基于常识的判断（游玩时长、宜早/宜晚、餐饮类型）；
名称、坐标、评分、人均、营业时间一律取自 PoiRepo。
"""

import json
from dataclasses import dataclass, field
from typing import Any, List, Optional, Tuple

from ..engine import LocationPoint, Spot, Restaurant
from ..engine.calendar_pack import is_sub_spot
from .poi_repo import PoiRepo, PoiRecord, gate_label

MEAL_TYPES = ("meal", "snack", "night_market")
DURATION_RANGE = (15, 600)
# 子景点（见 calendar_pack.is_sub_spot，如中山陵景区里的音乐台）模型没给时长时的默认值：
# 按独立景点的 120 分钟算，和所属景区一起放不进一天
SUB_SPOT_DEFAULT_MIN = 45
TIPS_MAX_CHARS = 120  # 景点经验建议的长度上限：卡片上只放一两句，长篇内容交给最终回复
# 室内场馆：天黑后空档补进去也不受影响（户外景点只补到日落前，见 scheduler._best_gap_spot）
# 忌口里有「辣」时补上的常见辣味菜名：店名里常常只有菜名、不带「辣」字。
# 2026-10-30 第 4 轮测评，模型自己扩了「麻辣,火锅,串串,冒菜…」，仍漏过张老二凉粉、冒烤鸭
SPICY_WORDS = ("麻辣", "香辣", "酸辣", "辣子", "剁椒", "红油", "凉粉", "冒菜", "冒烤鸭", "钵钵鸡", "兔头", "水煮",
               "毛血旺", "口水鸡", "担担面", "串串", "麻辣烫", "冷锅", "老火锅", "干锅", "香锅", "烤鱼", "肥肠粉")
INDOOR_WORDS = ("博物", "美术馆", "展览馆", "纪念馆", "科技馆", "图书馆", "书店", "购物服务", "剧院", "剧场", "音乐厅")


def parse_refs(text: Any) -> List[dict]:
    """接受 JSON 列表（元素为 id 字符串或含 id 的对象）、逗号分隔的 id，或空值；统一成 [{"id": ...}]。"""
    if not text:
        return []
    if isinstance(text, str):
        text = text.strip()
        if not text:
            return []
        items = json.loads(text) if text[0] in "[{" else [x for x in text.replace("，", ",").split(",")]
    else:
        items = text
    if isinstance(items, dict):
        items = [items]
    refs = []
    for x in items:
        if isinstance(x, dict) and x.get("id"):
            refs.append({**x, "id": str(x["id"]).strip()})
        elif isinstance(x, str) and x.strip():
            refs.append({"id": x.strip()})
    return refs


def _duration(value: Any, default: int) -> int:
    try:
        v = int(value)
    except (TypeError, ValueError):
        return default
    return max(DURATION_RANGE[0], min(DURATION_RANGE[1], v))


def _day(value: Any) -> int:
    """用户指定的第几天：正整数，其他一律视为不限（0）。超出行程天数由排程器报错。"""
    try:
        v = int(value)
    except (TypeError, ValueError):
        return 0
    return v if v >= 1 else 0


def _flag(value: Any) -> bool:
    return value in (True, "true", "True", 1, "1")


def _tips(value: Any) -> str:
    """模型给的经验建议：压成一行、截断，不做其他改写（展示时标明未经核实）。"""
    text = " ".join(str(value or "").split())
    return text[:TIPS_MAX_CHARS] + ("…" if len(text) > TIPS_MAX_CHARS else "")


def _gate_point(repo: PoiRepo, gate_id: str, parent: PoiRecord, label: str) -> Tuple[Optional[LocationPoint], str]:
    """单向参观景区的入口 / 出口（模型按常识给出、坐标来自高德 POI，如「故宫博物院-神武门」）。
    查不到时按普通出入口算，不影响排程。"""
    rec, err = repo.get(gate_id)
    if not rec:
        return None, f"【{parent.name}】的{label} id「{gate_id}」查不到（{err}），已按普通出入口计算。"
    return LocationPoint(name=gate_label(rec.name, parent.name), lng=rec.lng, lat=rec.lat, poi_id=rec.id), ""


def to_spot(rec: PoiRecord, ref: dict, default_duration: int, branch_of: str = "") -> Spot:
    period = ref.get("best_period")
    return Spot(
        name=rec.name, lng=rec.lng, lat=rec.lat, address=rec.address, poi_id=rec.id,
        duration_minutes=_duration(ref.get("duration_minutes"), default_duration),
        open_time=rec.open_time, rating=rec.rating, cost=rec.cost, tags=list(rec.tags),
        best_period="evening" if period == "sunset" else period if period in ("morning", "evening") else "",
        sunset=period == "sunset",
        must_visit=_flag(ref.get("must")),
        gates=[LocationPoint(name=name, lng=lng, lat=lat) for name, lng, lat in rec.gates],
        branch_of=branch_of, tips=_tips(ref.get("tips")), day=_day(ref.get("day")),
        is_indoor=any(w in f"{rec.name} {rec.category}" for w in INDOOR_WORDS),
    )


def match_must_eat(rec: PoiRecord, must_eat: List[str]) -> str:
    """店名 / 品类 / 检索词里包含哪样必吃美食，就归为那一样。"""
    text = " ".join((rec.name, rec.cuisine, rec.search_keyword))
    return next((dish for dish in must_eat if dish in text), "")


def to_restaurant(rec: PoiRecord, ref: dict, must_eat: List[str]) -> Restaurant:
    meal_type = ref.get("meal_type") if ref.get("meal_type") in MEAL_TYPES else (rec.meal_type or "meal")
    return Restaurant(
        name=rec.name, lng=rec.lng, lat=rec.lat, address=rec.address, poi_id=rec.id,
        meal_type=meal_type, rating=rec.rating, cost=rec.cost, open_time=rec.open_time,
        cuisine=rec.cuisine or rec.search_keyword, category=rec.category,
        dish_key=match_must_eat(rec, must_eat),
    )


def split_must_eat(text: str) -> List[str]:
    return [x.strip() for x in (text or "").replace("，", ",").replace("、", ",").split(",") if x.strip()]


@dataclass
class TripInputs:
    spots: List[Spot] = field(default_factory=list)
    restaurants: List[Restaurant] = field(default_factory=list)
    backups: List[Spot] = field(default_factory=list)
    hotel: Optional[LocationPoint] = None
    departure: Optional[LocationPoint] = None
    skipped: List[str] = field(default_factory=list)  # 「id（原因）」，并入「未安排」告知用户
    notes: List[str] = field(default_factory=list)  # 需要告知用户的候选过滤说明（忌口 / 预算）


def _place(repo: PoiRepo, poi_id: str, label: str) -> Tuple[Optional[LocationPoint], str]:
    if not poi_id:
        return None, ""
    rec, err = repo.get(poi_id)
    if not rec:
        return None, f"{label}「{poi_id}」无法识别：{err}"
    return LocationPoint(name=rec.name, lng=rec.lng, lat=rec.lat, address=rec.address, poi_id=rec.id), ""


def build_trip_inputs(
    repo: PoiRepo,
    spots: Any,
    foods: Any,
    backup_spots: Any = None,
    hotel_id: str = "",
    departure_id: str = "",
    must_eat: str = "",
    avoid: str = "",
    max_cost_per_meal: Optional[float] = None,
    city: str = "",
) -> TripInputs:
    """解析各类 id 引用。住宿/返程地点无法识别时抛 ValueError（这类错误会改变整份行程，不能静默忽略）；
    单个景点/餐厅识别失败只记入 skipped。

    avoid / max_cost_per_meal：忌口关键词与人均上限，在这里确定性地过滤餐饮候选，
    不再依赖模型自己挑（实测模型为凑「不吃辣、人均 80」反复删候选重排了 5 次）。"""
    out = TripInputs()
    out.hotel, err = _place(repo, hotel_id, "住宿地点")
    if err:
        raise ValueError(err)
    out.departure, err = _place(repo, departure_id, "返程地点")
    if err:
        raise ValueError(err)

    dishes = split_must_eat(must_eat)
    seen = set()
    defaulted: List[Spot] = []  # 模型没给时长、用了默认值的景点
    for ref in parse_refs(spots):
        rec, err = repo.get(ref["id"])
        if not rec:
            out.skipped.append(f"{ref['id']}（{err}）")
        elif rec.id not in seen:
            seen.add(rec.id)
            spot = to_spot(repo.with_gates(rec), ref, default_duration=120)
            if _flag(ref.get("any_branch")):  # 用户只说了品牌：同城其他分店作候选，排程时按路线挑
                # 经验建议是模型针对原选那家写的（如「由地下车库改造」），换成别的分店后不再适用，不带过去
                branch_ref = {k: v for k, v in ref.items() if k != "tips"}
                spot.branches = [to_spot(repo.with_gates(b), branch_ref, 120, branch_of=rec.name)
                                 for b in repo.branches(rec, city) if b.id not in seen]
            for key, label, attr in (("entry_id", "入口", "entry_points"), ("exit_id", "出口", "exit_points")):
                if ref.get(key):
                    point, msg = _gate_point(repo, str(ref[key]), rec, label)
                    if point:
                        setattr(spot, attr, [point])
                    else:
                        out.notes.append(msg)
            out.spots.append(spot)
            if ref.get("duration_minutes") is None:
                defaulted.append(spot)
    for s in defaulted:
        if any(is_sub_spot(s, p) for p in out.spots):
            s.duration_minutes = SUB_SPOT_DEFAULT_MIN

    for ref in parse_refs(backup_spots):
        rec, _ = repo.get(ref["id"])  # 备选景点用不上也不算未安排，识别失败直接忽略
        if rec and rec.id not in seen:
            seen.add(rec.id)
            out.backups.append(to_spot(repo.with_gates(rec), ref, default_duration=60))

    avoid_said = split_must_eat(avoid)
    avoid_words = avoid_said[:]
    if any("辣" in w for w in avoid_said):
        avoid_words += [w for w in SPICY_WORDS if w not in avoid_said]
    by_diet, by_cost, unknown_cost = [], [], 0
    food_seen = set()
    for ref in parse_refs(foods):
        rec, _ = repo.get(ref["id"])  # 餐厅只是候选，识别失败不影响行程，忽略即可
        if not rec or rec.id in food_seen:
            continue
        food_seen.add(rec.id)
        r = to_restaurant(rec, ref, dishes)
        hit = next((w for w in avoid_words if w in f"{r.name} {r.category} {rec.search_keyword}"), "")
        if hit and not r.dish_key:  # 用户点名的必吃不受忌口过滤
            by_diet.append(f"{r.name}（{hit}）")
            continue
        if max_cost_per_meal is not None and r.cost is not None and r.cost > max_cost_per_meal:
            by_cost.append(f"{r.name}（人均 ¥{r.cost:.0f}）")
            continue
        if max_cost_per_meal is not None and r.cost is None:
            unknown_cost += 1
        out.restaurants.append(r)
    if by_diet:
        out.notes.append(f"按忌口（{'、'.join(avoid_said)}{'等常见辣味菜' if len(avoid_words) > len(avoid_said) else ''}）排除了 {len(by_diet)} 家候选餐厅："
                         + "、".join(by_diet[:5]) + ("等" if len(by_diet) > 5 else "") + "。")
    if by_cost:
        out.notes.append(f"按人均 ¥{max_cost_per_meal:.0f} 以内排除了 {len(by_cost)} 家："
                         + "、".join(by_cost[:5]) + ("等" if len(by_cost) > 5 else "") + "。")
    if unknown_cost:
        out.notes.append(f"有 {unknown_cost} 家候选没有人均数据，无法确认是否在预算内，已保留并标注。")
    return out
