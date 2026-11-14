import assert from 'node:assert/strict';
import path from 'node:path';
import os from 'node:os';
import { mkdir } from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
import { chromium } from '../frontend/node_modules/playwright/index.mjs';

const origin = process.env.ISTG_BASE_URL ?? 'http://127.0.0.1:8017';
const outputDirectory = path.resolve(process.env.ISTG_QA_OUTPUT_DIR ?? path.join(os.tmpdir(), 'istg-redesign-' + randomUUID()));
await mkdir(outputDirectory, { recursive: true });
const routes = [
  ['home', '/', /多模态\s*信息隐藏/], ['embed', '/embed', '信息隐藏'], ['extract', '/extract', '信息提取'],
  ['analysis', '/analysis', '安全分析'], ['compare', '/compare', '算法对比'],
  ['multimodal', '/multimodal', '多模态隐写'], ['network', '/network-lab', '网络隐写'], ['video', '/secret-video', '视频通话'],
];
const browser = await chromium.launch({ channel: 'msedge', headless: true });
const errors = [];
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
page.on('pageerror', error => errors.push(error.message));
const visible = async locator => { await locator.waitFor({ state: 'visible' }); };
const shot = async name => { await page.screenshot({ path: path.join(outputDirectory, `redesign-${name}.png`), fullPage: true }); };
try {
  const layouts = [];
  for (const width of [1440, 1280, 768, 390]) {
    await page.setViewportSize({ width, height: 900 });
    for (const [name, route, title] of routes) {
      await page.goto(origin + route);
      await visible(page.getByRole('heading', { name: title, exact: true }));
      await page.waitForTimeout(200);
      if (name === 'home') {
        for (const id of ['medical-imaging', 'robust-transmission', 'copyright-protection']) {
          await page.locator(`#${id}`).scrollIntoViewIfNeeded();
          await page.waitForTimeout(750);
        }
        await page.waitForFunction(() => [...document.querySelectorAll('.application-photo')].every(el => el.dataset.loaded === 'true'));
        await page.evaluate(() => window.scrollTo(0, 0));
      }
      const geometry = await page.evaluate(() => ({ width: innerWidth, scroll: document.documentElement.scrollWidth, heading: document.querySelector('h1').getBoundingClientRect().top, header: document.querySelector('.site-header').getBoundingClientRect().bottom }));
      assert(geometry.scroll <= width + 2, `${name} overflows at ${width}: ${geometry.scroll}`);
      assert(geometry.heading >= geometry.header - 1, `${name} heading covered by header`);
      assert.equal(await page.locator('.sidebar').count(), 0);
      assert.equal(await page.locator('.header-status').count(), name === 'home' ? 0 : 1);
      await shot(`${width}-${name}`);
      layouts.push(`${width}:${name}`);
    }
  }
  console.log('32 responsive layouts verified');
  // Desktop pointer and keyboard navigation.
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(origin);
  for (const [group, items] of [['信息隐写', ['信息隐藏', '信息提取', '多模态隐写']], ['分析评估', ['安全分析', '算法对比']], ['通信应用', ['网络隐写', '视频通话']]]) {
    await page.getByRole('button', { name: group, exact: true }).hover();
    for (const label of items) await visible(page.getByRole('menuitem', { name: label, exact: true }));
    await page.getByRole('menuitem', { name: items[0], exact: true }).click();
    await visible(page.getByRole('heading', { name: items[0], exact: true }));
    assert(await page.getByRole('button', { name: group, exact: true }).evaluate(el => el.classList.contains('active')));
  }
  const trigger = page.getByRole('button', { name: '信息隐写', exact: true });
  await page.mouse.move(0, 300); await trigger.focus(); await trigger.press('ArrowDown');
  await visible(page.getByRole('menuitem', { name: '信息隐藏', exact: true }));
  await page.waitForTimeout(150);
  assert(await page.evaluate(() => !!document.activeElement.closest('[role="menu"]')), 'Keyboard menu did not receive focus');
  await page.keyboard.press('Escape');
  await page.waitForFunction(() => document.querySelector('button[aria-haspopup="menu"]').getAttribute('aria-expanded') === 'false');
  await trigger.press('Enter'); await visible(page.getByRole('menuitem', { name: '信息隐藏', exact: true }));
  await page.keyboard.press('Escape');
  // Every tool is also reachable from the mobile drawer, which closes after selection.
  await page.setViewportSize({ width: 390, height: 844 });
  for (const [, route, title] of routes.slice(1)) {
    await page.getByRole('button', { name: '打开导航', exact: true }).click();
    await page.getByRole('navigation', { name: '移动导航' }).getByRole('link', { name: title, exact: true }).click();
    await page.waitForURL(origin + route);
    await page.getByRole('dialog').waitFor({ state: 'hidden' });
    await visible(page.getByRole('heading', { name: title, exact: true }));
  }
  await page.goto(origin + '/applications#copyright-protection');
  await page.waitForURL(origin + '/#copyright-protection');
  await visible(page.getByRole('heading', { name: /数字版权\s*保护/, exact: true }));
  assert(await page.locator('#copyright-protection').evaluate(el => el.getBoundingClientRect().top >= document.querySelector('.site-header').getBoundingClientRect().bottom - 1));
  await page.goto(origin);
  await page.locator('#robust-transmission').getByRole('link', { name: '进入场景', exact: true }).click();
  await visible(page.getByRole('heading', { name: '压缩图像高清恢复', exact: true }));
  await page.goto(origin + '/analysis?tab=performance&panel=robustness');
  await visible(page.getByRole('tab', { name: '鲁棒性测试', exact: true }));
  assert.equal(await page.getByRole('tab', { name: '鲁棒性测试', exact: true }).getAttribute('aria-selected'), 'true');
  await page.reload();
  await visible(page.getByRole('tab', { name: '鲁棒性测试', exact: true }));
  assert.equal(await page.getByRole('tab', { name: '鲁棒性测试', exact: true }).getAttribute('aria-selected'), 'true');
  await page.goto(origin + '/analysis?tab=invalid&panel=invalid');
  assert.equal(await page.getByRole('tab', { name: /自动提取分析/ }).getAttribute('aria-selected'), 'true');
  console.log('Navigation, keyboard, mobile drawer and deep links verified');
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto(origin);
  assert.equal(await page.locator('.applications-hero__content').evaluate(el => getComputedStyle(el).animationName), 'none');
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  await page.goto(origin + '/multimodal');
  await page.getByRole('tab', { name: '多载体协同', exact: true }).click();
  await visible(page.getByText('载体 3', { exact: true }));
  await page.getByRole('tab', { name: '安全攻防', exact: true }).click();
  await visible(page.getByRole('button', { name: '开始攻击测试', exact: true }));
  await page.goto(origin + '/network-lab');
  for (const label of ['双机 / 公网', '协议字段 · 离线', '流量分析与鲁棒性']) {
    const tab = page.getByRole('tab', { name: label, exact: true });
    await tab.click(); assert.equal(await tab.getAttribute('aria-selected'), 'true');
  }
  await page.goto(origin + '/secret-video');
  await page.getByRole('button', { name: '运行自检', exact: true }).click();
  await visible(page.getByText('自检通过', { exact: true }));
  console.log('Multimodal and network panels, video self-test verified');
  // A real image embed/extract round trip through the revised five-step UI.
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(origin + '/embed');
  await page.getByTestId('secret-text').fill('ISTG 界面改版往返验证 🔐');
  const cover = await page.evaluate(() => {
    const canvas = document.createElement('canvas'); canvas.width = canvas.height = 256;
    const ctx = canvas.getContext('2d'); const image = ctx.createImageData(256, 256); let seed = 47;
    for (let i = 0; i < image.data.length; i++) { seed ^= seed << 13; seed ^= seed >>> 17; seed ^= seed << 5; image.data[i] = i % 4 === 3 ? 255 : seed & 255; }
    ctx.putImageData(image, 0, 0); return canvas.toDataURL('image/png').split(',')[1];
  });
  await page.getByTestId('cover-upload').locator('input[type=file]').setInputFiles({ name: 'cover.png', mimeType: 'image/png', buffer: Buffer.from(cover, 'base64') });
  for (let step = 0; step < 3; step++) {
    const next = page.getByTestId('wizard-next');
    await next.waitFor();
    await page.waitForFunction(() => !document.querySelector('[data-testid="wizard-next"]').disabled);
    await next.click();
  }
  await page.getByTestId('embed-submit').click();
  await visible(page.getByTestId('result-download'));
  await visible(page.getByText('逐字节一致', { exact: true }));
  const download = await page.getByTestId('result-download').getAttribute('href');
  const image = await page.request.get(new URL(download, origin).href);
  assert(image.ok());
  const report = await page.request.get(new URL(await page.getByRole('link', { name: /导出报告/ }).getAttribute('href'), origin).href);
  assert(report.ok()); assert((await report.body()).length > 0);
  const stego = await image.body();
  await shot('roundtrip-embed');
  await page.getByRole('button', { name: '提取验证', exact: true }).click();
  await page.waitForURL(origin + '/extract');
  await visible(page.getByRole('group', { name: '待提取图片' }));
  assert.equal(await page.locator('.ant-segmented-item-selected').filter({ hasText: '手动提取' }).count(), 1);
  await page.waitForFunction(() => !document.querySelector('[data-testid="extract-submit"]')?.hasAttribute('disabled'));
  assert.equal(await page.getByTestId('extract-submit').isEnabled(), true);
  assert.equal(await page.locator('input[placeholder="未加密时留空"]').inputValue(), '');
  assert.equal(await page.evaluate(() => typeof history.state?.usr?.experimentId), 'string');
  assert.equal(await page.evaluate(() => JSON.stringify(history.state).includes('ISTG 界面改版往返验证')), false);
  assert.equal(await page.evaluate(() => location.search + location.hash), '');
  await page.getByTestId('extract-submit').click();
  await visible(page.getByRole('textbox', { name: '恢复的秘密文本', exact: true }));
  assert.equal(await page.getByRole('textbox', { name: '恢复的秘密文本', exact: true }).inputValue(), 'ISTG 界面改版往返验证 🔐');
  await shot('roundtrip-extract');
  const extractReport = await page.request.get(new URL(await page.getByRole('link', { name: /导出提取报告/ }).getAttribute('href'), origin).href);
  assert(extractReport.ok());
  await page.getByRole('button', { name: '返回处理结果', exact: true }).click();
  await page.waitForURL(origin + '/embed');
  await visible(page.getByTestId('result-download'));
  await page.getByRole('button', { name: '安全分析', exact: true }).click();
  await page.waitForURL(origin + '/analysis?tab=performance&panel=quality');
  assert.equal(await page.getByRole('button', { name: '计算图像质量', exact: true }).isEnabled(), true);
  const qualityRequest = page.waitForResponse(response => response.url().endsWith('/api/analysis/quality') && response.request().method() === 'POST');
  await page.getByRole('button', { name: '计算图像质量', exact: true }).click();
  let qualityTask = await (await qualityRequest).json();
  for (let attempt = 0; qualityTask.status !== 'completed' && attempt < 60; attempt++) {
    assert.notEqual(qualityTask.status, 'failed', JSON.stringify(qualityTask.error));
    await page.waitForTimeout(100);
    const response = await page.request.get(origin + '/api/tasks/' + encodeURIComponent(qualityTask.id));
    assert(response.ok()); qualityTask = await response.json();
  }
  assert.equal(qualityTask.status, 'completed');
  const quality = qualityTask.result.quality;
  assert(quality.mse >= 0 && quality.ssim >= -1 && quality.ssim <= 1);
  assert(quality.psnr === 'Infinity' || Number.isFinite(quality.psnr));
  for (const [key, label, digits, suffix] of [['psnr', 'PSNR', 3, ' dB'], ['ssim', 'SSIM', 6, ''], ['mse', 'MSE', 6, '']]) {
    const formatted = quality[key] === 'Infinity' ? '∞' : Number(quality[key]).toLocaleString('zh-CN', { maximumFractionDigits: digits });
    const measurement = page.locator('.analysis-measure').filter({ has: page.getByText(label, { exact: true }) });
    await visible(measurement.locator('strong'));
    assert.equal(await measurement.locator('strong').innerText(), formatted + suffix);
  }
  await page.getByRole('button', { name: '返回处理结果', exact: true }).click();
  await page.waitForURL(origin + '/embed');
  await visible(page.getByTestId('result-download'));
  await page.reload();
  await visible(page.getByText('暂存结果已失效，请重新上传图片', { exact: true }));
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ layouts: layouts.length, desktopMenus: true, keyboard: true, mobileRoutes: 7, legacyAnchor: true, robustnessDeepLink: true, reducedMotion: true, toolPanelsAndVideoSelfTest: true, imageRoundTrip: true, downloadsAndReports: true, inMemoryHandoff: true, qualityAnalysis: true, expiredHandoff: true, screenshotDirectory: outputDirectory, pageErrors: errors }));
} catch (error) { await shot('failure'); console.log(await page.locator('h1').allTextContents()); throw error; } finally { await browser.close(); }
