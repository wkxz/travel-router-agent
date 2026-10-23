"""Agent handler — EdgeOne Makers
========================================

The file path agents/chat/index.py is auto-mapped to **POST /chat**.
旅游路线生成 + 必吃推荐 Agent。

核心原则：
1. 意图解析：从自然语言中提取目的地城市、游玩天数、必去地点/偏好、必吃食物/菜系、出发/住宿位置。
2. 数据支撑：通过高德 Web API 查询天气、景点坐标与餐饮/特色小吃信息。
3. 确定性排程：LLM 严禁自行编造行程表，必须将候选数据提交给 generate_trip_itinerary 工具由 Python 算法生成。
4. 行程展示：行程数据经 SSE plan 事件直接推给前端渲染，不经模型转述；模型只补充天气提示、游玩建议与注意事项。
"""

from datetime import datetime, timedelta, timezone
from typing import Any, AsyncGenerator
import asyncio
import json
import os
import threading
from collections import OrderedDict

from dotenv import load_dotenv
from openai import AsyncOpenAI
from openai.types.responses import ResponseTextDeltaEvent
from agents import Agent, MaxTurnsExceeded, OpenAIChatCompletionsModel, Runner

from .._logger import create_logger
from .._data.poi_repo import PoiRepo, TripContext
from .._tools import (
    extract_plan,
    geocode_location,
    get_city_weather,
    search_scenic_spots,
    search_food_and_snacks,
    generate_trip_itinerary,
)

load_dotenv()

logger = create_logger("chat")

# ========== LLM Model ==========
llm_client = AsyncOpenAI(
    api_key=os.getenv("AI_GATEWAY_API_KEY"),
    base_url=os.getenv("AI_GATEWAY_BASE_URL"),
)

llm_model = OpenAIChatCompletionsModel(
    model=os.getenv("AI_GATEWAY_MODEL", "@makers/deepseek-v4-flash"),
    openai_client=llm_client,
)

# SDK 默认 max_turns=10（agents/run_config.py: DEFAULT_MAX_TURNS）。一份行程要查天气、逐个景点查周边餐饮、
# 地理编码再排程，模型逐个串行调用工具时 10 轮不够用，会中途抛 MaxTurnsExceeded。
MAX_TURNS = int(os.getenv("AGENT_MAX_TURNS", "30"))

CHINA_TZ = timezone(timedelta(hours=8))  # 部署节点系统时区可能是 UTC；固定 UTC+8，不依赖 tzdata
_WEEKDAYS = "一二三四五六日"

# ========== 动态城市规则与 Prompt 解耦 ==========
def _load_city_rules_text() -> str:
    possible_paths = [
        os.path.join(os.path.dirname(__file__), "..", "city_rules.json"),
        os.path.join(os.path.dirname(__file__), "city_rules.json"),
        os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "agents", "city_rules.json"),
        os.path.join(os.getcwd(), "agents", "city_rules.json"),
        os.path.join(os.getcwd(), ".edgeone", "agent-python", "pages_agents", "city_rules.json"),
    ]
    rules_path = None
    for p in possible_paths:
        if os.path.exists(p):
            rules_path = p
            break

    if not rules_path:
        return ""
    try:
        with open(rules_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        common = "\n".join(f"- {r}" for r in data.get("common_rules", []))
        cities_summary = []
        for city_name, detail in data.get("cities", {}).items():
            districts_str = "；".join(f"{k}: {v}" for k, v in detail.get("districts", {}).items())
            tips_str = " ".join(detail.get("tips", []))
            name_corr = f"（{detail['name_correction']}）" if "name_correction" in detail else ""
            cities_summary.append(f"• 【{city_name}{name_corr}】：{districts_str} 贴士：{tips_str}")
        if not common and not cities_summary:
            return ""
        res = f"\n【补充规则】\n{common}" if common else ""
        if cities_summary:
            res += "\n\n【重点城市先验动线与避坑】\n" + "\n".join(cities_summary)
        return res
    except Exception as e:
        logger.log(f"Failed to load city_rules.json: {e}")
        return ""


# ========== Agent System Prompt ==========
AGENT_INSTRUCTIONS = f"""你是一位严谨的旅游路线规划与美食向导，面向中国任意城市、县城或景区提供行程规划。
目标：行程顺路不折返、时间上真的走得通、体力分配合理，所有店名与数据都有工具查询依据。

{_load_city_rules_text()}

【分工原则】
- 行程表（每天去哪、几点、在哪吃）由 generate_trip_itinerary 的确定性算法生成，你不手写、不改写、不增删其中的条目。
- 你负责：理解需求、检索数据、为算法准备参数，以及在行程表之外补充天气、预约、安全等实用提示。
- 地方性经验（哪里适合看日出、哪道菜是当地招牌、哪个景区需要预约）依靠你自身的知识判断，但涉及店名、坐标、评分、价格、营业时间时，一律以工具返回为准。

【通用决策原则】
1. 天气参与决策：先查天气。低温大风时少排长时间露天骑行、游船等暴露项目；雨雪天对登山步道、石板路等湿滑路段给出防滑提醒，并考虑室内替代；
   高温时把户外项目安排在早晚，正午留给室内场馆。
2. 体力与节奏：大型博物馆、山岳、主题乐园等体量大的景点通常需要 3 小时以上，duration_minutes 要如实设置；同一天出现两个同类型的大型景点时，提示用户可二选一。
3. 时段偏好：根据景点性质判断 best_period——需要早去的（如动物园、早市、观日出、热门景区避开人流）标 "morning"，看夜景、逛夜市的标 "evening"，不确定就不标。
4. 现实条件：演出、灯光秀、季节性景观（花期、红叶、冰雪）等有时间或季节限制的，提醒用户核实；需要实名预约的场馆提醒提前预约。
5. 措辞客观平实，不用营销号式的夸张表达。

【标准执行流程】
1. 意图分析与追问：
   - 提取目的地、天数、出行日期（"下周一""后天"等推算为 YYYY-MM-DD）、必去景点、必吃美食、住宿位置、返程车站/机场及时间、节奏偏好。
   - 只有缺目的地或天数时才追问；其他缺失项用默认值，并在回复中说明按什么假设排的。
2. 检索数据（必须调用工具）：
   - 所有地点都用工具返回的 id 引用，你拿不到也不需要坐标；不要自己编造 id。
   - get_city_weather 查天气。
   - 用户指定的景点用 search_scenic_spots 查出 id 与营业信息；用户没指定时，按目的地特色检索代表性景点。
   - 餐饮检索一次做全、并行调用（之后不要为了换几家店反复检索重排）：
     · 对【每一个】景点各调用一次 search_food_and_snacks(keywords="美食", meal_type="meal", near_id=该景点 id)；
       keywords 用「美食」或菜系词，不要用「正餐」「午餐」（高德按店名匹配，实测返回 0 条）；
     · 用户点名的每样必吃，用该美食名作 keywords 检索一次（near_id 选行程中最相关的景点）；
     · 再在主要游览片区用 keywords="特色小吃"、meal_type="snack" 检索一次。
   - 用户提到住宿地、返程车站/机场时，用 geocode_location 查出 id。
3. 生成行程（必须调用 generate_trip_itinerary）：
   - spots 传景点 id 列表；可按景点性质附上 duration_minutes 与 best_period。foods 传检索到的餐饮 id 列表。
   - 用户点名要去的景点写成 {{"id": "...", "must": true}}，景点放不下时算法会先舍弃没标 must 的；
     你自己补充推荐的景点不要标 must，优先放进 backup_spots（只在有空档且顺路时加入）。
   - must_eat 只填用户原话里点名的菜（逗号分隔），不要自己补充「当地必吃」；用户没点名就留空。
     算法会优先安排且全程只排一次，你不需要再处理去重。
   - 忌口与预算交给参数，不要靠删减 foods 来凑：用户说的忌口填 avoid，人均预算填 max_cost_per_meal。
   - 工具返回 gaps 非空时，严格按 next_step 并行补检索，然后只重新调用一次 generate_trip_itinerary；
     之后仍有「周边自选」或没排上的必吃，就接受并在回复里如实说明原因，不要再重排、也不要在文字里自行补一家店。
     gaps 为空时不要重排：行程已通过可行性校验，你觉得某天偏满、某个景点要玩很久，就在回复里提出建议，
     不要自行删减用户要求的景点去重排（实测出现过：用户只要求换一个景点，却被删掉了另外两个）。
   - 有出行日期就传 start_date（用于闭馆校验），日期一律按上方【当前日期】推算；用户没说出行日期时不传，
     算法按今天起算，并在回复中说明「按今天出发排的，闭馆日按此核对」。
   - 有返程信息就传 departure_id、departure_time；用户说了节奏就传 pace。
   - 用户没说住宿位置时，hotel_id 留空，并在回复中请用户补充。
   - 可通过 backup_spots 额外提供 2~4 个各片区附近的轻量备选景点 id（公园、街区、小型展馆等），算法只在出现 1 小时以上空档且顺路时才加入。
   - 觉得行程需要调整时，修改参数后重新调用 generate_trip_itinerary，而不是在文字里另写一套时间或顺序。
   - 用户要求修改（如「把 A 换成 B」）时，只改用户要求的部分，其余景点、日期、偏好保持不变；
     如果为了放下新景点不得不删掉其他景点，必须在回复里明确告诉用户删了哪些、为什么。
   - 工具返回 success=false 时，把 error 里的原因如实告诉用户（例如返程时间过早），不要自行编排行程。
4. 回复用户：
   - 行程表由系统直接展示在你的回复上方。不要复述、改写或重新排版行程表，也不要另列每天的时间安排。
   - 你的回复只写：一两句整体概括（每天的主题即可）；「未安排的地点」及原因（原样告知，不要自行补进行程）；
     天气与穿衣、预约与门票、安全与注意事项。这些提示不得与行程表的时间、顺序、餐厅矛盾。
   - 想推荐行程表以外的餐厅时，只能推荐工具检索结果里出现过的店；否则只说品类（如「附近的面馆、小吃店」），不要编造店名。

【硬性约束】
- 真实性：地点、坐标、评分、人均、营业时间以工具返回为准；工具没返回的数据标注「需现场确认」。
- 错误透明：工具返回错误时如实告诉用户原因。
- 使用中文，结构清晰，重点信息加粗。
"""

def today_line(now: datetime | None = None) -> str:
    now = now or datetime.now(CHINA_TZ)
    return f"【当前日期】今天是 {now:%Y-%m-%d}（周{_WEEKDAYS[now.weekday()]}，北京时间）。"


def build_instructions(_ctx: Any = None, _agent: Any = None) -> str:
    """每次请求重新生成：把当天日期放进提示词，模型才能把「下周一」「后天」换算成正确的 YYYY-MM-DD。
    （SDK 要求 instructions 回调恰好接收 (context, agent) 两个参数。）"""
    return f"{today_line()}\n\n{AGENT_INSTRUCTIONS}"


agent = Agent(
    name="旅游路线与必吃推荐助手",
    instructions=build_instructions,
    tools=[
        geocode_location,
        get_city_weather,
        search_scenic_spots,
        search_food_and_snacks,
        generate_trip_itinerary,
    ],
    model=llm_model,
)


# ========== 会话级 POI 缓存 ==========
# 多轮对话（如「把第二天的 A 换成 B」）会引用上一轮检索到的 id。每轮新建空仓库时，这些 id 要逐个调
# /place/detail 回查（受 0.35s 节流），实测第二轮 96~112 秒。按会话缓存后直接命中；
# 只存在本进程内存里，换实例或重启后未命中会自动回查高德，结果不变，只是慢一些。
_REPO_CACHE_MAX = 200
_repo_cache: "OrderedDict[str, PoiRepo]" = OrderedDict()
_repo_cache_lock = threading.Lock()


def context_for(conversation_id: str | None) -> TripContext:
    if not conversation_id:
        return TripContext()
    with _repo_cache_lock:
        repo = _repo_cache.pop(conversation_id, None) or PoiRepo()
        _repo_cache[conversation_id] = repo  # 移到末尾：最近使用
        while len(_repo_cache) > _REPO_CACHE_MAX:
            _repo_cache.popitem(last=False)
    return TripContext(repo=repo)


# ========== SSE Helper ==========
def sse_event(event: str, data: dict) -> str:
    """Format a single SSE event."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# ========== Event Stream Generator ==========
async def _event_stream(
    message: str,
    session=None,
    cancel_signal: asyncio.Event | None = None,
    conversation_id: str | None = None,
) -> AsyncGenerator[str, None]:
    # 同一会话共享一份 POI 仓库（多轮引用上一轮的 id 不必回查）；不同会话互不干扰
    result = Runner.run_streamed(agent, input=message, session=session, context=context_for(conversation_id),
                                 max_turns=MAX_TURNS)

    async for event in result.stream_events():
        if cancel_signal and cancel_signal.is_set():
            break

        if event.type == "raw_response_event" and isinstance(event.data, ResponseTextDeltaEvent):
            logger.log(f"[stream] text_delta: {repr(event.data.delta)}")
            yield sse_event("text_delta", {"delta": event.data.delta})

        elif event.type == "run_item_stream_event":
            if event.name == "tool_called":
                tool_name = (
                    getattr(event.item, "name", None)
                    or getattr(getattr(event.item, "raw_item", None), "name", None)
                )
                if tool_name:
                    logger.log(f"[stream] tool_called: {tool_name}")
                    yield sse_event("tool_called", {"tool": tool_name})

            elif event.name == "tool_output":
                # 行程数据绕过模型直接推给前端渲染，模型无从改写
                plan = extract_plan(getattr(event.item, "output", None))
                if plan:
                    logger.log(f"[stream] plan: {plan.get('city')} {plan.get('total_days')}d")
                    yield sse_event("plan", plan)


# ========== Core Handler ==========
async def handler(context: Any) -> AsyncGenerator[str, None]:
    request = context.request
    body = request.body
    message = body.get("message") if isinstance(body, dict) else None

    if not message:
        yield sse_event("error", {"message": "'message' is required"})
        yield sse_event("done", {})
        return

    raw_user_id = ""
    if isinstance(body, dict):
        raw_user_id = body.get("userId") or body.get("user_id") or ""
    user_id = str(raw_user_id).strip() or None

    cid = context.conversation_id
    logger.log(f"[request] cid={cid}, uid={user_id or '-'}, message={message[:50]!r}")

    if user_id and cid:
        try:
            existing = await context.store.get_messages(
                conversation_id=cid, limit=1
            )
            already_indexed = bool(existing)
        except Exception as e:
            logger.error(f"[user-index] probe failed: {type(e).__name__}: {e}")
            already_indexed = False

        if not already_indexed:
            try:
                msg_id = await context.store.append_message(
                    conversation_id=cid,
                    role="user",
                    content=message,
                    user_id=user_id,
                )
                logger.log(f"[user-index] WROTE first-turn: cid={cid} msg_id={msg_id!r}")
            except Exception as e:
                logger.error(f"[user-index] FAILED: {type(e).__name__}: {e}")

    session = context.store.openai_session(cid) if cid else None
    cancel_signal = request.signal
    stopped = False

    try:
        async for frame in _event_stream(message, session, cancel_signal, conversation_id=cid):
            if cancel_signal and cancel_signal.is_set():
                stopped = True
                break
            yield frame
    except asyncio.CancelledError:
        stopped = True
        logger.log("[stream] cancelled")
    except MaxTurnsExceeded as e:
        logger.error(f"[stream] max turns exceeded: {e}")
        yield sse_event("error", {
            "message": f"本次规划的工具调用超过了 {MAX_TURNS} 轮上限，已中止。可以减少景点数量或分几次提问后重试。",
            "errorType": type(e).__name__,
            "status": None,
            "detail": str(e),
        })
    except Exception as e:
        logger.error(f"[stream] error: {type(e).__name__}: {e}")
        detail: Any = str(e)
        status: Any = None
        response = getattr(e, "response", None)
        if response is not None:
            status = getattr(response, "status_code", None)
            try:
                body_text = response.text if hasattr(response, "text") else None
                if callable(body_text):
                    body_text = body_text()
                if body_text:
                    try:
                        detail = json.loads(body_text)
                    except (json.JSONDecodeError, ValueError, TypeError):
                        detail = body_text
            except Exception:
                pass
        body_attr = getattr(e, "body", None)
        if body_attr and detail == str(e):
            detail = body_attr
        yield sse_event("error", {
            "message": str(e),
            "errorType": type(e).__name__,
            "status": status,
            "detail": detail,
        })
    finally:
        yield sse_event("done", {"stopped": stopped})
