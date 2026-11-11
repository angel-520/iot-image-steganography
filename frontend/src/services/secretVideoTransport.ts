import { CHUNK, HEIGHT, MAX_FILE, MAX_PACKET, WIDTH, concat, decodePixels, embedPixels, encodeFrame, tag } from './secretVideoCodec';
import type { Frame } from './secretVideoCodec';
import { equal, hash, open, receipt, seal } from './secretVideoCrypto';

export interface Person { id: string; name: string; audio: boolean; video: boolean }
export interface ChatItem { id: string; peer: string; direction: 'send' | 'receive'; type: string; text: string; progress: number; status: string; file?: { url: string; name: string; size: number; sha256: string }; latency?: number }
export interface Metrics { psnr?: number; pilotBer: number; corrected: number; good: number; bad: number; kbps: number; delivered: number; expected: number; latency?: number }
interface Transmission { id: number; total: number; chunks: Uint8Array[]; key: Uint8Array; digest: string; recipients: Set<string>; receipts: Set<string>; acks: Map<string, Uint8Array>; cursor: number; created: number; item: ChatItem; finished: boolean }
interface Reception { id: number; total: number; parts: Map<number, Uint8Array>; created: number; item: ChatItem; busy: boolean; tried?: string; packet?: Uint8Array; complete: boolean; receipt?: Frame }

export class VideoChannel {
  id = ''; room = ''; step = 24; camera = true; microphone = true; groupKey = ''; privateKeys = new Map<string, string>(); drop = 0;
  canvas = document.createElement('canvas'); raw = document.createElement('video'); stream = new MediaStream(); devices?: MediaStream;
  members = new Map<string, Person>();
  metrics: Metrics = { pilotBer: 0, corrected: 0, good: 0, bad: 0, kbps: 0, delivered: 0, expected: 0 };
  onItem: (item: ChatItem) => void; onMetrics: (metrics: Metrics) => void; onError: (message: string) => void;
  onDevices?: () => void;
  private tx = new Map<number, Transmission>(); private rx = new Map<string, Reception>();
  private controls = new Map<string, { frame: Frame; count: number }>(); private urls: string[] = [];
  private renderTimer?: number; private active?: Uint8Array; private activeUntil = 0; private lastControl = false; private round = 0;
  private decoders = new Map<string, { video: HTMLVideoElement; timer: number; stream: MediaStream }>();
  private lastMetric = 0; private closed = false;
  constructor(item: (item: ChatItem) => void, metrics: (metrics: Metrics) => void, error: (message: string) => void) {
    this.onItem = item; this.onMetrics = metrics; this.onError = error;
    this.canvas.width = WIDTH; this.canvas.height = HEIGHT; this.raw.muted = true; this.raw.playsInline = true;
  }
  async prepare() {
    if (!window.isSecureContext || !navigator.mediaDevices) throw Error('请使用 HTTPS 地址或 localhost 开启摄像头与通话加密。');
    try { this.devices = await navigator.mediaDevices.getUserMedia({ video: { width: 640, height: 480, frameRate: 15 }, audio: { echoCancellation: true, noiseSuppression: true } }); }
    catch {
      try { this.devices = await navigator.mediaDevices.getUserMedia({ audio: true }); this.camera = false; this.onError('未能开启摄像头，已切换为封面视频。'); }
      catch { this.devices = new MediaStream(); this.camera = this.microphone = false; this.onError('摄像头和麦克风不可用，已启用无声封面视频。'); }
    }
    for (const track of this.devices.getTracks()) track.addEventListener('ended', () => {
      if (this.closed) return;
      if (track.kind === 'video') { this.camera = false; this.onError('摄像头已断开或被其他程序占用，已切换为封面视频'); }
      else { this.microphone = false; this.onError('麦克风已断开，语音和转写已停止'); }
      this.onDevices?.();
    });
    this.raw.srcObject = this.devices; if (this.devices.getVideoTracks().length) await this.raw.play();
    const ctx = this.canvas.getContext('2d', { willReadFrequently: true })!;
    const render = () => {
      if (this.closed) return;
      if (this.camera && this.raw.readyState >= 2) ctx.drawImage(this.raw, 0, 0, WIDTH, HEIGHT);
      else { ctx.fillStyle = '#315567'; ctx.fillRect(0, 0, WIDTH, HEIGHT); ctx.fillStyle = '#dbe9ec'; ctx.font = '26px sans-serif'; ctx.fillText('ISTG · SecretVideo', 180, 220); ctx.font = '18px sans-serif'; ctx.fillText('摄像头已关闭', 195, 260); }
      if (this.id && performance.now() >= this.activeUntil) {
        const frame = this.next(); this.active = frame ? encodeFrame(frame) : undefined; this.activeUntil = performance.now() + 250;
      }
      if (this.active) { const image = ctx.getImageData(0, 0, WIDTH, HEIGHT); this.metrics.psnr = embedPixels(image.data, this.active, this.step); ctx.putImageData(image, 0, 0); }
      if (performance.now() - this.lastMetric > 500) { this.lastMetric = performance.now(); this.onMetrics({ ...this.metrics }); }
    };
    render(); this.renderTimer = window.setInterval(render, 1000 / 15);
    this.stream = this.canvas.captureStream(15);
    this.stream.getVideoTracks()[0].contentHint = 'detail';
    this.devices.getAudioTracks().forEach(track => this.stream.addTrack(track));
  }
  toggleCamera(enabled: boolean) { this.camera = enabled && Boolean(this.devices?.getVideoTracks().some(track => track.readyState === 'live')); this.devices?.getVideoTracks().forEach(track => { track.enabled = this.camera; }); this.onDevices?.(); }
  toggleMicrophone(enabled: boolean) { this.microphone = enabled && Boolean(this.devices?.getAudioTracks().some(track => track.readyState === 'live')); this.devices?.getAudioTracks().forEach(track => { track.enabled = this.microphone; }); this.onDevices?.(); }
  private password(peer?: string) {
    const value = peer ? this.privateKeys.get(peer) : this.groupKey;
    if (!value) return '';
    return peer ? `${this.room}:${[this.id, peer].sort().join(':')}:${value}` : `${this.room}:group:${value}`;
  }
  async send(type: 'text' | 'file' | 'speech', target: string, content: string | File) {
    const recipients = target === 'all' ? [...this.members.keys()] : [target];
    if (!this.id || !recipients.length || recipients.some(id => !this.members.has(id))) throw Error('请先加入房间并等待通话成员连接');
    if ([...this.tx.values()].filter(t => !t.finished).length >= 4) throw Error('最多同时发送 4 条消息或文件，请稍后重试');
    const password = this.password(target === 'all' ? undefined : target);
    if (!password) throw Error(target === 'all' ? '请先填写与其他成员一致的群聊密钥' : '请先为此成员设置双方一致的私聊密钥');
    const id = crypto.getRandomValues(new Uint32Array(1))[0];
    if (this.tx.has(id)) throw Error('消息编号冲突，请重试');
    const value: Record<string, unknown> = { v: 1, from: this.id, to: target, type, created: Date.now() };
    if (typeof content === 'string') {
      if (!content.trim() || new TextEncoder().encode(content).length > 8192) throw Error('文字不能为空且不能超过 8 KiB');
      value.text = content;
    } else {
      if (content.size > MAX_FILE) throw Error('文件不能超过 128 KiB');
      const data = new Uint8Array(await content.arrayBuffer());
      Object.assign(value, { data, name: content.name.slice(0, 120), mime: content.type, sha256: await hash(data) });
    }
    const result = await seal(value, password);
    if (this.closed) throw Error('通话已结束');
    const chunks = Array.from({ length: Math.ceil(result.packet.length / CHUNK) }, (_, i) => result.packet.slice(i * CHUNK, (i + 1) * CHUNK));
    const item: ChatItem = { id: `send-${id}`, peer: target, direction: 'send', type, text: typeof content === 'string' ? content : content.name, progress: 0, status: '等待发送' };
    const tx: Transmission = { id, total: chunks.length, chunks, key: result.key, digest: result.digest, recipients: new Set(recipients), receipts: new Set(), acks: new Map(), cursor: 0, created: performance.now(), item, finished: false };
    recipients.forEach(peer => tx.acks.set(peer, new Uint8Array(Math.ceil(chunks.length / 8))));
    this.tx.set(id, tx); this.metrics.expected += recipients.length; this.onItem({ ...item });
  }
  private control(frame: Frame, count = 1) { if (!this.closed) this.controls.set(`${frame.target}:${frame.id}:${frame.kind}`, { frame, count }); }
  private next(): Frame | undefined {
    const control = this.controls.entries().next().value;
    if (control && (!this.lastControl || ![...this.tx.values()].some(tx => !tx.finished))) {
      const [key, value] = control; this.controls.delete(key); if (--value.count > 0) this.controls.set(key, value);
      this.lastControl = true; return value.frame;
    }
    this.lastControl = false;
    const tasks = [...this.tx.values()].filter(tx => !tx.finished);
    if (!tasks.length) return;
    const tx = tasks[this.round++ % tasks.length];
    if (performance.now() - tx.created > 15 * 60_000) { tx.finished = true; tx.item.status = '发送超时，尚未确认全部成员收到'; this.onItem({ ...tx.item }); return; }
    let index = tx.cursor++ % tx.total;
    for (let i = 0; i < tx.total; i++) {
      const candidate = (index + i) % tx.total;
      if ([...tx.recipients].some(peer => !((tx.acks.get(peer)![candidate >> 3] >> (candidate & 7)) & 1))) { index = candidate; break; }
    }
    tx.cursor = index + 1;
    if (tx.item.status === '等待发送') { tx.item.status = '发送中，等待接收确认'; this.onItem({ ...tx.item }); }
    return { kind: 1, sender: tag(this.id), target: tx.item.peer === 'all' ? 0 : tag(tx.item.peer), id: tx.id, index, total: tx.total, data: tx.chunks[index] };
  }
  attach(peer: Person, stream: MediaStream) {
    if (this.decoders.has(peer.id)) return;
    const video = document.createElement('video'); video.muted = true; video.playsInline = true; video.srcObject = stream;
    const canvas = document.createElement('canvas'); canvas.width = WIDTH; canvas.height = HEIGHT;
    const ctx = canvas.getContext('2d', { willReadFrequently: true })!;
    const play = () => { void video.play().catch(error => { if (error?.name === 'NotAllowedError') this.onError('视频暂停播放，请点击“播放通话音频”重试'); }); };
    video.onloadedmetadata = play; play();
    let lastTime = -1;
    const timer = window.setInterval(() => {
      if (video.readyState < 2 || video.currentTime === lastTime || !this.id) return;
      if (video.paused) play();
      lastTime = video.currentTime;
      ctx.drawImage(video, 0, 0, WIDTH, HEIGHT);
      const decoded = decodePixels(ctx.getImageData(0, 0, WIDTH, HEIGHT).data, this.step);
      if (!decoded.detected) return;
      this.metrics.pilotBer = decoded.pilotBer; this.metrics.corrected += decoded.corrections;
      if (!decoded.frame) { this.metrics.bad++; return; }
      this.metrics.good++;
      if (Math.random() * 100 < this.drop) return;
      void this.receive(peer, decoded.frame).catch(error => this.onError(error instanceof Error ? error.message : '视频数据接收失败'));
    }, 100);
    this.decoders.set(peer.id, { video, timer, stream });
  }
  play() { for (const decoder of this.decoders.values()) void decoder.video.play(); }
  detach(id: string) {
    const decoder = this.decoders.get(id); if (decoder) { window.clearInterval(decoder.timer); decoder.video.pause(); decoder.video.srcObject = null; this.decoders.delete(id); }
    this.members.delete(id);
    for (const tx of this.tx.values()) if (!tx.finished && tx.recipients.has(id) && !tx.receipts.has(id)) { tx.finished = true; tx.item.status = '成员离开，未确认送达'; this.onItem({ ...tx.item }); }
  }
  private async receive(peer: Person, frame: Frame) {
    if (this.closed) return;
    if (frame.sender !== tag(peer.id) || frame.target !== 0 && frame.target !== tag(this.id)) return;
    if (frame.total < 1 || frame.total > Math.ceil(MAX_PACKET / CHUNK) || frame.index >= frame.total) return;
    if (frame.kind !== 1) {
      const tx = this.tx.get(frame.id); if (!tx || !tx.recipients.has(peer.id) || frame.total !== tx.total) return;
      if (frame.kind === 2 && frame.data.length === Math.ceil(tx.total / 8)) {
        const ack = tx.acks.get(peer.id)!; frame.data.forEach((n, i) => { ack[i] |= n; });
        const received = [...tx.acks.values()].map(bits => { let n = 0; for (let i = 0; i < tx.total; i++) n += bits[i >> 3] >> (i & 7) & 1; return n; });
        tx.item.progress = Math.floor(Math.min(...received) / tx.total * 100); this.onItem({ ...tx.item });
      }
      if (frame.kind === 3 && !tx.receipts.has(peer.id) && equal(frame.data, await receipt(tx.key, this.room, this.id, peer.id, frame.id, tx.digest))) {
        if (this.closed || tx.receipts.has(peer.id)) return;
        tx.receipts.add(peer.id); this.metrics.delivered++; this.metrics.latency = performance.now() - tx.created;
        tx.item.status = `已送达 ${tx.receipts.size}/${tx.recipients.size}`;
        if (tx.receipts.size === tx.recipients.size) { tx.finished = true; tx.item.progress = 100; tx.item.latency = performance.now() - tx.created; tx.item.status = '已送达'; }
        this.onItem({ ...tx.item });
        this.onMetrics({ ...this.metrics });
      }
      return;
    }
    if (frame.index < frame.total - 1 && frame.data.length !== CHUNK || !frame.data.length) return;
    const key = `${peer.id}-${frame.id}`;
    let rx = this.rx.get(key);
    if (!rx) {
      for (const [id, item] of this.rx) if (performance.now() - item.created > 20 * 60_000) this.rx.delete(id);
      const previous = [...this.rx.entries()].filter(([id]) => id.startsWith(peer.id));
      if (previous.filter(([, item]) => !item.complete).length >= 8) return;
      // A long speech/chat session must not fill its receive quota with completed messages.
      if (previous.length >= 256) {
        const oldestComplete = previous.find(([, item]) => item.complete);
        if (oldestComplete) this.rx.delete(oldestComplete[0]);
      }
      rx = { id: frame.id, total: frame.total, parts: new Map(), created: performance.now(), busy: false, complete: false,
        item: { id: `receive-${key}`, peer: peer.id, direction: 'receive', type: 'encrypted', text: '正在接收内容…', progress: 0, status: '接收中' } };
      this.rx.set(key, rx);
    }
    if (rx.total !== frame.total) return;
    if (rx.complete) { if (rx.receipt) this.control(rx.receipt); return; }
    if (!rx.parts.has(frame.index)) {
      rx.parts.set(frame.index, frame.data); rx.item.progress = Math.floor(rx.parts.size / rx.total * 100); this.onItem({ ...rx.item });
    }
    const bitmap = new Uint8Array(Math.ceil(rx.total / 8)); rx.parts.forEach((_, index) => { bitmap[index >> 3] |= 1 << (index & 7); });
    this.control({ kind: 2, sender: tag(this.id), target: tag(peer.id), id: frame.id, index: 0, total: rx.total, data: bitmap });
    if (rx.parts.size === rx.total && !rx.busy) {
      const password = this.password(frame.target === 0 ? undefined : peer.id);
      if (password === rx.tried) return;
      rx.tried = password; rx.busy = true;
      try {
        if (!password) throw Error('请填写对应的群聊或私聊密钥以解密');
        rx.packet ??= concat(...Array.from({ length: rx.total }, (_, i) => rx!.parts.get(i)!));
        const decoded = await open(rx.packet, password), value = decoded.value;
        if (this.closed) return;
        if (value?.v !== 1 || value.from !== peer.id || value.to !== (frame.target === 0 ? 'all' : this.id) || !['text', 'file', 'speech'].includes(value.type)) throw Error('消息身份校验失败');
        if (value.type === 'file') {
          if (!(value.data instanceof Uint8Array) || value.data.length > MAX_FILE || typeof value.name !== 'string' || value.name.length > 120 || value.sha256 !== await hash(value.data)) throw Error('文件完整性校验失败');
          if (this.closed) return;
          const url = URL.createObjectURL(new Blob([value.data.slice().buffer], { type: 'application/octet-stream' })); this.urls.push(url);
          rx.item.file = { url, name: value.name, size: value.data.length, sha256: value.sha256 }; rx.item.text = value.name;
        } else {
          if (typeof value.text !== 'string' || new TextEncoder().encode(value.text).length > 8192) throw Error('恢复文字长度超限');
          rx.item.text = value.text;
        }
        rx.item.type = value.type; rx.item.status = '已接收并验证'; rx.item.progress = 100; rx.complete = true;
        rx.receipt = { kind: 3, sender: tag(this.id), target: tag(peer.id), id: frame.id, index: 0, total: rx.total,
          data: await receipt(decoded.key, this.room, peer.id, this.id, frame.id, decoded.digest) };
        this.control(rx.receipt, 3); rx.parts.clear(); rx.packet = undefined;
      } catch (error) { rx.item.status = error instanceof Error ? error.message : '解密失败'; }
      finally { rx.busy = false; if (!this.closed) this.onItem({ ...rx.item }); }
    }
  }
  close() {
    this.closed = true; if (this.renderTimer) window.clearInterval(this.renderTimer);
    this.stream.getTracks().forEach(track => track.stop()); this.devices?.getTracks().forEach(track => track.stop());
    this.raw.pause(); this.raw.srcObject = null;
    this.active = undefined; this.canvas.width = WIDTH; // Clear the final camera / covert frame on leave.
    for (const id of [...this.decoders.keys()]) this.detach(id);
    this.urls.forEach(url => URL.revokeObjectURL(url)); this.tx.clear(); this.rx.clear(); this.controls.clear(); this.privateKeys.clear(); this.groupKey = '';
  }
}
