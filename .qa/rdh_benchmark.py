"""Reproducible RDH capacity, fidelity and 1080p/4K timing report (no private data)."""
from __future__ import annotations

from io import BytesIO
from pathlib import Path
from time import perf_counter
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from PIL import Image
from skimage.data import astronaut

import istg
from istg import rdh


def main():
    rng = np.random.default_rng(20261115)
    photo = astronaut()
    gray = np.repeat(np.tile(np.arange(256, dtype=np.uint8), (256, 1))[:, :, None], 3, axis=2)
    flat = np.full((512, 768, 3), 102, np.uint8)
    flat[120:250, 180:400] = rng.integers(0, 256, (130, 220, 3), dtype=np.uint8)
    cases = [
        ('random_rgb', rng.integers(0, 256, (512, 768, 3), dtype=np.uint8)),
        ('natural_photo', photo), ('monochrome', np.full((512, 512, 3), 128, np.uint8)),
        ('grayscale_rgb', gray), ('flat_regions', flat),
        ('boundary_0_255', rng.choice(np.array([0, 255], np.uint8), (512, 512, 3))),
        ('small_8x8', np.zeros((8, 8, 3), np.uint8)),
        ('thin_2x2048', np.zeros((2, 2048, 3), np.uint8)),
        ('natural_resized_1920x1080', np.array(Image.fromarray(photo).resize((1920, 1080), Image.Resampling.LANCZOS))),
        ('natural_resized_3840x2160', np.array(Image.fromarray(photo).resize((3840, 2160), Image.Resampling.LANCZOS))),
    ]
    raw = 'RDH 中文 English !@# 0123456789\n'.encode('utf-8')
    packet = istg.pack_istg('text', raw.decode('utf-8'))
    rows = []
    for name, pixels in cases:
        buffer = BytesIO(); Image.fromarray(pixels).save(buffer, format='PNG'); source = buffer.getvalue()
        started = perf_counter()
        cap = rdh.capacity(source, len(packet), len(raw))
        row = {'name': name, 'width': pixels.shape[1], 'height': pixels.shape[0], 'source_bytes': len(source),
               'packet_bytes': len(packet), 'secret_bytes': len(raw), 'capacity': cap}
        if not cap['fits']:
            try: rdh.embed_image(source, packet, secret_bytes=len(raw))
            except rdh.RDHError as exc: row.update(status='rejected', reason=exc.code)
            else: raise AssertionError('Insufficient carrier unexpectedly succeeded')
        else:
            embedded = rdh.embed_image(source, packet, secret_bytes=len(raw))
            restored = rdh.extract_image(embedded['stego'])
            assert restored['packet'] == packet
            with Image.open(BytesIO(restored['restored'])) as image:
                np.testing.assert_array_equal(np.array(image), pixels)
            row.update(status='passed', metrics=embedded['metrics'], extraction_seconds=restored['metrics']['extraction_seconds'])
        row['benchmark_seconds'] = perf_counter() - started
        rows.append(row)
        print(json.dumps({'name': name, 'status': row['status'], 'capacity_bytes': cap['secret_capacity_bytes'],
                          'seconds': round(row['benchmark_seconds'], 3)}, ensure_ascii=True), flush=True)
    report = {'algorithm': rdh.ALGORITHM, 'seed': 20261115, 'text': raw.decode('utf-8'),
              'note': 'HD cases resize the same NASA astronaut photo; they are throughput fixtures, not native-resolution photographs.',
              'rows': rows}
    output = Path(__file__).with_name('rdh-benchmark.json')
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Report: ' + str(output), flush=True)


if __name__ == '__main__': main()
