"""高德请求录制回放（AMAP_CACHE=record / replay）：测评与调试离线重放，不耗额度、结果可复现。

- record：照常请求高德，成功的返回按「接口 + 参数」存进本地 SQLite；
- replay：只读缓存、不联网，未命中直接报错（不悄悄去请求高德，保证重放用的是同一份数据）；
- 默认关闭（线上不缓存：路况、营业时间会变）。
出错的返回（限流、额度用完等）不存，键里不含 Key。

运行：./.venv/Scripts/python.exe -m unittest tests.test_amap_cache -v
"""

import os
import sqlite3
import tempfile
import unittest
from unittest import mock

from app._data import amap

OK = {"status": "1", "pois": [{"id": "B000A8UIN8", "name": "故宫博物院"}]}
QPS = {"status": "0", "info": "CUQPS_HAS_EXCEEDED_THE_LIMIT", "infocode": "10021"}


class _Resp:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


class TestAmapCache(unittest.TestCase):
    def setUp(self):
        self.db = os.path.join(tempfile.mkdtemp(), "amap.db")
        amap._quota_blocked_until = 0.0
        for p in (mock.patch.dict(os.environ, {"AMAP_API_KEY": "test-key-123456", "AMAP_CACHE_PATH": self.db}),
                  mock.patch.object(amap, "_throttle", lambda: None),
                  mock.patch.object(amap.time, "sleep", lambda _s: None)):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(amap.close_cache)

    def _mode(self, mode):
        p = mock.patch.dict(os.environ, {"AMAP_CACHE": mode})
        p.start()
        self.addCleanup(p.stop)

    def test_record_then_replay_offline(self):
        self._mode("record")
        with mock.patch.object(amap.httpx, "get", return_value=_Resp(OK)) as get:
            self.assertEqual(amap.amap_get("/place/text", {"keywords": "故宫", "city": "北京"}), OK)
        self.assertEqual(get.call_count, 1)
        self._mode("replay")
        with mock.patch.object(amap.httpx, "get", side_effect=AssertionError("回放时不应联网")):
            # 参数顺序不同也命中
            self.assertEqual(amap.amap_get("/place/text", {"city": "北京", "keywords": "故宫"}), OK)

    def test_replay_miss_is_error(self):
        self._mode("replay")
        with mock.patch.object(amap.httpx, "get", side_effect=AssertionError("回放时不应联网")):
            res = amap.amap_get("/place/text", {"keywords": "没录过"})
        self.assertIn("回放缓存未命中", res["error"])

    def test_errors_not_recorded(self):
        self._mode("record")
        with mock.patch.object(amap.httpx, "get", return_value=_Resp(QPS)):
            self.assertIn("error", amap.amap_get("/place/text", {"keywords": "故宫"}, retries=1))
        self._mode("replay")
        self.assertIn("回放缓存未命中", amap.amap_get("/place/text", {"keywords": "故宫"})["error"])

    def test_key_not_stored(self):
        self._mode("record")
        with mock.patch.object(amap.httpx, "get", return_value=_Resp(OK)):
            amap.amap_get("/place/text", {"keywords": "故宫"})
        amap.close_cache()
        with sqlite3.connect(self.db) as conn:
            dump = "\n".join(str(row) for row in conn.execute("SELECT * FROM responses"))
        self.assertNotIn("test-key-123456", dump)
        self.assertIn("故宫", dump)

    def test_off_by_default(self):
        with mock.patch.dict(os.environ, {"AMAP_CACHE": ""}), \
                mock.patch.object(amap.httpx, "get", return_value=_Resp(OK)) as get:
            amap.amap_get("/place/text", {"keywords": "故宫"})
            amap.amap_get("/place/text", {"keywords": "故宫"})
        self.assertEqual(get.call_count, 2)
        self.assertFalse(os.path.exists(self.db))


if __name__ == "__main__":
    unittest.main()
