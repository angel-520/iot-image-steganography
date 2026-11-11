"""ISTG v1 认证加解密库，提供 bytes 接口及十六进制 TXT 文件接口。"""

from __future__ import annotations

from pathlib import Path
import secrets
import struct
import zlib

import cbor2
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM, ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

# 与前一个 istg_pack.py 保持一致：前 16 字节 + CRC32(4字节)。
_PREFIX = struct.Struct(">4sBBBBBBHI")
_CRC = struct.Struct(">I")
_HEADER_SIZE = _PREFIX.size + _CRC.size
_MAGIC = b"ISTG"
_VERSION = 1
_META_CBOR = 1
_FLAG_ENCRYPTED = 0x01

# v1 使用固定、合理的 scrypt 成本，读取不可信文件时不让其指定无限资源消耗。
_SCRYPT_N = 2**15
_SCRYPT_R = 8
_SCRYPT_P = 1
_SALT_SIZE = 16
_NONCE_SIZE = 12
_TAG_SIZE = 16
_ALLOWED_ALGORITHMS = {"AES-256-GCM", "ChaCha20-Poly1305"}


def _password_key(password: str, salt: bytes) -> bytes:
    if not isinstance(password, str):
        raise TypeError("password 必须是 str")
    if not password:
        raise ValueError("密码不能为空")
    return Scrypt(
        salt=salt,
        length=32,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
    ).derive(password.encode("utf-8"))


def _cipher(algorithm: str, key: bytes):
    if algorithm == "AES-256-GCM":
        return AESGCM(key)
    if algorithm == "ChaCha20-Poly1305":
        return ChaCha20Poly1305(key)
    raise ValueError("algorithm 仅支持 AES-256-GCM 或 ChaCha20-Poly1305")


def _read_packet(packet: bytes) -> dict:
    """检查 ISTG v1 格式与 CRC32，分离 Header、Metadata、Payload。"""
    if not isinstance(packet, bytes):
        raise TypeError("data 必须是 bytes")
    if len(packet) < _HEADER_SIZE:
        raise ValueError("ISTG 数据包不足 20 字节")

    fields = _PREFIX.unpack(packet[:_PREFIX.size])
    magic, version, algorithm_id, flags, channel, bit_depth, meta_format, meta_len, payload_len = fields
    if magic != _MAGIC or version != _VERSION:
        raise ValueError("不是有效的 ISTG v1 数据包")
    if meta_format != _META_CBOR:
        raise ValueError("只支持 CBOR 元数据格式（Metadata Format=1）")

    expected_length = _HEADER_SIZE + meta_len + payload_len
    if len(packet) != expected_length:
        raise ValueError(
            f"数据包长度错误：应为 {expected_length} 字节，实际 {len(packet)} 字节"
        )
    metadata_bytes = packet[_HEADER_SIZE:_HEADER_SIZE + meta_len]
    payload = packet[_HEADER_SIZE + meta_len:]

    expected_crc = _CRC.unpack(packet[_PREFIX.size:_HEADER_SIZE])[0]
    actual_crc = zlib.crc32(packet[:_PREFIX.size] + metadata_bytes) & 0xFFFFFFFF
    if actual_crc != expected_crc:
        raise ValueError("ISTG Header/Metadata CRC32 校验失败")
    try:
        metadata = cbor2.loads(metadata_bytes)
    except (cbor2.CBORDecodeError, ValueError, TypeError, EOFError) as exc:
        raise ValueError("CBOR 元数据格式错误") from exc
    if not isinstance(metadata, dict):
        raise ValueError("CBOR 元数据必须是 map（字典）")
    return {
        "fields": fields,
        "prefix": packet[:_PREFIX.size],
        "metadata_bytes": metadata_bytes,
        "metadata": metadata,
        "payload": payload,
    }


def _assemble(fields: tuple, metadata_bytes: bytes, payload: bytes) -> bytes:
    if len(metadata_bytes) > 0xFFFF:
        raise ValueError("元数据长度超过 ISTG v1 的 65535 字节上限")
    if len(payload) > 0xFFFFFFFF:
        raise ValueError("Payload 长度超过 ISTG v1 的 4 GiB 上限")
    magic, version, algorithm_id, flags, channel, bit_depth, meta_format, _, _ = fields
    prefix = _PREFIX.pack(
        magic, version, algorithm_id, flags, channel, bit_depth,
        meta_format, len(metadata_bytes), len(payload)
    )
    crc = zlib.crc32(prefix + metadata_bytes) & 0xFFFFFFFF
    return prefix + _CRC.pack(crc) + metadata_bytes + payload


def encrypt_istg(
    data: bytes,
    password: str,
    algorithm: str = "AES-256-GCM",
) -> bytes:
    """认证加密 ISTG 包中的 Payload，返回新的完整 ISTG 包 bytes。

    Salt 和 Nonce 自动随机生成，无需调用者传入。
    原始 Metadata 以原始 bytes 保存在加密元数据中，方便解密时逐字节恢复原包。
    """
    if algorithm not in _ALLOWED_ALGORITHMS:
        raise ValueError(f"不支持的算法：{algorithm}")
    parsed = _read_packet(data)
    fields = parsed["fields"]
    if fields[3] & _FLAG_ENCRYPTED:
        raise ValueError("数据包已经加密，不能重复加密；请先解密")
    # v1 的上一阶段生成文件为 cipher='none'。
    if parsed["metadata"].get("cipher", "none") != "none":
        raise ValueError("元数据声明已加密，但 Header 未标记；拒绝处理")

    salt = secrets.token_bytes(_SALT_SIZE)
    nonce = secrets.token_bytes(_NONCE_SIZE)
    key = _password_key(password, salt)
    new_metadata = {
        "cipher": algorithm,
        "kdf": "scrypt",
        "kdf_params": {"n": _SCRYPT_N, "r": _SCRYPT_R, "p": _SCRYPT_P},
        "salt": salt,
        "nonce": nonce,
        # 不包含秘密正文；保存原始元数据字节只是为了能够精确恢复封装结果。
        "original_metadata": parsed["metadata_bytes"],
    }
    encrypted_metadata_bytes = cbor2.dumps(new_metadata)
    enc_flags = fields[3] | _FLAG_ENCRYPTED
    # AEAD 加密得到密文 + 16 B 认证标签。
    planned_payload_size = len(parsed["payload"]) + _TAG_SIZE
    if len(encrypted_metadata_bytes) > 0xFFFF or planned_payload_size > 0xFFFFFFFF:
        raise ValueError("加密后 ISTG 数据包长度超出格式限制")
    magic, version, algorithm_id, _, channel, bit_depth, meta_format, _, _ = fields
    new_prefix = _PREFIX.pack(
        magic, version, algorithm_id, enc_flags, channel, bit_depth,
        meta_format, len(encrypted_metadata_bytes), planned_payload_size,
    )
    aad = new_prefix + encrypted_metadata_bytes
    encrypted_payload = _cipher(algorithm, key).encrypt(nonce, parsed["payload"], aad)
    encrypted_fields = (
        magic, version, algorithm_id, enc_flags, channel, bit_depth, meta_format,
        len(encrypted_metadata_bytes), len(encrypted_payload),
    )
    return _assemble(encrypted_fields, encrypted_metadata_bytes, encrypted_payload)


def decrypt_istg(data: bytes, password: str) -> bytes:
    """认证解密 ISTG Payload，返回最初未加密的完整 ISTG 包 bytes。"""
    parsed = _read_packet(data)
    fields = parsed["fields"]
    if not (fields[3] & _FLAG_ENCRYPTED):
        raise ValueError("输入的 ISTG 数据包尚未加密")
    meta = parsed["metadata"]
    algorithm = meta.get("cipher")
    if algorithm not in _ALLOWED_ALGORITHMS:
        raise ValueError("不受支持的加密算法")
    if meta.get("kdf") != "scrypt" or meta.get("kdf_params") != {
        "n": _SCRYPT_N, "r": _SCRYPT_R, "p": _SCRYPT_P
    }:
        raise ValueError("不支持的密钥派生配置")
    salt = meta.get("salt")
    nonce = meta.get("nonce")
    previous_metadata = meta.get("original_metadata")
    if not isinstance(salt, bytes) or len(salt) != _SALT_SIZE:
        raise ValueError("Salt 格式或长度错误")
    if not isinstance(nonce, bytes) or len(nonce) != _NONCE_SIZE:
        raise ValueError("Nonce 格式或长度错误")
    if not isinstance(previous_metadata, bytes):
        raise ValueError("原始元数据缺失或格式错误")
    try:
        original_meta_obj = cbor2.loads(previous_metadata)
        if not isinstance(original_meta_obj, dict):
            raise ValueError("原始元数据不是字典")
    except (cbor2.CBORDecodeError, ValueError, TypeError, EOFError) as exc:
        raise ValueError("原始元数据损坏") from exc

    key = _password_key(password, salt)
    aad = parsed["prefix"] + parsed["metadata_bytes"]
    try:
        plaintext_payload = _cipher(algorithm, key).decrypt(
            nonce, parsed["payload"], aad
        )
    except InvalidTag as exc:
        raise ValueError("认证解密失败：密码错误或数据遭到修改") from exc

    old_fields = (
        fields[0], fields[1], fields[2], fields[3] & ~_FLAG_ENCRYPTED,
        fields[4], fields[5], fields[6], len(previous_metadata), len(plaintext_payload)
    )
    return _assemble(old_fields, previous_metadata, plaintext_payload)


def read_hex_txt(path: str | Path) -> bytes:
    """读取之前 istg_pack.py 产生的空格分隔十六进制 TXT。"""
    source = Path(path)
    if source.suffix.lower() != ".txt":
        raise ValueError("输入文件必须是 .txt")
    try:
        return bytes.fromhex(source.read_text(encoding="ascii"))
    except (ValueError, UnicodeError) as exc:
        raise ValueError("输入 TXT 必须包含十六进制数据（允许空格和换行）") from exc


def write_hex_txt(path: str | Path, data: bytes) -> None:
    """将原始 bytes 以易读的十六进制 TXT 格式保存。"""
    if not isinstance(data, bytes):
        raise TypeError("data 必须是 bytes")
    target = Path(path)
    if target.suffix.lower() != ".txt":
        raise ValueError("输出文件必须以 .txt 结尾")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(data.hex(" ").upper(), encoding="ascii")


def encrypt_txt(
    input_path: str | Path,
    output_path: str | Path,
    password: str,
    algorithm: str = "AES-256-GCM",
) -> bytes:
    """便捷接口：读 TXT -> 加密 ISTG bytes -> 写 TXT。"""
    result = encrypt_istg(read_hex_txt(input_path), password, algorithm)
    write_hex_txt(output_path, result)
    return result


def decrypt_txt(
    input_path: str | Path,
    output_path: str | Path,
    password: str,
) -> bytes:
    """便捷接口：读加密 TXT -> 解密 ISTG bytes -> 写原始 TXT。"""
    result = decrypt_istg(read_hex_txt(input_path), password)
    write_hex_txt(output_path, result)
    return result
