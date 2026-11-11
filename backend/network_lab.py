"""Network lab: loopback defaults; optional explicit authenticated remote peers."""
from __future__ import annotations

from hashlib import sha256
from typing import Literal
import shutil

import numpy as np
from fastapi import APIRouter, File, Request, UploadFile
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

import istg
from istg import network_stego as net
from istg import network_peer as remote
from istg import network_transfer as transfer
from istg.network_detection import analyze, evaluate_impairment
from .runtime import APIError, MAX_UPLOAD

Channel = Literal["timing", "length", "ip_id", "tcp_seq", "udp_sport"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class ChannelParams(StrictModel):
    channel: Channel = "timing"
    short_ms: float = Field(default=8, ge=1, le=30)
    long_ms: float = Field(default=24, ge=5, le=100)
    short_length: int = Field(default=64, ge=16, le=1399, strict=True)
    long_length: int = Field(default=192, ge=17, le=1400, strict=True)
    repair_order: bool = True

    @model_validator(mode="after")
    def validate_channel(self):
        if self.channel != 'data':
            net.validate_params(self.channel, self.short_ms, self.long_ms, self.short_length, self.long_length)
        return self

    def codec_params(self):
        return {k: getattr(self, k) for k in ("short_ms", "long_ms", "short_length", "long_length")}


class Impairment(StrictModel):
    delay_ms: float = Field(default=0, ge=0, le=500)
    jitter_ms: float = Field(default=0, ge=0, le=100)
    loss_rate: float = Field(default=0, ge=0, le=1)
    reorder_rate: float = Field(default=0, ge=0, le=1)
    seed: int = Field(default=42, ge=0, le=2**32 - 1, strict=True)


class RunRequest(ChannelParams):
    text: str = Field(default="ISTG 网络实验", min_length=1, max_length=128)
    execution: Literal["simulation", "loopback", "offline"] = "simulation"
    host: Literal["127.0.0.1"] = "127.0.0.1"
    encryption: Literal["none", "AES-256-GCM", "ChaCha20-Poly1305"] = "none"
    password: SecretStr | None = Field(default=None, max_length=1024)
    confirm_password: SecretStr | None = Field(default=None, max_length=1024)
    impairment: Impairment = Field(default_factory=Impairment)

    @field_validator("text")
    @classmethod
    def text_limit(cls, value):
        if len(value.encode("utf-8")) > 128:
            raise ValueError("网络实验秘密文本最多 128 UTF-8 字节")
        return value

    @model_validator(mode="after")
    def offline_fields(self):
        if self.execution == "loopback" and self.channel not in ("timing", "length"):
            raise ValueError("协议字段隐写仅允许离线实验")
        return self


class AnalyzeRequest(ChannelParams):
    file_id: str = Field(min_length=32, max_length=32)
    password: SecretStr | None = Field(default=None, max_length=1024)


class RobustnessRequest(AnalyzeRequest):
    impairment: Impairment = Field(default_factory=Impairment)


class RemoteRequest(ChannelParams):
    channel: Literal['length', 'timing', 'data'] = 'length'
    repair_order: Literal[True] = True
    peer_name: str = Field(min_length=1, max_length=40, pattern=r'^[a-zA-Z0-9_-]+$')
    text: str | None = Field(default=None, min_length=1, max_length=65536)
    secret_id: str | None = Field(default=None, min_length=32, max_length=32)
    transport: Literal['tcp', 'udp'] = 'udp'
    cipher: Literal['AES-256-GCM', 'ChaCha20-Poly1305', 'none'] = 'AES-256-GCM'
    rate_mbps: float = Field(default=5, ge=.05, le=100)
    packets_per_second: float = Field(default=500, ge=1, le=2000)
    chunk_size: int = Field(default=1024, ge=512, le=1200, strict=True)
    window_size: int = Field(default=32, ge=1, le=64, strict=True)
    retries: int = Field(default=3, ge=0, le=5, strict=True)

    @model_validator(mode="after")
    def validate_remote(self):
        if (self.text is None) == (self.secret_id is None):
            raise ValueError('文字与文件必须二选一')
        if self.channel != 'data':
            if self.transport != 'udp' or self.secret_id is not None or self.cipher == 'none':
                raise ValueError('包长/时间隐写仅支持 UDP 小文本和认证加密；文件请选择分块传输')
        limit = 65536 if self.channel == 'data' else 128
        if self.text is not None and len(self.text.encode('utf-8')) > limit:
            raise ValueError(f'当前模式的文本最多 {limit} UTF-8 字节')
        return self


class WebPeerRequest(StrictModel):
    host: str = Field(min_length=7, max_length=15)
    port: int = Field(default=42100, ge=1024, le=65535, strict=True)
    shared_key: SecretStr = Field(min_length=32, max_length=256)

    @field_validator('host')
    @classmethod
    def valid_host(cls, value):
        return remote.ipv4(value)


class ReceiverRequest(StrictModel):
    bind_host: str = Field(default='0.0.0.0', max_length=15)
    allowed_peer: str = Field(min_length=7, max_length=15)
    port: int = Field(default=42100, ge=1024, le=65535, strict=True)
    timeout: int = Field(default=300, ge=1, le=3600, strict=True)
    shared_key: SecretStr = Field(min_length=32, max_length=256)
    transport: Literal['tcp', 'udp'] = 'udp'
    allowed_ciphers: list[Literal['AES-256-GCM', 'ChaCha20-Poly1305', 'none']] = Field(
        default_factory=lambda: ['AES-256-GCM', 'ChaCha20-Poly1305'], min_length=1, max_length=3)

    @field_validator('allowed_peer')
    @classmethod
    def valid_peer(cls, value):
        return remote.ipv4(value)

    @field_validator('bind_host')
    @classmethod
    def valid_bind(cls, value):
        return value if value == '0.0.0.0' else remote.ipv4(value)


def packet_for(model, *, probe=False):
    packet = istg.pack_istg("text", model.text)
    if model.encryption != "none":
        password = model.password.get_secret_value() if model.password else ""
        if not probe and (not password or not model.confirm_password or password != model.confirm_password.get_secret_value()):
            raise ValueError("认证加密需要填写一致的非空密码")
        packet = istg.encrypt_istg(packet, "capacity-probe" if probe else password, model.encryption)
    net.frame(packet)
    return packet


class NetworkService:
    def __init__(self, store, sessions=None):
        self.store = store
        self.sessions = sessions

    def pcap(self, rows, name):
        return self.store.add(net.pcap_bytes(rows), name, "application/vnd.tcpdump.pcap").public()

    def send_remote(self, model, progress):
        peers = self.sessions.all_peers() if self.sessions else remote.configured_peers()
        peer = next((p for p in peers if p.name == model.peer_name), None)
        if peer is None:
            raise ValueError('请先在网页中保存接收方 IP、端口和共享口令')
        progress(5, '正在准备发送内容')
        data = model.text
        filename = None
        if model.secret_id is not None:
            item = self.store.get(model.secret_id)
            if item.size_bytes > transfer.MAX_FILE:
                raise ValueError('网络文件不能超过 8 MiB')
            data, filename = item.path.read_bytes(), item.name
        plaintext = data.encode('utf-8') if isinstance(data, str) else data
        packet = transfer.pack_content(data, peer.secret, model.cipher, filename)
        traces = {}
        progress(10, '正在连接接收端')
        if model.channel == 'data':
            result = transfer.transmit(peer, packet, sha256(plaintext).hexdigest(), transport=model.transport,
                cipher=model.cipher, rate_mbps=model.rate_mbps, chunk_size=model.chunk_size,
                window_size=model.window_size, retries=model.retries, progress=progress,
                trace_sink=lambda rows: traces.update(wire=rows))
            count = (len(packet)+model.chunk_size-1)//model.chunk_size
        else:
            net.frame(packet)
            rows = net.encode(packet, model.channel, **model.codec_params())
            result = remote.transmit(peer, rows, model.channel, sha256(packet).hexdigest(),
                sha256(plaintext).hexdigest(), model.codec_params(), progress=progress,
                packets_per_second=model.packets_per_second,
                trace_sink=lambda logical, wire: traces.update(logical=logical,wire=wire))
            count = len(rows)
        progress(95, '接收端已确认保存')
        return {**result, 'execution': 'remote', 'planned_packets': count, 'packet_bytes': len(packet),
            'cipher': model.cipher, 'kind': 'file' if filename else 'text', 'filename': filename,
            'pcap': self.pcap(traces['wire'], 'sender_socket_trace.pcap'),
            'logical_pcap': self.pcap(traces['logical'], 'sender_logical_trace.pcap') if 'logical' in traces else None,
            'trace_note': '导出记录 socket 观测地址、应用数据和高精度时间，IP/TCP 头为重建，非完整网卡抓包；NAT 两侧地址可能不同。'}

    def recover(self, rows, model):
        try:
            packet = net.decode(rows, model.channel, repair_order=model.repair_order, **model.codec_params())
            parsed = istg.parse_istg(packet)
            encrypted = parsed["encrypted"]
            if encrypted:
                if not model.password:
                    return {"status": "password_required", "encrypted": True, "error": "隐写包完整，需输入原密码才能恢复秘密文本"}
                packet = istg.decrypt_istg(packet, model.password.get_secret_value())
                parsed = istg.parse_istg(packet)
            inner = parsed["payload_data"]
            if inner["kind"] != "text":
                raise ValueError("网络教学模块仅恢复文本类型 ISTG 包")
            data = inner["data"]
            artifact = self.store.add(data, "network_recovered.txt", "text/plain; charset=utf-8").public()
            return {"status": "ok", "text": data.decode("utf-8"), "sha256": sha256(data).hexdigest(),
                    "encrypted": encrypted, "authenticated": True if encrypted else None, "file": artifact}
        except ValueError as exc:
            return {"status": "failed", "error": str(exc)}

    def summary(self, rows, model):
        return {"packet_count": len(rows), "duration_seconds": rows[-1]["t"] - rows[0]["t"] if rows else 0,
                "total_ip_bytes": sum(r["length"] for r in rows), "rows": net.public_rows(rows),
                "detection": analyze(rows), "recovered": self.recover(rows, model)}

    def run(self, model, progress):
        progress(5, "正在准备实验")
        packet = packet_for(model)
        planned = net.encode(packet, model.channel, **model.codec_params())
        transport = None
        if model.execution == "loopback":
            progress(15, "正在发送 UDP 数据")
            baseline, transport = net.transmit_loopback(planned, model.channel, progress=progress)
        else:
            baseline = planned
        progress(78, "正在分析流量")
        result = self.summary(baseline, model)
        result["recovery"] = net.recovery(packet, baseline, model.channel, repair_order=model.repair_order, **model.codec_params())
        result["pcap"] = self.pcap(baseline, "network_baseline.pcap")
        attacked = net.impair(baseline, **model.impairment.model_dump())
        damaged = self.summary(attacked, model)
        damaged["recovery"] = net.recovery(packet, attacked, model.channel, repair_order=model.repair_order, **model.codec_params())
        damaged["pcap"] = self.pcap(attacked, "network_impaired.pcap")
        progress(95, "正在校验恢复结果")
        return {"status": "ok", "channel": model.channel, "execution": model.execution,
            "params": {**model.codec_params(), "repair_order": model.repair_order}, "impairment": model.impairment.model_dump(),
            "packet_bytes": len(packet), "frame_bytes": len(net.frame(packet)), "planned_packets": len(planned),
            "baseline": result, "impaired": damaged, "transport": transport,
            "detection_evaluation": evaluate_impairment(model.impairment.model_dump()),
            "capture_note": "PCAP 为 IP 层实验重建；真实 UDP 模式使用接收时刻与实际载荷长度，IP 字段为重建值；不捕获系统接口流量。",
            "impairment_note": "扰动在基线接收轨迹上离线施加，不改变系统网络设置，不额外发送流量。"}

    def load(self, model):
        item = self.store.get(model.file_id)
        rows, metadata = net.parse_pcap(item.path.read_bytes())
        if not rows:
            raise ValueError("PCAP 中没有受支持的非分片 IPv4 TCP/UDP 包")
        return item, rows, metadata

    def inspect(self, model, progress):
        progress(15, "正在读取 PCAP")
        item, rows, metadata = self.load(model)
        progress(50, "正在分析流量并提取信息")
        return {"status": "ok", "channel": model.channel, "file": item.public(), "parsing": metadata,
                "analysis": self.summary(rows, model), "params": {**model.codec_params(), "repair_order": model.repair_order}}

    def robustness(self, model, progress):
        item, rows, metadata = self.load(model)
        # Robustness needs a known recoverable baseline, not a guessed secret.
        reference = net.decode(rows, model.channel, repair_order=model.repair_order, **model.codec_params())
        progress(25, "正在模拟网络扰动")
        changed = net.impair(rows, **model.impairment.model_dump())
        before, after = self.summary(rows, model), self.summary(changed, model)
        for trace, data in ((rows, before), (changed, after)):
            data["recovery"] = net.recovery(reference, trace, model.channel, repair_order=model.repair_order, **model.codec_params())
        after["pcap"] = self.pcap(changed, "network_robustness.pcap")
        return {"status": "ok", "channel": model.channel, "baseline": before, "impaired": after,
                "detection_evaluation": evaluate_impairment(model.impairment.model_dump()),
                "file": item.public(), "parsing": metadata, "impairment": model.impairment.model_dump()}


router = APIRouter(prefix="/api/network-lab", tags=["network-lab"])


@router.get("/capabilities")
def capabilities():
    return {"channels": list(net.CHANNELS), "active_host": "127.0.0.1", "active_transport": "UDP only",
            "fields_offline_only": True, "max_packets": net.MAX_PACKETS, "max_pcap_packets": net.MAX_CAPTURE_PACKETS, "max_active_seconds": net.MAX_ACTIVE_SECONDS,
            "max_text_utf8_bytes": 128, "tshark_available": shutil.which("tshark") is not None,
            "driver_required": False, "pcap_linktype": "DLT_RAW / IPv4", "model": "Isolation Forest (synthetic references)",
            "remote_configured_only": True, "remote_channels": ['length', 'timing', 'data'],
            "remote_transports": ['tcp', 'udp'], "remote_file_bytes": transfer.MAX_FILE, "ciphers": list(remote.CIPHERS)}


@router.get('/peers')
def peers(request: Request):
    return {'peers': [p.public() for p in request.app.state.network_sessions.all_peers()], 'shared_key_field': False}


@router.post('/peers')
def save_peer(model: WebPeerRequest, request: Request):
    try:
        return request.app.state.network_sessions.save_peer(model.host, model.port, model.shared_key.get_secret_value())
    finally:
        model.shared_key = SecretStr('')


@router.post('/receiver/start')
def start_receiver(model: ReceiverRequest, request: Request):
    try:
        return request.app.state.network_sessions.start(model.bind_host, model.port, model.allowed_peer,
            model.shared_key.get_secret_value(), model.timeout, model.transport, model.allowed_ciphers)
    finally:
        model.shared_key = SecretStr('')


@router.get('/receiver')
def receiver_status(request: Request):
    return request.app.state.network_sessions.status()


@router.post('/receiver/stop')
def stop_receiver(request: Request):
    return request.app.state.network_sessions.stop()


@router.post('/send-remote', status_code=202)
def send_remote(model: RemoteRequest, request: Request):
    return submit(request, 'send_remote', model)


@router.post('/file-upload')
async def upload_network_file(request: Request, file: UploadFile = File(...)):
    content=bytearray()
    try:
        while chunk := await file.read(1024*1024):
            content.extend(chunk)
            if len(content)>transfer.MAX_FILE:
                raise APIError('file_too_large','网络文件不能超过 8 MiB',413)
        if not content:
            raise ValueError('文件不能为空')
        return request.app.state.files.add(bytes(content),file.filename or 'payload.bin','application/octet-stream').public()
    finally:
        await file.close()


@router.post("/estimate")
def estimate(model: RunRequest):
    packet = packet_for(model, probe=True)
    trace = net.encode(packet, model.channel, **model.codec_params())
    seconds = trace[-1]["t"]
    return {"packet_bytes": len(packet), "frame_bytes": len(net.frame(packet)), "packets": len(trace),
            "planned_seconds": seconds, "active_fits": seconds <= net.MAX_ACTIVE_SECONDS - 2}


def submit(request, operation, model):
    service = NetworkService(request.app.state.files, request.app.state.network_sessions)
    def work(progress):
        try:
            return getattr(service, operation)(model, progress)
        finally:
            for name in ("password", "confirm_password"):
                if hasattr(model, name):
                    setattr(model, name, None)
    return request.app.state.tasks.submit("network-" + operation, work)


@router.post("/run", status_code=202)
def run(model: RunRequest, request: Request):
    return submit(request, "run", model)


@router.post("/analyze", status_code=202)
def inspect(model: AnalyzeRequest, request: Request):
    return submit(request, "inspect", model)


@router.post("/robustness", status_code=202)
def robustness(model: RobustnessRequest, request: Request):
    return submit(request, "robustness", model)


@router.post("/uploads")
async def upload(request: Request, file: UploadFile = File(...)):
    content = bytearray()
    try:
        while chunk := await file.read(1024 * 1024):
            content.extend(chunk)
            if len(content) > MAX_UPLOAD:
                raise APIError("file_too_large", "PCAP 文件不能超过 16 MiB", 413)
        if not content:
            raise ValueError("PCAP 文件不能为空")
        import asyncio
        _, metadata = await asyncio.to_thread(net.parse_pcap, bytes(content))
        if not metadata["supported_packets"]:
            raise ValueError("PCAP 中没有受支持的 IPv4 TCP/UDP 包")
        item = request.app.state.files.add(bytes(content), file.filename or "traffic.pcap", "application/vnd.tcpdump.pcap")
        return {**item.public(), **metadata}
    finally:
        await file.close()
