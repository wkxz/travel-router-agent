"""高德额度用完时熔断：之后的请求不再发出，直接返回明确的说明，免得模型换关键词反复检索。

2026-10-30 实测：月免费额度用完后，/v3/place/text 返回 status=0、info=USER_DAILY_QUERY_OVER_LIMIT、
infocode=10044（名称里是 DAILY，实际是月额度）。旧版每次都真的发请求，再把错误原样转给模型。
熔断期 QUOTA_COOLDOWN 过后放一个请求探测：充值或到了下个月能自动恢复，不用重启服务。

运行：./.venv/Scripts/python.exe -m unittest tests.test_amap_quota -v
"""

import os
import unittest
from unittest import mock

from app._data import amap

QUOTA = {"status": "0", "info": "USER_DAILY_QUERY_OVER_LIMIT", "infocode": "10044"}
OK = {"status": "1", "pois": []}


class _Resp:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


class TestAmapQuota(unittest.TestCase):
    def setUp(self):
        amap._quota_blocked_until = 0.0
        self.addCleanup(setattr, amap, "_quota_blocked_until", 0.0)
        for p in (mock.patch.dict(os.environ, {"AMAP_API_KEY": "test-key-123456"}),
                  mock.patch.object(amap, "_throttle", lambda: None)):
            p.start()
            self.addCleanup(p.stop)

    def test_quota_error_trips_breaker(self):
        with mock.patch.object(amap.httpx, "get", return_value=_Resp(QUOTA)) as get:
            first = amap.amap_get("/place/text", {"keywords": "故宫"})
            second = amap.amap_get("/distance", {"origins": "1,1", "destination": "2,2"})
        self.assertEqual(get.call_count, 1)  # 第二次没有真的发请求
        for res in (first, second):
            self.assertIn("额度已用完", res["error"])
            self.assertIn("10044", res["error"])
            self.assertNotIn("今日", res["error"])  # 免费额度按月算

    def test_probe_after_cooldown(self):
        with mock.patch.object(amap.httpx, "get", return_value=_Resp(QUOTA)):
            amap.amap_get("/place/text", {"keywords": "故宫"})
        amap._quota_blocked_until = 0.0  # 模拟熔断期已过
        with mock.patch.object(amap.httpx, "get", return_value=_Resp(OK)) as get:
            self.assertEqual(amap.amap_get("/place/text", {"keywords": "故宫"}), OK)
            self.assertEqual(amap.amap_get("/place/text", {"keywords": "故宫"}), OK)
        self.assertEqual(get.call_count, 2)

    def test_other_errors_do_not_trip(self):
        bad = {"status": "0", "info": "INVALID_PARAMS", "infocode": "20000"}
        with mock.patch.object(amap.httpx, "get", return_value=_Resp(bad)) as get:
            amap.amap_get("/place/text", {})
            amap.amap_get("/place/text", {})
        self.assertEqual(get.call_count, 2)


if __name__ == "__main__":
    unittest.main()
