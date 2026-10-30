"""高德 Web 服务客户端：项目里唯一直接访问高德的地方。

- Key 只从环境变量 AMAP_API_KEY 读取，绝不硬编码、绝不打印；
- 全局节流 + QPS 限流（10019~10022）退避重试；
- 额度用完（10044 等）时熔断，熔断期内不再发请求；
- 录制回放（AMAP_CACHE=record / replay，默认关闭）：测评与调试离线重放，不耗额度、结果可复现；
- 失败统一返回 {"error": "..."}，让模型能如实解释原因。
"""

import json
import os
import sqlite3
import threading
import time
from typing import Any, Optional, Tuple

import httpx

AMAP_HOST = "https://restapi.amap.com"
AMAP_BASE = f"{AMAP_HOST}/v3"
TIMEOUT = 10.0

MIN_INTERVAL = 0.35
_qps_lock = threading.Lock()
_last_call_at = 0.0

# QPS 超限类错误码：都按限流退避重试，且与「查不到」区分开。
# 2026-10-23 测评实测出现 10022（info=CGQPS_HAS_EXCEEDED_THE_LIMIT），旧代码只认 10021，直接当失败返回给了模型。
QPS_CODES = {"10019", "10020", "10021", "10022"}


# 额度用完：2026-10-30 月免费额度用完后实测 10044（info=USER_DAILY_QUERY_OVER_LIMIT，名称是 DAILY、实际按月）。
# 10003（DAILY_QUERY_OVER_LIMIT）、10045（USER_ABROAD_DAILY_QUERY_OVER_LIMIT）按官方错误码表一并处理，未实测。
# 熔断 QUOTA_COOLDOWN 秒后放一个请求探测，充值或到了下个月能自动恢复，不用重启服务。
QUOTA_CODES = {"10003", "10044", "10045"}
QUOTA_COOLDOWN = 600
_quota_blocked_until = 0.0
_quota_error = ""


def is_qps_error(data: dict) -> bool:
    return str(data.get("infocode")) in QPS_CODES or "QPS_HAS_EXCEEDED" in str(data.get("info"))


SECRET_ENV_VARS = ("AMAP_API_KEY", "LLM_API_KEY", "AI_GATEWAY_API_KEY", "REVIEW_API_KEY")  # REVIEW_：eval/review.py 的点评模型


def redact_secrets(text: str) -> str:
    """把错误文本里出现的密钥值替换成 ***。所有要返回给模型或写进日志的错误文本都先过这一步。"""
    for name in SECRET_ENV_VARS:
        value = os.getenv(name)
        if value and len(value) >= 6:
            text = text.replace(value, "***")
    return text


# ---------- 录制回放 ----------
# record：照常请求，成功的返回按「接口 + 参数」存进 SQLite；replay：只读缓存、不联网，未命中直接报错。
# 只给测评和调试用，线上默认关闭：录下来的驾车时间是录制那一刻的路况，营业时间、店铺也会变。
DEFAULT_CACHE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                                  "data", "amap-cache.db")
_cache_lock = threading.Lock()
_cache_conn: Optional[sqlite3.Connection] = None
_cache_conn_path = ""


def cache_mode() -> str:
    mode = (os.getenv("AMAP_CACHE") or "").strip().lower()
    return mode if mode in ("record", "replay") else ""


def _cache_key(path: str, params: dict) -> str:
    return path + "?" + json.dumps(params, ensure_ascii=False, sort_keys=True, default=str)


def _cache_db() -> sqlite3.Connection:
    """调用方持有 _cache_lock。AMAP_CACHE_PATH 变了（测试里）就重新打开。"""
    global _cache_conn, _cache_conn_path
    path = os.getenv("AMAP_CACHE_PATH") or DEFAULT_CACHE_PATH
    if _cache_conn is None or _cache_conn_path != path:
        if _cache_conn is not None:
            _cache_conn.close()
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        _cache_conn = sqlite3.connect(path, check_same_thread=False)
        _cache_conn.execute("CREATE TABLE IF NOT EXISTS responses (key TEXT PRIMARY KEY, body TEXT NOT NULL, "
                            "recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
        _cache_conn_path = path
    return _cache_conn


def _cache_get(key: str) -> Optional[dict]:
    with _cache_lock:
        row = _cache_db().execute("SELECT body FROM responses WHERE key = ?", (key,)).fetchone()
    return json.loads(row[0]) if row else None


def _cache_put(key: str, data: dict) -> None:
    with _cache_lock:
        db = _cache_db()
        db.execute("INSERT OR REPLACE INTO responses (key, body) VALUES (?, ?)",
                   (key, json.dumps(data, ensure_ascii=False)))
        db.commit()


def close_cache() -> None:
    global _cache_conn, _cache_conn_path
    with _cache_lock:
        if _cache_conn is not None:
            _cache_conn.close()
        _cache_conn, _cache_conn_path = None, ""


def _throttle() -> None:
    """全局最小请求间隔，避免高德并发超限。"""
    global _last_call_at
    with _qps_lock:
        gap = MIN_INTERVAL - (time.monotonic() - _last_call_at)
        if gap > 0:
            time.sleep(gap)
        _last_call_at = time.monotonic()


def amap_get(path: str, params: dict, retries: int = 3) -> dict:
    """统一高德 GET 请求：注入 Key、节流、QPS 限流退避重试、status 校验。

    path 默认指 v3 接口（如 "/place/text"）；以 "/v5/" 开头时请求 v5 接口（如 "/v5/place/detail"）。"""
    mode = cache_mode()
    if mode:
        key = _cache_key(path, params)
        if mode == "replay":
            cached = _cache_get(key)
            return cached if cached is not None else {"error": f"回放缓存未命中（AMAP_CACHE=replay 不联网）：{key}"}
        data = _request(path, params, retries)
        if "error" not in data:
            _cache_put(key, data)
        return data
    return _request(path, params, retries)


def _request(path: str, params: dict, retries: int) -> dict:
    key = os.getenv("AMAP_API_KEY")
    if not key:
        return {"error": "AMAP_API_KEY 未配置：请在 .env 中填写高德 Web 服务 Key"}

    global _quota_blocked_until, _quota_error
    if time.monotonic() < _quota_blocked_until:
        return {"error": _quota_error}

    last_error = ""
    for attempt in range(retries):
        _throttle()
        try:
            resp = httpx.get(
                f"{AMAP_HOST}{path}" if path.startswith("/v5/") else f"{AMAP_BASE}{path}",
                params={"key": key, **params},
                timeout=TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as e:
            # 不能带 str(e)：httpx 的异常文本含完整 URL，URL 里有 key=AMAP_API_KEY（实测）
            last_error = f"高德接口网络请求失败：HTTP {e.response.status_code}"
            time.sleep(0.6 * (attempt + 1))
            continue
        except httpx.HTTPError as e:
            last_error = redact_secrets(f"高德接口网络请求失败：{type(e).__name__}: {e}")
            time.sleep(0.6 * (attempt + 1))
            continue
        except ValueError:
            return {"error": "高德接口返回了非 JSON 内容，请稍后重试"}

        if str(data.get("status")) == "1":
            return data

        info = f"{data.get('info')}（infocode={data.get('infocode')}）"
        if str(data.get("infocode")) in QUOTA_CODES:
            _quota_error = (f"高德接口调用额度已用完：{info}。这不代表查不到，之后的检索都会失败，不要换关键词重试；"
                            "如实告诉用户地图服务额度已用完、请稍后再试。")
            _quota_blocked_until = time.monotonic() + QUOTA_COOLDOWN
            return {"error": _quota_error}
        if is_qps_error(data):
            last_error = f"高德接口限流（请求过于频繁，不代表查不到），已重试 {attempt + 1} 次：{info}"
            if attempt < retries - 1:
                time.sleep(1.0 * (attempt + 1))
                continue
            return {"error": last_error}
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
