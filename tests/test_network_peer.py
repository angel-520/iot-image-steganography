"""Real UDP peer integration, authorization, integrity and retry semantics."""
from hashlib import sha256
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from backend.main import create_app
from backend.runtime import APIError
from istg import network_peer as peer
from istg import network_stego as net

SECRET = 'test-only-shared-key-0123456789abcdef'
PARAMS = {'short_ms': 8., 'long_ms': 24., 'short_length': 64, 'long_length': 192}


def unused_port():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


class PeerTransfer(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.listener = None
        self.thread = None
        self.stop = threading.Event()

    def tearDown(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=2)
        if self.listener:
            self.listener.close()
        self.temp.cleanup()

    def receiver(self, cls=peer.Receiver, allowed='127.0.0.1'):
        port = unused_port()
        self.listener = cls('127.0.0.1', port, allowed, SECRET, self.root / 'inbox')
        self.thread = threading.Thread(target=self.listener.serve, kwargs={'stop':self.stop,'timeout':45}, daemon=True)
        self.thread.start()
        return peer.Peer('test-peer', '127.0.0.1', port, SECRET)

    def send(self, destination, text='双机中文 🔐', channel='length'):
        packet = peer.encrypted_text(text, SECRET)
        rows = net.encode(packet, channel, **PARAMS)
        return peer.transmit(destination, rows, channel, sha256(packet).hexdigest(), sha256(text.encode()).hexdigest(), PARAMS)

    def test_authenticated_actual_transfer_and_pcap_recovery(self):
        destination = self.receiver()
        result = self.send(destination)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['receipt']['plaintext_sha256'], sha256('双机中文 🔐'.encode()).hexdigest())
        self.assertEqual(len(self.listener.messages), 1)
        received = self.listener.messages[0]
        self.assertEqual(Path(received['file']).read_text(encoding='utf-8'), '双机中文 🔐')
        rows, _ = net.parse_pcap(Path(received['pcap']).read_bytes())
        packet = net.decode(rows, 'length')
        self.assertEqual(peer.decrypt_text(packet, SECRET), '双机中文 🔐'.encode())
        self.assertNotIn(SECRET, repr(destination))
        self.assertNotIn(SECRET, json.dumps(result))

    def test_selective_retry_duplicate_and_lost_receipt(self):
        class DropReceiver(peer.Receiver):
            dropped = False
            lost_receipt = False
            def process(self, data, address):
                current = self.current
                decoded = peer.decode_wire(data, current['key']) if current else None
                if decoded and decoded[0] == peer.DATA:
                    if decoded[2] == 7 and not self.dropped:
                        self.dropped = True
                        return
                    # Deliver each valid data packet twice to exercise deduplication.
                    super().process(data, address)
                super().process(data, address)
            def _reply(self, address, kind, session, body, key):
                if kind == peer.STATUS and not self.lost_receipt:
                    self.lost_receipt = True
                    return
                super()._reply(address, kind, session, body, key)
        result = self.send(self.receiver(DropReceiver), text='retry test')
        self.assertEqual(result['retransmitted_datagrams'], 1)
        self.assertEqual(len(self.listener.messages), 1)
        self.assertEqual(self.listener.messages[0]['text'], 'retry test')

    def test_wrong_key_and_disallowed_source_do_not_deliver(self):
        good = self.receiver()
        wrong = peer.Peer(good.name, good.host, good.port, 'wrong-key-01234567890123456789012345')
        with self.assertRaisesRegex(ValueError, '握手'):
            self.send(wrong)
        self.assertFalse(self.listener.messages)
        self.assertIsNone(self.listener.current)

    def test_receiver_ignores_unauthorized_ip_and_forged_auth(self):
        listener = peer.Receiver('127.0.0.1', unused_port(), '127.0.0.2', SECRET, self.root / 'restricted')
        try:
            manifest = peer.json_bytes({'channel':'length','count':100,'digest':'0'*64,'params':PARAMS})
            wire = peer.encode_wire(peer.HELLO,b'12345678',0,manifest,peer.auth_key(SECRET))
            listener.process(wire,('127.0.0.1',45000))
            self.assertIsNone(listener.current)
            forged = bytearray(wire); forged[-1] ^= 1
            listener.process(bytes(forged),('127.0.0.2',45000))
            self.assertIsNone(listener.current)
        finally:
            listener.close()

    def test_configured_peer_boundary_and_no_plaintext_packets(self):
        config = self.root / 'peers.json'
        config.write_text(json.dumps({'peers':[{'name':'friend','host':'127.0.0.1','port':42100,'key_env':'ISTG_TEST_KEY'}]}))
        with patch.dict(os.environ, {'ISTG_NETWORK_PEERS':str(config),'ISTG_TEST_KEY':SECRET}):
            loaded = peer.configured_peers()
            self.assertEqual(loaded[0].host,'127.0.0.1')
            self.assertNotIn(SECRET,json.dumps(loaded[0].public()))
        for address in ('example.com','0.0.0.0','224.0.0.1','255.255.255.255','169.254.0.2'):
            with self.assertRaises(ValueError):
                peer.Peer('bad',address,42100,SECRET)
        with self.assertRaises(ValueError):
            peer.auth_key('short')
        wire = peer.encode_wire(peer.DATA,b'12345678',1,b'padding',peer.auth_key(SECRET))
        self.assertIsNotNone(peer.decode_wire(wire,peer.auth_key(SECRET)))
        self.assertIsNone(peer.decode_wire(wire,peer.auth_key('another-key-01234567890123456789012')))
        encrypted = peer.encrypted_text('plaintext-to-conceal',SECRET)
        self.assertNotIn(b'plaintext-to-conceal',encrypted)

    def test_new_challenge_rejects_old_session_data(self):
        listener = peer.Receiver('127.0.0.1', unused_port(), '127.0.0.1', SECRET, self.root / 'replay')
        try:
            session = b'12345678'
            manifest = peer.json_bytes({'channel':'length','count':100,'digest':'0'*64,'params':PARAMS})
            hello = peer.encode_wire(peer.HELLO,session,0,manifest,peer.auth_key(SECRET))
            address=('127.0.0.1',45000)
            listener.process(hello,address)
            old_key = listener.current['key']
            body = net.MARKER.pack(b'NLB1',session,0).ljust(64,b'\x00')
            old_data = peer.encode_wire(peer.DATA,session,0,body,old_key)
            listener.current['started'] -= peer.MAX_SECONDS+1
            listener.process(hello,address)
            self.assertNotEqual(listener.current['key'],old_key)
            listener.process(old_data,address)
            self.assertFalse(listener.current['rows'])
        finally:
            listener.close()

    def test_web_api_peers_and_delivery_receipt(self):
        destination = self.receiver()
        config = self.root / 'web-peers.json'
        config.write_text(json.dumps({'peers':[{'name':destination.name,'host':destination.host,'port':destination.port,'key_env':'ISTG_TEST_KEY'}]}))
        with patch.dict(os.environ, {'ISTG_NETWORK_PEERS':str(config),'ISTG_TEST_KEY':SECRET}):
            with TestClient(create_app(self.root / 'store')) as client:
                response = client.get('/api/network-lab/peers')
                self.assertEqual(response.status_code,200)
                self.assertNotIn(SECRET,response.text)
                submitted = client.post('/api/network-lab/send-remote',json={'peer_name':destination.name,'channel':'length','text':'web delivered'})
                self.assertEqual(submitted.status_code,202,submitted.text)
                task = submitted.json()
                deadline = time.monotonic()+35
                while task['status'] in ('queued','running') and time.monotonic()<deadline:
                    time.sleep(.05)
                    task=client.get('/api/tasks/'+task['id']).json()
                self.assertEqual(task['status'],'completed',task)
                self.assertEqual(task['result']['receipt']['status'],'ok')
                self.assertEqual(self.listener.messages[0]['text'],'web delivered')
                exported=client.get('/api/tasks/'+task['id']+'/export')
                self.assertNotIn(SECRET,exported.text)
                rejected=client.post('/api/network-lab/send-remote',json={'peer_name':'x','text':'x','host':'192.0.2.1'})
                self.assertEqual(rejected.status_code,422)
                rejected=client.post('/api/network-lab/send-remote',json={'peer_name':'x','text':'x','channel':'ip_id'})
                self.assertEqual(rejected.status_code,422)
                rejected=client.post('/api/network-lab/send-remote',json={'peer_name':'x','text':'x','repair_order':False})
                self.assertEqual(rejected.status_code,422)


class WebsiteReceiver(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {'ISTG_NETWORK_PEERS':str(self.root/'absent.json')})
        self.env.start()
        self.app = create_app(self.root/'studio')
        self.client = TestClient(self.app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None,None,None)
        self.env.stop()
        self.temp.cleanup()

    def start(self, port=None, timeout=30):
        port = port or unused_port()
        response=self.client.post('/api/network-lab/receiver/start',json={
            'bind_host':'127.0.0.1','port':port,'allowed_peer':'127.0.0.1','shared_key':SECRET,'timeout':timeout})
        self.assertEqual(response.status_code,200,response.text)
        self.assertNotIn(SECRET,response.text)
        return port

    def send(self, port):
        saved=self.client.post('/api/network-lab/peers',json={'host':'127.0.0.1','port':port,'shared_key':SECRET})
        self.assertEqual(saved.status_code,200,saved.text)
        self.assertNotIn(SECRET,saved.text)
        submitted=self.client.post('/api/network-lab/send-remote',json={'peer_name':saved.json()['name'],'text':'完全通过网站接收 🔐'})
        self.assertEqual(submitted.status_code,202,submitted.text)
        task=submitted.json()
        deadline=time.monotonic()+40
        while task['status'] in ('queued','running') and time.monotonic()<deadline:
            time.sleep(.05)
            task=self.client.get('/api/tasks/'+task['id']).json()
        return task

    def test_website_settings_start_receive_download_stop(self):
        self.assertEqual(self.client.get('/api/network-lab/peers').json()['peers'],[])
        port=self.start()
        conflict=self.client.post('/api/network-lab/receiver/start',json={'port':port,'allowed_peer':'127.0.0.1','shared_key':SECRET})
        self.assertEqual(conflict.status_code,409)
        task=self.send(port)
        self.assertEqual(task['status'],'completed',task)
        state=self.client.get('/api/network-lab/receiver')
        self.assertNotIn(SECRET,state.text)
        messages=state.json()['messages']
        self.assertEqual(len(messages),1)
        self.assertEqual(messages[0]['text'],'完全通过网站接收 🔐')
        actual=self.client.get(messages[0]['file']['download_url']).content
        self.assertEqual(actual,'完全通过网站接收 🔐'.encode())
        self.assertEqual(sha256(actual).hexdigest(),messages[0]['sha256'])
        pcap=self.client.get(messages[0]['pcap']['download_url']).content
        rows,_=net.parse_pcap(pcap)
        self.assertEqual(peer.decrypt_text(net.decode(rows,'length'),SECRET),actual)
        stopped=self.client.post('/api/network-lab/receiver/stop').json()
        self.assertEqual(stopped['status'],'stopped')
        self.assertEqual(len(stopped['messages']),1)
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as probe:
            probe.bind(('127.0.0.1',port))

    def test_expiry_and_validation_do_not_expose_shared_key(self):
        self.start(timeout=1)
        deadline=time.monotonic()+3
        state=self.client.get('/api/network-lab/receiver').json()
        while state['status']=='listening' and time.monotonic()<deadline:
            time.sleep(.1)
            state=self.client.get('/api/network-lab/receiver').json()
        self.assertEqual(state['status'],'expired')
        for body in ({'host':'example.com','shared_key':SECRET}, {'host':'224.0.0.1','shared_key':SECRET}, {'host':'127.0.0.1','shared_key':'too-short'}):
            rejected=self.client.post('/api/network-lab/peers',json=body)
            self.assertEqual(rejected.status_code,422)
            self.assertNotIn(SECRET,rejected.text)

    def test_application_shutdown_releases_listener(self):
        port=self.start()
        self.app.state.network_sessions.close()
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as probe:
            probe.bind(('127.0.0.1',port))
        self.assertEqual(self.app.state.network_sessions.status()['status'],'stopped')

    def test_storage_failure_never_reports_delivery(self):
        port=self.start()
        with patch.object(self.app.state.files,'add',side_effect=APIError('storage_full','测试存储配额已满',429)):
            task=self.send(port)
        self.assertEqual(task['status'],'failed',task)
        self.assertEqual(self.client.get('/api/network-lab/receiver').json()['messages'],[])


if __name__ == '__main__':
    unittest.main()
