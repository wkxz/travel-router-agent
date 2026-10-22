"""行程渲染器：输出机读的 JSON 数据结构（前端直接渲染）与人读的 Markdown 行程单。

纯标准库：这里不访问网络、不读环境变量。
"""

from typing import Dict, Any, List

from .models import FullTripPlan, ScheduleItem

PLAN_KIND = "trip_plan"  # 前端 / 历史记录据此识别工具输出里的行程数据


def trip_tips(plan: FullTripPlan) -> List[str]:
    """只给和本次行程实际相关的提示，不附带任何针对特定地区的固定文案。"""
    tips = ["**动线说明**：本行程由空间聚类与路径优化算法（最近邻 + 2-opt）计算，每日按地理组团推进，减少折返。"]

    all_items = [it for d in plan.days for it in d.items]
    if any("需现场确认" in w for it in all_items for w in it.warnings):
        tips.append("**数据核实**：标注「需现场确认」的项目缺少营业时间等数据，出发前建议电话或在地图 App 上核实。")

    museum_kw = ["博物", "科技馆", "美术馆", "纪念馆", "展览馆"]
    if any(it.category == "spot" and any(kw in it.title for kw in museum_kw) for it in all_items):
        tips.append("**场馆预约**：行程包含博物馆类场馆，这类场馆多实行实名预约，且常在周一闭馆，请提前在官方渠道核实开放时间并预约。")

    if any(it.category == "spot" and "宜早去" in w for it in all_items for w in it.warnings):
        tips.append("**早起提醒**：部分景点已安排提前出发，前一晚注意早些休息。")

    if any(it.category == "free" for it in all_items):
        tips.append("**自由活动**：行程中的空档可按体力灵活安排，想多加景点可以告诉我重新生成。")

    if any(it.category == "transit" for it in all_items):
        tips.append("**返程**：最后一天已按车次/航班时间倒推安排，预留了进站或值机缓冲；退房后如需继续游玩，可先寄存行李。")

    tips.append("**天气**：出发前再看一次实时天气预报，按温度和降水调整衣物与户外项目。")
    return tips


def _item_dict(item: ScheduleItem) -> Dict[str, Any]:
    loc = item.location
    return {
        "time_slot": item.time_slot,
        "category": item.category,
        "title": item.title,
        "description": item.description,
        "transit_info": item.transit_info,
        "cost": item.cost,
        "open_time": item.open_time,
        "rating": item.rating,
        "warnings": item.warnings,
        "location": {"name": loc.name, "lng": loc.lng, "lat": loc.lat, "poi_id": loc.poi_id} if loc else None,
    }


def render_dict(plan: FullTripPlan) -> Dict[str, Any]:
    """将 FullTripPlan 转换为纯 dict，支持直接 json.dumps；前端按此结构渲染行程卡片。"""
    notes = [n.strip() for n in plan.summary.split("\n")[1:] if n.strip()]
    return {
        "kind": PLAN_KIND,
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
        "summary": plan.summary.split("\n")[0],
        "notes": notes,
        "tips": trip_tips(plan),
        "unassigned_spots": plan.unassigned_spots,
        "unmet_must_eat": plan.unmet_must_eat,
        "total_cost": sum(d.total_cost for d in plan.days),
        "has_unknown_cost": any(d.has_unknown_cost for d in plan.days),
        "total_distance_km": round(sum(d.estimated_distance_km for d in plan.days), 1),
        "days": [
            {
                "day_number": d.day_number,
                "date": d.date,
                "theme": d.theme,
                "total_spots": d.total_spots,
                "total_cost": d.total_cost,
                "has_unknown_cost": d.has_unknown_cost,
                "estimated_distance_km": d.estimated_distance_km,
                "items": [_item_dict(item) for item in d.items],
            }
            for d in plan.days
        ],
    }


def render_markdown(plan: FullTripPlan) -> str:
    """生成具备【顶层极简打卡速查手卡 + 底层折叠深度向导】的 Markdown 文本。"""
    lines = []
    lines.append(f"# 🗺️ {plan.city} {plan.total_days} 日游定制行程指南")
    lines.append("")
    if plan.hotel:
        lines.append(f"> **建议住宿基点**：🏨 {plan.hotel.name}（以此为基点进行全城空间优化）")
    lines.append("> **规划方式**：按地理片区分天、片区内按距离排序，尽量减少折返；时间按营业时间、路程和用餐倒推，保证走得通。")
    lines.append("")

    # 顶层：每日极简打卡速查手卡（出行随性看，一眼即明）
    lines.append("### 📋 每日极简打卡速查手卡（出行随性看，一眼即明）")
    for day in plan.days:
        day_spots = [it for it in day.items if it.category == "spot"]
        morning_spots = day_spots[:1] if len(day_spots) <= 2 else day_spots[:2]
        afternoon_spots = day_spots[len(morning_spots):]

        lunch_items = [it for it in day.items if it.category == "meal" and "午餐" in it.title]
        dinner_items = [
            it for it in day.items
            if it.category in ("meal", "transit") and any(k in it.title for k in ("晚餐", "夜市", "告别餐", "返程"))
        ]

        m_str = " ➔ ".join(s.title.replace("游览【", "").replace("】", "") for s in morning_spots) or "市区休闲漫步"
        a_str = " ➔ ".join(s.title.replace("游览【", "").replace("】", "") for s in afternoon_spots) if afternoon_spots else ""
        day_flow = f"{m_str}" + (f" ➔ {a_str}" if a_str else "")

        lunch_str = lunch_items[0].title if lunch_items else "周边特色风味简餐"
        dinner_str = dinner_items[0].title if dinner_items else "地道夜市风味"

        lines.append(f"- **📅 第 {day.day_number} 天：{day.theme}**")
        lines.append(f"  * 🏛️ **主要游览**：{day_flow}")
        lines.append(f"  * 🍲 **就近用餐**：{lunch_str} ｜ {dinner_str}")

    lines.append("")
    if plan.unassigned_spots:
        lines.append(
            "⚠️ **未安排的地点**（为保证行程走得通，未强行塞入）：" + "；".join(plan.unassigned_spots)
            + "。可减少其他景点、增加天数或调整节奏后重新生成。"
        )
        lines.append("")
    if plan.unmet_must_eat:
        lines.append("⚠️ **没能安排的必吃**：" + "；".join(plan.unmet_must_eat) + "。")
        lines.append("")
    if plan.summary and "\n" in plan.summary:
        for note in plan.summary.split("\n")[1:]:
            if note.strip():
                lines.append(f"> ℹ️ {note.strip()}")
        lines.append("")

    lines.append("---")
    lines.append("")

    # 3. 底层：可折叠深度向导
    lines.append("<details>")
    lines.append("<summary><b>👇 点击展开 / 收起：每日详细时段表与出行提示</b></summary>")
    lines.append("")

    total_trip_cost = sum(d.total_cost for d in plan.days)
    total_trip_dist = sum(d.estimated_distance_km for d in plan.days)

    lines.append("### 📊 全程预估与指标概览")
    lines.append(f"- **总游玩景点**：{sum(d.total_spots for d in plan.days)} 处")
    cost_note = "；部分项目暂无价格数据，未计入" if any(d.has_unknown_cost for d in plan.days) else ""
    lines.append(f"- **门票与餐饮预估**：约 ¥{total_trip_cost:.0f} / 人（不含酒店房费与往返大交通{cost_note}）")
    lines.append(f"- **预估市内通勤里程**：约 {total_trip_dist:.1f} 公里（已通过算法消除折返）")
    lines.append("")

    category_icons = {
        "hotel": "🏨",
        "spot": "🏛️",
        "meal": "🍲",
        "snack": "🍢",
        "transit": "🚗",
        "free": "☕",
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
            if item.category in ("hotel", "transit") or (item.cost is None and item.location is None):
                cost_str = "—"
            elif item.cost is None:
                cost_str = "暂无数据"
            else:
                cost_str = f"¥{item.cost:.0f}" if item.cost > 0 else "免费"
            warn_str = "；".join(item.warnings) if item.warnings else "正常"
            if item.open_time:
                warn_str += f"<br><sub>({item.open_time})</sub>"

            desc_clean = item.description.replace("\n", " ")
            lines.append(
                f"| {item.time_slot} | {icon} {item.title} | {transit} | {desc_clean} | {cost_str} | {warn_str} |"
            )

        lines.append("")

    lines.append("---")
    lines.append("### 💡 出行提示")
    tips = trip_tips(plan)
    for i, tip in enumerate(tips, 1):
        lines.append(f"{i}. {tip}")
    lines.append("")
    lines.append("</details>")

    return "\n".join(lines)
