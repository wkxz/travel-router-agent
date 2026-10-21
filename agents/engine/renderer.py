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
    lines.append("### 💡 出行贴心建议")
    lines.append("1. **动线说明**：本行程由路径优化算法（2-opt）计算，每日游玩顺序单向推进，避免东西往返折返。")
    lines.append("2. **餐饮选择**：推荐餐厅均为临近景点的口碑老字号/高分餐厅，且已预先核验营业时段。高峰期可能需要排队，建议提前 15 分钟在线取号。")
    lines.append("3. **门票预约**：博物馆类、历史古迹类景点通常实行实名预约制，请务必提前 1~7 天在官方公众号核实并提前预订。")
    lines.append("")

    return "\n".join(lines)
