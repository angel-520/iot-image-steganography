"""Copyright identities, signed image binding and every-request authorization."""
from __future__ import annotations

from hashlib import sha256
from io import BytesIO
from pathlib import Path
import json
import tempfile
import time
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from backend.copyright import COOKIE, COOKIE_PATH, CopyrightService, router
from backend.runtime import APIError
from istg.copyright import embed_identifier, extract_packet, image_rgb, png_bytes, verify_identifier
from istg.istg_pack import pack_istg
from istg import istg_stego as stego
from istg.security_analysis.packet import parse_istg


def picture(seed=7, size=(256, 256)):
    pixels = np.random.default_rng(seed).integers(0, 256, (*size, 3), dtype=np.uint8)
    return png_bytes(pixels)


class CopyrightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.service = CopyrightService(self.root)
        self.owner, owner_token = self.service.register("owner", "owner-password")
        self.guest, guest_token = self.service.register("reader", "reader-password")
        self.other, other_token = self.service.register("other", "other-password")
        self.app = FastAPI()
        self.app.state.copyright = self.service
        self.app.include_router(router)
        @self.app.exception_handler(APIError)
        async def error(request, exc):
            return JSONResponse({"error": {"code": exc.code, "message": exc.message}}, status_code=exc.status)
        self.a, self.b, self.c, self.anonymous = [TestClient(self.app) for _ in range(4)]
        for client, token in [(self.a, owner_token), (self.b, guest_token), (self.c, other_token)]:
            client.cookies.set(COOKIE, token, path=COOKIE_PATH)
        self.original = picture()

    def tearDown(self):
        for client in [self.a, self.b, self.c, self.anonymous]:
            client.close()
        self.service.close()
        self.temp.cleanup()

    def create(self, image=None, client=None):
        response = (client or self.a).post(COOKIE_PATH + "/works",
            files={"file": ("original.png", image or self.original, "image/png")},
            data={"title": "Test artwork", "author": "Declared author", "rights": "Permission required"})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["work"]

    def mark(self, work):
        response = self.a.post(f"{COOKIE_PATH}/works/{work['id']}/mark", json={"recipient": "reader"})
        self.assertEqual(response.status_code, 200, response.text)
        mark = response.json()["mark"]
        response = self.a.get(mark["download_url"])
        self.assertEqual(response.status_code, 200, response.text)
        return mark, response.content

    def verify(self, data):
        response = self.anonymous.post(COOKIE_PATH + "/verify", files={"file": ("verify.png", data, "image/png")})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_registration_cookie_hashing_login_and_logout(self):
        response = self.anonymous.post(COOKIE_PATH + "/auth/register",
                                       json={"username": "alice", "password": "my-long-password"})
        self.assertEqual(response.status_code, 201, response.text)
        cookie = response.headers["set-cookie"].lower()
        self.assertIn("httponly", cookie); self.assertIn("samesite=strict", cookie)
        self.assertIn("path=/api/copyright", cookie)
        user = response.json()["user"]
        row = self.service.db.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone()
        self.assertNotEqual(row["password_hash"], b"my-long-password")
        self.assertEqual(len(row["salt"]), 16)
        session = self.service.db.execute("SELECT token_hash FROM sessions WHERE user_id=?", (user["id"],)).fetchone()[0]
        self.assertEqual(len(session), 64)
        self.assertNotEqual(session, self.anonymous.cookies.get(COOKIE))
        self.assertEqual(self.anonymous.get(COOKIE_PATH + "/auth/me").json()["user"]["username"], "alice")
        self.anonymous.post(COOKIE_PATH + "/auth/logout")
        self.assertIsNone(self.anonymous.get(COOKIE_PATH + "/auth/me").json()["user"])
        self.assertEqual(self.anonymous.get(COOKIE_PATH + "/works").status_code, 401)
        self.assertEqual(self.anonymous.post(COOKIE_PATH + "/auth/login",
                         json={"username": "alice", "password": "wrong-password"}).status_code, 401)
        self.assertEqual(self.anonymous.post(COOKIE_PATH + "/auth/login",
                         json={"username": "ALICE", "password": "my-long-password"}).status_code, 200)

    def test_secure_cookie_and_rejected_invalid_credentials(self):
        https = TestClient(self.app, base_url="https://testserver")
        try:
            response = https.post(COOKIE_PATH + "/auth/login", json={"username": "owner", "password": "owner-password"})
            self.assertIn("secure", response.headers["set-cookie"].lower())
        finally:
            https.close()
        for username, password in [("short", "123456789"), ("bad username", "valid-password"), ("spaces", " " * 12)]:
            self.assertEqual(self.anonymous.post(COOKIE_PATH + "/auth/register",
                json={"username": username, "password": password}).status_code, 422)
        for _ in range(5):
            self.assertEqual(self.anonymous.post(COOKIE_PATH + "/auth/login",
                json={"username": "missing", "password": "bad-password"}).status_code, 401)
        self.assertEqual(self.anonymous.post(COOKIE_PATH + "/auth/login",
            json={"username": "missing", "password": "bad-password"}).status_code, 429)

    def test_sessions_expire_and_do_not_accept_forged_owner_fields(self):
        self.assertEqual(self.anonymous.post(COOKIE_PATH + "/auth/login",
            json={"username": "owner", "password": "owner-password", "owner_id": self.owner["id"]}).status_code, 422)
        with self.service.lock, self.service.db:
            self.service.db.execute("UPDATE sessions SET expires_at=0 WHERE user_id=?", (self.guest["id"],))
        self.assertEqual(self.b.get(COOKIE_PATH + "/works").status_code, 401)
        self.c.cookies.set(COOKIE, "x" * 43, path=COOKIE_PATH)
        self.assertEqual(self.c.get(COOKIE_PATH + "/works").status_code, 401)

    def test_work_storage_is_private_and_download_preserves_original(self):
        work = self.create()
        self.assertTrue(work["can_manage"])
        self.assertEqual(work["original_sha256"], sha256(self.original).hexdigest())
        self.assertTrue(work["content_url"].startswith(COOKIE_PATH))
        self.assertNotIn("/api/files/", json.dumps(work))
        ciphertext = (self.root / (work["id"] + ".source.bin")).read_bytes()
        self.assertFalse(ciphertext.startswith(b"\x89PNG"))
        self.assertNotEqual(ciphertext, self.original)
        original = self.a.get(work["content_url"] + "?download=true")
        self.assertEqual(original.content, self.original)
        self.assertEqual(original.headers["cache-control"], "no-store")
        self.assertEqual(self.anonymous.get(work["content_url"]).status_code, 401)
        self.assertEqual(self.b.get(work["content_url"]).status_code, 403)
        self.assertEqual(self.b.get(COOKIE_PATH + "/works").json()["works"], [])
        self.assertEqual(self.anonymous.get("/content.key").status_code, 404)

    def test_authorization_is_checked_for_view_download_mark_grant_and_audit(self):
        work = self.create(); wid = work["id"]
        grant = self.a.post(f"{COOKIE_PATH}/works/{wid}/grants",
                           json={"username": "reader", "expires_at": time.time() + 3600, "can_download": False})
        self.assertEqual(grant.status_code, 200, grant.text)
        listed = self.b.get(COOKIE_PATH + "/works").json()["works"][0]
        self.assertEqual(listed["access_status"], "active"); self.assertFalse(listed["can_manage"])
        self.assertEqual(self.b.get(work["content_url"]).status_code, 200)
        self.assertEqual(self.b.get(work["content_url"] + "?download=true").status_code, 403)
        self.assertEqual(self.c.get(work["content_url"]).status_code, 403)
        for path in ["marks", "grants", "audit"]:
            self.assertEqual(self.b.get(f"{COOKIE_PATH}/works/{wid}/{path}").status_code, 403)
        self.assertEqual(self.b.post(f"{COOKIE_PATH}/works/{wid}/mark", json={}).status_code, 403)
        self.assertEqual(self.b.post(f"{COOKIE_PATH}/works/{wid}/grants",
                                    json={"username": "other", "can_download": True}).status_code, 403)
        self.a.post(f"{COOKIE_PATH}/works/{wid}/grants",
                    json={"username": "reader", "can_download": True})
        self.assertEqual(self.b.get(work["content_url"] + "?download=true").content, self.original)
        actions = [row["action"] for row in self.a.get(f"{COOKIE_PATH}/works/{wid}/audit").json()["events"]]
        self.assertIn("view", actions); self.assertIn("download", actions); self.assertIn("grant", actions)

    def test_revocation_and_expiry_deny_new_access_with_existing_session(self):
        work = self.create(); wid = work["id"]
        response = self.a.post(f"{COOKIE_PATH}/works/{wid}/grants",
                               json={"username": "reader", "expires_at": time.time() + 120, "can_download": True})
        gid = response.json()["grant"]["id"]
        self.assertEqual(self.b.get(work["content_url"]).status_code, 200)
        self.assertEqual(self.b.post(f"{COOKIE_PATH}/works/{wid}/grants/{gid}/revoke").status_code, 403)
        self.assertEqual(self.a.post(f"{COOKIE_PATH}/works/{wid}/grants/{gid}/revoke").status_code, 200)
        self.assertEqual(self.b.get(work["content_url"]).status_code, 403)
        self.assertEqual(self.b.get(work["content_url"] + "?download=true").status_code, 403)
        self.assertEqual(self.b.get(COOKIE_PATH + "/works").json()["works"][0]["access_status"], "revoked")
        self.a.post(f"{COOKIE_PATH}/works/{wid}/grants", json={"username": "reader", "expires_at": time.time() + 120})
        with self.service.lock, self.service.db:
            self.service.db.execute("UPDATE grants SET expires_at=0 WHERE id=?", (gid,))
        self.assertEqual(self.b.get(work["content_url"]).status_code, 403)
        self.assertEqual(self.b.get(COOKIE_PATH + "/works").json()["works"][0]["access_status"], "expired")

    def test_bad_authorization_values_and_work_path_are_rejected(self):
        work = self.create(); path = f"{COOKIE_PATH}/works/{work['id']}/grants"
        for payload in [{"username": "reader", "expires_at": 0},
                        {"username": "reader", "expires_at": True},
                        {"username": "reader", "can_download": 1},
                        {"username": "owner"}, {"username": "nobody"}]:
            self.assertIn(self.a.post(path, json=payload).status_code, [422, 404])
        self.assertEqual(self.a.get(COOKIE_PATH + "/works/not-a-work/content").status_code, 404)

    def test_signed_identifier_can_be_verified_and_is_compatible_with_original_extractor(self):
        work = self.create(); mark, data = self.mark(work)
        result = self.verify(data)
        self.assertEqual(result["status"], "verified")
        self.assertTrue(result["signature_valid"]); self.assertTrue(result["binding_valid"])
        self.assertTrue(result["record_matched"])
        self.assertEqual(result["metadata"]["recipient"], "reader")
        self.assertEqual(result["metadata"]["work_id"], work["id"])
        self.assertEqual(self.b.get(mark["url"]).status_code, 403)
        self.assertEqual(self.anonymous.get(mark["url"]).status_code, 401)
        path = self.root / "compatibility.png"; path.write_bytes(data)
        packet = stego.extract_bytes(path, "sequential_lsb", channels="RGB", bit_depth=1)
        self.assertEqual(json.loads(parse_istg(packet)["payload_data"]["data"])["metadata"], mark["metadata"])

    def test_changed_image_is_rejected_even_when_identifier_signature_is_valid(self):
        work = self.create(); _, data = self.mark(work)
        array = image_rgb(data)
        array[-1, -1, 0] ^= 2
        result = self.verify(png_bytes(array))
        self.assertTrue(result["signature_valid"]); self.assertFalse(result["binding_valid"])
        self.assertEqual(result["status"], "invalid")

    def test_transplanting_valid_identifier_to_another_picture_is_rejected(self):
        work = self.create(); _, data = self.mark(work)
        packet = extract_packet(image_rgb(data))
        other = image_rgb(picture(19))
        bits = stego._bits(stego._make_frame(packet, "sequential_lsb"))
        flat = other.reshape(-1); flat[:bits.size] = (flat[:bits.size] & 254) | bits
        result = self.verify(png_bytes(other))
        self.assertTrue(result["signature_valid"]); self.assertFalse(result["binding_valid"])
        self.assertEqual(result["status"], "invalid")

    def test_forged_signature_and_unknown_signing_key_are_rejected(self):
        work = self.create(); mark, _ = self.mark(work)
        details = {k: v for k, v in mark["metadata"].items() if k not in
                   {"version", "width", "height", "image_binding", "issuer"}}
        forged, _ = embed_identifier(self.original, details, Ed25519PrivateKey.generate())
        result = self.verify(forged)
        self.assertEqual(result["status"], "invalid"); self.assertFalse(result["signature_valid"])
        # A signature over the original values cannot authenticate a changed declaration.
        packet = extract_packet(image_rgb(self.a.get(mark["url"]).content))
        envelope = json.loads(parse_istg(packet)["payload_data"]["data"])
        envelope["metadata"]["author"] = "forged"
        fake_packet = pack_istg("text", json.dumps(envelope))
        array = image_rgb(self.original)
        bits = stego._bits(stego._make_frame(fake_packet, "sequential_lsb"))
        flat = array.reshape(-1); flat[:bits.size] = (flat[:bits.size] & 254) | bits
        result = self.verify(png_bytes(array))
        self.assertFalse(result["signature_valid"])

    def test_missing_identifier_and_missing_record_have_distinct_results(self):
        self.assertEqual(self.verify(self.original)["status"], "not_found")
        work = self.create(); mark, data = self.mark(work)
        with self.service.lock, self.service.db:
            self.service.db.execute("DELETE FROM marks WHERE id=?", (mark["id"],))
        result = self.verify(data)
        self.assertTrue(result["signature_valid"]); self.assertTrue(result["binding_valid"])
        self.assertFalse(result["record_matched"]); self.assertEqual(result["status"], "invalid")

    def test_key_records_and_encrypted_files_survive_service_restart(self):
        work = self.create(); _, data = self.mark(work)
        self.service.close()
        self.service = CopyrightService(self.root); self.app.state.copyright = self.service
        self.assertEqual(self.verify(data)["status"], "verified")
        self.assertEqual(self.a.get(work["content_url"] + "?download=true").content, self.original)

    def test_encrypted_original_tampering_is_rejected(self):
        work = self.create()
        path = self.root / (work["id"] + ".source.bin")
        content = bytearray(path.read_bytes()); content[-1] ^= 1; path.write_bytes(content)
        response = self.a.get(work["content_url"] + "?download=true")
        self.assertEqual(response.status_code, 422)
        self.assertNotIn(str(self.root), response.text)

    def test_invalid_animated_or_oversized_input_and_capacity_failure(self):
        self.assertEqual(self.a.post(COOKIE_PATH + "/works",
            files={"file": ("bad.html", b"<script>bad</script>", "text/html")},
            data={"title": "x", "author": "x", "rights": "x"}).status_code, 422)
        small = self.create(picture(size=(4, 4)))
        self.assertEqual(self.a.post(f"{COOKIE_PATH}/works/{small['id']}/mark", json={}).status_code, 422)
        gif = BytesIO()
        Image.new("RGB", (32, 32), "red").save(gif, format="GIF", save_all=True,
            append_images=[Image.new("RGB", (32, 32), "blue")], duration=10)
        self.assertEqual(self.a.post(COOKIE_PATH + "/works",
            files={"file": ("animation.gif", gif.getvalue(), "image/gif")},
            data={"title": "x", "author": "x", "rights": "x"}).status_code, 422)
        with patch("backend.copyright.MAX_PRIVATE_BYTES", 1):
            response = self.a.post(COOKIE_PATH + "/works", files={"file": ("image.png", self.original, "image/png")},
                                   data={"title": "x", "author": "x", "rights": "x"})
        self.assertEqual(response.status_code, 429)



    def test_normal_multiline_rights_are_supported_and_controls_are_rejected(self):
        response = self.a.post(COOKIE_PATH + "/works",
            files={"file": ("image.png", self.original, "image/png")},
            data={"title": "Multiline rights", "author": "Author", "rights": "Line one\r\nLine two\n\tThird line"})
        self.assertEqual(response.status_code, 201, response.text)
        work = response.json()["work"]
        self.assertEqual(work["rights"], "Line one\nLine two\n\tThird line")
        _, marked = self.mark(work)
        self.assertEqual(self.verify(marked)["metadata"]["rights"], work["rights"])
        response = self.a.post(COOKIE_PATH + "/works",
            files={"file": ("image.png", self.original, "image/png")},
            data={"title": "Title\ninvalid", "author": "Author", "rights": "Line one"})
        self.assertEqual(response.status_code, 422)

    def test_exif_orientation_is_normalized_before_marking_and_verifying(self):
        array = np.random.default_rng(9).integers(0, 256, (192, 320, 3), dtype=np.uint8)
        image = Image.fromarray(array)
        exif = image.getexif(); exif[274] = 6
        output = BytesIO(); image.save(output, format="JPEG", exif=exif)
        work = self.create(output.getvalue())
        self.assertEqual((work["width"], work["height"]), (192, 320))
        _, marked = self.mark(work)
        result = self.verify(marked)
        self.assertEqual(result["status"], "verified")
        self.assertEqual((result["metadata"]["width"], result["metadata"]["height"]), (192, 320))

    def test_missing_persistent_keys_refuses_startup_without_replacing_them(self):
        self.create()
        for name in ["content.key", "identifier.key"]:
            self.service.close()
            key = self.root / name
            content = key.read_bytes(); key.unlink()
            try:
                with self.assertRaisesRegex(RuntimeError, "缺少密钥"):
                    CopyrightService(self.root)
                self.assertFalse(key.exists())
            finally:
                key.write_bytes(content)
                self.service = CopyrightService(self.root)
                self.app.state.copyright = self.service

    def test_login_tracking_and_sessions_remain_bounded(self):
        from collections import deque
        now = time.time()
        self.service.login_attempts.update({f"unknown_{index}": deque([now]) for index in range(2048)})
        with self.assertRaises(APIError):
            self.service.login("another_unknown", "bad-password")
        self.assertEqual(len(self.service.login_attempts), 2048)
        with patch("backend.copyright.time.time", return_value=now + 61):
            with self.assertRaises(APIError):
                self.service.login("fresh_unknown", "bad-password")
        self.assertEqual(len(self.service.login_attempts), 1)
        for _ in range(21):
            self.service.login("owner", "owner-password")
        count = self.service.db.execute("SELECT COUNT(*) FROM sessions WHERE user_id=?", (self.owner["id"],)).fetchone()[0]
        self.assertEqual(count, 20)
        self.assertEqual(self.a.get(COOKIE_PATH + "/works").status_code, 401)



    def test_deeply_nested_malformed_identifier_is_rejected_without_server_error(self):
        packet = pack_istg("text", "[" * 1500 + "0" + "]" * 1500)
        array = image_rgb(self.original)
        bits = stego._bits(stego._make_frame(packet, "sequential_lsb"))
        flat = array.reshape(-1); flat[:bits.size] = (flat[:bits.size] & 254) | bits
        result = self.verify(png_bytes(array))
        self.assertEqual(result["status"], "invalid")
        self.assertFalse(result["signature_valid"])



    def _failing_cipher_writer(self, suffix, failure="write"):
        original_open = Path.open

        class FaultyWriter:
            def __init__(self, target):
                self.target = target

            def __enter__(self):
                return self

            def __exit__(self, kind, value, traceback):
                self.target.close()
                if failure == "close" and kind is None:
                    raise OSError("simulated close failure")
                return False

            def write(self, content):
                if failure == "close":
                    return self.target.write(content)
                amount = max(1, len(content) // 2)
                written = self.target.write(content[:amount])
                self.target.flush()
                if failure == "short":
                    return written
                raise OSError("simulated partial write failure")

        def fault_open(path, mode="r", *args, **kwargs):
            target = original_open(path, mode, *args, **kwargs)
            if mode == "xb" and path.name.endswith(suffix):
                return FaultyWriter(target)
            return target

        return patch.object(Path, "open", fault_open)

    def test_partial_work_writes_are_removed_and_never_counted_as_created(self):
        for suffix in [".source.bin", ".preview.bin"]:
            with self.subTest(failing_file=suffix):
                before = {path.name: path.read_bytes() for path in self.root.glob("*.bin")}
                with self._failing_cipher_writer(suffix):
                    with self.assertRaisesRegex(OSError, "partial write"):
                        self.service.create_work(self.owner, self.original, "file.png", "Title", "Author", "Rights")
                after = {path.name: path.read_bytes() for path in self.root.glob("*.bin")}
                self.assertEqual(after, before)
                self.assertEqual(self.service.db.execute("SELECT COUNT(*) FROM works").fetchone()[0], 0)
                self.assertEqual(self.service.db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 0)

    def test_partial_mark_writes_are_removed_and_existing_files_preserved(self):
        work = self.create()
        before = {path.name: path.read_bytes() for path in self.root.glob("*.bin")}
        used = self.service.db.execute("SELECT SUM(stored_bytes) FROM works").fetchone()[0]
        for failure in ["write", "close", "short"]:
            with self.subTest(failure=failure):
                with self._failing_cipher_writer(".mark.bin", failure):
                    with self.assertRaises(OSError):
                        self.service.mark(self.owner, work["id"], "reader")
                self.assertEqual({path.name: path.read_bytes() for path in self.root.glob("*.bin")}, before)
                self.assertEqual(self.service.db.execute("SELECT COUNT(*) FROM marks").fetchone()[0], 0)
                self.assertEqual(self.service.db.execute("SELECT SUM(stored_bytes) FROM works").fetchone()[0], used)
                self.assertEqual(self.a.get(work["content_url"] + "?download=true").content, self.original)

    def test_failed_create_close_removes_new_files_but_open_collision_keeps_old_file(self):
        with self._failing_cipher_writer(".preview.bin", "close"):
            with self.assertRaisesRegex(OSError, "close failure"):
                self.service.create_work(self.owner, self.original, "file.png", "Title", "Author", "Rights")
        self.assertEqual(list(self.root.glob("*.bin")), [])
        existing = self.root / "existing.bin"
        existing.write_bytes(b"existing ciphertext")
        with self.assertRaises(FileExistsError):
            self.service._put(existing.name, b"new content", b"test")
        self.assertEqual(existing.read_bytes(), b"existing ciphertext")


if __name__ == "__main__":
    unittest.main()
