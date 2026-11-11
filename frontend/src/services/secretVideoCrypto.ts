/** Browser port of ISTG v1 + AES-256-GCM/scrypt; verified against Python istg_crypto.
 * The password, cleartext and encrypted packets never go to HTTP/WebSocket APIs. */
import { Encoder, decode } from 'cbor-x';
import { scryptAsync } from '@noble/hashes/scrypt.js';
import { concat, crc32, MAX_PACKET } from './secretVideoCodec';
const encoder = new Encoder({ useRecords: false, mapsAsObjects: true, structuredClone: false, tagUint8Array: false });
export const encodeObject = (value: unknown): Uint8Array => new Uint8Array(encoder.encode(value));
export const decodeObject = (data: Uint8Array): any => decode(data); // Validated at the protocol boundary below.
const utf8 = new TextEncoder();
const source = (value: Uint8Array): ArrayBuffer => value.slice().buffer as ArrayBuffer;
export async function hash(data: Uint8Array): Promise<string> {
  return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', source(data))), b => b.toString(16).padStart(2, '0')).join('');
}
function prefix(metadata: Uint8Array, payloadSize: number, encrypted: boolean): Uint8Array {
  const out = new Uint8Array(16), view = new DataView(out.buffer);
  out.set([73, 83, 84, 71, 1, 0, encrypted ? 1 : 0, 0, 0, 1]);
  view.setUint16(10, metadata.length); view.setUint32(12, payloadSize); return out;
}
function assemble(head: Uint8Array, meta: Uint8Array, data: Uint8Array): Uint8Array {
  const crc = new Uint8Array(4); new DataView(crc.buffer).setUint32(0, crc32(concat(head, meta))); return concat(head, crc, meta, data);
}
async function derive(password: string, salt: Uint8Array) {
  if (!password || password.length > 1024 || salt.length !== 16) throw Error('请填写加密密钥（最多 1024 字符）');
  return scryptAsync(utf8.encode(password), salt, { N: 32768, r: 8, p: 1, dkLen: 32, maxmem: 64 * 1024 * 1024, asyncTick: 8 });
}
export async function seal(value: unknown, password: string): Promise<{ packet: Uint8Array; key: Uint8Array; digest: string }> {
  const content = encodeObject(value);
  const payload = encodeObject({ kind: 'file', filename: 'secret-video.cbor', mime: 'application/cbor', data: content, sha256: await hash(content) });
  const oldMeta = encodeObject({ payload_format: 'cbor', cipher: 'none', compression: 'none' });
  const salt = crypto.getRandomValues(new Uint8Array(16)), nonce = crypto.getRandomValues(new Uint8Array(12));
  const key = await derive(password, salt);
  const meta = encodeObject({ cipher: 'AES-256-GCM', kdf: 'scrypt', kdf_params: { n: 32768, r: 8, p: 1 }, salt, nonce, original_metadata: oldMeta });
  const head = prefix(meta, payload.length + 16, true);
  const aes = await crypto.subtle.importKey('raw', source(key), 'AES-GCM', false, ['encrypt']);
  const cipher = new Uint8Array(await crypto.subtle.encrypt({ name: 'AES-GCM', iv: source(nonce), additionalData: source(concat(head, meta)) }, aes, source(payload)));
  const packet = assemble(head, meta, cipher);
  if (packet.length > MAX_PACKET) throw Error('实时视频密文超过 160 KiB 上限');
  return { packet, key, digest: await hash(packet) };
}
export async function open(packet: Uint8Array, password: string): Promise<{ value: any; key: Uint8Array; digest: string }> {
  if (packet.length < 36 || packet.length > MAX_PACKET) throw Error('秘密视频数据长度无效');
  const view = new DataView(packet.buffer, packet.byteOffset, packet.byteLength);
  const metaSize = view.getUint16(10), dataSize = view.getUint32(12), head = packet.slice(0, 16), meta = packet.slice(20, 20 + metaSize);
  if (head.slice(0, 10).join() !== '73,83,84,71,1,0,1,0,0,1' || metaSize > 4096 || 20 + metaSize + dataSize !== packet.length || view.getUint32(16) !== crc32(concat(head, meta))) throw Error('ISTG 帧头 / CRC 无效');
  const object = decodeObject(meta), kdf = object.kdf_params;
  if (object.cipher !== 'AES-256-GCM' || object.kdf !== 'scrypt' || kdf?.n !== 32768 || kdf?.r !== 8 || kdf?.p !== 1 || !(object.salt instanceof Uint8Array) || !(object.nonce instanceof Uint8Array) || object.nonce.length !== 12) throw Error('ISTG 加密参数无效');
  const key = await derive(password, object.salt);
  const aes = await crypto.subtle.importKey('raw', source(key), 'AES-GCM', false, ['decrypt']);
  let raw;
  try { raw = new Uint8Array(await crypto.subtle.decrypt({ name: 'AES-GCM', iv: source(object.nonce), additionalData: source(concat(head, meta)) }, aes, source(packet.slice(20 + metaSize)))); }
  catch { throw Error('认证解密失败：密钥不一致或视频数据已损坏'); }
  const inner = decodeObject(raw);
  if (inner.kind !== 'file' || !(inner.data instanceof Uint8Array) || inner.sha256 !== await hash(inner.data)) throw Error('文件 SHA-256 完整性校验失败');
  return { value: decodeObject(inner.data), key, digest: await hash(packet) };
}
export async function receipt(key: Uint8Array, room: string, sender: string, receiver: string, id: number, digest: string): Promise<Uint8Array> {
  const hmac = await crypto.subtle.importKey('raw', source(key), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  return new Uint8Array(await crypto.subtle.sign('HMAC', hmac, source(utf8.encode(`${room}:${sender}:${receiver}:${id}:${digest}`))));
}
export function equal(a: Uint8Array, b: Uint8Array): boolean { let diff = a.length ^ b.length; for (let i = 0; i < a.length; i++) diff |= a[i] ^ (b[i] ?? 0); return diff === 0; }
