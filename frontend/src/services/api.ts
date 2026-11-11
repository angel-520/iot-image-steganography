import axios from 'axios';
import type { Algorithm, Artifact, Task, UploadedImage, AnyResult } from '../types';

export const api = axios.create({ baseURL: '/api', timeout: 120_000 });
export async function getAlgorithms(): Promise<Algorithm[]> {
  const { data } = await api.get<{algorithms: Algorithm[]}>('/algorithms');
  return data.algorithms.map(item => ({ ...item, parameters: item.parameters ?? [] }));
}
export async function uploadImage(file: File, role = 'cover'): Promise<UploadedImage> {
  const form = new FormData(); form.append('file', file); form.append('role', role);
  return (await api.post<UploadedImage>('/uploads', form)).data;
}
export async function postTask(endpoint: string, payload: unknown, signal?: AbortSignal): Promise<Task<AnyResult>> {
  return (await api.post<Task<AnyResult>>(endpoint, payload, { signal })).data;
}
export async function getTask(id: string, signal?: AbortSignal): Promise<Task<AnyResult>> {
  return (await api.get<Task<AnyResult>>(`/tasks/${encodeURIComponent(id)}`, { signal })).data;
}
export function fileUrl(file?: string | Artifact | UploadedImage | null): string {
  if (!file) return '';
  if (typeof file === 'string') return file.startsWith('/') || file.startsWith('blob:') ? file : `/api/files/${encodeURIComponent(file)}`;
  return file.url ?? `/api/files/${encodeURIComponent(file.id ?? ('file_id' in file ? file.file_id ?? '' : ''))}`;
}
export function downloadUrl(file?: Artifact | UploadedImage | null): string {
  if (!file) return '';
  return file.download_url ?? `${fileUrl(file)}?download=true`;
}
export function exportUrl(id: string, format: 'json' | 'csv' = 'json'): string { return `/api/tasks/${encodeURIComponent(id)}/export?format=${format}`; }
export function errorMessage(error: unknown): string {
  if (axios.isAxiosError(error)) {
    const status = error.response?.status;
    if ((status === 404 || status === 405) && error.config?.url?.startsWith('/network-lab/')) {
      return '网络实验暂时不可用，请重启服务后重试。';
    }
    if ((status === 404 || status === 405) && error.config?.url?.startsWith('/multimodal/')) {
      return '多模态功能暂时不可用，请重启服务后重试。';
    }
    if (status === 405) {
      return '操作暂时不可用，请刷新页面后重试。';
    }
    const response = error.response?.data as {error?: {message?: string}; detail?: string} | undefined;
    return response?.error?.message ?? (typeof response?.detail === 'string' ? response.detail : undefined)
      ?? (error.code === 'ERR_NETWORK' ? '连接中断，请稍后重试。' : '请求未能完成，请检查输入后重试。');
  }
  return error instanceof Error ? error.message : '操作未能完成，请重试。';
}
export function formatBytes(bytes?: number | null): string {
  if (bytes === undefined || bytes === null) return '—';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(2)} KB`;
  return `${(bytes / 1024 ** 2).toFixed(2)} MB`;
}
export function formatMetric(value: unknown, precision = 3): string {
  if (value === 'Infinity' || value === Infinity) return '∞';
  if (value === undefined || value === null || typeof value !== 'number' || Number.isNaN(value)) return '—';
  return value.toFixed(precision);
}

export function errorCode(error: unknown): string | undefined {
  if (axios.isAxiosError(error)) return (error.response?.data as { error?: { code?: string } } | undefined)?.error?.code;
  return error instanceof Error && 'code' in error && typeof error.code === 'string' ? error.code : undefined;
}
