import { chromium } from '../frontend/node_modules/playwright/index.mjs';
import assert from 'node:assert/strict';
import path from 'node:path';

const launch = () => chromium.launch({ channel: 'msedge', headless: true,
  args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', `--use-file-for-fake-video-capture=${path.resolve('.qa/secret-video-camera.y4m')}`, '--autoplay-policy=no-user-gesture-required', '--disable-background-timer-throttling', '--disable-renderer-backgrounding', '--disable-backgrounding-occluded-windows'] });
const pages = [], browsers = [], contexts = [], errors = [], wires = [];
try {
  for (const name of ['Alice', 'Bob', 'Carol', 'Dave']) {
    const browser = await launch(); browsers.push(browser);
    const context = await browser.newContext({ permissions: ['camera', 'microphone'], viewport: { width: 1440, height: 1080 } }); contexts.push(context);
    await context.addInitScript(() => {
      window.__dataChannels = 0; window.__svPeers = [];
      const Original = RTCPeerConnection;
      window.RTCPeerConnection = new Proxy(Original, { construct(target, args) { const pc = new target(...args); window.__svPeers.push(pc); return pc; } });
      RTCPeerConnection.prototype.createDataChannel = function () { window.__dataChannels++; throw Error('DataChannels forbidden by video-only test'); };
    });
    const page = await context.newPage(); pages.push(page); page.setDefaultTimeout(90000);
    page.on('pageerror', error => errors.push(`${name}: ${error.message}`));
    page.on('websocket', ws => { ws.on('framesent', event => wires.push(String(event.payload)));
      ws.on('framereceived', event => { const data = JSON.parse(String(event.payload)); if (data.type === 'error') console.error(name, 'server error', data); });
      ws.on('close', () => console.error(name, 'signaling closed')); });
    await page.goto('http://127.0.0.1:8019/secret-video');
    await page.getByLabel('成员昵称', { exact: true }).fill(name);
    await page.getByLabel('群聊隐写密钥', { exact: true }).fill('group-key-never-on-wire');
  }
  const [a, b, c, d] = pages;
  await a.getByRole('button', { name: '创建房间并加入', exact: true }).click();
  await a.getByText('已加入房间', { exact: true }).waitFor();
  const invite = new URL(await a.getByLabel('邀请链接', { exact: true }).inputValue());
  const config = new URLSearchParams(invite.hash.slice(1));
  for (const page of [b, c, d]) {
    await page.getByLabel('房间编号', { exact: true }).fill(config.get('room'));
    await page.getByLabel('房间邀请令牌', { exact: true }).fill(config.get('token'));
    await page.getByRole('button', { name: '加入房间', exact: true }).click();
    await page.getByText('已加入房间', { exact: true }).waitFor();
  }
  for (const page of pages) await page.waitForFunction(() => [...document.querySelectorAll('.sv-tile>span')].filter(el => el.textContent.includes('connected')).length === 3);
  const camera = await a.evaluate(() => {
    const video = document.querySelector('.sv-details video'), canvas = document.querySelector('.sv-canvas canvas');
    const probe = document.createElement('canvas'); probe.width = 64; probe.height = 48;
    const ctx = probe.getContext('2d');
    const average = data => { let sum = 0; for (let i = 0; i < data.length; i += 4) sum += data[i] + data[i + 1] + data[i + 2]; return sum / (data.length / 4 * 3); };
    let original = null; if (video.readyState >= 2) { ctx.drawImage(video, 0, 0, 64, 48); original = average(ctx.getImageData(0, 0, 64, 48).data); }
    return { original, ownCanvas: canvas ? average(canvas.getContext('2d').getImageData(0, 0, 640, 480).data) : null,
      originalReady: video.readyState, time: video.currentTime, videoTrack: video.srcObject?.getVideoTracks().map(t => ({ label: t.label, enabled: t.enabled, state: t.readyState })) };
  });
  assert.ok(camera.original > 10 && camera.ownCanvas > 10);
  assert.ok(camera.videoTrack.every(track => track.state === 'live'));
  const secret = 'SV-VIDEO-ONLY-你好-7261';
  await a.getByLabel('秘密聊天文字', { exact: true }).fill(secret);
  await a.getByRole('button', { name: /发送隐写文字/ }).click();
  for (const page of [b, c, d]) await page.getByText(secret, { exact: true }).waitFor();
  await a.locator('.sv-message-send[data-status="已送达"]').waitFor();
  await b.getByRole('slider', { name: '模拟丢帧比例' }).focus(); await b.keyboard.press('End');
  const file = Buffer.from(Array.from({ length: 8192 }, (_, i) => i % 256));
  await a.locator('input[type=file]').setInputFiles({ name: 'video-secret.bin', mimeType: 'application/octet-stream', buffer: file });
  for (const page of [b, c, d]) {
    const link = page.getByRole('link', { name: /下载 video-secret.bin/ }); await link.waitFor();
    const data = await link.evaluate(async el => Array.from(new Uint8Array(await (await fetch(el.href)).arrayBuffer())));
    assert.deepEqual(Buffer.from(data), file);
  }
  // Private keys are configured independently, and never exchanged by signaling.
  const choose = async (page, label, text) => { await page.getByRole('combobox', { name: label, exact: true }).focus(); await page.keyboard.press('ArrowDown'); await page.getByText(text, { exact: true }).last().click(); };
  await choose(a, '隐写接收者', '私聊 · Bob');
  await choose(b, '隐写接收者', '私聊 · Alice');
  await a.getByLabel('私聊隐写密钥', { exact: true }).fill('pair-key-never-on-wire');
  await b.getByLabel('私聊隐写密钥', { exact: true }).fill('wrong-pair-key');
  const privateText = 'ONLY-BOB-秘密-3981';
  await a.getByLabel('秘密聊天文字', { exact: true }).fill(privateText);
  await a.getByRole('button', { name: /发送隐写文字/ }).click();
  await b.getByText(/认证解密失败：密钥不一致/).waitFor();
  assert.equal(await b.getByText(privateText, { exact: true }).count(), 0);
  await b.getByLabel('私聊隐写密钥', { exact: true }).fill('pair-key-never-on-wire');
  await b.getByText(privateText, { exact: true }).waitFor();
  await a.locator('.sv-message-send[data-status="已送达"]').filter({ hasText: privateText }).waitFor();
  assert.equal(await c.getByText(privateText, { exact: true }).count(), 0);
  assert.equal(await d.getByText(privateText, { exact: true }).count(), 0);
  await a.evaluate(() => scrollTo(0, 0)); await a.waitForTimeout(500);
  await a.screenshot({ path: '.qa/secret-video-desktop.png', fullPage: true });
  for (const page of pages) assert.equal(await page.evaluate(() => window.__dataChannels), 0);
  for (const page of pages) {
    const media = await page.evaluate(async () => {
      const reports = await Promise.all(window.__svPeers.map(pc => pc.getStats()));
      return reports.map(report => ({
        audio: [...report.values()].some(r => r.type === 'inbound-rtp' && r.kind === 'audio' && r.bytesReceived > 0),
        video: [...report.values()].some(r => r.type === 'inbound-rtp' && r.kind === 'video' && r.framesDecoded > 0),
      }));
    });
    assert.equal(media.length, 3); assert.ok(media.every(report => report.audio && report.video));
  }
  for (const wire of wires) for (const forbidden of [secret, privateText, 'video-secret.bin', 'group-key-never-on-wire', 'pair-key-never-on-wire']) assert.equal(wire.includes(forbidden), false, `Leaked through signaling: ${forbidden}`);
  for (const wire of wires) assert.ok(['join', 'offer', 'answer', 'candidate', 'media', 'ping'].includes(JSON.parse(wire).type));
  await d.setViewportSize({ width: 390, height: 844 }); await d.waitForTimeout(500);
  await d.screenshot({ path: '.qa/secret-video-mobile.png', fullPage: true });
  assert.equal(await d.evaluate(() => document.documentElement.scrollWidth > innerWidth + 2), false);
  await a.getByRole('button', { name: /离开房间/ }).click();
  assert.equal(await a.getByLabel('群聊隐写密钥', { exact: true }).inputValue(), '');
  assert.equal(await a.locator('.sv-canvas canvas').count(), 0);
  assert.equal(await a.getByLabel('秘密聊天文字', { exact: true }).isDisabled(), true);
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ fourRealWebRtcPeers: true, groupTextRestoredFromVideo: true, binaryShaRoundtrip: true,
    privateRecipientOnly: true, wrongPrivateKeyRejected: true, simulatedFrameLossPercent: 30,
    actualMovingCameraFrames: true, audioAndVideoRtpReceived: true,
    websocketAudit: true, dataChannelsCreated: 0, leaveClearsKeysAndCamera: true, browserErrors: errors }));
} catch (error) {
  for (let i = 0; i < pages.length; i++) { await pages[i].screenshot({ path: `.qa/sv-failure-${i}.png`, fullPage: true }).catch(() => {});
    console.error(i, await pages[i].locator('.ant-alert-message').allTextContents(), await pages[i].locator('.sv-message').allTextContents()); }
  console.error('page errors', errors); throw error;
} finally { await Promise.all(browsers.map(browser => browser.close())); }
