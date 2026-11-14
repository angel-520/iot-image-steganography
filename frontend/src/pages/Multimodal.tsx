import { useRef, useState } from 'react';
import { Alert, Button, Card, Descriptions, Image, Input, Progress, Segmented, Select, Slider, Space, Statistic, Tabs, Tag, Upload, message } from 'antd';
import { DownloadOutlined, InboxOutlined, PlayCircleOutlined } from '@ant-design/icons';
import type { EChartsOption } from 'echarts';
import { ResultCharts } from '../components/ResultCharts';
import { JobStatus } from '../components/JobStatus';
import { useJob } from '../hooks/useJob';
import { api, downloadUrl, errorMessage, exportUrl, formatBytes, formatMetric } from '../services/api';
import type { AnyResult, Artifact } from '../types';
import '../styles/multimodal.css';

type Modality = 'image' | 'audio' | 'video' | 'text';
interface MediaFile extends Artifact { id: string; name: string; size_bytes: number; url: string }
interface Carrier { file_id: string; modality: Modality; algorithm: string; bit_depth?: number }
interface Secret { text?: string; secret_id?: string; compression?: 'auto' | 'none' }
const labels: Record<Modality, string> = { image: '图像', audio: '音频', video: '视频', text: '文本' };
const defaults: Record<Modality, string> = { image: 'image_lsb', audio: 'audio_lsb', video: 'video_lsb', text: 'text_zero_width' };
const accepts = { image: '.png,.jpg,.jpeg,.bmp,.webp,.tif,.tiff', audio: '.wav', video: '.mp4,.mkv,.avi,.webm,.mov', text: '.txt', file: '*' };
const modalityOptions = Object.entries(labels).map(([value, label]) => ({ value, label }));

function MediaUpload({ modality, value, onChange, onBusy, purpose = 'cover' }: { modality: Modality | 'file'; value?: MediaFile; onChange: (file: MediaFile) => void; onBusy?: (busy: boolean) => void; purpose?: 'cover' | 'stego' }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const maximum = purpose === 'stego' && ['audio', 'video'].includes(modality) ? 1024 : purpose === 'stego' && modality === 'text' ? 64 : { image: 16, audio: 256, video: 512, text: 16, file: 64 }[modality];
  return <div className="mm-upload"><Upload.Dragger accept={accepts[modality]} multiple={false} showUploadList={false} disabled={busy} beforeUpload={file => {
    if (file.size > maximum * 1024 ** 2) { setError(`此类文件不能超过 ${maximum} MiB`); return false; }
    const form = new FormData(); form.append('file', file); form.append('modality', modality); form.append('purpose', purpose);
    setBusy(true); onBusy?.(true); setError(undefined);
    void api.post<MediaFile>('/multimodal/uploads', form, { timeout: 30 * 60_000 }).then(({ data }) => onChange(data)).catch(err => setError(errorMessage(err))).finally(() => { setBusy(false); onBusy?.(false); });
    return false;
  }}><p className="ant-upload-drag-icon"><InboxOutlined /></p><p>{busy ? '正在上传…' : value ? value.name : '点击或拖入文件'}</p><p className="ant-upload-hint">{value ? formatBytes(value.size_bytes) : `${modality === 'file' ? '任意秘密文件' : `${labels[modality]}${purpose === 'stego' ? '隐写主文件' : '载体'}`} · 最大 ${maximum} MiB`}</p></Upload.Dragger>
    {error && <Alert showIcon type="error" message={error} />}
    {value && modality === 'audio' && <audio controls src={value.url} />}
    {value && modality === 'video' && <video controls preload="metadata" src={value.url} />}
    {value && modality === 'image' && <Image src={value.url} height={150} style={{ objectFit: 'contain' }} />}
  </div>;
}

function CarrierInput({ value, onChange, onBusy, allowText = true, initialModality = 'audio', purpose = 'cover' }: { value?: Carrier; onChange: (carrier?: Carrier) => void; onBusy?: (busy: boolean) => void; allowText?: boolean; initialModality?: Modality; purpose?: 'cover' | 'stego' }) {
  const [modality, setModality] = useState<Modality>(value?.modality ?? initialModality);
  const [algorithm, setAlgorithm] = useState(value?.algorithm ?? defaults[modality]);
  const [depth, setDepth] = useState(value?.bit_depth ?? 1);
  const [file, setFile] = useState<MediaFile>();
  const [uploading, setUploading] = useState(false);
  const select = (next: string) => { setAlgorithm(next); setDepth(1); if (file) onChange({ file_id: file.id, modality, algorithm: next, bit_depth: 1 }); };
  return <Space direction="vertical" size="middle" style={{ width: '100%' }}>
    <Segmented block disabled={uploading} value={modality} options={modalityOptions.filter(option => allowText || option.value !== 'text')} onChange={value => { const next = value as Modality; setModality(next); setAlgorithm(defaults[next]); setDepth(1); setFile(undefined); onChange(undefined); }} />
    <Select disabled={uploading} aria-label="载体隐写算法" value={algorithm} style={{ width: '100%' }} onChange={select} options={(modality === 'audio' ? [{ value: 'audio_lsb', label: 'Audio LSB' }, { value: 'audio_dwt', label: 'Audio DWT' }] : [{ value: defaults[modality], label: { image: 'Image LSB', audio: '', video: 'Video LSB', text: '零宽字符' }[modality] }])} />
    {['audio', 'video'].includes(modality) && <><Select disabled={uploading} aria-label="嵌入位数" value={depth} style={{ width: '100%' }} options={Array.from({ length: algorithm === 'audio_lsb' ? 12 : 4 }, (_, i) => ({ value: i + 1, label: `${i + 1} 位${i === 0 ? '（默认）' : ''}` }))} onChange={next => { setDepth(next); if (file) onChange({ file_id: file.id, modality, algorithm, bit_depth: next }); }} />
      {depth > 1 && purpose === 'cover' && <Alert showIcon type={depth >= 8 || modality === 'video' && depth >= 3 ? 'warning' : 'info'} message={modality === 'audio' ? '位数越高，音质损失越明显。' : '位数越高，画质损失越明显。'} />}</>}
    <MediaUpload key={`${modality}-${purpose}`} modality={modality} purpose={purpose} value={file} onBusy={value => { setUploading(value); onBusy?.(value); }} onChange={file => { setFile(file); onChange({ file_id: file.id, modality, algorithm, bit_depth: depth }); }} />
    <TypographyNote modality={modality} purpose={purpose} />
  </Space>;
}

function TypographyNote({ modality, purpose }: { modality: Modality; purpose: 'cover' | 'stego' }) {
  const formats = { audio: '支持单／双声道 16-bit PCM WAV。', video: purpose === 'stego' ? '请上传 MKV 隐写主文件。' : '输出 MKV 主文件和 MP4 预览，均不含音轨。', image: purpose === 'stego' ? '请上传原始隐写图片。' : '输出 PNG 图片。', text: '支持 UTF-8 TXT。' };
  return <p className="mm-note">{purpose === 'stego' && '算法和位数需与嵌入时一致。'}{formats[modality]}</p>;
}

function SecretFields({ value, onChange, onBusy }: { value: Secret; onChange: (value: Secret) => void; onBusy: (busy: boolean) => void }) {
  const [kind, setKind] = useState('text');
  const [file, setFile] = useState<MediaFile>();
  const [uploading, setUploading] = useState(false);
  return <Space direction="vertical" size="middle" style={{ width: '100%' }}><Segmented block disabled={uploading} value={kind} options={[{ value: 'text', label: '秘密文字' }, { value: 'file', label: '图片 / 音频 / 任意文件' }]} onChange={next => { setKind(String(next)); onChange(next === 'text' ? { text: '', compression: value.compression ?? 'auto' } : { compression: value.compression ?? 'auto' }); setFile(undefined); }} />
    {kind === 'text' ? <Input.TextArea aria-label="秘密文字" rows={5} maxLength={262144} value={value.text ?? ''} onChange={event => onChange({ ...value, text: event.target.value })} placeholder="输入秘密文字" /> : <><MediaUpload modality="file" value={file} onBusy={value => { setUploading(value); onBusy(value); }} onChange={file => { setFile(file); onChange({ secret_id: file.id, compression: value.compression ?? 'auto' }); }} /><Select aria-label="秘密文件压缩" value={value.compression ?? 'auto'} style={{ width: '100%' }} options={[{ value: 'auto', label: '自动无损压缩' }, { value: 'none', label: '不压缩' }]} onChange={compression => onChange({ ...value, compression })} /></>}
    </Space>;
}

function Recovered({ result }: { result: AnyResult }) {
  return <Card title="恢复内容" extra={<Button type="primary" icon={<DownloadOutlined />} href={downloadUrl(result.file)}>下载原始内容</Button>}>
    <Descriptions column={1} size="small" items={[{ key: 'name', label: '文件名', children: result.file?.name }, { key: 'size', label: '原始大小', children: formatBytes(result.size_bytes) }, { key: 'hash', label: 'SHA-256', children: <code className="mm-hash">{result.sha256}</code> }, { key: 'auth', label: '验证', children: <Tag color="success">{result.authenticated ? '认证解密通过' : '完整性校验通过'}</Tag> }]} />
    {typeof result.text === 'string' && <pre className="mm-text">{result.text}</pre>}
  </Card>;
}

function signalChart(data: AnyResult, spectrum = false): EChartsOption {
  const x: number[] = spectrum ? data.frequency : data.time;
  return { color: ['#19384b', '#0d8f87'], tooltip: { trigger: 'axis' }, legend: { top: 0, data: ['原始', '隐写'] }, grid: { top: 45, left: 65, right: 25, bottom: 65 }, dataZoom: [{ type: 'inside' }, { type: 'slider', bottom: 8 }],
    xAxis: { type: 'value', name: spectrum ? 'Hz' : '秒' }, yAxis: { type: 'value', name: spectrum ? 'dBFS' : 'PCM' },
    series: [['原始', spectrum ? data.original_spectrum : data.original_wave], ['隐写', spectrum ? data.stego_spectrum : data.stego_wave]].map(([name, values]) => ({ name: name as string, type: 'line', showSymbol: false, lineStyle: { width: 1 }, data: (values as number[]).map((v, i) => [x[i], v]) })) };
}

function EmbeddedResult({ result }: { result: AnyResult }) {
  const analysis = result.analysis;
  const modality: Modality = result.carrier.modality;
  const [frame, setFrame] = useState(0);
  const player = useRef<HTMLVideoElement>(null);
  const thumbnails: AnyResult[] = result.thumbnails ?? [];
  const thumbnail = thumbnails.reduce<AnyResult | undefined>((closest, item) => !closest || Math.abs(item.frame - frame) < Math.abs(closest.frame - frame) ? item : closest, undefined);
  return <Card title={<Space>嵌入完成<Tag color="success">提取验证通过</Tag></Space>} extra={<Button type="primary" icon={<DownloadOutlined />} href={downloadUrl(result.stego)}>下载隐写{modality === 'video' ? '主文件 MKV' : '文件'}</Button>}>
    <Descriptions column={{ xs: 1, sm: 2 }} size="small" items={[{ key: 'size', label: '封装包大小', children: formatBytes(result.packet_bytes) }, { key: 'alg', label: '算法', children: `${result.carrier.algorithm} · ${result.carrier.bit_depth ?? 1} 位` }]} />
    {modality === 'audio' && <><div className="mm-two"><div><label>原始 WAV</label><audio controls src={result.cover.url} /></div><div><label>隐写 WAV</label><audio controls src={result.stego.url} /></div></div><div className="mm-metrics"><Statistic title="SNR (dB)" value={analysis.identical ? '∞' : formatMetric(analysis.snr_db)} /><Statistic title="MSE" value={formatMetric(analysis.mse)} /><Statistic title="变化采样点" value={analysis.changed_samples} /><Statistic title="时长 (s)" value={formatMetric(analysis.duration)} /></div><Tabs items={[{ key: 'wave', label: '波形对比 · 第一声道', children: <ResultCharts option={signalChart(analysis)} /> }, { key: 'spectrum', label: '频谱对比 · 开头采样窗口', children: <ResultCharts option={signalChart(analysis, true)} /> }]} /></>}
    {modality === 'video' && <><Alert showIcon type="info" message="提取请使用 MKV 主文件，下方 MP4 仅供预览。" /><video className="mm-video" ref={player} controls src={result.preview?.url} onTimeUpdate={event => { if (!event.currentTarget.seeking) setFrame(Math.min(analysis.frame_count - 1, Math.floor(event.currentTarget.currentTime * analysis.fps + .001))); }} /><label>帧级时间轴 · 第 {frame} 帧 / {analysis.frame_count} · {formatMetric(frame / analysis.fps)} s</label><Slider min={0} max={analysis.frame_count - 1} value={frame} onChange={value => { setFrame(value); if (player.current) player.current.currentTime = value / analysis.fps; }} />
      <ResultCharts option={{ color: ['#0d8f87', '#19384b'], tooltip: { trigger: 'axis' }, legend: { top: 0, data: ['变化通道数', '写入位数'] }, grid: { top: 45, bottom: 65 }, xAxis: { type: 'value', name: '帧' }, yAxis: { type: 'value' }, dataZoom: [{ type: 'inside' }, { type: 'slider', bottom: 8 }], series: [{ name: '变化通道数', type: 'bar', data: analysis.frames.map((row: AnyResult) => [row.frame, row.changed_channels]) }, { name: '写入位数', type: 'line', showSymbol: false, data: analysis.frames.map((row: AnyResult) => [row.frame, row.embedded_bits]) }] }} />
      {thumbnail && <div className="mm-two"><div><label>采样预览 · 第 {thumbnail.frame} 帧</label><Image src={thumbnail.original.url} /></div><div><label>变化区域（放大 255 倍）</label><Image src={thumbnail.difference.url} /></div></div>}</>}
    {modality === 'image' && <Image src={result.stego.url} height={240} style={{ objectFit: 'contain' }} />}
    {modality === 'text' && <><div className="mm-metrics"><Statistic title="可见字符" value={analysis.visible_characters} /><Statistic title="隐藏位数" value={analysis.hidden_bits} /><Statistic title="U+200B / 0" value={analysis.zero_count} /><Statistic title="U+200C / 1" value={analysis.one_count} /></div><label>隐藏字符可视化（前 1024 位）</label><pre className="mm-text">{analysis.visualization}</pre><label>可见文本</label><pre className="mm-text">{analysis.visible_text}</pre></>}
  </Card>;
}

function EmbedExtract() {
  const [operation, setOperation] = useState('embed');
  const [carrier, setCarrier] = useState<Carrier>();
  const [secret, setSecret] = useState<Secret>({ text: '', compression: 'auto' });
  const [encryption, setEncryption] = useState('AES-256-GCM');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [carrierBusy, setCarrierBusy] = useState(false);
  const [secretBusy, setSecretBusy] = useState(false);
  const [capacity, setCapacity] = useState<AnyResult>();
  const [checking, setChecking] = useState(false);
  const job = useJob();
  const invalidate = () => { setCapacity(undefined); job.reset(); };
  const payload = { carrier, ...secret, encryption };
  const ready = Boolean(carrier && !carrierBusy && !secretBusy && (operation === 'extract' || secret.text || secret.secret_id));
  const check = async () => {
    setChecking(true);
    try { setCapacity((await api.post('/multimodal/capacity', payload, { timeout: 30 * 60_000 })).data); }
    catch (error) { void message.error(errorMessage(error)); }
    finally { setChecking(false); }
  };
  const execute = async () => {
    try { await job.run(`/multimodal/${operation}`, operation === 'embed' ? { ...payload, ...(encryption === 'none' ? {} : { password, confirm_password: confirm }) } : { carrier, ...(password ? { password } : {}) }); }
    finally { setPassword(''); setConfirm(''); }
  };
  return <div className="mm-stack"><Segmented value={operation} options={[{ value: 'embed', label: '嵌入' }, { value: 'extract', label: '提取' }]} disabled={job.loading} onChange={value => { setOperation(String(value)); invalidate(); setPassword(''); setConfirm(''); }} />
    <fieldset className="mm-fieldset" disabled={job.loading || checking}><div className="mm-two"><Card title={operation === 'embed' ? '选择载体' : '上传隐写主文件'}><CarrierInput value={carrier} purpose={operation === 'extract' ? 'stego' : 'cover'} onBusy={setCarrierBusy} onChange={value => { setCarrier(value); invalidate(); }} /></Card>
      <Card title={operation === 'embed' ? '秘密内容与加密' : '提取设置'}><Space direction="vertical" size="middle" style={{ width: '100%' }}>{operation === 'embed' && <><SecretFields value={secret} onBusy={setSecretBusy} onChange={value => { setSecret(value); invalidate(); }} /><Select aria-label="加密方式" value={encryption} style={{ width: '100%' }} onChange={value => { setEncryption(value); invalidate(); }} options={['none', 'AES-256-GCM', 'ChaCha20-Poly1305'].map(value => ({ value, label: value === 'none' ? '不加密' : value }))} /></>}
        {(operation === 'extract' || encryption !== 'none') && <Input.Password autoComplete="new-password" aria-label="加密或提取密码" value={password} onChange={event => setPassword(event.target.value)} placeholder={operation === 'extract' ? '加密内容需填写原密码' : '设置加密密码'} />}
        {operation === 'embed' && encryption !== 'none' && <Input.Password autoComplete="new-password" aria-label="确认密码" value={confirm} onChange={event => setConfirm(event.target.value)} placeholder="再次输入密码" />}
        {operation === 'embed' && <Button disabled={!ready || job.loading} loading={checking} onClick={() => void check()}>检查容量</Button>}
        {capacity && <><Progress percent={Math.min(100, Number(capacity.usage_percent.toFixed(1)))} status={capacity.fits ? 'normal' : 'exception'} /><span>原始 {formatBytes(capacity.original_bytes)} · 封装 {formatBytes(capacity.packet_bytes)} / 可用 {formatBytes(capacity.capacity_bytes)}</span>{!capacity.fits && <Alert type="error" message={capacity.suggested_bit_depth ? `容量不足，请调整为至少 ${capacity.suggested_bit_depth} 位后重新检查。` : '容量不足，请更换更大的载体或减少秘密内容。'} />}</>}
      </Space></Card></div></fieldset>
    <div><Button type="primary" size="large" icon={<PlayCircleOutlined />} loading={job.loading} disabled={!ready || checking || (operation === 'embed' && (capacity?.fits === false || (encryption !== 'none' && (!password || password !== confirm))))} onClick={() => void execute()}>{operation === 'embed' ? '开始嵌入' : '开始提取'}</Button></div>
    <JobStatus task={job.task} loading={job.loading} error={job.error} />
    {job.result && (operation === 'embed' ? <EmbeddedResult key={job.task?.id} result={job.result} /> : <Recovered result={job.result} />)}
  </div>;
}

function Cooperative() {
  const [operation, setOperation] = useState('split');
  const [carriers, setCarriers] = useState<(Carrier | undefined)[]>([undefined, undefined, undefined]);
  const [count, setCount] = useState(3);
  const [secret, setSecret] = useState<Secret>({ text: '', compression: 'auto' });
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [busy, setBusy] = useState<Record<string, boolean>>({});
  const job = useJob();
  const ready = carriers.slice(0, count).every(Boolean) && !Object.values(busy).some(Boolean) && (operation === 'combine' || (Boolean(secret.text || secret.secret_id) && password && password === confirm));
  const execute = async () => {
    try { await job.run(`/multimodal/${operation}`, { carriers: carriers.slice(0, count), ...(operation === 'split' ? { ...secret, encryption: 'AES-256-GCM', password, confirm_password: confirm } : password ? { password } : {}) }); }
    finally { setPassword(''); setConfirm(''); }
  };
  return <div className="mm-stack"><Alert showIcon type="info" message="恢复时需上传全部不同份额；每个载体都需容纳完整数据包。" />
    <Segmented value={operation} disabled={job.loading} options={[{ value: 'split', label: '拆分隐藏' }, { value: 'combine', label: '组合恢复' }]} onChange={value => { setOperation(String(value)); job.reset(); }} />
    <fieldset disabled={job.loading} className="mm-fieldset"><Select disabled={Object.values(busy).some(Boolean)} aria-label="协同载体数量" value={count} options={[{ value: 2, label: '2 个载体' }, { value: 3, label: '3 个载体' }]} onChange={value => { setCount(value); setCarriers([undefined, undefined, undefined]); job.reset(); }} />
      <div className="mm-carriers">{Array.from({ length: count }, (_, index) => <Card key={`${count}-${index}`} title={`载体 ${index + 1}`}><CarrierInput allowText={false} purpose={operation === 'combine' ? 'stego' : 'cover'} initialModality={(['image', 'audio', 'video'] as Modality[])[index]} value={carriers[index]} onBusy={value => setBusy(current => ({ ...current, [String(index)]: value }))} onChange={value => { setCarriers(current => current.map((item, i) => i === index ? value : item)); job.reset(); }} /></Card>)}</div>
      <Card title={operation === 'split' ? '秘密内容与密码' : '恢复密码'}><Space direction="vertical" size="middle" style={{ width: '100%' }}>{operation === 'split' && <SecretFields value={secret} onChange={value => { setSecret(value); job.reset(); }} onBusy={value => setBusy(current => ({ ...current, secret: value }))} />}<Input.Password autoComplete="new-password" aria-label="协同加密密码" value={password} onChange={event => setPassword(event.target.value)} placeholder="输入加密密码" />{operation === 'split' && <Input.Password autoComplete="new-password" aria-label="协同确认密码" value={confirm} onChange={event => setConfirm(event.target.value)} placeholder="确认密码" />}</Space></Card>
    </fieldset><div><Button type="primary" size="large" loading={job.loading} disabled={!ready} onClick={() => void execute()}>{operation === 'split' ? '生成协同隐写文件' : '组合恢复'}</Button></div><JobStatus task={job.task} loading={job.loading} error={job.error} />
    {job.result && (operation === 'combine' ? <Recovered result={job.result} /> : <>{job.result.results.map((result: AnyResult, index: number) => <Card key={index} title={`份额 ${index + 1} · ${labels[result.carrier.modality as Modality]}`}><Button href={downloadUrl(result.stego)} icon={<DownloadOutlined />}>下载份额文件</Button><Tag color="success">提取验证通过</Tag></Card>)}</>)}
  </div>;
}

function AttackPanel() {
  const [carrier, setCarrier] = useState<Carrier>();
  const [busy, setBusy] = useState(false);
  const [attack, setAttack] = useState('noise');
  const [password, setPassword] = useState('');
  const job = useJob();
  const attacks = carrier?.modality === 'video' ? [{ value: 'compression', label: 'H.264 有损压缩 · CRF 22' }, { value: 'crop', label: '时间裁剪 · 删除开头 20% 帧' }] : carrier?.modality === 'text' ? [{ value: 'strip_zero_width', label: '清理零宽字符' }] : [{ value: 'noise', label: '高斯噪声 · σ=8 PCM 单位' }, { value: 'resample', label: '重采样 · 降至一半后恢复' }, { value: 'compression', label: 'AAC 压缩 · 64 kbps 后解码' }];
  const execute = async () => { try { await job.run('/multimodal/attack', { carrier, attack, ...(password ? { password } : {}) }); } finally { setPassword(''); } };
  return <div className="mm-stack"><Alert showIcon type="info" message="请上传可正常提取的隐写文件。" />
    <fieldset disabled={job.loading} className="mm-fieldset"><div className="mm-two"><Card title="待攻击的隐写文件"><CarrierInput purpose="stego" value={carrier} onBusy={setBusy} onChange={value => { setCarrier(value); setAttack(value?.modality === 'video' ? 'compression' : value?.modality === 'text' ? 'strip_zero_width' : 'noise'); job.reset(); }} /></Card><Card title="攻击设置"><Space direction="vertical" size="middle" style={{ width: '100%' }}><Select aria-label="攻击方式" value={attack} options={attacks} style={{ width: '100%' }} onChange={value => { setAttack(value); job.reset(); }} /><Input.Password autoComplete="new-password" aria-label="攻击恢复验证密码" value={password} onChange={event => setPassword(event.target.value)} placeholder="加密文件请填写原密码" />{carrier?.modality === 'image' && <Alert type="warning" message="图像攻击请前往「安全性分析」。" />}</Space></Card></div></fieldset>
    <div><Button type="primary" size="large" loading={job.loading} disabled={!carrier || busy || carrier.modality === 'image'} onClick={() => void execute()}>开始攻击测试</Button></div><JobStatus task={job.task} loading={job.loading} error={job.error} />
    {job.result && <Card title="攻击结果" extra={<Space><Button href={downloadUrl(job.result.file)} icon={<DownloadOutlined />}>下载攻击文件</Button>{job.task && <Button href={exportUrl(job.task.id)}>导出报告</Button>}</Space>}><div className="mm-metrics"><Statistic title="比特恢复率" value={job.result.bit_recovery_rate * 100} precision={2} suffix="%" /><Statistic title="字节恢复率" value={job.result.byte_recovery_rate * 100} precision={2} suffix="%" /><Statistic title="BER" value={job.result.ber} precision={4} /><Statistic title="完整恢复" value={job.result.exact_match ? '通过' : '失败'} /></div><Alert showIcon type={job.result.payload_recoverable ? 'success' : 'warning'} message={job.result.payload_recoverable ? '原始内容已完整恢复' : '未完整恢复，请检查文件和密码。'} /><p className="mm-note">{job.result.parameters}</p><ResultCharts height={220} option={{ color: ['#0d8f87'], xAxis: { type: 'category', data: ['比特恢复率', '字节恢复率'] }, yAxis: { type: 'value', min: 0, max: 100, name: '%' }, series: [{ type: 'bar', barMaxWidth: 60, data: [job.result.bit_recovery_rate * 100, job.result.byte_recovery_rate * 100] }] }} />{job.result.recovered && <Recovered result={job.result.recovered} />}</Card>}
  </div>;
}

export default function Multimodal() {
  return <div className="mm-page"><div className="page-heading"><div><h1>多模态隐写</h1></div></div>
    <Tabs destroyInactiveTabPane items={[{ key: 'embed', label: '多模态嵌入与提取', children: <EmbedExtract /> }, { key: 'cooperative', label: '多载体协同', children: <Cooperative /> }, { key: 'attack', label: '安全攻防', children: <AttackPanel /> }]} />
  </div>;
}
