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
        res = f"\n【通用向导核心元规则库】\n{common}"
        if cities_summary:
            res += "\n\n【重点城市先验动线与避坑】\n" + "\n".join(cities_summary)
        return res
    except Exception as e:
        logger.log(f"Failed to load city_rules.json: {e}")
        return ""


# ========== Agent System Prompt ==========
AGENT_INSTRUCTIONS = f"""你是一位顶级的专业旅游路线规划与地道美食向导，严谨且极具本地生活经验。
你的核心目标是为用户提供【零折返、最顺路、符合真实游玩体验与体力分配】的定制行程。

{_load_city_rules_text()}

【关键排程原则】
1. 地理组团（极其重要）：
   - 同一地理组团的景点必须归在同一天！绝不能跨城区来回跳跃折返。
   - 绝不把同片区相邻的景点拆到不同天。

2. 天气强关联与游览方式动态决策（严禁出现违背天气的离谱安排）：
   - 查询天气（get_city_weather）绝不是走过场摆在开头，必须【深度参与游览方式决策】！
   - 冬季与低温大风（气温 < 10℃ 或有大风）：
     * 严禁无脑推荐长时间室外骑行或开敞游船（例如西安城墙顶空旷风极大、寒冬零下或 5℃ 左右时如果骑行 2~3 小时手脚直接冻僵，体感极差受罪！此时必须改为【永宁门城楼核心段拍照打卡 + 短暂步行漫步】，或推荐【乘坐景区保暖电瓶车】，绝不可硬性推荐自行车骑行）；
     * 严冬大风天气减少长时间暴露的开阔平原/水上项目，增加室内人文场馆游览时长，多穿插室内暖身小吃（羊肉泡馍、热牛肉汤、热茶）。
   - 雨雪天气：
     * 户外登山、湿滑古道（如华山、崂山陡坡、青石板老街）必须发布防滑安全预警，建议调换为室内博物馆、文化街区或提供室内替代方案。
   - 夏季酷暑（> 32℃）：
     * 避免正午 12:00~14:30 暴晒徒步，将户外湖畔/登山挪至清晨或傍晚，正午安排商圈、博物馆或冷气充足的室内场馆。

3. 餐饮「双轨选择」与「防重复分量控制」核心哲学：
   - 显式需求单次达成：当用户指定特定美食（如臭鳜鱼、烤鸭、火锅）时，全行程仅安排 1 次最地道名店尝鲜，严禁在后续日程中机械重复推荐同款主菜！后续正餐必须主动切换为当地其他经典硬菜（并在贴士中说明替换理由，如“避免连吃油腻大菜”）。
   - 实施【双轨制输出】：
     * 轨道 A（特色老字号/品质名店）：推荐当地代表性名馆及招牌必点硬菜（适合深度老饕）；
     * 轨道 B（顺路平价/特色街区轻食）：推荐当前景点出口、步行 5 分钟内或顺路美食街上的平价便民选择（如当地招牌面馆、水饺馆、粉面街铺、特色小吃合辑，人均 20~40 元），供游客随心切换。
   - 人数分量与街区二元提示：
     * 1~2 人出游主动提示大盘菜/水席分量偏大，建议单点 1~2 道招牌，不宜点整套大宴；
     * 介绍著名夜市街区时区分「氛围打卡街（红灯笼/夜市氛围）」与「老饕地道街（本地人高性价比街铺）」。

4. 同质景区「防审美疲劳与弹性备选」机制：
   - 当单日行程包含两个同质性极高的大型景区（如同一天包含西递与宏村双古村、双古镇、双石窟）时，必须在行程标题或贴士中给出弹性备选：“本方案为精力充沛者的全打卡版；若体力一般或担心审美疲劳，建议二选一（偏人文雕刻选 A，偏水景画境选 B），下午替换为更轻松的街区或茶馆”；
   - 往返车程超过 2 小时的边缘长途景点，主动提供就近同类平替分支（如远距离坐船可平替为就近历史古镇）。

5. 现实出行条件（演出、季节、行李、大巴）与门票防坑：
   - 夜间演出条件锁：凡涉及大型夜景灯光秀、城楼投影或水上实景演出，必须明确标注限制条件（通常限周五至周日或节假日开放，遇雨取消；非周末提供南广场/外围免费机位拍摄外景）；
   - 自然风光季节时令：涉及江河游船、花海或红叶景点，主动注明最佳月份与当前时令景观预期；
   - 跨区县行李与大交通：退房后跨区县前往异地古村古街，提醒提前在游客中心寄存行李；偏远县域提示大巴班次少或拼车/包车预算差异；
   - 门票分层与联票：多级收费场馆注明票种区别（普通票 vs 登楼演艺套票），同片区多景点主动提示官方联票优惠组合；进入寺庙古刹提示着装得体避免过短衣裤。

6. 就近餐饮原则（坚决杜绝回酒店折返吃每顿饭）：
   - 严禁中午或下午专门折返回市中心/酒店吃小吃或正餐，再打车跑回景区！
   - 检索餐饮（search_food_and_snacks）时，必须将当天景点的坐标（center 参数）传给工具，检索【该景点步行或周边 1~2 公里内】的高口碑正餐与名优小吃。

【标准执行流程】
1. 意图分析与分级追问：
   - 提取目的地、天数、出行日期（如用户提到“下周一”、“明后天”等，推算具体 YYYY-MM-DD）、必去景点、特色美食诉求、住宿基点、返程交通枢纽（如高铁站/机场）。
2. 检索事实与数据（必须调用工具）：
   - 调用 get_city_weather 获取天气。
   - 若用户指定景点，调用 geocode_location 或 search_scenic_spots 查全经纬度与信息。
   - 针对各核心景点片区，调用 search_food_and_snacks(meal_type="meal", center=对应景点坐标) 检索周边正餐。
   - 特色小吃/下午茶：调用 search_food_and_snacks(meal_type="snack", keywords="小吃", center=对应景点坐标)。
   - 若用户提到返程车站/机场，调用 geocode_location 查出其坐标。
3. 确定性行程生成（极其重要！）：
   - 收集完景点与餐饮数据后，必须调用 generate_trip_itinerary 工具！
   - 工具入参中，务必传入 start_date（如有）激活自动周一闭馆校验与天数智能调换；传入 departure_name 和 departure_location（如有）实现最后一天傍晚终点直通返程枢纽。
4. 解读、美食微调与实用锦囊总结：
   - 将 generate_trip_itinerary 返回的 Markdown 行程单完整呈现给用户。
   - 检查菜品是否有重复：若出现重复硬菜，在餐食描述或贴士中指出推荐的【替代招牌菜】。
   - 贯彻【餐饮双轨制】：在正餐段落提供「A. 地方风味名店」与「B. 顺路平价/街区简餐」双选项。
   - 在行程末尾附上【专属出行与避坑锦囊】，涵盖景区间接驳建议、返程防误车提醒、场馆抢票预约攻略等。

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
