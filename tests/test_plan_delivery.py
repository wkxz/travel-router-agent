"""行程投递链路测试：chat 流式接口推送 plan 事件；history 云函数在刷新后恢复行程。

不调用大模型：用假的 Runner 事件流替代 Runner.run_streamed；history 用仿照
cloud-functions/history 文档中存储格式的假记录。

运行：./.venv/Scripts/python.exe -m unittest tests.test_plan_delivery -v
"""

import asyncio
import importlib.util
import json
import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

import tests.fake_amap  # noqa: F401  同时完成 app 别名挂载

os.environ.setdefault("AI_GATEWAY_API_KEY", "dummy")  # 模块加载时会创建 OpenAI 客户端，不会真正请求
import app.chat.index as chat  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLAN = {"kind": "trip_plan", "city": "南京", "total_days": 1, "days": []}
PLAN_OUTPUT = json.dumps({"success": True, "display": "…", "plan": PLAN}, ensure_ascii=False)


def _load_history():
    path = os.path.join(ROOT, "cloud-functions", "history", "index.py")
    spec = importlib.util.spec_from_file_location("history_fn", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


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


def _rec(role, content, item_type, run_id, t, mid=None):
    return SimpleNamespace(role=role, content=content, created_at=t, message_id=mid or f"m{t}",
                           metadata={"agent_sdk_session": True, "item_type": item_type, "run_id": run_id})


def _text(role, text, run_id, t):
    part = "input_text" if role == "user" else "output_text"
    return _rec(role, {"type": "message", "role": role, "content": [{"type": part, "text": text}]}, "message", run_id, t)


class TestHistoryRestoresPlan(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.history = _load_history()

    def _bubbles(self, records):
        h = self.history
        visible = [p for r in records if (p := h._to_message(r) or h._to_plan_fragment(r))]
        return h._dedupe_adjacent(h._merge_assistant_fragments(visible))

    def test_plan_attached_to_same_run_bubble(self):
        bubbles = self._bubbles([
            _text("user", "去南京玩一天", "r1", 1),
            _text("assistant", "我先查一下天气。", "r1", 2),
            _rec("assistant", {"type": "function_call", "name": "generate_trip_itinerary", "call_id": "c1",
                               "arguments": "{}"}, "function_call", "r1", 3),
            _rec("tool", {"type": "function_call_output", "call_id": "c1", "output": PLAN_OUTPUT},
                 "function_call_output", "r1", 4),
            _text("assistant", "行程如上，注意带伞。", "r1", 5),
        ])
        self.assertEqual([b["role"] for b in bubbles], ["user", "assistant"])
        self.assertEqual(bubbles[1]["plan"]["city"], "南京")
        self.assertEqual(bubbles[1]["content"], "我先查一下天气。\n\n行程如上，注意带伞。")

    def test_plan_without_text_still_shown(self):
        # 用户在模型回复前点了停止：只有行程，没有文字
        bubbles = self._bubbles([
            _text("user", "去南京", "r1", 1),
            _rec("tool", {"type": "function_call_output", "call_id": "c1", "output": PLAN_OUTPUT},
                 "function_call_output", "r1", 2),
            _text("user", "再来一份", "r2", 3),
            _rec("tool", {"type": "function_call_output", "call_id": "c2", "output": PLAN_OUTPUT},
                 "function_call_output", "r2", 4),
        ])
        self.assertEqual([(b["role"], bool(b.get("plan"))) for b in bubbles],
                         [("user", False), ("assistant", True), ("user", False), ("assistant", True)])

    def test_failed_or_other_outputs_ignored(self):
        bubbles = self._bubbles([
            _text("user", "去南京", "r1", 1),
            _rec("tool", {"type": "function_call_output", "output": '{"success": false, "error": "x"}'},
                 "function_call_output", "r1", 2),
            _rec("tool", {"type": "function_call_output", "output": '{"count": 2, "spots": []}'},
                 "function_call_output", "r1", 3),
        ])
        self.assertEqual([b["role"] for b in bubbles], ["user"])

    def test_copy_of_extract_plan_matches_agent(self):
        import app._tools as tools
        for output in (PLAN_OUTPUT, '{"success": false}', "坏", '{"success": true, "plan": {"kind": "x"}}'):
            self.assertEqual(self.history._extract_plan(output), tools.extract_plan(output), output)


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
