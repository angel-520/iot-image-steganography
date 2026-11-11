"""Authenticated two-machine UDP carrier transport with explicit peers.

Control/data envelopes have HMAC authentication and a receiver challenge. Only
length/timing carriers are accepted. No discovery, forwarding or arbitrary replay.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import hmac
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import socket
import struct
import time

from . import network_stego as net
from .istg_pack import pack_istg
from .istg_crypto import encrypt_istg, decrypt_istg
from .security_analysis.packet import parse_istg

WIRE = struct.Struct('!4sB8sI')
TAG_SIZE = 32
HELLO, READY, DATA, FINISH, STATUS, MISSING = range(1, 7)
MAX_SECONDS = 60
MAX_BODY = 1400
CIPHERS = ('AES-256-GCM', 'ChaCha20-Poly1305', 'none')


def local_endpoint(sock, peer):
    address, port = sock.getsockname()[:2]
    if address == '0.0.0.0':
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(peer)
            address = probe.getsockname()[0]
    return (address, port)


def ipv4(value):
    if not isinstance(value, str):
        raise ValueError('对端 IPv4 地址必须为字符串')
    try:
        address = ipaddress.IPv4Address(value)
    except (ipaddress.AddressValueError, ValueError) as exc:
        raise ValueError('对端必须为明确的 IPv4 地址，不接受域名') from exc
    if address.is_multicast or address.is_unspecified or address.is_link_local or int(address) == 0xffffffff:
        raise ValueError('不能使用组播、广播、未指定或链路本地地址作为对端')
    return str(address)


def port_number(value):
    if type(value) is not int or not 1024 <= value <= 65535:
        raise ValueError('UDP 对端端口需为 1024–65535 的整数')
    return value


def auth_key(secret):
    if not isinstance(secret, str) or not 32 <= len(secret) <= 256:
        raise ValueError('共享口令需为 32–256 个字符，请使用 network_peer.py keygen 生成')
    return sha256(b'ISTG-peer-auth-v1\x00' + secret.encode('utf-8')).digest()


def session_key(key, session, challenge):
    return hmac.digest(key, b'ISTG-session-v1' + session + challenge, 'sha256')


def encode_wire(kind, session, sequence, body, key):
    if len(body) > MAX_BODY:
        raise ValueError('远程数据包超过 1400 字节载荷限制')
    content = WIRE.pack(b'NPR1', kind, session, sequence) + body
    return content + hmac.digest(key, content, 'sha256')


def decode_wire(data, key):
    if not WIRE.size + TAG_SIZE <= len(data) <= WIRE.size + MAX_BODY + TAG_SIZE:
        return None
    content, signature = data[:-TAG_SIZE], data[-TAG_SIZE:]
    if not hmac.compare_digest(signature, hmac.digest(key, content, 'sha256')):
        return None
    magic, kind, session, sequence = WIRE.unpack_from(content)
    if magic != b'NPR1' or kind not in range(1, 7):
        return None
    return kind, session, sequence, content[WIRE.size:]


def json_bytes(value):
    return json.dumps(value, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode()


def read_json(data):
    try:
        value = json.loads(data)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ValueError('远程控制包格式无效') from exc
    if not isinstance(value, dict):
        raise ValueError('远程控制包必须为对象')
    return value


@dataclass(frozen=True)
class Peer:
    name: str
    host: str
    port: int
    secret: str = field(repr=False)

    def __post_init__(self):
        ipv4(self.host); port_number(self.port); auth_key(self.secret)

    def public(self):
        return {'name': self.name, 'host': self.host, 'port': self.port}


def configured_peers():
    path = Path(os.environ.get('ISTG_NETWORK_PEERS', Path(__file__).resolve().parents[1] / 'network-peers.json'))
    if not path.is_file():
        return []
    if path.stat().st_size > 16384:
        raise ValueError('远程对端配置超过 16 KiB')
    try:
        config = json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError, RecursionError) as exc:
        raise ValueError('无法读取远程对端配置文件') from exc
    if not isinstance(config, dict) or set(config) != {'peers'} or not isinstance(config['peers'], list) or len(config['peers']) > 8:
        raise ValueError('配置需包含 peers 列表，最多 8 个对端')
    peers = []
    for item in config['peers']:
        if not isinstance(item, dict) or set(item) != {'name', 'host', 'port', 'key_env'}:
            raise ValueError('每个对端需设置 name、host、port、key_env')
        if not isinstance(item['name'], str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,40}', item['name']):
            raise ValueError('对端名称只能包含 1–40 位字母、数字、下划线和横线')
        if not isinstance(item['key_env'], str) or not re.fullmatch(r'[A-Z][A-Z0-9_]{0,63}', item['key_env']):
            raise ValueError('key_env 必须为有效环境变量名称')
        if any(p.name == item['name'] for p in peers):
            raise ValueError('对端名称重复')
        peers.append(Peer(item['name'], item['host'], item['port'], os.environ.get(item['key_env'], '')))
    return peers


def encrypted_text(text, secret):
    auth_key(secret)
    if not isinstance(text, str) or not 1 <= len(text.encode('utf-8')) <= 128:
        raise ValueError('双机秘密文本需为 1–128 UTF-8 字节')
    packet = encrypt_istg(pack_istg('text', text), secret, 'AES-256-GCM')
    net.frame(packet)
    return packet


def decrypt_text(packet, secret):
    parsed = parse_istg(packet)
    if not parsed['encrypted'] or parsed['metadata'].get('cipher') not in CIPHERS[:2]:
        raise ValueError('隐写文本需使用支持的认证加密算法')
    inner = parse_istg(decrypt_istg(packet, secret))['payload_data']
    if inner['kind'] != 'text' or not 1 <= len(inner['data']) <= 128:
        raise ValueError('远程内容不是允许大小的文本')
    return inner['data']


class Receiver:
    """Foreground receiver; only the configured source IP may initiate one session."""
    def __init__(self, bind_host, port, allowed_peer, secret, output_dir, message_sink=None, allowed_ciphers=None):
        if bind_host != '0.0.0.0':
            ipv4(bind_host)
        self.allowed_peer = ipv4(allowed_peer)
        port_number(port)
        self.key, self.secret = auth_key(secret), secret
        self.output = Path(output_dir).resolve()
        self.output.mkdir(parents=True, exist_ok=True)
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024 * 1024)
            self.socket.bind((bind_host, port))
            self.socket.settimeout(.1)
        except Exception:
            self.socket.close(); raise
        self.current = None
        self.completed = None
        self.messages = []
        self.message_sink = message_sink
        self.allowed_ciphers = tuple(allowed_ciphers or CIPHERS[:2])

    def close(self):
        self.socket.close()

    def _reply(self, address, kind, session, body, key):
        self.socket.sendto(encode_wire(kind, session, 0, body, key), address)

    @staticmethod
    def manifest(body):
        value = read_json(body)
        if set(value) != {'channel', 'count', 'digest', 'params'} or value['channel'] not in ('length', 'timing'):
            raise ValueError('远程会话参数无效')
        if type(value['count']) is not int or not 2 <= value['count'] <= net.MAX_PACKETS:
            raise ValueError('远程包数超限')
        if not isinstance(value['digest'], str) or not re.fullmatch('[0-9a-f]{64}', value['digest']):
            raise ValueError('远程包摘要无效')
        params = value['params']
        if not isinstance(params, dict) or set(params) != {'short_ms', 'long_ms', 'short_length', 'long_length'}:
            raise ValueError('远程信道参数无效')
        net.validate_params(value['channel'], **params)
        return value

    def process(self, data, address):
        now = time.monotonic()
        if address[0] != self.allowed_peer:
            return
        if self.current and now - self.current['started'] > MAX_SECONDS:
            self.current = None
        if self.completed and now - self.completed['finished'] > 5:
            self.completed = None
        hello = decode_wire(data, self.key)
        if hello and hello[0] == HELLO:
            _, session, _, body = hello
            if self.completed and session == self.completed['session']:
                return
            if self.current:
                if self.current['session'] == session and self.current['address'] == address and self.current['manifest_body'] == body:
                    self._reply(address, READY, session, self.current['challenge'], self.key)
                return
            try:
                manifest = self.manifest(body)
            except (ValueError, TypeError):
                return
            challenge = secrets.token_bytes(16)
            self.current = {'session': session, 'address': address, 'challenge': challenge,
                'key': session_key(self.key, session, challenge), 'manifest': manifest, 'manifest_body': body,
                'rows': {}, 'started': now, 'origin': None, 'local': local_endpoint(self.socket, address)}
            self._reply(address, READY, session, challenge, self.key)
            return
        if self.completed and address == self.completed['address']:
            message = decode_wire(data, self.completed['key'])
            if message and message[0] == FINISH and message[1] == self.completed['session']:
                self._reply(address, STATUS, message[1], self.completed['receipt'], self.completed['key'])
                return
        current = self.current
        if not current or address != current['address']:
            return
        decoded = decode_wire(data, current['key'])
        if not decoded or decoded[1] != current['session']:
            return
        kind, session, sequence, body = decoded
        manifest = current['manifest']
        if kind == DATA:
            if sequence >= manifest['count'] or sequence in current['rows']:
                return
            params = manifest['params']
            allowed_lengths = (params['short_length'], params['long_length']) if manifest['channel'] == 'length' else (params['short_length'],)
            if len(body) not in allowed_lengths or len(body) < net.MARKER.size:
                return
            magic, marker_session, number = net.MARKER.unpack_from(body)
            if magic != b'NLB1' or marker_session != session or number != sequence:
                return
            # Timestamp is measured before Scapy serialization, authentication work is bounded.
            stamp = time.perf_counter()
            if current['origin'] is None:
                current['origin'] = stamp
            current['rows'][sequence] = net.make_record(stamp - current['origin'], body,
                sport=address[1], dport=self.socket.getsockname()[1], ip_id=sequence % 65536,
                sequence=sequence, session=session.hex(), src=address[0], dst=current['local'][0])
        elif kind == FINISH:
            missing = [i for i in range(manifest['count']) if i not in current['rows']]
            if missing:
                self._reply(address, MISSING, session, json_bytes({'missing': missing[:128]}), current['key'])
                return
            rows = sorted(current['rows'].values(), key=lambda r: r['t'])
            receipt = {'status': 'failed', 'error': 'integrity_or_timing_failed'}
            text_path = pcap_path = None
            try:
                packet = net.decode(rows, manifest['channel'], repair_order=True, **manifest['params'])
                if sha256(packet).hexdigest() != manifest['digest']:
                    raise ValueError('摘要不匹配')
                if parse_istg(packet)['metadata'].get('cipher') not in self.allowed_ciphers:
                    raise ValueError('接收端未允许此加密算法')
                plaintext = decrypt_text(packet, self.secret)
                stem = 'message_' + secrets.token_hex(8)
                text_path, pcap_path = self.output / (stem + '.txt'), self.output / (stem + '.pcap')
                # Fresh unpredictable filenames; never take a filename from the sender.
                with pcap_path.open('xb') as target:
                    target.write(net.pcap_bytes(rows))
                with text_path.open('xb') as target:
                    target.write(plaintext)
                receipt = {'status': 'ok', 'packet_sha256': manifest['digest'],
                    'plaintext_sha256': sha256(plaintext).hexdigest(), 'bytes': len(plaintext),
                    'received_packets': len(rows), 'file': text_path.name, 'pcap': pcap_path.name,
                    'source': list(address), 'destination': list(current['local']), 'transport': 'udp'}
                message = {'file': str(text_path), 'pcap': str(pcap_path), 'peer': address[0],
                    'text': plaintext.decode('utf-8'), 'session': session.hex(), 'kind': 'text',
                    'source': list(address), 'destination': list(current['local']), 'transport': 'udp',
                    'cipher': parse_istg(packet)['metadata'].get('cipher')}
                if self.message_sink:
                    self.message_sink(message)
                self.messages.append(message)
                self.messages = self.messages[-10:]
            except (ValueError, OSError):
                receipt = {'status': 'failed', 'error': 'integrity_or_storage_failed'}
                for path in (text_path, pcap_path):
                    if path is not None:
                        path.unlink(missing_ok=True)
            encoded = json_bytes(receipt)
            self.completed = {**current, 'receipt': encoded, 'finished': now}
            self.current = None
            self._reply(address, STATUS, session, encoded, current['key'])

    def serve(self, stop=None, timeout=300, once=False, on_message=None):
        if not 1 <= timeout <= 3600:
            raise ValueError('接收端单次监听时长需为 1–3600 秒')
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not (stop and stop.is_set()):
            if once and self.completed and time.monotonic() - self.completed['finished'] >= 2:
                break  # Linger briefly to answer a retried FINISH if a receipt was lost.
            try:
                data, address = self.socket.recvfrom(WIRE.size + MAX_BODY + TAG_SIZE + 1)
            except socket.timeout:
                continue
            before = self.messages[-1]['session'] if self.messages else None
            try:
                self.process(data, address)
            except (ValueError, OSError, struct.error):
                continue
            if on_message and self.messages and self.messages[-1]['session'] != before:
                on_message(self.messages[-1])
        return list(self.messages)


def transmit(peer, rows, channel, packet_digest, expected_plaintext_digest, params, progress=None,
             packets_per_second=500, trace_sink=None):
    if not isinstance(peer, Peer):
        raise ValueError('需使用已明确配置的对端')
    if channel not in ('length', 'timing'):
        raise ValueError('远程仅支持长度和时间信道')
    net.validate_params(channel, **params)
    if not rows or len(rows) > net.MAX_PACKETS or rows[-1]['t'] > 43:
        raise ValueError('远程传输超出包数或 43 秒计划时长限制')
    from scapy.layers.inet import IP, UDP
    if not 1 <= packets_per_second <= 2000:
        raise ValueError('包长信道速率需为 1–2000 包/秒')
    if channel == 'length' and len(rows) / packets_per_second > 43:
        raise ValueError('设定速率下预计超过 43 秒，请提高包速率或缩短文本')
    payloads = [bytes(IP(row['_raw'])[UDP].payload) for row in rows]
    session = bytes.fromhex(rows[0]['session'])
    master = auth_key(peer.secret)
    target = (peer.host, peer.port)
    manifest = json_bytes({'channel': channel, 'count': len(rows), 'digest': packet_digest, 'params': params})
    started = time.monotonic()
    sent, retries = 0, 0
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(('0.0.0.0', 0)); sock.settimeout(.1)
        source = local_endpoint(sock, target)
        logical_rows = []
        logical_seen = set()
        wire_rows = []
        wire_origin = time.perf_counter()
        def send_wire(kind, sequence, body, key):
            wire = encode_wire(kind, session, sequence, body, key)
            sock.sendto(wire, target)
            wire_rows.append(net.make_record(time.perf_counter()-wire_origin, wire,
                src=source[0],dst=target[0],sport=source[1],dport=target[1]))
        def wait_response(key, kinds, timeout):
            deadline = min(started + MAX_SECONDS, time.monotonic() + timeout)
            while time.monotonic() < deadline:
                try:
                    wire, source = sock.recvfrom(2048)
                except socket.timeout:
                    continue
                except (ConnectionResetError, ConnectionRefusedError):
                    return None
                if source != target:
                    continue
                response = decode_wire(wire, key)
                if response and response[1] == session and response[0] in kinds:
                    return response
            return None
        ready = None
        for _ in range(3):
            send_wire(HELLO, 0, manifest, master)
            ready = wait_response(master, (READY,), 1)
            if ready and len(ready[3]) == 16:
                break
        if not ready or len(ready[3]) != 16:
            raise ValueError('对端未完成认证握手：请检查接收端、共享口令、允许的来源 IP、UDP 防火墙和端口映射')
        key = session_key(master, session, ready[3])
        def send_symbol(index, pause):
            nonlocal sent
            if time.monotonic() - started > MAX_SECONDS - 2:
                raise ValueError('远程传输超过时限，未确认交付')
            if pause:
                time.sleep(pause)
            send_wire(DATA, index, payloads[index], key)
            if index not in logical_seen:
                logical_seen.add(index)
                logical_rows.append(net.make_record(time.perf_counter()-wire_origin,payloads[index],src=source[0],dst=target[0],
                    sport=source[1],dport=target[1],sequence=index,session=session.hex()))
            sent += 1
        for index in range(len(rows)):
            gap = rows[index]['t'] - rows[index - 1]['t'] if index else 0
            send_symbol(index, 1 / packets_per_second if channel == 'length' else gap)
            if progress and index % 128 == 0:
                progress(15 + int(index / len(rows) * 65), f'向已配置对端发送 {index + 1}/{len(rows)} 包')
        for attempt in range(5):
            send_wire(FINISH, 0, b'', key)
            reply = wait_response(key, (STATUS, MISSING), 1.5)
            if reply is None:
                continue
            value = read_json(reply[3])
            if reply[0] == STATUS:
                if (value.get('status') != 'ok' or value.get('packet_sha256') != packet_digest
                        or value.get('plaintext_sha256') != expected_plaintext_digest):
                    raise ValueError('接收端未通过完整恢复验证；时间信道可能受公网抖动影响，建议使用包长信道')
                if trace_sink:
                    trace_sink(logical_rows, wire_rows)
                return {'status': 'ok', 'peer': peer.public(), 'channel': channel, 'receipt': value,
                    'source': list(source), 'destination': list(target), 'transport': 'udp',
                    'sent_datagrams': sent, 'retransmitted_datagrams': retries,
                    'elapsed_seconds': time.monotonic() - started,
                    'wire_overhead_bytes': WIRE.size + TAG_SIZE}
            missing = value.get('missing')
            if (channel != 'length' or attempt >= 3 or not isinstance(missing, list) or len(missing) > 128
                    or any(type(i) is not int or not 0 <= i < len(rows) for i in missing)):
                raise ValueError('数据包缺失，未确认交付；包长信道最多进行三轮选择性重传')
            for index in sorted(set(missing)):
                send_symbol(index, .002); retries += 1
    raise ValueError('未收到接收端确认，不能确认是否交付；请检查接收端输出，避免盲目重复发送')
