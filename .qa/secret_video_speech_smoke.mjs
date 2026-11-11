import { chromium } from '../frontend/node_modules/playwright/index.mjs';
import assert from 'node:assert/strict';
import path from 'node:path';
const chinese = process.env.SV_SPEECH_LANGUAGE === 'cn';
const browser = await chromium.launch({ channel: 'msedge', headless: true, args: [
  '--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream',
  `--use-file-for-fake-video-capture=${path.resolve('.qa/secret-video-camera.y4m')}`,
  `--use-file-for-fake-audio-capture=${path.resolve(chinese ? '.qa/secret-video-zh-mic.wav' : '.qa/secret-video-mic.wav')}`,
  '--autoplay-policy=no-user-gesture-required', '--disable-background-timer-throttling', '--disable-renderer-backgrounding'] });
const pages = [], wires = [], errors = [];
try {
  for (const name of ['Speech sender', 'Speech receiver']) {
    const context = await browser.newContext({ permissions: ['camera', 'microphone'], viewport: { width: 1440, height: 1080 } });
    const page = await context.newPage(); pages.push(page); page.setDefaultTimeout(120000);
    page.on('pageerror', error => errors.push(error.message));
    page.on('console', item => { if (item.type() === 'error') console.error(name, item.text().slice(0, 350)); });
    page.on('websocket', ws => ws.on('framesent', event => wires.push(String(event.payload))));
    await page.goto('http://127.0.0.1:8019/secret-video');
    await page.getByLabel('成员昵称', { exact: true }).fill(name);
    await page.getByLabel('群聊隐写密钥', { exact: true }).fill('local-speech-key');
  }
  const [a, b] = pages;
  await a.getByRole('button', { name: '创建房间并加入', exact: true }).click();
  await a.getByText('已加入房间', { exact: true }).waitFor();
  const params = new URLSearchParams(new URL(await a.getByLabel('邀请链接', { exact: true }).inputValue()).hash.slice(1));
  await b.getByLabel('房间编号', { exact: true }).fill(params.get('room'));
  await b.getByLabel('房间邀请令牌', { exact: true }).fill(params.get('token'));
  await b.getByRole('button', { name: '加入房间', exact: true }).click();
  await b.waitForFunction(() => document.querySelector('.sv-tile:last-child>span')?.textContent.includes('connected'));
  await a.getByRole('combobox', { name: '离线转写语言' }).focus(); await a.keyboard.press('ArrowDown');
  await a.getByText(chinese ? '中文' : '英文', { exact: true }).last().click();
  await a.getByRole('switch', { name: '实时通话隐写' }).click();
  console.log('Speech mode requested; waiting for offline model / microphone transcript');
  const recognized = b.locator('.sv-message-receive').filter({ hasText: '语音转写' }).filter({ hasText: chinese ? /你好|秘密|视频|通信|测试/ : /hello|video|communication/i }).first();
  await Promise.race([recognized.waitFor(), a.getByText(/语音识别加载失败|语音识别加载超时|语音识别资源不可用/).waitFor().then(async () => { throw Error((await a.locator('.ant-alert-message').allTextContents()).join('; ')); })]);
  const transcript = await recognized.locator('p').first().textContent();
  assert.ok(transcript.trim().length > (chinese ? 3 : 8));
  await a.getByRole('switch', { name: '实时通话隐写' }).click();
  assert.equal(await a.getByRole('switch', { name: '实时通话隐写' }).getAttribute('aria-checked'), 'false');
  await a.waitForFunction(() => document.querySelector('.sv-tile:last-child>span')?.textContent.includes('connected'));
  for (const wire of wires) { assert.equal(wire.includes(transcript), false); assert.equal(wire.includes('local-speech-key'), false); }
  await b.screenshot({ path: '.qa/secret-video-speech.png', fullPage: true });
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ realOfflineVoskMicrophoneRecognition: true, transcript, restoredFromWebRtcVideo: true,
    speechStopPreservesCall: true, noSpeechOverSignaling: true, browserErrors: errors }));
} catch (error) {
  for (let i = 0; i < pages.length; i++) { console.error(i, await pages[i].locator('.ant-alert-message').allTextContents()); await pages[i].screenshot({ path: `.qa/sv-speech-failure-${i}.png`, fullPage: true }).catch(() => {}); }
  console.error(errors); throw error;
} finally { await browser.close(); }
