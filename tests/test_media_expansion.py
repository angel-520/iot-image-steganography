"""Large real carriers, multi-bit framing, limits, compression and bounded decoding."""
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch
import tempfile
import time
import unittest

import numpy as np
from fastapi.testclient import TestClient

import istg
from backend.main import create_app
from istg import multimodal as mm, media_stream as stream, media_limits as limits
from istg.istg_pack import pack_binary


class MediaExpansion(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def upload(client, data, name, modality='file', purpose='cover'):
        response = client.post('/api/multimodal/uploads', files={'file': (name, data)},
                               data={'modality': modality, 'purpose': purpose})
        if response.status_code != 200:
            raise AssertionError(response.text)
        return response.json()

    def task(self, client, endpoint, body):
        response = client.post('/api/multimodal/' + endpoint, json=body)
        self.assertEqual(response.status_code, 202, response.text)
        task = response.json()
        deadline = time.monotonic() + 120
        while task['status'] in {'queued', 'running'} and time.monotonic() < deadline:
            time.sleep(.04)
            task = client.get('/api/tasks/' + task['id']).json()
        self.assertEqual(task['status'], 'completed', task)
        return task['result']

    def test_multibit_streaming_odd_stereo_and_saturation(self):
        rng = np.random.default_rng(123)
        packet = rng.bytes(1553)
        arrays = [rng.integers(-32768, 32768, (35001, 2), dtype=np.int16),
                  np.full((35001, 1), 32767, dtype=np.int16),
                  np.full((35001, 2), -32768, dtype=np.int16),
                  np.tile(np.array([[32767], [-32768]], dtype=np.int16), (17501, 1))]
        for algorithm, depths in [('audio_lsb', [1, 2, 4, 9, 12]), ('audio_dwt', [1, 2, 3, 4])]:
            for depth in depths:
                for samples in arrays:
                    with self.subTest(algorithm=algorithm, depth=depth, first=int(samples[0, 0])):
                        source, output = self.root / 'source.wav', self.root / 'stego.wav'
                        source.write_bytes(mm.audio_bytes(samples, 16000))
                        stream.embed_audio(source, output, packet, algorithm, depth)
                        self.assertEqual(stream.extract(stream.audio_slot_chunks(output, algorithm), depth), packet)
                        decoded, rate = mm.read_audio(output)
                        self.assertEqual(decoded.shape, samples.shape)
                        self.assertEqual(rate, 16000)
                        if depth == 1:
                            self.assertLessEqual(np.abs(samples.astype(int) - decoded.astype(int)).max(), 2)
                        with self.assertRaises(ValueError):
                            stream.extract(stream.audio_slot_chunks(output, algorithm), 2 if depth == 1 else 1)

    def test_screenshot_size_payload_fits_at_nine_bits_with_aead(self):
        samples = np.zeros((int(5.05 * 1048576) // 4, 2), dtype=np.int16)
        secret = np.random.default_rng(1).bytes(int(2.61 * 1048576))
        with TestClient(create_app(self.root / 'store')) as client:
            wav = self.upload(client, mm.audio_bytes(samples, 44100), 'minute.wav', 'audio')
            file = self.upload(client, secret, 'archive.zip')
            carrier = {'file_id': wav['id'], 'modality': 'audio', 'bit_depth': 1}
            body = {'carrier': carrier, 'secret_id': file['id'], 'encryption': 'AES-256-GCM',
                    'password': 'expansion', 'confirm_password': 'expansion', 'compression': 'auto'}
            capacity = client.post('/api/multimodal/capacity', json=body).json()
            self.assertFalse(capacity['fits'])
            self.assertEqual(capacity['suggested_bit_depth'], 9)
            carrier['bit_depth'] = 9
            capacity = client.post('/api/multimodal/capacity', json=body).json()
            self.assertTrue(capacity['fits'])
            embedded = self.task(client, 'embed', body)
            self.assertEqual(embedded['carrier']['bit_depth'], 9)
            output = client.get(embedded['stego']['download_url']).content
            uploaded = self.upload(client, output, 'reupload.wav', 'audio', 'stego')
            recovered = self.task(client, 'extract', {'carrier': {**carrier, 'file_id': uploaded['id']}, 'password': 'expansion'})
            self.assertEqual(recovered['sha256'], sha256(secret).hexdigest())
            self.assertEqual(client.get(recovered['file']['download_url']).content, secret)

    def test_uncompressed_seventeen_mib_secret_and_large_wav_upload(self):
        secret = np.random.default_rng(4).bytes(17 * 1048576)
        samples = np.zeros((24 * 1048576 // 4, 2), dtype=np.int16)
        with TestClient(create_app(self.root / 'store')) as client:
            file = self.upload(client, secret, 'large.bin')
            wav = self.upload(client, mm.audio_bytes(samples, 48000), 'large.wav', 'audio')
            embedded = self.task(client, 'embed', {'carrier': {'file_id': wav['id'], 'modality': 'audio', 'bit_depth': 12},
                                                    'secret_id': file['id'], 'compression': 'none'})
            self.assertGreater(embedded['packet_bytes'], 16 * 1048576)
            recovered = self.task(client, 'extract', {'carrier': embedded['carrier']})
            self.assertEqual(recovered['sha256'], sha256(secret).hexdigest())
            self.assertEqual(client.get(recovered['file']['download_url']).content, secret)

    def test_video_beyond_1200_frames_and_96_mib_is_streamed(self):
        source = self.root / 'long.mkv'
        info = {'width': 256, 'height': 144, 'fps': 30}
        writer = stream.video_writer(source, info)
        try:
            for index in range(1205):
                frame = np.full((144, 256, 3), index % 256, dtype=np.uint8)
                writer.send(frame.tobytes())
        finally:
            writer.close()
        # Padding inside AVI/MKV is not required: the 132 MiB decoded sequence is enough
        # to exercise both old decode guards, and np.stack is forbidden in the web path.
        with TestClient(create_app(self.root / 'store')) as client, patch.object(mm, 'read_video', side_effect=AssertionError('whole-video decode')):
            file = self.upload(client, source.read_bytes(), 'long.mkv', 'video')
            embedded = self.task(client, 'embed', {'carrier': {'file_id': file['id'], 'modality': 'video', 'bit_depth': 4}, 'text': 'streamed long video'})
            self.assertEqual(embedded['analysis']['frame_count'], 1205)
            self.assertEqual(embedded['analysis']['width'], 256)
            self.assertTrue(embedded['thumbnails'])
            recovered = self.task(client, 'extract', {'carrier': embedded['carrier']})
            self.assertEqual(recovered['text'], 'streamed long video')
            attacked = self.task(client, 'attack', {'carrier': embedded['carrier'], 'attack': 'crop'})
            self.assertFalse(attacked['exact_match'])

    def test_4k_240fps_metadata_and_video_upload_over_16_mib(self):
        # A real 4K frame: upload/header validation and capacity use the streaming reader.
        source = self.root / '4k.mkv'
        writer = stream.video_writer(source, {'width': 3840, 'height': 2160, 'fps': 240})
        try:
            writer.send(np.zeros((2160, 3840, 3), dtype=np.uint8).tobytes())
        finally:
            writer.close()
        self.assertEqual(stream.video_info(source, count=True)['count'], 1)
        # PCM AVI video has a valid >16 MiB on-disk payload without expensive random FFV1.
        import imageio_ffmpeg
        import subprocess
        large = self.root / 'large.avi'
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-y', '-v', 'error', '-f', 'lavfi', '-i',
                        'color=size=640x480:rate=30', '-frames:v', '24', '-c:v', 'rawvideo', '-pix_fmt', 'bgr24',
                        '-threads', '1', str(large)], check=True, capture_output=True)
        self.assertGreater(large.stat().st_size, 16 * 1048576)
        with TestClient(create_app(self.root / 'store')) as client:
            file = self.upload(client, large.read_bytes(), 'large.avi', 'video')
            cap = client.post('/api/multimodal/capacity', json={'carrier': {'file_id': file['id'], 'modality': 'video'}, 'text': 'large upload'})
            self.assertEqual(cap.status_code, 200, cap.text)
            self.assertTrue(cap.json()['fits'])
            embedded = self.task(client, 'embed', {'carrier': {'file_id': file['id'], 'modality': 'video'}, 'text': 'large upload'})
            self.assertTrue(embedded['recovery']['exact_match'])

    def test_compressed_64_mib_roundtrip_and_bomb_rejection(self):
        from istg.security_analysis.packet import _payload_schema
        import cbor2
        import zlib
        data = b'A' * limits.SECRET_BYTES
        packet = pack_binary(data, 'large.txt', max_bytes=limits.SECRET_BYTES, compress=True)
        self.assertLess(len(packet), 100000)
        encrypted = istg.encrypt_istg(packet, 'compression', 'ChaCha20-Poly1305')
        restored = istg.decrypt_istg(encrypted, 'compression')
        self.assertEqual(istg.parse_istg(restored, limits.PACKET_BYTES)['payload_data']['data'], data)
        for size in [limits.SECRET_BYTES + 1, 5]:
            bomb = cbor2.dumps({'kind': 'file', 'data': zlib.compress(b'A' * 100), 'content_compression': 'zlib', 'original_size': size})
            with self.assertRaises(ValueError):
                _payload_schema(bomb, limits.PACKET_BYTES)
        raw = np.random.default_rng(1).bytes(4096)
        parsed = istg.parse_istg(pack_binary(raw, 'random.zip', compress=True))
        self.assertNotIn('content_compression', parsed['payload_data'])

    def test_limits_and_original_image_guard_are_preserved(self):
        with TestClient(create_app(self.root / 'store')) as client:
            caps = client.get('/api/multimodal/capabilities').json()
            self.assertEqual(caps['limits']['video_frames'], 100000)
            self.assertEqual(caps['limits']['video_upload_bytes'], 512 * 1048576)
            self.assertEqual(caps['limits']['secret_bytes'], 64 * 1048576)
            for modality, depth in [('audio', 13), ('video', 5), ('image', 2), ('audio', True)]:
                response = client.post('/api/multimodal/capacity', json={'carrier': {'file_id': '0' * 32, 'modality': modality, 'bit_depth': depth}, 'text': 'x'})
                self.assertEqual(response.status_code, 422)
            response = client.post('/api/uploads', files={'file': ('too-large.bin', b'0' * (17 * 1048576))})
            self.assertEqual(response.status_code, 413)
            with patch.object(limits, 'SECRET_BYTES', 1024):
                response = client.post('/api/multimodal/uploads', files={'file': ('too-large.bin', b'x' * 1025)}, data={'modality': 'file'})
                self.assertEqual(response.status_code, 413)
            with self.assertRaises(ValueError):
                mm.embed_text('cover', b'x' * (limits.TEXT_OUTPUT // 24))


if __name__ == '__main__':
    unittest.main()
