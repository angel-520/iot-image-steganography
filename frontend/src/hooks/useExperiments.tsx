import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { Alert } from 'antd';
import type { AlgorithmParams, AnyResult, Encryption, UploadedImage } from '../types';

export interface CompletedImageExperiment {
  id: string;
  taskId?: string;
  status: 'ok';
  algorithm: string;
  params: AlgorithmParams;
  cover: UploadedImage;
  stego: UploadedImage;
  encrypted: boolean;
  cipher: Encryption;
  packet_bytes: number;
  quality: Record<string, number | string | null>;
  timings: { embed_seconds: number };
  recovery: { exact_match: boolean };
  dataType: 'text' | 'image';
  imageMode: 'file' | 'bitplanes';
  secretBits: number;
}

const publicParameters = ['channels', 'bit_depth', 'strength', 'edge_threshold'];
export function publicImageParams(params: AlgorithmParams): AlgorithmParams {
  return Object.fromEntries(publicParameters.filter(key => typeof params[key] === 'string' || typeof params[key] === 'number').map(key => [key, params[key]]));
}
function imageMetadata(file: UploadedImage): UploadedImage {
  return { id: file.id, url: file.url, name: file.name, width: file.width, height: file.height, format: file.format, size_bytes: file.size_bytes, media_type: file.media_type, download_url: file.download_url };
}
interface ExperimentStore {
  get: (id?: string) => CompletedImageExperiment | undefined;
  save: (result: AnyResult, taskId: string | undefined, configuration: Pick<CompletedImageExperiment, 'dataType' | 'imageMode' | 'secretBits'>) => CompletedImageExperiment;
  invalidate: (id?: string) => void;
}
const Experiments = createContext<ExperimentStore | null>(null);
export function ExperimentProvider({ children }: { children: ReactNode }) {
  const records = useRef(new Map<string, CompletedImageExperiment>());
  const store = useMemo<ExperimentStore>(() => ({
    get: id => id ? records.current.get(id) : undefined,
    invalidate: id => { if (id) records.current.delete(id); },
    save: (result, taskId, configuration) => {
      const quality = Object.fromEntries(['psnr', 'ssim', 'mse'].map(key => [key, typeof result.quality?.[key] === 'number' || result.quality?.[key] === 'Infinity' ? result.quality[key] as number | string : null]));
      const snapshot: CompletedImageExperiment = {
        id: crypto.randomUUID(), taskId, status: 'ok', algorithm: String(result.algorithm), params: publicImageParams(result.params ?? {}),
        cover: imageMetadata(result.cover), stego: imageMetadata(result.stego), encrypted: result.encrypted === true,
        cipher: ['AES-256-GCM', 'ChaCha20-Poly1305'].includes(result.cipher) ? result.cipher as Encryption : 'none',
        packet_bytes: Number(result.packet_bytes), quality, timings: { embed_seconds: Number(result.timings?.embed_seconds ?? 0) },
        recovery: { exact_match: result.recovery?.exact_match === true },
        dataType: configuration.dataType, imageMode: configuration.imageMode, secretBits: configuration.secretBits,
      };
      records.current.set(snapshot.id, snapshot);
      return snapshot;
    },
  }), []);
  return <Experiments.Provider value={store}>{children}</Experiments.Provider>;
}
export function useExperiments() {
  const store = useContext(Experiments);
  if (!store) throw new Error('ExperimentProvider is required');
  return store;
}
export function useExperimentHandoff() {
  const location = useLocation();
  const navigate = useNavigate();
  const store = useExperiments();
  const id = typeof location.state?.experimentId === 'string' ? location.state.experimentId as string : undefined;
  const [experiment] = useState(() => store.get(id));
  const [expired, setExpired] = useState(Boolean(id && !experiment));
  const detach = useCallback(() => {
    if (id) navigate({ pathname: location.pathname, search: location.search, hash: location.hash }, { replace: true, state: null });
  }, [id, location.pathname, location.search, location.hash, navigate]);
  useEffect(() => { if (id && !store.get(id)) detach(); }, [id, store, detach]);
  const expire = () => { store.invalidate(id ?? experiment?.id); detach(); setExpired(true); };
  return { experiment, expired, dismiss: () => setExpired(false), detach, expire };
}
export function HandoffNotice({ visible, onClose }: { visible: boolean; onClose: () => void }) {
  return visible ? <Alert className="handoff-notice" type="info" showIcon closable onClose={onClose} message="实验暂存已失效，请重新上传图片" /> : null;
}
