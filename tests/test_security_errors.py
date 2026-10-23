"""工具出错信息：要具体（让模型一次改对），且绝不带出密钥。

1. 第四轮测评：模型漏传必填的 total_days，SDK 默认只回「An error occurred while running the tool」，模型只能盲猜重试；
2. 排查时发现：httpx 的 HTTP 状态码异常文本含完整 URL（实测），URL 里有 key=AMAP_API_KEY，
   旧代码把它原样拼进错误返回给模型 —— 高德一旦返回 4xx/5xx，Key 就会进模型上下文和日志。

运行：./.venv/Scripts/python.exe -m unittest tests.test_security_errors -v
"""

import asyncio
import json
import unittest
from unittest import mock

import httpx

from tests.fake_amap import FakeAmap  # noqa: F401  挂载 app 别名
import app._tools as tools
import app._data.amap as amap
from agents.tool_context import ToolContext
from app._data.poi_repo import TripContext

FAKE_KEY = "FAKE_AMAP_KEY_0123456789"


def invoke(tool, args: dict) -> dict:
    raw = json.dumps(args, ensure_ascii=False)
    tc = ToolContext(context=TripContext(), tool_name=tool.name, tool_call_id="t1", tool_arguments=raw)
    return json.loads(asyncio.run(tool.on_invoke_tool(tc, raw)))


class TestToolErrorMessage(unittest.TestCase):
    def test_missing_required_param_is_named(self):
        out = invoke(tools.generate_trip_itinerary, {"city": "成都", "spots": "[]", "foods": "[]"})  # 缺 total_days
        self.assertFalse(out["success"])
        self.assertIn("total_days", out["error"])

    def test_error_text_is_redacted(self):
        with mock.patch.dict("os.environ", {"AMAP_API_KEY": FAKE_KEY}):
            msg = tools.tool_error(None, ValueError(f"boom key={FAKE_KEY}"))
        self.assertNotIn(FAKE_KEY, msg)
        self.assertIn("***", msg)


class TestAmapHttpErrorHasNoKey(unittest.TestCase):
    def _get(self, exc_factory):
        with mock.patch.dict("os.environ", {"AMAP_API_KEY": FAKE_KEY}), \
                mock.patch.object(amap.httpx, "get", side_effect=exc_factory), \
                mock.patch.object(amap.time, "sleep"):
            return amap.amap_get("/place/text", {"keywords": "x"})

    def test_status_error(self):
        def raise_403(url, params=None, timeout=None):
            req = httpx.Request("GET", url, params=params)
            httpx.Response(403, request=req).raise_for_status()
        out = self._get(raise_403)
        self.assertNotIn(FAKE_KEY, json.dumps(out, ensure_ascii=False))
        self.assertIn("HTTP 403", out["error"])

    def test_transport_error(self):
        def raise_connect(url, params=None, timeout=None):
            raise httpx.ConnectError(f"cannot connect {url}?key={params['key']}")
        out = self._get(raise_connect)
        self.assertNotIn(FAKE_KEY, json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
