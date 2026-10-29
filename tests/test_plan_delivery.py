"""行程投递链路测试：chat 事件流推送 plan 事件；空回复自动重试。

不调用大模型：用假的 Runner 事件流替代 Runner.run_streamed。刷新后恢复行程见 tests/test_server.py。

运行：./.venv/Scripts/python.exe -m unittest tests.test_plan_delivery -v
"""

import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest import mock

import app.agent as chat

PLAN = {"kind": "trip_plan", "city": "南京", "total_days": 1, "days": []}
PLAN_OUTPUT = json.dumps({"success": True, "display": "…", "plan": PLAN}, ensure_ascii=False)


class FakeStream:
    def __init__(self, events):
        self._events = events

    async def stream_events(self):
        for e in self._events:
            yield e


def _tool_event(event_name, **item):
    return SimpleNamespace(type="run_item_stream_event", name=event_name, item=SimpleNamespace(**item))


class TestChatStreamsPlan(unittest.TestCase):
    def _frames(self, events):
        async def collect():
            with mock.patch.object(chat.Runner, "run_streamed", return_value=FakeStream(events)):
                return [f async for f in chat._event_stream("去南京")]
        return asyncio.run(collect())

    def test_plan_event_emitted_from_itinerary_output(self):
        frames = self._frames([
            _tool_event("tool_called", name="generate_trip_itinerary"),
            _tool_event("tool_output", output=PLAN_OUTPUT),
        ])
        self.assertEqual(frames[0].split("\n")[0], "event: tool_called")
        self.assertEqual(frames[1].split("\n")[0], "event: plan")
        self.assertEqual(json.loads(frames[1].split("data: ", 1)[1])["city"], "南京")

    def test_other_tool_outputs_are_not_plans(self):
        frames = self._frames([
            _tool_event("tool_output", output='{"count": 1, "spots": []}'),
            _tool_event("tool_output", output='{"success": false, "error": "返程时间过早"}'),
        ])
        # 只验证不会推 plan 事件；这条假事件流没有任何文字，会按「空回复」重试后报错，见 TestEmptyRunRetry
        self.assertFalse(any(f.startswith("event: plan") for f in frames), frames)


if __name__ == "__main__":
    unittest.main()


class TestEmptyRunRetry(unittest.TestCase):
    """模型网关偶发空回复：一轮既没文字也没行程时自动重试一次，仍为空则明确报错，而不是让用户看到空白。"""

    def _frames(self, streams):
        seq = iter(streams)

        async def collect():
            with mock.patch.object(chat.Runner, "run_streamed", side_effect=lambda *a, **k: next(seq)) as run:
                frames = [f async for f in chat._event_stream("去南京")]
                return frames, run.call_count
        return asyncio.run(collect())

    def test_empty_then_success(self):
        from openai.types.responses import ResponseTextDeltaEvent
        delta = ResponseTextDeltaEvent(content_index=0, delta="好的", item_id="i", output_index=0,
                                       sequence_number=0, type="response.output_text.delta", logprobs=[])
        text_event = SimpleNamespace(type="raw_response_event", data=delta)
        frames, calls = self._frames([FakeStream([]), FakeStream([text_event])])
        self.assertEqual(calls, 2)
        self.assertTrue(any("好的" in f for f in frames))
        self.assertFalse(any(f.startswith("event: error") for f in frames))

    def test_plan_counts_as_output(self):
        frames, calls = self._frames([FakeStream([_tool_event("tool_output", output=PLAN_OUTPUT)])])
        self.assertEqual(calls, 1)
        self.assertTrue(any(f.startswith("event: plan") for f in frames))

    def test_empty_twice_reports_error(self):
        frames, calls = self._frames([FakeStream([]), FakeStream([])])
        self.assertEqual(calls, 2)
        self.assertTrue(frames[-1].startswith("event: error"))
        self.assertIn("再发送一次", frames[-1])
