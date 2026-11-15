import assert from 'node:assert/strict';
import { createHash, randomUUID } from 'node:crypto';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { chromium } from '../frontend/node_modules/playwright/index.mjs';

const origin = process.env.ISTG_BASE_URL ?? 'http://127.0.0.1:8017';
const output = process.env.ISTG_QA_OUTPUT_DIR ?? await fs.mkdtemp(path.join(os.tmpdir(), 'istg-scenes-'));
await fs.mkdir(output, { recursive: true });
const browser = await chromium.launch({ channel: 'msedge', headless: true });
const contexts = [], errors = [], checks = [];
const visible = locator => locator.waitFor({ state: 'visible' });
function observe(page) { page.on('pageerror', error => errors.push(error.message)); page.setDefaultTimeout(25000); }
async function layout(page, name, width) {
  const geometry = await page.evaluate(() => ({ width: document.documentElement.scrollWidth, text: document.body.innerText }));
  assert(geometry.width <= width + 2, name + ' overflows: ' + geometry.width);
  assert(!/图片仅用于本次|不等同于完整|本实验用于|隐写实验|通信实验|多模态实验/.test(geometry.text), 'Redundant copy remains: ' + name);
  checks.push(name);
}
async function shot(page, name) { await page.screenshot({ path: path.join(output, name + '.png'), fullPage: true }); }
async function waitTask(request, id) {
  for (let attempt = 0; attempt < 240; attempt++) {
    const response = await request.get(origin + '/api/tasks/' + id); assert(response.ok());
    const task = await response.json();
    if (task.status === 'completed') return task.result;
    if (task.status === 'failed') throw Error(JSON.stringify(task.error));
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  throw Error('Task timeout: ' + id);
}
async function register(page, username, password) {
  await page.goto(origin + '/applications/copyright', { waitUntil: 'networkidle' });
  await page.getByText('注册', { exact: true }).click();
  await page.getByLabel('版权用户名', { exact: true }).fill(username);
  await page.getByLabel('版权密码', { exact: true }).fill(password);
  await page.getByLabel('版权确认密码', { exact: true }).fill(password);
  await page.getByRole('button', { name: /注册并登录/ }).click();
  await visible(page.getByRole('button', { name: /添加作品/ }));
}
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, reducedMotion: 'reduce' });
  contexts.push(context);
  const page = await context.newPage(); observe(page);
  const scenes = [['medical', '影像信息嵌入'], ['robustness', '可逆图像隐写'], ['copyright', '数字版权保护']];
  for (const width of process.env.ISTG_SKIP_LAYOUT ? [] : [1440, 1280, 768, 390]) {
    await page.setViewportSize({ width, height: 900 });
    for (const [scene, title] of scenes) {
      await page.goto(origin + '/applications/' + scene, { waitUntil: 'networkidle' });
      await visible(page.getByRole('heading', { name: title, exact: true }));
      await layout(page, scene + ':' + width, width);
      if (scene === 'medical') {
        await page.getByText(/^高级设置/).click();
        await layout(page, scene + ':settings:' + width, width);
      } else {
        for (const tab of scene === 'copyright' ? ['版权标识', '标识验证', '授权管理'] : ['提取及恢复']) {
          await page.getByRole('tab', { name: tab, exact: true }).click();
          await layout(page, scene + ':' + tab + ':' + width, width);
        }
      }
      await shot(page, scene + '-' + width);
    }
  }
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(origin, { waitUntil: 'networkidle' });
  assert.deepEqual((await page.locator('.desktop-navigation > button').allTextContents()).map(text => text.trim()), ['信息隐写', '分析评估', '通信应用', '应用场景']);
  for (const [scene, title] of scenes) {
    await page.getByRole('button', { name: '应用场景', exact: true }).click();
    await page.getByRole('menuitem', { name: title, exact: true }).click();
    await page.waitForURL(origin + '/applications/' + scene);
    await visible(page.getByRole('heading', { name: title, exact: true }));
  }
  checks.push('Application navigation');
  await page.goto(origin + '/applications/medical', { waitUntil: 'networkidle' });
  const fixture = await page.evaluate(async () => {
    const canvas = document.createElement('canvas'); canvas.width = 640; canvas.height = 480;
    const ctx = canvas.getContext('2d'), data = ctx.createImageData(640, 480), rgb = new Uint8Array(640 * 480 * 3);
    let seed = 71, at = 0;
    for (let i = 0; i < data.data.length; i += 4) {
      for (let channel = 0; channel < 3; channel++) { seed ^= seed << 13; seed ^= seed >>> 17; seed ^= seed << 5; const value = seed & 255; data.data[i + channel] = value; rgb[at++] = value; }
      data.data[i + 3] = 255;
    }
    ctx.putImageData(data, 0, 0);
    const digest = await crypto.subtle.digest('SHA-256', rgb);
    return { png: canvas.toDataURL('image/png').split(',')[1], pixelHash: [...new Uint8Array(digest)].map(value => value.toString(16).padStart(2, '0')).join('') };
  });
  const source = Buffer.from(fixture.png, 'base64');
  const pngFile = { name: 'source.png', mimeType: 'image/png', buffer: source };
  await page.locator('input[type=file]').first().setInputFiles(pngFile);
  await page.getByLabel('信息内容', { exact: true }).fill('影像信息写入与提取验证');
  await page.getByLabel('记录编号', { exact: true }).fill('record-001');
  await page.getByTestId('scene-embed-submit').click();
  await visible(page.getByRole('link', { name: /下载隐写图片/ }));
  await page.getByTestId('scene-verify').click();
  await visible(page.getByTestId('scene-verification-result'));
  assert((await page.getByLabel('提取出的信息', { exact: true }).inputValue()).includes('影像信息写入与提取验证'));
  checks.push('Information embedding and extraction');
  await page.goto(origin + '/applications/robustness', { waitUntil: 'networkidle' });
  await page.locator('input[type=file]').first().setInputFiles(pngFile);
  await page.getByLabel('秘密文本内容', { exact: true }).fill('RDH round trip');
  const preparation = page.waitForResponse(response => response.url().endsWith('/api/rdh/embed') && response.request().method() === 'POST');
  await page.getByRole('button', { name: '嵌入并验证', exact: true }).click();
  const preparedTask = await (await preparation).json();
  await visible(page.getByText('原始RGB像素精确恢复：通过', { exact: true }));
  const prepared = await waitTask(page.request, preparedTask.id);
  assert.equal(prepared.metrics.original_rgb_sha256, fixture.pixelHash);
  assert.equal(prepared.metrics.restored_rgb_sha256, fixture.pixelHash);
  assert(prepared.metrics.quality.mse > 0);
  const carrierResponse = await page.request.get(origin + prepared.stego.download_url);
  assert(carrierResponse.ok()); const carrier = await carrierResponse.body();
  assert.equal(carrier.length, prepared.metrics.stego_bytes);
  await shot(page, 'recovery-generated');
  await page.request.delete(origin + '/api/tasks/' + preparedTask.id);
  await page.getByRole('tab', { name: '提取及恢复', exact: true }).click();
  await page.locator('input[type=file]').first().setInputFiles({ name: 'recoverable.png', mimeType: 'image/png', buffer: carrier });
  const restoration = page.waitForResponse(response => response.url().endsWith('/api/rdh/extract') && response.request().method() === 'POST');
  await page.getByRole('button', { name: '提取及恢复', exact: true }).click();
  const restoredTask = await (await restoration).json();
  await visible(page.getByText('原始RGB像素精确恢复：通过', { exact: true }));
  const restored = await waitTask(page.request, restoredTask.id);
  assert.equal(restored.metrics.restored_rgb_sha256, fixture.pixelHash);
  assert.equal(restored.text, 'RDH round trip');
  await page.locator('input[type=file]').first().setInputFiles(pngFile);
  await page.getByRole('button', { name: '提取及恢复', exact: true }).click();
  await visible(page.getByText(/未找到恢复信息|恢复文件的图像格式不匹配/));
  checks.push('Pixel-domain RDH secret and exact RGB recovery after source task deletion');
  checks.push('Ordinary PNG is rejected as missing recovery data');
  const readerContext = await browser.newContext({ viewport: { width: 1440, height: 900 }, reducedMotion: 'reduce' });
  contexts.push(readerContext); const reader = await readerContext.newPage(); observe(reader);
  const suffix = randomUUID().replaceAll('-', '').slice(0, 12), ownerName = 'owner_' + suffix, readerName = 'reader_' + suffix;
  await register(page, ownerName, 'copyright-owner-password');
  await register(reader, readerName, 'copyright-reader-password');
  await page.getByRole('button', { name: /添加作品/ }).click();
  await page.locator('input[type=file]').first().setInputFiles(pngFile);
  await page.getByLabel('作品名称', { exact: true }).fill('高清作品_' + suffix);
  await page.getByLabel('署名作者', { exact: true }).fill('测试作者');
  await page.getByLabel('版权声明', { exact: true }).fill('原创作品\n按授权范围使用');
  const creation = page.waitForResponse(response => response.url().endsWith('/api/copyright/works') && response.request().method() === 'POST');
  await page.getByRole('button', { name: /保存作品/ }).click();
  const creationResponse = await creation; assert.equal(creationResponse.status(), 201);
  const work = (await creationResponse.json()).work;
  assert(!JSON.stringify(work).includes('/api/files/'));
  assert.equal((await reader.request.get(origin + work.content_url)).status(), 403);
  await page.getByRole('tab', { name: '版权标识', exact: true }).click();
  await page.getByLabel('分发对象', { exact: true }).fill(readerName);
  const marking = page.waitForResponse(response => response.url().endsWith('/mark') && response.request().method() === 'POST');
  await page.getByRole('button', { name: /生成版权标识/ }).click();
  const markResponse = await marking; assert(markResponse.ok()); const mark = (await markResponse.json()).mark;
  await visible(page.getByText('标识已生成', { exact: true }));
  const downloadEvent = page.waitForEvent('download');
  await page.getByRole('button', { name: /下载标识图片/ }).click();
  const download = await downloadEvent; assert.equal(await download.failure(), null);
  const marked = await fs.readFile(await download.path());
  assert.equal((await reader.request.get(origin + mark.url)).status(), 403);
  await page.getByRole('tab', { name: '标识验证', exact: true }).click();
  await page.locator('input[type=file]').first().setInputFiles({ name: 'marked.png', mimeType: 'image/png', buffer: marked });
  await page.getByRole('button', { name: /验证版权标识/ }).click();
  for (const text of ['签名有效', '图像匹配', '记录匹配']) await visible(page.getByText(text, { exact: true }));
  await shot(page, 'copyright-verified');
  await page.getByRole('tab', { name: '授权管理', exact: true }).click();
  await page.getByLabel('授权用户名', { exact: true }).fill(readerName);
  await page.getByRole('button', { name: '添加授权', exact: true }).click();
  await visible(page.getByRole('button', { name: '撤销授权', exact: true }));
  await reader.getByRole('button', { name: /刷新/ }).click();
  await visible(reader.getByText('授权有效', { exact: true }));
  await reader.getByRole('button', { name: '查看作品', exact: true }).click();
  await visible(reader.locator('.copyright-private-preview'));
  assert((await reader.locator('.copyright-private-preview').getAttribute('src')).startsWith('blob:'));
  assert.equal((await reader.request.get(origin + work.content_url + '?download=true')).status(), 403);
  await reader.keyboard.press('Escape'); await reader.getByRole('dialog').waitFor({ state: 'hidden' });
  await page.getByLabel('授权用户名', { exact: true }).fill(readerName);
  await page.getByRole('checkbox', { name: '允许下载原文件', exact: true }).check();
  await page.getByRole('button', { name: '添加授权', exact: true }).click();
  await page.getByText('查看与下载', { exact: true }).waitFor();
  const originalResponse = await reader.request.get(origin + work.content_url + '?download=true');
  assert.equal(originalResponse.status(), 200);
  assert.equal(createHash('sha256').update(await originalResponse.body()).digest('hex'), createHash('sha256').update(source).digest('hex'));
  await page.getByRole('button', { name: '撤销授权', exact: true }).click();
  await visible(page.getByText('已撤销', { exact: true }));
  assert.equal((await reader.request.get(origin + work.content_url)).status(), 403);
  await reader.getByRole('button', { name: /刷新/ }).click();
  await visible(reader.getByText('授权已撤销', { exact: true }));
  assert.equal(await reader.getByRole('button', { name: '查看作品', exact: true }).isDisabled(), true);
  const grantResponse = await page.request.post(origin + '/api/copyright/works/' + work.id + '/grants', { data: { username: readerName, expires_at: Date.now() / 1000 + 0.8, can_download: false } });
  assert.equal(grantResponse.status(), 200);
  await new Promise(resolve => setTimeout(resolve, 1100));
  assert.equal((await reader.request.get(origin + work.content_url)).status(), 403);
  await reader.getByRole('button', { name: /刷新/ }).click();
  await visible(reader.getByText('授权已到期', { exact: true }));
  await page.getByRole('tab', { name: '作品管理', exact: true }).click();
  await page.getByRole('tab', { name: '授权管理', exact: true }).click();
  await page.getByRole('tab', { name: '访问记录', exact: true }).click();
  await visible(page.getByText('撤销访问权限', { exact: true }));
  await shot(page, 'copyright-access-audit');
  const signedOut = page.waitForResponse(response => response.url().endsWith('/api/copyright/auth/logout') && response.request().method() === 'POST');
  await page.getByRole('button', { name: '退出登录', exact: true }).click();
  assert.equal((await signedOut).status(), 200);
  await page.getByRole('button', { name: '退出登录', exact: true }).waitFor({ state: 'hidden' });
  assert.equal((await page.request.get(origin + work.content_url)).status(), 401);
  checks.push('Independent real owner and reader accounts');
  checks.push('Private work upload, signed identifier generation and verification');
  checks.push('View-only grant, original download grant, revoke and expiry enforced');
  checks.push('Audit records and logout invalidate further access');
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ passed: true, checks, pageErrors: errors, screenshotDirectory: output }, null, 2));
} catch (error) {
  console.error(JSON.stringify({ passed: false, error: String(error), checks, pageErrors: errors, screenshotDirectory: output }, null, 2)); throw error;
} finally { await Promise.all(contexts.map(context => context.close())); await browser.close(); }
