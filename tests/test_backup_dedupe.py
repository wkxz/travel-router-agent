"""备选景点和主景点是同一个地方时，不拿来补空档。

round8 南京：
- 12_elderly_multiturn：轻松节奏也补空档后，第 1 天补进「夫子庙秦淮风光带」，第 2 天晚上本来就去「南京夫子庙」（相距 0.49km）；
- 06_typo（原样复现）：第 2 天上午游完老门东，下午空档又补「老门东牌坊」（相距 0.05km，是老门东的入口牌坊）。
只看距离不行：北海公园离「什刹海」POI 坐标 0.06km，却是两个景点；只看公共字也不行：凤凰「凤凰古城-西门城楼」
「凤凰古城虹桥」都带「凤凰古城」，是古城里的不同景点。所以要求相距 1km 内，且一个的名称（去掉城市名）完整出现在另一个里，
或是子景点。

运行：./.venv/Scripts/python.exe -m unittest tests.test_backup_dedupe -v
"""

import unittest

from app.engine import LocationPoint, Spot, TripScheduler
from app.engine.scheduler import duplicates_spot

HOTEL = LocationPoint(name="住处", lng=118.783, lat=32.04)
FUZIMIAO = Spot(name="南京夫子庙", lng=118.788899, lat=32.02066, duration_minutes=120, best_period="evening")
QINHUAI = Spot(name="夫子庙秦淮风光带", lng=118.7898, lat=32.0249, duration_minutes=90, open_time="00:00-24:00")
MAIN = Spot(name="大景区", lng=118.785, lat=32.03, duration_minutes=150, open_time="08:00-18:00")


class TestBackupDedupe(unittest.TestCase):
    def test_rule(self):
        laomendong = Spot(name="老门东", lng=118.787645, lat=32.011604)
        self.assertTrue(duplicates_spot(QINHUAI, FUZIMIAO, "南京"))
        self.assertTrue(duplicates_spot(Spot(name="老门东牌坊", lng=118.7877, lat=32.0112), laomendong))
        beihai = Spot(name="北海公园", lng=116.3893, lat=39.9419)
        self.assertFalse(duplicates_spot(beihai, Spot(name="什刹海", lng=116.3897, lat=39.9424)))
        # 名称像、但离得远的不算（分店、同名不同地）
        self.assertFalse(duplicates_spot(Spot(name="夫子庙秦淮风光带", lng=118.90, lat=32.10), FUZIMIAO, "南京"))
        # round8 凤凰：古城里的不同景点（相距 0.4km）
        self.assertFalse(duplicates_spot(Spot(name="凤凰古城-西门城楼", lng=109.5978, lat=27.9505),
                                         Spot(name="凤凰古城虹桥", lng=109.6018, lat=27.9506), "凤凰县"))

    def test_duplicate_backup_not_used(self):
        plan = TripScheduler("南京", 2, HOTEL, start_date="2026-11-03").schedule(
            [MAIN, FUZIMIAO], [], backup_spots=[QINHUAI])
        names = [it.location.name for d in plan.days for it in d.items if it.category == "spot"]
        self.assertNotIn("夫子庙秦淮风光带", names)
        self.assertIn("南京夫子庙", names)


if __name__ == "__main__":
    unittest.main()
