"""V2 file integrity, socket addresses, transport selection, throttling and recovery."""
from hashlib import sha256
from io import BytesIO
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from PIL import Image
from fastapi.testclient import TestClient
from backend.main import create_app
from istg import network_peer as peer, network_stego as net, network_transfer as transfer

KEY='transfer-test-shared-key-0123456789012345'


def free_port(transport='udp',host='127.0.0.1'):
    with socket.socket(socket.AF_INET,socket.SOCK_STREAM if transport=='tcp' else socket.SOCK_DGRAM) as probe:
        probe.bind((host,0)); return probe.getsockname()[1]


class TransferAlgorithms(unittest.TestCase):
    def test_eight_mib_tcp_crosses_legacy_packet_limit(self):
        with tempfile.TemporaryDirectory() as folder:
            port=free_port('tcp');messages=[]
            receiver=transfer.TCPReceiver('127.0.0.1',port,'127.0.0.1',KEY,folder,message_sink=messages.append)
            stop=threading.Event();worker=threading.Thread(target=receiver.serve,kwargs={'stop':stop,'timeout':90});worker.start()
            try:
                data=bytes(range(256))*(transfer.MAX_FILE//256)
                packet=transfer.pack_content(data,KEY,filename='eight-mib.bin')
                result=transfer.transmit(peer.Peer('large','127.0.0.1',port,KEY),packet,sha256(data).hexdigest(),rate_mbps=50)
                self.assertGreater(result['receipt']['received_packets'],net.MAX_PACKETS)
                self.assertEqual(Path(messages[0]['file']).read_bytes(),data)
                rows,_=net.parse_pcap(Path(messages[0]['pcap']).read_bytes())
                self.assertGreater(len(rows),net.MAX_PACKETS)
            finally:
                stop.set();worker.join(2);receiver.close()

    def test_udp_loss_and_receipt_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            port=free_port(); messages=[]
            receiver=transfer.UDPReceiver('127.0.0.1',port,'127.0.0.1',KEY,folder,message_sink=messages.append)
            original=receiver.engine.process; dropped=False; lost=False
            def impair(data,address,local,stamp=None):
                nonlocal dropped,lost
                current=receiver.engine.current
                decoded=peer.decode_wire(data,current['key']) if current else None
                if decoded and decoded[0]==peer.DATA and decoded[2]==1 and not dropped:
                    dropped=True; return None
                response=original(data,address,local,stamp)
                if receiver.engine.completed and response and not lost:
                    lost=True; return None
                return response
            receiver.engine.process=impair
            stop=threading.Event(); worker=threading.Thread(target=receiver.serve,kwargs={'stop':stop,'timeout':30}); worker.start()
            data=bytes(range(256))*20
            try:
                packet=transfer.pack_content(data,KEY,filename='binary.bin')
                result=transfer.transmit(peer.Peer('local','127.0.0.1',port,KEY),packet,sha256(data).hexdigest(),transport='udp',window_size=4)
                self.assertEqual(result['retransmitted_datagrams'],1)
                self.assertEqual(len(messages),1)
                self.assertEqual(Path(messages[0]['file']).read_bytes(),data)
            finally:
                stop.set();worker.join(2);receiver.close()

    def test_legacy_receiver_pcap_uses_observed_addresses(self):
        with tempfile.TemporaryDirectory() as folder:
            port=free_port(host='127.0.0.2')
            receiver=peer.Receiver('127.0.0.2',port,'127.0.0.1',KEY,folder)
            stop=threading.Event(); worker=threading.Thread(target=receiver.serve,kwargs={'stop':stop,'timeout':25});worker.start()
            try:
                packet=peer.encrypted_text('address test',KEY)
                rows=net.encode(packet,'length'); traces={}
                result=peer.transmit(peer.Peer('other-loopback','127.0.0.2',port,KEY),rows,'length',sha256(packet).hexdigest(),
                    sha256(b'address test').hexdigest(),{'short_ms':8.,'long_ms':24.,'short_length':64,'long_length':192},
                    trace_sink=lambda logical,wire:traces.update(logical=logical,wire=wire))
                self.assertEqual(result['receipt']['destination'][0],'127.0.0.2')
                parsed,_=net.parse_pcap(Path(receiver.messages[0]['pcap']).read_bytes())
                self.assertTrue(all(r['dst']=='127.0.0.2' for r in parsed))
                self.assertTrue(all(r['src']=='127.0.0.1' for r in parsed))
                self.assertTrue(all(r['dst']=='127.0.0.2' for r in traces['wire']))
                self.assertEqual(net.decode(parsed,'length'),packet)
            finally:
                stop.set();worker.join(2);receiver.close()

    def test_precise_timestamp_and_trace_addresses(self):
        trace=transfer.Trace('tcp')
        trace.add(b'abc',('192.0.2.1',3000),('198.51.100.2',4000),trace.origin+.001234)
        trace.add(b'def',('192.0.2.1',3000),('198.51.100.2',4000),trace.origin+.002468)
        rows,_=net.parse_pcap(net.pcap_bytes(trace.rows))
        self.assertEqual(rows[0]['src'],'192.0.2.1')
        self.assertEqual(rows[0]['dst'],'198.51.100.2')
        self.assertAlmostEqual(rows[1]['t'],.001234,places=5)
        self.assertEqual(rows[1]['tcp_seq']-rows[0]['tcp_seq'],3)

    def test_rate_limit_and_cipher_rejection(self):
        with tempfile.TemporaryDirectory() as folder:
            port=free_port('tcp');receiver=transfer.TCPReceiver('127.0.0.1',port,'127.0.0.1',KEY,folder)
            stop=threading.Event();worker=threading.Thread(target=receiver.serve,kwargs={'stop':stop,'timeout':20});worker.start()
            try:
                data=b'x'*8192
                packet=transfer.pack_content(data,KEY,filename='rate.bin')
                result=transfer.transmit(peer.Peer('rate','127.0.0.1',port,KEY),packet,sha256(data).hexdigest(),rate_mbps=.05)
                self.assertGreaterEqual(result['elapsed_seconds'],len(packet)*8/50000*.9)
                packet=transfer.pack_content(data,KEY,cipher='none',filename='plain.bin')
                with self.assertRaisesRegex(ValueError,'加密'):
                    transfer.transmit(peer.Peer('reject','127.0.0.1',port,KEY),packet,sha256(data).hexdigest(),cipher='none')
            finally:
                stop.set();worker.join(2);receiver.close()


class TransferWebsite(unittest.TestCase):
    def test_all_transports_and_ciphers_binary_roundtrip(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ,{'ISTG_NETWORK_PEERS':str(Path(folder)/'missing.json')}):
            with TestClient(create_app(Path(folder)/'store')) as client:
                image=BytesIO();Image.new('RGB',(50,30),(25,150,233)).save(image,format='PNG')
                data=image.getvalue()+bytes(range(256))*8
                for protocol in ('tcp','udp'):
                    for cipher in peer.CIPHERS:
                        with self.subTest(protocol=protocol,cipher=cipher):
                            port=free_port(protocol,host='127.0.0.2')
                            response=client.post('/api/network-lab/receiver/start',json={'bind_host':'127.0.0.2','port':port,
                                'allowed_peer':'127.0.0.1','shared_key':KEY,'transport':protocol,'allowed_ciphers':[cipher]})
                            self.assertEqual(response.status_code,200,response.text)
                            saved=client.post('/api/network-lab/peers',json={'host':'127.0.0.2','port':port,'shared_key':KEY}).json()
                            uploaded=client.post('/api/network-lab/file-upload',files={'file':('image-original.png',data)})
                            self.assertEqual(uploaded.status_code,200,uploaded.text)
                            task=client.post('/api/network-lab/send-remote',json={'peer_name':saved['name'],'channel':'data',
                                'transport':protocol,'cipher':cipher,'secret_id':uploaded.json()['id'],'rate_mbps':1,'window_size':4}).json()
                            deadline=time.monotonic()+30
                            while task['status'] in ('queued','running') and time.monotonic()<deadline:
                                time.sleep(.03);task=client.get('/api/tasks/'+task['id']).json()
                            self.assertEqual(task['status'],'completed',task)
                            message=client.get('/api/network-lab/receiver').json()['messages'][0]
                            self.assertEqual(message['kind'],'file')
                            self.assertEqual(message['filename'],'image-original.png')
                            self.assertEqual(client.get(message['file']['download_url']).content,data)
                            self.assertEqual(message['sha256'],sha256(data).hexdigest())
                            self.assertEqual(message['destination'][0],'127.0.0.2')
                            wire=client.get(message['pcap']['download_url']).content
                            rows,_=net.parse_pcap(wire)
                            self.assertTrue(any(r['dst']=='127.0.0.2' and r['src']=='127.0.0.1' for r in rows))
                            self.assertTrue(all(r['protocol']==protocol.upper() for r in rows))
                            self.assertNotIn(KEY,client.get('/api/tasks/'+task['id']+'/export').text)
                            self.assertEqual(client.post('/api/network-lab/receiver/stop').status_code,200)

    def test_invalid_transfer_options(self):
        with tempfile.TemporaryDirectory() as folder:
            with TestClient(create_app(folder)) as client:
                for extra in ({'transport':'tcp'}, {'channel':'data','rate_mbps':0}, {'channel':'data','chunk_size':4000},
                              {'channel':'data','retries':99}, {'channel':'data','secret_id':'a'*32}, {'cipher':'unknown'}):
                    response=client.post('/api/network-lab/send-remote',json={'peer_name':'x','text':'x',**extra})
                    self.assertEqual(response.status_code,422,response.text)
                too_big=client.post('/api/network-lab/file-upload',files={'file':('large.bin',b'x'*(transfer.MAX_FILE+1))})
                self.assertEqual(too_big.status_code,413)


if __name__=='__main__':
    unittest.main()
