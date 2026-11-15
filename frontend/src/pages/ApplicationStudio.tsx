import { useEffect, useMemo, useRef, useState } from 'react';
import { Alert, Button, Card, Collapse, Descriptions, Input, Space, Tag } from 'antd';
import { CheckCircleOutlined, DownloadOutlined, EditOutlined } from '@ant-design/icons';
import { useLocation, useNavigate } from 'react-router-dom';
import { ImageUploader } from '../components/ImageUploader';
import { AlgorithmSelector } from '../components/AlgorithmSelector';
import { defaultParams, ParameterPanel } from '../components/ParameterPanel';
import { EncryptionSettings } from '../components/EncryptionSettings';
import { ImageComparison } from '../components/ImageComparison';
import { MetricCards } from '../components/MetricCards';
import { JobStatus } from '../components/JobStatus';
import { SceneVerification } from '../components/applications/SceneVerification';
import { useAlgorithms } from '../hooks/useAlgorithms';
import { useDebounced } from '../hooks/useDebounced';
import { HandoffNotice, useExperimentHandoff, useExperiments } from '../hooks/useExperiments';
import type { CompletedImageExperiment } from '../hooks/useExperiments';
import { useJob } from '../hooks/useJob';
import { api, downloadUrl, errorCode, errorMessage, exportUrl, fileUrl, formatBytes } from '../services/api';
import { encryptionError } from '../services/parameters';
import type { AlgorithmParams, AnyResult, Encryption, UploadedImage } from '../types';
import '../styles/application-studio.css';

function SceneHeading() {
  return <header className="scene-heading"><div><span className="scene-eyebrow">应用场景</span><h1>影像信息嵌入</h1><p>将文字信息嵌入图像，按需提取与核验。</p></div><img src="/images/applications/medical-imaging.webp" alt="" width={360} height={220} /></header>;
}
const fieldConfig = { medical: [
  { key: 'description', label: '信息内容', placeholder: '输入要随图像携带的信息', required: true, multiline: true },
  { key: 'identifier', label: '记录编号', placeholder: '可选', required: false },
  { key: 'note', label: '备注信息', placeholder: '可选', required: false },
] } as const;
function makeText(scene: 'medical', fields: Record<string, string>) {
  return fieldConfig[scene].map(field => fields[field.key]?.trim() ? `${field.label}：${fields[field.key].trim()}` : '').filter(Boolean).join('\n');
}
export default function ApplicationStudio() {
  const scene = 'medical' as const;
  const handoff = useExperimentHandoff();
  const navigate = useNavigate();
  const location = useLocation();
  const experiments = useExperiments();
  const { algorithms, loading: algorithmsLoading, error: algorithmsError } = useAlgorithms();
  const initial = handoff.experiment?.sourceScene === scene ? handoff.experiment : undefined;
  const [cover, setCover] = useState<UploadedImage | undefined>(initial?.cover);
  const [fields, setFields] = useState<Record<string, string>>({});
  const [algorithm, setAlgorithm] = useState(initial?.algorithm ?? 'sequential_lsb');
  const [params, setParams] = useState<AlgorithmParams>(initial?.params ?? {});
  const [encryption, setEncryption] = useState<Encryption>(initial?.cipher ?? 'none');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [completed, setCompleted] = useState<CompletedImageExperiment | undefined>(initial);
  const [uploading, setUploading] = useState(false);
  const selected = algorithms.find(item => item.id === algorithm);
  const job = useJob();
  const submitted = useRef<string>();
  const submittedText = useRef<string>();
  const text = useMemo(() => makeText(scene, fields), [scene, fields]);
  const payload = useMemo(() => cover && text ? { cover_id: cover.id, data_type: 'text' as const, text, algorithm, params, encryption } : null, [cover?.id, text, algorithm, params, encryption]);
  const checkedPayload = useDebounced(payload);
  const [capacity, setCapacity] = useState<AnyResult>();
  const [capacityError, setCapacityError] = useState<string>();
  const [capacityLoading, setCapacityLoading] = useState(false);
  const [capacityAttempt, setCapacityAttempt] = useState(0);

  useEffect(() => { if (selected) setParams(current => ({ ...defaultParams(selected), ...current })); }, [selected]);
  useEffect(() => {
    if (!checkedPayload || checkedPayload !== payload) { setCapacity(undefined); setCapacityError(undefined); setCapacityLoading(false); return; }
    const abort = new AbortController(); setCapacityLoading(true); setCapacityError(undefined);
    void api.post<AnyResult>('/capacity', checkedPayload, { signal: abort.signal }).then(({ data }) => { if (!abort.signal.aborted) setCapacity(data); }).catch(error => { if (!abort.signal.aborted) { setCapacity(undefined); setCapacityError(errorMessage(error)); if (errorCode(error) === 'file_not_found') setCover(undefined); } }).finally(() => { if (!abort.signal.aborted) setCapacityLoading(false); });
    return () => abort.abort();
  }, [checkedPayload, payload, capacityAttempt]);
  useEffect(() => {
    const result = job.result;
    if (result?.status !== 'ok' || !job.task?.id || submitted.current === job.task.id) return;
    submitted.current = job.task.id; submittedText.current = text;
    const snapshot = experiments.save(result, job.task.id, { dataType: 'text', imageMode: 'file', secretBits: 2, sourceScene: scene });
    setCompleted(snapshot); navigate(location.pathname, { replace: true, state: { experimentId: snapshot.id } });
  }, [job.result, job.task, experiments, navigate, location.pathname, scene, text]);
  useEffect(() => { if (job.failureCode === 'file_not_found') { setCover(undefined); setCompleted(undefined); handoff.expire(); job.reset(); } }, [job.failureCode]);

  const requiredFields = fieldConfig[scene].filter(field => field.required).every(field => Boolean(fields[field.key]?.trim()));
  const validAlgorithm = Boolean(selected?.available && (!selected.parameters.some(item => item.name === 'key' && item.required) || Boolean(params.key)));
  const encryptionProblem = encryptionError(encryption, password, confirm);
  const ready = Boolean(cover && requiredFields && validAlgorithm && !encryptionProblem && capacity?.fits && checkedPayload === payload && !capacityLoading && !uploading && !job.loading);
  const update = (key: string, value: string) => { setFields(current => ({ ...current, [key]: value })); setCompleted(undefined); job.reset(); };
  const changeCover = (value?: UploadedImage) => { setCover(value); setCompleted(undefined); job.reset(); handoff.detach(); handoff.dismiss(); };
  const changeAlgorithm = (value: string) => { setAlgorithm(value); setParams(defaultParams(algorithms.find(item => item.id === value))); setCompleted(undefined); job.reset(); };
  const run = async () => { if (!payload || !ready) return; const result = await job.run('/embed', { ...payload, ...(encryption === 'none' ? {} : { password, confirm_password: confirm }) }); if (result?.status === 'ok') setPassword(''); setConfirm(''); };
  const fieldsForm = <div className="scene-fields">{fieldConfig[scene].map(field => <label key={field.key}><span>{field.label}{field.required && <em aria-hidden="true"> *</em>}</span>{('multiline' in field && field.multiline) ? <Input.TextArea aria-label={field.label} aria-required={field.required} rows={3} maxLength={2000} value={fields[field.key] ?? ''} onChange={event => update(field.key, event.target.value)} placeholder={field.placeholder} disabled={job.loading} /> : <Input aria-label={field.label} aria-required={field.required} maxLength={240} value={fields[field.key] ?? ''} onChange={event => update(field.key, event.target.value)} placeholder={field.placeholder} disabled={job.loading} />}</label>)}</div>;
  const settings = <div className="scene-settings"><AlgorithmSelector algorithms={algorithms} filter={item => item.mode !== 'bitplanes' && item.id !== 'lsb_msb'} value={algorithm} onChange={changeAlgorithm} disabled={job.loading} /><ParameterPanel algorithm={selected} value={params} onChange={value => { setParams(value); setCompleted(undefined); job.reset(); }} disabled={job.loading} /><EncryptionSettings value={encryption} onChange={value => { setEncryption(value); setCompleted(undefined); job.reset(); }} password={password} onPasswordChange={setPassword} confirm={confirm} onConfirmChange={setConfirm} disabled={job.loading} /></div>;
  return <div className="application-studio"><SceneHeading /><HandoffNotice visible={handoff.expired} onClose={handoff.dismiss} />{algorithmsError && <Alert type="error" showIcon message={algorithmsError} />}{completed ? <Card className="scene-result" title={<span><CheckCircleOutlined /> 信息嵌入完成</span>} extra={<Button type="primary" href={downloadUrl(completed.stego)} icon={<DownloadOutlined />}>下载隐写图片</Button>}><div className="scene-result-state"><Tag color={completed.recovery.exact_match ? 'success' : 'warning'}>{completed.recovery.exact_match ? '写入后恢复检查通过' : '恢复结果需检查'}</Tag><span>本次使用：{algorithms.find(item => item.id === completed.algorithm)?.name ?? completed.algorithm}</span></div><ImageComparison original={fileUrl(completed.cover)} stego={fileUrl(completed.stego)} /><MetricCards metrics={completed.quality} /><SceneVerification experiment={completed} expectedText={submittedText.current} onExpired={() => { experiments.invalidate(completed.id); setCompleted(undefined); setCover(undefined); setFields({}); setPassword(''); setConfirm(''); setParams(completed.params); submittedText.current = undefined; job.reset(); handoff.expire(); }} /><Space wrap className="scene-result-actions"><Button onClick={() => navigate('/extract', { state: { experimentId: completed.id } })}>信息提取</Button><Button onClick={() => navigate('/analysis?tab=performance&panel=quality', { state: { experimentId: completed.id } })}>图像质量分析</Button><Button onClick={() => navigate('/applications/robustness', { state: { experimentId: completed.id } })}>可逆图像隐写</Button>{completed.taskId && <Button href={exportUrl(completed.taskId)} icon={<DownloadOutlined />}>导出报告</Button>}<Button onClick={() => { setParams(completed.params); setCover(completed.cover); setFields({}); setPassword(''); setConfirm(''); submittedText.current = undefined; setCompleted(undefined); job.reset(); handoff.detach(); }}>修改信息</Button></Space><details className="result-details-disclosure"><summary>处理详情</summary><Descriptions size="small" column={{ xs: 1, sm: 2, lg: 3 }} items={[{ key: 'algorithm', label: '算法', children: algorithms.find(item => item.id === completed.algorithm)?.name ?? completed.algorithm }, { key: 'cover', label: '载体图片', children: completed.cover.name }, { key: 'size', label: '写入大小', children: formatBytes(completed.packet_bytes) }, { key: 'elapsed', label: '嵌入耗时', children: `${(completed.timings.embed_seconds * 1000).toFixed(1)} ms` }]} /></details></Card> : <div className="scene-form-grid"><Card title="01 选择图像"><ImageUploader hideLabel label="用于写入信息的载体图片" value={cover} onChange={changeCover} onLoadingChange={setUploading} role="cover" disabled={job.loading} /></Card><Card title="02 填写信息">{fieldsForm}<Collapse ghost className="scene-advanced" items={[{ key: 'advanced', label: '高级设置 · 算法、参数与加密', children: settings }]} /><div className="scene-summary"><span>实际写入</span><strong>{capacity ? formatBytes(capacity.used_bytes) : '—'}</strong><span>容量使用</span><strong>{capacityLoading ? '计算中' : capacity ? `${Number(capacity.usage_percent ?? 0).toFixed(1)}%` : '上传图片并填写信息后计算'}</strong></div></Card><div className="scene-form-footer"><span className="scene-note">{!cover ? '请先上传载体图片' : !requiredFields ? '请填写必填信息' : encryptionProblem ?? (!validAlgorithm ? '请在高级设置中补全算法参数' : capacityLoading || checkedPayload !== payload ? '正在检查图像容量…' : capacityError ? '容量检查失败，请重试' : capacity && !capacity.fits ? '容量不足，请减少信息或更换图片' : '')}</span><Button data-testid="scene-embed-submit" className="scene-submit" type="primary" size="large" icon={<EditOutlined />} disabled={!ready || algorithmsLoading} loading={job.loading} onClick={() => void run()}>嵌入信息</Button></div><JobStatus task={job.task} loading={job.loading} error={job.error} />{capacityError && <Alert className="scene-capacity-error" type="error" showIcon message={capacityError} action={<Button onClick={() => setCapacityAttempt(value => value + 1)}>重试容量检查</Button>} />}</div>}</div>;
}
