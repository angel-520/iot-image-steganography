"""只读校验现有 ISTG v1 协议，不修改封装或认证加密逻辑。"""

from __future__ import annotations

from io import BytesIO
import struct
from typing import Any
import zlib
from hashlib import sha256

import cbor2

from .utils import MAX_IMAGE_PIXELS, MAX_PACKET_BYTES


PREFIX = struct.Struct(">4sBBBBBBHI")
HEADER_BYTES = PREFIX.size + 4


def decode_cbor_object(data: bytes, max_bytes: int = MAX_PACKET_BYTES) -> dict[str, Any]:
    """限深解码单个 CBOR map，拒绝尾随、重复键及不定长对象。"""
    if not isinstance(data, bytes):
        raise TypeError("CBOR 对象必须是 bytes")
    if type(max_bytes) is not int or not 1 <= max_bytes <= 128 * 1024 * 1024 or len(data) > max_bytes:
        raise ValueError("CBOR 对象超过大小限制")
    stream = BytesIO(data)
    try:
        result = cbor2.CBORDecoder(
            stream, max_depth=32, allow_indefinite=False,
            allow_duplicate_keys=False, strict_hashable_types=True,
        ).decode()
    except (cbor2.CBORDecodeError, ValueError, TypeError, OverflowError, RecursionError) as exc:
        raise ValueError("无效或超出限制的 CBOR 对象") from exc
    if stream.read(1):
        raise ValueError("CBOR 对象后存在尾随数据")
    if not isinstance(result, dict) or any(not isinstance(key, str) for key in result):
        raise ValueError("CBOR 内容必须是字符串键的 map")
    return result


def _plain_metadata(metadata: dict[str, Any]) -> None:
    if (metadata.get("payload_format") != "cbor" or metadata.get("cipher") != "none"
            or metadata.get("compression") != "none"):
        raise ValueError("不支持的明文 Payload 格式、压缩方式或 cipher 声明")


def _payload_schema(payload: bytes, limit: int = MAX_PACKET_BYTES) -> dict[str, Any]:
    inner = decode_cbor_object(payload, limit)
    kind, data = inner.get("kind"), inner.get("data")
    if not isinstance(data, bytes):
        raise ValueError("CBOR Payload 的 data 必须为 bytes")
    compression = inner.get('content_compression', 'none')
    if compression != 'none':
        size = inner.get('original_size')
        if compression != 'zlib' or kind != 'file' or type(size) is not int or not 0 <= size <= 64 * 1024 * 1024:
            raise ValueError('文件压缩描述无效或解压大小超限')
        try:
            decoder = zlib.decompressobj()
            decoded = decoder.decompress(data, size + 1)
            if len(decoded) != size or not decoder.eof or decoder.unconsumed_tail or decoder.unused_data:
                raise ValueError('压缩文件超限、截断或含尾随内容')
        except zlib.error as exc:
            raise ValueError('压缩文件数据损坏') from exc
        inner['data'] = data = decoded
    if kind == "text":
        if inner.get("encoding") != "utf-8":
            raise ValueError("仅支持 UTF-8 文本")
        try:
            data.decode("utf-8")
        except UnicodeError as exc:
            raise ValueError("文本数据不是有效 UTF-8") from exc
    elif kind in {"image_file", "file"}:
        if not isinstance(inner.get("filename"), str) or not isinstance(inner.get("mime"), str):
            raise ValueError("图片文件描述缺少 filename 或 mime")
        if kind == "file" and inner.get("sha256") != sha256(data).hexdigest():
            raise ValueError("文件 SHA-256 校验失败")
    elif kind == "image_pixels":
        width, height, bits = inner.get("width"), inner.get("height"), inner.get("bits_per_channel")
        if any(isinstance(value, bool) or not isinstance(value, int) for value in (width, height, bits)):
            raise ValueError("图片像素尺寸和位数必须是整数")
        if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS or not 1 <= bits <= 4:
            raise ValueError("图片像素尺寸或位数超出限制")
        if inner.get("channels") != 3 or inner.get("bit_order") != "msb_first":
            raise ValueError("仅支持 RGB、MSB-first 像素数据")
        needed = width * height * 3 * bits
        if len(data) != (needed + 7) // 8:
            raise ValueError("图片像素数据长度错误")
        padding = (-needed) % 8
        if padding and data[-1] & ((1 << padding) - 1):
            raise ValueError("图片像素尾部填充位错误")
    else:
        raise ValueError("未知的 ISTG Payload kind")
    return inner


def parse_istg(data: bytes, max_packet_bytes: int = MAX_PACKET_BYTES) -> dict[str, Any]:
    """严格校验实际 ISTG v1 包，返回解析结果；加密包尚不代表认证成功。

    校验签名、版本、CBOR 格式、精确长度及头部/元数据 CRC32。明文校验
    Payload schema；加密包校验现有加密元数据，但不尝试破解密码。
    """
    if type(max_packet_bytes) is not int or not 1 <= max_packet_bytes <= 128 * 1024 * 1024:
        raise ValueError('ISTG 解析上限无效')
    limit = max_packet_bytes
    if not isinstance(data, bytes):
        raise TypeError("ISTG 数据必须是 bytes")
    if len(data) > limit or len(data) < HEADER_BYTES:
        raise ValueError("ISTG 包长度超出限制或不足 20 字节")
    fields = PREFIX.unpack_from(data)
    magic, version, algorithm_id, flags, channels, depth, meta_format, meta_len, payload_len = fields
    if magic != b"ISTG" or version != 1 or meta_format != 1:
        raise ValueError("不是支持的 ISTG v1/CBOR 数据包")
    if HEADER_BYTES + meta_len + payload_len != len(data):
        raise ValueError("ISTG 元数据长度或 Payload 长度与实际不一致")
    metadata_bytes = data[HEADER_BYTES:HEADER_BYTES + meta_len]
    crc = struct.unpack_from(">I", data, PREFIX.size)[0]
    if zlib.crc32(data[:PREFIX.size] + metadata_bytes) & 0xFFFFFFFF != crc:
        raise ValueError("ISTG 头部/元数据 CRC32 校验失败")
    metadata = decode_cbor_object(metadata_bytes)
    payload = data[HEADER_BYTES + meta_len:]
    encrypted = bool(flags & 1)
    inner = None
    if encrypted:
        if metadata.get("cipher") not in ("AES-256-GCM", "ChaCha20-Poly1305"):
            raise ValueError("未知加密算法")
        if metadata.get("kdf") != "scrypt" or metadata.get("kdf_params") != {"n": 2**15, "r": 8, "p": 1}:
            raise ValueError("不支持的密钥派生配置")
        for name, size in (("salt", 16), ("nonce", 12)):
            if not isinstance(metadata.get(name), bytes) or len(metadata[name]) != size:
                raise ValueError(f"无效的加密参数 {name}")
        original = metadata.get("original_metadata")
        if not isinstance(original, bytes) or len(payload) < 16:
            raise ValueError("加密包缺少原始元数据或认证标签")
        _plain_metadata(decode_cbor_object(original))
    else:
        _plain_metadata(metadata)
        inner = _payload_schema(payload, limit)
    return {
        "fields": fields, "metadata": metadata, "payload": payload,
        "payload_data": inner, "encrypted": encrypted,
        "packet_length": len(data), "payload_length": payload_len,
        "header_crc_verified": True,
    }
