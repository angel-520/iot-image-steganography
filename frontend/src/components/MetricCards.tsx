import { Statistic, Tooltip } from 'antd';
import { formatMetric } from '../services/api';
const metricsInfo = [
  { key: 'psnr', label: 'PSNR', suffix: 'dB', precision: 2, help: '峰值信噪比，单位 dB；越高通常表示像素失真越小，不代表隐写安全性。' },
  { key: 'ssim', label: 'SSIM', suffix: '', precision: 5, help: '结构相似性，无单位；越接近 1 通常表示结构越相似，不代表隐写安全性。' },
  { key: 'mse', label: 'MSE', suffix: '', precision: 4, help: '像素均方误差；越低表示像素差异越小，不代表隐写安全性。' },
];
export function MetricCards({ metrics }: { metrics?: Record<string, unknown> }) {
  return <div className="metric-grid">{metricsInfo.map(item => <div className="metric-card" key={item.key}><Statistic title={<Tooltip title={item.help}><button type="button" className="metric-help" aria-label={`${item.label} 指标说明`}>{item.label}</button></Tooltip>} value={formatMetric(metrics?.[item.key], item.precision)} suffix={item.suffix} /></div>)}</div>;
}
