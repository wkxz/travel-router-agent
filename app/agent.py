"""旅游路线生成 + 必吃推荐 Agent：Agent 定义、提示词与 SSE 事件流。

与 Web 框架无关：server/app.py 把 chat_events() 产出的 SSE 帧原样写给浏览器。

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
from agents import Agent, MaxTurnsExceeded, OpenAIChatCompletionsModel, Runner, set_tracing_disabled

from ._logger import create_logger
from ._data.poi_repo import PoiRepo, TripContext
from ._tools import (
    extract_plan,
    geocode_location,
    get_city_weather,
    search_scenic_spots,
    search_food_and_snacks,
    generate_trip_itinerary,
)

load_dotenv()

logger = create_logger("chat")

# SDK 默认把运行轨迹上报到 OpenAI 平台；私有部署不需要，也不应把对话外发
set_tracing_disabled(True)

# ========== LLM Model ==========
def llm_env(name: str) -> str:
    """读取 LLM_<name>；兼容旧变量名 AI_GATEWAY_<name>。"""
    return os.getenv(f"LLM_{name}") or os.getenv(f"AI_GATEWAY_{name}") or ""


# 任意 OpenAI 兼容端点均可（需支持 function calling 与流式输出）
llm_client = AsyncOpenAI(
    api_key=llm_env("API_KEY") or "missing",  # 未配置时也要能加载模块（测试、健康检查）；真正请求时会报鉴权错误
    base_url=llm_env("BASE_URL") or None,
)

llm_model = OpenAIChatCompletionsModel(
    model=llm_env("MODEL"),
    openai_client=llm_client,
)

# SDK 默认 max_turns=10（agents/run_config.py: DEFAULT_MAX_TURNS）。一份行程要查天气、逐个景点查周边餐饮、
# 地理编码再排程，模型逐个串行调用工具时 10 轮不够用，会中途抛 MaxTurnsExceeded。
MAX_TURNS = int(os.getenv("AGENT_MAX_TURNS", "30"))

CHINA_TZ = timezone(timedelta(hours=8))  # 服务器系统时区可能是 UTC；固定 UTC+8，不依赖 tzdata
_WEEKDAYS = "一二三四五六日"

# ========== 动态城市规则与 Prompt 解耦 ==========
def _load_city_rules_text() -> str:
    rules_path = os.path.join(os.path.dirname(__file__), "city_rules.json")
    if not os.path.exists(rules_path):
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
   - 每次调用都必须传齐 city、total_days、spots、foods 四个必填参数（实测常漏掉 total_days，白白多一轮）。
   - spots 传景点 id 列表；可按景点性质附上 duration_minutes 与 best_period。foods 传检索到的餐饮 id 列表。
   - 用户点名要去的景点写成 {{"id": "...", "must": true}}，景点放不下时算法会先舍弃没标 must 的。
   - 用户只说了连锁品牌、没指定分店（如「先锋书店」）时，任选一家的 id 并加 "any_branch": true，
     算法会查出同城其他分店、按当天路线换成最顺路的一家；用户点名了具体分店就不加。
   - 给每个景点写 "tips"：一两句你知道的实用经验，如是否要提前预约及在哪预约、门票大致价格、从哪个地铁站或哪个门进、
     最佳游览时段与看点（≤60 字）。它会原样显示在行程卡片对应景点下、标明「未经核实」；不确定的不要写，
     不要写营业时间和闭馆日（以检索数据为准，写了容易与行程表矛盾）。
   - 景点数量要够排满：每天白天景点上限为轻松 2 / 均衡 3 / 紧凑 4 个（晚餐后的夜游另加 1 个）。用户点名的景点少于「天数 × 上限」时，
     自己按目的地特色检索并补充推荐景点，直接放进 spots（不标 must），使 spots 总数达到「天数 × 上限」左右；
     只给点名的几个景点会导致整天整上午空着（实测 3 天只传 3 个景点，有两个半天是空档）。
     放不下的推荐景点算法会自动舍弃，不必担心排太满。
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
   - 另通过 backup_spots 提供 2~4 个各片区附近的轻量备选景点 id（公园、街区、小型展馆等），算法只在出现 1 小时以上空档且顺路时才加入。
   - 觉得行程需要调整时，修改参数后重新调用 generate_trip_itinerary，而不是在文字里另写一套时间或顺序。
   - 用户要求修改（如「把 A 换成 B」）时，只改用户要求的部分，其余景点、日期、偏好保持不变；
     如果为了放下新景点不得不删掉其他景点，必须在回复里明确告诉用户删了哪些、为什么。
   - 工具返回 success=false 时，把 error 里的原因如实告诉用户（例如返程时间过早），不要自行编排行程。
4. 回复用户：
   - 行程表由系统直接展示在你的回复上方。不要复述、改写或重新排版行程表：不要按天（Day 1 / 第一天……）
     逐日概括，也不要另列时间安排。逐日复述时容易写出行程表里没有的活动（实测写了「傍晚漫步某街区」，
     行程表那个时段其实是去别处吃晚饭），和行程表矛盾。
   - 你的回复只写：一两句全程的整体概括（不分天）；「未安排的地点」及原因（原样告知，不要自行补进行程）；
     行程表里标注「以地图导航为准」的路段（可能隔水需轮渡、或需绕行），提醒用户实际的交通方式；
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
EMPTY_RUN_RETRIES = 1  # 一轮既没文字也没行程（模型网关偶发空回复）时，自动重试的次数


async def _event_stream(
    message: str,
    session=None,
    cancel_signal: asyncio.Event | None = None,
    conversation_id: str | None = None,
) -> AsyncGenerator[str, None]:
    # 同一会话共享一份 POI 仓库（多轮引用上一轮的 id 不必回查）；不同会话互不干扰
    context = context_for(conversation_id)
    for attempt in range(EMPTY_RUN_RETRIES + 1):
        # 重试用同一句话：会话里已有上一轮的检索结果，模型会接着做；history 会折叠相邻的重复用户消息
        result = Runner.run_streamed(agent, input=message, session=session, context=context, max_turns=MAX_TURNS)
        text_chars, produced = 0, False

        async for event in result.stream_events():
            if cancel_signal and cancel_signal.is_set():
                return

            if event.type == "raw_response_event" and isinstance(event.data, ResponseTextDeltaEvent):
                text_chars += len(event.data.delta)
                produced = True
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
                        produced = True
                        logger.log(f"[stream] plan: {plan.get('city')} {plan.get('total_days')}d")
                        yield sse_event("plan", plan)

        logger.log(f"[stream] run finished: attempt={attempt + 1} text_chars={text_chars} produced={produced}")
        if produced:
            return
        # 实测出现过：检索了几轮后模型返回空内容就结束，用户看到一片空白
        logger.error(f"[stream] empty run (attempt {attempt + 1}), "
                     + ("retrying" if attempt < EMPTY_RUN_RETRIES else "giving up"))
    yield sse_event("error", {"message": "模型这次没有返回结果，请再发送一次。", "errorType": "EmptyResponse",
                              "status": None, "detail": None})


# ========== 对外入口 ==========
async def chat_events(
    message: str,
    conversation_id: str | None = None,
    session=None,
    cancel_event: asyncio.Event | None = None,
) -> AsyncGenerator[str, None]:
    """一轮对话的完整 SSE 帧序列，最后一帧总是 done。

    session：Agents SDK 的会话记忆（如 SQLiteSession），None 表示无记忆的单轮对话。
    cancel_event：被 set 时尽快停止（POST /chat/stop）。
    """
    if not message:
        yield sse_event("error", {"message": "'message' is required"})
        yield sse_event("done", {})
        return

    cid = conversation_id
    logger.log(f"[request] cid={cid}, message={message[:50]!r}")
    cancel_signal = cancel_event
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
