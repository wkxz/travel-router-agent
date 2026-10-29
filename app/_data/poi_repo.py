"""POI 仓库：模型只拿到 id，坐标、评分、营业时间等事实数据只在这里流转。

- 搜索工具把高德结果写进仓库，只把 id + 摘要返回给模型；
- 排程工具只接收 id，从仓库取数据，模型无从改写坐标或评分；
- 缓存未命中（如多轮对话的新请求）时按 id 回查高德：
  POI id 走 /place/detail（实测返回与检索一致的 name/location/biz_ext），
  地理编码 id 形如「geo:地址|城市」，重新编码即可。
  编造的 id 查不到，只会报错，不会产生假数据。

大型景区的出入口（门区）：检索结果自带导航入口 entr_location；风景名胜类再用 v5 详情的 children
取门区子 POI（实测玄武湖有「玄武湖景区(玄武门)」「(解放门)」，typecode 9914xx）。排程时按最近的门区算通勤，
不用几何中心点（见 engine.geo.gate_pair）。
"""

import re
import threading
from dataclasses import dataclass, field, replace
from typing import Callable, Dict, List, Optional, Tuple

from .amap import amap_get, parse_location, poi_open_time, to_float, to_text
from ..engine.geo import haversine_distance

Fetch = Callable[[str, dict], dict]
Gate = Tuple[str, float, float]  # (门名，可为空串, lng, lat)

# 2026-10-29 实测 v5 /place/detail?show_fields=children：
# - 玄武湖景区：玄武门 991401、解放门 991400（subtype「门」），其余是停车场、游客中心；
# - 圆明园遗址公园：没有「门」类子点，但有「东门售票处」「藻园门售票处」「绮春园宫门售票处」（070306）；
# - 停车场不当门区：有的在园内（「玄武湖公园内部停车场」），步行游客也不从那里进。
GATE_TYPE_PREFIX = "9914"
TICKET_OFFICE_TYPE = "070306"
SCENIC_CATEGORY = "风景名胜"
GATE_MAX_KM = 5.0     # 离景区中心更远的子点视为数据异常
GATE_MERGE_KM = 0.15  # 相距这么近的出入口视为同一个（导航入口常与某个门重合）


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
    gates: List[Gate] = field(default_factory=list)  # 出入口；只对景点有意义
    gates_loaded: bool = False  # 是否已查过 v5 门区子点（每个 POI 只查一次）

    @property
    def is_scenic(self) -> bool:
        return any(part.startswith(SCENIC_CATEGORY) for part in self.category.split("|"))

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
        gates=_entrance_gates(p, coords),
    )


def _entrance_gates(p: dict, coords: Tuple[float, float]) -> List[Gate]:
    """检索结果自带的导航入口 entr_location（没有名称）。西湖实测：中心点在西南山里，入口在 3km 外的南山路一侧。"""
    entr = parse_location(p.get("entr_location"))
    if not entr or haversine_distance(coords[0], coords[1], entr[0], entr[1]) > GATE_MAX_KM:
        return []
    return [("", entr[0], entr[1])]


def gate_label(child_name: str, parent_name: str) -> str:
    """「玄武湖景区(玄武门)」→「玄武门」，「圆明园遗址公园东门售票处」→「东门」。"""
    label = child_name.replace(parent_name, "", 1) if child_name.startswith(parent_name) else child_name
    label = re.sub(r"售票处$", "", label)
    return label.strip("()（）-·— ") or child_name


def gates_from_children(parent: PoiRecord, children: list) -> List[Gate]:
    """v5 children → 门区：「门」类子点，以及名称带「门」的景点售票处。"""
    gates = []
    for c in children or []:
        codes = to_text(c.get("typecode")).split("|")
        name = to_text(c.get("name"))
        is_gate = any(code.startswith(GATE_TYPE_PREFIX) for code in codes)
        is_gate_ticket = TICKET_OFFICE_TYPE in codes and "门" in name.replace(parent.name, "")
        coords = parse_location(c.get("location"))
        if not (is_gate or is_gate_ticket) or not coords or not name:
            continue
        if haversine_distance(parent.lng, parent.lat, coords[0], coords[1]) > GATE_MAX_KM:
            continue
        gates.append((gate_label(name, parent.name), coords[0], coords[1]))
    return gates


def merge_gates(gates: List[Gate]) -> List[Gate]:
    """去掉重合的出入口，有名字的优先保留（导航入口与某个门重合时用门名）。"""
    out: List[Gate] = []
    for g in sorted(gates, key=lambda g: not g[0]):
        if all(haversine_distance(g[1], g[2], o[1], o[2]) > GATE_MERGE_KM for o in out):
            out.append(g)
    return out


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
                              search_keyword=rec.search_keyword or old.search_keyword,
                              gates=old.gates if old.gates_loaded else rec.gates,
                              gates_loaded=old.gates_loaded)
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

    def with_gates(self, rec: PoiRecord) -> PoiRecord:
        """风景名胜类景点补查 v5 门区子点并缓存；查询失败（如 Key 未开通 v5）时沿用已有的导航入口，不影响排程。"""
        if rec.gates_loaded or not rec.is_scenic or rec.id.startswith("geo:"):
            return rec
        data = (self._fetch or amap_get)("/v5/place/detail", {"id": rec.id, "show_fields": "children"})
        pois = [] if "error" in data else (data.get("pois") or [])
        children = (pois[0].get("children") if pois else None) or []
        rec = replace(rec, gates=merge_gates(gates_from_children(rec, children) + rec.gates), gates_loaded=True)
        with self._lock:
            self._items[rec.id] = rec
        return rec


@dataclass
class TripContext:
    """随 Runner.run_streamed(context=...) 传入，工具通过 ctx.context 取用。"""
    repo: PoiRepo = field(default_factory=PoiRepo)
