"""eval/replay.py：按测评记录的工具调用重放；先 AMAP_CACHE=record 跑一遍，之后 replay 不联网、行程完全一致。

高德用 tests/fake_amap.py 在 httpx 层模拟（经过 amap_get 的录制回放逻辑）。
运行：./.venv/Scripts/python.exe -m unittest tests.test_eval_replay -v
"""

import json
import os
import tempfile
import unittest
from unittest import mock

from app._data import amap
from eval.replay import replay
from tests.fake_amap import FakeAmap


class _Resp:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


def _case():
    call = lambda tool, **args: {"tool": tool, "args": json.dumps(args, ensure_ascii=False)}
    return {"turns": [{"tools": [
        call("geocode_location", address="新街口", city="南京"),
        call("search_scenic_spots", city="南京", keywords="南京博物院"),
        call("search_food_and_snacks", city="南京", keywords="鸭血粉丝", near_id="B0MUSEUM"),
        call("generate_trip_itinerary", city="南京", total_days=1, spots='["B0MUSEUM", "B0LAKE"]',
             foods='["B0DUCK", "B0SNACK"]', hotel_id="geo:新街口|南京", start_date="2026-10-27"),
    ]}]}


class TestEvalReplay(unittest.TestCase):
    def setUp(self):
        db = os.path.join(tempfile.mkdtemp(), "amap.db")
        amap._quota_blocked_until = 0.0
        for p in (mock.patch.dict(os.environ, {"AMAP_API_KEY": "test-key-123456", "AMAP_CACHE_PATH": db}),
                  mock.patch.object(amap, "_throttle", lambda: None)):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(amap.close_cache)

    def test_record_then_replay_same_plan(self):
        fake = FakeAmap()

        def get(url, params, timeout):
            path = url[len(amap.AMAP_HOST):] if url.startswith(amap.AMAP_HOST + "/v5/") else url[len(amap.AMAP_BASE):]
            return _Resp(fake(path, {k: v for k, v in params.items() if k != "key"}))

        with mock.patch.dict(os.environ, {"AMAP_CACHE": "record"}), mock.patch.object(amap.httpx, "get", get):
            recorded = replay(_case())[-1]
        self.assertTrue(recorded and recorded["days"], recorded)
        with mock.patch.dict(os.environ, {"AMAP_CACHE": "replay"}), \
                mock.patch.object(amap.httpx, "get", side_effect=AssertionError("回放时不应联网")):
            replayed = replay(_case())[-1]
        self.assertEqual(replayed, recorded)


if __name__ == "__main__":
    unittest.main()
