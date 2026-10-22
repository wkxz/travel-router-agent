"""POI 仓库：模型只拿到 id，坐标、评分、营业时间等事实数据只在这里流转。

- 搜索工具把高德结果写进仓库，只把 id + 摘要返回给模型；
- 排程工具只接收 id，从仓库取数据，模型无从改写坐标或评分；
- 缓存未命中（如多轮对话的新请求）时按 id 回查高德：
  POI id 走 /place/detail（实测返回与检索一致的 name/location/biz_ext），
  地理编码 id 形如「geo:地址|城市」，重新编码即可。
  编造的 id 查不到，只会报错，不会产生假数据。
"""

import threading
from dataclasses import dataclass, field, replace
from typing import Callable, Dict, List, Optional, Tuple

from .amap import amap_get, parse_location, poi_open_time, to_float, to_text

Fetch = Callable[[str, dict], dict]


@dataclass
class PoiRecord:
    id: str
    name: str
    lng: float
    lat: float
    address: str = ""
    category: str = ""  # 高德完整分类，如「餐饮服务;中餐厅;川菜」
    rating: Optional[float] = None
    cost: Optional[float] = None
    open_time: str = ""
    tags: List[str] = field(default_factory=list)
    meal_type: str = ""  # 由哪种餐饮检索得到：meal / snack / night_market；非餐饮为空
    search_keyword: str = ""  # 命中这条结果的检索词，用于匹配用户的必吃美食

    @property
    def cuisine(self) -> str:
        return self.category.split(";")[-1] if self.category else ""

    def brief(self) -> dict:
        """返回给模型的摘要：不含坐标。"""
        return {
            "id": self.id,
            "name": self.name,
            "address": self.address,
            "type": self.cuisine,
            "rating": self.rating,
            "cost": self.cost,
            "open_time": self.open_time,
        }


def record_from_poi(p: dict, meal_type: str = "", search_keyword: str = "") -> Optional[PoiRecord]:
    """高德 POI → PoiRecord；缺 id/名称/有效坐标时返回 None。"""
    coords = parse_location(p.get("location"))
    if not p.get("id") or not p.get("name") or not coords:
        return None
    biz = p.get("biz_ext") if isinstance(p.get("biz_ext"), dict) else {}
    return PoiRecord(
        id=str(p["id"]),
        name=p["name"],
        lng=coords[0],
        lat=coords[1],
        address=to_text(p.get("address")),
        category=to_text(p.get("type")),
        rating=to_float(biz.get("rating")),
        cost=to_float(biz.get("cost")),
        open_time=poi_open_time(biz),
        tags=[t for t in to_text(p.get("tag")).split(";") if t][:3],
        meal_type=meal_type,
        search_keyword=search_keyword,
    )


def geo_id(address: str, city: str = "") -> str:
    return f"geo:{address}|{city}"


def geocode(address: str, city: str = "", fetch: Optional[Fetch] = None) -> Tuple[Optional[PoiRecord], str]:
    """地名 → PoiRecord（id 为 geo:地址|城市）；失败时返回 (None, 原因)。"""
    fetch = fetch or amap_get
    params = {"address": address}
    if city:
        params["city"] = city
    data = fetch("/geocode/geo", params)
    if "error" in data:
        return None, data["error"]
    geocodes = data.get("geocodes") or []
    if not geocodes:
        return None, f"未找到地点「{address}」，请核实名称或补充城市"
    g = geocodes[0]
    coords = parse_location(g.get("location"))
    if not coords:
        return None, f"高德返回的「{address}」坐标无效：{g.get('location')!r}"
    return PoiRecord(
        id=geo_id(address, city),
        name=address,
        lng=coords[0],
        lat=coords[1],
        address=to_text(g.get("formatted_address")),
    ), ""


class PoiRepo:
    """单个请求内的 POI 缓存；每个请求一份，并发请求互不干扰。"""

    def __init__(self, fetch: Optional[Fetch] = None):
        self._fetch = fetch  # None = 调用时再取 amap_get，便于测试替换
        self._items: Dict[str, PoiRecord] = {}
        self._lock = threading.Lock()  # 模型并行调用工具时会在多个线程里写入

    def put(self, rec: PoiRecord) -> PoiRecord:
        """写入；同一 POI 被多次检索到时，保留已有的餐饮类型与检索词。"""
        with self._lock:
            old = self._items.get(rec.id)
            if old:
                rec = replace(rec, meal_type=rec.meal_type or old.meal_type,
                              search_keyword=rec.search_keyword or old.search_keyword)
            self._items[rec.id] = rec
        return rec

    def get(self, poi_id: str) -> Tuple[Optional[PoiRecord], str]:
        """按 id 取 POI；未命中时回查高德。返回 (记录, 失败原因)。"""
        poi_id = (poi_id or "").strip()
        if not poi_id:
            return None, "id 为空"
        with self._lock:
            rec = self._items.get(poi_id)
        if rec:
            return rec, ""

        if poi_id.startswith("geo:"):
            address, _, city = poi_id[4:].partition("|")
            rec, err = geocode(address, city, fetch=self._fetch)
        else:
            data = (self._fetch or amap_get)("/place/detail", {"id": poi_id})
            if "error" in data:
                return None, data["error"]
            pois = data.get("pois") or []
            rec = record_from_poi(pois[0]) if pois else None
            err = "" if rec else f"id「{poi_id}」不存在，请使用搜索或地理编码工具返回的 id"
        if not rec:
            return None, err
        return self.put(rec), ""


@dataclass
class TripContext:
    """随 Runner.run_streamed(context=...) 传入，工具通过 ctx.context 取用。"""
    repo: PoiRepo = field(default_factory=PoiRepo)
