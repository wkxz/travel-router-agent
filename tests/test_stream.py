import asyncio
import os
import sys
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, ".edgeone/agent-python")

from pages_agents.chat.index import agent
from agents import Runner

async def main():
    print("开始测试 Agent...")
    result = Runner.run_streamed(agent, input="去成都玩2天，想去大熊猫基地和宽窄巷子，想吃火锅，住春熙路")
    async for event in result.stream_events():
        t = event.type
        name = getattr(event, "name", None)
        if t == "run_item_stream_event":
            item = getattr(event, "item", None)
            item_type = getattr(item, "type", None)
            print(f"[RunItem] name={name}, item_type={item_type}, item={repr(item)[:150]}")
        elif t == "raw_response_event":
            delta = getattr(getattr(event, "data", None), "delta", None)
            if delta:
                print(f"[Delta] {delta}", end="", flush=True)

if __name__ == "__main__":
    asyncio.run(main())
