"""Network lab: real localhost sockets, PCAP round trips and fail-closed boundaries."""
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import socket
import tempfile
import time
import unittest
from unittest.mock import patch

import numpy as np
from fastapi.testclient import TestClient
from scapy.layers.inet import IP, UDP, TCP
from scapy.layers.l2 import Ether
from scapy.packet import Raw, Padding
from scapy.utils import PcapWriter

from backend.main import create_app
from istg import network_stego as net
from istg.network_detection import analyze, features, model_and_evaluation, synthetic_trace


class NetworkAlgorithms(unittest.TestCase):
    def test_all_channels_pcap_and_checksum_roundtrip(self):
        payload = 'secret-中文🔐'.encode()
        for channel in net.CHANNELS:
            with self.subTest(channel=channel):
                rows = net.encode(payload, channel)
                pcap = net.pcap_bytes(rows)
                loaded, info = net.parse_pcap(pcap)
                self.assertEqual(info['supported_packets'], len(rows))
                self.assertEqual(net.decode(loaded, channel), payload)
                self.assertTrue(net.recovery(payload, loaded, channel)['exact_match'])
                # IP, UDP and TCP checksums must be recomputable from wire data.
                packet = IP(rows[1]['_raw'])
                expected_ip = packet.chksum
                del packet.chksum
                if UDP in packet:
                    expected_transport = packet[UDP].chksum
                    del packet[UDP].chksum
                    recomputed = IP(bytes(packet))
                    self.assertEqual(recomputed[UDP].chksum, expected_transport)
                else:
                    expected_transport = packet[TCP].chksum
                    del packet[TCP].chksum
                    recomputed = IP(bytes(packet))
                    self.assertEqual(recomputed[TCP].chksum, expected_transport)
                self.assertEqual(recomputed.chksum, expected_ip)

    def test_actual_loopback_timing_and_length(self):
        for channel in ('timing', 'length'):
            with self.subTest(channel=channel):
                params = {'short_ms': 5., 'long_ms': 35.}
                planned = net.encode(b'OK', channel, **params)
                received, metadata = net.transmit_loopback(planned, channel)
                self.assertEqual(metadata['host'], '127.0.0.1')
                self.assertEqual(metadata['sent_packets'], metadata['received_packets'])
                self.assertEqual(net.decode(received, channel, **params), b'OK')
                self.assertNotEqual([r['t'] for r in planned], [r['t'] for r in received])

    def test_active_scope_blocked_before_opening_sockets(self):
        rows = net.encode(b'a', 'length')
        rows[0]['dst'] = '192.0.2.10'
        with patch.object(socket, 'socket', side_effect=AssertionError('socket must not open')):
            with self.assertRaises(ValueError):
                net.transmit_loopback(rows, 'length')
            with self.assertRaises(ValueError):
                net.transmit_loopback(rows, 'tcp_seq')

    def test_delay_loss_reordering_jitter(self):
        for channel in net.CHANNELS:
            with self.subTest(channel=channel):
                rows = net.encode(b'network-robustness', channel)
                delayed = net.impair(rows, delay_ms=200)
                self.assertEqual(net.decode(delayed, channel), b'network-robustness')
                self.assertEqual(net.impair(rows, seed=42, jitter_ms=10), net.impair(rows, seed=42, jitter_ms=10))
                lost = net.impair(rows, loss_rate=.5)
                self.assertFalse(net.recovery(b'network-robustness', lost, channel)['exact_match'])
                self.assertGreater(net.recovery(b'network-robustness', lost, channel)['missing_bits'], 0)
                empty = net.impair(rows, loss_rate=1)
                stats = net.recovery(b'network-robustness', empty, channel)
                self.assertEqual(stats['bit_recovery_rate'], 0)
                self.assertEqual(stats['ber'], 1)
                reordered = list(rows)
                first, second = reordered[4]['t'], reordered[5]['t']
                reordered[4] = {**reordered[4], 't': second}
                reordered[5] = {**reordered[5], 't': first}
                reordered.sort(key=lambda r: r['t'])
                if channel != 'timing':
                    self.assertEqual(net.decode(reordered, channel, repair_order=True), b'network-robustness')
                    self.assertFalse(net.recovery(b'network-robustness', reordered, channel, repair_order=False)['exact_match'])
                    simulated = net.impair(rows, reorder_rate=1, seed=42)
                    self.assertNotEqual([r['sequence'] for r in simulated], [r['sequence'] for r in rows])
                    self.assertEqual(net.decode(simulated, channel), b'network-robustness')

    def test_corruption_and_pcap_bounds(self):
        rows = net.encode(b'test', 'length')
        changed = [dict(r) for r in rows]
        changed[2]['payload_length'] = 192 if changed[2]['payload_length'] == 64 else 64
        with self.assertRaises(ValueError):
            net.decode(changed, 'length')
        with self.assertRaises(ValueError):
            net.frame(b'x' * 473)
        with self.assertRaises(ValueError):
            net.parse_pcap(b'not a capture')
        self.assertEqual(net.parse_pcap(net.pcap_bytes([]))[0], [])
        with self.assertRaises(ValueError):
            net.decode(rows + [rows[-1]], 'length')
        with self.assertRaises(ValueError):
            net.encode(b'a', 'length', short_length=192, long_length=64)

    def test_ethernet_padding_is_not_ip_payload(self):
        buffer = BytesIO()
        writer = PcapWriter(buffer, linktype=1, sync=True)
        packet = Ether()/IP(src='192.0.2.1', dst='192.0.2.2')/UDP(sport=50,dport=51)/Raw(b'123')
        packet = Ether(bytes(packet)) / Padding(b'\x00' * 10)
        writer.write(packet)
        data = buffer.getvalue()
        writer.close()
        rows, _ = net.parse_pcap(data)
        self.assertEqual(rows[0]['payload_length'], 3)
        self.assertEqual(rows[0]['length'], 31)

    def test_statistics_and_ml_holdout(self):
        for channel in ('timing', 'length'):
            result = analyze(net.encode(b'text', channel))
            self.assertEqual(result['status'], 'ok')
            self.assertTrue(result['statistical_suspicious'])
            self.assertTrue(result['windows'])
            self.assertTrue(np.isfinite(features(net.encode(b'text', channel))).all())
        _, report = model_and_evaluation()
        self.assertEqual(report['training_windows'], 256)
        self.assertEqual(sum(report[key] for key in ('TP', 'FP', 'TN', 'FN')), 120)
        self.assertGreaterEqual(report['roc_auc'], 0)
        self.assertLessEqual(report['roc_auc'], 1)
        self.assertEqual(analyze([])['status'], 'insufficient_data')
        normal = synthetic_trace(np.random.default_rng(100))
        self.assertFalse(analyze(normal)['statistical_suspicious'])


class NetworkAPI(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.client = TestClient(create_app(Path(self.temp.name)))
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temp.cleanup()

    def task(self, operation, body):
        response = self.client.post('/api/network-lab/' + operation, json=body)
        self.assertEqual(response.status_code, 202, response.text)
        task = response.json()
        deadline = time.monotonic() + 50
        while task['status'] in ('running','queued') and time.monotonic() < deadline:
            time.sleep(.03)
            task = self.client.get('/api/tasks/' + task['id']).json()
        self.assertEqual(task['status'], 'completed', task)
        return task['result'], task['id']

    def test_api_all_channels_encrypted_export_upload_extract(self):
        for channel in net.CHANNELS:
            with self.subTest(channel=channel):
                body = {'channel': channel, 'execution': 'simulation', 'text': '网络 secret 🔐',
                        'encryption':'AES-256-GCM', 'password':'password', 'confirm_password':'password'}
                result, identifier = self.task('run', body)
                self.assertEqual(result['baseline']['recovered']['text'], body['text'])
                self.assertTrue(result['baseline']['recovered']['authenticated'])
                self.assertTrue(result['baseline']['recovery']['exact_match'])
                self.assertTrue(result['impaired']['recovery']['exact_match'])
                pcap = self.client.get(result['baseline']['pcap']['download_url']).content
                upload = self.client.post('/api/network-lab/uploads', files={'file':('lab.pcap',pcap)})
                self.assertEqual(upload.status_code, 200, upload.text)
                restored, _ = self.task('analyze', {'file_id':upload.json()['id'],'channel':channel,'password':'password'})
                self.assertEqual(restored['analysis']['recovered']['text'], body['text'])
                report = self.client.get('/api/tasks/' + identifier + '/export').json()
                self.assertNotIn('password', report['result'])
                self.assertNotIn('_raw', str(report))

    def test_api_actual_loopback_length(self):
        result, _ = self.task('run', {'channel':'length','execution':'loopback','text':'local only'})
        self.assertEqual(result['transport']['host'], '127.0.0.1')
        self.assertEqual(result['baseline']['recovered']['text'], 'local only')

    def test_scope_and_resource_validation(self):
        for invalid in ({'host':'192.0.2.1'}, {'destination':'example.com'}, {'execution':'loopback','channel':'tcp_seq'},
                        {'short_ms':10,'long_ms':11}, {'text':'汉' * 128}, {'short_ms':float('inf')}):
            if any(isinstance(v,float) and not np.isfinite(v) for v in invalid.values()):
                response = self.client.post('/api/network-lab/run', content='{"short_ms":1e999}', headers={'Content-Type':'application/json'})
            else:
                response = self.client.post('/api/network-lab/run', json=invalid)
            self.assertEqual(response.status_code, 422, response.text)
        invalid_upload = self.client.post('/api/network-lab/uploads',files={'file':('bad.pcap',b'bad')})
        self.assertEqual(invalid_upload.status_code,422)
        for truncated in (b'\xd4\xc3\xb2\xa1', b'\x0a\x0d\x0d\x0a\x00\x00\x00\x00'):
            malformed = self.client.post('/api/network-lab/uploads',files={'file':('truncated.pcap',truncated)})
            self.assertEqual(malformed.status_code,422)
        result = self.client.get('/api/network-lab/capabilities').json()
        self.assertTrue(result['fields_offline_only'])
        self.assertFalse(result['driver_required'])

    def test_robustness_and_missing_password(self):
        result, _ = self.task('run', {'channel':'length','text':'known reference', 'encryption':'AES-256-GCM',
            'password':'pwd','confirm_password':'pwd', 'impairment':{'loss_rate':1}})
        self.assertEqual(result['impaired']['recovery']['ber'],1)
        self.assertEqual(result['impaired']['recovered']['status'],'failed')
        self.assertEqual(result['detection_evaluation']['impaired']['undetermined_windows'],120)
        self.assertIsNone(result['detection_evaluation']['impaired']['precision'])
        identifier = result['baseline']['pcap']['id']
        inspected, _ = self.task('analyze',{'file_id':identifier,'channel':'length'})
        self.assertEqual(inspected['analysis']['recovered']['status'],'password_required')
        attacked, _ = self.task('robustness',{'file_id':identifier,'channel':'length','password':'pwd','impairment':{'jitter_ms':25}})
        self.assertTrue(attacked['impaired']['recovery']['exact_match'])
        self.assertEqual(attacked['impaired']['recovered']['text'],'known reference')


if __name__ == '__main__':
    unittest.main()
