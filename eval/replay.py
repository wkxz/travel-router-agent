"""离线重放一例测评：按结果文件里记录的工具调用（参数原样），依次调用工具、重新排程，不调用模型。

改了排程引擎后，用它对比同一份输入前后排出来的行程；配合 AMAP_CACHE=replay 时完全不联网、不耗高德额度
（需要这一轮测评是用 AMAP_CACHE=record 跑的）。不设 AMAP_CACHE 时会实时请求高德。

用法（仓库根目录）：
  AMAP_CACHE=replay ./.venv/Scripts/python.exe eval/replay.py eval/results/round8/01_beijing_departure.json
  ./.venv/Scripts/python.exe eval/replay.py <结果 json> --turn 1      # 只看第 2 轮对话最后一份行程（默认最后一轮）
"""
import argparse
import asyncio
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
from dotenv import load_dotenv  # noqa: E402

load_dotenv(".env")
from agents.tool_context import ToolContext  # noqa: E402

from app import _tools as tools  # noqa: E402
from app._data.amap import cache_mode  # noqa: E402
from app._data.poi_repo import TripContext  # noqa: E402
from eval.plan_checks import check_plan  # noqa: E402


def replay(case: dict, upto_turn: int = -1) -> list:
    """按顺序重放到第 upto_turn 轮（含），返回每轮最后一份行程。多轮对话共用一个仓库，与线上同一会话一致。"""
    repo = TripContext().repo
    turns = case["turns"][: (upto_turn % len(case["turns"])) + 1]
    plans = []
    for turn in turns:
        ctx, plan = TripContext(repo=repo), None
        for call in turn["tools"]:
            tool = getattr(tools, call["tool"])
            tc = ToolContext(context=ctx, tool_name=tool.name, tool_call_id="replay", tool_arguments=call["args"])
            out = asyncio.run(tool.on_invoke_tool(tc, call["args"]))
            if call["tool"] == "generate_trip_itinerary":
                parsed = json.loads(out)
                plan = tools.extract_plan(parsed, ctx) or plan
                if not parsed.get("success"):
                    print(f"[排程失败] {parsed.get('error')}")
        plans.append(plan)
    return plans


def print_plan(plan: dict) -> None:
    for day in plan["days"]:
        print(f"第 {day['day_number']} 天 {day['date']}")
        for it in day["items"]:
            print(f"  {it['time_slot']:<15} {it['title']}  {it.get('transit_info') or ''}")
    if plan.get("unassigned_spots"):
        print("未安排：", "；".join(plan["unassigned_spots"]))
    print("plan_checks：", [f"[{i['type']}] {i['detail']}" for i in check_plan(plan)] or "无")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("result", help="eval/results/roundN/<用例>.json")
    ap.add_argument("--turn", type=int, default=-1, help="重放到第几轮对话（从 0 起，默认最后一轮）")
    args = ap.parse_args(argv)
    print(f"高德：{'回放缓存（不联网）' if cache_mode() == 'replay' else '录制' if cache_mode() == 'record' else '实时请求'}")
    with open(args.result, encoding="utf-8") as f:
        plans = replay(json.load(f), args.turn)
    if not plans or plans[-1] is None:
        print("这一轮没有生成行程")
        return
    print_plan(plans[-1])


if __name__ == "__main__":
    main()
