"""Agent Tools — private module (starts with _), not mapped as a route.

旅游路线生成 + 必吃推荐 Agent 工具集，基于高德地图 Web 服务 API + 确定性排程引擎。

设计原则：
  1. 所有 Key 仅从环境变量 AMAP_API_KEY 读取，绝不硬编码、绝不打印。
  2. 节流与退避重试（最小间隔 0.35s，针对 10021 限流自动退避）。
  3. 精简返回值字段（高德原始单条 POI 有 40+ 字段，在此精简到 5~8 个核心字段防 Token 爆炸）。
  4. 确定性 Python 排程工具（generate_trip_itinerary）：由代码消除折返、校验营业时间，防模型幻觉。
"""

import json
import os
import threading
import time
from typing import Annotated, Dict, Any, List

import httpx
from agents import function_tool

from .engine import (
    LocationPoint,
    Spot,
    Restaurant,
    TripScheduler,
    render_markdown,
    render_dict,
)

AMAP_BASE = "https://restapi.amap.com/v3"
TIMEOUT = 10.0

MIN_INTERVAL = 0.35
_qps_lock = threading.Lock()
_last_call_at = 0.0

QPS_EXCEEDED = "10021"

# 缓存城市 -> adcode，避免天气接口重复查询
_adcode_cache: Dict[str, str] = {}


def _throttle() -> None:
    """全局最小请求间隔，避免高德并发超限。"""
    global _last_call_at
    with _qps_lock:
        gap = MIN_INTERVAL - (time.monotonic() - _last_call_at)
        if gap > 0:
            time.sleep(gap)
        _last_call_at = time.monotonic()


def _amap_get(path: str, params: dict, retries: int = 3) -> dict:
    """统一高德 GET 请求：注入 Key、节流、QPS 限流退避重试、status 校验。"""
    key = os.getenv("AMAP_API_KEY")
    if not key:
        return {"error": "AMAP_API_KEY 未配置：请在 .env 中填写高德 Web 服务 Key"}

    last_error = ""
    for attempt in range(retries):
        _throttle()
        try:
            resp = httpx.get(
                f"{AMAP_BASE}{path}",
                params={"key": key, **params},
                timeout=TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as e:
            last_error = f"高德接口网络请求失败：{type(e).__name__}: {e}"
            time.sleep(0.6 * (attempt + 1))
            continue
        except ValueError:
            return {"error": "高德接口返回了非 JSON 内容，请稍后重试"}

        if str(data.get("status")) == "1":
            return data

        info = f"{data.get('info')}（infocode={data.get('infocode')}）"
        if str(data.get("infocode")) == QPS_EXCEEDED and attempt < retries - 1:
            last_error = f"高德接口限流，已重试 {attempt + 1} 次：{info}"
            time.sleep(1.0 * (attempt + 1))
            continue
        return {"error": f"高德接口返回错误：{info}"}

    return {"error": last_error or "高德接口连续重试失败，请稍后重试"}


def _parse_location(loc_str: str) -> tuple[float, float]:
    """解析经纬度字符串 'lng,lat' 为 (lng, lat)。"""
    try:
        parts = loc_str.split(",")
        return float(parts[0]), float(parts[1])
    except Exception:
        return 0.0, 0.0


# ========== Tool 1: 地理编码 ==========
@function_tool
def geocode_location(
    address: Annotated[str, "地点或住宿酒店名称，例如「成都春熙路」「北京王府井」"],
    city: Annotated[str, "可选。限定城市名，例如「成都」「北京」"] = "",
) -> str:
    """把地名、酒店或景点名称解析为经纬度坐标与标准地址。

    规划路线和周边搜索前，必须先获取经纬度坐标。
    """
    params: dict = {"address": address}
    if city:
        params["city"] = city

    data = _amap_get("/geocode/geo", params)
    if "error" in data:
        return json.dumps(data, ensure_ascii=False)

    geocodes = data.get("geocodes") or []
    if not geocodes:
        return json.dumps(
            {"found": False, "message": f"未找到地点「{address}」，请核实名称或补充城市"},
            ensure_ascii=False,
        )

    g = geocodes[0]
    loc = g.get("location", "")
    lng, lat = _parse_location(loc)

    return json.dumps(
        {
            "found": True,
            "name": address,
            "formatted_address": g.get("formatted_address"),
            "location": loc,
            "lng": lng,
            "lat": lat,
            "city": g.get("city") or city,
            "adcode": g.get("adcode"),
        },
        ensure_ascii=False,
    )


# ========== Tool 2: 城市天气预报 ==========
@function_tool
def get_city_weather(
    city: Annotated[str, "城市名称，例如「成都」「北京」「南京」"],
) -> str:
    """查询指定城市的未来 3~4 天天气预报。

    用于在行程中提示晴雨情况、适宜户外或室内景点。
    """
    global _adcode_cache
    adcode = _adcode_cache.get(city)

    # 1. 若无缓存，先查 adcode
    if not adcode:
        dist_data = _amap_get("/config/district", {"keywords": city, "subdistrict": 0})
        if "error" in dist_data:
            return json.dumps(dist_data, ensure_ascii=False)
        districts = dist_data.get("districts") or []
        if not districts:
            return json.dumps({"error": f"未能识别城市「{city}」的行政区划代码"}, ensure_ascii=False)
        adcode = str(districts[0].get("adcode"))
        _adcode_cache[city] = adcode

    # 2. 查询天气预报
    w_data = _amap_get("/weather/weatherInfo", {"city": adcode, "extensions": "all"})
    if "error" in w_data:
        return json.dumps(w_data, ensure_ascii=False)

    forecasts = w_data.get("forecasts") or []
    if not forecasts:
        return json.dumps({"message": f"暂无「{city}」的预报信息"}, ensure_ascii=False)

    casts = forecasts[0].get("casts") or []
    simplified_casts = [
        {
            "date": c.get("date"),
            "dayweather": c.get("dayweather"),
            "nightweather": c.get("nightweather"),
            "daytemp": c.get("daytemp"),
            "nighttemp": c.get("nighttemp"),
            "wind": f"{c.get('daywind')}风 {c.get('daypower')}级",
        }
        for c in casts[:3]
    ]

    return json.dumps(
        {
            "city": city,
            "reporttime": forecasts[0].get("reporttime"),
            "forecasts": simplified_casts,
        },
        ensure_ascii=False,
    )


# ========== Tool 3: 景点检索 ==========
@function_tool
def search_scenic_spots(
    city: Annotated[str, "城市名，例如「成都」"],
    keywords: Annotated[str, "景点关键词或景点名称，例如「宽窄巷子」「大熊猫基地」「博物馆」"],
    center: Annotated[str, "可选。中心点经纬度 'lng,lat'，若提供则优先检索周边景点"] = "",
) -> str:
    """搜索目的地城市的著名景点、公园、博物馆或街区。

    必须显式传入 extensions=all 以获取评分、门票参考及营业时间。
    返回已精简的关键属性列表。
    """
    params: dict = {
        "city": city,
        "keywords": keywords,
        "types": "110000",  # 风景名胜大类
        "offset": 6,
        "page": 1,
        "extensions": "all",
    }
    if center:
        params["location"] = center
        params["radius"] = 15000
        params["sortrule"] = "weight"
        data = _amap_get("/place/around", params)
    else:
        data = _amap_get("/place/text", params)

    if "error" in data:
        return json.dumps(data, ensure_ascii=False)

    pois = data.get("pois") or []
    if not pois:
        return json.dumps(
            {"count": 0, "message": f"在「{city}」未检索到与「{keywords}」相匹配的景点，请核对名称"},
            ensure_ascii=False,
        )

    results = []
    for p in pois[:6]:
        loc = p.get("location", "")
        lng, lat = _parse_location(loc)
        biz = p.get("biz_ext") or {}

        # 评分转换
        try:
            rating = float(biz.get("rating") or "4.5")
        except ValueError:
            rating = 4.5

        # 门票/消费
        try:
            cost = float(biz.get("cost") or "0")
        except ValueError:
            cost = 0.0

        open_time = biz.get("open_time") or biz.get("opentime2") or "08:30-17:30"
        tag_list = [t for t in (p.get("tag") or "").split(";") if t]

        results.append(
            {
                "name": p.get("name"),
                "location": loc,
                "lng": lng,
                "lat": lat,
                "address": p.get("address"),
                "rating": rating,
                "cost": cost,
                "open_time": open_time,
                "tags": tag_list[:3],
                "type": p.get("type"),
            }
        )

    return json.dumps({"count": len(results), "spots": results}, ensure_ascii=False)


# ========== Tool 4: 餐饮与特色小吃检索 ==========
@function_tool
def search_food_and_snacks(
    city: Annotated[str, "城市名，例如「成都」"],
    keywords: Annotated[str, "美食关键词，例如「南京大牌档」「盐水鸭」「鸭血粉丝」「汤包」「生煎」「特色小吃」"],
    center: Annotated[str, "中心坐标 'lng,lat'，必须传入临近景点的坐标以检索该景点周边的美食，切忌只传酒店坐标造成来回折返"] = "",
    meal_type: Annotated[str, "餐饮类型：'meal'(正餐) | 'snack'(特色小吃) | 'night_market'(夜市/小吃街)"] = "meal",
) -> str:
    """搜索附近的优质正餐、特色地方小吃或夜市。

    依据实战经验：分类码 050400 实际是咖啡饮品，因此统一使用 types=050000 结合 keywords
    与 extensions=all 获取真实评分、人均花费与营业时间。
    请务必传入对应景点或目标时段位置的 center 坐标，实现「景点就近用餐」。
    """
    params: dict = {
        "city": city,
        "keywords": keywords,
        "types": "050000",  # 餐饮服务大类
        "offset": 8,
        "page": 1,
        "extensions": "all",
    }
    if center:
        params["location"] = center
        params["radius"] = 4000
        params["sortrule"] = "weight"
        data = _amap_get("/place/around", params)
    else:
        data = _amap_get("/place/text", params)

    if "error" in data:
        return json.dumps(data, ensure_ascii=False)

    pois = data.get("pois") or []
    if not pois:
        return json.dumps(
            {"count": 0, "message": f"未检索到与「{keywords}」相符的美食餐厅，建议更换关键词"},
            ensure_ascii=False,
        )

    results = []
    for p in pois[:6]:
        loc = p.get("location", "")
        lng, lat = _parse_location(loc)
        biz = p.get("biz_ext") or {}

        try:
            rating = float(biz.get("rating") or "4.5")
        except ValueError:
            rating = 4.5

        try:
            cost = float(biz.get("cost") or ("25" if meal_type == "snack" else "80"))
        except ValueError:
            cost = 25.0 if meal_type == "snack" else 80.0

        open_time = biz.get("open_time") or biz.get("opentime2") or "10:00-22:00"

        results.append(
            {
                "name": p.get("name"),
                "location": loc,
                "lng": lng,
                "lat": lat,
                "address": p.get("address"),
                "rating": rating,
                "cost": cost,
                "open_time": open_time,
                "cuisine": p.get("type", "").split(";")[-1] if p.get("type") else keywords,
                "meal_type": meal_type,
            }
        )

    return json.dumps({"count": len(results), "restaurants": results}, ensure_ascii=False)


# ========== Tool 5: 确定性路线排程引擎 ==========
@function_tool
def generate_trip_itinerary(
    city: Annotated[str, "目的地城市名称，例如「成都」"],
    total_days: Annotated[int, "游玩总天数，例如 2 或 3"],
    hotel_name: Annotated[str, "住宿酒店或出发参考地名称，例如「春熙路全季酒店」"],
    hotel_location: Annotated[str, "住宿地坐标 'lng,lat'"],
    spots_json: Annotated[str, "由 search_scenic_spots 查出的景点 JSON 列表字符串，含 name, lng, lat, cost, open_time 等"],
    foods_json: Annotated[str, "由 search_food_and_snacks 查出的餐饮候选 JSON 列表字符串，含 name, lng, lat, meal_type, rating, cost, open_time 等"],
    departure_name: Annotated[str, "返程出发地点（如「武汉站」「成都东站」「大兴机场」），若用户未说明可留空"] = "",
    departure_location: Annotated[str, "返程出发地坐标 'lng,lat'，若有则终点直通返程枢纽"] = "",
    start_date: Annotated[str, "出行起始日期 'YYYY-MM-DD'，用于自动核验周一闭馆及日历开放状态"] = "",
    pace: Annotated[str, "游玩节奏：'relaxed'(轻松慢游，每日1~2点) | 'balanced'(均衡标准，每日2~3点) | 'compact'(特种兵打卡，每日3~4点)"] = "balanced",
) -> str:
    """调用纯 Python 排程算法，生成不折返、按天切分、带双轨餐饮（正餐+特色小吃/夜市）的完整行程。

    必须在收集完景点的坐标与餐饮候选后调用本工具。
    LLM 不应手写行程表，必须通过本工具生成严谨可行的行程。
    支持传入 departure_name/departure_location 规划返程日终点无缝接驳；
    支持传入 start_date 自动检测周一闭馆并调换游玩天数。
    """
    try:
        h_lng, h_lat = _parse_location(hotel_location)
        hotel = LocationPoint(name=hotel_name, lng=h_lng, lat=h_lat)

        dep_point = None
        if departure_name and departure_location:
            d_lng, d_lat = _parse_location(departure_location)
            dep_point = LocationPoint(name=departure_name, lng=d_lng, lat=d_lat)

        # 解析 spots
        raw_spots = json.loads(spots_json) if isinstance(spots_json, str) else spots_json
        spot_objects: List[Spot] = []
        for s in raw_spots:
            spot_objects.append(
                Spot(
                    name=s.get("name", "未命名景点"),
                    lng=float(s.get("lng", 0.0)),
                    lat=float(s.get("lat", 0.0)),
                    address=s.get("address", ""),
                    duration_minutes=int(s.get("duration_minutes", 120)),
                    open_time=s.get("open_time", "08:30-17:30"),
                    rating=float(s.get("rating", 4.5)),
                    cost=float(s.get("cost", 0.0)),
                    tags=s.get("tags", []),
                )
            )

        # 解析 foods
        raw_foods = json.loads(foods_json) if isinstance(foods_json, str) else foods_json
        rest_objects: List[Restaurant] = []
        for f in raw_foods:
            rest_objects.append(
                Restaurant(
                    name=f.get("name", "精选餐厅"),
                    lng=float(f.get("lng", 0.0)),
                    lat=float(f.get("lat", 0.0)),
                    address=f.get("address", ""),
                    meal_type=f.get("meal_type", "meal"),
                    rating=float(f.get("rating", 4.5)),
                    cost=float(f.get("cost", 60.0)),
                    open_time=f.get("open_time", "10:00-22:00"),
                    cuisine=f.get("cuisine", ""),
                    recommend_reason=f.get("recommend_reason", ""),
                )
            )

        scheduler = TripScheduler(
            city=city,
            total_days=total_days,
            hotel=hotel,
            departure_point=dep_point,
            start_date=start_date or None,
            pace=pace,
        )
        plan = scheduler.schedule(spots=spot_objects, restaurants=rest_objects)

        markdown_result = render_markdown(plan)
        dict_result = render_dict(plan)

        return json.dumps(
            {
                "success": True,
                "markdown": markdown_result,
                "data": dict_result,
            },
            ensure_ascii=False,
        )
    except Exception as e:
        return json.dumps(
            {"success": False, "error": f"排程算法执行异常：{type(e).__name__}: {str(e)}"},
            ensure_ascii=False,
        )
