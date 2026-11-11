"""Bounded covert-channel teaching primitives; active I/O is loopback UDP only.

No raw sockets, interface capture, DNS lookup, configurable peer or packet replay.
PCAP files are IP-layer reconstructions, not promiscuous captures.
"""
from __future__ import annotations

from hashlib import sha256
from io import BytesIO
import math
import secrets
import socket
import struct
import threading
import time

import numpy as np

FRAME = struct.Struct("!4sI32s")
MARKER = struct.Struct("!4s8sI")
MAX_PACKET_BYTES = 512
MAX_PACKETS = 4097
MAX_CAPTURE_PACKETS = 30000
MAX_ACTIVE_SECONDS = 45
CHANNELS = ("timing", "length", "ip_id", "tcp_seq", "udp_sport")
WIDTHS = {"timing": 1, "length": 1, "ip_id": 16, "tcp_seq": 32, "udp_sport": 16}


def frame(data: bytes) -> bytes:
    if not isinstance(data, bytes) or not 1 <= len(data) <= MAX_PACKET_BYTES - FRAME.size:
        raise ValueError("网络实验包需为 1–472 字节，含帧头后最多 512 字节")
    return FRAME.pack(b"NCL1", len(data), sha256(data).digest()) + data


def unframe(data: bytes) -> bytes:
    if len(data) < FRAME.size:
        raise ValueError("数据不足，无法恢复隐写帧头")
    magic, size, digest = FRAME.unpack_from(data)
    if magic != b"NCL1" or not 1 <= size <= MAX_PACKET_BYTES - FRAME.size or FRAME.size + size > len(data):
        raise ValueError("隐写帧头损坏、参数不匹配或数据包缺失")
    payload = data[FRAME.size:FRAME.size + size]
    if sha256(payload).digest() != digest:
        raise ValueError("SHA-256 校验失败，未输出损坏的秘密内容")
    return payload


def validate_params(channel, short_ms, long_ms, short_length, long_length):
    if channel not in CHANNELS:
        raise ValueError("未知的网络隐写信道")
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in (short_ms, long_ms)):
        raise ValueError("时间间隔必须为有限数值")
    if not 1 <= short_ms <= 30 or not short_ms + 4 <= long_ms <= 100:
        raise ValueError("短间隔需为 1–30 ms，长间隔至少大 4 ms 且不超过 100 ms")
    if (type(short_length) is not int or type(long_length) is not int or
            not MARKER.size <= short_length < long_length <= 1400):
        raise ValueError("UDP 载荷长度需为 16–1400 字节，长包必须大于短包")


def make_record(t, payload, *, sport=42000, dport=42001, ip_id=0, tcp_seq=None, sequence=None, session=None,
                src='127.0.0.1', dst='127.0.0.1'):
    from scapy.layers.inet import IP, UDP, TCP
    from scapy.packet import Raw
    transport = UDP(sport=sport, dport=dport) if tcp_seq is None else TCP(sport=sport, dport=dport, seq=tcp_seq, flags="PA")
    raw = bytes(IP(src=src, dst=dst, id=ip_id) / transport / Raw(payload))
    return {"t": float(t), "length": len(raw), "payload_length": len(payload),
            "src": src, "dst": dst, "sport": sport, "dport": dport,
            "protocol": "UDP" if tcp_seq is None else "TCP", "ip_id": ip_id, "tcp_seq": tcp_seq,
            "sequence": sequence, "session": session, "_raw": raw}


def encode(data: bytes, channel: str, *, short_ms=8., long_ms=24., short_length=64, long_length=192):
    validate_params(channel, short_ms, long_ms, short_length, long_length)
    framed = frame(data)
    width = WIDTHS[channel]
    bits = np.unpackbits(np.frombuffer(framed, dtype=np.uint8))
    if bits.size % width:
        bits = np.pad(bits, (0, -bits.size % width))
    symbols = bits.reshape(-1, width) @ (1 << np.arange(width - 1, -1, -1, dtype=np.int64))
    session = secrets.token_bytes(8)
    rows, timestamp = [], 0.
    for index, value in enumerate([0, *symbols.tolist()]):
        if index:
            timestamp += ((long_ms if value else short_ms) if channel == "timing" else 1.) / 1000
        size = (long_length if value and channel == "length" else short_length)
        payload = MARKER.pack(b"NLB1", session, index).ljust(size, b"\x00")
        rows.append(make_record(timestamp, payload, sequence=index, session=session.hex(),
                    sport=value if channel == "udp_sport" and index else 42000,
                    ip_id=value if channel == "ip_id" and index else index % 65536,
                    tcp_seq=value if channel == "tcp_seq" and index else (0 if channel == "tcp_seq" else None)))
    if len(rows) > MAX_PACKETS:
        raise ValueError("实验数据包超过 4097 个上限")
    return rows


def decode_symbols(rows, channel, *, short_ms=8., long_ms=24., short_length=64, long_length=192, repair_order=True):
    validate_params(channel, short_ms, long_ms, short_length, long_length)
    labs = [r for r in rows if r.get("session") is not None and r.get("sequence") is not None]
    if not labs or len({r["session"] for r in labs}) != 1:
        raise ValueError("需选择包含单次 ISTG 实验标记的 PCAP；普通流量可分析但无法解码")
    if len({r["sequence"] for r in labs}) != len(labs):
        raise ValueError("实验序号重复，无法确定唯一符号序列")
    if max(r["sequence"] for r in labs) >= MAX_PACKETS:
        raise ValueError("实验序号超出上限")
    ordered = sorted(labs, key=lambda r: r["sequence"]) if repair_order else sorted(labs, key=lambda r: r["t"])
    width = WIDTHS[channel]
    values, previous = [], None
    if repair_order:
        values = [None] * max(r["sequence"] for r in labs)
    for row in ordered:
        if row["sequence"] == 0:
            previous = row
            continue
        value = None
        if channel == "timing":
            if previous is not None and row["sequence"] == previous["sequence"] + 1:
                gap = (row["t"] - previous["t"]) * 1000
                if gap >= 0:
                    value = int(gap >= (short_ms + long_ms) / 2)
        elif channel == "length" and row["protocol"] == "UDP":
            if row["payload_length"] in (short_length, long_length):
                value = int(row["payload_length"] == long_length)
        elif channel == "ip_id":
            value = row["ip_id"]
        elif channel == "tcp_seq" and row["protocol"] == "TCP":
            value = row["tcp_seq"]
        elif channel == "udp_sport" and row["protocol"] == "UDP":
            value = row["sport"]
        if repair_order:
            values[row["sequence"] - 1] = value
        else:
            values.append(value)
        previous = row
    bits = []
    for value in values:
        bits.extend([None] * width if value is None else [(int(value) >> shift) & 1 for shift in range(width - 1, -1, -1)])
    return bits


def decode(rows, channel, **params):
    bits = decode_symbols(rows, channel, **params)
    if any(v is None for v in bits):
        raise ValueError("存在缺失或无法判定的符号，未恢复完整内容")
    packet = np.packbits(np.array(bits, dtype=np.uint8)).tobytes()
    return unframe(packet)


def recovery(reference: bytes, rows, channel, **params):
    expected = np.unpackbits(np.frombuffer(frame(reference), dtype=np.uint8)).tolist()
    try:
        actual = decode_symbols(rows, channel, **params)
        error = None
    except ValueError as exc:
        actual, error = [], str(exc)
    good = sum(i < len(actual) and bit == actual[i] for i, bit in enumerate(expected))
    missing = sum(i >= len(actual) or actual[i] is None for i in range(len(expected)))
    exact = False
    try:
        exact = decode(rows, channel, **params) == reference
    except ValueError as exc:
        error = str(exc)
    return {"exact_match": exact, "bit_recovery_rate": good / len(expected), "ber": 1 - good / len(expected),
            "missing_bits": missing, "expected_bits": len(expected), "error": error if not exact else None}


def transmit_loopback(rows, channel, *, progress=None):
    """Use ordinary UDP sockets with two ephemeral loopback ports and a private session.

    The receiver is ready before sending. Timestamps are observed at recvfrom, not
    copied from the transmission schedule. No external destination is accepted.
    """
    if channel not in ("timing", "length"):
        raise ValueError("协议字段隐写仅支持离线构造，禁止主动发送")
    if not rows or len(rows) > MAX_PACKETS or rows[-1]["t"] > MAX_ACTIVE_SECONDS - 2:
        raise ValueError("本机 UDP 实验预计超过 43 秒，请缩短秘密内容或间隔")
    from scapy.layers.inet import IP, UDP
    # Validate every packet before opening sockets; rows can never redirect traffic.
    for row in rows:
        if row["src"] != "127.0.0.1" or row["dst"] != "127.0.0.1" or row["protocol"] != "UDP":
            raise ValueError("主动实验只允许 127.0.0.1 UDP 回环")
    received, stop, errors = [], threading.Event(), []
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
        receiver.bind(("127.0.0.1", 0)); sender.bind(("127.0.0.1", 0))
        receiver.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024 * 1024)
        receiver.settimeout(.05)
        target, source = receiver.getsockname(), sender.getsockname()
        first = rows[0]["session"]
        started = time.perf_counter()
        def receive():
            while not stop.is_set() and time.perf_counter() - started < MAX_ACTIVE_SECONDS:
                try:
                    data, peer = receiver.recvfrom(2048)
                    stamp = time.perf_counter()
                    if peer != source or len(data) < MARKER.size:
                        continue
                    magic, session, sequence = MARKER.unpack_from(data)
                    if magic != b"NLB1" or session.hex() != first:
                        continue
                    received.append(make_record(stamp - started, data, sport=source[1], dport=target[1],
                        ip_id=sequence % 65536, sequence=sequence, session=first))
                    if len(received) >= len(rows):
                        return
                except socket.timeout:
                    continue
                except Exception as exc:
                    errors.append(exc)
                    return
        thread = threading.Thread(target=receive, name="istg-loopback-receiver", daemon=True)
        thread.start()
        try:
            previous_sent = time.perf_counter()
            for index, row in enumerate(rows):
                # Schedule each gap relative to the actual preceding send; no catch-up bursts.
                gap = row["t"] - rows[index - 1]["t"] if index else 0
                remaining = previous_sent + gap - time.perf_counter()
                if remaining > 0:
                    time.sleep(remaining)
                if time.perf_counter() - started > MAX_ACTIVE_SECONDS - 1:
                    raise ValueError("本机传输超时，已停止发送")
                payload = bytes(IP(row["_raw"])[UDP].payload)
                sender.sendto(payload, target)
                previous_sent = time.perf_counter()
                if progress and index % 64 == 0:
                    progress(20 + int(index / len(rows) * 55), f"回环 UDP：已发送 {index + 1}/{len(rows)} 包")
            thread.join(timeout=1)
        finally:
            stop.set(); thread.join(timeout=.2)
        if errors:
            raise ValueError("本机 UDP 接收失败") from errors[0]
        if received:
            origin = received[0]["t"]
            received = [{**r, "t": r["t"] - origin} for r in received]
        return received, {"host": "127.0.0.1", "sender_port": source[1], "receiver_port": target[1],
                          "sent_packets": len(rows), "received_packets": len(received), "elapsed_seconds": time.perf_counter() - started}


def impair(rows, *, delay_ms=0., jitter_ms=0., loss_rate=0., reorder_rate=0., seed=42):
    if not (0 <= delay_ms <= 500 and 0 <= jitter_ms <= 100 and 0 <= loss_rate <= 1 and 0 <= reorder_rate <= 1):
        raise ValueError("网络扰动参数超出限制")
    rng = np.random.default_rng(seed)
    result = []
    intervals = np.diff([r["t"] for r in rows])
    reorder_delay = max(.002, float(np.median(intervals)) * 3) if len(intervals) else .01
    for row in rows:
        if rng.random() < loss_rate:
            continue
        # Independent, non-negative per-packet delay; base delay alone preserves gaps.
        delay = delay_ms / 1000 + max(0., rng.normal(0., jitter_ms / 1000))
        if rng.random() < reorder_rate:
            delay += rng.uniform(1., 4.) * reorder_delay
        result.append({**row, "t": row["t"] + delay})
    result.sort(key=lambda r: r["t"])
    return result


def pcap_bytes(rows):
    from scapy.utils import RawPcapWriter
    buffer = BytesIO()
    writer = RawPcapWriter(buffer, linktype=101, sync=True)
    try:
        writer.write_header(None)
        for row in rows:
            stamp = 1_700_000_000_000_000 + round(row["t"] * 1_000_000)
            writer.write_packet(row["_raw"], sec=stamp // 1_000_000, usec=stamp % 1_000_000)
        return buffer.getvalue()
    finally:
        writer.close()


def parse_pcap(data: bytes):
    from scapy.layers.inet import IP, UDP, TCP
    from scapy.utils import PcapReader
    from scapy.error import Scapy_Exception
    if len(data) > 16 * 1024 * 1024 or data[:4] not in (b"\xd4\xc3\xb2\xa1", b"\xa1\xb2\xc3\xd4", b"\x4d\x3c\xb2\xa1", b"\xa1\xb2\x3c\x4d", b"\x0a\x0d\x0d\x0a"):
        raise ValueError("仅支持最大 16 MiB 的 PCAP / PCAPNG 文件")
    if len(data) < (28 if data[:4] == b"\x0a\x0d\x0d\x0a" else 24):
        raise ValueError("PCAP / PCAPNG 文件头被截断")
    rows, ignored, count = [], 0, 0
    try:
        with PcapReader(BytesIO(data)) as reader:
            for packet in reader:
                count += 1
                if count > MAX_CAPTURE_PACKETS:
                    raise ValueError("PCAP 超过 30000 包，请先截取实验流量")
                if IP not in packet:
                    ignored += 1; continue
                ip = packet[IP]
                is_udp = ip.proto == 17
                if (ip.proto not in (6, 17) or not isinstance(ip.payload, UDP if is_udp else TCP)
                        or ip.frag or ip.flags.MF):
                    ignored += 1; continue
                transport = ip.payload
                payload = bytes(transport.payload)
                raw = bytes(ip)
                header_bytes = int(ip.ihl) * 4
                transport_header = 8 if is_udp else int(transport.dataofs) * 4
                if (not 20 <= header_bytes <= 60 or int(ip.len) > len(raw) or
                        (not is_udp and not 20 <= transport_header <= 60) or
                        int(ip.len) < header_bytes + transport_header):
                    raise ValueError("PCAP 包截断或 IP 长度无效")
                raw = raw[:int(ip.len)]  # Ethernet padding is not part of the IP datagram.
                payload = raw[header_bytes + transport_header:]
                if (
                        len(raw) < header_bytes + transport_header or
                        (is_udp and int(transport.len) != len(raw) - header_bytes)):
                    raise ValueError("PCAP 包截断或 IP/UDP 长度无效")
                sequence = session = None
                if len(payload) >= MARKER.size:
                    magic, token, number = MARKER.unpack_from(payload)
                    if magic == b"NLB1":
                        session, sequence = token.hex(), number
                timestamp = float(packet.time)
                if not math.isfinite(timestamp):
                    raise ValueError("PCAP 时间戳无效")
                rows.append({"t": timestamp, "length": len(raw), "payload_length": len(payload),
                    "src": ip.src, "dst": ip.dst, "sport": int(transport.sport), "dport": int(transport.dport),
                    "protocol": "UDP" if is_udp else "TCP", "ip_id": int(ip.id),
                    "tcp_seq": int(transport.seq) if not is_udp else None,
                    "sequence": sequence, "session": session, "_raw": raw})
    except (ValueError, TypeError, KeyError, OSError, EOFError, struct.error, Scapy_Exception, AttributeError, IndexError) as exc:
        raise ValueError(str(exc) if isinstance(exc, ValueError) else "PCAP 数据损坏或格式不支持") from exc
    rows.sort(key=lambda r: r["t"])
    if rows:
        start = rows[0]["t"]
        rows = [{**r, "t": r["t"] - start} for r in rows]
    return rows, {"total_packets": count, "ignored_packets": ignored, "supported_packets": len(rows)}


def public_rows(rows):
    return [{**{k: v for k, v in row.items() if not k.startswith("_")}, "index": i,
             "gap_ms": (row["t"] - rows[i - 1]["t"]) * 1000 if i else None} for i, row in enumerate(rows)]
