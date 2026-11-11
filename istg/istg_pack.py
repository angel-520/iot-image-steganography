"""文本与图片预处理及 ISTG v1 封装库，通过 pack_istg() 调用。"""

from pathlib import Path
import struct
import zlib
from hashlib import sha256

import cbor2
from PIL import Image


def pack_binary(data: bytes, filename: str = "payload.bin", mime: str = "application/octet-stream", *,
                max_bytes: int = 15 * 1024 * 1024, compress: bool = False) -> bytes:
    """Lossless arbitrary-file payload using the existing ISTG v1 envelope."""
    if not 0 < max_bytes <= 64 * 1024 * 1024 or not isinstance(data, bytes) or len(data) > max_bytes:
        raise ValueError(f"秘密文件必须为 bytes 且不超过 {max_bytes // (1024 * 1024)} MiB")
    filename = str(filename).replace("\\", "/").rsplit("/", 1)[-1][:120] or "payload.bin"
    inner = {"kind": "file", "filename": filename, "mime": mime[:100],
             "data": data, "sha256": sha256(data).hexdigest()}
    if compress:
        compressed = zlib.compress(data, level=6)
        if len(compressed) + 64 < len(data):
            inner.update(data=compressed, content_compression='zlib', original_size=len(data))
    payload = cbor2.dumps(inner)
    metadata = cbor2.dumps({"payload_format": "cbor", "cipher": "none", "compression": "none"})
    prefix = struct.pack(">4sBBBBBBHI", b"ISTG", 1, 0, 0, 0, 0, 1, len(metadata), len(payload))
    return prefix + struct.pack(">I", zlib.crc32(prefix + metadata) & 0xFFFFFFFF) + metadata + payload


def _pack_pixel_msb(rgb_bytes: bytes, bits: int) -> bytes:
    """按 RGB 顺序，逐通道取高 bits 位，按 MSB-first 紧密打包为字节流。"""
    mask = (1 << bits) - 1
    acc = 0
    count = 0
    result = bytearray()
    for channel in rgb_bytes:
        acc = (acc << bits) | ((channel >> (8 - bits)) & mask)
        count += bits
        while count >= 8:
            count -= 8
            result.append((acc >> count) & 0xFF)
            acc &= (1 << count) - 1
    if count:
        result.append(acc << (8 - count))  # 最后不足 8 bit 时右侧补零
    return bytes(result)


def pack_istg(
    data_type: str,
    content: str,
    output_path: str | Path | None = None,
    image_mode: str = "file",
    pixel_bits: int = 4,
) -> bytes:
    """封装文本或图片，返回完整 ISTG 包，可选写入十六进制 TXT。

    content 为文本正文或图片路径；image_mode='file' 保留原文件字节，
    'pixels' 仅保留 RGB 每通道高 pixel_bits 位（1~4 位，有损）。
    output_path=None 时仅返回 bytes，适合 Web 流程；提供路径时必须以
    .txt 结尾，父目录不存在时自动创建，兼容原文件接口。
    """
    if data_type not in {"text", "image"}:
        raise ValueError("data_type 只能是 'text' 或 'image'")

    algorithm_id = 0  # 0x00：尚未选择具体的隐写算法
    channel_mask = 0   # 0：尚未选择嵌入用 RGB 通道
    bit_depth = 0      # 0：尚未选择嵌入位深

    if data_type == "text":
        if not isinstance(content, str):
            raise TypeError("文本 content 必须是 str")
        inner = {"kind": "text", "encoding": "utf-8", "data": content.encode("utf-8")}
    else:
        if image_mode not in {"file", "pixels"}:
            raise ValueError("image_mode 只能是 'file' 或 'pixels'")
        path = Path(content)
        if not path.is_file():
            raise FileNotFoundError(f"秘密图片不存在：{path}")

        with Image.open(path) as img:
            fmt = img.format
            if image_mode == "pixels":
                if not isinstance(pixel_bits, int) or not 1 <= pixel_bits <= 4:
                    raise ValueError("pixel_bits 必须是 1~4 的整数")
                rgb = img.convert("RGB")
                w, h = rgb.size
                packed = _pack_pixel_msb(rgb.tobytes(), pixel_bits)
                inner = {
                    "kind": "image_pixels", "width": w, "height": h,
                    "channels": 3, "bits_per_channel": pixel_bits,
                    "bit_order": "msb_first", "data": packed,
                }
                algorithm_id = 0x08
                channel_mask = 0x07
                bit_depth = pixel_bits
            else:
                inner = {
                    "kind": "image_file", "filename": path.name,
                    "mime": Image.MIME.get(fmt, "application/octet-stream"),
                    "data": path.read_bytes(),
                }

    payload = cbor2.dumps(inner)
    metadata = cbor2.dumps({
        "payload_format": "cbor",
        "cipher": "none",
        "compression": "none",
    })

    if len(metadata) > 0xFFFF:
        raise ValueError("元数据长度超过 65535 字节")
    if len(payload) > 0xFFFFFFFF:
        raise ValueError("有效载荷长度超过 4 GiB 限制")

    # 前 16 字节，严格匹配 ISTG v1 固定头布局。
    prefix = struct.pack(
        ">4sBBBBBBHI", b"ISTG", 1, algorithm_id, 0,
        channel_mask, bit_depth, 1, len(metadata), len(payload),
    )
    crc32 = zlib.crc32(prefix + metadata) & 0xFFFFFFFF
    header = prefix + struct.pack(">I", crc32)
    packet = header + metadata + payload

    if output_path is not None:
        target = Path(output_path)
        if target.suffix.lower() != ".txt":
            raise ValueError("output_path 必须以 .txt 结尾")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(packet.hex(" ").upper(), encoding="ascii")
    return packet
