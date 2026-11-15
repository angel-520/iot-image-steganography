import { useEffect, useMemo, useRef, useState } from 'react';
import { Alert, Button, Card, Collapse, Descriptions, Image, Input, Progress, Segmented, Space, Tabs, Tag, Upload } from 'antd';
import { DownloadOutlined, InboxOutlined, UploadOutlined } from '@ant-design/icons';
import { ImageUploader } from '../components/ImageUploader';
import { ImageComparison } from '../components/ImageComparison';
import { EncryptionSettings } from '../components/EncryptionSettings';
import { JobStatus } from '../components/JobStatus';
import { MetricCards } from '../components/MetricCards';
import { HandoffNotice, useExperimentHandoff } from '../hooks/useExperiments';
import { useJob } from '../hooks/useJob';
import { api, downloadUrl, errorMessage, exportUrl, formatBytes } from '../services/api';
import type { AnyResult, Encryption, UploadedImage } from '../types';
import '../styles/application-studio.css';
import '../styles/restoration.css';

function Integrity({ metrics }: { metrics: AnyResult }) {
  const secret = metrics.secret_verified === true && metrics.packet_verified === true;
  const rgb = metrics.rgb_verified === true && metrics.original_rgb_sha256 === metrics.restored_rgb_sha256;
  return <Space wrap className="rdh-integrity" data-testid="rdh-integrity">
    <Tag color={secret ? 'success' : 'error'}>秘密数据完整恢复：{secret ? '通过' : '未通过'}</Tag>
    <Tag color={rgb ? 'success' : 'error'}>原始RGB像素精确恢复：{rgb ? '通过' : '未通过'}</Tag>
  </Space>;
}

function EmbedResult({ result, taskId, onExtract }: { result: AnyResult; taskId?: string; onExtract: (image: UploadedImage) => void }) {
  const m = result.metrics;
  return <Card title="RDH 嵌入完成" className="rdh-result" extra={<Button type="primary" icon={<DownloadOutlined />} href={downloadUrl(result.stego)}>下载 RDH PNG</Button>}>
    <Integrity metrics={m} />
    <Descriptions size="small" column={{ xs: 1, sm: 2, lg: 3 }} items={[
      { key: 'size', label: '图像尺寸', children: `${m.width} × ${m.height}` },
      { key: 'secret', label: '秘密载荷', children: formatBytes(m.secret_bytes) },
      { key: 'packet', label: 'ISTG 数据包', children: formatBytes(m.packet_bytes) },
      { key: 'bits', label: '实际嵌入', children: `${m.embedded_bits.toLocaleString()} bit` },
      { key: 'capacity', label: '有效秘密容量', children: formatBytes(m.secret_capacity_bytes) },
      { key: 'bpp', label: '嵌入率', children: m.bpp.toFixed(5) + ' bpp' },
      { key: 'auxiliary', label: '算法辅助数据', children: formatBytes(m.auxiliary_bytes) },
      { key: 'time', label: '嵌入耗时', children: m.embedding_seconds.toFixed(3) + ' 秒' },
      { key: 'file', label: 'RDH 文件大小', children: formatBytes(m.stego_bytes) },
    ]} />
    <ImageComparison original={result.source.url} stego={result.stego.url} />
    <MetricCards metrics={m.quality} />
    <Space wrap className="rdh-result-actions"><Button onClick={() => onExtract(result.stego)}>提取及恢复此文件</Button>
      {taskId && <Button href={exportUrl(taskId)}>导出报告</Button>}</Space>
    <details className="result-details-disclosure"><summary>容量与完整性详情</summary>
      <Descriptions size="small" column={1} items={[
        { key: 'theory', label: '理论峰值容量', children: `${m.theoretical_capacity_bits.toLocaleString()} bit` },
        { key: 'safe', label: '安全可用容量', children: `${m.effective_capacity_bits.toLocaleString()} bit（${formatBytes(m.packet_capacity_bytes)} ISTG 数据包）` },
        { key: 'overhead', label: '封装与加密开销', children: formatBytes(m.packet_overhead_bytes) },
        { key: 'map', label: '边界位置图', children: formatBytes(m.location_map_bytes) },
        { key: 'verify', label: '完整往返验证', children: m.verification_seconds.toFixed(3) + ' 秒' },
        { key: 'secretHash', label: '秘密数据 SHA-256', children: <code className="restoration-hash">{m.secret_sha256}</code> },
        { key: 'originalHash', label: '原始 RGB SHA-256', children: <code className="restoration-hash">{m.original_rgb_sha256}</code> },
        { key: 'restoredHash', label: '恢复 RGB SHA-256', children: <code className="restoration-hash">{m.restored_rgb_sha256}</code> },
      ]} />
      <p className="scene-note">有效秘密容量已扣除当前 ISTG 封装和加密开销。辅助数据单独存放于 PNG，不占像素载荷槽，也不包含秘密正文或原图残差。</p>
    </details>
  </Card>;
}

function Embed({ onExtract }: { onExtract: (image: UploadedImage) => void }) {
  const handoff = useExperimentHandoff();
  const [image, setImage] = useState<UploadedImage | undefined>(handoff.experiment?.stego);
  const [kind, setKind] = useState('text');
  const [text, setText] = useState('');
  const [secretFile, setSecretFile] = useState<AnyResult>();
  const [uploading, setUploading] = useState(false);
  const [secretUploading, setSecretUploading] = useState(false);
  const [secretError, setSecretError] = useState<string>();
  const [encryption, setEncryption] = useState<Encryption>('none');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [checkedKey, setCheckedKey] = useState('');
  const [attempt, setAttempt] = useState(0);
  const job = useJob(), capacity = useJob();
  const { run: capacityRun, reset: capacityReset } = capacity;
  const submitted = useRef(false);
  const secretBusy = useRef(false);
  const payload = useMemo(() => image && (kind === 'text' ? text.length > 0 : secretFile) ? {
    image_id: image.id, ...(kind === 'text' ? { text } : { secret_id: secretFile!.id }), encryption,
  } : undefined, [image?.id, kind, text, secretFile?.id, encryption]);
  const key = payload ? JSON.stringify(payload) : '';
  useEffect(() => {
    capacityReset(); setCheckedKey('');
    if (!payload) return;
    let active = true;
    const timer = window.setTimeout(async () => {
      const result = await capacityRun('/rdh/capacity', payload);
      if (active && result) setCheckedKey(key);
    }, 500);
    return () => { active = false; window.clearTimeout(timer); };
  }, [key, attempt, capacityRun, capacityReset]);
  const changed = () => job.reset();
  const encryptionProblem = encryption === 'none' ? undefined : !password ? '请填写加密密码' : password.length > 1024 ? '密码过长' : password !== confirm ? '两次输入的密码不一致' : undefined;
  const ready = Boolean(payload && checkedKey === key && capacity.result?.fits && !encryptionProblem && !uploading && !secretUploading);
  const run = async () => {
    if (!ready || submitted.current || job.loading) return;
    submitted.current = true;
    try { await job.run('/rdh/embed', { ...payload, ...(encryption === 'none' ? {} : { password, confirm_password: confirm }) }); }
    finally { submitted.current = false; setPassword(''); setConfirm(''); }
  };
  return <div className="scene-stack"><HandoffNotice visible={handoff.expired} onClose={handoff.dismiss} />
    <div className="restoration-input-grid"><Card title="原始图像"><ImageUploader label="用于可逆嵌入的原始图像" role="cover" value={image}
      disabled={job.loading} onLoadingChange={setUploading} onChange={value => { setImage(value); changed(); handoff.detach(); handoff.dismiss(); }} /></Card>
      <Card title="秘密信息"><Segmented aria-label="秘密数据类型" value={kind} disabled={job.loading} options={[{ value: 'text', label: '秘密文本' }, { value: 'file', label: '秘密文件' }]}
        onChange={value => { setKind(value); changed(); }} />
        <div className="rdh-secret-input">{kind === 'text' ? <Input.TextArea aria-label="秘密文本内容" placeholder="输入中文、英文或其他 UTF-8 文本" rows={5} maxLength={262144} showCount
          value={text} disabled={job.loading} onChange={event => { setText(event.target.value); changed(); }} /> : <Upload showUploadList={false} disabled={job.loading || secretUploading} beforeUpload={async file => {
          if (secretBusy.current) return Upload.LIST_IGNORE;
          changed(); setSecretError(undefined);
          if (file.size > 4 * 1024 ** 2) { setSecretFile(undefined); setSecretError('秘密文件不能超过 4 MiB'); return Upload.LIST_IGNORE; }
          secretBusy.current = true; setSecretUploading(true);
          try { const form = new FormData(); form.append('file', file); setSecretFile((await api.post('/rdh/uploads', form)).data); }
          catch (error) { setSecretFile(undefined); setSecretError(errorMessage(error)); }
          finally { secretBusy.current = false; setSecretUploading(false); }
          return Upload.LIST_IGNORE;
        }}><Button icon={<UploadOutlined />} loading={secretUploading}>{secretFile ? `${secretFile.name} · ${formatBytes(secretFile.size_bytes)}` : '选择秘密文件（最大 4 MiB）'}</Button></Upload>}</div>
        {secretError && <Alert type="error" message={secretError} showIcon />}
        <Collapse ghost className="scene-advanced" items={[{ key: 'encryption', label: '可选加密', children: <EncryptionSettings value={encryption} disabled={job.loading}
          onChange={value => { setEncryption(value); setPassword(''); setConfirm(''); changed(); }} password={password} confirm={confirm}
          onPasswordChange={value => { setPassword(value); changed(); }} onConfirmChange={value => { setConfirm(value); changed(); }} /> }]} />
        <div className="rdh-capacity" data-testid="rdh-capacity">
          {capacity.loading ? <p>正在计算实际容量…</p> : checkedKey === key && capacity.result && payload ? <>
            <Space wrap><strong>有效秘密容量 {formatBytes(capacity.result.secret_capacity_bytes)}</strong><Tag color={capacity.result.fits ? 'success' : 'error'}>{capacity.result.fits ? '容量充足' : '容量不足'}</Tag></Space>
            <Progress percent={Math.min(100, capacity.result.usage_percent)} showInfo={false} status={capacity.result.fits ? 'normal' : 'exception'} />
            <p>实际 ISTG 数据包 {formatBytes(capacity.result.packet_bytes)} / 可用像素容量 {formatBytes(capacity.result.packet_capacity_bytes)}</p>
          </> : <p>上传原图并填写秘密信息后，显示准确可用容量。</p>}
          {capacity.error && <Alert showIcon type="error" message={capacity.error} action={<Button onClick={() => setAttempt(value => value + 1)}>重试</Button>} />}
        </div>
        <Button type="primary" size="large" disabled={!ready} loading={job.loading} onClick={() => void run()}>嵌入并验证</Button>
        {encryptionProblem && <p className="scene-note">{encryptionProblem}</p>}
        <p className="scene-note">输出为 RDH PNG。请原样保存文件，以保留像素载荷和必要的恢复参数。</p>
      </Card></div>
    <JobStatus task={job.task} loading={job.loading} error={job.error} />
    {job.result && <EmbedResult result={job.result} taskId={job.task?.id} onExtract={onExtract} />}
  </div>;
}

function Extract({ initial }: { initial?: UploadedImage }) {
  const [file, setFile] = useState<File>();
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string>();
  const [loadingFile, setLoadingFile] = useState(false);
  const job = useJob();
  const { reset } = job;
  const submitted = useRef(false);
  useEffect(() => {
    if (!initial) return;
    const controller = new AbortController(); setLoadingFile(true);
    fetch(initial.url, { signal: controller.signal }).then(async response => {
      if (!response.ok) throw new Error('本次 RDH 图像已失效，请上传下载的 PNG。');
      const blob = await response.blob();
      if (!controller.signal.aborted) { setFile(new File([blob], initial.name, { type: 'image/png' })); setPassword(''); reset(); }
    }).catch(error => { if (!controller.signal.aborted) setError(errorMessage(error)); })
      .finally(() => { if (!controller.signal.aborted) setLoadingFile(false); });
    return () => controller.abort();
  }, [initial?.id, reset]);
  const run = async () => {
    if (!file || submitted.current || loadingFile || job.loading) return;
    submitted.current = true;
    const form = new FormData(); form.append('file', file); if (password) form.append('password', password);
    try { await job.run('/rdh/extract', form); }
    finally { submitted.current = false; setPassword(''); }
  };
  const m = job.result?.metrics;
  return <div className="scene-stack"><Card title="上传 RDH 图像"><Upload.Dragger accept=".png,image/png" showUploadList={false} multiple={false} disabled={job.loading || loadingFile} beforeUpload={async selected => {
    reset(); setError(undefined); setFile(undefined); setPassword('');
    if (selected.size > 96 * 1024 ** 2) { setError('RDH PNG 不能超过 96 MiB'); return false; }
    const header = new Uint8Array(await selected.slice(0, 8).arrayBuffer());
    if (header.length !== 8 || ![137, 80, 78, 71, 13, 10, 26, 10].every((value, i) => header[i] === value)) { setError('请选择完整的 RDH PNG 文件'); return false; }
    setFile(selected); return false;
  }}><p className="ant-upload-drag-icon"><InboxOutlined /></p><p>{loadingFile ? '正在读取 RDH 图像' : file?.name ?? '点击或拖入 RDH PNG'}</p><p className="ant-upload-hint">{file ? formatBytes(file.size) : '无需原图 · 最大 96 MiB'}</p></Upload.Dragger>
    <div className="rdh-extract-actions"><Input.Password aria-label="RDH 解密口令" autoComplete="off" maxLength={1024} placeholder="已加密时填写解密口令" value={password} disabled={job.loading}
      onChange={event => { setPassword(event.target.value); reset(); }} /><Button type="primary" size="large" disabled={!file || loadingFile} loading={job.loading} onClick={() => void run()}>提取及恢复</Button></div>
    {error && <Alert showIcon type="error" message={error} />}
  </Card><JobStatus task={job.task} loading={job.loading} error={job.error} />
    {job.result && <Card title="提取及恢复完成" className="rdh-result" extra={<Button icon={<DownloadOutlined />} type="primary" href={downloadUrl(job.result.restored)}>下载恢复图像 PNG</Button>}>
      <Integrity metrics={m} />
      <Descriptions size="small" column={{ xs: 1, sm: 2, lg: 3 }} items={[
        { key: 'size', label: '恢复尺寸', children: `${m.width} × ${m.height}` },
        { key: 'secret', label: '秘密数据', children: formatBytes(m.secret_bytes) },
        { key: 'file', label: '恢复 PNG', children: formatBytes(m.restored_png_bytes) },
      ]} />
      {job.result.data_type === 'text' && <Input.TextArea aria-label="提取出的秘密文本" readOnly value={job.result.text} autoSize={{ minRows: 3, maxRows: 10 }} />}
      <Space wrap className="rdh-result-actions"><Button href={downloadUrl(job.result.secret)} icon={<DownloadOutlined />}>{job.result.data_type === 'text' ? '下载秘密文本' : `下载秘密文件：${job.result.secret.name}`}</Button>
        {job.task && <Button href={exportUrl(job.task.id)}>导出恢复报告</Button>}</Space>
      <div className="restoration-result-image"><Image src={job.result.restored.url} alt="像素精确恢复的原始 RGB 图像" /></div>
      <details className="result-details-disclosure"><summary>SHA-256 校验记录</summary><Descriptions column={1} size="small" items={[
        { key: 'secret', label: '秘密数据', children: <code className="restoration-hash">{m.secret_sha256}</code> },
        { key: 'original', label: '原始 RGB', children: <code className="restoration-hash">{m.original_rgb_sha256}</code> },
        { key: 'restored', label: '恢复 RGB', children: <code className="restoration-hash">{m.restored_rgb_sha256}</code> },
      ]} /></details>
    </Card>}
  </div>;
}

export default function ImageRestoration() {
  const [tab, setTab] = useState('embed');
  const [initial, setInitial] = useState<UploadedImage>();
  return <div className="application-studio restoration-page"><header className="scene-heading"><div><span className="scene-eyebrow">应用场景</span><h1>可逆图像隐写</h1>
    <p>在图像像素中隐藏秘密信息，提取后精确恢复原始 RGB 像素。</p></div><img src="/images/applications/robust-transmission.webp" alt="" width={360} height={220} /></header>
    <Tabs activeKey={tab} onChange={setTab} destroyOnHidden items={[
      { key: 'embed', label: '嵌入秘密信息', children: <Embed onExtract={image => { setInitial(image); setTab('extract'); }} /> },
      { key: 'extract', label: '提取及恢复', children: <Extract initial={initial} /> },
    ]} />
  </div>;
}
