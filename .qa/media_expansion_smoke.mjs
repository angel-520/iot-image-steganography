import { chromium } from '../frontend/node_modules/playwright/index.mjs';
import { createHash, randomBytes } from 'node:crypto';
import path from 'node:path';

// Run against an already running local test instance: python run.py --port 8017.
const browser = await chromium.launch({ channel: 'msedge', headless: true });
let page;
try {
  page = await browser.newPage({ viewport: { width: 1440, height: 1080 } });
  page.setDefaultTimeout(30_000);
  const errors = [];
  let embedded, recovered;
  page.on('pageerror', error => errors.push(error.message));
  page.on('response', async response => {
    if (/\/api\/tasks\/[a-f0-9]+$/.test(response.url())) {
      try { const data = await response.json(); if (data.status === 'completed' && data.result?.stego) embedded = data.result;
        if (data.status === 'completed' && data.result?.size_bytes) recovered = data.result;
        if (data.status === 'failed') console.error(JSON.stringify({ failedTask: data.error })); } catch {}
    }
  });
  const size = Math.floor(5.05 * 1048576 / 4) * 4;
  const wav = Buffer.alloc(44 + size);
  wav.write('RIFF', 0); wav.writeUInt32LE(size + 36, 4); wav.write('WAVEfmt ', 8);
  wav.writeUInt32LE(16, 16); wav.writeUInt16LE(1, 20); wav.writeUInt16LE(2, 22);
  wav.writeUInt32LE(44100, 24); wav.writeUInt32LE(44100 * 4, 28);
  wav.writeUInt16LE(4, 32); wav.writeUInt16LE(16, 34); wav.write('data', 36); wav.writeUInt32LE(size, 40);
  const secret = randomBytes(Math.floor(2.61 * 1048576));
  await page.goto('http://127.0.0.1:8017/multimodal');
  await page.getByText('音频载体 · 最大 256 MiB', { exact: true }).waitFor();
  await page.locator('input[type=file]').first().setInputFiles({ name: '5.05MiB.wav', mimeType: 'audio/wav', buffer: wav });
  await page.getByText('5.05MiB.wav', { exact: true }).waitFor();
  await page.getByText('图片 / 音频 / 任意文件', { exact: true }).click();
  await page.getByText('任意秘密文件 · 最大 64 MiB', { exact: true }).waitFor();
  await page.locator('input[type=file]').nth(1).setInputFiles({ name: '2.61MiB.zip', mimeType: 'application/zip', buffer: secret });
  await page.getByText('2.61MiB.zip', { exact: true }).waitFor();
  await page.getByLabel('加密或提取密码', { exact: true }).fill('browser-expansion');
  await page.getByLabel('确认密码', { exact: true }).fill('browser-expansion');
  await page.getByRole('button', { name: '检查容量', exact: true }).click();
  await page.getByText(/容量不足，请调整为至少 9 位后重新检查/).waitFor();
  const depth = page.getByRole('combobox', { name: '嵌入位数', exact: true });
  await depth.focus();
  await depth.press('ArrowDown');
  for (let i = 0; i < 8; i++) await depth.press('ArrowDown');
  await depth.press('Enter');
  await page.getByRole('button', { name: '检查容量', exact: true }).click();
  await page.getByRole('button', { name: /开始嵌入/ }).click();
  await page.getByText('提取验证通过', { exact: true }).waitFor();
  await page.screenshot({ path: path.resolve('.qa/expanded-audio-desktop.png'), fullPage: true });
  if (!embedded || embedded.carrier.bit_depth !== 9) throw Error('9-bit browser embed did not complete');
  const stego = await (await page.request.get(new URL(embedded.stego.url, page.url()).href)).body();
  await page.getByText('提取', { exact: true }).click();
  await page.locator('input[type=file]').first().setInputFiles({ name: 'stego-nine.wav', mimeType: 'audio/wav', buffer: stego });
  await page.getByText('stego-nine.wav', { exact: true }).waitFor();
  await page.getByLabel('加密或提取密码', { exact: true }).fill('browser-expansion');
  await page.getByRole('button', { name: /开始提取/ }).click();
  await page.getByText('认证解密通过', { exact: true }).waitFor();
  if (!recovered) throw Error('No recovery task result');
  const actual = await (await page.request.get(new URL(recovered.file.download_url, page.url()).href)).body();
  if (!actual.equals(secret)) throw Error('Browser recovered bytes differ');
  if (recovered.sha256 !== createHash('sha256').update(secret).digest('hex')) throw Error('SHA mismatch');
  await page.getByText('认证解密通过', { exact: true }).waitFor();
  await page.getByText('嵌入', { exact: true }).click();
  await page.getByText('视频', { exact: true }).click();
  await page.getByText('视频载体 · 最大 512 MiB', { exact: true }).waitFor();
  await page.screenshot({ path: path.resolve('.qa/expanded-video-desktop.png'), fullPage: true });
  await page.getByText('提取', { exact: true }).click();
  await page.getByText('视频隐写主文件 · 最大 1024 MiB', { exact: true }).waitFor();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(500); // Let Ant Design's segmented thumb finish its resize animation.
  await page.screenshot({ path: path.resolve('.qa/expanded-video-mobile.png'), fullPage: true });
  if (await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 2)) throw Error('Mobile overflow');
  if (errors.length) throw Error(errors.join('\n'));
  console.log(JSON.stringify({ browserNineBitEncryptedFileRoundtrip: true, secretBytes: secret.length,
    limitsVisible: true, mobileNoOverflow: true, pageErrors: errors }));
} catch (error) {
  if (page) {
    await page.screenshot({ path: path.resolve('.qa/expanded-failure.png'), fullPage: true });
    console.error((await page.locator('.ant-alert-message').allTextContents()).join('\n'));
  }
  throw error;
} finally {
  await browser.close();
}
