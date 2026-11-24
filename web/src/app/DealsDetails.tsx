import { Fragment } from 'react';
import type { ActivityDistribution, RecentActivity, RedDeal } from './api';

const numberFormat = new Intl.NumberFormat('ru-RU');
const dateFormat = new Intl.DateTimeFormat('ru-RU', {
  timeZone: 'Europe/Moscow', day: '2-digit', month: '2-digit', year: 'numeric',
  hour: '2-digit', minute: '2-digit',
});
export const formatNumber = (value: number | undefined) => numberFormat.format(Number(value ?? 0));
export function formatDate(raw?: string) {
  const date = raw ? new Date(raw) : null;
  return !date || Number.isNaN(date.getTime()) ? 'Нет данных' : dateFormat.format(date);
}
const tail = (uri?: string) => uri?.substring(uri.lastIndexOf('/') + 1) ?? '';

function LastActivity({ deal, source }: { deal: RedDeal; source: 'binding' | 'owner' }) {
  const at = deal[`${source}_activity_at`];
  const subject = deal[`${source}_activity_subject`];
  const responsible = deal[`${source}_activity_responsible`];
  const uri = deal[`${source}_activity_uri`];
  if (!at) return <td className="muted-cell">Не найдена</td>;
  return <td>
    <span className="activity-subject">{formatDate(at)}</span>
    {subject && <span className="activity-employee">{subject}</span>}
    {responsible && <span className="activity-uri">{responsible}</span>}
    {uri && <span className="activity-uri">Activity {tail(uri)}</span>}
  </td>;
}

export function RedDealRow({ deal }: { deal: RedDeal }) {
  return <tr>
    <td><span className="red-title">{deal.title || 'Без названия'}</span><span className="deal-uri">{tail(deal.uri)}</span></td>
    <td>{deal.manager || 'Не назначен'}</td>
    <td>{formatDate(deal.created_at)}</td>
    <td className="muted-cell">{formatDate(deal.last_activity_time)}</td>
    <LastActivity deal={deal} source="binding"/>
    <LastActivity deal={deal} source="owner"/>
  </tr>;
}

const classLabels: Readonly<Record<string, string>> = {
  InProgressDeal: 'В процессе', ActiveDeal: 'Активная', ClosedDeal: 'Закрыта', SuccessfulDeal: 'Успешно закрыта',
};

export function ActivityRow({ activity }: { activity: RecentActivity }) {
  const classes = activity.deal_classes?.split(' · ').map((name) =>
    Object.hasOwn(classLabels, name) ? classLabels[name] : name).join(' · ');
  return <tr>
    <td>{formatDate(activity.created_at)}</td>
    <td><span className="activity-subject">{activity.subject || 'Без темы'}</span><span className="activity-uri">{tail(activity.uri)}</span></td>
    <td><span className="deal-class">{classes || 'Класс не определён'}</span><span className="activity-deal-title">{activity.deal_title || 'Без названия'}</span><span className="activity-uri">{tail(activity.deal_uri)}</span></td>
    <td><span className="activity-employee">{activity.employee_name || 'Сотрудник не указан'}</span><span className="activity-uri">{tail(activity.employee_uri)}</span></td>
  </tr>;
}

export function DistributionChart({ buckets }: { buckets: NonNullable<ActivityDistribution['buckets']> }) {
  const width = 900;
  const height = 300;
  const left = 48;
  const right = 18;
  const top = 20;
  const plotHeight = height - top - 56;
  const max = Math.max(1, ...buckets.map((bucket) => Number(bucket.deal_count || 0)));
  const slot = (width - left - right) / buckets.length;
  const barWidth = Math.min(68, slot * 0.66);
  return <svg viewBox={`0 0 ${width} ${height}`} role="presentation" className="activity-distribution-svg">
    {[0, 1, 2, 3, 4].map((tick) => {
      const y = top + plotHeight * (1 - tick / 4);
      return <Fragment key={tick}>
        <line x1={left} x2={width - right} y1={y} y2={y} className="chart-gridline"/>
        <text x={left - 9} y={y + 4} className="chart-axis-label" textAnchor="end">{formatNumber(Math.round(max * tick / 4))}</text>
      </Fragment>;
    })}
    {buckets.map((bucket, index) => {
      const count = Number(bucket.deal_count || 0);
      const barHeight = plotHeight * count / max;
      const x = left + slot * index + (slot - barWidth) / 2;
      const y = top + plotHeight - barHeight;
      return <Fragment key={bucket.key}>
        <rect x={x} y={y} width={barWidth} height={barHeight} rx={5} className="activity-distribution-bar">
          <title>{bucket.label}: {formatNumber(count)} сделок</title>
        </rect>
        <text x={x + barWidth / 2} y={Math.max(top - 4, y - 7)} className="chart-value-label" textAnchor="middle">{formatNumber(count)}</text>
        <text x={left + slot * index + slot / 2} y={height - 27} className="chart-axis-label" textAnchor="middle">{bucket.label.replace(' дней', '')}</text>
      </Fragment>;
    })}
    <text x={width / 2} y={height - 7} className="chart-axis-label" textAnchor="middle">Давность последней активности</text>
  </svg>;
}
