"""地理计算与时间换算工具函数。

不依赖外部库（如 geopy/shapely），使用标准 math 模块实现。
"""

import math
import re
from typing import Tuple, Optional


def haversine_distance(lng1: float, lat1: float, lng2: float, lat2: float) -> float:
    """计算两点经纬度的大圆球面距离，单位：千米 (km)。"""
    R = 6371.0088  # 地球平均半径(km)

    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lng2 - lng1)

    a = (
        math.sin(delta_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * (math.sin(delta_lambda / 2.0) ** 2)
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


def estimate_transit(
    dist_km: float, mode: str = "auto"
) -> Tuple[int, str]:
    """根据直线距离估算通行方式、通行耗时（分钟）和描述文本。

    mode: "walking" | "driving" | "auto"
    - 直线距离转实际道路距离通常有约 1.25~1.35 的折线系数。
    """
    road_km = dist_km * 1.3  # 折线估算系数

    if mode == "walking" or (mode == "auto" and road_km <= 1.2):
        # 步行模式（约 4.5 km/h = 13.3 分钟/km）
        minutes = max(5, int(math.ceil(road_km * 13.3)))
        return minutes, f"步行约 {minutes} 分钟 ({road_km:.1f} km)"
    else:
        # 市内打车/驾车模式（平均时速约 25 km/h + 红绿灯起步等候 5 分钟）
        minutes = max(8, int(math.ceil(road_km / 25.0 * 60 + 5)))
        return minutes, f"打车/驾车约 {minutes} 分钟 ({road_km:.1f} km)"


def parse_time_to_minutes(hhmm: str) -> Optional[int]:
    """把 '09:30' 字符串转换成从当日 00:00 开始的分钟数。若跨天如 '02:00' 可用于后续对比。"""
    match = re.match(r"^(\d{1,2}):(\d{2})$", hhmm.strip())
    if not match:
        return None
    h, m = int(match.group(1)), int(match.group(2))
    return h * 60 + m


def minutes_to_hhmm(minutes: int) -> str:
    """把从 00:00 开始的分钟数转换成 '09:30' 格式。"""
    normalized = minutes % (24 * 60)
    h = normalized // 60
    m = normalized % 60
    return f"{h:02d}:{m:02d}"


def is_time_in_range(target_hhmm: str, open_time_str: str) -> Tuple[bool, str]:
    """检查特定时间（如 '19:30'）是否落在 open_time 字符串描述的营业区间内。

    支持格式：
    - '09:30-22:00'
    - '09:30-02:00'（跨零点）
    - '07:00-09:00 10:00-22:00'（多段营业）
    若无法解析或未指定，返回 (True, "营业时间需现场确认")。
    """
    if not open_time_str or not open_time_str.strip():
        return True, "营业时间未知"

    target_min = parse_time_to_minutes(target_hhmm)
    if target_min is None:
        return True, "时间格式未知"

    # 提取所有 "HH:MM-HH:MM" 区间
    intervals = re.findall(r"(\d{1,2}:\d{2})\s*[-–~]\s*(\d{1,2}:\d{2})", open_time_str)
    if not intervals:
        return True, "营业时间格式特殊，建议现场核对"

    for start_s, end_s in intervals:
        start_m = parse_time_to_minutes(start_s)
        end_m = parse_time_to_minutes(end_s)
        if start_m is None or end_m is None:
            continue

        if start_m <= end_m:
            # 正常不跨日
            if start_m <= target_min <= end_m:
                return True, f"在营业时间内 ({start_s}-{end_s})"
        else:
            # 跨日营业，如 09:30 - 02:00 (end_m=120)
            if target_min >= start_m or target_min <= end_m:
                return True, f"在夜间/跨日营业时间内 ({start_s}-{end_s})"

    return False, f"可能未营业（营业时间：{open_time_str}）"
