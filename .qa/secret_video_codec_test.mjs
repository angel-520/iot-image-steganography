import { build } from '../frontend/node_modules/esbuild/lib/main.js';
import fs from 'node:fs/promises';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';

await build({ entryPoints: ['frontend/src/services/secretVideoCodec.ts'], bundle: true, platform: 'browser', format: 'esm', outfile: '.qa/sv-codec-bundle.mjs' });
await build({ entryPoints: ['frontend/src/services/secretVideoCrypto.ts'], bundle: true, platform: 'browser', format: 'esm', outfile: '.qa/sv-crypto-bundle.mjs' });
const codec = await import('./sv-codec-bundle.mjs');
const crypto = await import('./sv-crypto-bundle.mjs');
assert.equal(codec.selfTest().ok, true);
const original = { v: 1, from: 'Alice', to: 'Bob', type: 'text', text: '视频帧测试 🔐', created: Date.now() };
const { packet } = await crypto.seal(original, 'interop-test');
assert.deepEqual((await crypto.open(packet, 'interop-test')).value, original);
await assert.rejects(crypto.open(packet, 'wrong-key'));
await fs.writeFile('.qa/sv-browser.istg', packet);
const py = spawnSync(process.env.ISTG_TEST_PYTHON ?? 'python', ['-c',
  "from pathlib import Path; import istg,cbor2; p=Path('.qa/sv-browser.istg').read_bytes(); q=istg.decrypt_istg(p,'interop-test'); i=istg.parse_istg(q); v=cbor2.loads(i['payload_data']['data']); assert v['from']=='Alice'; Path('.qa/sv-python.istg').write_bytes(istg.encrypt_istg(q,'python-test'))"], { encoding: 'utf8' });
assert.equal(py.status, 0, py.stderr);
assert.deepEqual((await crypto.open(new Uint8Array(await fs.readFile('.qa/sv-python.istg')), 'python-test')).value, original);
const bits = codec.encodeFrame({ kind: 1, sender: 1, target: 2, id: 3, index: 0, total: 1, data: new TextEncoder().encode('roundtrip') });
const corrupt = bits.slice(); corrupt[0] ^= 1; corrupt[1] ^= 1;
assert.equal(codec.decodeBits(corrupt).frame, undefined);
console.log(JSON.stringify({ losslessQimRoundtrip: true, hammingSingleBitCorrection: true, corruptionRejected: true, browserPythonAesInterop: true, wrongKeyRejected: true }));
