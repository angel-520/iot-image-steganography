"""Lossless RGB recovery, corrupt-container rejection and API round trips."""
from contextlib import asynccontextmanager
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import json
import math
import struct
import tempfile
import time
import unittest
from unittest.mock import patch
import zlib

import numpy as np
from PIL import Image, ImageOps
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.testclient import TestClient

from backend.restoration import router
from backend.runtime import APIError, FileStore, TaskManager
from istg import image_restoration as restoration


def encode(pixels, format="PNG", **options):
    output = BytesIO()
    Image.fromarray(pixels).save(output, format=format, **options)
    return output.getvalue()


def rgb(data):
    with Image.open(BytesIO(data)) as image:
        return np.array(ImageOps.exif_transpose(image).convert("RGB"), dtype=np.uint8)


def chunks(data):
    offset = 8
    result = []
    while offset < len(data):
        size = struct.unpack_from(">I", data, offset)[0]
        result.append((data[offset + 4:offset + 8], data[offset + 8:offset + 8 + size]))
        offset += 12 + size
    return result


def png(parts):
    return restoration.PNG_SIGNATURE + b"".join(restoration._chunk(kind, content) for kind, content in parts)


def replace_layer(carrier, transform):
    return png([(kind, transform(content) if kind == restoration.LAYER_CHUNK else content)
                for kind, content in chunks(carrier)])


def replace_metadata(carrier, transform, compressed=None):
    def change(layer):
        metadata, original = restoration._parse_layer(layer)
        transform(metadata)
        header = json.dumps(metadata, separators=(",", ":")).encode()
        return restoration._LAYER_PREFIX.pack(restoration.LAYER_MAGIC, len(header)) + header + (
            original if compressed is None else compressed)
    return replace_layer(carrier, change)


class ImageRestoration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pixels = np.random.default_rng(3107).integers(0, 256, (80, 96, 3), dtype=np.uint8)
        cls.source = encode(cls.pixels)
        cls.prepared = restoration.prepare_image(cls.source, 45)
        cls.carrier = cls.prepared["carrier"]

    def assert_pixels(self, first, second):
        np.testing.assert_array_equal(rgb(first), rgb(second))

    def test_actual_lossy_preview_and_exact_self_contained_recovery(self):
        result = restoration.restore_image(self.carrier)
        self.assert_pixels(result["restored"], self.source)
        self.assertFalse(np.array_equal(rgb(self.prepared["preview"]), self.pixels))
        self.assert_pixels(self.carrier, self.prepared["preview"])
        self.assertEqual(result["metrics"]["original_pixel_sha256"], sha256(self.pixels.tobytes()).hexdigest())
        self.assertEqual(result["metrics"]["original_pixel_sha256"], result["metrics"]["restored_pixel_sha256"])
        self.assertTrue(result["metrics"]["exact_match"])
        self.assertGreater(result["metrics"]["preview_quality"]["mse"], 0)
        self.assertLess(result["metrics"]["preview_quality"]["ssim"], 1)
        self.assertEqual(result["metrics"]["restored_quality"]["mse"], 0)
        self.assertTrue(math.isinf(result["metrics"]["restored_quality"]["psnr"]))

    def test_random_dimensions_small_images_and_jpeg_input(self):
        rng = np.random.default_rng(812)
        for height, width in [(1, 1), (2, 5), (13, 17), (43, 41)]:
            pixels = rng.integers(0, 256, (height, width, 3), dtype=np.uint8)
            for format in ["PNG", "JPEG"]:
                for quality in [1, 70, 95]:
                    with self.subTest(size=(height, width), format=format, quality=quality):
                        source = encode(pixels, format)
                        result = restoration.prepare_image(source, quality)
                        restored = restoration.restore_image(result["carrier"])
                        self.assert_pixels(source, restored["restored"])
                        if min(height, width) < 3:
                            self.assertIsNone(restored["metrics"]["preview_quality"]["ssim"])

    def test_exif_orientation_and_alpha_are_normalized_consistently(self):
        exif = Image.Exif()
        exif[274] = 6
        source = encode(self.pixels, "JPEG", exif=exif)
        result = restoration.prepare_image(source)
        self.assertEqual(rgb(result["restored"]).shape, (96, 80, 3))
        self.assert_pixels(source, result["restored"])
        rgba = np.concatenate([self.pixels, np.full((80, 96, 1), 73, np.uint8)], axis=2)
        source_rgba = encode(rgba)
        self.assert_pixels(source_rgba, restoration.prepare_image(source_rgba)["restored"])

    def test_sizes_are_actual_and_carrier_is_not_claimed_smaller(self):
        result = self.prepared
        metrics = result["metrics"]
        self.assertEqual(metrics["original_file_bytes"], len(self.source))
        self.assertEqual(metrics["preview_jpeg_bytes"], len(result["preview"]))
        self.assertEqual(metrics["carrier_bytes"], len(result["carrier"]))
        self.assertEqual(metrics["restored_png_bytes"], len(result["restored"]))
        layer = next(content for kind, content in chunks(result["carrier"]) if kind == restoration.LAYER_CHUNK)
        self.assertEqual(metrics["recovery_layer_bytes"], len(layer))
        self.assertAlmostEqual(metrics["size_ratio"], len(result["carrier"]) / len(self.source))

    def test_carrier_needs_no_source_file_or_store(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "only-carrier.png"
            path.write_bytes(self.carrier)
            result = restoration.restore_image(path.read_bytes())
            self.assertEqual(rgb(result["restored"]).tobytes(), self.pixels.tobytes())

    def test_missing_duplicate_or_early_layer_is_rejected(self):
        parts = chunks(self.carrier)
        layer = next(part for part in parts if part[0] == restoration.LAYER_CHUNK)
        malformed = [
            png([part for part in parts if part[0] != restoration.LAYER_CHUNK]),
            png(parts[:-1] + [layer, parts[-1]]),
            png(parts[:1] + [layer] + [part for part in parts[1:] if part[0] != restoration.LAYER_CHUNK]),
        ]
        for data in malformed:
            with self.subTest(size=len(data)), self.assertRaises(ValueError):
                restoration.restore_image(data)

    def test_crc_truncation_signature_and_trailing_bytes_are_rejected(self):
        corrupted = bytearray(self.carrier)
        corrupted[45] ^= 1
        for data in [bytes(corrupted), self.carrier[:-1], self.carrier + b"x",
                     b"bad" + self.carrier, self.prepared["preview"], b""]:
            with self.subTest(size=len(data)), self.assertRaises(ValueError):
                restoration.restore_image(data)

    def test_valid_png_pixel_modification_rejected_by_digest(self):
        modified = rgb(self.carrier)
        modified[0, 0, 0] ^= 1
        clean_parts = chunks(encode(modified))
        layer = next(part for part in chunks(self.carrier) if part[0] == restoration.LAYER_CHUNK)
        changed = png(clean_parts[:-1] + [layer, clean_parts[-1]])
        with self.assertRaisesRegex(ValueError, "修改"):
            restoration.restore_image(changed)

    def test_metadata_types_dimensions_hashes_and_version_rejected(self):
        cases = [
            ("version", True), ("version", 2), ("width", True), ("width", 16_000_001),
            ("height", -1), ("width", 95), ("residual_bytes", 1), ("mode", "RGBA"),
            ("jpeg_quality", 0), ("original_file_bytes", 0), ("preview_jpeg_bytes", -1),
            ("original_pixel_sha256", "x" * 64), ("base_pixel_sha256", None),
        ]
        for key, value in cases:
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                restoration.restore_image(replace_metadata(self.carrier, lambda metadata: metadata.update({key: value})))
        with self.assertRaises(ValueError):
            restoration.restore_image(replace_metadata(self.carrier, lambda metadata: metadata.update(extra=1)))

    def test_truncated_concatenated_overlong_and_corrupted_zlib_rejected(self):
        expected = self.pixels.size
        variants = [
            b"not-zlib", zlib.compress(b"x" * (expected + 1)), zlib.compress(b"x" * (expected - 1)),
            zlib.compress(b"x" * expected) + b"tail",
            zlib.compress(b"x" * expected) + zlib.compress(b"extra"),
            zlib.compress(b"x" * expected)[:-1],
            zlib.compress(b"x" * expected),
        ]
        for compressed in variants:
            with self.subTest(length=len(compressed)), self.assertRaises(ValueError):
                restoration.restore_image(replace_metadata(self.carrier, lambda metadata: None, compressed))

    def test_duplicate_json_header_and_huge_header_rejected(self):
        for header in [b'{"width":1,"width":2}', b"[]" , b"{" * 2000]:
            malformed = replace_layer(self.carrier, lambda layer: restoration._LAYER_PREFIX.pack(
                restoration.LAYER_MAGIC, len(header)) + header + zlib.compress(b"abc"))
            with self.assertRaises(ValueError):
                restoration.restore_image(malformed)
        malformed = replace_layer(self.carrier, lambda layer: restoration._LAYER_PREFIX.pack(
            restoration.LAYER_MAGIC, restoration.MAX_METADATA_BYTES + 1) + layer)
        with self.assertRaises(ValueError):
            restoration.restore_image(malformed)

    def test_resource_limits_and_quality_validation(self):
        for value in [True, 0, 96, 70.5, "70"]:
            with self.subTest(quality=value), self.assertRaises(ValueError):
                restoration.prepare_image(self.source, value)
        for key, value, operation in [
            ("MAX_SOURCE_BYTES", 10, lambda: restoration.prepare_image(self.source)),
            ("MAX_PIXELS", 10, lambda: restoration.prepare_image(self.source)),
            ("MAX_CONTAINER_BYTES", 10, lambda: restoration.restore_image(self.carrier)),
            ("MAX_LAYER_BYTES", 10, lambda: restoration.restore_image(self.carrier)),
            ("MAX_CHUNKS", 1, lambda: restoration.restore_image(self.carrier)),
        ]:
            with self.subTest(limit=key), patch.object(restoration, key, value), self.assertRaises(ValueError):
                operation()

    def test_png_critical_structure_and_animation_rejected(self):
        parts = chunks(self.carrier)
        cases = [
            png(parts[:1] + [parts[0]] + parts[1:]),
            png([parts[1], parts[0]] + parts[2:]),
            png(parts[:-1]),
            png(parts[:-1] + [(b"IEND", b"wrong")]),
            png(parts[:1] + [(b"acTL", struct.pack(">II", 1, 0))] + parts[1:]),
            png(parts[:-1] + [(b"ABCD", b"")] + parts[-1:]),
        ]
        for data in cases:
            with self.subTest(size=len(data)), self.assertRaises(ValueError):
                restoration.restore_image(data)


class RestorationAPI(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        @asynccontextmanager
        async def lifespan(app):
            app.state.files = FileStore(Path(self.temp.name))
            app.state.tasks = TaskManager(app.state.files)
            try:
                yield
            finally:
                app.state.tasks.close()
        self.app = FastAPI(lifespan=lifespan)
        self.app.include_router(router)
        @self.app.exception_handler(APIError)
        async def error_handler(request, error):
            return JSONResponse({"error": {"code": error.code, "message": error.message}}, status_code=error.status)
        @self.app.get("/api/tasks/{identifier}")
        def task(identifier: str):
            return self.app.state.tasks.public(identifier)
        @self.app.get("/api/files/{identifier}")
        def file(identifier: str):
            return FileResponse(self.app.state.files.get(identifier).path)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.source = encode(np.random.default_rng(77).integers(0, 256, (31, 29, 3), dtype=np.uint8))
        self.file = self.app.state.files.add(self.source, "source.png", "image/png", image=True)

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temp.cleanup()

    def wait(self, response):
        self.assertEqual(response.status_code, 202, response.text)
        task = response.json()
        deadline = time.monotonic() + 20
        while task["status"] in {"queued", "running"} and time.monotonic() < deadline:
            time.sleep(.01)
            task = self.client.get("/api/tasks/" + task["id"]).json()
        self.assertEqual(task["status"], "completed", task)
        return task["result"]

    def test_prepare_and_upload_only_carrier_restore(self):
        result = self.wait(self.client.post("/api/restoration/prepare", json={
            "image_id": self.file.id, "jpeg_quality": 30}))
        self.assertEqual(set(result), {"source", "preview", "carrier", "restored", "metrics"})
        self.assertTrue(result["metrics"]["exact_match"])
        carrier = self.client.get(result["carrier"]["url"]).content
        self.app.state.files.remove(self.file.id)
        restored = self.wait(self.client.post("/api/restoration/restore", files={"file": ("carrier.png", carrier)}))
        image = self.client.get(restored["restored"]["url"]).content
        np.testing.assert_array_equal(rgb(image), rgb(self.source))
        self.assertEqual(restored["metrics"]["carrier_bytes"], len(carrier))
        self.assertEqual(restored["metrics"]["restored_quality"]["psnr"], "Infinity")

    def test_invalid_inputs_and_size_guard(self):
        for body in [{"image_id": "../bad"}, {"image_id": self.file.id, "jpeg_quality": True},
                     {"image_id": self.file.id, "jpeg_quality": 0},
                     {"image_id": self.file.id, "extra": 1}]:
            self.assertEqual(self.client.post("/api/restoration/prepare", json=body).status_code, 422)
        self.assertEqual(self.client.post("/api/restoration/restore", files={"file": ("bad.png", b"abc")}).status_code, 422)
        with patch("backend.restoration.MAX_CONTAINER_BYTES", 8):
            self.assertEqual(self.client.post("/api/restoration/restore", files={"file": ("big.png", b"x" * 9)}).status_code, 413)
        with patch("backend.restoration.MAX_SOURCE_BYTES", 1):
            self.assertEqual(self.client.post("/api/restoration/prepare", json={"image_id": self.file.id}).status_code, 413)

    def test_failed_publication_rolls_back_partial_artifacts(self):
        before = len(self.app.state.files.files)
        real_add = self.app.state.files.add
        calls = [0]
        def fail_second(*args, **kwargs):
            calls[0] += 1
            if calls[0] == 2:
                raise APIError("storage_full", "Storage full", 429)
            return real_add(*args, **kwargs)
        with patch.object(self.app.state.files, "add", side_effect=fail_second):
            response = self.client.post("/api/restoration/prepare", json={"image_id": self.file.id})
            self.assertEqual(response.status_code, 202)
            task = response.json()
            for _ in range(100):
                task = self.client.get("/api/tasks/" + task["id"]).json()
                if task["status"] not in {"queued", "running"}:
                    break
                time.sleep(.01)
            self.assertEqual(task["status"], "failed")
            self.assertEqual(task["error"]["code"], "storage_full")
        self.assertEqual(len(self.app.state.files.files), before)

    def test_bad_png_fails_task_without_result_artifacts(self):
        before = len(self.app.state.files.files)
        response = self.client.post("/api/restoration/restore", files={
            "file": ("truncated.png", restoration.PNG_SIGNATURE + b"bad")})
        self.assertEqual(response.status_code, 202)
        task = response.json()
        for _ in range(100):
            task = self.client.get("/api/tasks/" + task["id"]).json()
            if task["status"] not in {"queued", "running"}:
                break
            time.sleep(.01)
        self.assertEqual(task["status"], "failed")
        self.assertIsNone(task["result"])
        self.assertEqual(len(self.app.state.files.files), before)


if __name__ == "__main__":
    unittest.main()
