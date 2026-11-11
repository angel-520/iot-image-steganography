"""Room membership and AV signaling only. No message/file/key relay exists here."""
from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
import json
import os
import secrets
from time import monotonic

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field

from .runtime import APIError


@dataclass
class Member:
    id: str
    name: str
    socket: WebSocket
    audio: bool = True
    video: bool = True
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def public(self):
        return {'id': self.id, 'name': self.name, 'audio': self.audio, 'video': self.video}

    async def send(self, value):
        async with self.lock:
            await asyncio.wait_for(self.socket.send_json(value), 5)


class Rooms:
    def __init__(self):
        self.rooms = {}
        self.lock = asyncio.Lock()
        raw = json.loads(os.getenv('ISTG_RTC_ICE_SERVERS', '[]'))
        if not isinstance(raw, list) or len(raw) > 8:
            raise ValueError('ISTG_RTC_ICE_SERVERS 必须为最多 8 项的 JSON 数组')
        for server in raw:
            if not isinstance(server, dict) or set(server) - {'urls', 'username', 'credential'}:
                raise ValueError('无效 ICE 配置')
            urls = server.get('urls', [])
            urls = [urls] if isinstance(urls, str) else urls
            if not urls or any(not isinstance(url, str) or not url.startswith(('stun:', 'stuns:', 'turn:', 'turns:')) for url in urls):
                raise ValueError('ICE 地址必须为 STUN / TURN URL')
        self.ice = raw

    async def create(self, step):
        async with self.lock:
            now = monotonic()
            self.rooms = {key: room for key, room in self.rooms.items()
                          if room['members'] or now - room['empty_since'] < 600}
            if len(self.rooms) >= 64:
                raise APIError('rooms_full', '房间数量已达上限，请稍后重试', 429)
            identifier = secrets.token_hex(6)
            token = secrets.token_urlsafe(24)
            self.rooms[identifier] = {'token': token, 'step': step, 'members': {}, 'empty_since': now}
            return {'room_id': identifier, 'token': token, 'max_members': 4, 'qim_step': step}

    async def close(self):
        for room in list(self.rooms.values()):
            for member in list(room['members'].values()):
                try:
                    await member.socket.close(1001)
                except Exception:
                    pass
        self.rooms.clear()


class RoomRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    qim_step: int = Field(default=24, ge=16, le=40, strict=True)


router = APIRouter(prefix='/api/secret-video', tags=['secret-video'])


@router.get('/capabilities')
async def capabilities(request: Request):
    return {'max_members': 4, 'file_bytes': 128 * 1024, 'width': 640, 'height': 480,
            'transport': 'WebRTC video frames only', 'ice_servers': request.app.state.secret_video.ice}


@router.post('/rooms', status_code=201)
async def create_room(model: RoomRequest, request: Request):
    return await request.app.state.secret_video.create(model.qim_step)


def signal(value):
    """Strict allowlist, including nested fields; never forward arbitrary objects."""
    if not isinstance(value, dict):
        raise ValueError('invalid_signal')
    kind = value.get('type')
    if kind in {'offer', 'answer'}:
        if set(value) != {'type', 'target', 'description'}:
            raise ValueError('invalid_signal')
        description = value['description']
        if not isinstance(description, dict) or set(description) != {'type', 'sdp'} or description['type'] != kind:
            raise ValueError('invalid_signal')
        sdp = description['sdp']
        if (not isinstance(sdp, str) or len(sdp) > 32000 or not sdp.startswith('v=0\r\n')
                or '\nm=application' in sdp or 'sctp' in sdp.lower()):
            raise ValueError('invalid_signal')
        return {'type': kind, 'target': value['target'], 'description': {'type': kind, 'sdp': sdp}}
    if kind == 'candidate':
        if set(value) != {'type', 'target', 'candidate'}:
            raise ValueError('invalid_signal')
        candidate = value['candidate']
        if not isinstance(candidate, dict) or set(candidate) - {'candidate', 'sdpMid', 'sdpMLineIndex', 'usernameFragment'}:
            raise ValueError('invalid_signal')
        if not isinstance(candidate.get('candidate'), str) or len(candidate['candidate']) > 2048:
            raise ValueError('invalid_signal')
        if any(val is not None and not isinstance(val, (str, int)) for val in candidate.values()):
            raise ValueError('invalid_signal')
        return {'type': kind, 'target': value['target'], 'candidate': candidate}
    if kind == 'media' and set(value) == {'type', 'audio', 'video'} and type(value['audio']) is bool and type(value['video']) is bool:
        return value
    if value == {'type': 'ping'}:
        return value
    raise ValueError('invalid_signal')


@router.websocket('/rooms/{room_id}/signal')
async def signaling(socket: WebSocket, room_id: str):
    origin = socket.headers.get('origin')
    scheme = 'https' if socket.url.scheme == 'wss' else 'http'
    if origin not in {f'{scheme}://{socket.url.netloc}', 'http://localhost:5173', 'http://127.0.0.1:5173'}:
        return await socket.close(1008)
    await socket.accept()
    hub = socket.app.state.secret_video
    member = room = None
    try:
        raw = await asyncio.wait_for(socket.receive_text(), 10)
        if len(raw) > 1024:
            raise ValueError('invalid_join')
        value = json.loads(raw)
        if not isinstance(value, dict) or set(value) != {'type', 'token', 'name'} or value.get('type') != 'join':
            raise ValueError('invalid_join')
        name, token = value.get('name'), value.get('token')
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 32 or not isinstance(token, str):
            raise ValueError('invalid_join')
        async with hub.lock:
            room = hub.rooms.get(room_id)
            if not room or not secrets.compare_digest(room['token'], token) or len(room['members']) >= 4:
                raise ValueError('room_missing_full_or_invitation_invalid')
            member = Member(secrets.token_hex(8), name.strip(), socket)
            others = list(room['members'].values())
            room['members'][member.id] = member
            await member.send({'type': 'welcome', 'id': member.id, 'members': [m.public() for m in others],
                               'qim_step': room['step'], 'ice_servers': hub.ice})
            for other in others:
                await other.send({'type': 'joined', 'member': member.public()})
        received = deque()
        while True:
            raw = await asyncio.wait_for(socket.receive_text(), 90)
            if len(raw) > 36000:
                raise ValueError('signal_too_large')
            now = monotonic()
            while received and received[0] < now - 10:
                received.popleft()
            received.append(now)
            if len(received) > 256:
                raise ValueError('signal_rate_limit')
            value = signal(json.loads(raw))
            if value['type'] == 'ping':
                await member.send({'type': 'pong'})
            elif value['type'] == 'media':
                member.audio, member.video = value['audio'], value['video']
                for other in list(room['members'].values()):
                    if other.id != member.id:
                        await other.send({'type': 'media', 'member': member.public()})
            else:
                target = room['members'].get(value.pop('target'))
                if target is None or target.id == member.id:
                    raise ValueError('invalid_target')
                await target.send({**value, 'from': member.id})
    except (WebSocketDisconnect, asyncio.TimeoutError):
        pass
    except (ValueError, TypeError, KeyError, RuntimeError):
        try:
            await socket.send_json({'type': 'error', 'message': '房间已满、邀请失效或信令格式无效；禁止通过信令发送聊天和文件'})
            await socket.close(1008)
        except Exception:
            pass
    finally:
        if member and room:
            async with hub.lock:
                room['members'].pop(member.id, None)
                room['empty_since'] = monotonic()
                for other in list(room['members'].values()):
                    try:
                        await other.send({'type': 'left', 'id': member.id})
                    except Exception:
                        pass
