"""把模型给出的 id 引用转换成排程引擎的输入。

模型能决定的只有「选哪些 id」和基于常识的判断（游玩时长、宜早/宜晚、餐饮类型）；
名称、坐标、评分、人均、营业时间一律取自 PoiRepo。
"""

import json
from dataclasses import dataclass, field
from typing import Any, List, Optional, Tuple

from ..engine import LocationPoint, Spot, Restaurant
from .poi_repo import PoiRepo, PoiRecord

MEAL_TYPES = ("meal", "snack", "night_market")
DURATION_RANGE = (15, 600)


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


def to_spot(rec: PoiRecord, ref: dict, default_duration: int) -> Spot:
    period = ref.get("best_period")
    return Spot(
        name=rec.name, lng=rec.lng, lat=rec.lat, address=rec.address, poi_id=rec.id,
        duration_minutes=_duration(ref.get("duration_minutes"), default_duration),
        open_time=rec.open_time, rating=rec.rating, cost=rec.cost, tags=list(rec.tags),
        best_period=period if period in ("morning", "evening") else "",
        must_visit=ref.get("must") in (True, "true", "True", 1, "1"),
        gates=[LocationPoint(name=name, lng=lng, lat=lat) for name, lng, lat in rec.gates],
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
    for ref in parse_refs(spots):
        rec, err = repo.get(ref["id"])
        if not rec:
            out.skipped.append(f"{ref['id']}（{err}）")
        elif rec.id not in seen:
            seen.add(rec.id)
            out.spots.append(to_spot(repo.with_gates(rec), ref, default_duration=120))

    for ref in parse_refs(backup_spots):
        rec, _ = repo.get(ref["id"])  # 备选景点用不上也不算未安排，识别失败直接忽略
        if rec and rec.id not in seen:
            seen.add(rec.id)
            out.backups.append(to_spot(repo.with_gates(rec), ref, default_duration=60))

    avoid_words = split_must_eat(avoid)
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
        out.notes.append(f"按忌口（{'、'.join(avoid_words)}）排除了 {len(by_diet)} 家候选餐厅："
                         + "、".join(by_diet[:5]) + ("等" if len(by_diet) > 5 else "") + "。")
    if by_cost:
        out.notes.append(f"按人均 ¥{max_cost_per_meal:.0f} 以内排除了 {len(by_cost)} 家："
                         + "、".join(by_cost[:5]) + ("等" if len(by_cost) > 5 else "") + "。")
    if unknown_cost:
        out.notes.append(f"有 {unknown_cost} 家候选没有人均数据，无法确认是否在预算内，已保留并标注。")
    return out
