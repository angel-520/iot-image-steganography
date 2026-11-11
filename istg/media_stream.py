"""Bounded-memory WAV and FFmpeg frame processing for large carriers."""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import math
import wave

import numpy as np
from . import multimodal as mm
from . import media_limits as limits


class Units:
    """Expand only small aligned chunks of the packet into coefficient values."""
    def __init__(self, packet, depth):
        self.data = mm.frame(packet)
        self.depth = depth
        self.offset = self.position = 0
        self.pending = np.empty(0, dtype=np.int32)
        self.used = 0

    def take(self, count):
        pieces = []
        while count:
            if self.position == len(self.pending):
                if self.offset == len(self.data):
                    break
                chunk = self.data[self.offset:self.offset + self.depth * 8192]
                self.offset += len(chunk)
                bits = np.unpackbits(np.frombuffer(chunk, dtype=np.uint8))
                bits = np.pad(bits, (0, (-len(bits)) % self.depth))
                weights = 1 << np.arange(self.depth - 1, -1, -1)
                self.pending = (bits.reshape(-1, self.depth) @ weights).astype(np.int32)
                self.position = 0
            size = min(count, len(self.pending) - self.position)
            pieces.append(self.pending[self.position:self.position + size])
            self.position += size
            self.used += size
            count -= size
        return np.concatenate(pieces) if pieces else np.empty(0, dtype=np.int32)


def modify(values, units):
    result = values.copy()
    offset = 0
    while offset < result.size:
        values_to_write = units.take(min(65536, result.size - offset))
        if not len(values_to_write):
            break
        count = len(values_to_write)
        result[offset:offset + count] = ((result[offset:offset + count].astype(np.int32) & ~((1 << units.depth) - 1))
                                          | values_to_write).astype(result.dtype)
        offset += count
    return result


def extract(chunks, depth=1, *, verify=True, expected=None):
    """Read a bounded frame, carrying unaligned bits between carrier blocks."""
    header, payload = bytearray(), bytearray()
    carry = np.empty(0, dtype=np.uint8)
    length, digest = expected, None
    try:
        for chunk in chunks:
            flat = chunk.reshape(-1)
            for start in range(0, len(flat), 65536):
                values = flat[start:start + 65536].astype(np.int32)
                bits = ((values[:, None] >> np.arange(depth - 1, -1, -1)) & 1).astype(np.uint8).reshape(-1)
                if len(carry):
                    bits = np.concatenate((carry, bits))
                complete = len(bits) // 8 * 8
                carry = bits[complete:].copy()
                data = np.packbits(bits[:complete]).tobytes()
                if len(header) < mm.HEADER.size:
                    count = min(mm.HEADER.size - len(header), len(data))
                    header.extend(data[:count]); data = data[count:]
                    if len(header) == mm.HEADER.size and expected is None:
                        magic, length, digest = mm.HEADER.unpack(header)
                        if magic != b'MM01' or length > limits.PACKET_BYTES:
                            raise ValueError('未检测到有效隐写帧；请确认算法及嵌入位数与嵌入时相同')
                if len(header) == mm.HEADER.size:
                    if type(length) is not int or not 0 <= length <= limits.PACKET_BYTES:
                        raise ValueError('隐写数据长度超限')
                    payload.extend(data[:length - len(payload)])
                    if len(payload) == length:
                        result = bytes(payload)
                        if verify and sha256(result).digest() != digest:
                            raise ValueError('隐写数据 SHA-256 校验失败，载体可能经过压缩或修改')
                        return result
        if verify:
            raise ValueError('载体截断、容量不足或未检测到完整隐写数据')
        return bytes(payload)
    finally:
        if hasattr(chunks, 'close'):
            chunks.close()


def audio_info(path):
    try:
        with wave.open(str(path), 'rb') as source:
            channels, rate, count = source.getnchannels(), source.getframerate(), source.getnframes()
            if source.getcomptype() != 'NONE' or source.getsampwidth() != 2:
                raise ValueError('音频载体仅支持 16-bit PCM WAV')
            if channels not in (1, 2) or not 8000 <= rate <= 192000 or not count:
                raise ValueError('WAV 需为单/双声道、8–192 kHz 且包含采样数据')
            if count * channels * 2 > limits.RESULT_BYTES or count * channels * 2 > Path(path).stat().st_size:
                raise ValueError('WAV 数据截断或声明长度超限')
            return {'channels': channels, 'rate': rate, 'count': count}
    except (wave.Error, EOFError, OSError) as exc:
        raise ValueError('无法读取 PCM WAV 音频') from exc


def audio_chunks(path):
    info = audio_info(path)
    with wave.open(str(path), 'rb') as source:
        remaining = info['count']
        while remaining:
            count = min(32768, remaining)
            raw = source.readframes(count)
            if len(raw) != count * info['channels'] * 2:
                raise ValueError('WAV 采样数据截断')
            remaining -= count
            yield np.frombuffer(raw, dtype='<i2').reshape(-1, info['channels'])


def audio_slot_chunks(path, algorithm):
    for samples in audio_chunks(path):
        yield mm.audio_slots(samples, algorithm)


def audio_embed_block(samples, units, algorithm):
    modified = modify(mm.audio_slots(samples, algorithm), units)
    if algorithm == 'audio_lsb':
        return modified.astype(np.int16).reshape(samples.shape)
    result = samples.copy()
    count = len(samples) // 2
    a, b = samples[:count * 2:2].astype(np.int32), samples[1:count * 2:2].astype(np.int32)
    low = (a + b) // 2
    difference = modified.reshape(a.shape)
    step = 1 << units.depth
    difference[difference > 65535] -= step
    difference[difference < -65535] += step
    ceiling, floor = (difference + 1) // 2, difference // 2
    low = np.clip(low, np.maximum(-32768 - ceiling, -32768 + floor),
                  np.minimum(32767 - ceiling, 32767 + floor))
    result[:count * 2:2], result[1:count * 2:2] = low + ceiling, low - floor
    return result


def embed_audio(path, output, packet, algorithm, depth):
    info = audio_info(path)
    slots = (info['count'] if algorithm == 'audio_lsb' else info['count'] // 2) * info['channels']
    if (len(packet) + mm.HEADER.size) * 8 > slots * depth:
        raise ValueError('容量不足，请增加嵌入位数、选择更大载体或压缩秘密文件')
    units = Units(packet, depth)
    indices = np.linspace(0, info['count'] - 1, min(1200, info['count']), dtype=int)
    original_wave, stego_wave = [], []
    first_a, first_b = None, None
    energy = error = 0.0
    changed = position = 0
    with wave.open(str(output), 'wb') as writer:
        writer.setnchannels(info['channels']); writer.setsampwidth(2); writer.setframerate(info['rate'])
        for a in audio_chunks(path):
            b = audio_embed_block(a, units, algorithm)
            af, bf = a.astype(np.float64), b.astype(np.float64)
            energy += float(np.sum(af * af)); error += float(np.sum((af - bf) ** 2))
            changed += int(np.count_nonzero(a != b))
            chosen = indices[(indices >= position) & (indices < position + len(a))] - position
            original_wave.extend(a[chosen, 0].tolist()); stego_wave.extend(b[chosen, 0].tolist())
            if first_a is None:
                first_a, first_b = a[:16384].copy(), b[:16384].copy()
            position += len(a)
            writer.writeframes(b.astype('<i2').tobytes())
    analysis = mm.audio_analysis(first_a, first_b, info['rate'])
    analysis.update(duration=info['count'] / info['rate'], mse=error / (info['count'] * info['channels']),
        snr_db=10 * math.log10(energy / error) if energy and error else None,
        identical=error == 0, changed_samples=changed, time=(indices / info['rate']).tolist(),
        original_wave=original_wave, stego_wave=stego_wave, bit_depth=depth)
    return analysis


def video_reader(path):
    import imageio_ffmpeg
    with Path(path).open('rb') as source:
        signature = source.read(16)
    if not (signature.startswith(b'\x1aE\xdf\xa3') or
            signature.startswith(b'RIFF') and signature[8:12] == b'AVI ' or
            signature[4:8] in (b'ftyp', b'moov', b'mdat', b'wide', b'free')):
        raise ValueError('视频需为 MP4/MOV、MKV/WebM 或 AVI 容器')
    reader = imageio_ffmpeg.read_frames(str(path), pix_fmt='rgb24',
        input_params=['-threads', '1', '-protocol_whitelist', 'file,pipe', '-format_whitelist', 'mov,matroska,webm,avi'],
        output_params=['-threads', '1', '-frames:v', str(limits.VIDEO_FRAMES + 1)])
    process = None
    try:
        meta = next(reader)
        # imageio-ffmpeg 0.6/0.7 skips closing stdin/stdout when FFmpeg has already
        # exited. Retain these optional handles while its generator is still live.
        frame = getattr(reader, 'gi_frame', None)
        process = frame.f_locals.get('process') if frame else None
        yield meta
        yield from reader
    finally:
        reader.close()
        if process is not None:
            for pipe in (process.stdin, process.stdout):
                if pipe is not None and not pipe.closed:
                    pipe.close()
            process.wait(timeout=5)


def checked_meta(reader):
    meta = next(reader)
    width, height = meta['size']; fps = float(meta['fps'])
    if min(width, height) < 2 or width * height > limits.VIDEO_PIXELS or not math.isfinite(fps) or not 0 < fps <= limits.VIDEO_FPS:
        raise ValueError('视频限制：最高 4K（8294400 像素）、240 fps')
    if float(meta.get('duration') or 0) > limits.VIDEO_SECONDS + 1:
        raise ValueError('视频时长超过 30 分钟，请先裁剪')
    return {'width': width, 'height': height, 'fps': fps}


def video_info(path, *, count=False):
    reader = video_reader(path)
    try:
        info = checked_meta(reader)
        if count:
            n = 0
            for _ in checked_frames(reader, info):
                n += 1
            info['count'] = n
        return info
    except (RuntimeError, OSError, StopIteration) as exc:
        raise ValueError('视频解码失败，请检查格式及 FFmpeg') from exc
    finally:
        reader.close()


def checked_frames(reader, info):
    width, height, fps = info['width'], info['height'], info['fps']
    count = 0
    for raw in reader:
        count += 1
        if (count > limits.VIDEO_FRAMES or count / fps > limits.VIDEO_SECONDS + 1 / fps or
                count * width * height * 3 > limits.VIDEO_WORK_BYTES):
            raise ValueError('视频处理上限：100000 帧、30 分钟、累计解码 64 GiB；请截取片段')
        if len(raw) != width * height * 3:
            raise ValueError('视频帧数据截断')
        yield np.frombuffer(raw, dtype=np.uint8).reshape(height, width, 3)
    if not count:
        raise ValueError('视频没有可解码帧')


def video_chunks(path):
    reader = video_reader(path)
    try:
        yield from checked_frames(reader, checked_meta(reader))
    except (RuntimeError, OSError, StopIteration) as exc:
        raise ValueError('视频解码失败') from exc
    finally:
        reader.close()


def video_writer(path, info, *, preview=False):
    import imageio_ffmpeg
    writer = imageio_ffmpeg.write_frames(str(path), (info['width'], info['height']), fps=info['fps'],
        codec='libx264' if preview else 'ffv1', pix_fmt_in='rgb24', pix_fmt_out='yuv420p' if preview else 'bgr0',
        macro_block_size=1, ffmpeg_timeout=60, ffmpeg_log_level='error',
        output_params=(['-crf', '24', '-preset', 'veryfast', '-vf', 'pad=ceil(iw/2)*2:ceil(ih/2)*2', '-movflags', '+faststart'] if preview else ['-level', '3']) + ['-threads', '1'])
    writer.send(None)
    return writer


def embed_video(path, output, preview, packet, depth, info, thumbnail):
    from PIL import Image
    count = info['count']
    slots_per_frame = info['width'] * info['height'] * 3
    if slots_per_frame * count * depth < (len(packet) + mm.HEADER.size) * 8:
        raise ValueError('视频容量不足，请使用更多帧、更高分辨率或增加嵌入位数')
    step = max(1, math.ceil(info['fps'] / 12))
    scale = min(1, 960 / info['width'], 540 / info['height'])
    small = {'width': max(2, int(info['width'] * scale)), 'height': max(2, int(info['height'] * scale)), 'fps': info['fps'] / step}
    writer = video_writer(output, info)
    player = video_writer(preview, small, preview=True)
    units = Units(packet, depth)
    digest = sha256()
    rows, thumbs = [], []
    selected = set(np.linspace(0, count - 1, min(8, count), dtype=int).tolist())
    stride = max(1, math.ceil(count / 2000))
    chunks = video_chunks(path)
    try:
        for index, a in enumerate(chunks):
            b = modify(a.reshape(-1), units).reshape(a.shape)
            raw = b.tobytes(); writer.send(raw); digest.update(raw)
            if index % step == 0:
                player.send(np.asarray(Image.fromarray(b).resize((small['width'], small['height']))).tobytes())
            if index % stride == 0 or index == count - 1 or index in selected:
                diff = a.astype(np.int16) - b.astype(np.int16)
                rows.append({'frame': index, 'time': index / info['fps'], 'changed_channels': int(np.count_nonzero(diff)),
                    'mse': float(np.mean(diff.astype(np.float64) ** 2)),
                    'embedded_bits': min(slots_per_frame * depth, max(0, len(units.data) * 8 - index * slots_per_frame * depth))})
                if index in selected:
                    thumbs.append(thumbnail(index, a, np.max(np.abs(diff), axis=2), info['fps']))
            if index % 32 == 0 and output.exists() and output.stat().st_size > limits.RESULT_BYTES:
                raise ValueError('FFV1 无损主文件超过 1 GiB，请缩短视频或降低分辨率')
    finally:
        chunks.close(); writer.close(); player.close()
    if output.stat().st_size > limits.RESULT_BYTES or preview.stat().st_size > limits.RESULT_BYTES:
        raise ValueError('视频输出超过 1 GiB，请缩短视频或降低分辨率')
    decoded_hash = sha256()
    for frame in video_chunks(output):
        decoded_hash.update(frame.tobytes())
    if decoded_hash.digest() != digest.digest():
        raise ValueError('FFV1 无损逐帧校验失败，未发布结果')
    return {**info, 'frame_count': count, 'duration': count / info['fps'], 'frames': rows,
            'analysis_stride': stride, 'bit_depth': depth}, thumbs
