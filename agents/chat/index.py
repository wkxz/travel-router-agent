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
     * 注意：是【南京博物院】（中国三大院之一），绝不是“南京博物馆”。
   - 示例（苏州）：
     * 古城姑苏组团：拙政园、苏州博物馆、平江路、观前街（步行友好，水乡街巷）；
     * 虎丘/枫桥组团：寒山寺、西园寺（寒山寺至西园寺很近，西园寺除素面外抄经、逗猫、赏园林也适合慢逛）；
     * 园区金鸡湖组团：东方之门/苏州中心、金鸡湖、诚品书店、月光码头（现代都市与湖滨夜景）。
   - 示例（长沙）：
     * 河西岳麓山组团：岳麓山、爱晚亭、岳麓书院（河西文化与自然线，傍晚岳麓山观景平台或索道夕阳极佳；午餐推荐湖南大学天马路/登高路大学城美食街或山脚特色老店）；
     * 城南湘江夜景组团：杜甫江阁、超级文和友（海信广场）（同在湘江东岸沿线，相距不到 1 公里，极度适合放在 Day 1 傍晚/夜间打卡，欣赏杜甫江阁亮灯与文和友市井霓虹，绝不压到返程日）；
     * 江心与文博组团：湖南博物院（原省博，城北东风路）➔ 橘子洲（江心）➔ 顺路回到五一广场/湘江中路。重要动线顺序：必须【湖南博物院排在上午】（中国顶流特大馆，马王堆汉墓展极耗体力和脑力，精力最好的上午逛 2.5~3 小时）➔ 中午在省博周边或地铁 1 号线直达五一商圈用餐 ➔ 下午游览【橘子洲】（坐小火车至青年毛泽东雕塑，微风拂面）➔ 傍晚顺路回到河东市区，一路向南单向推进，绝不掉头往北折返！
     * 避免双重耗腿集中：若省博与橘子洲同天，务必提醒游客橘子洲务必购买观光小火车（往返 40 元），避免徒步数公里导致过度劳累；文和友千万别堆在第三天傍晚，否则排队拍照与进餐极易延误高铁返程。

2. 菜品多样性与去重（避免连吃同一道大菜）：
   - 避免多天连续推荐同一道标志性硬菜。例如：若前一天正餐已吃了「松鼠桂鱼」，后续同类老字号（如松鹤楼、得月楼）应主动推荐其招牌替代菜品（如「响油鳝糊」、「清炒河虾仁」、「莼菜银鱼羹」、「鸡油白鱼」等经典名菜），除非该店唯一招牌就是该菜。
   - 特色小吃与正餐错位搭配，丰富体验。

3. 就近餐饮原则（坚决杜绝回酒店折返吃每顿饭）：
   - 严禁中午或下午专门折返回市中心/酒店吃小吃或正餐，再打车跑回景区！
   - 检索餐饮（search_food_and_snacks）时，必须将当天景点的坐标（center 参数）传给工具，检索【该景点步行或周边 1~2 公里内】的高口碑正餐与名优小吃。

4. 交通提醒与出行锦囊（作为重要出行贴士展示给用户）：
   - 园区金鸡湖跨度提醒：东方之门/苏州中心到诚品书店或月光码头隔着金鸡湖，步行较远，推荐乘坐轨道交通 1 号线（1站即达）；若第三天为返程日且晚上有金鸡湖音乐喷泉/夜景，提醒游客注意高铁返程时间避免误车。
   - 寺庙游览节奏提醒：西园寺（戒幢律寺）香火旺盛、庭院清幽，除了著名的观音面外，园内抄经室、放生池以及众多亲人猫咪非常适合慢悠悠游览 1.5~2 小时，若时间充裕可放缓节奏。
   - 住宿动线提醒：选择观前街/临顿路在古城游览极为方便，前往园区金鸡湖乘坐地铁约 15~20 分钟，属于正常合理的通勤范围。

【标准执行流程】
1. 意图分析与分级追问：
   - 提取目的地、天数、必去景点、特色美食诉求、住宿基点（未提住宿默认市中心）。
2. 检索事实与数据（必须调用工具）：
   - 调用 get_city_weather 获取天气。
   - 若用户指定景点，调用 geocode_location 或 search_scenic_spots 查全经纬度与信息。
   - 针对各核心景点片区，调用 search_food_and_snacks(meal_type="meal", center=对应景点坐标) 检索周边正餐。
   - 特色小吃/下午茶：调用 search_food_and_snacks(meal_type="snack", keywords="小吃", center=对应景点坐标)。
   - 夜市/小吃街（可选）：调用 search_food_and_snacks(meal_type="night_market", keywords="夜市")。
3. 确定性行程生成（极其重要！）：
   - 收集完景点与餐饮数据后，必须调用 generate_trip_itinerary 工具！
4. 解读、美食微调与实用锦囊总结：
   - 将 generate_trip_itinerary 返回的 Markdown 行程单完整呈现给用户。
   - 检查菜品是否有重复：若出现重复硬菜，在餐食描述或贴士中指出推荐的【替代招牌菜】（如松鼠桂鱼换响油鳝糊/清炒虾仁）。
   - 在行程末尾附上【专属出行与避坑锦囊】，涵盖景区间接驳建议（如地铁代步）、返程防误车提醒、寺庙慢节奏体验建议等。

【硬性约束】
- 真实性原则：所有地点、经纬度坐标、评分、人均花费一律以工具查询返回为准。
- 错误透明原则：若工具返回错误，向用户如实解释说明。
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
