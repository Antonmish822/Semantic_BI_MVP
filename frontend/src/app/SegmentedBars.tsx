import { useId } from 'react';

type Bar = { key: string | number; x: number; y: number; tooltip: string };

export function SegmentedBars({ bars, width, baseline, step }: {
  bars: Bar[]; width: number; baseline: number; step: number;
}) {
  const patternId = useId();
  const maskId = useId();
  return <>
    <defs>
      <pattern id={patternId} width="1" height={step} y={baseline} patternUnits="userSpaceOnUse">
        <rect y="2" width="1" height={step - 2} fill="white"/>
      </pattern>
      <mask id={maskId}>
        <rect width="100%" height="100%" fill={`url(#${patternId})`}/>
      </mask>
    </defs>
    <g mask={`url(#${maskId})`}>
      {bars.map(({ key, x, y, tooltip }) => {
        const height = Math.max(2, baseline - y);
        return <rect key={key} x={x} y={baseline - height} width={width} height={height}
          rx={1.5} className="summary-chart-bar"><title>{tooltip}</title></rect>;
      })}
    </g>
  </>;
}
