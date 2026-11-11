import { useEffect, useMemo, useRef, useState } from 'react';
import { Alert, Button, Card, Collapse, Descriptions, Input, Space, Tag } from 'antd';
import { CheckCircleOutlined, DownloadOutlined, ExperimentOutlined } from '@ant-design/icons';
import { useLocation, useNavigate } from 'react-router-dom';
import { ImageUploader } from '../components/ImageUploader';
import { AlgorithmSelector } from '../components/AlgorithmSelector';
import { defaultParams, ParameterPanel } from '../components/ParameterPanel';
import { EncryptionSettings } from '../components/EncryptionSettings';
import { ImageComparison } from '../components/ImageComparison';
import { MetricCards } from '../components/MetricCards';
import { JobStatus } from '../components/JobStatus';
import { RobustnessPanel } from '../components/analysis/RobustnessPanel';
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

export type ApplicationScene = 'medical' | 'robustness' | 'copyright';
const definitions = {
  medical: { title: '医疗影像隐私保护', intro: '将检查说明和影像编号写入图像，验证信息恢复与画质变化。', image: '/images/applications/medical-imaging.webp' },
  robustness: { title: '鲁棒性传输测试', intro: '模拟压缩、噪声和尺寸变化，观察隐藏信息经过处理后能否恢复。', image: '/images/applications/robust-transmission.webp' },
  copyright: { title: '数字版权与水印验证', intro: '将作品署名和权利声明写入图像，再提取核对版权标识。', image: '/images/applications/copyright-protection.webp' },
} as const;

function SceneHeading({ scene }: { scene: ApplicationScene }) {
  const definition = definitions[scene];
  return <header className="scene-heading"><div><span className="scene-eyebrow">应用场景</span><h1>{definition.title}</h1><p>{definition.intro}</p></div><img src={definition.image} alt="" width={360} height={220} /></header>;
}

const fieldConfig = {
  medical: [
    { key: 'description', label: '检查说明', placeholder: '例如：胸部影像，2026 年复查', required: true, multiline: true },
    { key: 'identifier', label: '影像编号', placeholder: '可选', required: false },
    { key: 'note', label: '备注信息', placeholder: '可选', required: false },
  ],
  copyright: [
    { key: 'author', label: '作者或机构', placeholder: '例如：影像工作室', required: true },
    { key: 'work', label: '作品名称', placeholder: '输入作品名称', required: true },
    { key: 'rights', label: '权利声明', placeholder: '例如：版权所有，未经许可不得转载', required: true, multiline: true },
    { key: 'date', label: '创建日期', placeholder: '', required: false, type: 'date' },
    { key: 'reference', label: '作品编号或联系方式', placeholder: '可选', required: false },
  ],
} as const;

type EmbedScene = 'medical' | 'copyright';

function makeText(scene: EmbedScene, fields: Record<string, string>) {
  const labels = scene === 'medical' ? { description: '检查说明', identifier: '影像编号', note: '备注信息' } : { author: '作者或机构', work: '作品名称', rights: '权利声明', date: '创建日期', reference: '作品编号或联系方式' };
  return Object.entries(labels).map(([key, label]) => fields[key]?.trim() ? `${label}：${fields[key].trim()}` : '').filter(Boolean).join('\n');
}

function EmbedScene({ scene }: { scene: EmbedScene }) {
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
  const fieldsForm = <div className="scene-fields">{fieldConfig[scene].map(field => <label key={field.key}><span>{field.label}{field.required && <em aria-hidden="true"> *</em>}</span>{('multiline' in field && field.multiline) ? <Input.TextArea aria-label={field.label} aria-required={field.required} rows={3} maxLength={2000} value={fields[field.key] ?? ''} onChange={event => update(field.key, event.target.value)} placeholder={field.placeholder} disabled={job.loading} /> : <Input aria-label={field.label} aria-required={field.required} type={'type' in field ? field.type : undefined} maxLength={240} value={fields[field.key] ?? ''} onChange={event => update(field.key, event.target.value)} placeholder={field.placeholder} disabled={job.loading} />}</label>)}</div>;
  const settings = <div className="scene-settings"><AlgorithmSelector algorithms={algorithms} filter={item => item.mode !== 'bitplanes' && item.id !== 'lsb_msb'} value={algorithm} onChange={changeAlgorithm} disabled={job.loading} /><ParameterPanel algorithm={selected} value={params} onChange={value => { setParams(value); setCompleted(undefined); job.reset(); }} disabled={job.loading} /><EncryptionSettings value={encryption} onChange={value => { setEncryption(value); setCompleted(undefined); job.reset(); }} password={password} onPasswordChange={setPassword} confirm={confirm} onConfirmChange={setConfirm} disabled={job.loading} /></div>;
  return <div className="application-studio"><SceneHeading scene={scene} /><HandoffNotice visible={handoff.expired} onClose={handoff.dismiss} />{scene === 'copyright' && <p className="scene-boundary">本实验用于验证版权信息写入与提取流程，不等同于完整的 DRM 授权系统。</p>}{algorithmsError && <Alert type="error" showIcon message={algorithmsError} />}{completed ? <Card className="scene-result" title={<span><CheckCircleOutlined /> 场景实验完成</span>} extra={<Button type="primary" href={downloadUrl(completed.stego)} icon={<DownloadOutlined />}>下载{scene === 'copyright' ? '水印图片' : '隐写图片'}</Button>}><div className="scene-result-state"><Tag color={completed.recovery.exact_match ? 'success' : 'warning'}>{completed.recovery.exact_match ? '写入后恢复检查通过' : '恢复结果需检查'}</Tag><span>本次使用：{algorithms.find(item => item.id === completed.algorithm)?.name ?? completed.algorithm}</span></div><ImageComparison original={fileUrl(completed.cover)} stego={fileUrl(completed.stego)} /><MetricCards metrics={completed.quality} /><SceneVerification experiment={completed} expectedText={submittedText.current} copyright={scene === 'copyright'} onExpired={() => { experiments.invalidate(completed.id); setCompleted(undefined); setCover(undefined); setFields({}); setPassword(''); setConfirm(''); setParams(completed.params); submittedText.current = undefined; job.reset(); handoff.expire(); }} /><Space wrap className="scene-result-actions"><Button onClick={() => navigate('/extract', { state: { experimentId: completed.id } })}>信息提取</Button><Button onClick={() => navigate('/analysis?tab=performance&panel=quality', { state: { experimentId: completed.id } })}>图像质量分析</Button><Button onClick={() => navigate('/applications/robustness', { state: { experimentId: completed.id } })}>传输测试</Button>{completed.taskId && <Button href={exportUrl(completed.taskId)} icon={<DownloadOutlined />}>导出报告</Button>}<Button onClick={() => { setParams(completed.params); setCover(completed.cover); setFields({}); setPassword(''); setConfirm(''); submittedText.current = undefined; setCompleted(undefined); job.reset(); handoff.detach(); }}>修改信息</Button></Space><details className="result-details-disclosure"><summary>实验详情</summary><Descriptions size="small" column={{ xs: 1, sm: 2, lg: 3 }} items={[{ key: 'algorithm', label: '算法', children: algorithms.find(item => item.id === completed.algorithm)?.name ?? completed.algorithm }, { key: 'cover', label: '载体图片', children: completed.cover.name }, { key: 'size', label: '写入大小', children: formatBytes(completed.packet_bytes) }, { key: 'elapsed', label: '嵌入耗时', children: `${(completed.timings.embed_seconds * 1000).toFixed(1)} ms` }]} /></details></Card> : <div className="scene-form-grid"><Card title={scene === 'copyright' ? '01 上传作品' : '01 上传影像'}><ImageUploader hideLabel label="用于写入信息的载体图片" value={cover} onChange={changeCover} onLoadingChange={setUploading} role="cover" disabled={job.loading} /><p className="scene-note">图片仅用于本次隐写实验。</p></Card><Card title={scene === 'copyright' ? '02 填写版权信息' : '02 填写影像说明'}>{fieldsForm}<Collapse ghost className="scene-advanced" items={[{ key: 'advanced', label: '高级设置 · 算法、参数与加密', children: settings }]} /><div className="scene-summary"><span>实际写入</span><strong>{capacity ? formatBytes(capacity.used_bytes) : '—'}</strong><span>容量使用</span><strong>{capacityLoading ? '计算中' : capacity ? `${Number(capacity.usage_percent ?? 0).toFixed(1)}%` : '上传图片并填写信息后计算'}</strong></div></Card><div className="scene-form-footer"><span className="scene-note">{!cover ? '请先上传载体图片' : !requiredFields ? '请填写带 * 的场景信息' : encryptionProblem ?? (!validAlgorithm ? '请在高级设置中补全算法参数' : capacityLoading || checkedPayload !== payload ? '正在检查图像容量…' : capacityError ? '容量检查失败，请重试' : capacity && !capacity.fits ? '容量不足，请减少信息或更换图片' : '')}</span><Button data-testid="scene-embed-submit" className="scene-submit" type="primary" size="large" icon={<ExperimentOutlined />} disabled={!ready || algorithmsLoading} loading={job.loading} onClick={() => void run()}>{scene === 'copyright' ? '生成版权水印' : '开始影像隐写'}</Button></div><JobStatus task={job.task} loading={job.loading} error={job.error} />{capacityError && <Alert className="scene-capacity-error" type="error" showIcon message={capacityError} action={<Button onClick={() => setCapacityAttempt(value => value + 1)}>重试容量检查</Button>} />}</div>}{scene === 'medical' && <p className="scene-boundary scene-boundary--footer">这是图像隐写演示，不用于医学诊断或临床影像管理。</p>}</div>;
}

export default function ApplicationStudio({ scene }: { scene: ApplicationScene }) {
  if (scene === 'robustness') return <div className="application-studio"><SceneHeading scene={scene} /><RobustnessPage /></div>;
  return <EmbedScene key={scene} scene={scene} />;
}

function RobustnessPage() {
  const { algorithms, loading, error } = useAlgorithms();
  return <div className="scene-robustness">{error && <Alert type="error" showIcon message={error} />}{loading ? <Card loading /> : <RobustnessPanel algorithms={algorithms} guided />}</div>;
}
