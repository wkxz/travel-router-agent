import { Fragment, memo, useState, type CSSProperties } from 'react';
import type { PlanItem, TripPlan } from '../types';
import styles from './TripPlanCard.module.css';

const DAY_COLORS = ['#0d9488', '#2563eb', '#d97706', '#9333ea', '#dc2626', '#0891b2', '#65a30d'];
const dayColor = (dayIndex: number) => DAY_COLORS[dayIndex % DAY_COLORS.length];

const CATEGORY_ICONS: Record<PlanItem['category'], string> = {
  hotel: '🏨',
  spot: '🏛️',
  meal: '🍲',
  snack: '🍢',
  transit: '🚗',
  free: '☕',
};

/** 提示文案里只有 **加粗** 一种标记，这里就地渲染，不引入 Markdown 解析。 */
function renderBold(text: string) {
  return text.split('**').map((part, i) => (i % 2 ? <strong key={i}>{part}</strong> : <Fragment key={i}>{part}</Fragment>));
}

function formatCost(item: PlanItem): string | null {
  if (item.category === 'hotel' || item.category === 'transit' || item.category === 'free') return null;
  if (item.cost === null) return item.location ? '价格暂无数据' : null;
  return item.cost > 0 ? `¥${item.cost.toFixed(0)}` : '免费';
}

function weekday(date: string): string {
  const d = new Date(`${date}T00:00:00`);
  return Number.isNaN(d.getTime()) ? '' : '周' + '日一二三四五六'[d.getDay()];
}

/**
 * 行程卡片：数据直接来自排程引擎（SSE plan 事件 / 历史记录），不经模型转述。
 * 按天切换时间线。
 */
export default memo(function TripPlanCard({ plan }: { plan: TripPlan }) {
  const [activeDay, setActiveDay] = useState(0);
  const day = plan.days[activeDay] ?? plan.days[0];
  const totalSpots = plan.days.reduce((s, d) => s + d.total_spots, 0);

  return (
    <section className={styles.card} aria-label={`${plan.city} ${plan.total_days} 天行程`}>
      <header className={styles.header}>
        <h3 className={styles.title}>🗺️ {plan.city} · {plan.total_days} 天行程</h3>
        <div className={styles.stats}>
          <span><b>{totalSpots}</b> 个景点</span>
          <span>
            门票与餐饮约 <b>¥{plan.total_cost.toFixed(0)}</b>/人
            {plan.has_unknown_cost && <em>（部分无价格数据，未计入）</em>}
          </span>
          <span>市内移动约 <b>{plan.total_distance_km}</b> km</span>
        </div>
        {plan.hotel && <p className={styles.hotel}>住宿基点：{plan.hotel.name}</p>}
      </header>

      {plan.notes.length > 0 && (
        <ul className={styles.notes}>
          {plan.notes.map((n, i) => <li key={i}>ℹ️ {n}</li>)}
        </ul>
      )}
      {plan.unassigned_spots.length > 0 && (
        <div className={styles.unassigned}>
          <strong>未安排的地点</strong>（为保证行程走得通，未强行塞入）：
          <ul>{plan.unassigned_spots.map((u, i) => <li key={i}>{u}</li>)}</ul>
        </div>
      )}
      {(plan.unmet_must_eat?.length ?? 0) > 0 && (
        <div className={styles.unassigned}>
          <strong>没能安排的必吃</strong>：
          <ul>{plan.unmet_must_eat!.map((u, i) => <li key={i}>{u}</li>)}</ul>
        </div>
      )}

      <div className={styles.tabs} role="tablist">
        {plan.days.map((d, i) => (
          <button
            key={d.day_number}
            role="tab"
            aria-selected={i === activeDay}
            className={`${styles.tab} ${i === activeDay ? styles.tabActive : ''}`}
            style={{ '--day-color': dayColor(i) } as CSSProperties}
            onClick={() => setActiveDay(i)}
          >
            <span className={styles.tabDot} />
            第 {d.day_number} 天
            {d.date && <small>{d.date.slice(5)} {weekday(d.date)}</small>}
          </button>
        ))}
      </div>

      {day && (
        <div role="tabpanel" className={styles.dayPanel}>
          <p className={styles.dayMeta}>
            <strong>{day.theme}</strong>
            <span>
              {day.total_spots} 个景点 · 约 ¥{day.total_cost.toFixed(0)}{day.has_unknown_cost ? '+' : ''} · 移动约 {day.estimated_distance_km} km
            </span>
          </p>
          <ol className={styles.timeline}>
            {day.items.map((item, i) => {
              const cost = formatCost(item);
              return (
                <li key={i} className={`${styles.item} ${styles[`cat_${item.category}`] ?? ''}`}>
                  <time className={styles.time}>{item.time_slot}</time>
                  <span className={styles.icon} aria-hidden>{CATEGORY_ICONS[item.category] ?? '📍'}</span>
                  <div className={styles.body}>
                    <div className={styles.itemTitle}>{item.title}</div>
                    {item.transit_info && <div className={styles.transit}>↳ {item.transit_info}</div>}
                    {item.description && <div className={styles.desc}>{item.description}</div>}
                    {(cost || item.open_time) && (
                      <div className={styles.meta}>
                        {cost && <span>{cost}</span>}
                        {item.open_time && <span>营业 {item.open_time}</span>}
                      </div>
                    )}
                    {item.tips && (
                      <p className={styles.itemTips}>
                        💡 {item.tips}<span className={styles.unverified}>经验建议，未经核实，出发前请确认</span>
                      </p>
                    )}
                    {item.warnings.length > 0 && (
                      <ul className={styles.warnings}>
                        {item.warnings.map((w, j) => <li key={j}>{w}</li>)}
                      </ul>
                    )}
                  </div>
                </li>
              );
            })}
          </ol>
        </div>
      )}

      {plan.tips.length > 0 && (
        <details className={styles.tips}>
          <summary>出行提示</summary>
          <ol>{plan.tips.map((t, i) => <li key={i}>{renderBold(t)}</li>)}</ol>
        </details>
      )}
    </section>
  );
});
