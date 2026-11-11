/** Public video framing: QIM DC/luminance quantization + Hamming(7,4), CRC and pilots.
 * No signaling or RTCDataChannel dependency is permitted in this module. */
export const WIDTH = 640, HEIGHT = 480, FRAME_BYTES = 336, HEADER = 28, CHUNK = FRAME_BYTES - HEADER;
export const MAX_FILE = 128 * 1024, MAX_PACKET = 160 * 1024;
const BLOCK = 8, COLS = WIDTH / BLOCK, SLOTS = WIDTH * HEIGHT / (BLOCK * BLOCK);
const PILOTS = SLOTS - FRAME_BYTES * 14;
export interface Frame { kind: number; sender: number; target: number; id: number; index: number; total: number; data: Uint8Array }
export interface Decoded { frame?: Frame; pilotBer: number; corrections: number; detected: boolean }
export function tag(id: string): number { return parseInt(id.slice(0, 8), 16) >>> 0; }
export function concat(...parts: Uint8Array[]): Uint8Array {
  const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0)); let offset = 0;
  for (const p of parts) { out.set(p, offset); offset += p.length; } return out;
}
const crcTable = Uint32Array.from({ length: 256 }, (_, n) => { for (let i = 0; i < 8; i++) n = n & 1 ? 0xedb88320 ^ (n >>> 1) : n >>> 1; return n >>> 0; });
export function crc32(bytes: Uint8Array): number {
  let crc = 0xffffffff; for (const byte of bytes) crc = crcTable[(crc ^ byte) & 255] ^ (crc >>> 8); return (crc ^ 0xffffffff) >>> 0;
}
export function packFrame(frame: Frame): Uint8Array {
  if (frame.data.length > CHUNK || ![1, 2, 3].includes(frame.kind)) throw Error('视频帧数据超限');
  const raw = new Uint8Array(FRAME_BYTES), view = new DataView(raw.buffer);
  raw.set([83, 86, 48, 49, frame.kind, 0]); view.setUint32(6, frame.sender); view.setUint32(10, frame.target);
  view.setUint32(14, frame.id); view.setUint16(18, frame.index); view.setUint16(20, frame.total); view.setUint16(22, frame.data.length);
  raw.set(frame.data, HEADER); view.setUint32(24, crc32(concat(raw.slice(0, 24), frame.data))); return raw;
}
export function parseFrame(raw: Uint8Array): Frame | undefined {
  if (raw[0] !== 83 || raw[1] !== 86 || raw[2] !== 48 || raw[3] !== 49 || ![1, 2, 3].includes(raw[4]) || raw[5] !== 0) return;
  const view = new DataView(raw.buffer, raw.byteOffset, raw.byteLength), length = view.getUint16(22);
  if (length > CHUNK) return;
  const data = raw.slice(HEADER, HEADER + length);
  if (view.getUint32(24) !== crc32(concat(raw.slice(0, 24), data))) return;
  return { kind: raw[4], sender: view.getUint32(6), target: view.getUint32(10), id: view.getUint32(14), index: view.getUint16(18), total: view.getUint16(20), data };
}
function hamming(n: number): number[] {
  const a = n >> 3 & 1, b = n >> 2 & 1, c = n >> 1 & 1, d = n & 1;
  return [a ^ b ^ d, a ^ c ^ d, a, b ^ c ^ d, b, c, d];
}
const codes = Array.from({ length: 16 }, (_, i) => hamming(i));
export function encodeFrame(frame: Frame): Uint8Array {
  const bytes = packFrame(frame), bits = new Uint8Array(SLOTS);
  for (let i = 0; i < bytes.length; i++) { bits.set(codes[bytes[i] >> 4], i * 14); bits.set(codes[bytes[i] & 15], i * 14 + 7); }
  for (let i = 0; i < PILOTS; i++) bits[FRAME_BYTES * 14 + i] = pilot(i);
  return bits;
}
function pilot(i: number) { return ((i * 73 + 19) ^ (i >> 1) ^ (i >> 3)) & 1; }
export function decodeBits(bits: Uint8Array): Decoded {
  let errors = 0, corrections = 0;
  for (let i = 0; i < PILOTS; i++) errors += bits[FRAME_BYTES * 14 + i] !== pilot(i) ? 1 : 0;
  const pilotBer = errors / PILOTS;
  if (pilotBer > .26) return { pilotBer, corrections, detected: false };
  const raw = new Uint8Array(FRAME_BYTES);
  for (let i = 0; i < FRAME_BYTES * 2; i++) {
    const b = Array.from(bits.slice(i * 7, i * 7 + 7));
    const syndrome = (b[0] ^ b[2] ^ b[4] ^ b[6]) | (b[1] ^ b[2] ^ b[5] ^ b[6]) << 1 | (b[3] ^ b[4] ^ b[5] ^ b[6]) << 2;
    if (syndrome) { b[syndrome - 1] ^= 1; corrections++; }
    raw[i >> 1] |= (b[2] << 3 | b[4] << 2 | b[5] << 1 | b[6]) << (i % 2 === 0 ? 4 : 0);
  }
  return { frame: parseFrame(raw), pilotBer, corrections, detected: true };
}
function slot(i: number): number { return i * 73 % SLOTS; } // Coprime interleave spreads spatial codec errors.
function mean(data: Uint8ClampedArray, x: number, y: number): number {
  let sum = 0;
  for (let dy = 0; dy < BLOCK; dy++) for (let dx = 0; dx < BLOCK; dx++) {
    const p = ((y + dy) * WIDTH + x + dx) * 4; sum += .299 * data[p] + .587 * data[p + 1] + .114 * data[p + 2];
  } return sum / (BLOCK * BLOCK);
}
export function embedPixels(data: Uint8ClampedArray, bits: Uint8Array, step: number): number {
  if (data.length !== WIDTH * HEIGHT * 4 || bits.length !== SLOTS) throw Error('视频帧必须为 640×480 RGBA');
  let square = 0;
  for (let i = 0; i < SLOTS; i++) {
    const pos = slot(i), x = pos % COLS * BLOCK, y = Math.floor(pos / COLS) * BLOCK, average = mean(data, x, y);
    let q = Math.round(average / step);
    if ((q & 1) !== bits[i]) q += average >= q * step ? 1 : -1;
    const max = Math.floor(255 / step);
    if (q < 0) q += 2; if (q > max) q -= 2;
    const target = q * step, delta = target - average;
    // Move toward black/white affinely so saturated RGB channels cannot clip
    // and shift the block's DC coefficient across a decision boundary.
    const scale = delta >= 0 ? (255 - target) / (255 - average || 1) : target / (average || 1);
    for (let dy = 0; dy < BLOCK; dy++) for (let dx = 0; dx < BLOCK; dx++) {
      const p = ((y + dy) * WIDTH + x + dx) * 4;
      for (let c = 0; c < 3; c++) { const old = data[p + c]; data[p + c] = delta >= 0 ? 255 - (255 - old) * scale : old * scale; square += (data[p + c] - old) ** 2; }
    }
  }
  const mse = square / (WIDTH * HEIGHT * 3); return mse ? 10 * Math.log10(255 ** 2 / mse) : Infinity;
}
export function decodePixels(data: Uint8ClampedArray, step: number): Decoded {
  if (data.length !== WIDTH * HEIGHT * 4) throw Error('解码图像大小错误');
  const bits = new Uint8Array(SLOTS);
  for (let i = 0; i < SLOTS; i++) { const pos = slot(i); bits[i] = Math.round(mean(data, pos % COLS * BLOCK, Math.floor(pos / COLS) * BLOCK) / step) & 1; }
  return decodeBits(bits);
}
export function selfTest(): { ok: boolean; psnr: number; corrected: number } {
  const frame: Frame = { kind: 1, sender: 123, target: 0, id: 456, index: 0, total: 1, data: new TextEncoder().encode('ISTG 无损视频自检') };
  const data = new Uint8ClampedArray(WIDTH * HEIGHT * 4); for (let i = 0; i < data.length; i += 4) { data[i] = 90; data[i + 1] = 130; data[i + 2] = 150; data[i + 3] = 255; }
  const bits = encodeFrame(frame), psnr = embedPixels(data, bits, 24), result = decodePixels(data, 24);
  const damaged = bits.slice(); for (let i = 0; i < FRAME_BYTES * 14; i += 7) damaged[i] ^= 1;
  const corrected = decodeBits(damaged);
  return { ok: result.frame?.id === frame.id && corrected.frame?.id === frame.id && new TextDecoder().decode(result.frame.data) === 'ISTG 无损视频自检', psnr, corrected: corrected.corrections };
}
