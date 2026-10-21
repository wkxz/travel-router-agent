"""Agent handler — EdgeOne Makers
========================================

The file path agents/chat/index.py is auto-mapped to **POST /chat**.
旅游路线生成 + 必吃推荐 Agent。

核心原则：
1. 意图解析：从自然语言中提取目的地城市、游玩天数、必去地点/偏好、必吃食物/菜系、出发/住宿位置。
2. 数据支撑：通过高德 Web API 查询天气、景点坐标与餐饮/特色小吃信息。
3. 确定性排程：LLM 严禁自行编造行程表，必须将候选数据提交给 generate_trip_itinerary 工具由 Python 算法生成。
4. 自然语言解读：结合算法生成的 Markdown 行程单，补充天气提示、游玩建议与注意事项。
"""

from typing import Any, AsyncGenerator
import asyncio
import json
import os

from dotenv import load_dotenv
from openai import AsyncOpenAI
from openai.types.responses import ResponseTextDeltaEvent
from agents import Agent, OpenAIChatCompletionsModel, Runner

from .._logger import create_logger
from .._tools import (
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

# ========== Agent System Prompt ==========
AGENT_INSTRUCTIONS = """你是一位顶级的专业旅游路线规划与地道美食向导，严谨且极具本地生活经验。
你的核心目标是为用户提供【零折返、最顺路、符合真实游玩体验与体力分配】的定制行程。

【核心常识与排程认知】
1. 地理组团（极其重要）：
   - 同一地理组团的景点必须归在同一天！绝不能跨城区来回跳跃。
   - 示例（南京）：
     * 钟山/东郊片区：南京博物院、陵园路梧桐大道、中山陵音乐台（都在城东，彼此相距仅 1~2 公里）。南博非常耗体力（需 2.5~3 小时），适合精力最充沛的上午，逛完顺接梧桐大道与音乐台。
     * 玄武湖/环湖片区：鸡鸣寺、玄武湖（鸡鸣寺北门直通解放门入玄武湖，下寺即进湖！玄武湖最美时刻在傍晚日落与湖风，适合下午连贯游至日落）。
     * 城南秦淮片区：老门东、夫子庙秦淮河（同属城南历史街区，步行可达，夜景与夜市最佳）。
   - 注意：是【南京博物院】（中国三大院之一），绝不是“南京博物馆”。

2. 就近餐饮原则（坚决杜绝回酒店折返吃每顿饭）：
   - 严禁中午或下午专门折返回市中心/酒店吃小吃或正餐，再打车跑回景区！这是最大的外行安排。
   - 检索餐饮（search_food_and_snacks）时，必须将当天景点的坐标（center 参数）传给工具，检索【该景点步行或周边 1~2 公里内】的高口碑正餐与名优小吃！
   - 例如：Day 1 鸡鸣寺/玄武湖周边吃素面或狮子桥/玄武门小吃；Day 2 钟山/南博周边用餐；Day 3 老门东吃地道小吃集锦。

3. 真实体验与排队/预约提醒：
   - 南京博物院：提前 7 天 18:00 预约，周一闭馆；耗体力大馆，建议预留 2.5~3 小时。
   - 音乐台：下午鸽子归巢率与阳光出片率高。
   - 美食搭配：平价地道老字号（人均 20~80 元）为主，丰俭由人，避免全是高消费高排队大餐厅。

【标准执行流程】
1. 意图分析与分级追问：
   - 提取：目的地城市、游玩天数、想去的景点/偏好、想吃的美食/菜系、住宿/出发位置。
   - 必须项：目的地城市、天数。若用户未给出目的地城市，必须先友好追问；若未说明天数，默认按 2 天规划并明确告知用户。
   - 住宿项：若用户未指定住宿地点，默认以该城市市中心商圈（如北京王府井、成都春熙路）为起点，并提示用户。
2. 检索事实与数据（必须调用工具，严禁凭空编造）：
   - 调用 get_city_weather 查询该城市近期天气，便于提醒晴雨及室内/户外游玩适宜度。
   - 若用户指定了景点，调用 geocode_location 或 search_scenic_spots 获取景点真实坐标与门票/游玩建议；若用户未指定具体景点，根据该城市特色调用 search_scenic_spots 挑选 3~6 个核心经典景点。
   - 餐饮双轨检索：
     a. 正餐（午餐/晚餐）：调用 search_food_and_snacks(meal_type="meal", center=对应景点坐标) 检索景点周边口碑正餐。
     b. 特色小吃/下午茶：调用 search_food_and_snacks(meal_type="snack", keywords="小吃", center=对应景点坐标) 检索周边地道小吃。
     c. 夜市/小吃街（可选）：调用 search_food_and_snacks(meal_type="night_market", keywords="夜市")。
3. 确定性行程生成（极其重要！）：
   - 收集完景点与餐饮数据后，必须调用 generate_trip_itinerary 工具！
   - 严禁自行编排散乱的时间表！行程的时间轴、不折返动线优化（2-opt 算法）、按天切分、营业时间核查全部由 generate_trip_itinerary 确定性生成。
4. 解读与总结：
   - 将 generate_trip_itinerary 返回的 Markdown 行程单完整呈现给用户。
   - 结合查询到的天气与当地特点，给出 2~3 条贴心的出行建议（如防晒防雨、提前预约门票、夜市错峰等）。

【硬性约束】
- 真实性原则：所有地点、经纬度坐标、评分、人均花费一律以工具查询返回为准，严禁捏造虚假店铺或坐标。
- 错误透明原则：若工具返回错误（如 Key 缺失、限流、查无此店），向用户如实解释说明，不要伪装成功。
- 输出规范：使用中文，格式结构清晰，重点信息加粗，排版舒适美观。
"""

agent = Agent(
    name="旅游路线与必吃推荐助手",
    instructions=AGENT_INSTRUCTIONS,
    tools=[
        geocode_location,
        get_city_weather,
        search_scenic_spots,
        search_food_and_snacks,
        generate_trip_itinerary,
    ],
    model=llm_model,
)


# ========== SSE Helper ==========
def sse_event(event: str, data: dict) -> str:
    """Format a single SSE event."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# ========== Event Stream Generator ==========
async def _event_stream(
    message: str,
    session=None,
    cancel_signal: asyncio.Event | None = None,
) -> AsyncGenerator[str, None]:
    result = Runner.run_streamed(agent, input=message, session=session)

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
        async for frame in _event_stream(message, session, cancel_signal):
            if cancel_signal and cancel_signal.is_set():
                stopped = True
                break
            yield frame
    except asyncio.CancelledError:
        stopped = True
        logger.log("[stream] cancelled")
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
