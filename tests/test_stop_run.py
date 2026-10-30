"""点「停止生成」后，后台的 agent 必须真的停下，不能继续调模型、调工具（花钱、耗高德额度）。

2026-10-30 实测：旧版 _event_stream / chat_events 只是不再读事件流（return / break），没有调用
RunResultStreaming.cancel()，SDK 的后台任务照常运行——停止后 3 秒内又调了 6 次工具、请求了 7 次模型。
这里用假模型（每轮都要求调一次工具）复现，不调用真实模型。

运行：./.venv/Scripts/python.exe -m unittest tests.test_stop_run -v
"""

import asyncio
import time
import unittest
from unittest import mock

from agents import Agent, function_tool
from agents.models.interface import Model
from openai.types.responses import Response, ResponseCompletedEvent, ResponseFunctionToolCall

from app import agent as agent_mod

calls = []


@function_tool
def slow_tool() -> str:
    """测试用工具。"""
    calls.append(time.monotonic())
    time.sleep(0.1)
    return "ok"


class LoopingModel(Model):
    """每次都返回一个工具调用，永不结束（直到 max_turns）。"""

    def __init__(self):
        self.requests = 0

    async def get_response(self, *args, **kwargs):
        raise NotImplementedError

    async def stream_response(self, *args, **kwargs):
        self.requests += 1
        await asyncio.sleep(0.05)
        call = ResponseFunctionToolCall(type="function_call", call_id=f"c{self.requests}", name="slow_tool",
                                        arguments="{}", id=f"fc{self.requests}", status="completed")
        resp = Response.model_construct(id=f"r{self.requests}", output=[call], usage=None, created_at=0,
                                        model="fake", object="response", parallel_tool_calls=True,
                                        tool_choice="auto", tools=[])
        yield ResponseCompletedEvent(type="response.completed", response=resp, sequence_number=0)


class TestStopRun(unittest.TestCase):
    def test_stop_cancels_background_run(self):
        model = LoopingModel()
        fake = Agent(name="fake", tools=[slow_tool], model=model)

        async def scenario():
            cancel = asyncio.Event()
            frames = []
            async for frame in agent_mod.chat_events("hi", None, None, cancel):
                frames.append(frame)
                if "tool_called" in frame:
                    cancel.set()  # 用户点了停止
            n_calls, n_requests = len(calls), model.requests
            await asyncio.sleep(1.0)
            return frames, len(calls) - n_calls, model.requests - n_requests

        calls.clear()
        with mock.patch.object(agent_mod, "agent", fake), mock.patch.object(agent_mod, "MAX_TURNS", 30):
            frames, more_calls, more_requests = asyncio.run(scenario())
        self.assertIn('"stopped": true', frames[-1])
        self.assertEqual((more_calls, more_requests), (0, 0))


if __name__ == "__main__":
    unittest.main()
