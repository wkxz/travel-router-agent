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
    count = 0
    async for event in result.stream_events():
        count += 1
        t = event.type
        name = getattr(event, "name", None)
        print(f"#{count} [{t}] {name}")
        if t == "run_item_stream_event":
            item = getattr(event, "item", None)
            print("   -> item:", type(item), getattr(item, "type", None))

if __name__ == "__main__":
    asyncio.run(main())
