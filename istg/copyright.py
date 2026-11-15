"""Signed image identifiers carried by the existing ISTG/STG1 sequential-LSB format."""
from __future__ import annotations

from hashlib import sha256
from io import BytesIO
import base64
import json
import struct
import warnings

import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from .istg_pack import pack_istg
from .security_analysis.packet import parse_istg
from . import istg_stego as stego

MAX_PIXELS = 16_000_000
MAX_IMAGE_BYTES = 64 * 1024 * 1024
MAX_MARK_PACKET = 32 * 1024
FIELDS = {"version", "work_id", "distribution_id", "title", "author", "rights", "recipient",
          "issued_at", "width", "height", "image_binding", "issuer"}


def image_rgb(data: bytes) -> np.ndarray:
    """Decode a bounded, single-frame image without retaining its source metadata."""
    if not isinstance(data, bytes) or not data or len(data) > MAX_IMAGE_BYTES:
        raise ValueError("图片大小超出限制")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as image:
                if image.format not in {"PNG", "JPEG", "BMP", "WEBP", "TIFF"}:
                    raise ValueError("不支持此图片格式")
                if image.width * image.height > MAX_PIXELS or min(image.size) <= 0:
                    raise ValueError("图片不能超过 1600 万像素")
                if getattr(image, "n_frames", 1) != 1:
                    raise ValueError("仅支持单帧图片")
                return np.asarray(ImageOps.exif_transpose(image).convert("RGB")).copy()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombWarning,
            Image.DecompressionBombError) as exc:
        raise ValueError("图片损坏或无法读取") from exc


def png_bytes(array: np.ndarray) -> bytes:
    output = BytesIO()
    Image.fromarray(array).save(output, format="PNG")
    return output.getvalue()


def image_binding(array: np.ndarray) -> str:
    """Hash dimensions and RGB high seven bits; signing never depends on written LSBs."""
    prefix = struct.pack(">II", array.shape[1], array.shape[0])
    return sha256(prefix + (array & 254).tobytes()).hexdigest()


def canonical(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def issuer_id(public: Ed25519PublicKey) -> str:
    return sha256(public.public_bytes(Encoding.Raw, PublicFormat.Raw)).hexdigest()


def extract_packet(array: np.ndarray) -> bytes:
    values = array.reshape(-1)
    if values.size < stego._FRAME_BITS:
        raise ValueError("图片中未找到版权标识")
    bits = values[:stego._FRAME_BITS] & 1
    length, _ = stego._header_from_bits(bits, "sequential_lsb")
    if not 0 < length <= MAX_MARK_PACKET:
        raise ValueError("版权标识长度无效")
    needed = (stego._FRAME.size + length) * 8
    if needed > values.size:
        raise ValueError("版权标识不完整")
    return stego._payload_from_bits(values[:needed] & 1, "sequential_lsb")


def embed_identifier(data: bytes, details: dict, private: Ed25519PrivateKey) -> tuple[bytes, dict]:
    array = image_rgb(data)
    metadata = {**details, "version": 1, "width": array.shape[1], "height": array.shape[0],
                "image_binding": image_binding(array), "issuer": issuer_id(private.public_key())}
    if set(metadata) != FIELDS:
        raise ValueError("版权标识字段不完整")
    signed = canonical(metadata)
    envelope = {"metadata": metadata, "signature": base64.b64encode(private.sign(signed)).decode("ascii")}
    packet = pack_istg("text", canonical(envelope).decode("utf-8"))
    if len(packet) > MAX_MARK_PACKET:
        raise ValueError("版权信息过长")
    bits = stego._bits(stego._make_frame(packet, "sequential_lsb"))
    values = array.reshape(-1)
    if bits.size > values.size:
        raise ValueError("图片容量不足，请使用尺寸更大的图片")
    # RGB + one low bit is sequential LSB. Only the prefix needed for this bounded packet
    # is materialised, avoiding a full-size index/bit-position array for large images.
    values[:bits.size] = (values[:bits.size] & 254) | bits
    if extract_packet(array) != packet:
        raise ValueError("版权标识写入校验失败")
    return png_bytes(array), metadata


def _no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("版权标识字段重复")
        result[key] = value
    return result


def verify_identifier(data: bytes, public: Ed25519PublicKey) -> dict:
    array = image_rgb(data)
    try:
        packet = extract_packet(array)
    except ValueError:
        return {"status": "not_found", "signature_valid": False, "binding_valid": False,
                "record_matched": False, "message": "未找到完整的版权标识"}
    metadata = None
    signature_valid = False
    binding_valid = False
    try:
        inner = parse_istg(packet, max_packet_bytes=MAX_MARK_PACKET)
        if inner["encrypted"] or inner["payload_data"]["kind"] != "text":
            raise ValueError("版权标识格式不匹配")
        envelope = json.loads(inner["payload_data"]["data"].decode("utf-8"), object_pairs_hook=_no_duplicates)
        if not isinstance(envelope, dict) or set(envelope) != {"metadata", "signature"}:
            raise ValueError("版权标识格式不匹配")
        metadata = envelope["metadata"]
        if not isinstance(metadata, dict) or set(metadata) != FIELDS or type(metadata["version"]) is not int or metadata["version"] != 1:
            raise ValueError("版权标识版本无效")
        for field in FIELDS - {"version", "issued_at", "width", "height"}:
            if not isinstance(metadata[field], str) or len(metadata[field]) > 2000:
                raise ValueError("版权标识字段无效")
        if any(type(metadata[field]) is not int for field in ("issued_at", "width", "height")):
            raise ValueError("版权标识尺寸或时间无效")
        signature = base64.b64decode(envelope["signature"], validate=True)
        if len(signature) != 64 or metadata["issuer"] != issuer_id(public):
            raise ValueError("版权标识签发来源不匹配")
        public.verify(signature, canonical(metadata))
        signature_valid = True
        binding_valid = (metadata["width"] == array.shape[1] and metadata["height"] == array.shape[0]
                         and metadata["image_binding"] == image_binding(array))
    except (ValueError, KeyError, TypeError, UnicodeError, InvalidSignature, OverflowError, RecursionError):
        pass
    valid = signature_valid and binding_valid
    result = {"status": "verified" if valid else "invalid", "signature_valid": signature_valid,
              "binding_valid": binding_valid, "record_matched": False,
              "message": "标识签名与图像匹配" if valid else "标识签名或图像匹配校验失败"}
    if signature_valid:
        result["metadata"] = metadata
    return result
