import { useEffect, useState } from 'react';
import { Alert, Button, Card, Descriptions, Input, InputNumber, Select, Space, Statistic, Switch, Table, Tabs, Tag, Upload, message } from 'antd';
import { DownloadOutlined, InboxOutlined, PlayCircleOutlined } from '@ant-design/icons';
import { ResultCharts } from '../components/ResultCharts';
import { JobStatus } from '../components/JobStatus';
import { useJob } from '../hooks/useJob';
import { api, downloadUrl, errorMessage, exportUrl, formatBytes, formatMetric } from '../services/api';
import type { AnyResult, Artifact } from '../types';
import '../styles/network-lab.css';

type Channel = 'timing' | 'length' | 'ip_id' | 'tcp_seq' | 'udp_sport';
interface Codec { channel: Channel; short_ms: number; long_ms: number; short_length: number; long_length: number; repair_order: boolean }
interface Disturbance { delay_ms: number; jitter_ms: number; loss_rate: number; reorder_rate: number; seed: number }
const defaults: Codec = { channel: 'timing', short_ms: 8, long_ms: 24, short_length: 64, long_length: 192, repair_order: true };
const noDisturbance: Disturbance = { delay_ms: 0, jitter_ms: 0, loss_rate: 0, reorder_rate: 0, seed: 42 };
const names: Record<Channel, string> = { timing: 'UDP 时间间隔', length: 'UDP 数据包长度', ip_id: 'IPv4 · Identification', tcp_seq: 'TCP · Sequence Number', udp_sport: 'UDP · Source Port' };
const choices = (fields: boolean) => Object.entries(names).filter(([key]) => fields ? !['timing', 'length'].includes(key) : ['timing', 'length'].includes(key)).map(([value, label]) => ({ value, label }));
const percent = (value: number | null | undefined) => value == null ? '—' : `${(value * 100).toFixed(1)}%`;

function CodecFields({ value, onChange, fields = false, all = false, showRepair = true }: { value: Codec; onChange: (value: Codec) => void; fields?: boolean; all?: boolean; showRepair?: boolean }) {
  return <Space direction="vertical" size="middle" style={{ width: '100%' }}>
    <label className="field-label">编码 / 解码信道</label><Select aria-label="网络信道" value={value.channel} style={{ width: '100%' }} options={all ? [...choices(false), ...choices(true)] : choices(fields)} onChange={channel => onChange({ ...value, channel })} />
    {value.channel === 'timing' && <div className="network-fields"><label>0 · 短间隔 (ms)<InputNumber aria-label="短间隔" min={1} max={30} value={value.short_ms} onChange={number => onChange({ ...value, short_ms: number ?? 8 })} /></label><label>1 · 长间隔 (ms)<InputNumber aria-label="长间隔" min={5} max={100} value={value.long_ms} onChange={number => onChange({ ...value, long_ms: number ?? 24 })} /></label></div>}
    {value.channel === 'length' && <div className="network-fields"><label>0 · 短载荷 (字节)<InputNumber aria-label="短包载荷" min={16} max={1399} value={value.short_length} onChange={number => onChange({ ...value, short_length: number ?? 64 })} /></label><label>1 · 长载荷 (字节)<InputNumber aria-label="长包载荷" min={17} max={1400} value={value.long_length} onChange={number => onChange({ ...value, long_length: number ?? 192 })} /></label></div>}
    {showRepair && <label className="network-switch"><Switch checked={value.repair_order} onChange={checked => onChange({ ...value, repair_order: checked })} />修复乱序</label>}

  </Space>;
}

function ImpairmentFields({ value, onChange }: { value: Disturbance; onChange: (value: Disturbance) => void }) {
  return <><div className="network-fields">
    <label>固定延迟 (ms)<InputNumber aria-label="固定延迟" min={0} max={500} value={value.delay_ms} onChange={v => onChange({ ...value, delay_ms: v ?? 0 })} /></label>
    <label>抖动 σ (ms)<InputNumber aria-label="网络抖动" min={0} max={100} value={value.jitter_ms} onChange={v => onChange({ ...value, jitter_ms: v ?? 0 })} /></label>
    <label>丢包率 (%)<InputNumber aria-label="丢包率" min={0} max={100} value={value.loss_rate * 100} onChange={v => onChange({ ...value, loss_rate: (v ?? 0) / 100 })} /></label>
    <label>乱序概率 (%)<InputNumber aria-label="乱序概率" min={0} max={100} value={value.reorder_rate * 100} onChange={v => onChange({ ...value, reorder_rate: (v ?? 0) / 100 })} /></label>
    <label>随机种子<InputNumber aria-label="随机种子" min={0} max={2 ** 32 - 1} value={value.seed} onChange={v => onChange({ ...value, seed: v ?? 42 })} /></label>
  </div></>;
}

function Recovery({ result }: { result: AnyResult }) {
  if (result.status !== 'ok') return <Alert showIcon type={result.status === 'password_required' ? 'info' : 'warning'} message={result.status === 'password_required' ? '需要解密密码' : '未完整恢复秘密信息'} description={result.error} />;
  return <div className="network-recovery"><Space wrap><Tag color="success">{result.authenticated ? '解密与校验通过' : '校验通过'}</Tag><Button icon={<DownloadOutlined />} href={downloadUrl(result.file)}>下载恢复文字</Button></Space><pre>{result.text}</pre><code className="network-hash">SHA-256 · {result.sha256}</code></div>;
}

function Detection({ data }: { data: AnyResult }) {
  if (data.status !== 'ok') return <Alert type="info" showIcon message={data.reason} />;
  const evaluation = data.evaluation;
  return <div className="network-stack"><div className="network-metrics"><Statistic title="统计告警" value={data.findings.length} /><Statistic title="异常窗口比例" value={percent(data.anomaly_ratio)} /><Statistic title="包长双值集中度" value={percent(data.features.length_two_level_mass)} /><Statistic title="间隔双簇紧密度" value={formatMetric(data.features.gap_two_cluster_balance)} /></div>

    {data.findings.length ? data.findings.map((item: AnyResult) => <Alert key={item.feature} type="warning" message={item.message} description={`${item.feature} = ${formatMetric(item.value)}`} />) : <Alert type="success" message="未发现统计异常" />}
    {data.windows.length > 0 ? <ResultCharts option={{ color: ['#0d8f87'], tooltip: { trigger: 'axis' }, xAxis: { type: 'value', name: '起始包' }, yAxis: { type: 'value', name: 'decision' }, grid: { left: 65, right: 30, bottom: 45 }, series: [{ type: 'line', name: 'Isolation Forest 决策值', data: data.windows.map((row: AnyResult) => [row.start, row.decision]), markLine: { silent: true, data: [{ yAxis: 0 }], label: { formatter: '异常阈值 0' } } }] }} height={240} /> : <Alert type="info" message="异常分析至少需要 32 个数据包" />}
    <Card size="small" title="模型评估 · 独立合成测试集"><Descriptions size="small" column={{ xs: 1, sm: 2, lg: 4 }} items={[{ key: 'precision', label: 'Precision', children: percent(evaluation.precision) }, { key: 'recall', label: 'Recall', children: percent(evaluation.recall) }, { key: 'fpr', label: '误报率', children: percent(evaluation.false_positive_rate) }, { key: 'auc', label: 'ROC AUC', children: formatMetric(evaluation.roc_auc) }]} /><Table size="small" pagination={false} rowKey="truth" columns={[{ title: '实际类别', dataIndex: 'truth' }, { title: '判定正常', dataIndex: 'normal' }, { title: '判定异常', dataIndex: 'abnormal' }]} dataSource={[{ truth: '正常流量', normal: evaluation.TN, abnormal: evaluation.FP }, { truth: '隐写流量', normal: evaluation.FN, abnormal: evaluation.TP }]} /></Card>
  </div>;
}

function histogram(values: number[]) {
  if (!values.length) return { labels: [], counts: [] };
  const min = Math.min(...values), max = Math.max(...values);
  const width = Math.max(1, (max - min) / 16);
  const counts = new Array(16).fill(0);
  values.forEach(v => { counts[Math.min(15, Math.floor((v - min) / width))] += 1; });
  return { labels: counts.map((_, i) => (min + i * width).toFixed(1)), counts };
}

function TrafficView({ data }: { data: AnyResult }) {
  const rows: AnyResult[] = data.rows;
  const lengths = histogram(rows.map(r => r.length));
  const gaps = histogram(rows.filter(r => r.gap_ms != null).map(r => r.gap_ms));
  return <div className="network-stack"><div className="network-metrics"><Statistic title="IPv4 包数" value={data.packet_count} /><Statistic title="轨迹时长 (s)" value={formatMetric(data.duration_seconds)} /><Statistic title="比特恢复率" value={percent(data.recovery?.bit_recovery_rate)} /><Statistic title="完整恢复" value={data.recovered.status === 'ok' ? '通过' : '未恢复'} /></div>
    {data.recovery && <p className="network-note">BER {formatMetric(data.recovery.ber, 4)} · 缺失位 {data.recovery.missing_bits} / {data.recovery.expected_bits}</p>}
    <Recovery result={data.recovered} />
    <Tabs items={[
      { key: 'timeline', label: '到达时间轴', children: <ResultCharts option={{ color: ['#0d8f87'], tooltip: { trigger: 'axis' }, xAxis: { type: 'value', name: '秒' }, yAxis: { type: 'value', name: 'IPv4 字节' }, dataZoom: [{ type: 'inside' }, { type: 'slider', bottom: 4 }], grid: { left: 65, bottom: 65, right: 35 }, series: [{ type: 'scatter', symbolSize: 5, data: rows.map(r => [r.t, r.length]) }] }} /> },
      { key: 'gaps', label: '时间间隔', children: <ResultCharts option={{ color: ['#19384b'], tooltip: { trigger: 'axis' }, xAxis: { type: 'value', name: '到达序号' }, yAxis: { type: 'value', name: 'ms' }, dataZoom: [{ type: 'inside' }, { type: 'slider', bottom: 4 }], grid: { left: 65, bottom: 65, right: 35 }, series: [{ type: 'line', showSymbol: false, data: rows.filter(r => r.gap_ms != null).map(r => [r.index, r.gap_ms]) }] }} /> },
      { key: 'histogram', label: '分布对比', children: <div className="network-two"><ResultCharts height={250} option={{ color: ['#0d8f87'], tooltip: { trigger: 'axis' }, xAxis: { type: 'category', name: '包长', data: lengths.labels }, yAxis: { type: 'value', name: '包数' }, series: [{ type: 'bar', data: lengths.counts }] }} /><ResultCharts height={250} option={{ color: ['#19384b'], tooltip: { trigger: 'axis' }, xAxis: { type: 'category', name: '间隔 ms', data: gaps.labels }, yAxis: { type: 'value', name: '包数' }, series: [{ type: 'bar', data: gaps.counts }] }} /></div> },
      { key: 'detection', label: '隐蔽信道检测', children: <Detection data={data.detection} /> },
      { key: 'packets', label: '协议字段明细', children: <Table size="small" rowKey="index" scroll={{ x: 900 }} pagination={{ pageSize: 10, showSizeChanger: true }} dataSource={rows} columns={[{ title: '#', dataIndex: 'index', width: 65 }, { title: '时间 (s)', dataIndex: 't', render: v => formatMetric(v, 6) }, { title: 'IP 包长', dataIndex: 'length' }, { title: '协议', dataIndex: 'protocol' }, { title: 'IP ID', dataIndex: 'ip_id' }, { title: 'TCP Seq', dataIndex: 'tcp_seq' }, { title: '源端口', dataIndex: 'sport' }, { title: '目的端口', dataIndex: 'dport' }, { title: '实验序号', dataIndex: 'sequence' }]} /> },
    ]} />
  </div>;
}

function ComparedResult({ result, taskId }: { result: AnyResult; taskId?: string }) {
  return <Card title="实验轨迹与恢复分析" extra={taskId && <Button href={exportUrl(taskId)} icon={<DownloadOutlined />}>导出报告</Button>}>
    {result.transport && <Alert showIcon type="success" message={`UDP 传输完成 · ${result.transport.host}:${result.transport.receiver_port}`} description={`发送 ${result.transport.sent_packets} 包 / 接收 ${result.transport.received_packets} 包 · 耗时 ${formatMetric(result.transport.elapsed_seconds)} 秒`} />}

    <Tabs items={[{ key: 'baseline', label: '基线流量', children: <><div className="network-download"><Button href={downloadUrl(result.baseline.pcap ?? result.file)} icon={<DownloadOutlined />}>下载基线 PCAP</Button></div><TrafficView data={result.baseline} /></> }, { key: 'impaired', label: '扰动后流量', children: <><div className="network-download"><Button href={downloadUrl(result.impaired.pcap)} icon={<DownloadOutlined />}>下载扰动 PCAP</Button></div><TrafficView data={result.impaired} /></> }]} />
    {result.detection_evaluation && <Card size="small" title="扰动对检测效果的影响 · 合成留出集"><Table size="small" rowKey="name" scroll={{ x: 650 }} pagination={false} columns={[{ title: '测试条件', dataIndex: 'name' }, { title: 'Precision', dataIndex: 'precision', render: percent }, { title: 'Recall', dataIndex: 'recall', render: percent }, { title: '误报率', dataIndex: 'false_positive_rate', render: percent }, { title: 'ROC AUC', dataIndex: 'roc_auc', render: v => formatMetric(v) }, { title: '评分 / 无法判定', render: (_, row: AnyResult) => `${row.scored_windows} / ${row.undetermined_windows}` }]} dataSource={[{ name: '基线', ...result.detection_evaluation.baseline }, { name: '扰动后', ...result.detection_evaluation.impaired }]} /></Card>}
  </Card>;
}

function Experiment({ fields = false }: { fields?: boolean }) {
  const [codec, setCodec] = useState<Codec>({ ...defaults, channel: fields ? 'ip_id' : 'timing' });
  const [text, setText] = useState('ISTG 网络实验');
  const [execution, setExecution] = useState('simulation');
  const [impairment, setImpairment] = useState(noDisturbance);
  const [encryption, setEncryption] = useState('none');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [estimate, setEstimate] = useState<AnyResult>();
  const [checking, setChecking] = useState(false);
  const job = useJob();
  const bytes = new TextEncoder().encode(text).length;
  const parametersOK = codec.long_ms >= codec.short_ms + 4 && codec.long_length > codec.short_length;
  const ready = bytes > 0 && bytes <= 128 && parametersOK;
  const body = { ...codec, text, execution: fields ? 'offline' : execution, impairment, encryption };
  const invalidate = () => { setEstimate(undefined); job.reset(); };
  const inspect = async () => { setChecking(true); try { setEstimate((await api.post('/network-lab/estimate', body)).data); } catch (error) { void message.error(errorMessage(error)); } finally { setChecking(false); } };
  const run = async () => { try { await job.run('/network-lab/run', { ...body, ...(encryption !== 'none' ? { password, confirm_password: confirm } : {}) }); } finally { setPassword(''); setConfirm(''); } };
  return <div className="network-stack"><fieldset disabled={job.loading || checking} className="network-fieldset"><div className="network-two"><Card title={fields ? '协议字段编码' : 'UDP 隐写信道'}><CodecFields value={codec} fields={fields} onChange={v => { setCodec(v); invalidate(); }} />{!fields && <div className="network-control"><label className="field-label">执行方式</label><Select aria-label="传输方式" value={execution} style={{ width: '100%' }} options={[{ value: 'simulation', label: '离线模拟' }, { value: 'loopback', label: '本机回环 UDP' }]} onChange={v => { setExecution(v); invalidate(); }} /></div>}</Card>
      <Card title="秘密文字"><Input.TextArea aria-label="网络秘密文本" rows={4} maxLength={128} value={text} onChange={e => { setText(e.target.value); invalidate(); }} /><p className="network-note">{bytes} / 128 UTF-8 字节</p><Select aria-label="网络加密方式" value={encryption} style={{ width: '100%', marginTop: 16 }} options={['none', 'AES-256-GCM', 'ChaCha20-Poly1305'].map(value => ({ value, label: value === 'none' ? '不加密' : value }))} onChange={value => { setEncryption(value); invalidate(); }} />{encryption !== 'none' && <Space direction="vertical" style={{ width: '100%', marginTop: 16 }}><Input.Password autoComplete="new-password" aria-label="网络加密密码" value={password} placeholder="设置密码" onChange={e => setPassword(e.target.value)} /><Input.Password autoComplete="new-password" aria-label="网络确认密码" value={confirm} placeholder="确认密码" onChange={e => setConfirm(e.target.value)} /></Space>}
      <div className="network-control"><Button loading={checking} disabled={!ready} onClick={() => void inspect()}>估算包数与传输时长</Button>{estimate && <p className="network-note">数据 {formatBytes(estimate.packet_bytes)} · {estimate.packets} 包 · 预计耗时 {formatMetric(estimate.planned_seconds)} 秒{!estimate.active_fits && <Tag color="warning">预计超过 45 秒，请缩短文字或选择离线模拟</Tag>}</p>}</div>
    </Card></div><Card title="网络扰动参数 · 与基线对比"><ImpairmentFields value={impairment} onChange={v => { setImpairment(v); job.reset(); }} /></Card></fieldset>
    {!parametersOK && <Alert type="error" message="长间隔至少比短间隔大 4 ms；长包载荷必须大于短包" />}{bytes > 128 && <Alert type="error" message="秘密内容超过 128 UTF-8 字节，请缩短文本" />}
    <div><Button type="primary" size="large" icon={<PlayCircleOutlined />} loading={job.loading} disabled={!ready || checking || (execution === 'loopback' && !fields && estimate?.active_fits === false) || (encryption !== 'none' && (!password || password !== confirm))} onClick={() => void run()}>{fields ? '生成 PCAP 并提取' : execution === 'loopback' ? '开始 UDP 传输' : '开始离线实验'}</Button></div><JobStatus task={job.task} loading={job.loading} error={job.error} />
    {job.result && <ComparedResult result={job.result} taskId={job.task?.id} />}
  </div>;
}

function TrafficAnalysis() {
  const [file, setFile] = useState<Artifact & { id: string; total_packets: number }>();
  const [codec, setCodec] = useState(defaults);
  const [impairment, setImpairment] = useState(noDisturbance);
  const [password, setPassword] = useState('');
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string>();
  const job = useJob();
  const run = async (robustness = false) => { try { await job.run(`/network-lab/${robustness ? 'robustness' : 'analyze'}`, { file_id: file?.id, ...codec, ...(password ? { password } : {}), ...(robustness ? { impairment } : {}) }); } finally { setPassword(''); } };
  return <div className="network-stack">
    <fieldset className="network-fieldset" disabled={job.loading}><div className="network-two"><Card title="导入实验流量"><Upload.Dragger accept=".pcap,.pcapng,.cap" multiple={false} showUploadList={false} disabled={uploading} beforeUpload={selected => {
      if (selected.size > 16 * 1024 ** 2) { setUploadError('PCAP 不能超过 16 MiB'); return false; }
      const form = new FormData(); form.append('file', selected); setUploading(true); setUploadError(undefined); job.reset();
      void api.post('/network-lab/uploads', form).then(({ data }) => setFile(data)).catch(error => { setFile(undefined); setUploadError(errorMessage(error)); }).finally(() => setUploading(false));
      return false;
    }}><p className="ant-upload-drag-icon"><InboxOutlined /></p><p>{uploading ? '正在解析数据包…' : file?.name ?? '点击或拖入 PCAP / PCAPNG'}</p><p className="ant-upload-hint">最大 16 MiB / 30000 包{file ? ` · 当前 ${file.total_packets} 包` : ''}</p></Upload.Dragger>{uploadError && <Alert type="error" showIcon message={uploadError} />}</Card>
    <Card title="提取参数"><CodecFields all value={codec} onChange={v => { setCodec(v); job.reset(); }} /><Input.Password autoComplete="new-password" aria-label="网络提取密码" style={{ marginTop: 16 }} value={password} onChange={e => setPassword(e.target.value)} placeholder="输入原密码（已加密时填写）" /></Card></div><Card title="鲁棒性重测"><ImpairmentFields value={impairment} onChange={v => { setImpairment(v); job.reset(); }} /></Card></fieldset>
    <Space wrap><Button type="primary" size="large" loading={job.loading} disabled={!file || uploading} onClick={() => void run()}>分析流量并尝试恢复</Button><Button size="large" disabled={!file || uploading || job.loading} onClick={() => void run(true)}>测试扰动后的恢复效果</Button></Space><JobStatus task={job.task} loading={job.loading} error={job.error} />
    {job.result && (job.result.analysis ? <Card title="离线流量分析" extra={job.task && <Button href={exportUrl(job.task.id)}>导出报告</Button>}><TrafficView data={job.result.analysis} /></Card> : <ComparedResult result={job.result} taskId={job.task?.id} />)}
  </div>;
}

function SharedKeyField({ value, onChange, label }: { value: string; onChange: (value: string) => void; label: string }) {
  return <Space direction="vertical" size="small" style={{ width: '100%' }}><label className="field-label">双方共享口令</label><Input.Password aria-label={label} autoComplete="new-password" value={value} onChange={event => onChange(event.target.value)} placeholder="粘贴双方约定的同一份共享口令" maxLength={256} />
    <Space wrap><Button onClick={() => { const bytes = crypto.getRandomValues(new Uint8Array(32)); onChange(Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('')); }}>生成共享口令</Button><Button disabled={!value} onClick={() => { void navigator.clipboard.writeText(value).then(() => message.success('口令已复制')).catch(() => message.warning('请展开口令后手动复制')); }}>复制口令</Button></Space><p className="network-note">双方填写相同口令，至少 32 个字符。</p></Space>;
}

function RemoteSender() {
  const [peers, setPeers] = useState<AnyResult[]>([]);
  const [peerName, setPeerName] = useState<string>();
  const [configurationError, setConfigurationError] = useState<string>();
  const [refreshing, setRefreshing] = useState(false);
  const [host, setHost] = useState('');
  const [port, setPort] = useState(42100);
  const [sharedKey, setSharedKey] = useState('');
  const [saving, setSaving] = useState(false);
  const [codec, setCodec] = useState<Codec>({ ...defaults, channel: 'length' });
  const [text, setText] = useState('你好，这是 ISTG 双机消息');
  const [payloadType, setPayloadType] = useState('text');
  const [file, setFile] = useState<AnyResult>();
  const [uploading, setUploading] = useState(false);
  const [transport, setTransport] = useState('udp');
  const [mode, setMode] = useState('length');
  const [cipher, setCipher] = useState('AES-256-GCM');
  const [rate, setRate] = useState(5);
  const [pps, setPps] = useState(500);
  const [chunkSize, setChunkSize] = useState(1024);
  const [windowSize, setWindowSize] = useState(32);
  const [retries, setRetries] = useState(3);
  const job = useJob();
  const bytes = new TextEncoder().encode(text).length;
  const refresh = async () => {
    setRefreshing(true); setConfigurationError(undefined);
    try { const { data } = await api.get('/network-lab/peers'); setPeers(data.peers); setPeerName(current => data.peers.some((p: AnyResult) => p.name === current) ? current : data.peers[0]?.name); }
    catch (error) { setConfigurationError(errorMessage(error)); setPeers([]); setPeerName(undefined); }
    finally { setRefreshing(false); }
  };
  useEffect(() => { void refresh(); }, []);
  const save = async () => {
    setSaving(true); setConfigurationError(undefined);
    try { const { data } = await api.post('/network-lab/peers', { host: host.trim(), port, shared_key: sharedKey }); setSharedKey(''); await refresh(); setPeerName(data.name); job.reset(); void message.success('接收方设置已保存'); }
    catch (error) { setConfigurationError(errorMessage(error)); }
    finally { setSaving(false); }
  };
  const selected = peers.find(peer => peer.name === peerName);
  const limit = mode === 'data' ? 65536 : 128;
  const ready = payloadType === 'file' ? Boolean(file) : bytes > 0 && bytes <= limit;
  const execute = () => void job.run('/network-lab/send-remote', { ...codec, channel: mode, transport, cipher,
    rate_mbps: rate, packets_per_second: pps, chunk_size: chunkSize, window_size: windowSize, retries,
    peer_name: peerName, ...(payloadType === 'file' ? { secret_id: file?.id } : { text }) });
  return <div className="network-stack">
    {configurationError && <Alert type="error" showIcon message={configurationError} />}
    <fieldset className="network-fieldset" disabled={job.loading || saving || uploading}><Card title="设置接收方"><div className="network-two"><Space direction="vertical" style={{ width: '100%' }} size="middle"><label className="field-label">接收方 IPv4 地址</label><Input aria-label="接收方 IP 地址" value={host} onChange={event => setHost(event.target.value)} placeholder="填写接收方的 IPv4 地址" maxLength={15} /><label className="field-label">接收方 TCP / UDP 端口</label><InputNumber aria-label="接收方端口" min={1024} max={65535} precision={0} value={port} onChange={value => setPort(value ?? 42100)} style={{ width: '100%' }} /><p className="network-note">局域网填写对方内网 IP；跨公网填写公网 IP 和映射端口。</p></Space><div><SharedKeyField value={sharedKey} onChange={setSharedKey} label="发送共享口令" /><Button style={{ marginTop: 12 }} type="primary" loading={saving} disabled={!host.trim() || sharedKey.length < 32} onClick={() => void save()}>保存接收方设置</Button></div></div></Card>
    <div className="network-two"><Card title="选择接收方与信道" extra={<Button loading={refreshing} onClick={() => void refresh()}>刷新列表</Button>}><Space direction="vertical" style={{ width: '100%' }} size="middle"><Select aria-label="远程接收端" placeholder="先在上方保存接收方设置" value={peerName} style={{ width: '100%' }} options={peers.map(peer => ({ value: peer.name, label: `${peer.host}:${peer.port}` }))} onChange={value => { setPeerName(value); job.reset(); }} />
      {selected && <Descriptions size="small" column={1} items={[{ key: 'host', label: 'IPv4', children: selected.host }, { key: 'port', label: `${transport.toUpperCase()} 端口`, children: selected.port }, { key: 'cipher', label: '保护方式', children: cipher==='none'?'不加密':cipher }]} />}

      <label className="field-label">传输协议</label><Select aria-label="发送协议" value={transport} style={{ width: '100%' }} options={[{value:'udp',label:'UDP'},{value:'tcp',label:'TCP'}]} onChange={value => { setTransport(value); if(value==='tcp') setMode('data'); job.reset(); }} />
      <label className="field-label">传输模式</label><Select aria-label="双机传输模式" value={mode} style={{width:'100%'}} options={[...(transport==='udp' && payloadType==='text' ? [{value:'length',label:'包长隐写 · 小文本'},{value:'timing',label:'时间间隔隐写 · 小文本'}] : []),{value:'data',label:'分块文件 / 文字传输'}]} onChange={value=>{setMode(value);if(value!=='data'){setCodec({...codec,channel:value as Channel});if(cipher==='none')setCipher('AES-256-GCM');}job.reset();}} />
      <label className="field-label">加密类型</label><Select aria-label="传输加密类型" style={{width:'100%'}} value={cipher} options={[{value:'AES-256-GCM',label:'AES-256-GCM'},{value:'ChaCha20-Poly1305',label:'ChaCha20-Poly1305'},...(mode==='data'?[{value:'none',label:'不加密'}]:[])]} onChange={value=>{setCipher(value);job.reset();}} />
      {mode!=='data' && <CodecFields showRepair={false} value={{...codec,channel:mode as Channel}} onChange={value=>{setCodec(value);job.reset();}} />}
      {mode==='data' ? <div className="network-fields"><label>发送速率上限 (Mbps)<InputNumber aria-label="发送速率" min={.05} max={100} step={.25} value={rate} onChange={v=>setRate(v??5)} /></label><label>分块大小 (字节)<InputNumber aria-label="分块大小" min={512} max={1200} precision={0} value={chunkSize} onChange={v=>setChunkSize(v??1024)} /></label>{transport==='udp'&&<><label>UDP 窗口 (块)<InputNumber aria-label="UDP 窗口" min={1} max={64} precision={0} value={windowSize} onChange={v=>setWindowSize(v??32)} /></label><label>UDP 重试次数<InputNumber aria-label="UDP 重试次数" min={0} max={5} precision={0} value={retries} onChange={v=>setRetries(v??3)} /></label></>}</div> : mode==='length' ? <label>发送包速率上限 (包/秒)<InputNumber aria-label="发送包速率" min={1} max={2000} value={pps} onChange={v=>setPps(v??500)} /></label> : null}
</Space></Card>
      <Card title="发送内容"><Space direction="vertical" size="middle" style={{width:'100%'}}><Select aria-label="发送内容类型" value={payloadType} style={{width:'100%'}} options={[{value:'text',label:'文字'},{value:'file',label:'图片 / 音频 / 视频 / 任意文件'}]} onChange={value=>{setPayloadType(value);if(value==='file'){setMode('data');setTransport('tcp');}job.reset();}} />{payloadType==='text'?<><Input.TextArea aria-label="远程秘密文字" rows={6} maxLength={limit} value={text} onChange={event=>{setText(event.target.value);job.reset();}} /><p className="network-note">{bytes} / {limit} UTF-8 字节。</p></>:<Upload.Dragger beforeUpload={selected=>{if(selected.size>8*1024**2){void message.error('文件不能超过 8 MiB');return false;}const form=new FormData();form.append('file',selected);setUploading(true);job.reset();void api.post('/network-lab/file-upload',form).then(({data})=>setFile(data)).catch(error=>message.error(errorMessage(error))).finally(()=>setUploading(false));return false;}} multiple={false} showUploadList={false} disabled={uploading}><p className="ant-upload-drag-icon"><InboxOutlined /></p><p>{uploading?'正在上传…':file?.name??'选择需要传输的文件'}</p><p>{file?formatBytes(file.size_bytes):'最大 8 MiB'}</p></Upload.Dragger>}
      {cipher==='none'&&<Alert type="warning" message="接收方需允许“不加密”选项。" />}
      <Alert type="info" message="发送前，请让对方开始接收，并保持协议、加密类型和共享口令一致。" /></Space></Card></div></fieldset>
    {payloadType==='text'&&bytes>limit&&<Alert type="error" message={`当前模式最多 ${limit} UTF-8 字节`} />}
    <div><Button type="primary" size="large" icon={<PlayCircleOutlined />} loading={job.loading} disabled={!selected||refreshing||saving||uploading||!ready||(mode!=='data'&&(codec.long_ms<codec.short_ms+4||codec.long_length<=codec.short_length))} onClick={execute}>发送给对方</Button></div>
    <JobStatus task={job.task} loading={job.loading} error={job.error} />
    {job.result && <Card title={<Space>发送成功<Tag color="success">对方已接收</Tag></Space>} extra={job.task && <Button href={exportUrl(job.task.id)}>导出回执报告</Button>}><div className="network-metrics"><Statistic title="发送块 / 符号" value={job.result.sent_datagrams} /><Statistic title="其中重传" value={job.result.retransmitted_datagrams} /><Statistic title="有效吞吐 (Mbps)" value={formatMetric(job.result.effective_mbps)} /><Statistic title="耗时 (s)" value={formatMetric(job.result.elapsed_seconds)} /></div><Descriptions column={1} size="small" items={[{key:'protocol',label:'协议 / 加密',children:`${job.result.transport?.toUpperCase()} / ${job.result.cipher}`},{key:'source',label:'发送地址',children:job.result.source?.join(':')},{key:'destination',label:'目标地址',children:job.result.destination?.join(':')},{key:'observed',label:'接收端所见地址',children:`${job.result.receipt.source?.join(':')} → ${job.result.receipt.destination?.join(':')}`},{ key: 'file', label: '恢复文件', children: job.result.filename??job.result.receipt.file }, { key: 'sha', label: '原文 SHA-256', children: <code className="network-hash">{job.result.receipt.plaintext_sha256}</code> }]} /><Space wrap><Button href={downloadUrl(job.result.pcap)} icon={<DownloadOutlined />}>下载发送记录 PCAP</Button>{job.result.logical_pcap&&<Button href={downloadUrl(job.result.logical_pcap)}>下载信道 PCAP</Button>}</Space>{job.result.trace_truncated&&<Alert type="warning" message="PCAP 已达记录上限，仅包含部分传输记录。" />}</Card>}
  </div>;
}

function RemoteReceiver() {
  const [allowedPeer, setAllowedPeer] = useState('');
  const [bindHost, setBindHost] = useState('0.0.0.0');
  const [port, setPort] = useState(42100);
  const [timeout, setTimeoutSeconds] = useState(300);
  const [sharedKey, setSharedKey] = useState('');
  const [transport, setTransport] = useState('udp');
  const [allowedCiphers, setAllowedCiphers] = useState(['AES-256-GCM','ChaCha20-Poly1305']);
  const [state, setState] = useState<AnyResult>({ status: 'loading', messages: [] });
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string>();
  const [pollError, setPollError] = useState<string>();
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof window.setTimeout>;
    const abort = new AbortController();
    const poll = async () => {
      try { const { data } = await api.get('/network-lab/receiver', { signal: abort.signal }); if (active) { setState(data); setPollError(undefined); if (data.status === 'listening' && data.settings) { setAllowedPeer(data.settings.allowed_peer);setBindHost(data.settings.bind_host??'0.0.0.0'); setPort(data.settings.port); setTimeoutSeconds(data.settings.timeout); setTransport(data.settings.transport??'udp');setAllowedCiphers(data.settings.allowed_ciphers??['AES-256-GCM','ChaCha20-Poly1305']); } } }
      catch (error) { if (active) setPollError(errorMessage(error)); }
      finally { if (active) timer = window.setTimeout(() => void poll(), 1000); }
    };
    void poll();
    return () => { active = false; abort.abort(); window.clearTimeout(timer); };
  }, []);
  const start = async () => {
    setBusy(true); setActionError(undefined);
    try { const { data } = await api.post('/network-lab/receiver/start', { bind_host:bindHost.trim(),allowed_peer: allowedPeer.trim(), port, timeout, shared_key: sharedKey,transport,allowed_ciphers:allowedCiphers }); setState(data); setSharedKey(''); void message.success('已开始接收'); }
    catch (error) { setActionError(errorMessage(error)); }
    finally { setBusy(false); }
  };
  const stop = async () => {
    setBusy(true); setActionError(undefined);
    try { setState((await api.post('/network-lab/receiver/stop')).data); }
    catch (error) { setActionError(errorMessage(error)); }
    finally { setBusy(false); }
  };
  const listening = state.status === 'listening';
  const statusLabel = ({ loading: '正在读取', listening: '正在接收', stopped: '已停止', expired: '监听已到期', failed: '接收已中断' } as Record<string, string>)[state.status] ?? state.status;
  return <div className="network-stack">{(actionError || pollError || state.error) && <Alert type="error" showIcon message={actionError ?? pollError ?? state.error} />}
    <fieldset className="network-fieldset" disabled={listening || busy}><Card title="接收设置"><div className="network-two"><Space direction="vertical" size="middle" style={{ width: '100%' }}><label className="field-label">允许的发送方 IPv4 地址</label><Input aria-label="允许的发送方 IP" maxLength={15} value={allowedPeer} onChange={event => setAllowedPeer(event.target.value)} placeholder="局域网填对方内网 IP，公网填对方出口 IP" /><div className="network-fields"><label>本机监听端口<InputNumber aria-label="本机接收端口" min={1024} max={65535} precision={0} value={port} onChange={value => setPort(value ?? 42100)} /></label><label>接收时长 (秒)<InputNumber aria-label="接收时长" min={1} max={3600} precision={0} value={timeout} onChange={value => setTimeoutSeconds(value ?? 300)} /></label></div><label className="field-label">监听协议</label><Select aria-label="接收协议" value={transport} style={{width:'100%'}} options={[{value:'udp',label:'UDP · 隐写或分块传输'},{value:'tcp',label:'TCP · 分块文件 / 文字传输'}]} onChange={setTransport} /><label className="field-label">允许的加密类型</label><Select aria-label="接收允许的加密类型" mode="multiple" value={allowedCiphers} style={{width:'100%'}} options={[{value:'AES-256-GCM',label:'AES-256-GCM'},{value:'ChaCha20-Poly1305',label:'ChaCha20-Poly1305'},{value:'none',label:'不加密'}]} onChange={setAllowedCiphers} /></Space><SharedKeyField value={sharedKey} onChange={setSharedKey} label="接收共享口令" /></div></Card></fieldset>
    <fieldset className="network-fieldset" disabled={listening||busy}><Card size="small" title="本机监听地址"><Input aria-label="本机绑定地址" value={bindHost} maxLength={15} onChange={event=>setBindHost(event.target.value)} /><p className="network-note">默认 0.0.0.0；也可填写本机的具体内网 IP。</p></Card></fieldset>
    <Space wrap><Button type="primary" size="large" loading={busy && !listening} disabled={listening || busy || !allowedPeer.trim() || !bindHost.trim() || sharedKey.length < 32 || !allowedCiphers.length} onClick={() => void start()}>开始接收</Button><Button size="large" loading={busy && listening} disabled={!listening || busy} onClick={() => void stop()}>停止接收</Button><Tag color={listening ? 'processing' : 'default'}>{statusLabel}</Tag></Space>
    <Card title="接收状态"><div className="network-metrics"><Statistic title={`${state.settings?.transport?.toUpperCase()??''} 端口`} value={state.settings?.port ?? '—'} /><Statistic title="剩余监听 (秒)" value={Math.ceil(state.remaining_seconds ?? 0)} /><Statistic title="当前块 / 符号" value={state.session_packets ?? 0} /><Statistic title="收到的内容" value={state.messages.length} /></div>{state.settings && <p className="network-note">允许来源 {state.settings.allowed_peer} · {state.session_expected ? `当前会话预计 ${state.session_expected} 块 / 符号` : '等待消息'}</p>}<p className="network-note">接收持续到停止或超时。跨公网接收需开放所选 TCP / UDP 端口。</p></Card>
    <Card title="收到的消息与文件" extra={<Tag>10 条 / 30 分钟</Tag>}>{!state.messages.length ? <p className="network-note">等待对方发送内容</p> : <div className="network-stack">{state.messages.map((item: AnyResult) => <section key={item.id} className="network-incoming"><Space wrap><Tag color="success">校验通过</Tag><Tag>{item.transport?.toUpperCase()} · {item.cipher}</Tag><span className="network-note">{new Date(item.received_at * 1000).toLocaleString('zh-CN')}</span></Space><p><strong>{item.filename??item.file.name}</strong> · {formatBytes(item.size_bytes)}</p><p className="network-note">来源 {item.source?.join(':')??item.peer} → 接收地址 {item.destination?.join(':')??'—'}</p>{item.kind!=='file'&&<pre>{item.text}</pre>}<Space wrap><Button icon={<DownloadOutlined />} href={downloadUrl(item.file)}>{item.kind==='file'?'下载原始文件':'下载消息 TXT'}</Button><Button href={downloadUrl(item.pcap)}>下载接收轨迹 PCAP</Button></Space><p className="network-hash">SHA-256 · {item.sha256}</p>{item.trace_truncated&&<Alert type="warning" message="PCAP 已达记录上限，仅包含部分传输记录。" />}</section>)}</div>}</Card>
  </div>;
}

function RemoteTransfer() {
  return <div className="network-stack"><Tabs items={[{ key: 'send', label: '发送消息', children: <RemoteSender /> }, { key: 'receive', label: '接收消息', children: <RemoteReceiver /> }]} /></div>;
}

export default function NetworkLab() {
  const [compatibilityError, setCompatibilityError] = useState<string>();
  useEffect(() => { const controller = new AbortController(); void api.get('/network-lab/capabilities', { signal: controller.signal }).catch(error => { if (!controller.signal.aborted) setCompatibilityError(errorMessage(error)); }); return () => controller.abort(); }, []);
  return <div className="network-page"><div className="page-heading"><div><div className="eyebrow">Network / 网络隐写实验</div><h1>在流量之间，观察信息</h1><p>网络隐写、双机传输与流量分析。</p></div></div>
    {compatibilityError && <Alert type="error" showIcon message={compatibilityError} />}
    <Tabs destroyInactiveTabPane items={[{ key: 'udp', label: 'UDP 信道', children: <Experiment /> }, { key: 'remote', label: '双机 / 公网', children: <RemoteTransfer /> }, { key: 'fields', label: '协议字段 · 离线', children: <Experiment fields /> }, { key: 'analysis', label: '流量分析与鲁棒性', children: <TrafficAnalysis /> }]} />
  </div>;
}
