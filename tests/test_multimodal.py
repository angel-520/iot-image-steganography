"""Real codec/API round trips, corruption and legacy compatibility.

Run: python -m unittest discover -s tests -v
"""
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import tempfile
import time
import unittest

import numpy as np
from PIL import Image
from fastapi.testclient import TestClient

import istg
from backend.main import create_app
from istg import multimodal as mm
from istg.istg_pack import pack_binary


class CarrierAlgorithms(unittest.TestCase):
    def test_audio_stereo_and_pcm_extremes(self):
        packet = b"\x00\xff" * 100
        rng = np.random.default_rng(10)
        for samples in (rng.integers(-32768, 32768, (10001, 2), dtype=np.int16),
                        np.full((10001, 2), 32767, dtype=np.int16),
                        np.full((10001, 1), -32768, dtype=np.int16),
                        np.tile(np.array([[32767], [-32768]], dtype=np.int16), (5000, 1))):
            for algorithm in ("audio_lsb", "audio_dwt"):
                with self.subTest(algorithm=algorithm, shape=samples.shape, first=int(samples[0, 0])):
                    stego = mm.embed_audio(samples, packet, algorithm)
                    self.assertEqual(mm.extract_bits(mm.audio_slots(stego, algorithm)), packet)
                    self.assertLessEqual(np.abs(samples.astype(int) - stego.astype(int)).max(), 2)

    def test_integrity_capacity_and_forged_header(self):
        slots = mm.embed_bits(np.zeros(4096, dtype=np.uint8), b"test")
        slots[mm.HEADER.size * 8] ^= 1
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            mm.extract_bits(slots)
        with self.assertRaisesRegex(ValueError, "容量不足"):
            mm.embed_bits(np.zeros(10, dtype=np.uint8), b"test")
        with self.assertRaises(ValueError):
            mm.extract_bits(np.zeros(4096, dtype=np.uint8))

    def test_file_packet_aead(self):
        packet = pack_binary(bytes(range(256)), "original.dat")
        for cipher in ("AES-256-GCM", "ChaCha20-Poly1305"):
            encrypted = istg.encrypt_istg(packet, "test-password", cipher)
            self.assertEqual(istg.decrypt_istg(encrypted, "test-password"), packet)
            with self.assertRaises(ValueError):
                istg.decrypt_istg(encrypted, "wrong-password")


class MultimodalAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        rng = np.random.default_rng(42)
        cls.samples = rng.integers(-12000, 12000, (18000, 2), dtype=np.int16)
        cls.wav = mm.audio_bytes(cls.samples, 16000)
        image = BytesIO()
        Image.fromarray(rng.integers(0, 256, (128, 128, 3), dtype=np.uint8)).save(image, format="PNG")
        cls.png = image.getvalue()
        cls.frames = rng.integers(0, 256, (12, 48, 64, 3), dtype=np.uint8)
        video = cls.root / "input.mp4"
        mm.write_video(video, cls.frames, 6, preview=True)
        cls.mp4 = video.read_bytes()
        # Legitimate pre-existing invisible characters must remain visible-text equivalent.
        cls.cover_text = "多模态平台文本载体 / Hello world\n保留可见字符。".encode("utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.client = TestClient(create_app(self.root / "store"))
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)

    def upload(self, data, name, modality="file"):
        response = self.client.post("/api/multimodal/uploads", files={"file": (name, data)}, data={"modality": modality})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def carrier(self, modality, algorithm=None):
        data, name = {"image": (self.png, "cover.png"), "audio": (self.wav, "cover.wav"),
                      "video": (self.mp4, "cover.mp4"), "text": (self.cover_text, "cover.txt")}[modality]
        result = {"file_id": self.upload(data, name, modality)["id"], "modality": modality}
        if algorithm:
            result["algorithm"] = algorithm
        return result

    def task(self, endpoint, payload, *, success=True):
        response = self.client.post(endpoint, json=payload)
        self.assertEqual(response.status_code, 202, response.text)
        task = response.json()
        deadline = time.monotonic() + 45
        while task["status"] in ("running", "queued") and time.monotonic() < deadline:
            time.sleep(.02)
            task = self.client.get("/api/tasks/" + task["id"]).json()
        if success:
            self.assertEqual(task["status"], "completed", task)
            return task["result"]
        self.assertEqual(task["status"], "failed", task)
        return task

    def test_all_carriers_text_and_arbitrary_file_roundtrip(self):
        payloads = [("text", "安全实验中文🔐"), ("image", self.png[:500]), ("audio", self.wav[:500]), ("file", bytes(range(256)))]
        for modality, algorithm in (("image", None), ("audio", "audio_lsb"), ("audio", "audio_dwt"), ("video", None), ("text", None)):
            carrier = self.carrier(modality, algorithm)
            for kind, content in payloads:
                with self.subTest(carrier=modality, algorithm=algorithm, kind=kind):
                    secret = {"text": content} if kind == "text" else {"secret_id": self.upload(content, "original." + kind)["id"]}
                    body = {"carrier": carrier, **secret, "encryption": "AES-256-GCM", "password": "roundtrip", "confirm_password": "roundtrip"}
                    capacity = self.client.post("/api/multimodal/capacity", json=body)
                    self.assertEqual(capacity.status_code, 200, capacity.text)
                    self.assertTrue(capacity.json()["fits"])
                    embedded = self.task("/api/multimodal/embed", body)
                    extracted = self.task("/api/multimodal/extract", {"carrier": embedded["carrier"], "password": "roundtrip"})
                    actual = self.client.get(extracted["file"]["download_url"]).content
                    expected = content.encode("utf-8") if isinstance(content, str) else content
                    self.assertEqual(actual, expected)
                    self.assertEqual(extracted["sha256"], sha256(expected).hexdigest())
                    self.assertTrue(extracted["authenticated"])
                    if modality == "video":
                        self.assertTrue(embedded["preview"])
                        self.assertEqual(embedded["analysis"]["frame_count"], 12)
                        self.assertTrue(embedded["thumbnails"])

    def test_exact_crossmodal_complete_media_files(self):
        for modality, data, name in (("image", self.wav[:1600], "audio.wav"), ("audio", self.png, "image.png"), ("video", self.wav, "payload.wav")):
            # Use a sufficiently capacious audio cover for a full PNG file.
            carrier = self.carrier(modality)
            if modality == "audio":
                larger = np.tile(self.samples, (12, 1))
                carrier["file_id"] = self.upload(mm.audio_bytes(larger, 16000), "large.wav", "audio")["id"]
            if modality == "image":
                # A complete tiny WAV, including its valid RIFF header.
                data = mm.audio_bytes(self.samples[:200], 16000)
            if modality == "video":
                data = bytes(range(256)) * 10
                name = "archive.bin"
            secret = self.upload(data, name)
            embedded = self.task("/api/multimodal/embed", {"carrier": carrier, "secret_id": secret["id"]})
            extracted = self.task("/api/multimodal/extract", {"carrier": embedded["carrier"]})
            self.assertEqual(self.client.get(extracted["file"]["download_url"]).content, data)
            self.assertEqual(extracted["file"]["name"], name)
            if modality == "image":
                legacy = self.task("/api/extract", {"image_id": embedded["stego"]["id"], "mode": "manual", "algorithm": "sequential_lsb", "params": {}})
                self.assertEqual(legacy["data_type"], "file")
                self.assertEqual(self.client.get(legacy["file"]["download_url"]).content, data)

    def test_video_lossless_decode(self):
        path = self.root / "lossless.mkv"
        mm.write_video(path, self.frames, 6)
        decoded, fps = mm.read_video(path)
        self.assertTrue(np.array_equal(decoded, self.frames))
        self.assertEqual(fps, 6)

    def test_split_reordered_combine_and_duplicate_rejected(self):
        body = {"carriers": [self.carrier(m) for m in ("image", "audio", "video")], "text": "协同秘密🔐", "encryption": "AES-256-GCM", "password": "shares", "confirm_password": "shares"}
        split = self.task("/api/multimodal/split", body)
        carriers = [result["carrier"] for result in split["results"]]
        restored = self.task("/api/multimodal/combine", {"carriers": carriers[::-1], "password": "shares"})
        self.assertEqual(restored["text"], body["text"])
        self.task("/api/multimodal/combine", {"carriers": carriers[:2], "password": "shares"}, success=False)
        self.task("/api/multimodal/combine", {"carriers": [carriers[0], carriers[0], carriers[2]], "password": "shares"}, success=False)
        self.task("/api/multimodal/extract", {"carrier": carriers[0], "password": "shares"}, success=False)

    def test_real_attacks(self):
        for modality, attacks in (("audio", ("noise", "resample", "compression")), ("video", ("compression", "crop")), ("text", ("strip_zero_width",))):
            embedded = self.task("/api/multimodal/embed", {"carrier": self.carrier(modality), "text": "attack test 123"})
            for attack in attacks:
                with self.subTest(modality=modality, attack=attack):
                    result = self.task("/api/multimodal/attack", {"carrier": embedded["carrier"], "attack": attack})
                    self.assertGreaterEqual(result["bit_recovery_rate"], 0)
                    self.assertLessEqual(result["bit_recovery_rate"], 1)
                    self.assertFalse(result["exact_match"])
                    self.assertFalse(result["payload_recoverable"])
                    self.assertEqual(self.client.get(result["file"]["download_url"]).status_code, 200)

    def test_invalid_media_password_and_schema(self):
        for modality, filename in (("audio", "bad.wav"), ("video", "bad.mp4"), ("image", "bad.png"), ("text", "bad.txt")):
            response = self.client.post("/api/multimodal/uploads", files={"file": (filename, b"\xff\xffinvalid")}, data={"modality": modality})
            self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.client.get("/api/files/not-a-valid-id").status_code, 404)
        self.assertEqual(self.client.get("/api/files/%2e%2e%2fREADME.md").status_code, 404)
        response = self.client.post("/api/multimodal/embed", json={"carrier": self.carrier("audio"), "text": "secret", "secret_id": "a" * 32})
        self.assertEqual(response.status_code, 422)
        self.assertNotIn("secret", response.text)
        embedded = self.task("/api/multimodal/embed", {"carrier": self.carrier("audio"), "text": "private", "encryption": "AES-256-GCM", "password": "right", "confirm_password": "right"})
        self.task("/api/multimodal/extract", {"carrier": embedded["carrier"], "password": "wrong"}, success=False)

    def test_legacy_image_routes_and_encryption(self):
        upload = self.client.post("/api/uploads", files={"file": ("cover.png", self.png)}, data={"role": "cover"})
        self.assertEqual(upload.status_code, 200)
        body = {"cover_id": upload.json()["id"], "data_type": "text", "text": "原图像功能兼容", "algorithm": "sequential_lsb", "params": {}, "encryption": "AES-256-GCM", "password": "legacy", "confirm_password": "legacy"}
        embedded = self.task("/api/embed", body)
        result = self.task("/api/extract", {"image_id": embedded["stego"]["id"], "algorithm": "sequential_lsb", "mode": "manual", "params": {}, "password": "legacy"})
        self.assertEqual(self.client.get(result["file"]["download_url"]).content, body["text"].encode())


if __name__ == "__main__":
    unittest.main()
