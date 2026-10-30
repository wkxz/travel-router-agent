"""模型点评脚本（eval/review.py）：提示词拼装、输出解析、失败处理。用假的模型客户端，不发网络请求、不产生费用。

运行：./.venv/Scripts/python.exe -m unittest tests.test_review -v
"""

import json
import os
import shutil
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from eval import review

PLAN = {"city": "南京", "total_days": 1, "hotel": {"name": "新街口"}, "notes": [], "unassigned_spots": [],
        "unmet_must_eat": [], "days": [{"day_number": 1, "date": "2026-11-25", "estimated_distance_km": 9.0, "items": [
            {"time_slot": "09:26 - 11:26", "category": "spot", "title": "游览【中山陵景区】", "transit_info": "打车约 26 分钟",
             "open_time": "08:30-17:00", "warnings": [],
             "location": {"name": "中山陵景区", "lng": 118.854097, "lat": 32.054508, "poi_id": "B1"}},
            {"time_slot": "13:16 - 16:16", "category": "spot", "title": "游览【南京博物院】", "transit_info": "",
             "open_time": "", "warnings": [],
             "location": {"name": "南京博物院", "lng": 118.825064, "lat": 32.040802, "poi_id": "B2"}},
            {"time_slot": "16:38 - 17:38", "category": "spot", "title": "游览【明孝陵景区】", "transit_info": "步行约 22 分钟",
             "open_time": "06:30-18:30", "warnings": ["顺路加游"],
             "location": {"name": "明孝陵景区", "lng": 118.840463, "lat": 32.051926, "poi_id": "B3"}},
        ]}]}
RECORD = {"id": "case1", "turns": [{"message": "去南京玩一天", "tools": [], "plans": [PLAN]}]}


class FakeClient:
    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.prompts = reply, error, []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, model, messages, temperature):
        self.prompts.append(messages[-1]["content"])
        if self.error:
            raise self.error
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self.reply))],
                               usage=SimpleNamespace(prompt_tokens=100, completion_tokens=20))


class TestReview(unittest.TestCase):
    def test_prompt_has_request_timeline_and_rule_findings(self):
        client = FakeClient(reply='{"issues": []}')
        review.review_record(RECORD, client, "m", dry_run=False)
        prompt = client.prompts[0]
        self.assertIn("去南京玩一天", prompt)
        self.assertIn("16:38 - 17:38 ｜ 游览【明孝陵景区】", prompt)
        self.assertIn("交通：步行约 22 分钟", prompt)
        # 规则已查出的问题放进提示词，让模型不必重复
        self.assertIn("[outdoor_after_sunset] 明孝陵景区", prompt)
        self.assertIn("[reentry]", prompt)

    def test_parse_tolerates_code_fence_and_drops_bad_items(self):
        text = '好的：\n```json\n{"issues": [{"day": 1, "problem": "天黑进景区", "severity": "urgent"}, {"day": 2}]}\n```'
        issues = review.parse_issues(text)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["severity"], "low")  # 不认识的等级降为 low
        with self.assertRaises(ValueError):
            review.parse_issues("没有问题")

    def test_model_error_recorded_without_secret(self):
        with mock.patch.dict(os.environ, {"REVIEW_API_KEY": "sk-review-123456"}):
            client = FakeClient(error=RuntimeError("401 bad key sk-review-123456"))
            entries = review.review_record(RECORD, client, "m", dry_run=False)
        self.assertIn("RuntimeError", entries[0]["error"])
        self.assertNotIn("sk-review-123456", entries[0]["error"])

    def test_main_writes_results_and_summary(self):
        tmp = tempfile.mkdtemp()
        try:
            with open(os.path.join(tmp, "case1.json"), "w", encoding="utf-8") as f:
                json.dump(RECORD, f, ensure_ascii=False)
            reply = json.dumps({"issues": [{"day": 1, "items": ["16:38 - 17:38 明孝陵景区"], "type": "时间",
                                            "severity": "high", "problem": "天黑才进明孝陵",
                                            "evidence": "常识判断：11 月底南京约 17:00 日落，需核实",
                                            "suggestion": "改到上午和中山陵一起"}]}, ensure_ascii=False)
            with mock.patch.dict(os.environ, {"REVIEW_MODEL": "m", "REVIEW_API_KEY": "k"}), \
                    mock.patch("openai.OpenAI", return_value=FakeClient(reply=reply)):
                with mock.patch("builtins.print"):
                    review.main([tmp])
            with open(os.path.join(tmp, "review", "case1.json"), encoding="utf-8") as f:
                saved = json.load(f)
            self.assertEqual(saved["turns"][0]["issues"][0]["problem"], "天黑才进明孝陵")
            with open(os.path.join(tmp, "review", "summary.md"), encoding="utf-8") as f:
                summary = f.read()
            self.assertIn("high 1", summary)
            self.assertIn("天黑才进明孝陵", summary)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_dry_run_does_not_call_model(self):
        with mock.patch("openai.OpenAI") as factory, mock.patch("builtins.print"):
            tmp = tempfile.mkdtemp()
            try:
                with open(os.path.join(tmp, "case1.json"), "w", encoding="utf-8") as f:
                    json.dump(RECORD, f, ensure_ascii=False)
                review.main([tmp, "--dry-run"])
                self.assertFalse(os.path.exists(os.path.join(tmp, "review")))
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
        factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
