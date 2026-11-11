"""Per-application browser-managed peer settings and UDP receive lifecycle."""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from threading import Event, RLock, Thread
from time import monotonic, time
from uuid import uuid4

from istg.network_peer import Peer, Receiver, configured_peers
from istg.network_transfer import UDPReceiver, TCPReceiver
from .runtime import APIError


class NetworkSessions:
    def __init__(self, store):
        self.store = store
        self.lock = RLock()
        self.peers: dict[str, Peer] = {}
        self.messages = []
        self.receiver = None
        self.thread = None
        self.stop_event = Event()
        self.phase = 'stopped'
        self.error = None
        self.settings = None
        self.started = 0.

    def all_peers(self):
        with self.lock:
            current = dict(self.peers)
        try:
            for item in configured_peers():
                current.setdefault(item.name, item)
        except ValueError:
            # Browser-managed settings remain usable if an optional legacy file is invalid.
            if not current:
                raise
        return list(current.values())

    def save_peer(self, host, port, secret):
        identifier = 'web-' + sha256(f'{host}:{port}'.encode()).hexdigest()[:12]
        item = Peer(identifier, host, port, secret)
        with self.lock:
            if identifier not in self.peers and len(self.peers) >= 8:
                raise APIError('peer_limit', '本次运行最多保存 8 个网页对端', 422)
            self.peers[identifier] = item
        return item.public()

    def _prune(self):
        alive = []
        for message in self.messages:
            try:
                self.store.get(message['file']['id']); self.store.get(message['pcap']['id'])
                alive.append(message)
            except APIError:
                for name in ('file', 'pcap'):
                    self.store.remove(message[name]['id'])
        self.messages = alive

    def _received(self, message):
        """Publish both files before acknowledging delivery to the sender."""
        artifacts = []
        try:
            data = Path(message['file']).read_bytes()
            kind = message.get('kind', 'text')
            filename = message.get('filename', Path(message['file']).name)
            artifacts.append(self.store.add(data, filename, 'text/plain; charset=utf-8' if kind == 'text' else 'application/octet-stream'))
            artifacts.append(self.store.add(Path(message['pcap']).read_bytes(), Path(message['pcap']).name, 'application/vnd.tcpdump.pcap'))
            record = {'id': message['session'], 'peer': message['peer'], 'text': message['text'],
                'sha256': sha256(data).hexdigest(), 'received_at': time(),
                'file': artifacts[0].public(), 'pcap': artifacts[1].public()}
            record.update(kind=kind, filename=filename, size_bytes=len(data),
                transport=message.get('transport', 'udp'), cipher=message.get('cipher', 'AES-256-GCM'),
                source=message.get('source'), destination=message.get('destination'),
                trace_truncated=message.get('trace_truncated', False))
            with self.lock:
                self._prune()
                self.error = None
                self.messages.insert(0, record)
                while len(self.messages) > 10:
                    old = self.messages.pop()
                    self.store.remove(old['file']['id']); self.store.remove(old['pcap']['id'])
            Path(message['file']).unlink(missing_ok=True)
            Path(message['pcap']).unlink(missing_ok=True)
        except (APIError, OSError) as exc:
            for item in artifacts:
                self.store.remove(item.id)
            with self.lock:
                self.error = '一条消息未能完整保存，请清理临时结果后让对方重试'
            raise ValueError('接收结果未能完整保存，请清理临时文件后重试') from exc

    def start(self, bind_host, port, allowed_peer, secret, timeout, transport='udp', allowed_ciphers=None):
        with self.lock:
            if self.thread and self.thread.is_alive():
                raise APIError('receiver_running', '接收端正在监听，请先停止后再修改设置', 409)
            directory = self.store.root / ('peer_receive_' + uuid4().hex)
            try:
                cls = TCPReceiver if transport == 'tcp' else UDPReceiver
                listener = cls(bind_host, port, allowed_peer, secret, directory,
                    message_sink=self._received, allowed_ciphers=allowed_ciphers)
            except OSError as exc:
                raise APIError('receiver_bind_failed', '无法监听此地址或端口，可能已被其他程序占用', 422) from exc
            self.receiver = listener
            self.stop_event = Event()
            self.phase, self.error, self.started = 'listening', None, monotonic()
            self.settings = {'bind_host': bind_host, 'port': port, 'allowed_peer': allowed_peer, 'timeout': timeout,
                             'transport': transport, 'allowed_ciphers': list(allowed_ciphers or ('AES-256-GCM', 'ChaCha20-Poly1305'))}
            event = self.stop_event
            def work():
                try:
                    listener.serve(stop=event, timeout=timeout)
                    with self.lock:
                        self.phase = 'stopped' if event.is_set() else 'expired'
                except Exception:
                    with self.lock:
                        self.phase, self.error = 'failed', '接收端已停止，请检查网络设置后重新启动'
                finally:
                    listener.close()
                    listener.secret = ''; listener.key = b''
                    listener.current = listener.completed = None
                    with self.lock:
                        if self.receiver is listener:
                            self.receiver = None
            self.thread = Thread(target=work, name='istg-web-receiver', daemon=True)
            self.thread.start()
        return self.status()

    def status(self):
        with self.lock:
            self._prune()
            remaining = max(0., self.settings['timeout'] - (monotonic() - self.started)) if self.settings and self.phase == 'listening' else 0
            current = self.receiver.current if self.receiver else None
            count, expected = self.receiver.progress() if self.receiver and hasattr(self.receiver, 'progress') else (
                (len(current['rows']), current['manifest']['count']) if current else (0, None))
            return {'status': self.phase, 'settings': self.settings, 'remaining_seconds': remaining,
                    'error': self.error, 'messages': list(self.messages),
                    'session_packets': count, 'session_expected': expected}

    def stop(self):
        with self.lock:
            thread = self.thread
            self.stop_event.set()
        if thread and thread.is_alive():
            thread.join(timeout=3)
            if thread.is_alive():
                raise APIError('receiver_stopping', '接收端正在完成当前保存，请稍后重试停止', 409)
        with self.lock:
            if self.phase != 'failed':
                self.phase = 'stopped'
        return self.status()

    def close(self):
        self.stop()
        with self.lock:
            self.peers.clear()
