"""Signaling admission, four-member rooms, protocol boundaries and HTTPS access."""
from contextlib import ExitStack
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from backend.main import create_app
from backend.secret_video import signal


class SecretVideoSignaling(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.client = TestClient(create_app(Path(self.temp.name)))
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temp.cleanup()

    def room(self):
        response = self.client.post('/api/secret-video/rooms', json={'qim_step': 24})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def connect(self, stack, room, name):
        socket = stack.enter_context(self.client.websocket_connect(f"/api/secret-video/rooms/{room['room_id']}/signal", headers={'origin': 'http://testserver'}))
        socket.send_json({'type': 'join', 'token': room['token'], 'name': name})
        return socket, socket.receive_json()

    def test_four_member_call_signaling_and_no_secret_relay(self):
        room = self.room()
        with ExitStack() as stack:
            a, welcome_a = self.connect(stack, room, 'Alice')
            b, welcome_b = self.connect(stack, room, 'Bob')
            self.assertEqual(a.receive_json()['type'], 'joined')
            self.assertEqual(len(welcome_b['members']), 1)
            sdp = {'type': 'offer', 'sdp': 'v=0\r\nm=video 9 UDP/TLS/RTP/SAVPF 96\r\n'}
            a.send_json({'type': 'offer', 'target': welcome_b['id'], 'description': sdp})
            relayed = b.receive_json()
            self.assertEqual(relayed, {'type': 'offer', 'from': welcome_a['id'], 'description': sdp})
            c, welcome_c = self.connect(stack, room, 'Carol')
            a.receive_json(); b.receive_json()
            d, welcome_d = self.connect(stack, room, 'Dave')
            a.receive_json(); b.receive_json(); c.receive_json()
            self.assertEqual(len(welcome_c['members']), 2)
            self.assertEqual(len(welcome_d['members']), 3)
            _, fifth = self.connect(stack, room, 'Fifth')
            self.assertEqual(fifth['type'], 'error')
            a.send_json({'type': 'media', 'audio': False, 'video': True})
            for peer in (b, c, d):
                event = peer.receive_json()
                self.assertEqual(event['type'], 'media'); self.assertFalse(event['member']['audio'])
            a.send_json({'type': 'chat', 'text': 'must-not-relay'})
            self.assertEqual(a.receive_json()['type'], 'error')
            for peer in (b, c, d):
                self.assertEqual(peer.receive_json()['type'], 'left')

    def test_invalid_invitation_and_origin_rejected(self):
        room = self.room()
        with self.client.websocket_connect(f"/api/secret-video/rooms/{room['room_id']}/signal", headers={'origin': 'http://testserver'}) as socket:
            socket.send_json({'type': 'join', 'token': 'wrong', 'name': 'No admission'})
            self.assertEqual(socket.receive_json()['type'], 'error')
        from starlette.websockets import WebSocketDisconnect
        with self.assertRaises(WebSocketDisconnect):
            with self.client.websocket_connect(f"/api/secret-video/rooms/{room['room_id']}/signal", headers={'origin': 'https://foreign.example'}):
                pass

    def test_nested_secret_fields_and_datachannel_sdp_rejected(self):
        samples = [
            {'type': 'chat', 'text': 'secret'}, {'type': 'file', 'data': 'secret'},
            {'type': 'ping', 'text': 'secret'},
            {'type': 'media', 'audio': True, 'video': False, 'password': 'secret'},
            {'type': 'candidate', 'target': 'peer', 'candidate': {'candidate': '', 'secret': 'data'}},
            {'type': 'offer', 'target': 'peer', 'description': {'type': 'offer', 'sdp': 'v=0\r\nm=application 9 UDP/DTLS/SCTP webrtc-datachannel\r\n'}},
            {'type': 'answer', 'target': 'peer', 'description': {'type': 'answer', 'sdp': 'v=0\r\n', 'data': 'hidden'}},
        ]
        for value in samples:
            with self.subTest(value=value['type']), self.assertRaises(ValueError):
                signal(value)
        self.assertEqual(signal({'type': 'ping'}), {'type': 'ping'})

    def test_https_explicit_host_and_legacy_default(self):
        with patch.dict('os.environ', {'ISTG_PUBLIC_HOSTS': '192.168.9.12'}):
            with TestClient(create_app(Path(self.temp.name) / 'https'), base_url='https://192.168.9.12') as client:
                response = client.post('/api/secret-video/rooms', json={'qim_step': 32}, headers={'origin': 'https://192.168.9.12'})
                self.assertEqual(response.status_code, 201)
                self.assertEqual(client.get('/api/health').status_code, 200)
        response = self.client.post('/api/secret-video/rooms', json={'qim_step': 24, 'secret': 'no'})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.client.get('/api/secret-video/capabilities').json()['transport'], 'WebRTC video frames only')


if __name__ == '__main__':
    unittest.main()
