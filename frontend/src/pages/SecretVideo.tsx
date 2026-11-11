import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Card, Descriptions, Input, List, Progress, Select, Slider, Space, Statistic, Switch, Tag, Upload, message } from 'antd';
import { CopyOutlined, DownloadOutlined, PhoneOutlined, SendOutlined, UploadOutlined, VideoCameraOutlined } from '@ant-design/icons';
import { api, errorMessage, formatBytes } from '../services/api';
import { ResultCharts } from '../components/ResultCharts';
import { VideoChannel } from '../services/secretVideoTransport';
import type { ChatItem, Metrics, Person } from '../services/secretVideoTransport';
import { selfTest } from '../services/secretVideoCodec';
import { LocalSpeech } from '../services/secretVideoSpeech';
import '../styles/secret-video.css';

interface Peer { pc: RTCPeerConnection; person: Person; stream: MediaStream; pending: RTCIceCandidateInit[] }
interface Session { channel: VideoChannel; ws?: WebSocket; peers: Map<string, Peer>; stats?: number; ping?: number; closing: boolean; previous: Map<string, { bytes: number; time: number }> }
const initialMetrics: Metrics = { pilotBer: 0, corrected: 0, good: 0, bad: 0, kbps: 0, delivered: 0, expected: 0 };
function StreamVideo({ stream, muted = false, label }: { stream?: MediaStream; muted?: boolean; label: string }) {
  const ref = useRef<HTMLVideoElement>(null);
  useEffect(() => { const video = ref.current!; video.srcObject = stream ?? null; if (stream) void video.play().catch(() => {}); return () => { video.srcObject = null; }; }, [stream]);
  return <video ref={ref} aria-label={label} autoPlay playsInline muted={muted} className="sv-video" />;
}

export default function SecretVideo() {
  const invitation = new URLSearchParams(window.location.hash.slice(1));
  const [room, setRoom] = useState(invitation.get('room') ?? ''), [token, setToken] = useState(invitation.get('token') ?? '');
  const [name, setName] = useState('成员'), [key, setKey] = useState(''), [privateKeys, setPrivateKeys] = useState<Record<string, string>>({});
  const [state, setState] = useState('尚未加入房间'), [busy, setBusy] = useState(false), [connected, setConnected] = useState(false);
  const [error, setError] = useState<string>(), [people, setPeople] = useState<Person[]>([]), [revision, setRevision] = useState(0);
  const [camera, setCamera] = useState(true), [microphone, setMicrophone] = useState(true), [target, setTarget] = useState('all');
  const [text, setText] = useState(''), [sending, setSending] = useState(false), [items, setItems] = useState<ChatItem[]>([]);
  const [metrics, setMetrics] = useState<Metrics>(initialMetrics), [history, setHistory] = useState<{ time: number; psnr?: number; ber: number }[]>([]);
  const [qim, setQim] = useState(24), [bitrate, setBitrate] = useState(2500000), [drop, setDrop] = useState(0), [test, setTest] = useState('未执行');
  const [speech, setSpeech] = useState(false), [speechBusy, setSpeechBusy] = useState(false), [partial, setPartial] = useState(''), [language, setLanguage] = useState('cn');
  const session = useRef<Session>(), speechEngine = useRef<LocalSpeech>(), stage = useRef<HTMLDivElement>(null);
  const selected = useRef(target); selected.current = target;
  const keyValue = useRef(key); keyValue.current = key;
  const bandwidth = useRef(bitrate); bandwidth.current = bitrate;
  const speechAttempt = useRef(0);

  const updateItem = (item: ChatItem) => setItems(current => {
    const index = current.findIndex(old => old.id === item.id);
    if (index < 0) return [...current.slice(-199), item];
    return current.map((old, i) => i === index ? item : old);
  });
  const end = (update = true) => {
    const current = session.current; if (!current) return;
    current.closing = true; session.current = undefined;
    speechAttempt.current++;
    speechEngine.current?.close(); speechEngine.current = undefined;
    if (current.stats) window.clearInterval(current.stats); if (current.ping) window.clearInterval(current.ping);
    current.ws?.close(); current.peers.forEach(peer => peer.pc.close()); current.channel.close();
    if (update) { setConnected(false); setBusy(false); setState('通话已结束'); setPeople([]); setSpeech(false); setSpeechBusy(false); setPartial(''); setItems([]); setKey(''); keyValue.current = ''; setPrivateKeys({}); setTarget('all'); setCamera(false); setMicrophone(false); setRevision(value => value + 1); }
  };
  useEffect(() => () => end(false), []);
  useEffect(() => { if (stage.current) stage.current.replaceChildren(...(session.current ? [session.current.channel.canvas] : [])); }, [revision, connected]);
  const sendSignal = (current: Session, value: unknown) => { if (current.ws?.readyState === WebSocket.OPEN) current.ws.send(JSON.stringify(value)); };
  const configure = async (pc: RTCPeerConnection) => {
    for (const sender of pc.getSenders()) if (sender.track?.kind === 'video') {
      const params = sender.getParameters(); if (!params.encodings.length) continue;
      params.encodings[0].maxBitrate = bandwidth.current; params.encodings[0].maxFramerate = 15;
      params.encodings[0].scaleResolutionDownBy = 1; params.degradationPreference = 'maintain-resolution';
      try { await sender.setParameters(params); } catch { setError('码率设置未生效，请重试'); }
    }
  };
  const makePeer = async (current: Session, person: Person, ice: RTCIceServer[], offer: boolean) => {
    if (current.peers.has(person.id)) return current.peers.get(person.id)!;
    const pc = new RTCPeerConnection({ iceServers: ice, bundlePolicy: 'max-bundle' });
    const peer: Peer = { pc, person, stream: new MediaStream(), pending: [] }; current.peers.set(person.id, peer);
    current.channel.members.set(person.id, person);
    current.channel.stream.getTracks().forEach(track => pc.addTrack(track, current.channel.stream));
    const codecs = RTCRtpReceiver.getCapabilities('video')?.codecs ?? [];
    const preferred = [...codecs.filter(codec => codec.mimeType.toLowerCase() === 'video/vp8'), ...codecs.filter(codec => codec.mimeType.toLowerCase() !== 'video/vp8')];
    pc.getTransceivers().filter(t => t.sender.track?.kind === 'video').forEach(t => { if (preferred.length && t.setCodecPreferences) t.setCodecPreferences(preferred); });
    pc.onicecandidate = event => { if (event.candidate) sendSignal(current, { type: 'candidate', target: person.id, candidate: event.candidate.toJSON() }); };
    pc.ondatachannel = event => { event.channel.close(); setError('收到不支持的通话连接请求'); };
    pc.ontrack = event => {
      if (event.streams[0]) peer.stream = event.streams[0]; else peer.stream.addTrack(event.track);
      current.channel.attach(person, peer.stream); setRevision(value => value + 1);
    };
    pc.onconnectionstatechange = () => {
      if (pc.connectionState === 'connected') void configure(pc);
      if (pc.connectionState === 'failed') setError(`与 ${person.name} 的通话连接失败，请检查网络后重新加入`);
      setRevision(value => value + 1);
    };
    if (offer) { await pc.setLocalDescription(await pc.createOffer()); sendSignal(current, { type: 'offer', target: person.id, description: pc.localDescription?.toJSON() }); }
    return peer;
  };
  const join = async (roomId = room, invitationToken = token) => {
    if (session.current || !name.trim()) return;
    setBusy(true); setError(undefined); setState('正在开启摄像头与麦克风…'); setItems([]); setMetrics({ ...initialMetrics }); setHistory([]);
    const result = selfTest(); setTest(result.ok ? '自检通过' : '失败');
    if (!result.ok) { setError('视频编码器自检失败'); setBusy(false); return; }
    const channel = new VideoChannel(updateItem, value => { setMetrics(value); setHistory(old => [...old.slice(-59), { time: Date.now(), psnr: value.psnr, ber: value.pilotBer * 100 }]); }, setError);
    const current: Session = { channel, peers: new Map(), closing: false, previous: new Map() }; session.current = current;
    channel.onDevices = () => {
      if (current.closing || session.current !== current) return;
      setCamera(channel.camera); setMicrophone(channel.microphone);
      if (!channel.microphone) void toggleSpeech(false);
      sendSignal(current, { type: 'media', audio: channel.microphone, video: channel.camera });
    };
    channel.room = roomId; channel.groupKey = keyValue.current; channel.drop = drop;
    try {
      await channel.prepare(); if (current.closing) return;
      setCamera(channel.camera); setMicrophone(channel.microphone); setRevision(value => value + 1);
      const url = new URL(`/api/secret-video/rooms/${encodeURIComponent(roomId)}/signal`, window.location.href); url.protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
      const ws = new WebSocket(url); current.ws = ws;
      ws.onopen = () => sendSignal(current, { type: 'join', token: invitationToken, name: name.trim().slice(0, 32) });
      let queue = Promise.resolve(); let ice: RTCIceServer[] = [];
      ws.onmessage = event => { queue = queue.then(async () => {
        if (current.closing) return;
        const value = JSON.parse(String(event.data));
        if (value.type === 'welcome') {
          channel.id = value.id; channel.step = value.qim_step; ice = value.ice_servers;
          setQim(value.qim_step); setConnected(true); setBusy(false); setState('已加入房间'); setPeople(value.members);
          for (const person of value.members as Person[]) await makePeer(current, person, ice, true);
          sendSignal(current, { type: 'media', audio: channel.microphone, video: channel.camera });
        } else if (value.type === 'joined') {
          setPeople(old => [...old.filter(p => p.id !== value.member.id), value.member]);
          await makePeer(current, value.member, ice, false);
        } else if (value.type === 'left') {
          current.peers.get(value.id)?.pc.close(); current.peers.delete(value.id); channel.detach(value.id);
          setPeople(old => old.filter(p => p.id !== value.id)); if (selected.current === value.id) setTarget('all');
        } else if (value.type === 'media') {
          setPeople(old => old.map(person => person.id === value.member.id ? value.member : person));
        } else if (value.type === 'error') { setError(value.message); }
        else if (['offer', 'answer', 'candidate'].includes(value.type)) {
          const peer = current.peers.get(value.from); if (!peer) throw Error('成员连接信息无效，请重新加入房间');
          if (value.type === 'candidate') {
            if (peer.pc.remoteDescription) await peer.pc.addIceCandidate(value.candidate); else peer.pending.push(value.candidate);
          } else {
            await peer.pc.setRemoteDescription(value.description);
            for (const candidate of peer.pending.splice(0)) await peer.pc.addIceCandidate(candidate);
            if (value.type === 'offer') { await peer.pc.setLocalDescription(await peer.pc.createAnswer()); sendSignal(current, { type: 'answer', target: value.from, description: peer.pc.localDescription?.toJSON() }); }
          }
        }
      }).catch(error => setError(errorMessage(error))); };
      ws.onerror = () => setError('无法连接房间，请检查网络或 HTTPS 证书');
      ws.onclose = () => { if (!current.closing) { end(); setError('房间连接已中断，请重新加入'); } };
      current.ping = window.setInterval(() => sendSignal(current, { type: 'ping' }), 25000);
      current.stats = window.setInterval(() => { void (async () => {
        let kbps = 0;
        for (const [id, peer] of current.peers) for (const report of (await peer.pc.getStats()).values()) {
          if (report.type === 'outbound-rtp' && report.kind === 'video') {
            const key = `${id}:${report.id}`, previous = current.previous.get(key);
            if (previous && report.timestamp > previous.time) kbps += (report.bytesSent - previous.bytes) * 8 / (report.timestamp - previous.time);
            current.previous.set(key, { bytes: report.bytesSent, time: report.timestamp });
          }
        }
        channel.metrics.kbps = kbps;
      })().catch(() => {}); }, 2000);
    } catch (error) { end(); setError(errorMessage(error)); }
  };
  const create = async () => {
    setBusy(true); setError(undefined);
    try { const { data } = await api.post('/secret-video/rooms', { qim_step: qim }); setRoom(data.room_id); setToken(data.token); window.history.replaceState(null, '', `#room=${data.room_id}&token=${data.token}`); await join(data.room_id, data.token); }
    catch (error) { setError(errorMessage(error)); } finally { setBusy(false); }
  };
  const send = async (type: 'text' | 'file', content: string | File) => {
    setSending(true); setError(undefined);
    try { const current = session.current; if (!current || ![...current.peers.values()].some(peer => peer.pc.connectionState === 'connected')) throw Error('请等待其他成员连接后再发送'); await current.channel.send(type, target, content); if (type === 'text') setText(''); }
    catch (error) { setError(errorMessage(error)); } finally { setSending(false); }
  };
  const toggleSpeech = async (enabled: boolean) => {
    const attempt = ++speechAttempt.current;
    if (!enabled) { speechEngine.current?.stop(); setSpeech(false); setSpeechBusy(false); setPartial(''); return; }
    setSpeechBusy(true); setError(undefined);
    try {
      const current = session.current; if (!current?.channel.devices) throw Error('请先加入通话');
      speechEngine.current ??= new LocalSpeech();
      await speechEngine.current.start(current.channel.devices, `/models/vosk-small-${language}.tar.gz`, transcript => {
        setPartial(transcript); void current.channel.send('speech', selected.current, transcript).catch(error => setError(`转写片段未发送：${errorMessage(error)}`));
      }, setPartial);
      if (attempt === speechAttempt.current && session.current === current && current.channel.microphone) setSpeech(true);
    } catch (error) { if (attempt === speechAttempt.current) { setSpeech(false); if (session.current) setError(errorMessage(error)); } }
    finally { if (attempt === speechAttempt.current) setSpeechBusy(false); }
  };
  const invitationUrl = room && token ? `${location.origin}/secret-video#room=${room}&token=${token}` : '';
  const local = session.current?.channel;
  const readablePeer = (id: string) => id === 'all' ? '全体成员' : people.find(person => person.id === id)?.name ?? id.slice(0, 8);
  return <div className="sv-page"><div className="page-heading"><div><div className="eyebrow">SecretVideo / 多人实时视频隐写</div><h1>在画面里，传递秘密</h1><p>2–4 人通话，分享加密文字、文件与语音转写。</p></div></div>
    {!window.isSecureContext && <Alert showIcon type="error" message="请使用 HTTPS 地址或 localhost 开启摄像头与通话加密。" />}
    {error && <Alert closable showIcon type="warning" message={error} onClose={() => setError(undefined)} />}
    <Card title="房间与密钥" extra={<Tag color={connected ? 'success' : 'default'}>{state}</Tag>}><div className="sv-setup"><Input aria-label="成员昵称" maxLength={32} value={name} disabled={connected || busy} onChange={event => setName(event.target.value)} placeholder="成员昵称" /><Input aria-label="房间编号" value={room} disabled={connected || busy} onChange={event => setRoom(event.target.value)} placeholder="房间编号" /><Input.Password aria-label="房间邀请令牌" value={token} disabled={connected || busy} onChange={event => setToken(event.target.value)} placeholder="邀请令牌" /><Input.Password aria-label="群聊隐写密钥" autoComplete="new-password" value={key} onChange={event => { setKey(event.target.value); if (local) local.groupKey = event.target.value; }} placeholder="输入与其他成员一致的群聊密钥" /></div>
      <Space wrap style={{ marginTop: 16 }}><Button type="primary" disabled={connected || busy || !name.trim()} loading={busy} onClick={() => void create()}>创建房间并加入</Button><Button disabled={connected || busy || !room || !token || !name.trim()} onClick={() => void join()}>加入房间</Button><Button icon={<CopyOutlined />} disabled={!invitationUrl} onClick={() => void navigator.clipboard.writeText(invitationUrl).then(() => message.success('邀请链接已复制'))}>复制邀请链接</Button><Button danger icon={<PhoneOutlined />} disabled={!connected} onClick={() => end()}>离开房间</Button></Space>
      {invitationUrl && <Input readOnly aria-label="邀请链接" value={invitationUrl} style={{ marginTop: 12 }} />}
      <p className="sv-note">请与其他成员约定一致的密钥，离开房间前请保存文件。</p>
    </Card>
    <div className="sv-main"><div className="sv-stack"><Card title={<Space><VideoCameraOutlined />实时通话<Tag>{connected ? people.length + 1 : 0}/4 人</Tag></Space>} extra={<Button size="small" disabled={!connected} onClick={() => { document.querySelectorAll<HTMLVideoElement>('.sv-video').forEach(video => void video.play()); local?.play(); }}>播放通话音频</Button>}>
      <div className="sv-grid"><div className="sv-tile"><div className="sv-canvas" ref={stage} /><span>{name} · 我</span></div>{people.map(person => <div className="sv-tile" key={person.id}><StreamVideo stream={session.current?.peers.get(person.id)?.stream} label={`${person.name} 的接收视频`} /><span>{person.name} · {session.current?.peers.get(person.id)?.pc.connectionState ?? '等待连接'}</span></div>)}</div>
      <Space wrap style={{ marginTop: 16 }}><span>摄像头 <Switch aria-label="摄像头" checked={camera} disabled={!connected || !local?.devices?.getVideoTracks().some(track => track.readyState === 'live')} onChange={value => local?.toggleCamera(value)} /></span><span>麦克风 <Switch aria-label="麦克风" checked={microphone} disabled={!connected || !local?.devices?.getAudioTracks().some(track => track.readyState === 'live')} onChange={value => local?.toggleMicrophone(value)} /></span></Space>
      <details className="sv-details"><summary>摄像头原画面</summary><StreamVideo stream={local?.devices} muted label="原始摄像头视频" /></details>
    </Card><Card title="通话质量"><div className="sv-metrics"><Statistic title="发送视频码率 (kbps)" value={metrics.kbps.toFixed(1)} /><Statistic title="嵌入 PSNR (dB)" value={metrics.psnr === undefined ? '—' : metrics.psnr.toFixed(2)} /><Statistic title="导频 BER" value={metrics.good + metrics.bad ? (metrics.pilotBer * 100).toFixed(2) : '—'} suffix="%" /><Statistic title="确认送达率" value={metrics.expected ? (metrics.delivered / metrics.expected * 100).toFixed(1) : '—'} suffix="%" /></div>
      <Descriptions size="small" column={{ xs: 1, sm: 2 }} items={[{ key: 'frames', label: '有效 / 无效帧', children: `${metrics.good} / ${metrics.bad}` }, { key: 'fec', label: '纠错位数', children: metrics.corrected }, { key: 'latency', label: '最近送达耗时', children: metrics.latency === undefined ? '—' : `${(metrics.latency / 1000).toFixed(2)} s` }, { key: 'qim', label: '视频规格', children: '640×480 / 15 fps' }]} />
      <ResultCharts height={220} option={{ color: ['#0d8f87', '#19384b'], tooltip: { trigger: 'axis' }, legend: { data: ['嵌入 PSNR', '导频 BER %'] }, xAxis: { type: 'time' }, yAxis: [{ type: 'value', name: 'dB' }, { type: 'value', name: '%' }], grid: { top: 45, bottom: 35, left: 50, right: 45 }, series: [{ name: '嵌入 PSNR', type: 'line', showSymbol: false, data: history.filter(row => row.psnr !== undefined).map(row => [row.time, row.psnr]) }, { name: '导频 BER %', type: 'line', yAxisIndex: 1, showSymbol: false, data: history.map(row => [row.time, row.ber]) }] }} />

    </Card></div>
    <div className="sv-stack"><Card title="视频隐写聊天"><Select aria-label="隐写接收者" value={target} style={{ width: '100%' }} onChange={setTarget} options={[{ value: 'all', label: '群聊 · 全体成员' }, ...people.map(person => ({ value: person.id, label: `私聊 · ${person.name}` }))]} />
      {target !== 'all' && <Input.Password aria-label="私聊隐写密钥" autoComplete="new-password" value={privateKeys[target] ?? ''} onChange={event => { setPrivateKeys(old => ({ ...old, [target]: event.target.value })); local?.privateKeys.set(target, event.target.value); }} placeholder="与此成员约定的独立私聊密钥" style={{ marginTop: 12 }} />}
      <div className="sv-chat" aria-label="隐写聊天记录" aria-live="polite">{items.length ? items.map(item => <div key={item.id} className={`sv-message sv-message-${item.direction}`} data-status={item.status}><Space wrap><Tag>{item.direction === 'send' ? `我 → ${readablePeer(item.peer)}` : `${readablePeer(item.peer)} → 我`}</Tag><small>{item.type === 'speech' ? '语音转写' : item.type === 'file' ? '文件' : item.type === 'encrypted' ? '加密数据' : '文字'}</small></Space><p>{item.text}</p>{item.progress < 100 && <Progress size="small" percent={item.progress} />}<small>{item.status}{item.latency !== undefined ? ` · ${(item.latency / 1000).toFixed(2)} s` : ''}</small>{item.file && <><p><Button icon={<DownloadOutlined />} href={item.file.url} download={item.file.name}>下载 {item.file.name} · {formatBytes(item.file.size)}</Button></p><code className="sv-hash">SHA-256: {item.file.sha256}</code></>}</div>) : <p className="sv-note">加入房间后，即可发送文字或文件。</p>}</div>
      <Input.TextArea aria-label="秘密聊天文字" value={text} maxLength={2048} rows={3} onChange={event => setText(event.target.value)} placeholder="输入要发送的文字" disabled={!connected} />
      <Space wrap style={{ marginTop: 12 }}><Button type="primary" icon={<SendOutlined />} disabled={!connected || !people.length || !text.trim()} loading={sending} onClick={() => void send('text', text)}>发送隐写文字</Button><Upload showUploadList={false} disabled={!connected || !people.length || sending} beforeUpload={file => { void send('file', file); return false; }}><Button icon={<UploadOutlined />} disabled={!connected || !people.length || sending}>发送文件 · ≤128 KiB</Button></Upload></Space>
      <div className="sv-speech"><Space wrap><span>语音转写</span><Switch aria-label="实时通话隐写" checked={speech} loading={speechBusy} disabled={!connected || !microphone} onChange={value => void toggleSpeech(value)} /><Select aria-label="离线转写语言" value={language} disabled={speech || speechBusy} onChange={setLanguage} options={[{ value: 'cn', label: '中文' }, { value: 'en', label: '英文' }]} /></Space><p className="sv-note">将语音转为文字，发送给当前接收者。</p>{partial && <pre className="sv-partial">正在转写：{partial}</pre>}</div>
    </Card><Card title="成员与通话设置"><List size="small" dataSource={people} locale={{ emptyText: '等待其他成员加入' }} renderItem={person => <List.Item><span>{person.name}</span><Space><Tag>{person.audio ? '麦克风开' : '已静音'}</Tag><Tag>{person.video ? '摄像头开' : '封面视频'}</Tag></Space></List.Item>} />
      <div className="sv-setting"><label>视频码率上限（每位成员）</label><Select aria-label="视频码率上限" value={bitrate} style={{ width: '100%' }} options={[{ value: 1000000, label: '1 Mbps' }, { value: 2500000, label: '2.5 Mbps · 默认' }, { value: 4000000, label: '4 Mbps' }]} onChange={value => { setBitrate(value); bandwidth.current = value; session.current?.peers.forEach(peer => void configure(peer.pc)); }} /></div>
      <div className="sv-setting"><label>嵌入强度（创建房间后固定）</label><Select aria-label="QIM 强度" value={qim} disabled={connected || busy} style={{ width: '100%' }} onChange={setQim} options={[{ value: 16, label: '16 · 较轻画面变化' }, { value: 24, label: '24 · 平衡' }, { value: 32, label: '32 · 较大画面变化' }]} /></div>
      <div className="sv-setting"><label>模拟丢帧：{drop}%</label><Slider ariaLabelForHandle="模拟丢帧比例" min={0} max={30} value={drop} onChange={value => { setDrop(value); if (local) local.drop = value; }} /></div>
      <Button onClick={() => { const result = selfTest(); setTest(result.ok ? '自检通过' : '失败'); }}>运行自检</Button><p className="sv-note">{test}</p>
    </Card></div></div>
  </div>;
}
