"""按日期取营业时间：高德 opentime2 常按星期、特殊日期分段写，要先取出当天适用的那一段再判断。

实测（2026-10-24 武汉）：「周一至周四 06:00-15:00；周五至周日 06:00-20:00」。旧逻辑把两段时刻合在一起，
周二 18:00 的晚餐被判为营业，实际 15:00 已关门。还有「2026-02-16至2026-02-18 周一至周日 全天关闭」
这类节假日特殊安排。

纯标准库，不依赖引擎其他模块（geo / calendar_pack 都从这里取星期正则）。
"""

import re
from functools import lru_cache
from datetime import date
from typing import List, Optional

_WEEKDAY_CHARS = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
_D = "[一二三四五六日天]"
_WD = rf"(?:周|星期|礼拜)({_D})"
_RANGE = _WD + r"\s*(?:至|到|-|–|~|—)\s*" + _WD
_PREFIX = "(?:周|星期|礼拜)"
_SEP = r"\s*(?:至|到|-|–|~|—)\s*"
# 列表里的每一项可以是单日或区间：「周一，周三至周日」是 {周一} ∪ {周三..周日}。
# 旧版列表只认单日，把「周一，周三」当成一组、吃掉了「周三至周日」的开头（成都宽窄巷子实测只剩周日营业）
_ITEM_RE = re.compile(rf"({_D})(?:{_SEP}{_PREFIX}?({_D}))?")
_ITEM = rf"{_D}(?:{_SEP}{_PREFIX}?{_D})?"
_WD_LIST = rf"({_PREFIX}{_ITEM}(?:\s*[、,，和及]\s*{_PREFIX}?{_ITEM})*)"

# 一条星期条款的开头：「周一至周四」「周六、周日」「周一，周三至周日」「周末」「工作日」
_WEEKDAY_SPEC_RE = re.compile(rf"{_WD_LIST}|(周末)|(工作日)")
# 日期：「2026-10-03」「03-16」（不写年份，每年循环）「10月8日」；前后不能紧挨数字或冒号，免得把「08:00-17:30」里的「00-17」当成日期
# 数字写法两侧不能紧挨数字或冒号（免得吃进「8:30-19:00」）；「10月6日:9:30」这种月日写法后面可以直接跟冒号
_DATE = r"(?<![\d:])(?:(?:(\d{4})-)?(\d{1,2})-(\d{1,2})(?![\d:])|(\d{1,2})月(\d{1,2})日)"
# 连接符含短横线：「10月1日-10月6日」（2026-10-30 测评长隆）。_DATE 两侧排除了数字和冒号，不会误吃「8:30-19:00」
# 「11月16日—次年3月14日」：次年只说明跨年，按月日比较时本来就支持跨年
_DATE_RANGE_RE = re.compile(_DATE + r"\s*(?:至|到|~|—|-|–)\s*(?:次年)?" + _DATE)
# 几个日期段共用后面一段时刻：「1月1日-3月31日与11月1日-12月31日 周二至周日 08:30-16:30」
_RANGE_JOIN_RE = re.compile(r"\s*(?:与|和|及|、|,|，)\s*")
_OPEN_BRACKETS = "(（"
_CLOSE_BRACKETS = ")）"
_TIME_RANGE_RE = re.compile(r"\d{1,2}:\d{2}\s*[-–~]\s*(?:次日)?\d{1,2}:\d{2}")  # 「07:00-次日00:30」
# 只写「营业」「开放」、不带时刻：当天开门，时刻未知（「周六至周日 营业」旧版被当成不营业）
_OPEN_WORD_RE = re.compile(r"^[\s,，、:：]*(?:正常)?(?:开放|营业)")
# 节假日的特殊安排：括号注释「（含周一）」「(法定节假日除外)」整段去掉，以「节假日」开头的分句也去掉，不当成平日营业时间。
# 实测「周一, 全天不开放；法定节假日（含周一）, 09:00-17:30」旧版把括号里的「周一」当成周一营业
_HOLIDAY_NOTE_RE = re.compile(r"[（(][^）)]*[）)]")
# 只去掉以「节假日」开头的整个分句（分号分隔）；「周六至周日、节假日 10:30-21:00」「，节假日以官方通知为准」不动
_HOLIDAY_CLAUSE_RE = re.compile(r"(?<=[；;])\s*(?:国家)?(?:法定)?节假日[^；;]*")
_CLOSED_RE = re.compile(r"关闭|闭馆|休馆|闭园|休息|休业|歇业|不营业|不开放|停业")
_ALL_DAY_RE = re.compile(r"24小时|全天(?!关闭|休息|闭馆|休馆|闭园|不开放|不营业|歇业|停业)")  # 「全天不开放」不是全天营业

CLOSED_MARK = "当日不营业"  # hours_on_date 判定当天不营业时的返回值，find_open_slot 据此返回 False


def day_span(a: str, b: str) -> List[int]:
    start, end = _WEEKDAY_CHARS[a], _WEEKDAY_CHARS[b]
    return list(range(start, end + 1)) if start <= end else list(range(start, 7)) + list(range(0, end + 1))


def _spec_days(m: re.Match) -> List[int]:
    if m.group(1):
        days: List[int] = []
        for a, b in _ITEM_RE.findall(m.group(1)):
            days += day_span(a, b) if b else [_WEEKDAY_CHARS[a]]
        return sorted(set(days))
    return [5, 6] if m.group(2) else [0, 1, 2, 3, 4]


def _resolve(body: str) -> Optional[str]:
    """一段条款正文的含义：有时刻或全天营业 → 原文；只写了关闭类字样 → CLOSED_MARK；都没有 → None（无信息）。"""
    if _TIME_RANGE_RE.search(body) or _ALL_DAY_RE.search(body) or _OPEN_WORD_RE.match(body):
        return body.strip()
    if _CLOSED_RE.search(body):
        return CLOSED_MARK
    return None


def strip_holiday_notes(text: str) -> str:
    text = _HOLIDAY_NOTE_RE.sub(lambda m: m.group(0) if "节假日" not in m.group(0) and "含" not in m.group(0) else " ", text)
    return _HOLIDAY_CLAUSE_RE.sub(" ", text)


def _weekday_part(text: str, weekday: int) -> str:
    text = strip_holiday_notes(text).strip()
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


# 只取决于两个参数、返回字符串，可安全缓存：分天穷举时同一段营业时间要按同一天解析十几万次，占分天耗时近一半
@lru_cache(maxsize=20_000)
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
            special = _weekday_part(_range_body(text, ranges, i), day.weekday())
            return special if _resolve(special) else text[:ranges[0].start()].strip() or text
        text = text[:ranges[0].start()].strip().rstrip(_OPEN_BRACKETS)
    return _weekday_part(text, day.weekday())


def _range_body(text: str, ranges: list, i: int) -> str:
    """第 i 个日期段对应的营业时间文本。两种写法：
    - 日期段在前：「10月7日-10月31日:9:30-18:00」，取到下一个日期段为止；
    - 时刻在前、日期段在其后的括号里：「06:30-18:30（2月1日-11月30日），7:00-17:30（12月1日-1月31日）」，
      取上一个括号之后、本括号之前的部分。"""
    m = ranges[i]
    before = text[:m.start()].rstrip()
    if before and before[-1] in _OPEN_BRACKETS:
        start = 0
        if i > 0:
            close = min((k for k in (text.find(c, ranges[i - 1].end()) for c in _CLOSE_BRACKETS) if k >= 0),
                        default=ranges[i - 1].end() - 1)
            start = close + 1
        return before[start:-1].strip(" ，,；;")
    end = ranges[i + 1].start() if i + 1 < len(ranges) else len(text)
    body = text[m.end():end]
    if i + 1 < len(ranges) and _RANGE_JOIN_RE.fullmatch(body):
        return _range_body(text, ranges, i + 1)
    return body
