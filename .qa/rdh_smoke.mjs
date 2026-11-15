import assert from 'node:assert/strict';
import { randomBytes } from 'node:crypto';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { chromium } from '../frontend/node_modules/playwright/index.mjs';

const origin = process.env.ISTG_BASE_URL ?? 'http://127.0.0.1:8021';
const output = process.env.ISTG_QA_OUTPUT_DIR ?? await fs.mkdtemp(path.join(os.tmpdir(), 'istg-rdh-browser-'));
await fs.mkdir(output, { recursive: true });
const browser = await chromium.launch({ channel: 'msedge', headless: true });
const contexts = [], errors = [], checks = [];
async function newPage(width = 1440) {
  const context = await browser.newContext({ viewport: { width, height: 1000 }, reducedMotion: 'reduce', acceptDownloads: true });
  contexts.push(context);
  const page = await context.newPage(); page.setDefaultTimeout(45000);
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(origin + '/applications/robustness', { waitUntil: 'networkidle' });
  await page.getByRole('heading', { name: '可逆图像隐写', exact: true }).waitFor();
  return page;
}
async function task(page, button, endpoint, expected = 'completed') {
  const response = page.waitForResponse(response => response.url().endsWith(endpoint) && response.request().method() === 'POST');
  await page.getByRole('button', { name: button, exact: true }).click();
  const created = await (await response).json();
  for (let i = 0; i < 600; i++) {
    const result = await (await page.request.get(origin + '/api/tasks/' + created.id)).json();
    if (!['queued', 'running'].includes(result.status)) { assert.equal(result.status, expected, JSON.stringify(result)); return result; }
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  throw Error('Task timeout');
}
async function integrity(page) {
  await page.getByText('秘密数据完整恢复：通过', { exact: true }).waitFor();
  await page.getByText('原始RGB像素精确恢复：通过', { exact: true }).waitFor();
}
async function layout(page, name) {
  const dimensions = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, width: innerWidth }));
  assert(dimensions.scroll <= dimensions.width + 2, name + ': ' + JSON.stringify(dimensions));
  await page.screenshot({ path: path.join(output, name + '.png'), fullPage: true }); checks.push(name);
}
async function download(page, name) {
  const link = page.getByRole('link', { name }); await link.waitFor(); await link.scrollIntoViewIfNeeded();
  const pending = page.waitForEvent('download'); await link.click(); const saved = await pending;
  assert.equal(await saved.failure(), null); return fs.readFile(await saved.path());
}
try {
  const page = await newPage();
  const fixture = await page.evaluate(async () => {
    const canvas = document.createElement('canvas'); canvas.width = 640; canvas.height = 480;
    const context = canvas.getContext('2d'), rgba = context.createImageData(640, 480), rgb = new Uint8Array(640 * 480 * 3);
    let at = 0;
    for (let y = 0; y < 480; y++) for (let x = 0; x < 640; x++) {
      const pixel = (y * 640 + x) * 4, color = [(x >> 2) + 25, (y >> 2) + 50, ((x + y) >> 3) + 30];
      for (let c = 0; c < 3; c++) { rgba.data[pixel + c] = color[c]; rgb[at++] = color[c]; }
      rgba.data[pixel + 3] = 255;
    }
    context.putImageData(rgba, 0, 0);
    const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', rgb));
    return { png: canvas.toDataURL('image/png').split(',')[1], hash: [...digest].map(v => v.toString(16).padStart(2, '0')).join('') };
  });
  const file = { name: 'carrier.png', mimeType: 'image/png', buffer: Buffer.from(fixture.png, 'base64') };
  const text = 'RDH 中文、English、特殊字符 !@#$%\n\t🙂';
  await page.locator('input[type=file]').first().setInputFiles(file);
  await page.getByLabel('秘密文本内容', { exact: true }).fill(text);
  const embedded = await task(page, '嵌入并验证', '/api/rdh/embed');
  await integrity(page);
  assert.equal(embedded.result.metrics.original_rgb_sha256, fixture.hash);
  assert.equal(embedded.result.metrics.restored_rgb_sha256, fixture.hash);
  const png = await download(page, '下载 RDH PNG');
  assert.equal(png.length, embedded.result.metrics.stego_bytes);
  await layout(page, 'rdh-embedded-1440');
  await page.setViewportSize({ width: 390, height: 900 }); await layout(page, 'rdh-embedded-390');
  await page.request.delete(origin + '/api/tasks/' + embedded.id);
  const reader = await newPage(390);
  await reader.getByRole('tab', { name: '提取及恢复', exact: true }).click();
  await reader.locator('input[type=file]').setInputFiles({ name: 'standalone-rdh.png', mimeType: 'image/png', buffer: png });
  const extracted = await task(reader, '提取及恢复', '/api/rdh/extract');
  await integrity(reader);
  assert.equal(await reader.getByLabel('提取出的秘密文本', { exact: true }).inputValue(), text);
  assert.equal(extracted.result.metrics.restored_rgb_sha256, fixture.hash);
  assert.deepEqual(await download(reader, '下载秘密文本'), Buffer.from(text, 'utf8'));
  await layout(reader, 'rdh-restored-new-session-390');
  checks.push('Fresh browser session extracts UTF-8 and restores RGB after embedding task deletion');

  await page.goto(origin + '/applications/robustness', { waitUntil: 'networkidle' });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.locator('input[type=file]').first().setInputFiles(file);
  await page.getByText('秘密文件', { exact: true }).click();
  const binary = randomBytes(1024);
  await page.locator('input[type=file]').nth(1).setInputFiles({ name: 'random-secret.bin', mimeType: 'application/octet-stream', buffer: binary });
  await page.getByText('可选加密', { exact: true }).click();
  await page.getByTestId('encryption-select').locator('.ant-select-selector').click();
  await page.locator('.ant-select-dropdown:visible .ant-select-item-option-content').filter({ hasText: /^AES-256-GCM$/ }).click();
  await page.getByLabel('加密密码', { exact: true }).fill('rdh-browser-password');
  await page.getByLabel('确认密码', { exact: true }).fill('rdh-browser-password');
  const encrypted = await task(page, '嵌入并验证', '/api/rdh/embed'); await integrity(page);
  assert.equal(encrypted.result.metrics.encryption, 'AES-256-GCM');
  const encryptedPng = await download(page, '下载 RDH PNG');
  const receiver = await newPage();
  await receiver.getByRole('tab', { name: '提取及恢复', exact: true }).click();
  await receiver.locator('input[type=file]').setInputFiles({ name: 'encrypted-rdh.png', mimeType: 'image/png', buffer: encryptedPng });
  await receiver.getByLabel('RDH 解密口令', { exact: true }).fill('incorrect-password');
  const wrong = await task(receiver, '提取及恢复', '/api/rdh/extract', 'failed');
  assert.equal(wrong.error.code, 'authentication_failed');
  assert.equal(await receiver.getByRole('link', { name: /下载恢复图像 PNG/ }).count(), 0);
  await receiver.getByLabel('RDH 解密口令', { exact: true }).fill('rdh-browser-password');
  const recovered = await task(receiver, '提取及恢复', '/api/rdh/extract'); await integrity(receiver);
  const secret = await receiver.request.get(origin + recovered.result.secret.download_url);
  assert.deepEqual(await secret.body(), binary);
  assert.equal(recovered.result.metrics.restored_rgb_sha256, fixture.hash);
  await layout(receiver, 'rdh-encrypted-file-restored-1440');
  checks.push('Encrypted binary payload, wrong-password failure without outputs, and byte-exact recovery');

  await page.goto(origin + '/applications/robustness', { waitUntil: 'networkidle' });
  await page.locator('input[type=file]').first().setInputFiles(file);
  await page.getByLabel('秘密文本内容', { exact: true }).fill('x'.repeat(100000));
  await page.getByText('容量不足', { exact: true }).waitFor();
  assert(await page.getByRole('button', { name: '嵌入并验证', exact: true }).isDisabled());
  checks.push('Insufficient capacity disables embedding without truncating data');
  assert.deepEqual(errors, []);
  const report = { checks, sample: embedded.result.metrics, pageErrors: errors };
  await fs.writeFile(path.join(output, 'rdh-browser-report.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify({ ...report, output }, null, 2));
} finally { await browser.close(); }
