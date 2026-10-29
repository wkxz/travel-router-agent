"""按日期取营业时间：高德 opentime2 常按星期、特殊日期分段写，要先取出当天适用的那一段再判断。

实测（2026-10-24 武汉）：「周一至周四 06:00-15:00；周五至周日 06:00-20:00」。旧逻辑把两段时刻合在一起，
周二 18:00 的晚餐被判为营业，实际 15:00 已关门。还有「2026-02-16至2026-02-18 周一至周日 全天关闭」
这类节假日特殊安排。

纯标准库，不依赖引擎其他模块（geo / calendar_pack 都从这里取星期正则）。
"""

import re
from datetime import date
from typing import List, Optional

_WEEKDAY_CHARS = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
_D = "[一二三四五六日天]"
_WD = rf"(?:周|星期|礼拜)({_D})"
_RANGE = _WD + r"\s*(?:至|到|-|–|~|—)\s*" + _WD
_WD_LIST = rf"(?:周|星期|礼拜)({_D}(?:[、,，和及]?(?:周|星期|礼拜)?{_D})*)"

# 一条星期条款的开头：「周一至周四」「周六、周日」「周末」「工作日」
_WEEKDAY_SPEC_RE = re.compile(rf"{_RANGE}|{_WD_LIST}|(周末)|(工作日)")
# 日期：「2026-10-03」「03-16」（不写年份，每年循环）「10月8日」；前后不能紧挨数字或冒号，免得把「08:00-17:30」里的「00-17」当成日期
_DATE = r"(?<![\d:])(?:(?:(\d{4})-)?(\d{1,2})-(\d{1,2})|(\d{1,2})月(\d{1,2})日)(?![\d:])"
_DATE_RANGE_RE = re.compile(_DATE + r"\s*(?:至|到|~|—)\s*" + _DATE)
_TIME_RANGE_RE = re.compile(r"\d{1,2}:\d{2}\s*[-–~]\s*\d{1,2}:\d{2}")
_CLOSED_RE = re.compile(r"关闭|闭馆|休馆|闭园|休息|休业|歇业|不营业|不开放|停业")
_ALL_DAY_RE = re.compile(r"24小时|全天(?!关闭|休息|闭馆)")

CLOSED_MARK = "当日不营业"  # hours_on_date 判定当天不营业时的返回值，find_open_slot 据此返回 False


def day_span(a: str, b: str) -> List[int]:
    start, end = _WEEKDAY_CHARS[a], _WEEKDAY_CHARS[b]
    return list(range(start, end + 1)) if start <= end else list(range(start, 7)) + list(range(0, end + 1))


def _spec_days(m: re.Match) -> List[int]:
    if m.group(1):
        return day_span(m.group(1), m.group(2))
    if m.group(3):
        return [_WEEKDAY_CHARS[c] for c in m.group(3) if c in _WEEKDAY_CHARS]
    return [5, 6] if m.group(4) else [0, 1, 2, 3, 4]


def _resolve(body: str) -> Optional[str]:
    """一段条款正文的含义：有时刻或全天营业 → 原文；只写了关闭类字样 → CLOSED_MARK；都没有 → None（无信息）。"""
    if _TIME_RANGE_RE.search(body) or _ALL_DAY_RE.search(body):
        return body.strip()
    if _CLOSED_RE.search(body):
        return CLOSED_MARK
    return None


def _weekday_part(text: str, weekday: int) -> str:
    text = text.strip()
    specs = list(_WEEKDAY_SPEC_RE.finditer(text))
    if not specs:
        return text
    picked = []
    for i, m in enumerate(specs):
        if weekday in _spec_days(m):
            end = specs[i + 1].start() if i + 1 < len(specs) else len(text)
            picked.append(_resolve(text[m.end():end]))
    picked = [p for p in picked if p]
    opened = [p for p in picked if p != CLOSED_MARK]
    if opened:
        return " ".join(opened)
    if picked:
        return CLOSED_MARK
    # 没有条款覆盖当天：条款前面若有不带星期的通用时刻（「09:00-17:00 周一闭馆」），就用它
    general = _resolve(text[:specs[0].start()])
    if general and general != CLOSED_MARK:
        return general
    if any(_resolve(text[m.end():]) not in (None, CLOSED_MARK) for m in specs):
        return CLOSED_MARK  # 列出了营业的星期却不含当天（「周二至周日 09:00-17:00」的周一）
    return text  # 只有闭馆条款且不涉及当天，原样交给调用方


def _date_range_covers(g: tuple, day: date) -> bool:
    """g 是 _DATE_RANGE_RE 的十个分组（每端：年、月、日、月、日）。不写年份的按月日比较，可跨年（「11-01至03-15」）。"""
    def parts(year, m1, d1, m2, d2):
        return (int(year) if year else None, int(m1 or m2), int(d1 or d2))
    (y1, m1, d1), (y2, m2, d2) = parts(*g[:5]), parts(*g[5:])
    try:
        if y1 and y2:
            return date(y1, m1, d1) <= day <= date(y2, m2, d2)
    except ValueError:
        return False
    lo, hi, today = (m1, d1), (m2, d2), (day.month, day.day)
    return lo <= today <= hi if lo <= hi else today >= lo or today <= hi


def hours_on_date(open_time: str, day: Optional[date]) -> str:
    """取出 day 当天适用的营业时间文本；不分星期/日期的文本原样返回，day 为 None 时也原样返回。

    - 特殊日期段（「2026-10-03至2026-10-07 05:30-20:00」）覆盖其日期内的常规安排；
      不写年份的季节段（「11-01至03-15 …」「10月8日至3月31日 …」）每年循环，可跨年；
    - 星期条款（「周一至周四 06:00-15:00；周五至周日 06:00-20:00」）只取包含当天的；
    - 判定当天不营业时返回 CLOSED_MARK。
    """
    text = (open_time or "").strip()
    if not text or day is None:
        return text
    ranges = list(_DATE_RANGE_RE.finditer(text))
    if ranges:
        for i, m in enumerate(ranges):
            if not _date_range_covers(m.groups(), day):
                continue
            end = ranges[i + 1].start() if i + 1 < len(ranges) else len(text)
            special = _weekday_part(text[m.end():end], day.weekday())
            return special if _resolve(special) else text[:ranges[0].start()].strip() or text
        text = text[:ranges[0].start()].strip()
    return _weekday_part(text, day.weekday())
