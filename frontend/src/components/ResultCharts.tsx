import { useEffect, useRef } from 'react';
import * as echarts from 'echarts/core';
import { BarChart, LineChart, PieChart, ScatterChart } from 'echarts/charts';
import { DataZoomComponent, DatasetComponent, GridComponent, LegendComponent, TitleComponent, TooltipComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import type { EChartsOption } from 'echarts';
echarts.use([BarChart, LineChart, PieChart, ScatterChart, DataZoomComponent, DatasetComponent, GridComponent, LegendComponent, TitleComponent, TooltipComponent, CanvasRenderer]);
export function ResultCharts({option,height=300}: {option: EChartsOption; height?: number}) {
  const reference = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!reference.current) return;
    const chart = echarts.init(reference.current, undefined, {renderer:'canvas'});
    const fontFamily = getComputedStyle(reference.current).fontFamily;
    chart.setOption({
      ...option,
      textStyle: {fontFamily, fontSize: 13, ...option.textStyle},
      animation: !window.matchMedia('(prefers-reduced-motion: reduce)').matches,
    });
    const observer = new ResizeObserver(() => chart.resize()); observer.observe(reference.current);
    return () => { observer.disconnect(); chart.dispose(); };
  }, [option]);
  return <div ref={reference} style={{height,width:'100%'}} role="img" aria-label="处理结果图表"/>;
}
