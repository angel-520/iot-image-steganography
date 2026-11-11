"""Foreground CLI for explicit two-machine UDP experiments (IPv4)."""
from __future__ import annotations
import argparse
from hashlib import sha256
import json
import os
import secrets
import sys


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description='ISTG 双机 UDP 收发；双方需预先配置相同共享口令')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('keygen', help='生成共享口令，双方通过可信方式预先交换')
    receive = sub.add_parser('receive', help='启动前台接收端')
    receive.add_argument('--bind', default='0.0.0.0')
    receive.add_argument('--port', type=int, default=42100)
    receive.add_argument('--allow-peer', required=True, help='允许的发送方 IPv4；跨公网填写发送方出口公网 IP')
    receive.add_argument('--output', default='received-messages')
    receive.add_argument('--timeout', type=int, default=300)
    receive.add_argument('--once', action='store_true')
    send = sub.add_parser('send', help='向明确指定的接收端发送一条加密消息')
    send.add_argument('--peer', required=True)
    send.add_argument('--port', type=int, default=42100)
    send.add_argument('--text', required=True)
    send.add_argument('--channel', choices=['length', 'timing'], default='length')
    args = parser.parse_args()
    if args.command == 'keygen':
        print(secrets.token_urlsafe(32)); return 0
    from istg import network_peer as remote
    from istg import network_stego as net
    key = os.environ.get('ISTG_PEER_KEY', '')
    try:
        remote.auth_key(key)
        if args.command == 'receive':
            listener = remote.Receiver(args.bind, args.port, args.allow_peer, key, args.output)
            print(f'ISTG UDP 接收端：{args.bind}:{args.port}，允许来源 {args.allow_peer}，监听 {args.timeout} 秒', flush=True)
            print('等待认证消息；Ctrl+C 停止。', flush=True)
            try:
                messages = listener.serve(timeout=args.timeout, once=args.once,
                    on_message=lambda value: print(json.dumps(value, ensure_ascii=False), flush=True))
                return 0 if messages else 2
            finally:
                listener.close()
        packet = remote.encrypted_text(args.text, key)
        params = {'short_ms': 8., 'long_ms': 24., 'short_length': 64, 'long_length': 192}
        rows = net.encode(packet, args.channel, **params)
        result = remote.transmit(remote.Peer('cli-peer', args.peer, args.port, key), rows, args.channel,
            sha256(packet).hexdigest(), sha256(args.text.encode('utf-8')).hexdigest(), params)
        print(json.dumps(result, ensure_ascii=False)); return 0
    except (ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr); return 1
    except KeyboardInterrupt:
        print('接收/发送已停止。'); return 0


if __name__ == '__main__':
    raise SystemExit(main())
