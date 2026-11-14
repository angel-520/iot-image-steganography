import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Card, Descriptions, Image, InputNumber, Segmented, Slider, Space, Tabs, Tag, Upload } from 'antd';
import { DownloadOutlined, InboxOutlined } from '@ant-design/icons';
import { ImageUploader } from '../components/ImageUploader';
import { JobStatus } from '../components/JobStatus';
import { MetricCards } from '../components/MetricCards';
import { useJob } from '../hooks/useJob';
import { HandoffNotice, useExperimentHandoff } from '../hooks/useExperiments';
import { downloadUrl, exportUrl, formatBytes } from '../services/api';
import type { AnyResult, UploadedImage } from '../types';
import '../styles/application-studio.css';
import '../styles/restoration.css';

function RecoveryResult({ result, taskId }: { result: AnyResult; taskId?: string }) {
  const [view, setView] = useState('restored');
  const metrics = result.metrics;
  const image = view === 'source' ? result.source : view === 'preview' ? result.preview : result.restored;
  return <Card title={<Space>恢复完成<Tag color={metrics.exact_match ? 'success' : 'error'}>{metrics.exact_match ? 'RGB 像素一致' : '像素校验未通过'}</Tag></Space>}
    extra={<Space wrap>{result.carrier && <Button type="primary" icon={<DownloadOutlined />} href={downloadUrl(result.carrier)}>下载可恢复图像 PNG</Button>}
      <Button icon={<DownloadOutlined />} href={downloadUrl(result.restored)}>下载恢复图像</Button></Space>}>
    <Descriptions size="small" column={{ xs: 1, sm: 2, lg: 3 }} items={[
      { key: 'dimensions', label: '图像尺寸', children: `${metrics.width} × ${metrics.height}` },
      { key: 'original', label: '原始文件', children: formatBytes(metrics.original_file_bytes) },
      { key: 'jpeg', label: 'JPEG 预览', children: formatBytes(metrics.preview_jpeg_bytes) },
      { key: 'layer', label: '内嵌恢复信息', children: formatBytes(metrics.recovery_layer_bytes) },
      { key: 'carrier', label: '可恢复图像总大小', children: formatBytes(metrics.carrier_bytes) },
      { key: 'restored', label: '恢复 PNG', children: formatBytes(metrics.restored_png_bytes) },
      { key: 'ratio', label: '最终体积 / 原始文件', children: typeof metrics.size_ratio === 'number' ? metrics.size_ratio.toFixed(2) + ' 倍' : '—' },
    ]} />
    {result.source && <Segmented aria-label="图像恢复对比" value={view} onChange={setView} options={[{ value: 'source', label: '原始图像' }, { value: 'preview', label: '压缩预览' }, { value: 'restored', label: '恢复图像' }]} />}
    <div className="restoration-result-image"><Image src={image?.url} alt={view === 'preview' ? 'JPEG 压缩预览' : '恢复图像'} /></div>
    {metrics.preview_quality && <div><h3>压缩预览质量</h3><MetricCards metrics={metrics.preview_quality} /></div>}
    <details className="result-details-disclosure"><summary>恢复校验</summary>
      <Descriptions size="small" column={1} items={[
        { key: 'originalHash', label: '原图 RGB SHA-256', children: <code className="restoration-hash">{metrics.original_pixel_sha256}</code> },
        { key: 'restoredHash', label: '恢复 RGB SHA-256', children: <code className="restoration-hash">{metrics.restored_pixel_sha256}</code> },
      ]} />
      {taskId && <Button href={exportUrl(taskId)}>导出恢复报告</Button>}
    </details>
  </Card>;
}

function Prepare() {
  const handoff = useExperimentHandoff();
  const [image, setImage] = useState<UploadedImage | undefined>(handoff.experiment?.stego);
  const [quality, setQuality] = useState(70);
  const [uploading, setUploading] = useState(false);
  const job = useJob();
  const submitted = useRef(false);
  useEffect(() => { if (job.failureCode === 'file_not_found') { setImage(undefined); handoff.expire(); } }, [job.failureCode]);
  const run = async () => {
    if (!image || uploading || job.loading || submitted.current) return;
    submitted.current = true;
    try { await job.run('/restoration/prepare', { image_id: image.id, jpeg_quality: quality }); }
    finally { submitted.current = false; }
  };
  return <div className="scene-stack">
    <HandoffNotice visible={handoff.expired} onClose={handoff.dismiss} />
    <div className="restoration-input-grid">
      <Card title="选择原始图像"><ImageUploader label="待恢复的原始图像" value={image} role="cover" disabled={job.loading} onLoadingChange={setUploading} onChange={value => { setImage(value); job.reset(); handoff.detach(); handoff.dismiss(); }} /></Card>
      <Card title="压缩设置"><label className="field-label">JPEG 预览质量</label>
        <div className="restoration-quality"><Slider aria-label="JPEG 预览质量滑块" min={1} max={95} value={quality} disabled={job.loading} onChange={value => { setQuality(value); job.reset(); }} />
          <InputNumber aria-label="JPEG 预览质量" min={1} max={95} precision={0} value={quality} disabled={job.loading} onChange={value => { setQuality(value ?? 70); job.reset(); }} /></div>
        <p className="scene-note">质量越低，预览细节损失越明显。生成的 PNG 同时包含恢复信息。</p>
        <Button type="primary" size="large" disabled={!image || uploading} loading={job.loading} onClick={() => void run()}>生成可恢复图像</Button>
      </Card>
    </div>
    <JobStatus task={job.task} loading={job.loading} error={job.error} />
    {job.result && <RecoveryResult key={job.task?.id} result={job.result} taskId={job.task?.id} />}
  </div>;
}

function Restore() {
  const [file, setFile] = useState<File>();
  const [fileError, setFileError] = useState<string>();
  const job = useJob();
  const submitted = useRef(false);
  const run = async () => {
    if (!file || submitted.current || job.loading) return;
    submitted.current = true;
    const form = new FormData(); form.append('file', file);
    try { await job.run('/restoration/restore', form); }
    finally { submitted.current = false; }
  };
  return <div className="scene-stack"><Card title="上传可恢复图像">
    <Upload.Dragger accept=".png" multiple={false} disabled={job.loading} showUploadList={false} beforeUpload={selected => {
      job.reset(); setFileError(undefined);
      if (selected.size > 96 * 1024 ** 2) { setFile(undefined); setFileError('文件不能超过 96 MiB'); return false; }
      setFile(selected); return false;
    }}><p className="ant-upload-drag-icon"><InboxOutlined /></p><p>{file?.name ?? '点击或拖入带恢复信息的 PNG'}</p><p className="ant-upload-hint">{file ? formatBytes(file.size) : '最大 96 MiB'}</p></Upload.Dragger>
    {fileError && <Alert type="error" showIcon message={fileError} />}
    <div className="scene-actions"><Button type="primary" size="large" disabled={!file} loading={job.loading} onClick={() => void run()}>恢复高清图像</Button></div>
  </Card><JobStatus task={job.task} loading={job.loading} error={job.error} />
    {job.result && <RecoveryResult key={job.task?.id} result={job.result} taskId={job.task?.id} />}</div>;
}

export default function ImageRestoration() {
  return <div className="application-studio restoration-page"><header className="scene-heading"><div><span className="scene-eyebrow">应用场景</span><h1>压缩图像高清恢复</h1><p>从带恢复信息的图像中，还原原始 RGB 像素。</p></div><img src="/images/applications/robust-transmission.webp" alt="" width={360} height={220} /></header>
    <Tabs destroyOnHidden items={[{ key: 'prepare', label: '生成可恢复图像', children: <Prepare /> }, { key: 'restore', label: '恢复高清图像', children: <Restore /> }]} />
  </div>;
}
