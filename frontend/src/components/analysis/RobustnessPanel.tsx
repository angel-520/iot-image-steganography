import { useEffect, useState } from 'react';
import { Alert, Button, Card, Checkbox, Collapse, Image, InputNumber, Space, Table, Tag, Tooltip } from 'antd';
import type { TableProps } from 'antd';
import { ExperimentOutlined } from '@ant-design/icons';
import { Link } from 'react-router-dom';
import type { EChartsOption } from 'echarts';
import { AlgorithmSelector } from '../AlgorithmSelector';
import { ImageUploader } from '../ImageUploader';
import { defaultParams, ParameterPanel } from '../ParameterPanel';
import { ResultCharts } from '../ResultCharts';
import { JobStatus } from '../JobStatus';
import { HandoffNotice, useExperimentHandoff } from '../../hooks/useExperiments';
import { useJob } from '../../hooks/useJob';
import type { Algorithm, AlgorithmParams, Artifact, UploadedImage } from '../../types';
import { Exports, percent } from './common';
import '../../styles/application-studio.css';

interface RobustnessRow {
  attack: { type: string; quality?: number; sigma?: number; ratio?: number; scale?: number };
  status: string; extraction_success: boolean; exact_match: boolean; ber: number | null; file?: Artifact; error?: string;
}
const attackNames: Record<string, string> = { jpeg: '图片压缩', gaussian_noise: '噪声干扰', crop: '图像裁剪', resize: '尺寸变化' };

export function RobustnessPanel({ algorithms, guided = false }: { algorithms: Algorithm[]; guided?: boolean }) {
  const handoff = useExperimentHandoff();
  const [image, setImage] = useState<UploadedImage | undefined>(handoff.experiment?.stego);
  const [algorithm, setAlgorithm] = useState(handoff.experiment?.algorithm ?? 'sequential_lsb');
  const [params, setParams] = useState<AlgorithmParams>(handoff.experiment?.params ?? {});
  const [attacks, setAttacks] = useState(['jpeg', 'gaussian_noise', 'crop', 'resize']);
  const [quality, setQuality] = useState(85);
  const [sigma, setSigma] = useState(2);
  const [ratio, setRatio] = useState(.9);
  const [scale, setScale] = useState(.75);
  const [uploading, setUploading] = useState(false);
  const job = useJob();
  const selected = algorithms.find(item => item.id === algorithm);
  const keyMissing = selected?.parameters.some(item => item.name === 'key' && item.required && !params.key);
  const rows = (job.result?.results ?? []) as RobustnessRow[];
  useEffect(() => { if (selected) setParams(current => ({ ...defaultParams(selected), ...current })); }, [selected]);
  useEffect(() => {
    if (job.failureCode !== 'file_not_found') return;
    setImage(undefined); handoff.expire(); job.reset();
  }, [job.failureCode]);
  const change = (action: () => void) => { job.reset(); action(); };
  const run = () => {
    if (!image || !attacks.length || keyMissing || !selected?.available || uploading || job.loading) return;
    const config = attacks.map(type => type === 'jpeg' ? { type, quality } : type === 'gaussian_noise' ? { type, sigma, seed: 0 } : type === 'crop' ? { type, ratio } : { type, scale });
    void job.run('/analysis/robustness', { image_id: image.id, algorithm, params, attacks: config });
  };
  const columns: TableProps<RobustnessRow>['columns'] = [
    { title: '处理方式', render: (_, row) => <Tooltip title={JSON.stringify(row.attack)}>{attackNames[row.attack.type] ?? row.attack.type}</Tooltip> },
    { title: '恢复情况', render: (_, row) => <Tag color={row.extraction_success ? 'success' : 'warning'}>{row.extraction_success ? '已恢复' : '未恢复'}</Tag> },
    { title: '完整一致', dataIndex: 'exact_match', render: (value: boolean) => value ? '是' : '否' },
    { title: <Tooltip title="恢复比特与原始比特的差异比例。无可用恢复数据时显示 —。">误码率</Tooltip>, dataIndex: 'ber', render: percent },
    { title: '处理后图片', render: (_, row) => row.file ? <Space><Button size="small" href={row.file.download_url ?? row.file.url}>下载</Button>{row.file.url && <Image width={48} height={36} style={{ objectFit: 'cover' }} src={row.file.url} alt={attackNames[row.attack.type] + '处理后图片'} />}</Space> : '—' },
    { title: '说明', dataIndex: 'error', render: (value?: string) => value || '—', width: 240 },
  ];
  const imageInput = <><ImageUploader hideLabel={guided} label="待测试的隐写图片" value={image} onChange={value => {
    change(() => setImage(value)); handoff.detach(); handoff.dismiss();
  }} onLoadingChange={setUploading} role="stego" disabled={job.loading} />
    <p className="scene-note">请使用嵌入时的算法和参数。</p></>;
  const method = <div className="scene-settings">
    <AlgorithmSelector algorithms={algorithms} value={algorithm} disabled={job.loading} onChange={value => change(() => {
      setAlgorithm(value); setParams(defaultParams(algorithms.find(item => item.id === value)));
    })} />
    <ParameterPanel algorithm={selected} value={params} disabled={job.loading} onChange={value => change(() => setParams(value))} />
  </div>;
  const strengths = <div className="scene-fields scene-fields--two">
    <label><span>JPEG 质量（1–100）</span><InputNumber aria-label="JPEG 质量" min={1} max={100} disabled={job.loading || !attacks.includes('jpeg')} value={quality} onChange={value => change(() => setQuality(value ?? 85))} /></label>
    <label><span>噪声强度 σ</span><InputNumber aria-label="噪声强度" min={0} max={255} step={.5} disabled={job.loading || !attacks.includes('gaussian_noise')} value={sigma} onChange={value => change(() => setSigma(value ?? 2))} /></label>
    <label><span>裁剪保留宽高比例</span><InputNumber aria-label="裁剪保留宽高比例" min={.05} max={1} step={.05} disabled={job.loading || !attacks.includes('crop')} value={ratio} onChange={value => change(() => setRatio(value ?? .9))} /></label>
    <label><span>尺寸缩放比例</span><InputNumber aria-label="尺寸缩放比例" min={.05} max={4} step={.05} disabled={job.loading || !attacks.includes('resize')} value={scale} onChange={value => change(() => setScale(value ?? .75))} /></label>
  </div>;
  const controls = <><Checkbox.Group className="scene-attack-options" aria-label="传输处理方式" value={attacks} disabled={job.loading} options={Object.entries(attackNames).map(([value, label]) => ({ value, label }))} onChange={values => change(() => setAttacks(values.map(String)))} />
    {guided ? <Collapse ghost className="scene-advanced" items={[{ key: 'settings', label: '高级设置 · 算法与处理强度', children: <div className="scene-settings">{method}{strengths}</div> }]} /> : <div className="scene-settings">{strengths}</div>}
    {keyMissing && <p className="scene-note">请在算法设置中填写散布位置密钥。</p>}
  </>;
  const option: EChartsOption = { tooltip: { trigger: 'axis' }, grid: { left: 55, right: 20, top: 20, bottom: 50 }, xAxis: { type: 'category', data: rows.map(row => attackNames[row.attack.type] ?? row.attack.type) }, yAxis: { type: 'value', min: 0, max: 1, name: 'BER', axisLabel: { formatter: (value: number) => (value * 100) + '%' } }, series: [{ name: 'BER', type: 'bar', data: rows.map(row => row.ber), itemStyle: { color: '#189d91', borderRadius: [5, 5, 0, 0] }, barMaxWidth: 60 }] };
  return <div className="scene-stack">
    <HandoffNotice visible={handoff.expired} onClose={handoff.dismiss} />
    {guided ? <div className="scene-form-grid">
      <Card title="01 上传隐写图片">{imageInput}<LinkHint /></Card>
      <Card title="02 选择传输处理">{controls}</Card>
    </div> : <><div className="analysis-two">{imageInput}{method}</div>{controls}</>}
    <JobStatus task={job.task} loading={job.loading} error={job.error} />
    <div className="scene-actions"><span className="scene-note">{!image ? '上传已嵌入信息的图片后开始' : !attacks.length ? '请至少选择一种处理方式' : ''}</span><Button data-testid="scene-robustness-submit" type="primary" disabled={!image || !attacks.length || !selected?.available || keyMissing || uploading} loading={job.loading} icon={<ExperimentOutlined />} onClick={run}>{guided ? '开始传输测试' : '开始鲁棒性测试'}</Button></div>
    {job.result && <Card title={guided ? '03 查看恢复结果' : '处理结果'} extra={<Exports taskId={job.task?.id} />}>
      {guided && <div className="scene-test-summary"><strong>{rows.filter(row => row.exact_match).length} / {rows.length}</strong><span>种处理后完整恢复</span></div>}

      {!guided && <ResultCharts option={option} height={270} />}
      <Table<RobustnessRow> data-testid="scene-robustness-results" rowKey={(_, index) => String(index)} columns={columns} dataSource={rows} pagination={false} scroll={{ x: 880 }} />
      {rows.some(row => !row.extraction_success) && <Alert className="scene-result-note" type="info" message="未恢复时可降低处理强度，或更换算法、图片后重新测试。" />}
    </Card>}
  </div>;
}

function LinkHint() {
  return <p className="scene-note">还没有隐写图片？<Link to="/applications/copyright">先为作品写入版权信息</Link></p>;
}
