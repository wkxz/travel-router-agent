"""行程渲染器：输出人读的 Markdown 行程单与机读的 JSON 数据结构。
"""

from typing import Dict, Any
from .models import FullTripPlan, DayItinerary, ScheduleItem


def render_dict(plan: FullTripPlan) -> Dict[str, Any]:
    """将 FullTripPlan 转换为纯 dict，支持直接 json.dumps。"""
    return {
        "city": plan.city,
        "total_days": plan.total_days,
        "hotel": {
            "name": plan.hotel.name,
            "lng": plan.hotel.lng,
            "lat": plan.hotel.lat,
            "address": plan.hotel.address,
        }
        if plan.hotel
        else None,
        "summary": plan.summary,
        "unassigned_spots": plan.unassigned_spots,
        "days": [
            {
                "day_number": d.day_number,
                "theme": d.theme,
                "total_spots": d.total_spots,
                "total_cost": d.total_cost,
                "estimated_distance_km": d.estimated_distance_km,
                "items": [
                    {
                        "time_slot": item.time_slot,
                        "category": item.category,
                        "title": item.title,
                        "description": item.description,
                        "transit_info": item.transit_info,
                        "cost": item.cost,
                        "open_time": item.open_time,
                        "rating": item.rating,
                        "warnings": item.warnings,
                        "location": {
                            "name": item.location.name,
                            "lng": item.location.lng,
                            "lat": item.location.lat,
                        }
                        if item.location
                        else None,
                    }
                    for item in d.items
                ],
            }
            for d in plan.days
        ],
    }


def render_markdown(plan: FullTripPlan) -> str:
    """生成排版精美、可直接用于用户阅读或粘贴到飞书的 Markdown 文本。"""
    lines = []
    lines.append(f"# 🗺️ {plan.city} {plan.total_days} 日游定制行程指南")
    lines.append("")
    lines.append(f"> **行程概览**：{plan.summary}")
    if plan.hotel:
        lines.append(f"> **建议住宿**：{plan.hotel.name}（动线以该处为起终点进行优化）")
    lines.append("")

    if plan.unassigned_spots:
        lines.append(
            f"⚠️ **行程容量提醒**：根据每天舒适游玩的节奏，以下地点时间稍紧未强行塞入：`{'、'.join(plan.unassigned_spots)}`，建议根据实际体力或后续增订行程。"
        )
        lines.append("")

    total_trip_cost = sum(d.total_cost for d in plan.days)
    total_trip_dist = sum(d.estimated_distance_km for d in plan.days)

    lines.append("### 📊 全程预估")
    lines.append(f"- **总游玩景点**：{sum(d.total_spots for d in plan.days)} 处")
    lines.append(f"- **门票与餐饮预估**：约 ¥{total_trip_cost:.0f} / 人（不含酒店交通大交通）")
    lines.append(f"- **预估市内通勤里程**：约 {total_trip_dist:.1f} 公里（已通过算法优化消除折返）")
    lines.append("")
    lines.append("---")
    lines.append("")

    category_icons = {
        "hotel": "🏨",
        "spot": "🏛️",
        "meal": "🍲",
        "snack": "🍢",
        "transit": "🚗",
    }

    for day in plan.days:
        lines.append(f"## 📅 第 {day.day_number} 天：{day.theme}")
        lines.append(
            f"**当日指标**：{day.total_spots} 个主要景点 ｜ 预计活动花费 ¥{day.total_cost:.0f} ｜ 市内移动约 {day.estimated_distance_km} km"
        )
        lines.append("")
        lines.append("| 时段 | 项目与地点 | 交通方式 / 耗时 | 游览与美食详情 | 人均/参考 | 提示 |")
        lines.append("|---|---|---|---|---|---|")

        for item in day.items:
            icon = category_icons.get(item.category, "📍")
            transit = item.transit_info or "—"
            cost_str = f"¥{item.cost:.0f}" if item.cost > 0 else "免费/已含"
            warn_str = "；".join(item.warnings) if item.warnings else "正常"
            if item.open_time:
                warn_str += f"<br><sub>({item.open_time})</sub>"

            # 过滤换行
            desc_clean = item.description.replace("\n", " ")
            lines.append(
                f"| {item.time_slot} | {icon} {item.title} | {transit} | {desc_clean} | {cost_str} | {warn_str} |"
            )

        lines.append("")

    lines.append("---")
    lines.append("### 💡 出行贴心建议与向导提示")
    lines.append("1. **动线与节奏说明**：本行程由空间层次聚类与路径优化算法（2-opt）计算，每日按地理组团单向推进，消除折返弯路。若单日安排了多个同质大型景区（如双古村/双古镇），体力一般者建议二选一慢逛，避免审美疲劳。")
    lines.append("2. **餐饮选择与分量**：推荐餐厅兼顾特色名店与便民街铺，且已避免连续推荐重复重油大菜。水席或大盘菜分量偏大，1~2 人出游建议单点 1~2 道招牌尝鲜，不宜点整套大宴；夜市分为红灯笼打卡街（重氛围）与老街坊街铺（重性价比）。")

    # 动态识别文博重点大馆，追加通用预约防呆协议
    has_museum = any(
        any(kw in item.title for kw in ["博物", "科技馆", "美术馆", "纪念馆", "遗址", "寺", "殿", "窟"])
        for d in plan.days for item in d.items if item.category == "spot"
    )
    if has_museum:
        lines.append("3. **重点场馆预约与门票**：行程中包含国家级/重点文博或历史名胜，现多实行实名预约制且部分场馆逢周一闭馆。放票周期多为提前 3~7 天，请务必提前在官方公众号核实放票时间并准时预约；部分景区有登楼/演艺套票或多景点官方联票，建议按需选购防踩坑；古刹寺庙请注意着装得体。")

    # 动态识别返程枢纽，追加防误车缓冲与行李协议
    has_departure = any(
        item.category == "transit" and "返程" in item.title
        for d in plan.days for item in d.items
    )
    if has_departure:
        lines.append("4. **返程接驳与行李寄存**：最后一天日程终点已贴心优化为直达返程枢纽，建议预留至少 60~90 分钟以上的进站安检或值机托运缓冲时间；退房后跨区县游玩时，建议将大件行李寄存于景区游客中心，轻装漫步。")

    lines.append("5. **天气、演艺与时令**：请结合出行实时天气增减衣物，雨雪天气山地与古城青石板路注意防滑防跌；大型夜间灯光秀或露天演艺多限周五至周日或节假日开放（遇雨易取消），非周末可优先选择外围免费机位打卡。")
    lines.append("")

    return "\n".join(lines)
