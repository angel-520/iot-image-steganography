export type AlgorithmParams = Record<string, string | number | boolean>;
export interface ParameterDefinition {
  name: string;
  label: string;
  type: string;
  default?: string | number | boolean;
  min?: number;
  max?: number;
  step?: number;
  required?: boolean;
  description?: string;
  options?: (string | number | {label: string; value: string | number})[];
}
export interface Algorithm {
  id: string;
  name: string;
  description: string;
  available: boolean;
  supports_embed?: boolean;
  supports_extract?: boolean;
  supports_capacity?: boolean;
  supported_formats?: string[];
  output_formats?: string[];
  parameters: ParameterDefinition[];
  fixed_parameters?: Record<string, unknown>;
  category?: string;
  note?: string;
  notes?: string;
  mode?: 'general' | 'bitplanes';
}
export interface UploadedImage {
  id: string;
  url: string;
  name: string;
  width: number;
  height: number;
  format: string;
  size_bytes: number;
  media_type?: string;
  download_url?: string;
}
export interface Task<T = Record<string, unknown>> {
  id: string;
  status: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled';
  progress?: number;
  message?: string;
  result?: T;
  error?: {code?: string; message?: string} | string;
}
export interface Artifact { id?: string; file_id?: string; url?: string; name?: string; download_url?: string; media_type?: string }
export type Encryption = 'none' | 'AES-256-GCM' | 'ChaCha20-Poly1305';
export type AnyResult = Record<string, any>;
