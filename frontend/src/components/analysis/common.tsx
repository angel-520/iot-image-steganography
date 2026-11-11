import { Button, Descriptions, Empty, Image, Input, Space, Tag, Typography, message } from 'antd';
import { CopyOutlined, DownloadOutlined } from '@ant-design/icons';
import type { ReactNode } from 'react';
import { exportUrl } from '../../services/api';
import type { Artifact } from '../../types';

export type Numeric = number | string | null | undefined;
export function numberOf(value: Numeric): number | null {
  if (value === null || value === undefined || value === '') return null;
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
}
export function metric(value: Numeric, digits = 3): string {
  if (value === 'Infinity' || value === Infinity) return '∞';
  const numeric = numberOf(value);
  return numeric === null ? '—' : numeric.toLocaleString('zh-CN', { maximumFractionDigits: digits });
}
export function bytes(value: Numeric): string {
  const numeric = numberOf(value);
  if (numeric === null) return '—';
  if (numeric < 1024) return `${metric(numeric, 0)} B`;
  if (numeric < 1024 ** 2) return `${metric(numeric / 1024, 2)} KiB`;
  return `${metric(numeric / 1024 ** 2, 2)} MiB`;
}
export function milliseconds(value: Numeric): string {
  const numeric = numberOf(value);
  return numeric === null ? '—' : `${metric(numeric * 1000, 2)} ms`;
}
export function percent(value: Numeric): string {
  const numeric = numberOf(value);
  return numeric === null ? '无法计算' : `${metric(numeric * 100, 3)}%`;
}

const statusText: Record<string, string> = {
  ok: '完成', recovered: '完整恢复', unsupported: '不支持', attack_failed: '扰动失败',
  extraction_failed: '提取失败', embedding_failed: '嵌入失败', authentication_failed: '认证失败',
  password_required: '需要密码', no_match: '无匹配', invalid_packet: '无效数据包',
  length_mismatch: '长度不一致', data_mismatch: '数据不一致', failed: '失败',
  capacity_exceeded: '容量不足', error: '失败', undetermined: '无法判定', degenerate: '统计退化',
};
export function StatusTag({ status }: { status?: string }) {
  const good = status === 'ok' || status === 'recovered';
  const warning = status === 'password_required' || status === 'unsupported' || status === 'undetermined' || status === 'degenerate';
  return <Tag color={good ? 'success' : warning ? 'warning' : 'error'}>{statusText[status ?? ''] ?? status ?? '—'}</Tag>;
}
export function Exports({ taskId }: { taskId?: string }) {
  return taskId ? <Space wrap><Button size="small" icon={<DownloadOutlined />} href={exportUrl(taskId, 'json')}>导出 JSON</Button><Button size="small" icon={<DownloadOutlined />} href={exportUrl(taskId, 'csv')}>导出 CSV</Button></Space> : null;
}
export function Measures({ items }: { items: { label: string; value: ReactNode; hint?: string }[] }) {
  return <div className="analysis-measures">{items.map(item => <div className="analysis-measure" key={item.label}><span>{item.label}</span><strong>{item.value}</strong>{item.hint && <small>{item.hint}</small>}</div>)}</div>;
}
export interface ExtractResult {
  status: string; data_type?: string; text?: string; authenticated?: boolean | null;
  encrypted?: boolean; packet_length?: number; error?: string;
  file?: Artifact; preview?: Artifact; metadata?: Record<string, unknown>;
}
export function RecoveredContent({ result }: { result?: ExtractResult }) {
  if (!result) return null;
  const copy = async () => {
    try {
      if (!navigator.clipboard) throw new Error('clipboard unavailable');
      await navigator.clipboard.writeText(result.text ?? '');
      message.success('文本已复制');
    } catch { message.warning('复制失败，请手动选择文本复制。'); }
  };
  return <div className="analysis-result-content">
    <Space wrap><StatusTag status={result.status}/>{result.authenticated === true && <Tag color="success">AEAD 认证通过</Tag>}{result.status === 'ok' && result.authenticated == null && <Tag>未加密内容</Tag>}</Space>
    {result.error && result.status !== 'no_match' && <Typography.Paragraph type={result.status === 'authentication_failed' ? 'danger' : 'secondary'}>{result.error}</Typography.Paragraph>}
    {result.status === 'ok' && <>
      <Descriptions size="small" column={2} items={[{key:'kind',label:'数据类型',children:result.data_type === 'text' ? '文本' : result.data_type === 'image_pixels' ? '图片高位像素（有损）' : '原始图片文件'},{key:'length',label:'数据包大小',children:bytes(result.packet_length)}]}/>
      {result.text !== undefined && <><Input.TextArea readOnly value={result.text} autoSize={{minRows:3,maxRows:10}} aria-label="恢复的秘密文本"/><Button icon={<CopyOutlined />} onClick={() => { void copy(); }}>复制文本</Button></>}
      {result.preview?.url && <Image src={result.preview.url} alt="恢复的秘密图片" style={{maxHeight:300,objectFit:'contain'}}/>}
      {result.file && <Button type="primary" icon={<DownloadOutlined />} href={result.file.download_url ?? result.file.url}>下载恢复文件</Button>}
    </>}
    {result.status === 'no_match' && <Empty description="未找到匹配的数据包" image={Empty.PRESENTED_IMAGE_SIMPLE}/>}
  </div>;
}
