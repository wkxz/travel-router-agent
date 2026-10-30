"""行程合理性检查（eval/plan_checks.py）：用 2026-10-30 南京用户实测的旧行程片段验证能查出问题，修复后的排法不误报。

运行：./.venv/Scripts/python.exe -m unittest tests.test_plan_checks -v
"""

import unittest

from eval.plan_checks import check_plan

LOC = {
    "中山陵景区": (118.854097, 32.054508), "南京博物院": (118.825064, 32.040802), "明孝陵景区": (118.840463, 32.051926),
    "钟山风景名胜区中山陵景区音乐台": (118.855382, 32.055096),
    "先锋书店(老门东店)": (118.785259, 32.011452), "老门东": (118.787645, 32.011604),
    "总统府": (118.797398, 32.044228), "南京夫子庙": (118.788899, 32.02066),
    "百味斋素菜馆": (118.7966, 32.0625), "鸡鸣寺素面馆": (118.7952, 32.0611), "淮扬菜馆": (118.7960, 32.0615),
}


def _it(slot, category, name, title=None):
    lng, lat = LOC[name]
    return {"time_slot": slot, "category": category, "title": title or f"游览【{name}】",
            "location": {"name": name, "lng": lng, "lat": lat, "poi_id": None}}


def _plan(*days):
    return {"days": [{"day_number": i + 1, "date": d, "items": items} for i, (d, items) in enumerate(days)]}


class TestPlanChecks(unittest.TestCase):
    def test_old_day1_flags_reentry_and_dusk(self):
        plan = _plan(("2026-11-25", [
            _it("09:26 - 11:26", "spot", "中山陵景区"),
            _it("13:16 - 16:16", "spot", "南京博物院"),
            _it("16:38 - 17:38", "spot", "明孝陵景区"),
        ]))
        types = {i["type"] for i in check_plan(plan)}
        self.assertEqual(types, {"reentry", "outdoor_after_sunset"}, check_plan(plan))

    def test_old_day3_flags_reentry(self):
        plan = _plan(("2026-11-27", [
            _it("10:00 - 12:00", "spot", "先锋书店(老门东店)"),
            _it("13:29 - 15:29", "spot", "老门东"),
            _it("15:51 - 17:51", "spot", "总统府"),
            _it("18:30 - 19:50", "meal", "淮扬菜馆", "晚餐：【淮扬菜馆】"),
            _it("19:35 - 21:35", "spot", "南京夫子庙"),
        ]))
        issues = check_plan(plan)
        # 总统府 15:51-17:51 也比日落（约 17:01）晚了 50 分钟
        self.assertEqual([i["type"] for i in issues], ["reentry", "reentry", "outdoor_after_sunset"], issues)
        self.assertIn("总统府", issues[0]["detail"])

    def test_two_vegetarian_meals_flagged(self):
        plan = _plan(("2026-11-26", [
            _it("12:08 - 13:23", "meal", "百味斋素菜馆", "午餐：【百味斋素菜馆】"),
            _it("17:45 - 19:05", "meal", "鸡鸣寺素面馆", "晚餐：【鸡鸣寺素面馆】"),
        ]))
        self.assertEqual([i["type"] for i in check_plan(plan)], ["repeat_cuisine_day"])

    def test_snack_right_after_lunch_flagged(self):
        """模型点评发现、核实为真（2026-10-30 第 2 轮测评 7 个城市都有）：南京午饭 14:25 吃完，14:35 又排小吃。"""
        plan = _plan(("2026-11-26", [
            _it("13:10 - 14:25", "meal", "淮扬菜馆", "午餐：【淮扬菜馆】"),
            {"time_slot": "14:35 - 15:15", "category": "snack", "title": "特色小吃尝鲜：【某锅贴】", "location": None},
            _it("18:03 - 19:23", "meal", "百味斋素菜馆", "晚餐：【百味斋素菜馆】"),
        ]))
        issues = check_plan(plan)
        self.assertEqual([i["type"] for i in issues], ["meal_too_close"], issues)
        self.assertIn("10 分钟", issues[0]["detail"])

    def test_fixed_plan_is_clean(self):
        plan = _plan(
            ("2026-11-25", [
                _it("07:31 - 08:16", "spot", "钟山风景名胜区中山陵景区音乐台"),
                _it("08:35 - 10:35", "spot", "中山陵景区"),
                _it("13:20 - 16:20", "spot", "南京博物院"),
            ]),
            ("2026-11-27", [
                _it("09:00 - 11:00", "spot", "总统府"),
                _it("11:30 - 13:00", "spot", "先锋书店(老门东店)"),
                _it("14:00 - 16:00", "spot", "老门东"),
                _it("18:00 - 19:20", "meal", "淮扬菜馆", "晚餐：【淮扬菜馆】"),
                _it("19:35 - 21:35", "spot", "南京夫子庙"),  # 晚上逛：不算天黑进户外
            ]),
        )
        self.assertEqual(check_plan(plan), [])


if __name__ == "__main__":
    unittest.main()
