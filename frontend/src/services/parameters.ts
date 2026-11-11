import type { Algorithm, AlgorithmParams, Encryption } from '../types';

export function defaultParams(algorithm?: Algorithm): AlgorithmParams {
  return Object.fromEntries((algorithm?.parameters ?? [])
    .filter(parameter => parameter.default !== undefined)
    .map(parameter => [parameter.name, parameter.default!])) as AlgorithmParams;
}

export function encryptionError(encryption: Encryption, password: string, confirmation: string): string | undefined {
  if (encryption === 'none') return undefined;
  if (!password.length) return '请输入非空加密密码。';
  if (password !== confirmation) return '两次输入的密码不一致。';
  return undefined;
}
