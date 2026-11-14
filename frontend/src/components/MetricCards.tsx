import { Statistic, Tooltip } from 'antd';
import { formatMetric } from '../services/api';
const metricsInfo = [
  { key: 'psnr', label: 'PSNR', suffix: 'dB', precision: 2, help: '峰值信噪比（dB），越高表示像素失真越小。' },
  { key: 'ssim', label: 'SSIM', suffix: '', precision: 5, help: '结构相似性，越接近 1 表示结构越相似。' },
  { key: 'mse', label: 'MSE', suffix: '', precision: 4, help: '均方误差，越低表示像素差异越小。' },
];
export function MetricCards({ metrics }: { metrics?: Record<string, unknown> }) {
  return <div className="metric-grid">{metricsInfo.map(item => <div className="metric-card" key={item.key}><Statistic title={<Tooltip title={item.help}><button type="button" className="metric-help" aria-label={`${item.label} 指标说明`}>{item.label}</button></Tooltip>} value={formatMetric(metrics?.[item.key], item.precision)} suffix={item.suffix} /></div>)}</div>;
}
