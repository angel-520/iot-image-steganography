"""Independent PEHS round trips, hostile files, ISTG/API integration and limits."""
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import json
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import cbor2
import numpy as np
from PIL import Image, ImageOps
from skimage import data as samples
from fastapi.testclient import TestClient

import istg
from istg import rdh
from istg.image_restoration import PNG_SIGNATURE, _chunk, _read_container
from backend.main import create_app, request_body_limit


def encode(pixels, format="PNG", **options):
    target = BytesIO()
    Image.fromarray(pixels).save(target, format=format, **options)
    return target.getvalue()


def rgb(data):
    with Image.open(BytesIO(data)) as image:
        return np.array(ImageOps.exif_transpose(image).convert("RGB"), np.uint8)


def chunks(data):
    offset, parts = 8, []
    while offset < len(data):
        length = struct.unpack_from('>I', data, offset)[0]
        parts.append((data[offset + 4:offset + 8], data[offset + 8:offset + 8 + length]))
        offset += length + 12
    return parts


def png(parts):
    return PNG_SIGNATURE + b''.join(_chunk(kind, content) for kind, content in parts)


def change_metadata(data, transform):
    parts = []
    for kind, content in chunks(data):
        if kind == rdh.RDH_CHUNK:
            meta = cbor2.loads(content[4:]); transform(meta)
            content = b'RDH1' + cbor2.dumps(meta, canonical=True)
        parts.append((kind, content))
    return png(parts)


class RDHAlgorithm(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pixels = samples.astronaut()
        cls.source = encode(cls.pixels)
        cls.packet = istg.pack_istg('text', 'RDH 中文 English 123 !@#$%^&*()\n\t🙂')
        cls.embedded = rdh.embed_image(cls.source, cls.packet)

    def round_trip(self, source, packet):
        original = rgb(source)
        result = rdh.embed_image(source, packet)
        extracted = rdh.extract_image(result['stego'])
        self.assertEqual(extracted['packet'], packet)
        np.testing.assert_array_equal(rgb(extracted['restored']), original)
        self.assertEqual(sha256(original.tobytes()).hexdigest(), extracted['metrics']['restored_rgb_sha256'])
        self.assertEqual(extracted['metrics']['original_rgb_sha256'], extracted['metrics']['restored_rgb_sha256'])
        self.assertTrue(extracted['metrics']['packet_verified'])
        self.assertTrue(extracted['metrics']['rgb_verified'])
        self.assertEqual(result['metrics']['auxiliary_bytes'], extracted['metrics']['auxiliary_bytes'])
        with Image.open(BytesIO(result['stego'])) as im:
            im.load(); self.assertEqual(im.format, 'PNG'); self.assertEqual(im.mode, 'RGB')
        self.assertLessEqual(int(np.abs(rgb(result['stego']).astype(np.int16) - original).max()), 1)
        return result

    def test_required_image_distributions(self):
        rng = np.random.default_rng(6108)
        gray = np.tile(np.arange(256, dtype=np.uint8), (256, 1))
        flat = np.full((256, 384, 3), 103, np.uint8); flat[50:130, 40:150] = rng.integers(0, 256, (80, 110, 3), dtype=np.uint8)
        cases = {
            'random_rgb': rng.integers(0, 256, (512, 768, 3), dtype=np.uint8),
            'natural_photo': self.pixels,
            'monochrome': np.full((128, 192, 3), 91, np.uint8),
            'grayscale_rgb': np.repeat(gray[:, :, None], 3, axis=2),
            'flat_regions': flat,
            'zeros': np.zeros((128, 129, 3), np.uint8),
            '255s': np.full((128, 129, 3), 255, np.uint8),
            'boundary_mixture': rng.choice(np.array([0, 1, 254, 255], np.uint8), (256, 384, 3)),
            'all_histogram_bins': np.repeat(gray[:, :, None], 3, axis=2),
        }
        for name, pixels in cases.items():
            with self.subTest(name=name):
                self.round_trip(encode(pixels), self.packet)

    def test_sizes_aspect_ratios_and_explicit_rejection_for_small_capacity(self):
        for height, width in [(1, 1), (2, 128), (3, 5), (7, 11), (24, 512), (512, 24), (129, 257)]:
            with self.subTest(shape=(height, width)):
                source = encode(np.full((height, width, 3), 127, np.uint8))
                capacity = rdh.capacity(source, len(self.packet), len(self.packet))
                if capacity['fits']:
                    self.round_trip(source, self.packet)
                else:
                    with self.assertRaisesRegex(rdh.RDHError, '容量不足'):
                        rdh.embed_image(source, self.packet)

    def test_actual_capacity_boundary_across_all_rgb_channels(self):
        source = encode(np.zeros((64, 65, 3), np.uint8))
        available = rdh.capacity(source)['packet_capacity_bytes']
        packet = np.random.default_rng(3).bytes(available)
        result = self.round_trip(source, packet)
        self.assertEqual(result['metrics']['packet_bytes'], available)
        with self.assertRaises(rdh.RDHError): rdh.embed_image(source, packet + b'x')

    def test_utf8_text_lengths_and_random_binary_files(self):
        rng = np.random.default_rng(293)
        for text in ('x', '中文 English\n\t\x00🙂', '不同长度的秘密文本。' * 100):
            with self.subTest(length=len(text)):
                packet = istg.pack_istg('text', text)
                result = self.round_trip(self.source, packet)
                inner = istg.parse_istg(rdh.extract_image(result['stego'])['packet'])['payload_data']
                self.assertEqual(inner['data'], text.encode('utf-8'))
        for size in (0, 1, 257, 4096):
            with self.subTest(binary_size=size):
                raw = rng.bytes(size)
                packet = istg.pack_binary(raw, 'binary.dat')
                result = self.round_trip(self.source, packet)
                self.assertEqual(istg.parse_istg(rdh.extract_image(result['stego'])['packet'])['payload_data']['data'], raw)

    def test_both_existing_authenticated_ciphers(self):
        for cipher in ('AES-256-GCM', 'ChaCha20-Poly1305'):
            with self.subTest(cipher=cipher):
                packet = istg.encrypt_istg(self.packet, 'RDH-test-password', cipher)
                result = self.round_trip(self.source, packet)
                self.assertEqual(istg.decrypt_istg(rdh.extract_image(result['stego'])['packet'], 'RDH-test-password'), self.packet)

    def test_exif_alpha_palette_and_supported_source_formats(self):
        exif = Image.Exif(); exif[274] = 6
        self.round_trip(encode(self.pixels, 'JPEG', exif=exif), self.packet)
        self.round_trip(encode(np.concatenate([self.pixels, np.full((512, 512, 1), 73, np.uint8)], axis=2)), self.packet)
        image = Image.fromarray(self.pixels).quantize(colors=128)
        out = BytesIO(); image.save(out, format='PNG'); self.round_trip(out.getvalue(), self.packet)
        for format in ('BMP', 'WEBP', 'TIFF'):
            with self.subTest(format=format): self.round_trip(encode(self.pixels, format), self.packet)

    def test_standalone_recovery_in_a_fresh_process_with_only_the_png(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'only-rdh.png'; path.write_bytes(self.embedded['stego'])
            code = "from pathlib import Path; from istg.rdh import extract_image; import sys,json; r=extract_image(Path(sys.argv[1]).read_bytes()); print(json.dumps({'packet':r['packet'].hex(),'hash':r['metrics']['restored_rgb_sha256']}))"
            child = subprocess.run([sys.executable, '-B', '-c', code, str(path)], capture_output=True, text=True, timeout=30, check=True)
            result = json.loads(child.stdout)
            self.assertEqual(bytes.fromhex(result['packet']), self.packet)
            self.assertEqual(result['hash'], sha256(self.pixels.tobytes()).hexdigest())

    def test_auxiliary_contains_only_parameters_boundary_maps_and_digests(self):
        _, auxiliary, _, _ = _read_container(self.embedded['stego'], layer_chunk=rdh.RDH_CHUNK)
        metadata = cbor2.loads(auxiliary[4:])
        self.assertNotIn(self.packet, auxiliary)
        self.assertNotIn(self.pixels.tobytes(), auxiliary)
        self.assertEqual(set(metadata), rdh._ROOT_FIELDS)
        self.assertTrue(all(set(p) == rdh._PLAN_FIELDS for p in metadata['plans']))
        self.assertEqual(self.embedded['metrics']['auxiliary_in_pixel_bits'], 0)
        self.assertEqual(self.embedded['metrics']['secret_capacity_bytes'], self.embedded['metrics']['packet_capacity_bytes'])

    def test_missing_duplicate_damaged_and_truncated_pngs_are_rejected(self):
        original = self.embedded['stego']; parts = chunks(original)
        aux = next(part for part in parts if part[0] == rdh.RDH_CHUNK)
        damaged = bytearray(original); damaged[45] ^= 1
        bad = [bytes(damaged), original[:-1], original + b'x', b'bad',
               png([p for p in parts if p[0] != rdh.RDH_CHUNK]),
               png(parts[:-1] + [aux, parts[-1]]), png(parts[:1] + [aux] + parts[1:])]
        for data in bad:
            with self.subTest(size=len(data)), self.assertRaises(ValueError): rdh.extract_image(data)

    def test_valid_png_pixel_edit_is_rejected_even_with_correct_png_crc(self):
        changed = rgb(self.embedded['stego']); changed[-1, -1, 0] ^= 1
        aux = next(part for part in chunks(self.embedded['stego']) if part[0] == rdh.RDH_CHUNK)
        parts = chunks(encode(changed))
        with self.assertRaisesRegex(rdh.RDHError, '像素已被修改'):
            rdh.extract_image(png(parts[:-1] + [aux, parts[-1]]))

    def test_malformed_parameters_and_bounded_location_maps_are_rejected(self):
        transforms = [
            lambda m: m.update(version=True), lambda m: m.update(width=10**12),
            lambda m: m.update(payload_bytes=8 * 1024**2 + 1), lambda m: m.update(packet_sha256='0'*64),
            lambda m: m.update(original_rgb_sha256='0'*64), lambda m: m.update(extra='not-allowed'),
            lambda m: m['plans'][0].update(used_bits=True), lambda m: m['plans'][0].update(channel=3),
            lambda m: m['plans'][0].update(zero=m['plans'][0]['peak']),
            lambda m: m['plans'][0].update(skip_map=b'not-zlib'),
            lambda m: m['plans'][0].update(skip_map=b'x' * 50000),
        ]
        for transform in transforms:
            with self.subTest(transform=transform), self.assertRaises(ValueError):
                rdh.extract_image(change_metadata(self.embedded['stego'], transform))

    def test_embedding_does_not_return_success_if_reread_verification_is_wrong(self):
        with patch.object(rdh, 'extract_image', return_value={'packet': b'wrong', 'restored': self.source}):
            with self.assertRaisesRegex(rdh.RDHError, '验证失败'):
                rdh.embed_image(self.source, self.packet)

    def test_limits_and_unsupported_or_animated_inputs(self):
        with patch.object(rdh, 'MAX_SOURCE_BYTES', 10), self.assertRaises(ValueError): rdh.capacity(self.source)
        with patch.object(rdh, 'MAX_PACKET_BYTES', 2), self.assertRaises(ValueError): rdh.embed_image(self.source, self.packet)
        with self.assertRaises(ValueError): rdh.embed_image(b'not-image', self.packet)
        with self.assertRaises(ValueError): rdh.embed_image(self.source, b'')
        out = BytesIO(); Image.fromarray(self.pixels).save(out, format='GIF')
        with self.assertRaises(ValueError): rdh.capacity(out.getvalue())


class RDHAPI(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.client = TestClient(create_app(Path(self.temp.name))); self.client.__enter__()
        self.pixels = np.full((192, 256, 3), 117, np.uint8)
        self.source = encode(self.pixels)
        response = self.client.post('/api/uploads', files={'file': ('cover.png', self.source, 'image/png')})
        self.image = response.json()

    def tearDown(self):
        self.client.__exit__(None, None, None); self.temp.cleanup()

    def wait(self, response, status='completed'):
        self.assertEqual(response.status_code, 202, response.text)
        task = response.json(); deadline = time.monotonic() + 30
        while task['status'] in {'queued', 'running'} and time.monotonic() < deadline:
            time.sleep(.01); task = self.client.get('/api/tasks/' + task['id']).json()
        self.assertEqual(task['status'], status, task)
        return task

    def embed(self, **secret):
        return self.wait(self.client.post('/api/rdh/embed', json={'image_id': self.image['id'], **secret}))

    def test_text_capacity_and_new_session_without_any_source_or_task_records(self):
        text = '秘密中文 English\n\t!@#🙂'
        body = {'image_id': self.image['id'], 'text': text}
        capacity = self.wait(self.client.post('/api/rdh/capacity', json=body))['result']
        task = self.embed(text=text); result = task['result']
        self.assertEqual(result['metrics']['packet_bytes'], capacity['packet_bytes'])
        self.assertEqual(result['metrics']['secret_capacity_bytes'], capacity['secret_capacity_bytes'])
        self.assertEqual(result['metrics']['auxiliary_bytes'], capacity['auxiliary_bytes'])
        self.assertTrue(result['metrics']['secret_verified'])
        data = self.client.get(result['stego']['download_url']).content
        self.assertEqual(len(data), result['metrics']['stego_bytes'])
        for identifier in list(self.client.app.state.files.files): self.client.app.state.files.remove(identifier)
        self.client.app.state.tasks.tasks.clear()
        with TestClient(create_app(Path(self.temp.name) / 'fresh-session')) as fresh:
            response = fresh.post('/api/rdh/extract', files={'file': ('rdh.png', data, 'image/png')})
            task2 = response.json()
            for _ in range(1000):
                task2 = fresh.get('/api/tasks/' + task2['id']).json()
                if task2['status'] not in {'queued', 'running'}: break
                time.sleep(.01)
            self.assertEqual(task2['status'], 'completed', task2)
            extracted = task2['result']
            self.assertEqual(extracted['text'], text)
            self.assertEqual(fresh.get(extracted['secret']['url']).content, text.encode('utf-8'))
            np.testing.assert_array_equal(rgb(fresh.get(extracted['restored']['url']).content), self.pixels)
            self.assertEqual(extracted['metrics']['original_rgb_sha256'], extracted['metrics']['restored_rgb_sha256'])

    def test_encrypted_random_file_correct_and_wrong_password_and_no_partial_artifacts(self):
        raw = np.random.default_rng(42).bytes(1024)
        secret = self.client.post('/api/rdh/uploads', files={'file': ('data.bin', raw)}).json()
        model = {'secret_id': secret['id'], 'encryption': 'AES-256-GCM', 'password': 'test-rdh-password', 'confirm_password': 'test-rdh-password'}
        capacity = self.wait(self.client.post('/api/rdh/capacity', json={'image_id': self.image['id'], **model}))['result']
        task = self.embed(**model); result = task['result']
        self.assertEqual(result['metrics']['packet_bytes'], capacity['packet_bytes'])
        self.assertNotIn('test-rdh-password', json.dumps(task))
        self.assertNotIn('test-rdh-password', self.client.get(task['export_url']).text)
        data = self.client.get(result['stego']['url']).content
        before = set(self.client.app.state.files.files)
        for password, code in (('', 'password_required'), ('wrong-password', 'authentication_failed')):
            failed = self.wait(self.client.post('/api/rdh/extract', data={'password': password}, files={'file': ('rdh.png', data)}), 'failed')
            self.assertEqual(failed['error']['code'], code)
            self.assertIsNone(failed['result']); self.assertEqual(set(self.client.app.state.files.files), before)
        extracted = self.wait(self.client.post('/api/rdh/extract', data={'password': 'test-rdh-password'}, files={'file': ('rdh.png', data)}))['result']
        self.assertEqual(self.client.get(extracted['secret']['url']).content, raw)
        self.assertEqual(extracted['metrics']['secret_sha256'], sha256(raw).hexdigest())
        np.testing.assert_array_equal(rgb(self.client.get(extracted['restored']['url']).content), self.pixels)

    def test_empty_binary_file_and_upload_limits(self):
        secret = self.client.post('/api/rdh/uploads', files={'file': ('empty.bin', b'')}).json()
        task = self.embed(secret_id=secret['id'])
        data = self.client.get(task['result']['stego']['url']).content
        extracted = self.wait(self.client.post('/api/rdh/extract', files={'file': ('rdh.png', data)}))['result']
        self.assertEqual(self.client.get(extracted['secret']['url']).content, b'')
        with patch('backend.restoration.MAX_SECRET_BYTES', 2):
            self.assertEqual(self.client.post('/api/rdh/uploads', files={'file': ('large.bin', b'123')}).status_code, 413)
        with patch('backend.restoration.MAX_CONTAINER_BYTES', 2):
            self.assertEqual(self.client.post('/api/rdh/extract', files={'file': ('large.png', b'123')}).status_code, 413)
        self.assertEqual(request_body_limit('/api/rdh/extract'), 97 * 1024**2)

    def test_bad_requests_capacity_and_rollback_of_failed_output_publication(self):
        for payload in ({}, {'text': ''}, {'text': 'x', 'secret_id': self.image['id']}, {'text': 'x', 'extra': 1}):
            self.assertEqual(self.client.post('/api/rdh/embed', json={'image_id': self.image['id'], **payload}).status_code, 422)
        failed = self.wait(self.client.post('/api/rdh/embed', json={'image_id': self.image['id'], 'text': 'x' * 50000}), 'failed')
        self.assertEqual(failed['error']['code'], 'capacity_exceeded')
        self.assertEqual(self.client.post('/api/rdh/extract', files={'file': ('bad.jpg', b'JPEG')}).status_code, 422)
        store = self.client.app.state.files; before = set(store.files); original = store.add; count = [0]
        def fail_second(*args, **kwargs):
            count[0] += 1
            if count[0] == 2: raise ValueError('Simulated publication failure')
            return original(*args, **kwargs)
        with patch.object(store, 'add', side_effect=fail_second):
            self.wait(self.client.post('/api/rdh/embed', json={'image_id': self.image['id'], 'text': 'rollback'}), 'failed')
        self.assertEqual(set(store.files), before)


if __name__ == '__main__': unittest.main()
