import { Fragment } from 'react';
import { formatNumber } from './DealsDetails';
import { SegmentedBars } from './SegmentedBars';
import { useChartWidth } from './useChartWidth';

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
  const { chartRef, width, compact } = useChartWidth();
  const values = (series: TrendPoint[]) => series.flatMap(({ value }) =>
    value != null && Number.isFinite(Number(value)) ? [Number(value)] : []);
  const primaryValues = values(points);
  const secondaryValues = values(secondary ?? []);
  const hasValues = primaryValues.length + secondaryValues.length > 0;
  const max = ceiling ?? Math.max(1, ...primaryValues) * 1.08;
  const secondaryMax = Math.max(1, ...secondaryValues) * 1.08;
  const hasSecondary = secondaryValues.length > 0;
  const durationGutter = compact ? 64 : 106;
  const left = ceiling ? 44 : durationGutter;
  const right = width - (hasSecondary ? durationGutter : 22);
  const top = 18;
  const baseline = compact ? 144 : 225;
  const slot = (right - left) / Math.max(1, points.length);
  const barWidth = Math.min(36, slot * 0.7);
  const x = (index: number) => left + slot * (index + 0.5);
  const y = (value: number) => baseline - (baseline - top) * value / max;
  const lastIndex = points.length - 1;
  const dateIndices = Array.from(new Set([0, Math.floor(lastIndex / 2), lastIndex]))
    .filter((index) => index >= 0 && index <= lastIndex);
  const bars = points.flatMap((point, index) =>
    point.value != null && Number.isFinite(Number(point.value)) ? [{
      key: point.date, x: x(index) - barWidth / 2, y: y(Number(point.value)),
      tooltip: point.tooltip(dateLabel(point.date)),
    }] : []);
  let segment: string[] = [];
  const conversion = secondary?.map((point, index) => {
    if (point.value == null || !Number.isFinite(Number(point.value))) {
      segment = [];
      return null;
    }
    const px = x(index);
    const py = y(Number(point.value) * max / secondaryMax);
    segment.push(`${px},${py}`);
    const next = secondary[index + 1]?.value;
    return <Fragment key={point.date}>
      {(next == null || !Number.isFinite(Number(next))) && segment.length > 1 &&
        <polyline points={segment.join(' ')} className="employee-conversion-line"/>}
      <circle cx={px} cy={py} r={3} className="employee-conversion-point">
        <title>{point.tooltip(dateLabel(point.date))}</title>
      </circle>
    </Fragment>;
  });
  return <>
    <div id={`${id}-empty`} className="empty-state" hidden={hasValues}>{empty}</div>
    <div id={`${id}-chart`} className="response-trend-chart" ref={chartRef} role="img" aria-label={label} hidden={!hasValues}>
      {hasValues && <svg viewBox={`0 0 ${width} ${baseline + 39}`} role="presentation"
        className={compact ? 'response-trend-svg chart-compact' : 'response-trend-svg'}>
        {[0, 1, 2, 3, 4].map((tick) => {
          const value = max * tick / 4;
          return <Fragment key={tick}>
            <line x1={left} x2={right} y1={y(value)} y2={y(value)} className={tick === 0 ? 'chart-gridline chart-baseline' : 'chart-gridline'}/>
            <text x={left - 10} y={y(value) + 4} className={secondary ? 'chart-axis-label chart-response-axis' : 'chart-axis-label'}
              textAnchor="end">{tickLabel(value)}</text>
            {hasSecondary && <text x={right + 10} y={y(value) + 4} className="chart-axis-label chart-conversion-axis"
              textAnchor="start">{tickLabel(secondaryMax * tick / 4)}</text>}
          </Fragment>;
        })}
        <SegmentedBars bars={bars} width={barWidth} baseline={baseline} step={compact ? 7 : 10}/>
        {conversion}
        {dateIndices.map((index) => <text key={index} x={x(index)} y={baseline + 23} className="chart-axis-label"
          textAnchor="middle">{dateLabel(points[index].date)}</text>)}
      </svg>}
    </div>
  </>;
}
