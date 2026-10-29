"""真实环境测评：在进程内运行与线上相同的 Agent，记录每轮的工具调用（参数 + 返回）、行程与最终回复。

需要 .env 里配置真实的 LLM_* 与 AMAP_API_KEY；会产生模型与高德调用费用。
用例串行执行（共用一个高德节流器，避免 10021 限流）。

用法（仓库根目录）：
  ./.venv/Scripts/python.exe eval/run_eval.py eval/results            # 全部用例
  ./.venv/Scripts/python.exe eval/run_eval.py eval/results 01_beijing_departure
  ./.venv/Scripts/python.exe eval/analyze.py eval/results              # 汇总指标
"""
import asyncio, json, os, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
from dotenv import load_dotenv; load_dotenv(".env")
from agents import Runner, SQLiteSession
from openai.types.responses import ResponseTextDeltaEvent
import app.agent as chat
from app._data.poi_repo import TripContext
from app._tools import extract_plan

CASES = [
    ("01_beijing_departure", ["下周三去北京玩3天，想去故宫、天坛、颐和园和南锣鼓巷，必吃烤鸭和炸酱面，住王府井，周五晚上18:00从北京南站坐高铁回去"]),
    ("02_hangzhou_relaxed", ["带爸妈去杭州玩2天，不想太累，想试试杭帮菜，住西湖边"]),
    ("03_xian_compact", ["西安2天特种兵行程，兵马俑、大雁塔、钟鼓楼、回民街都要去，想吃肉夹馍和羊肉泡馍，住钟楼附近"]),
    ("04_chongqing_1day", ["重庆一日游，洪崖洞、解放碑、李子坝轻轨穿楼，想吃小面和火锅，住解放碑"]),
    ("05_missing_info", ["想出去玩几天，帮我推荐一下"]),
    ("06_typo", ["去南京玩两天，想去中山林和夫子庙，想吃鸭血粉丝汤"]),
    ("07_small_town", ["去凤凰古城玩2天，必吃血粑鸭，住古城里"]),
    ("08_diet_budget", ["成都2天，我完全不能吃辣，人均80以内，想去宽窄巷子和武侯祠，住春熙路"]),
    ("09_multiturn", ["广州玩两天，想去广州塔和陈家祠，想吃早茶，住北京路", "第二天把陈家祠换成长隆野生动物世界"]),
    ("10_too_many", ["上海2天，想去外滩、豫园、南京路步行街、迪士尼、田子坊、武康路、陆家嘴，住人民广场"]),
]


async def run_turn(message, session, cid):
    rec = {"message": message, "tools": [], "plans": [], "text": "", "error": None}
    t0 = time.time()
    result = Runner.run_streamed(chat.agent, input=message, session=session, context=chat.context_for(cid),  # 与线上一致：同一会话共享 POI 缓存
                                 max_turns=chat.MAX_TURNS)
    pending = {}
    try:
        async for ev in result.stream_events():
            if ev.type == "raw_response_event" and isinstance(ev.data, ResponseTextDeltaEvent):
                rec["text"] += ev.data.delta
            elif ev.type == "run_item_stream_event" and ev.name == "tool_called":
                raw = getattr(ev.item, "raw_item", None)
                call = {"tool": getattr(raw, "name", None), "args": getattr(raw, "arguments", None),
                        "t": round(time.time() - t0, 1)}
                pending[getattr(raw, "call_id", None)] = call
                rec["tools"].append(call)
            elif ev.type == "run_item_stream_event" and ev.name == "tool_output":
                raw = getattr(ev.item, "raw_item", None)
                cid = raw.get("call_id") if isinstance(raw, dict) else getattr(raw, "call_id", None)
                out = getattr(ev.item, "output", None)
                if cid in pending:
                    pending[cid]["output"] = out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)
                plan = extract_plan(out)
                if plan:
                    rec["plans"].append(plan)
    except Exception as e:
        rec["error"] = f"{type(e).__name__}: {e}"
    rec["seconds"] = round(time.time() - t0, 1)
    rec["llm_turns"] = len(getattr(result, "raw_responses", []) or [])
    return rec


async def main(out_dir, ids):
    for cid, turns in CASES:
        if ids and cid not in ids:
            continue
        session = SQLiteSession(cid)  # 内存会话，多轮用例共享上下文
        recs = []
        for msg in turns:
            recs.append(await run_turn(msg, session, cid))
            print(f"[{cid}] {recs[-1]['seconds']}s tools={len(recs[-1]['tools'])} plans={len(recs[-1]['plans'])} "
                  f"err={recs[-1]['error']}", flush=True)
        json.dump({"id": cid, "turns": recs}, open(f"{out_dir}/{cid}.json", "w"), ensure_ascii=False, indent=1)

os.makedirs(sys.argv[1], exist_ok=True)
asyncio.run(main(sys.argv[1], sys.argv[2:]))
