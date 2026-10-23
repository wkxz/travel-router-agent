"""Agent Tools — private module (starts with _), not mapped as a route.

旅游路线生成 + 必吃推荐 Agent 工具集，基于高德地图 Web 服务 API + 确定性排程引擎。

设计原则：
  1. 高德访问统一走 _data/amap.py（Key 只从环境变量读取、节流、退避重试）。
  2. 模型只拿到 POI 的 id 和摘要，不接触坐标；排程工具只接收 id，事实数据从 PoiRepo 取，
     模型无从改写坐标、评分、营业时间。
  3. 行程表由确定性排程引擎生成并经独立校验器复核，模型不手写行程。
"""

import json
from typing import Annotated, Any, Dict, List, Optional

from agents import RunContextWrapper, function_tool

from ._logger import create_logger
from ._data.amap import amap_get, redact_secrets
from ._data.poi_repo import PoiRepo, TripContext, geocode, record_from_poi
from ._data.routing import AmapTravel
from ._data.trip_input import build_trip_inputs, split_must_eat
from .engine import TripScheduler, render_dict
from .engine.renderer import PLAN_KIND

logger = create_logger("tools")

DISPLAY_NOTE = "行程表已由系统直接展示给用户，回复中不要复述或改写行程表。"


def tool_error(ctx: RunContextWrapper, error: Exception) -> str:
    """工具调用失败（多为参数校验没通过）时告诉模型具体原因，而不是 SDK 默认的「出错了请重试」。

    实测：模型漏传必填的 total_days，只收到默认提示，只能盲猜重试。错误文本先脱敏，绝不带出密钥。
    """
    detail = f"{type(error).__name__}: {error}"
    if error.__cause__ is not None:  # SDK 的参数校验错误把具体字段（如缺 total_days）放在 __cause__ 里
        detail += f" | {type(error.__cause__).__name__}: {error.__cause__}"
    if str(error).startswith("Invalid JSON input") and error.__cause__ is None:
        # SDK 默认不记录工具参数（DONT_LOG_TOOL_DATA），校验细节被丢掉了，只能给出通用核对清单
        detail += "（参数不是合法 JSON，或某个参数类型不对：数字要传数字，id 列表要传 JSON 字符串）"
    detail = redact_secrets(detail)
    logger.error(f"[tool-error] {detail[:500]}")
    return _dumps({"success": False, "error": "工具调用失败，请按提示修正参数后重试一次：" + detail[:800]})


def extract_plan(output: Any) -> Optional[dict]:
    """从 generate_trip_itinerary 的输出里取出行程数据；不是行程或生成失败时返回 None。

    chat 流式接口据此推送 plan 事件。cloud-functions/history 里有一份同逻辑的副本
    （云函数单独部署，无法 import 本模块），改动时两边同步。
    """
    if isinstance(output, str):
        try:
            output = json.loads(output)
        except ValueError:
            return None
    if not isinstance(output, dict) or output.get("success") is not True:
        return None
    plan = output.get("plan")
    return plan if isinstance(plan, dict) and plan.get("kind") == PLAN_KIND else None

def next_step(gaps: List[dict]) -> str:
    """把缺口清单翻译成模型可直接执行的一步：并行补检索，然后只重排一次。"""
    if not gaps:
        return ("行程已完整并通过可行性校验（时间、营业、通勤、闭馆都已核对），直接回复用户，"
                "不要再调用 generate_trip_itinerary，也不要自行删减用户要求的景点；"
                "若觉得某天偏满，在回复里给出建议，由用户决定是否调整。")
    meals =[g for g in gaps if g["type"] == "meal_without_candidate" and g.get("near_id")]
    dishes = [g for g in gaps if g["type"] == "must_eat_unmet"]
    steps = []
    near_ids = list(dict.fromkeys(g["near_id"] for g in meals))
    if near_ids:
        steps.append("对这些地点各调用一次 search_food_and_snacks(keywords='美食', meal_type='meal', near_id=…)："
                     + "、".join(near_ids))
    for g in dishes:
        steps.append(f"必吃「{g['dish']}」：在行程景点附近用 keywords='{g['dish']}' 检索"
                     f"（near_id 任选：{'、'.join(g['near_ids'][:3]) or '住宿地 id'}）")
    if not steps:
        return "缺口所在位置没有可检索的 id，接受「周边自选」，直接回复用户。"
    return ("并行完成以下检索后，把新 id 加进 foods 重新调用 generate_trip_itinerary（只重排这一次；"
            "之后仍有「周边自选」就接受，直接回复用户）：" + "；".join(steps))


# 风景名胜 + 科教文化（实测：博物馆属于 140000，只传 110000 搜「南京博物院」会返回阅江楼）
SPOT_TYPES = "110000|140000"
FOOD_TYPES = "050000"  # 餐饮大类；050400/050900 的实际语义与名称不符，靠 keywords 筛选

# 缓存城市 -> adcode，避免天气接口重复查询
_adcode_cache: Dict[str, str] = {}


def _repo(ctx: RunContextWrapper) -> PoiRepo:
    context = getattr(ctx, "context", None)
    if isinstance(context, TripContext):
        return context.repo
    # 没传 context 时退化为空仓库：每次按 id 回查高德，结果仍然正确，只是多几次请求
    logger.log("[repo] run context missing, falling back to an empty PoiRepo")
    return PoiRepo()


def _dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


def _search(ctx: RunContextWrapper, *, city: str, keywords: str, types: str, near_id: str,
            radius: int, offset: int, meal_type: str = "") -> List[dict]:
    """检索 POI 并写入仓库；返回不含坐标的摘要列表。出错时抛 ValueError（消息可直接转告用户）。"""
    repo = _repo(ctx)
    params = {"city": city, "keywords": keywords, "types": types, "offset": offset, "page": 1,
              "extensions": "all"}
    if near_id:
        center, err = repo.get(near_id)
        if not center:
            raise ValueError(f"near_id「{near_id}」无法识别：{err}")
        params.update(location=f"{center.lng:.6f},{center.lat:.6f}", radius=radius, sortrule="weight")
        data = amap_get("/place/around", params)
    else:
        # 实测：city=南京 搜「西湖」不加 citylimit，返回的 6 条全是杭州；加上后只返回南京的结果
        params["citylimit"] = "true"
        data = amap_get("/place/text", params)
    if "error" in data:
        raise ValueError(data["error"])

    results = []
    for p in (data.get("pois") or [])[:offset]:
        rec = record_from_poi(p, meal_type=meal_type, search_keyword=keywords if meal_type else "")
        if rec:
            results.append(repo.put(rec).brief())
    return results


# ========== Tool 1: 地理编码 ==========
@function_tool(failure_error_function=tool_error)
def geocode_location(
    ctx: RunContextWrapper[TripContext],
    address: Annotated[str, "地点、酒店或车站名称，尽量带上所在城市或区县，例如「某市某某广场」"],
    city: Annotated[str, "可选。限定城市或区县名，可显著提高准确率"] = "",
) -> str:
    """把住宿地、返程车站/机场等地名解析为可供其他工具引用的 id。

    返回的 id 可作为 generate_trip_itinerary 的 hotel_id / departure_id，
    或作为搜索工具的 near_id。
    """
    rec, err = geocode(address, city)
    if not rec:
        return _dumps({"found": False, "error": err})
    _repo(ctx).put(rec)
    return _dumps({"found": True, "id": rec.id, "name": rec.name, "formatted_address": rec.address})


# ========== Tool 2: 城市天气预报 ==========
@function_tool(failure_error_function=tool_error)
def get_city_weather(
    city: Annotated[str, "城市或区县名称（地级市、县、县级市均可）"],
) -> str:
    """查询指定城市的未来 3~4 天天气预报。

    用于在行程中提示晴雨情况、适宜户外或室内景点。
    """
    adcode = _adcode_cache.get(city)

    # 1. 若无缓存，先查 adcode
    if not adcode:
        dist_data = amap_get("/config/district", {"keywords": city, "subdistrict": 0})
        if "error" in dist_data:
            return _dumps(dist_data)
        districts = dist_data.get("districts") or []
        if not districts:
            return _dumps({"error": f"未能识别城市「{city}」的行政区划代码"})
        adcode = str(districts[0].get("adcode"))
        _adcode_cache[city] = adcode

    # 2. 查询天气预报
    w_data = amap_get("/weather/weatherInfo", {"city": adcode, "extensions": "all"})
    if "error" in w_data:
        return _dumps(w_data)

    forecasts = w_data.get("forecasts") or []
    if not forecasts:
        return _dumps({"message": f"暂无「{city}」的预报信息"})

    casts = forecasts[0].get("casts") or []
    return _dumps({
        "city": city,
        "reporttime": forecasts[0].get("reporttime"),
        "forecasts": [
            {
                "date": c.get("date"),
                "dayweather": c.get("dayweather"),
                "nightweather": c.get("nightweather"),
                "daytemp": c.get("daytemp"),
                "nighttemp": c.get("nighttemp"),
                "wind": f"{c.get('daywind')}风 {c.get('daypower')}级",
            }
            for c in casts
        ],
    })


# ========== Tool 3: 景点检索 ==========
@function_tool(failure_error_function=tool_error)
def search_scenic_spots(
    ctx: RunContextWrapper[TripContext],
    city: Annotated[str, "城市或区县名"],
    keywords: Annotated[str, "景点名称，或品类词如「博物馆」「公园」「古镇」「步行街」"],
    near_id: Annotated[str, "可选。某个已查到的地点 id，提供时只检索其周边 15 公里内的景点"] = "",
) -> str:
    """搜索目的地城市的景点、公园、博物馆或街区。

    返回每个景点的 id、名称、评分、门票参考与营业时间；生成行程时引用 id 即可。
    """
    try:
        spots = _search(ctx, city=city, keywords=keywords, types=SPOT_TYPES, near_id=near_id,
                        radius=15000, offset=6)
    except ValueError as e:
        return _dumps({"error": str(e)})
    if not spots:
        return _dumps({"count": 0, "message": f"在「{city}」未检索到与「{keywords}」相匹配的景点，请核对名称"})
    return _dumps({"count": len(spots), "spots": spots})


# ========== Tool 4: 餐饮与特色小吃检索 ==========
@function_tool(failure_error_function=tool_error)
def search_food_and_snacks(
    ctx: RunContextWrapper[TripContext],
    city: Annotated[str, "城市或区县名"],
    keywords: Annotated[str, "美食关键词：用户点名的菜名或店名，或品类词如「美食」「特色小吃」「本地菜」「面馆」。"
                             "高德按店名/品类匹配，不要用「正餐」「午餐」这类词（实测返回 0 条）"],
    near_id: Annotated[str, "景点 id：检索该景点 4 公里内的餐饮，实现就近用餐；切忌只用住宿地 id，会造成来回折返"] = "",
    meal_type: Annotated[str, "餐饮类型：'meal'(正餐) | 'snack'(特色小吃) | 'night_market'(夜市/小吃街)"] = "meal",
) -> str:
    """搜索景点附近的正餐、特色地方小吃或夜市。

    返回每家店的 id、名称、品类、评分、人均与营业时间；生成行程时引用 id 即可。
    """
    try:
        foods = _search(ctx, city=city, keywords=keywords, types=FOOD_TYPES, near_id=near_id,
                        radius=4000, offset=8, meal_type=meal_type if meal_type in
                        ("meal", "snack", "night_market") else "meal")
    except ValueError as e:
        return _dumps({"error": str(e)})
    if not foods:
        return _dumps({"count": 0, "message": f"未检索到与「{keywords}」相符的美食餐厅，建议更换关键词"})
    return _dumps({"count": len(foods), "restaurants": foods})


# ========== Tool 5: 确定性路线排程引擎 ==========
@function_tool(failure_error_function=tool_error)
def generate_trip_itinerary(
    ctx: RunContextWrapper[TripContext],
    city: Annotated[str, "目的地城市或区县名称"],
    total_days: Annotated[int, "必填。游玩总天数，例如 2 或 3"] = 0,
    spots: Annotated[str, "必填。要去的景点，JSON 列表，元素为搜索结果里的 id，或带判断信息的对象（用户点名的景点加 \"must\": true，"
                          "放不下时最后才舍弃；你自己补充推荐的景点不要加 must，最好放进 backup_spots）："
                          "{\"id\": \"...\", \"duration_minutes\": 180, \"best_period\": \"morning\"}；"
                          "best_period 可选 'morning'(宜早去，如动物园/早市/观日出/登山) | 'evening'(宜傍晚夜间，如古街夜景/夜市街区/灯光秀)"] = "",
    foods: Annotated[str, "必填。餐饮候选，JSON 列表，元素为 search_food_and_snacks 返回的 id；"
                          "如需改变类型可写成 {\"id\": \"...\", \"meal_type\": \"snack\"}"] = "",
    hotel_id: Annotated[str, "住宿地 id（geocode_location 或搜索结果）；用户未说明住宿时留空，算法会按景点中心估算"] = "",
    departure_id: Annotated[str, "返程火车站或机场的 id（geocode_location 返回）；用户未说明可留空"] = "",
    start_date: Annotated[str, "出行起始日期 'YYYY-MM-DD'，用于自动核验闭馆日"] = "",
    pace: Annotated[str, "游玩节奏：'relaxed'(轻松慢游，每日≤2点) | 'balanced'(均衡标准，每日≤3点) | 'compact'(特种兵打卡，每日≤4点)"] = "balanced",
    departure_time: Annotated[str, "返程车次/航班出发时间 'HH:MM'（如「15:30」），用于保证最后一天提前到站；用户未说明可留空"] = "",
    must_eat: Annotated[str, "用户点名要吃的美食，逗号分隔（如用户说想吃 A 和 B，就传「A,B」）；"
                             "算法优先安排、全程只排一次；排不进的会在 plan.unmet_must_eat 里说明原因。用户没点名就留空"] = "",
    backup_spots: Annotated[str, "可选。备选景点 id 的 JSON 列表（格式同 spots），如各片区附近的公园、街区、小型博物馆；"
                                 "算法只在某天出现 1 小时以上空档且顺路时才加入，用不上不会出现在行程里"] = "",
    avoid: Annotated[str, "忌口关键词，逗号分隔，含这些词的餐厅（店名/品类）会被排除。只按用户明确说的忌口填，"
                          "如不吃辣可填「辣,麻辣,火锅,串串,冒菜,香锅,干锅,钵钵鸡」；没有忌口留空"] = "",
    max_cost_per_meal: Annotated[float, "每餐人均上限（元），超过的餐厅会被排除；用户没说预算填 0"] = 0,
) -> str:
    """调用纯 Python 排程算法，生成不折返、按天切分、带双轨餐饮（正餐+特色小吃/夜市）的完整行程。

    必须在用搜索工具拿到景点与餐饮 id 之后调用。只传 id，不要传名称或坐标。
    LLM 不应手写行程表，必须通过本工具生成严谨可行的行程。
    通勤时间取自高德步行/驾车路网。返回的 gaps 是需要补救的缺口（缺候选的饭点、没排上的必吃），
    next_step 给出一次性补齐的做法；gaps 为空时不要再重排。
    """
    # 必填项由这里检查并给出具体提示：SDK 参数校验失败时只回「Invalid JSON input」，不说缺了什么
    missing = [name for name, ok in (("total_days（游玩天数，正整数）", total_days and total_days >= 1),
                                     ("spots（景点 id 列表）", bool((spots or "").strip())),
                                     ("foods（餐饮 id 列表，可为 []）", foods is not None and foods != ""))
               if not ok]
    if missing:
        return _dumps({"success": False, "error": "缺少必填参数：" + "、".join(missing) + "。补齐后重新调用。"})
    try:
        budget = max_cost_per_meal if max_cost_per_meal and max_cost_per_meal > 0 else None
        inputs = build_trip_inputs(_repo(ctx), spots, foods, backup_spots,
                                   hotel_id=hotel_id, departure_id=departure_id, must_eat=must_eat,
                                   avoid=avoid, max_cost_per_meal=budget)
        points = [p for p in (inputs.hotel, inputs.departure) if p]
        points += inputs.spots + inputs.backups + inputs.restaurants
        travel = AmapTravel(points, fetch=amap_get)
        scheduler = TripScheduler(
            city=city,
            total_days=total_days,
            hotel=inputs.hotel,
            departure_point=inputs.departure,
            start_date=start_date or None,
            pace=pace,
            departure_time=departure_time or None,
            travel=travel,
            price_sensitive=budget is None,
        )
        scheduler.notes.extend(inputs.notes)
        logger.log("[itinerary-input] " + _dumps({
            "city": city, "days": total_days, "pace": pace, "hotel": hotel_id, "must_eat": must_eat,
            "avoid": avoid, "max_cost": budget,
            "departure": [departure_id, departure_time],
            "spots": [[s.poi_id, s.name, s.duration_minutes, s.best_period] for s in inputs.spots],
            "backups": [b.poi_id for b in inputs.backups],
            "foods": len(inputs.restaurants), "skipped": inputs.skipped,
        }))
        plan = scheduler.schedule(spots=inputs.spots, restaurants=inputs.restaurants, backup_spots=inputs.backups,
                                  must_eat=split_must_eat(must_eat))
        logger.log(f"[itinerary-routing] amap /distance requests={travel.requests}")
        plan.unassigned_spots.extend(inputs.skipped)
        return _dumps({"success": True, "display": DISPLAY_NOTE, "plan": render_dict(plan),
                       "gaps": plan.gaps, "next_step": next_step(plan.gaps)})
    except json.JSONDecodeError as e:
        return _dumps({"success": False, "error": f"spots / foods 参数不是合法的 JSON：{e}"})
    except ValueError as e:
        # 含 InfeasiblePlanError：输入不可行或行程未通过可行性校验，原因可直接转告用户
        return _dumps({"success": False, "error": str(e)})
    except Exception as e:
        return _dumps({"success": False, "error": f"排程算法执行异常：{type(e).__name__}: {str(e)}"})
