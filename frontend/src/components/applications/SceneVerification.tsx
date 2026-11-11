import { useEffect, useState } from 'react';
import { Alert, Button, Input, Tag, message } from 'antd';
import { CopyOutlined, DownloadOutlined } from '@ant-design/icons';
import { JobStatus } from '../JobStatus';
import { useJob } from '../../hooks/useJob';
import type { CompletedImageExperiment } from '../../hooks/useExperiments';
import { downloadUrl } from '../../services/api';

export function SceneVerification({ experiment, expectedText, copyright, onExpired }: {
  experiment: CompletedImageExperiment; expectedText?: string; copyright: boolean; onExpired: () => void;
}) {
  const [password, setPassword] = useState('');
  const [key, setKey] = useState('');
  const job = useJob();
  useEffect(() => { if (job.failureCode === 'file_not_found') onExpired(); }, [job.failureCode]);
  const needsKey = experiment.algorithm === 'scatter_lsb';
  const ready = (!experiment.encrypted || Boolean(password)) && (!needsKey || Boolean(key.trim()));
  const successful = job.result?.status === 'ok' && job.result.data_type === 'text';
  const recovered = successful ? String(job.result?.text ?? '') : '';
  const match = expectedText === undefined ? undefined : recovered === expectedText;
  const verify = async () => {
    if (!ready || job.loading) return;
    await job.run('/extract', { image_id: experiment.stego.id, mode: 'manual', algorithm: experiment.algorithm,
      params: { ...experiment.params, ...(needsKey ? { key } : {}) }, ...(password ? { password } : {}) });
    setPassword(''); setKey('');
  };
  const failures: Record<string, string> = {
    password_required: '请重新填写加密密码后验证。', authentication_failed: '认证失败，请检查密码或使用未经修改的图片。',
    extraction_failed: '提取失败，请检查散布位置密钥与图片是否完整。', invalid_packet: '信息包损坏或参数不一致，请重新生成或上传原始结果图片。',
    no_match: '未找到有效信息，请检查图片与算法。',
  };
  return <section className="scene-verification" aria-labelledby="scene-verification-heading">
    <h2 id="scene-verification-heading">{copyright ? '版权信息验证' : '影像信息恢复'}</h2>
    {(needsKey || experiment.encrypted) && <div className="scene-fields scene-fields--two">
      {needsKey && <label><span>散布位置密钥</span><Input.Password aria-label="验证散布位置密钥" value={key} onChange={event => { setKey(event.target.value); job.reset(); }} autoComplete="new-password" disabled={job.loading} /></label>}
      {experiment.encrypted && <label><span>解密密码</span><Input.Password aria-label="验证解密密码" value={password} onChange={event => { setPassword(event.target.value); job.reset(); }} autoComplete="new-password" disabled={job.loading} /></label>}
    </div>}
    {(needsKey || experiment.encrypted) && <p className="scene-note">验证前请重新填写所需凭据。</p>}
    <div className="result-actions"><Button data-testid="scene-verify" disabled={!ready} loading={job.loading} onClick={() => void verify()}>{successful ? '重新验证' : copyright ? '提取并核对版权信息' : '提取影像说明'}</Button></div>
    <JobStatus task={job.task} loading={job.loading} error={job.failureCode === 'file_not_found' ? undefined : job.error} />
    {job.failureCode === 'file_not_found' && <Alert type="error" showIcon message="图片已失效，请重新上传并生成结果。" action={<Button onClick={onExpired}>重新上传</Button>} />}
    {job.result && !successful && <Alert type="warning" showIcon message={failures[String(job.result.status)] ?? '未能恢复文本，请检查设置后重试。'} />}
    {successful && <div className="scene-verification__content" data-testid="scene-verification-result">
      <div className="scene-result-state"><Tag color={match === false ? 'error' : 'success'}>{match === undefined ? '信息已提取' : match ? '与写入内容一致' : '与写入内容不一致'}</Tag><Tag>{job.result?.authenticated ? '认证解密通过' : '未加密数据'}</Tag></div>
      <Input.TextArea aria-label={copyright ? '提取出的版权信息' : '恢复的影像说明'} readOnly autoSize={{ minRows: 5, maxRows: 12 }} value={recovered} />
      {match === undefined && <p className="scene-note">原始表单未保留，请核对本次提取内容。</p>}
      <div className="result-actions"><Button icon={<CopyOutlined />} onClick={() => { void navigator.clipboard.writeText(recovered).then(() => message.success('已复制信息')).catch(() => message.info('请在文本框中选择并复制')); }}>复制信息</Button>{job.result?.file && <Button href={downloadUrl(job.result.file)} icon={<DownloadOutlined />}>下载文本</Button>}</div>
    </div>}
  </section>;
}
