import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { HandoffNotice, useExperimentHandoff, useExperiments } from '../hooks/useExperiments';
import type { CompletedImageExperiment } from '../hooks/useExperiments';
import { Alert, Button, Card, Descriptions, Input, InputNumber, Segmented, Switch, Tag, message } from 'antd';
import { ArrowLeftOutlined, ArrowRightOutlined, CheckOutlined, CheckCircleOutlined, DownloadOutlined, FileImageOutlined } from '@ant-design/icons';
import { ImageUploader } from '../components/ImageUploader';
import { ImageViewer } from '../components/ImageViewer';
import { AlgorithmSelector } from '../components/AlgorithmSelector';
import { defaultParams, ParameterPanel } from '../components/ParameterPanel';
import { EncryptionSettings } from '../components/EncryptionSettings';
import { CapacityIndicator } from '../components/CapacityIndicator';
import { ImageComparison } from '../components/ImageComparison';
import { MetricCards } from '../components/MetricCards';
import { JobStatus } from '../components/JobStatus';
import { useAlgorithms } from '../hooks/useAlgorithms';
import { useDebounced } from '../hooks/useDebounced';
import { useJob } from '../hooks/useJob';
import { api, downloadUrl, errorCode, errorMessage, exportUrl, fileUrl, formatBytes } from '../services/api';
import { encryptionError } from '../services/parameters';
import type { AlgorithmParams, AnyResult, Encryption, UploadedImage } from '../types';
import '../styles/embed.css';

const steps = [
  { title: '准备信息', heading: '图片与秘密信息' },
  { title: '选择算法', heading: '算法设置' },
  { title: '设置加密', heading: '加密设置' },
  { title: '确认生成', heading: '确认设置' },
  { title: '查看结果', heading: '处理结果' },
];

export default function Embed() {
  const navigate = useNavigate();
  const handoff = useExperimentHandoff();
  const experiments = useExperiments();
  const [completed, setCompleted] = useState<CompletedImageExperiment | undefined>(handoff.experiment);
  const savedTask = useRef<string>();
  const { algorithms, loading: algorithmsLoading, error: algorithmsError } = useAlgorithms();
  const [cover, setCover] = useState<UploadedImage | undefined>(handoff.experiment?.cover);
  const [secret, setSecret] = useState<UploadedImage>();
  const [coverUploading, setCoverUploading] = useState(false);
  const [secretUploading, setSecretUploading] = useState(false);
  const [dataType, setDataType] = useState<'text' | 'image'>(handoff.experiment?.dataType ?? 'text');
  const [imageMode, setImageMode] = useState<'file' | 'bitplanes'>(handoff.experiment?.imageMode ?? 'file');
  const [secretBits, setSecretBits] = useState(handoff.experiment?.secretBits ?? 2);
  const [text, setText] = useState('');
  const [algorithm, setAlgorithm] = useState(handoff.experiment?.algorithm ?? 'sequential_lsb');
  const [params, setParams] = useState<AlgorithmParams>(handoff.experiment?.params ?? {});
  const [advanced, setAdvanced] = useState(false);
  const [encryption, setEncryption] = useState<Encryption>(handoff.experiment?.cipher ?? 'none');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [step, setStep] = useState(handoff.experiment ? 4 : 0);
  const [furthest, setFurthest] = useState(handoff.experiment ? 4 : 0);
  const [direction, setDirection] = useState<'forward' | 'backward'>('forward');
  const [capacity, setCapacity] = useState<AnyResult>();
  const [capacityLoading, setCapacityLoading] = useState(false);
  const [capacityError, setCapacityError] = useState<string>();
  const [checkedBody, setCheckedBody] = useState<unknown>();
  const heading = useRef<HTMLHeadingElement>(null);
  const submitted = useRef(false);
  const job = useJob();
  useEffect(() => {
    if (job.result?.status !== 'ok' || !job.task?.id || savedTask.current === job.task.id) return;
    savedTask.current = job.task.id;
    const snapshot = experiments.save(job.result, job.task.id, { dataType, imageMode, secretBits });
    setCompleted(snapshot);
    navigate('/embed', { replace: true, state: { experimentId: snapshot.id } });
  }, [job.result, job.task, experiments, navigate, dataType, imageMode, secretBits]);
  useEffect(() => {
    if (job.failureCode !== 'file_not_found') return;
    setCover(undefined); setCompleted(undefined); setStep(0); setFurthest(0);
    handoff.expire(); job.reset();
  }, [job.failureCode]);
  const selected = algorithms.find(item => item.id === algorithm);
  const uploading = coverUploading || secretUploading;
  const secretSize = dataType === 'text' ? new TextEncoder().encode(text).length : secret?.size_bytes;
  const clearPasswords = () => { setPassword(''); setConfirm(''); };

  useEffect(() => {
    if (selected) setParams(current => ({ ...defaultParams(selected), ...current }));
  }, [selected]);

  const invalidate = (from: number, clearCredentials = true) => {
    setFurthest(current => Math.min(current, from));
    job.reset(); setCompleted(undefined); handoff.detach(); handoff.dismiss();
    if (clearCredentials) clearPasswords();
  };
  const changeAlgorithm = (id: string) => {
    setAlgorithm(id);
    setParams(defaultParams(algorithms.find(item => item.id === id)));
    invalidate(1);
  };
  const selectType = (type: 'text' | 'image') => {
    setDataType(type);
    invalidate(0);
    if (type === 'text' && algorithm === 'lsb_msb') {
      setImageMode('file');
      setAlgorithm('sequential_lsb');
      setParams(defaultParams(algorithms.find(item => item.id === 'sequential_lsb')));
    }
  };
  const selectImageMode = (mode: 'file' | 'bitplanes') => {
    setImageMode(mode);
    const id = mode === 'bitplanes' ? 'lsb_msb' : 'sequential_lsb';
    setAlgorithm(id);
    setParams(defaultParams(algorithms.find(item => item.id === id)));
    invalidate(0);
  };
  const ready = Boolean(!uploading && cover && (dataType === 'text' ? text.length > 0 : secret));
  const body = useMemo(() => ready ? {
    cover_id: cover?.id, data_type: dataType, text: dataType === 'text' ? text : undefined,
    secret_id: dataType === 'image' ? secret?.id : undefined, image_mode: dataType === 'image' ? imageMode : 'file',
    secret_bits: secretBits, algorithm, params, encryption,
  } : null, [ready, cover?.id, dataType, text, secret?.id, imageMode, secretBits, algorithm, params, encryption]);
  const debounced = useDebounced(body);
  useEffect(() => {
    if (!debounced) {
      setCapacity(undefined); setCheckedBody(undefined); setCapacityError(undefined); setCapacityLoading(false);
      return;
    }
    const abort = new AbortController();
    setCapacityLoading(true); setCapacityError(undefined);
    void api.post<AnyResult>('/capacity', debounced, { signal: abort.signal })
      .then(({ data }) => { if (!abort.signal.aborted) { setCapacity(data); setCheckedBody(debounced); } })
      .catch(error => { if (!abort.signal.aborted) { setCapacity(undefined); setCheckedBody(undefined); setCapacityError(errorMessage(error)); if (errorCode(error) === 'file_not_found') { setCover(undefined); setCompleted(undefined); setStep(0); setFurthest(0); handoff.expire(); } } })
      .finally(() => { if (!abort.signal.aborted) setCapacityLoading(false); });
    return () => abort.abort();
  }, [debounced]);

  useEffect(() => { heading.current?.focus({ preventScroll: true }); }, [step]);
  const passwordsValid = !encryptionError(encryption, password, confirm);
  const keyValid = !selected?.parameters.some(parameter => parameter.name === 'key' && parameter.required) || Boolean(params.key);
  const checkingCapacity = capacityLoading || body !== debounced || (Boolean(body) && checkedBody !== body && !capacityError);
  const capacityFits = Boolean(body && checkedBody === body && !capacityLoading && !capacityError && capacity?.fits);
  const algorithmReady = Boolean(ready && selected?.available && keyValid && capacityFits);
  const canEmbed = algorithmReady && passwordsValid;
  const canContinue = step === 0 ? ready : step === 1 ? algorithmReady : canEmbed;
  const goTo = (target: number) => {
    if (job.loading || submitted.current || uploading) return;
    setDirection(target < step ? 'backward' : 'forward');
    setStep(target);
    if (!window.matchMedia('(prefers-reduced-motion: reduce)').matches) window.scrollTo({ top: 0, behavior: 'smooth' });
    else window.scrollTo({ top: 0 });
  };
  const canVisit = (target: number) => {
    if (job.loading || uploading || target > furthest) return false;
    if (target === 4) return Boolean(completed);
    if (target === 0) return true;
    if (!ready) return false;
    if (target >= 2 && !algorithmReady) return false;
    return target < 3 || passwordsValid;
  };
  const next = () => {
    if (!canContinue || step >= 3 || job.loading || uploading) return;
    setFurthest(current => Math.max(current, step + 1));
    goTo(step + 1);
  };
  const execute = async () => {
    if (!body || !canEmbed || submitted.current) return;
    submitted.current = true;
    try {
      const result = await job.run('/embed', { ...body, ...(encryption === 'none' ? {} : { password, confirm_password: confirm }) });
      if (result?.status === 'ok') {
        setDirection('forward'); setFurthest(4); setStep(4);
        window.scrollTo({ top: 0 });
        void message.success('隐写图片已生成。');
      } else if (encryption !== 'none') {
        setDirection('backward'); setStep(2); setFurthest(2);
      }
    } finally {
      clearPasswords();
      submitted.current = false;
    }
  };
  const stepHint = uploading ? '正在上传图片…'
    : step === 0 ? (!cover && !(dataType === 'text' ? text : secret) ? '添加秘密信息和载体图片后继续' : !cover ? '请添加载体图片' : !ready ? '请添加秘密信息' : '')
    : checkingCapacity ? '正在更新容量…'
    : step === 1 && !keyValid ? '请填写散布位置密钥。'
    : capacityError ? '请检查算法参数后重试。'
    : !capacityFits ? '当前容量不足，请调整设置。'
    : step === 2 && !passwordsValid ? encryptionError(encryption, password, confirm)
    : '';
  const resultAlgorithm = algorithms.find(item => item.id === completed?.algorithm)?.name ?? completed?.algorithm;

  return <div className="embed-studio">
    <div className="page-heading embed-page-heading"><h1>信息隐藏</h1></div>
    <HandoffNotice visible={handoff.expired} onClose={handoff.dismiss} />
    {algorithmsError && <Alert type="error" showIcon message={algorithmsError} />}
    <nav className="embed-wizard-rail" aria-label="信息隐藏步骤">
      {steps.map((item, index) => <button key={item.title} type="button" className={`embed-step-link ${index === step ? 'is-current' : ''} ${index < step ? 'is-complete' : ''}`} aria-current={index === step ? 'step' : undefined} disabled={!canVisit(index)} onClick={() => goTo(index)}>
        <span className="embed-step-index">{index < step ? <CheckOutlined /> : String(index + 1).padStart(2, '0')}</span><span className="embed-step-copy"><strong>{item.title}</strong></span>
      </button>)}
    </nav>
    <section key={step} className={`embed-current-step embed-enter-${direction}`} aria-labelledby="embed-step-heading" aria-busy={job.loading}>
      <div className={`embed-stage-heading${step === 1 ? ' has-controls' : ''}`}><div><h2 className="sr-only" id="embed-step-heading" ref={heading} tabIndex={-1} data-testid="wizard-current-step">{steps[step].heading}</h2></div>{step === 1 && <div className="advanced-toggle"><span>高级参数</span><Switch aria-label="高级模式" checked={advanced} onChange={setAdvanced} disabled={job.loading} /></div>}</div>
      {step === 0 && <div className="embed-input-grid">
        <Card title="秘密信息" className="embed-stage-card"><Segmented block aria-label="秘密信息类型" value={dataType} onChange={value => selectType(value as 'text' | 'image')} options={[{ value: 'text', label: '文字隐写' }, { value: 'image', label: '图片隐写' }]} />
          {dataType === 'text' ? <div className="secret-input"><label className="field-label">秘密文本</label><Input.TextArea data-testid="secret-text" aria-label="秘密文本" rows={5} maxLength={262144} value={text} onChange={event => { setText(event.target.value); invalidate(0); }} placeholder="输入你想隐藏的文字…" /><div className="input-caption"><span>{formatBytes(secretSize)}</span></div></div>
            : <div className="secret-input"><label className="field-label">图片处理模式</label><Segmented block value={imageMode} onChange={value => selectImageMode(value as 'file' | 'bitplanes')} options={[{ value: 'file', label: '原文件字节' }, { value: 'bitplanes', label: 'LSB-MSB 位平面' }]} />{imageMode === 'bitplanes' && <p className="embed-field-note">恢复图片会有一定失真。</p>}<ImageUploader hideLabel label="秘密图片" value={secret} onChange={value => { setSecret(value); invalidate(0); }} role="secret" compact onLoadingChange={setSecretUploading} />{imageMode === 'bitplanes' && <div className="parameter-field"><label className="field-label">秘密图片保留高位数</label><InputNumber min={1} max={4} value={secretBits} onChange={value => { setSecretBits(value ?? 2); invalidate(0); }} aria-label="秘密图片保留高位数" /></div>}</div>}
        </Card>
        <Card title="载体图片" className="embed-stage-card"><ImageUploader hideLabel label="载体图片" value={cover} onChange={value => { setCover(value); invalidate(0); }} role="cover" onLoadingChange={setCoverUploading} /></Card>
      </div>}
      {step > 0 && step < 4 && <div className="embed-configuration-grid"><div className="embed-primary-panel">
        {step === 1 && <Card title="隐写算法" className="embed-stage-card" loading={algorithmsLoading}><AlgorithmSelector label="选择算法" algorithms={algorithms} value={algorithm} onChange={changeAlgorithm} filter={item => dataType === 'image' && imageMode === 'bitplanes' ? item.id === 'lsb_msb' : item.mode !== 'bitplanes' && item.id !== 'lsb_msb'} /><ParameterPanel algorithm={selected} value={params} onChange={value => { setParams(value); invalidate(1); }} advanced={advanced} /><div className="embed-file-format"><FileImageOutlined /><span>输出图片</span><strong className="embed-serif">PNG</strong><Tag color="green">无损格式</Tag></div></Card>}
        {step === 2 && <Card title="加密设置" className="embed-stage-card"><EncryptionSettings value={encryption} onChange={value => { setEncryption(value); invalidate(2); }} password={password} onPasswordChange={value => { setPassword(value); invalidate(2, false); }} confirm={confirm} onConfirmChange={value => { setConfirm(value); invalidate(2, false); }} disabled={job.loading} />{encryption !== 'none' && !passwordsValid && <p className="embed-field-note">{encryptionError(encryption, password, confirm)}</p>}</Card>}
        {step === 3 && <Card title="本次设置" className="embed-stage-card"><Descriptions column={1} size="middle" items={[
          { key: 'type', label: '秘密信息', children: dataType === 'text' ? `文字 · ${formatBytes(secretSize)}` : `${imageMode === 'file' ? '原始图片文件' : `图片高位 · ${secretBits} 位`} · ${secret?.name}` },
          { key: 'cover', label: '载体图片', children: cover?.name },
          { key: 'algorithm', label: '隐写算法', children: <span className="embed-serif">{selected?.name}</span> },
          ...(selected?.parameters.map(parameter => ({ key: `param-${parameter.name}`, label: parameter.label, children: parameter.name === 'key' ? '已设置' : String(params[parameter.name] ?? parameter.default ?? '—') })) ?? []),
          { key: 'encryption', label: '加密方式', children: <span className="embed-serif">{encryption === 'none' ? '不加密' : encryption}</span> },
          { key: 'output', label: '输出格式', children: <span className="embed-serif">PNG</span> },
          { key: 'size', label: '写入大小', children: formatBytes(capacity?.used_bytes) },
        ]} />{dataType === 'text' && <div className="embed-secret-review"><span>秘密文本</span><p>{text}</p></div>}</Card>}
        <JobStatus task={job.task} loading={job.loading} error={job.error} />
      </div><aside className="embed-summary-column"><Card title="载体预览" className="embed-cover-summary"><ImageViewer src={cover?.url} title={cover?.name} /><div className="embed-cover-details"><span>{cover?.width} × {cover?.height}</span><span>{cover?.format} · {formatBytes(cover?.size_bytes)}</span></div></Card>{step === 1 ? <Card title="容量预估"><CapacityIndicator value={capacity} loading={checkingCapacity} error={capacityError} /></Card> : <div className="embed-selection-summary"><span>已选择的隐写算法</span><strong className="embed-serif">{selected?.name}</strong><small>{dataType === 'text' ? '文字隐写' : imageMode === 'file' ? '图片文件隐写' : '图片位平面替换'}</small></div>}</aside></div>}
      {step === 4 && completed && <Card className="embed-result-card">
        <div className="result-heading"><div><h2 className="success-title"><CheckCircleOutlined aria-hidden="true" /> 隐写完成</h2><Tag color={completed.recovery.exact_match ? 'success' : 'warning'}>{completed.recovery.exact_match ? '逐字节一致' : '请检查恢复结果'}</Tag></div>
          <Button data-testid="result-download" href={downloadUrl(completed.stego)} icon={<DownloadOutlined />} type="primary">下载隐写图片</Button>
        </div>
        <div className="result-actions"><Button onClick={() => navigate('/extract', { state: { experimentId: completed.id } })}>提取验证</Button><Button onClick={() => navigate('/analysis?tab=performance&panel=quality', { state: { experimentId: completed.id } })}>安全分析</Button>{completed.taskId && <Button href={exportUrl(completed.taskId)} icon={<DownloadOutlined />}>导出报告</Button>}</div>
        <ImageComparison original={fileUrl(completed.cover)} stego={fileUrl(completed.stego)} />
        <MetricCards metrics={completed.quality} />
        <details className="result-details-disclosure"><summary>处理详情</summary><Descriptions column={{ xs: 1, sm: 2, lg: 3 }} size="small" items={[
          { key: 'algorithm', label: '算法', children: resultAlgorithm },
          { key: 'size', label: '写入包大小', children: formatBytes(completed.packet_bytes) },
          { key: 'time', label: '嵌入耗时', children: `${(completed.timings.embed_seconds * 1000).toFixed(1)} ms` },
        ]} /></details>
      </Card>}
      <div className="embed-wizard-actions">{step > 0 && step < 4 ? <Button data-testid="wizard-back" size="large" icon={<ArrowLeftOutlined />} disabled={job.loading} onClick={() => goTo(step - 1)}>上一步</Button> : <span className="embed-action-caption"></span>}<div className="embed-next-actions">{step < 4 && <span className="embed-action-hint" aria-live="polite">{stepHint}</span>}{step < 3 ? <Button data-testid="wizard-next" type="primary" size="large" icon={<ArrowRightOutlined />} iconPosition="end" disabled={!canContinue || job.loading} onClick={next}>下一步</Button> : step === 3 ? <Button data-testid="embed-submit" type="primary" size="large" icon={<ArrowRightOutlined />} iconPosition="end" loading={job.loading} disabled={!canEmbed || job.loading} onClick={() => void execute()}>开始隐写</Button> : <Button data-testid="wizard-edit" size="large" icon={<ArrowLeftOutlined />} onClick={() => { invalidate(0); setText(''); setSecret(undefined); setParams(current => Object.fromEntries(Object.entries(current).filter(([key]) => key !== 'key'))); goTo(0); }}>修改设置</Button>}</div></div>
    </section>
  </div>;
}
