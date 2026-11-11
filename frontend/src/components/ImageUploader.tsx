import { useRef, useState } from 'react';
import { Button, Image, Spin, Upload, message } from 'antd';
import { CloudUploadOutlined, DeleteOutlined, FileImageOutlined } from '@ant-design/icons';
import { errorMessage, formatBytes, uploadImage } from '../services/api';
import type { UploadedImage } from '../types';

interface Props { label: string; value?: UploadedImage; onChange: (value: UploadedImage | undefined) => void; onLoadingChange?: (loading: boolean) => void; role?: string; disabled?: boolean; compact?: boolean; hideLabel?: boolean }
export function ImageUploader({ label, value, onChange, onLoadingChange, role = 'cover', disabled, compact, hideLabel }: Props) {
  const [loading, setLoading] = useState(false);
  const uploading = useRef(false);
  const beforeUpload = async (file: File) => {
    if(disabled || uploading.current) return Upload.LIST_IGNORE;
    if (file.size > 16 * 1024 * 1024) { void message.error('图片不能超过 16 MB。'); return Upload.LIST_IGNORE; }
    uploading.current = true;
    setLoading(true); onLoadingChange?.(true);
    try { onChange(await uploadImage(file, role)); } catch (err) { void message.error(errorMessage(err)); }
    finally { uploading.current = false; setLoading(false); onLoadingChange?.(false); }
    return Upload.LIST_IGNORE;
  };
  return <div className={`image-uploader ${compact ? 'compact' : ''}`} data-testid={`${role}-upload`} role="group" aria-label={label}>
    {!hideLabel && <div className="field-label"><FileImageOutlined aria-hidden="true" /> {label}</div>}
    {value ? <div className="uploaded-image">
      <div className="uploaded-preview"><Image src={value.url} alt={label} /></div>
      <div className="uploaded-meta"><strong title={value.name}>{value.name}</strong><span>{value.width} × {value.height} · {value.format} · {formatBytes(value.size_bytes)}</span></div>
      <div className="uploaded-actions"><Upload showUploadList={false} accept="image/png,image/jpeg,image/webp,image/bmp" beforeUpload={beforeUpload} disabled={disabled || loading}><Button size="small" loading={loading}>更换图片</Button></Upload><Button size="small" type="text" danger icon={<DeleteOutlined />} disabled={disabled || loading} onClick={() => onChange(undefined)}>移除</Button></div>
    </div> : <Upload.Dragger accept="image/png,image/jpeg,image/webp,image/bmp" multiple={false} showUploadList={false} beforeUpload={beforeUpload} disabled={disabled || loading}>
      <Spin spinning={loading}><div className="upload-glyph"><CloudUploadOutlined /></div><p className="upload-title">点击或拖拽上传图片</p><p className="upload-hint">PNG / JPEG / WebP / BMP · 最大 16 MB</p></Spin>
    </Upload.Dragger>}
  </div>;
}
