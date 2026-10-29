"""离线测试用的高德替身：字段格式取自 2026-10-22 的实测响应（含 opentime2 原文），不发网络请求。"""

MONDAY = "2026-10-26"

SPOTS = {
    "B0MUSEUM": {
        "id": "B0MUSEUM", "name": "南京博物院", "location": "118.823200,32.041000",
        "type": "科教文化服务;博物馆;博物馆", "address": "中山东路321号",
        "biz_ext": {"rating": "4.9", "cost": [], "open_time": "09:00-17:00",
                    "opentime2": "周一 闭馆(法定节假日除外)；周二至周日 09:00-17:00开放(16:00停止入园)"},
    },
    "B0LAKE": {
        "id": "B0LAKE", "name": "玄武湖景区", "location": "118.796600,32.070000",
        "type": "风景名胜;公园广场;公园", "address": "玄武巷1号",
        "biz_ext": {"rating": "4.7", "cost": [], "open_time": "", "opentime2": "周一至周日 00:00-24:00"},
    },
}
# 不在景点类目（110000|140000）里的地点：只有不限类目检索才返回（实测先锋书店是 061205 书店）
OTHERS = {
    "B0BOOKS": {
        "id": "B0BOOKS", "name": "先锋书店(五台山总店)", "location": "118.770300,32.052500",
        "type": "购物服务;专卖店;书店", "address": "广州路173号",
        "biz_ext": {"rating": "4.8", "cost": [], "open_time": "10:00-22:00", "opentime2": "周一至周日 10:00-22:00"},
    },
    "B0BOOKS2": {
        "id": "B0BOOKS2", "name": "先锋书店(玄武湖分店)", "location": "118.793003,32.074346",
        "type": "购物服务;专卖店;书店", "address": "玄武湖内",
        "biz_ext": {"rating": "4.7", "cost": [], "open_time": "10:00-21:00", "opentime2": "周一至周日 10:00-21:00"},
    },
}
FOODS = {
    "B0DUCK": {
        "id": "B0DUCK", "name": "回味鸭血粉丝汤(中山东路店)", "location": "118.821500,32.042500",
        "type": "餐饮服务;中餐厅;特色/地方风味餐厅", "address": "中山东路300号",
        "biz_ext": {"rating": "4.5", "cost": "35.00", "open_time": "10:00-21:00", "opentime2": []},
    },
    "B0SNACK": {
        "id": "B0SNACK", "name": "梅花糕小吃", "location": "118.797000,32.068500",
        "type": "餐饮服务;快餐厅;快餐厅", "address": "玄武湖菱洲",
        "biz_ext": {"rating": [], "cost": "12.00", "open_time": "", "opentime2": "周一至周日 09:00-20:00"},
    },
}
# 仿 v5 /place/detail?show_fields=children：取自 2026-10-29 玄武湖景区实测响应（节选）
CHILDREN = {
    "B0LAKE": [
        {"name": "玄武湖景区主游客中心", "typecode": "070201|200300", "location": "118.812702,32.066116"},
        {"name": "玄武湖景区(玄武门)", "subtype": "门", "typecode": "991401", "location": "118.787506,32.070519"},
        {"name": "玄武湖景区(解放门)", "subtype": "门", "typecode": "991400", "location": "118.796638,32.062228"},
        {"name": "玄武湖公园内部停车场", "subtype": "停车场", "typecode": "150905", "location": "118.806768,32.075273"},
    ],
}
PLACES = {
    "新街口": "118.784400,32.041600",
    "南京南站": "118.798300,31.969600",
}


def _fake_distance(params: dict) -> dict:
    """仿 /v3/distance：路网距离按直线 ×1.4，步行 1.25 m/s、驾车 7 m/s；与真实接口一样最多处理 100 个起点。"""
    import math

    def parse(s):
        lng, lat = (float(x) for x in s.split(","))
        return lng, lat

    dlng, dlat = parse(params["destination"])
    speed = 1.25 if params.get("type") == "3" else 7.0
    results = []
    for i, o in enumerate(params["origins"].split("|")[:100], start=1):
        olng, olat = parse(o)
        dx = (olng - dlng) * 111320 * math.cos(math.radians(dlat))
        dy = (olat - dlat) * 110540
        road = math.hypot(dx, dy) * 1.4
        results.append({"origin_id": str(i), "dest_id": "1", "distance": f"{road:.0f}", "duration": f"{road / speed:.0f}"})
    return {"status": "1", "results": results}


class FakeAmap:
    """可调用对象，签名与 amap_get(path, params) 相同；calls 记录每次请求。"""

    def __init__(self):
        self.calls = []

    def __call__(self, path: str, params: dict) -> dict:
        self.calls.append((path, dict(params)))
        if path in ("/place/text", "/place/around"):
            types = params.get("types")
            pool = FOODS if types == "050000" else SPOTS if types else {**SPOTS, **OTHERS}
            kw = params.get("keywords", "")
            pois = [p for p in pool.values() if kw in p["name"] or kw in p["type"]] or list(pool.values())
            return {"status": "1", "pois": pois}
        if path == "/place/detail":
            p = {**SPOTS, **OTHERS, **FOODS}.get(params["id"])
            return {"status": "1", "pois": [p] if p else []}
        if path == "/v5/place/detail":
            p = {**SPOTS, **OTHERS, **FOODS}.get(params["id"])
            return {"status": "1", "pois": [{**p, "children": CHILDREN.get(p["id"], [])}] if p else []}
        if path == "/distance":
            return _fake_distance(params)
        if path == "/geocode/geo":
            loc = PLACES.get(params["address"])
            if not loc:
                return {"status": "1", "geocodes": []}
            return {"status": "1", "geocodes": [{"location": loc, "formatted_address": f"南京市{params['address']}"}]}
        return {"error": f"FakeAmap 未模拟 {path}"}

    def paths(self):
        return [p for p, _ in self.calls]
