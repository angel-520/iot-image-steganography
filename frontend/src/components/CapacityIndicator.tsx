import { Alert, Descriptions, Progress, Skeleton } from 'antd';
import { formatBytes } from '../services/api';
import type { AnyResult } from '../types';
export function CapacityIndicator({value,loading,error}: {value?: AnyResult;loading?:boolean;error?:string}) {
  return <div className="capacity-indicator" data-testid="capacity-usage">{loading ? <Skeleton active paragraph={{rows:3}}/> : value ? <><div className="capacity-heading"><span>当前容量使用率</span><strong>{Number(value.usage_percent ?? 0).toFixed(1)}%</strong></div><Progress percent={Math.min(100,Number(value.usage_percent ?? 0))} showInfo={false} strokeColor={value.fits ? '#13a69a' : '#e66b53'}/><Descriptions size="small" column={1} colon={false} items={[
    {key:'capacity',label:'有效容量',children:formatBytes(value.capacity_bytes)},
    {key:'secret',label:'秘密数据',children:formatBytes(value.secret_bytes)},
    {key:'istg',label:'ISTG 封装开销',children:formatBytes(value.istg_overhead_bytes)},
    {key:'cipher',label:'加密开销',children:formatBytes(value.encryption_overhead_bytes)},
    {key:'used',label:'实际写入',children:<strong>{formatBytes(value.used_bytes ?? value.packet_bytes)}</strong>},
  ]}/>{!value.fits && <Alert showIcon type="warning" message="容量不足" description="请减少秘密数据、换用更大的载体或调整算法参数。"/>}</> : <div className="capacity-empty">请先上传载体并填写秘密信息。</div>}{error && <Alert type="warning" showIcon message={error}/>}</div>;
}
