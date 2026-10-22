"""地理计算与时间换算工具函数。

不依赖外部库（如 geopy/shapely），使用标准 math 模块实现。
"""

from __future__ import annotations
import math
import re
from typing import Tuple, Optional, Any


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


SAME_AREA_KM = 4.0  # 簇重心相距 4km 以内视为同一片区，优先排在同一天
WALKING_MAX_ROAD_KM = 2.0  # 道路距离 2km 以内按步行（约 25 分钟），超过才建议打车


def estimate_transit(
    dist_km: float, mode: str = "auto"
) -> Tuple[int, str]:
    """根据直线距离估算通行方式、通行耗时（分钟）和描述文本。

    mode: "walking" | "driving" | "auto"
    - 直线距离转实际道路距离通常有约 1.25~1.35 的折线系数。
    """
    road_km = dist_km * 1.3  # 折线估算系数

    if mode == "walking" or (mode == "auto" and road_km <= WALKING_MAX_ROAD_KM):
        # 步行模式（约 4.5 km/h = 13.3 分钟/km）
        minutes = max(5, int(math.ceil(road_km * 13.3)))
        return minutes, f"步行约 {minutes} 分钟 ({road_km:.1f} km)"
    else:
        # 打车/驾车：前 10km 按市区路况约 25 km/h，超出部分按快速路约 45 km/h，另加 5 分钟起步等候
        urban_km = min(road_km, 10.0)
        fast_km = max(road_km - 10.0, 0.0)
        minutes = max(8, int(math.ceil(urban_km / 25.0 * 60 + fast_km / 45.0 * 60 + 5)))
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


_ALL_DAY_MARKERS = ("24小时", "全天", "00:00-24:00", "00:00-23:59")


def _open_intervals(open_time_str: str) -> list:
    """把营业时间字符串解析成 [(开始分钟, 结束分钟)]，跨零点的结束时间 +1440。"""
    result = []
    for start_s, end_s in re.findall(r"(\d{1,2}:\d{2})\s*[-–~]\s*(\d{1,2}:\d{2})", open_time_str):
        start_m, end_m = parse_time_to_minutes(start_s), parse_time_to_minutes(end_s)
        if start_m is None or end_m is None:
            continue
        if end_m <= start_m:
            end_m += 24 * 60
        result.append((start_m, end_m))
    return result


def first_open_minute(open_time_str: str) -> Optional[int]:
    """营业时间里最早的开门时刻（分钟）；全天开放或无法解析时返回 None。"""
    intervals = _open_intervals((open_time_str or "").strip())
    return min(start for start, _ in intervals) if intervals else None


def find_open_slot(
    earliest_start: int, duration: int, open_time_str: str, max_wait: int = 90
) -> Tuple[Optional[bool], int, str]:
    """在营业时间内为一段 duration 分钟的活动找开始时刻。

    返回 (状态, 开始分钟, 说明)：
    - True：可行（开门前到达时允许等待至多 max_wait 分钟）
    - False：当天在营业时间内放不下
    - None：营业时间缺失或无法解析，按 earliest_start 排并要求现场确认
    """
    text = (open_time_str or "").strip()
    if not text:
        return None, earliest_start, "营业时间暂无数据，需现场确认"
    if any(m in text for m in _ALL_DAY_MARKERS):
        return True, earliest_start, "全天开放"
    intervals = _open_intervals(text)
    if not intervals:
        return None, earliest_start, f"营业时间格式特殊（{text}），需现场确认"

    for start_m, end_m in sorted(intervals):
        # 同时尝试把当天时刻平移一天，覆盖凌晨落在跨零点区间里的情况
        for shift in (0, 24 * 60):
            begin = max(earliest_start + shift, start_m)
            if begin - (earliest_start + shift) <= max_wait and begin + duration <= end_m:
                return True, begin - shift, f"营业时间 {text}"
    return False, earliest_start, f"营业时间 {text}，该时段放不下 {duration} 分钟的游览/用餐"


def check_open_window(start_min: int, end_min: int, open_time_str: str) -> Tuple[Optional[bool], str]:
    """检查 [start_min, end_min] 是否整段落在营业时间内（不允许等待）。

    返回 True / False / None（营业时间未知）。与 is_time_in_range 不同，这里同时校验结束时刻。
    """
    status, begin, msg = find_open_slot(start_min, end_min - start_min, open_time_str, max_wait=0)
    if status and begin != start_min:
        return False, msg
    return status, msg


def cluster_spots_spatially(
    hotel: LocationPoint,
    spots: list,
    num_clusters: int,
    max_cluster_minutes: Optional[int] = None,
) -> list:
    """基于大圆球面距离的轻量级层次空间聚类算法（纯标准库实现）。

    解决痛点：将 M 个景点聚类为 N 天（每天一个空间紧密簇），
    彻底取代人工硬编码写死「片区路线」，适用于全国任意城市。
    """
    if not spots:
        return [[] for _ in range(num_clusters)]
    if num_clusters <= 1:
        return [list(spots)]

    max_cluster_size = math.ceil(len(spots) / num_clusters) + 1

    # 初始化：各景点自成一簇
    clusters = [[s] for s in spots]

    def _cluster_centroid(cl: list) -> Tuple[float, float]:
        avg_lng = sum(s.lng for s in cl) / len(cl)
        avg_lat = sum(s.lat for s in cl) / len(cl)
        return avg_lng, avg_lat

    def _cluster_distance(c1: list, c2: list) -> float:
        lng1, lat1 = _cluster_centroid(c1)
        lng2, lat2 = _cluster_centroid(c2)
        return haversine_distance(lng1, lat1, lng2, lat2)

    # 层次凝聚合并，直到簇数量缩减至 num_clusters
    while len(clusters) > num_clusters:
        best_pair = None
        best_cost = float("inf")
        for i in range(len(clusters)):
            for j in range(i + 1, len(clusters)):
                combined_size = len(clusters[i]) + len(clusters[j])
                # 容量平衡惩罚项，避免把所有点聚在一个簇内
                size_penalty = 5.0 * max(0, combined_size - max_cluster_size)
                d = _cluster_distance(clusters[i], clusters[j]) + size_penalty
                if d < best_cost:
                    best_cost = d
                    best_pair = (i, j)

        if best_pair is None:
            break
        i, j = best_pair
        clusters[i].extend(clusters[j])
        clusters.pop(j)

    # 同片区合并：即使簇数已经不超过天数（例如景点数 ≤ 天数），
    # 相距 SAME_AREA_KM 以内的簇只要容量允许也合到同一天，宁可空出一天，也不拆散邻近景点
    while True:
        best_pair, best_d = None, SAME_AREA_KM
        for i in range(len(clusters)):
            for j in range(i + 1, len(clusters)):
                if len(clusters[i]) + len(clusters[j]) > max_cluster_size:
                    continue
                # 合并后一天游不完（游玩时长超过单日预算的 85%，留出通勤）就不合并
                minutes = sum(getattr(s, "duration_minutes", 0) for s in clusters[i] + clusters[j])
                if max_cluster_minutes is not None and minutes > max_cluster_minutes * 0.85:
                    continue
                d = _cluster_distance(clusters[i], clusters[j])
                if d <= best_d:
                    best_pair, best_d = (i, j), d
        if best_pair is None:
            break
        i, j = best_pair
        clusters[i].extend(clusters[j])
        clusters.pop(j)

    # 将聚好的簇按距酒店（或前一簇重心）由近及远贪心排序，确保天数动线连贯
    ordered_clusters = []
    curr_lng, curr_lat = hotel.lng, hotel.lat
    while clusters:
        best_idx = 0
        best_dist = float("inf")
        for idx, cl in enumerate(clusters):
            c_lng, c_lat = _cluster_centroid(cl)
            d = haversine_distance(curr_lng, curr_lat, c_lng, c_lat)
            if d < best_dist:
                best_dist = d
                best_idx = idx
        chosen = clusters.pop(best_idx)
        ordered_clusters.append(chosen)
        curr_lng, curr_lat = _cluster_centroid(chosen)

    while len(ordered_clusters) < num_clusters:
        ordered_clusters.append([])

    return ordered_clusters
