import { Fragment } from 'react';
import { formatNumber } from './DealsDetails';

export function formatDuration(minutes?: number | null) {
  if (minutes == null || !Number.isFinite(Number(minutes))) return 'Нет данных';
  const value = Math.max(0, Math.round(Number(minutes)));
  if (value < 60) return `${formatNumber(value)} мин`;
  const hours = Math.floor(value / 60);
  if (hours < 48) return `${formatNumber(hours)} ч ${value % 60} мин`;
  return `${formatNumber(Math.floor(hours / 24))} дн ${hours % 24} ч`;
}

export type TrendPoint = { date: string; value?: number | null; tooltip: (date: string) => string };
const labelFormat = new Intl.DateTimeFormat('ru-RU', { day: '2-digit', month: 'short' });
function dateLabel(raw: string) {
  const date = new Date(`${raw}T00:00:00`);
  return Number.isNaN(date.getTime()) ? raw : labelFormat.format(date);
}

export function TrendChart({ id, label, empty, points, ceiling, tickLabel = formatDuration, secondary }: {
  id: string; label: string; empty: string; points: TrendPoint[]; ceiling?: number;
  tickLabel?: (value: number) => string;
  secondary?: TrendPoint[];
}) {
  const values = (series: TrendPoint[]) => series.flatMap(({ value }) =>
    value != null && Number.isFinite(Number(value)) ? [Number(value)] : []);
  const primaryValues = values(points);
  const secondaryValues = values(secondary ?? []);
  const hasValues = primaryValues.length + secondaryValues.length > 0;
  const max = ceiling ?? Math.max(1, ...primaryValues) * 1.08;
  const secondaryMax = Math.max(1, ...secondaryValues) * 1.08;
  const left = secondary ? 84 : 76;
  const right = secondary ? 816 : 882;
  const x = (index: number) => left + (points.length <= 1 ? 0 : (right - left) * index / (points.length - 1));
  const y = (value: number) => 14 + 248 * (1 - value / max);
  const dateIndices = [...new Set([0, Math.floor((points.length - 1) / 2), points.length - 1])];
  function series(data: TrendPoint[], maxValue: number, line: string, marker: string) {
    let segment: string[] = [];
    return data.map((point, index) => {
      if (point.value == null || !Number.isFinite(Number(point.value))) {
        segment = [];
        return null;
      }
      const px = x(index);
      const py = y(Number(point.value) * max / maxValue);
      segment.push(`${px},${py}`);
      const next = data[index + 1]?.value;
      return <Fragment key={index}>
        <circle cx={px} cy={py} r={2.4} className={marker}><title>{point.tooltip(dateLabel(point.date))}</title></circle>
        {(next == null || !Number.isFinite(Number(next))) && segment.length > 1 &&
          <polyline points={segment.join(' ')} className={line}/>}
      </Fragment>;
    });
  }
  return <>
    <div id={`${id}-empty`} className="empty-state" hidden={hasValues}>{empty}</div>
    <div id={`${id}-chart`} className="response-trend-chart" role="img" aria-label={label} hidden={!hasValues}>
      {hasValues && <svg viewBox="0 0 900 300" role="presentation" className="response-trend-svg">
        {[0, 1, 2, 3, 4].map((tick) => {
          const value = max * tick / 4;
          return <Fragment key={tick}>
            <line x1={left} x2={right} y1={y(value)} y2={y(value)} className="chart-gridline"/>
            <text x={left - 10} y={y(value) + 4} className={secondary ? 'chart-axis-label chart-response-axis' : 'chart-axis-label'}
              textAnchor="end">{tickLabel(value)}</text>
            {secondary && <text x={right + 10} y={y(value) + 4} className="chart-axis-label chart-conversion-axis"
              textAnchor="start">{tickLabel(secondaryMax * tick / 4)}</text>}
          </Fragment>;
        })}
        {secondary ? <>
          {series(points, max, 'employee-response-line', 'employee-response-point')}
          {series(secondary, secondaryMax, 'employee-conversion-line', 'employee-conversion-point')}
        </> : series(points, max, 'response-trend-line', 'response-trend-point')}
        {dateIndices.map((index) => <text key={index} x={x(index)} y={291} className="chart-axis-label"
          textAnchor={index === 0 ? 'start' : index === points.length - 1 ? 'end' : 'middle'}>{dateLabel(points[index].date)}</text>)}
      </svg>}
    </div>
  </>;
}
