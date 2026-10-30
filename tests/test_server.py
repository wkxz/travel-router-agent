"""HTTP 服务端到端测试：对话、刷新后恢复行程、会话列表、清空、删除、停止、健康检查、前端托管。

不调用大模型：用假的 Runner 事件流替代 Runner.run_streamed；数据库用临时文件。
"""

import asyncio
import json
import os
import shutil
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

_TMP = tempfile.mkdtemp()
os.environ["DATABASE_PATH"] = os.path.join(_TMP, "test.db")

from openai.types.responses import ResponseTextDeltaEvent  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

import app.agent as chat  # noqa: E402
import server.app as server  # noqa: E402

PLAN = {"kind": "trip_plan", "city": "南京", "total_days": 1, "days": []}
PLAN_OUTPUT = json.dumps({"success": True, "display": "…", "plan": PLAN}, ensure_ascii=False)


def _text(delta):
    return SimpleNamespace(type="raw_response_event", data=ResponseTextDeltaEvent(
        content_index=0, delta=delta, item_id="i", output_index=0, sequence_number=0,
        type="response.output_text.delta", logprobs=[]))


def _tool(event_name, **item):
    return SimpleNamespace(type="run_item_stream_event", name=event_name, item=SimpleNamespace(**item))


class FakeStream:
    def __init__(self, events):
        self._events = events

    async def stream_events(self):
        for e in self._events:
            yield e

    def cancel(self):
        pass


def tearDownModule():
    server.store._conn.close()
    shutil.rmtree(_TMP, ignore_errors=True)


class TestServer(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(server.app)

    def _chat(self, cid, message, events, user="u1"):
        with mock.patch.object(chat.Runner, "run_streamed", return_value=FakeStream(events)):
            res = self.client.post("/chat", json={"message": message, "userId": user},
                                   headers={"X-Conversation-Id": cid})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.headers["content-type"].startswith("text/event-stream"))
        return [server._parse_sse(f)[0] for f in res.text.split("\n\n") if f.strip()]

    def test_chat_then_history_restores_text_and_plan(self):
        events = self._chat("c-plan", "去南京玩一天", [
            _tool("tool_called", name="generate_trip_itinerary"),
            _tool("tool_output", output=PLAN_OUTPUT),
            _text("行程如上，"), _text("注意带伞。"),
        ])
        self.assertEqual(events, ["tool_called", "plan", "text_delta", "text_delta", "done"])
        msgs = self.client.post("/history", json={"conversation_id": "c-plan"}).json()["messages"]
        self.assertEqual([m["role"] for m in msgs], ["user", "assistant"])
        self.assertEqual(msgs[0]["content"], "去南京玩一天")
        self.assertEqual(msgs[1]["content"], "行程如上，注意带伞。")
        self.assertEqual(msgs[1]["plan"]["city"], "南京")

    def test_conversation_list_scoped_by_user(self):
        self._chat("c-list-1", "成都三天怎么玩比较好", [_text("好的")], user="u-list")
        self._chat("c-list-2", "重庆一日游", [_text("**洪崖洞**夜景很美")], user="u-list")
        self._chat("c-other", "别人的会话", [_text("好的")], user="someone-else")
        data = self.client.post("/conversations", json={"user_id": "u-list"}).json()
        self.assertEqual([c["id"] for c in data["conversations"]], ["c-list-2", "c-list-1"])  # 最近的在前
        self.assertEqual(data["conversations"][1]["title"], "成都三天怎么玩比...")
        self.assertEqual(data["conversations"][0]["messageCount"], 2)
        self.assertEqual(data["conversations"][0]["preview"], "洪崖洞夜景很美")  # 预览去掉 Markdown 符号
        self.assertIsNone(data["nextCursor"])
        page = self.client.post("/conversations", json={"user_id": "u-list", "limit": 1}).json()
        self.assertEqual(page["nextCursor"], "1")
        page2 = self.client.post("/conversations", json={"user_id": "u-list", "limit": 1, "after": "1"}).json()
        self.assertEqual(page2["conversations"][0]["id"], "c-list-1")

    def test_clear_and_delete(self):
        self._chat("c-del", "去南京", [_text("好的")], user="u-del")
        self.assertEqual(self.client.post("/clear-history", json={"conversation_id": "c-del"}).status_code, 200)
        self.assertEqual(self.client.post("/history", json={"conversation_id": "c-del"}).json()["messages"], [])
        self.client.post("/delete-conversation", json={"conversation_id": "c-del"})
        self.assertEqual(self.client.post("/conversations", json={"user_id": "u-del"}).json()["conversations"], [])

    def test_agent_memory_is_per_conversation_and_cleared_on_delete(self):
        session = server.store.agent_session("c-mem")
        asyncio.run(session.add_items([{"role": "user", "content": "记住我住新街口"}]))
        self.assertEqual(len(asyncio.run(server.store.agent_session("c-mem").get_items())), 1)
        self.assertEqual(asyncio.run(server.store.agent_session("c-mem-other").get_items()), [])
        server.store.delete_conversation("c-mem")
        self.assertEqual(asyncio.run(server.store.agent_session("c-mem").get_items()), [])

    def test_stop_sets_cancel_signal_of_running_conversation(self):
        cancel = asyncio.Event()
        server._active_runs["c-stop"] = cancel
        try:
            res = self.client.post("/chat/stop", json={"conversation_id": "c-stop"}).json()
        finally:
            server._active_runs.pop("c-stop", None)
        self.assertTrue(res["aborted"])
        self.assertTrue(cancel.is_set())
        idle = self.client.post("/chat/stop", json={"conversation_id": "not-running"}).json()
        self.assertFalse(idle["aborted"])

    def test_bad_requests(self):
        self.assertEqual(self.client.post("/chat", json={}).status_code, 400)
        self.assertEqual(self.client.post("/conversations", json={}).status_code, 400)
        self.assertEqual(self.client.post("/chat/stop", json={}).status_code, 400)
        self.assertEqual(self.client.post("/history", content=b"not json").json()["messages"], [])

    def test_health_reports_missing_config(self):
        with mock.patch.dict(os.environ, {"AMAP_API_KEY": "", "LLM_MODEL": "", "AI_GATEWAY_MODEL": ""}):
            data = self.client.get("/health").json()
        self.assertEqual(data["status"], "misconfigured")
        self.assertIn("AMAP_API_KEY", data["missing"])
        self.assertIn("LLM_MODEL", data["missing"])

    def test_model_error_redacts_secrets(self):
        class Boom:
            async def stream_events(self):
                raise RuntimeError("upstream rejected key sk-test-123456")
                yield

            def cancel(self):
                pass
        with mock.patch.dict(os.environ, {"LLM_API_KEY": "sk-test-123456"}),                 mock.patch.object(chat.Runner, "run_streamed", return_value=Boom()):
            res = self.client.post("/chat", json={"message": "hi"}, headers={"X-Conversation-Id": "c-redact"})
        self.assertIn("error", res.text)
        self.assertNotIn("sk-test-123456", res.text)

    def test_frontend_served_with_spa_fallback(self):
        dist = os.path.join(_TMP, "dist")
        os.makedirs(os.path.join(dist, "assets"), exist_ok=True)
        with open(os.path.join(dist, "index.html"), "w", encoding="utf-8") as f:
            f.write("<html>index</html>")
        with open(os.path.join(dist, "assets", "a.js"), "w", encoding="utf-8") as f:
            f.write("console.log(1)")
        with mock.patch.object(server, "DIST_DIR", dist):
            self.assertIn("console.log", self.client.get("/assets/a.js").text)
            self.assertIn("index", self.client.get("/some/page").text)
            self.assertIn("index", self.client.get("/../../.env").text)  # 不能读到 dist 之外的文件
        # 前缀判断的漏洞：同级的 dist-old/ 路径也以 dist 开头
        sibling = os.path.join(_TMP, "dist-old")
        os.makedirs(sibling, exist_ok=True)
        with open(os.path.join(sibling, "secret.txt"), "w", encoding="utf-8") as f:
            f.write("secret")
        with mock.patch.object(server, "DIST_DIR", dist):
            self.assertNotIn("secret", self.client.get("/%2e%2e/dist-old/secret.txt").text)


if __name__ == "__main__":
    unittest.main()
