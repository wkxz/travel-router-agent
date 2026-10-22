"""高德 Web 服务客户端：项目里唯一直接访问高德的地方。

- Key 只从环境变量 AMAP_API_KEY 读取，绝不硬编码、绝不打印；
- 全局节流 + 10021 限流退避重试；
- 失败统一返回 {"error": "..."}，让模型能如实解释原因。
"""

import os
import threading
import time
from typing import Any, Optional, Tuple

import httpx

AMAP_BASE = "https://restapi.amap.com/v3"
TIMEOUT = 10.0

MIN_INTERVAL = 0.35
_qps_lock = threading.Lock()
_last_call_at = 0.0

QPS_EXCEEDED = "10021"


def _throttle() -> None:
    """全局最小请求间隔，避免高德并发超限。"""
    global _last_call_at
    with _qps_lock:
        gap = MIN_INTERVAL - (time.monotonic() - _last_call_at)
        if gap > 0:
            time.sleep(gap)
        _last_call_at = time.monotonic()


def amap_get(path: str, params: dict, retries: int = 3) -> dict:
    """统一高德 GET 请求：注入 Key、节流、QPS 限流退避重试、status 校验。"""
    key = os.getenv("AMAP_API_KEY")
    if not key:
        return {"error": "AMAP_API_KEY 未配置：请在 .env 中填写高德 Web 服务 Key"}

    last_error = ""
    for attempt in range(retries):
        _throttle()
        try:
            resp = httpx.get(
                f"{AMAP_BASE}{path}",
                params={"key": key, **params},
                timeout=TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as e:
            last_error = f"高德接口网络请求失败：{type(e).__name__}: {e}"
            time.sleep(0.6 * (attempt + 1))
            continue
        except ValueError:
            return {"error": "高德接口返回了非 JSON 内容，请稍后重试"}

        if str(data.get("status")) == "1":
            return data

        info = f"{data.get('info')}（infocode={data.get('infocode')}）"
        if str(data.get("infocode")) == QPS_EXCEEDED and attempt < retries - 1:
            last_error = f"高德接口限流，已重试 {attempt + 1} 次：{info}"
            time.sleep(1.0 * (attempt + 1))
            continue
        return {"error": f"高德接口返回错误：{info}"}

    return {"error": last_error or "高德接口连续重试失败，请稍后重试"}


def valid_coords(lng: float, lat: float) -> bool:
    return -180 <= lng <= 180 and -90 <= lat <= 90 and not (lng == 0 and lat == 0)


def parse_location(loc_str: Any) -> Optional[Tuple[float, float]]:
    """解析经纬度字符串 'lng,lat' 为 (lng, lat)；格式错误或为 (0,0) 时返回 None。"""
    try:
        parts = str(loc_str).split(",")
        lng, lat = float(parts[0]), float(parts[1])
    except (ValueError, IndexError):
        return None
    return (lng, lat) if valid_coords(lng, lat) else None


def to_float(value: Any) -> Optional[float]:
    """高德 biz_ext 缺数据时会返回 [] 或空串，这里统一转成 None，不编造默认值。"""
    if value is None or isinstance(value, (list, dict)):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def to_text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def poi_open_time(biz: dict) -> str:
    """营业时间优先取 opentime2：它带星期信息（如「周一 闭馆；周二至周日 09:00-17:00开放」），
    open_time 只有时刻（实测南京博物院 open_time="09:00-17:00"），用它会丢掉闭馆日。"""
    return to_text(biz.get("opentime2")) or to_text(biz.get("open_time"))
