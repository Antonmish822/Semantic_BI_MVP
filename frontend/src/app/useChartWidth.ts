import { useLayoutEffect, useRef, useState } from 'react';

export function useChartWidth() {
  const chartRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(900);

  useLayoutEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    const measure = () => {
      const available = chart.clientWidth;
      if (available > 0) setWidth(Math.min(available, 900));
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(chart);
    return () => observer.disconnect();
  }, []);

  return { chartRef, width, compact: width <= 520 };
}
