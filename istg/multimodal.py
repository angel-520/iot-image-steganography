"""Bounded multimedia carriers. No passwords, executable content or paths in framing."""
from __future__ import annotations

from hashlib import sha256
from io import BytesIO
from pathlib import Path
import struct
import wave

import numpy as np

from .media_limits import PACKET_BYTES, TEXT_OUTPUT

MAX_PACKET = PACKET_BYTES
MAX_DECODED = 96 * 1024 * 1024
HEADER = struct.Struct(">4sI32s")
ZERO, ONE = "\u200b", "\u200c"
START, END = "\u2063\u2063\u2063", "\u2064\u2064\u2064"


def frame(packet: bytes) -> bytes:
    if len(packet) > MAX_PACKET:
        raise ValueError("数据包超过 65 MiB")
    return HEADER.pack(b"MM01", len(packet), sha256(packet).digest()) + packet


def embed_bits(values: np.ndarray, packet: bytes) -> np.ndarray:
    bits = np.unpackbits(np.frombuffer(frame(packet), dtype=np.uint8))
    if bits.size > values.size:
        raise ValueError(f"容量不足：需要 {len(packet)} 字节，可用 {max(0, values.size // 8 - HEADER.size)} 字节")
    result = values.copy()
    mask = np.bitwise_not(np.array(1, dtype=result.dtype))
    result[:bits.size] = (result[:bits.size] & mask) | bits.astype(result.dtype)
    return result


def extract_bits(values: np.ndarray, *, verify: bool = True, expected: int | None = None) -> bytes:
    if expected is None:
        if values.size < HEADER.size * 8:
            raise ValueError("载体太小或未检测到隐写数据")
        header = np.packbits((values[:HEADER.size * 8] & 1).astype(np.uint8)).tobytes()
        magic, length, digest = HEADER.unpack(header)
        if magic != b"MM01" or length > MAX_PACKET or (length + HEADER.size) * 8 > values.size:
            raise ValueError("未检测到有效隐写帧，或帧头已损坏")
    else:
        length, digest = expected, b""
        if not 0 <= length <= MAX_PACKET:
            raise ValueError("无效的参考数据长度")
    count = min(values.size, (length + HEADER.size) * 8)
    data = np.packbits((values[HEADER.size * 8:count] & 1).astype(np.uint8)).tobytes()[:length]
    if verify and sha256(data).digest() != digest:
        raise ValueError("隐写数据 SHA-256 校验失败，载体可能经过压缩或修改")
    return data


def read_audio(path: str | Path) -> tuple[np.ndarray, int]:
    try:
        with wave.open(str(path), "rb") as source:
            if source.getcomptype() != "NONE" or source.getsampwidth() != 2:
                raise ValueError("音频载体仅支持 16-bit PCM WAV；请先转换格式")
            channels, rate, count = source.getnchannels(), source.getframerate(), source.getnframes()
            if channels not in (1, 2) or not 8000 <= rate <= 192000 or count * channels * 2 > MAX_DECODED:
                raise ValueError("WAV 需为单/双声道、8–192 kHz，解码数据不超过 96 MiB")
            data = source.readframes(count)
            if len(data) != count * channels * 2 or not count:
                raise ValueError("WAV 数据不完整或为空")
            return np.frombuffer(data, dtype="<i2").reshape(-1, channels).copy(), rate
    except (wave.Error, EOFError, OSError) as exc:
        raise ValueError("无法读取 PCM WAV 音频") from exc


def audio_bytes(samples: np.ndarray, rate: int) -> bytes:
    buffer = BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(samples.shape[1]); output.setsampwidth(2); output.setframerate(rate)
        output.writeframes(samples.astype("<i2").tobytes())
    return buffer.getvalue()


def audio_slots(samples: np.ndarray, algorithm: str) -> np.ndarray:
    if algorithm == "audio_lsb":
        return samples.reshape(-1).astype(np.int32)
    if algorithm != "audio_dwt":
        raise ValueError("音频算法应为 audio_lsb 或 audio_dwt")
    # Integer Haar lifting, pair samples within each channel (not across stereo channels).
    pairs = samples[:samples.shape[0] // 2 * 2].astype(np.int32)
    return (pairs[0::2] - pairs[1::2]).reshape(-1)


def embed_audio(samples: np.ndarray, packet: bytes, algorithm: str) -> np.ndarray:
    slots = audio_slots(samples, algorithm)
    modified = embed_bits(slots, packet)
    if algorithm == "audio_lsb":
        return modified.astype(np.int16).reshape(samples.shape)
    result = samples.copy()
    count = samples.shape[0] // 2
    a, b = samples[:count * 2:2].astype(np.int32), samples[1:count * 2:2].astype(np.int32)
    low = (a + b) // 2
    difference = modified.reshape(a.shape)
    upper, lower = low + (difference + 1) // 2, low - difference // 2
    # At PCM boundaries choose the other adjacent coefficient with the same parity.
    invalid = (upper > 32767) | (upper < -32768) | (lower > 32767) | (lower < -32768)
    difference[invalid] -= 2
    upper, lower = low + (difference + 1) // 2, low - difference // 2
    # Saturated equal sample pairs may require changing the low-pass coefficient by one.
    low[(upper > 32767) | (lower > 32767)] -= 1
    low[(upper < -32768) | (lower < -32768)] += 1
    upper, lower = low + (difference + 1) // 2, low - difference // 2
    if np.any((upper > 32767) | (upper < -32768) | (lower > 32767) | (lower < -32768)):
        raise ValueError("极值 PCM 样本无法安全嵌入 Haar 系数，请使用 Audio LSB")
    result[:count * 2:2], result[1:count * 2:2] = upper, lower
    return result


def audio_analysis(original: np.ndarray, stego: np.ndarray, rate: int) -> dict:
    a, b = original.astype(float), stego.astype(float)
    mse = float(np.mean((a - b) ** 2))
    power = float(np.mean(a ** 2))
    snr = 10 * np.log10(power / mse) if mse and power else None
    indices = np.linspace(0, len(a) - 1, min(1200, len(a)), dtype=int)
    n = min(16384, len(a))
    window = np.hanning(n)
    frequencies = np.fft.rfftfreq(n, 1 / rate)
    selected = np.linspace(0, len(frequencies) - 1, min(1024, len(frequencies)), dtype=int)
    def spectrum(data):
        amplitude = np.abs(np.fft.rfft(data[:n, 0] * window)) * 2 / max(1, float(window.sum())) / 32768
        amplitude[0] /= 2
        if n % 2 == 0:
            amplitude[-1] /= 2
        return (20 * np.log10(np.maximum(amplitude, 1e-8)))[selected].tolist()
    return {"sample_rate": rate, "channels": a.shape[1], "duration": len(a) / rate,
            "mse": mse, "snr_db": float(snr) if snr is not None else None,
            "identical": mse == 0, "changed_samples": int(np.count_nonzero(a != b)),
            "time": (indices / rate).tolist(), "original_wave": a[indices, 0].tolist(),
            "stego_wave": b[indices, 0].tolist(), "frequency": frequencies[selected].tolist(),
            "original_spectrum": spectrum(a), "stego_spectrum": spectrum(b)}


def read_video(path: str | Path) -> tuple[np.ndarray, float]:
    import imageio_ffmpeg
    with Path(path).open("rb") as source:
        signature = source.read(16)
    if not (signature.startswith(b"\x1aE\xdf\xa3") or
            (signature.startswith(b"RIFF") and signature[8:12] == b"AVI ") or
            signature[4:8] in (b"ftyp", b"moov", b"mdat", b"wide", b"free")):
        raise ValueError("视频需为 MP4/MOV、MKV/WebM 或 AVI 容器")
    reader = imageio_ffmpeg.read_frames(str(path), pix_fmt="rgb24",
        input_params=["-threads", "1", "-protocol_whitelist", "file,pipe", "-format_whitelist", "mov,matroska,webm,avi"],
        output_params=["-threads", "1", "-frames:v", "1201"])
    try:
        meta = next(reader)
        width, height = meta["size"]
        fps = float(meta["fps"])
        if min(width, height) < 2 or width * height > 1920 * 1080 or not np.isfinite(fps) or not 0 < fps <= 120:
            raise ValueError("视频限制：至多 1080p，帧率 0–120 fps")
        frames, size = [], 0
        for data in reader:
            size += len(data)
            if size > MAX_DECODED or len(frames) >= 1200:
                raise ValueError("视频解码超过 96 MiB 或 1200 帧，请截取短片或降低分辨率")
            frames.append(np.frombuffer(data, dtype=np.uint8).reshape(height, width, 3))
        if not frames:
            raise ValueError("视频没有可解码帧")
        return np.stack(frames), fps
    except (RuntimeError, OSError, StopIteration) as exc:
        raise ValueError("视频解码失败，请检查格式及 FFmpeg 安装") from exc
    finally:
        reader.close()


def write_video(path: str | Path, frames: np.ndarray, fps: float, *, preview: bool = False) -> None:
    import imageio_ffmpeg
    h, w = frames.shape[1:3]
    writer = imageio_ffmpeg.write_frames(str(path), (w, h), fps=fps, codec="libx264" if preview else "ffv1",
        pix_fmt_in="rgb24", pix_fmt_out="yuv420p" if preview else "bgr0", macro_block_size=1,
        ffmpeg_timeout=30, output_params=(["-crf", "22", "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2", "-movflags", "+faststart"] if preview else ["-level", "3"])
        + ["-threads", "1"], ffmpeg_log_level="error")
    try:
        writer.send(None)
        for data in frames:
            writer.send(np.ascontiguousarray(data).tobytes())
    except (OSError, RuntimeError) as exc:
        raise ValueError("视频编码失败，请检查 FFmpeg 编码器") from exc
    finally:
        writer.close()
    if not Path(path).is_file() or Path(path).stat().st_size == 0:
        raise ValueError("视频编码未生成结果")


def video_analysis(original: np.ndarray, stego: np.ndarray, fps: float, used_bytes: int = 0) -> dict:
    rows = []
    frame_bits = int(np.prod(original.shape[1:]))
    for index, (a, b) in enumerate(zip(original, stego)):
        difference = a.astype(float) - b.astype(float)
        mse = float(np.mean(difference ** 2))
        rows.append({"frame": index, "time": index / fps, "changed_channels": int(np.count_nonzero(difference)),
                     "mse": mse, "embedded_bits": min(frame_bits, max(0, used_bytes * 8 - index * frame_bits))})
    return {"fps": fps, "frame_count": len(original), "duration": len(original) / fps,
            "width": original.shape[2], "height": original.shape[1], "frames": rows}


def embed_text(cover: str, packet: bytes) -> str:
    if START in cover or END in cover:
        raise ValueError("载体已包含隐写标记，请使用新的普通文本")
    if len(cover.encode('utf-8')) + (len(packet) + HEADER.size) * 24 + 18 > TEXT_OUTPUT:
        raise ValueError('零宽字符扩展后超过 64 MiB，请减少秘密内容')
    bits = np.unpackbits(np.frombuffer(frame(packet), dtype=np.uint8))
    hidden = "".join(ONE if bit else ZERO for bit in bits)
    position = len(cover) // 2
    result = cover[:position] + START + hidden + END + cover[position:]
    return result


def text_slots(text: str) -> np.ndarray:
    start = text.find(START)
    end = text.find(END, start + len(START))
    if start < 0 or end < 0:
        raise ValueError("未找到零宽隐写标记，或不可见字符已被清理")
    hidden = text[start + len(START):end]
    if len(hidden) > (MAX_PACKET + HEADER.size) * 8 or any(c not in (ZERO, ONE) for c in hidden):
        raise ValueError("零宽字符区域损坏或过大")
    return np.fromiter((c == ONE for c in hidden), dtype=np.uint8, count=len(hidden))
