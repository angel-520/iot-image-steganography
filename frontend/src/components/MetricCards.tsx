import { Statistic } from 'antd';
import { formatMetric } from '../services/api';
export function MetricCards({metrics}: {metrics?: Record<string, unknown>}) {
  return <div className="metric-grid">{[{key:'psnr',label:'PSNR',suffix:'dB',precision:2},{key:'ssim',label:'SSIM',suffix:'',precision:5},{key:'mse',label:'MSE',suffix:'',precision:4}].map(item => <div className="metric-card" key={item.key}><Statistic title={item.label} value={formatMetric(metrics?.[item.key],item.precision)} suffix={item.suffix}/></div>)}</div>;
}
