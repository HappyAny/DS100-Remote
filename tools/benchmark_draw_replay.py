"""Measure PC drawing replay with generated operations. This is not a device FPS test."""

from pathlib import Path
import json
import statistics
import struct
import sys
import time
import math

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from ds100.drawing import DrawReplay, packet, record, CLEAR, FILL, PRESENT, BASELINE


def main():
    replay = DrawReplay()
    replay.apply(packet(1, 0, [record(CLEAR, b'\0\0'), record(PRESENT, bytes(4), BASELINE)], reset=True))
    sequence, timings, sizes = 2, [], []
    for frame in range(1, 301):
        records = [record(FILL, struct.pack('<5H', 20, 40, 419, 279, 0))]
        previous = 160
        for x in range(20, 420):
            y = 160 + round(70 * math.sin(x / 35 + frame / 20))
            records.append(record(FILL, struct.pack('<5H', x, min(previous, y), x, max(previous, y), 0xFFE0)))
            previous = y
        records.append(record(PRESENT, struct.pack('<I', frame)))
        data = packet(1, sequence, records)
        sequence += len(records)
        started = time.perf_counter()
        shown = replay.apply(data)
        timings.append(time.perf_counter() - started)
        sizes.append(len(data))
        if shown[0][0] != frame:
            raise RuntimeError('The replay did not present the expected frame.')
    sorted_times = sorted(timings)
    report = {'physical_device_test': False, 'input': 'Generated clear and 400 vertical waveform segments per frame.',
        'frames': len(timings), 'median_ms': statistics.median(timings) * 1000,
        'p95_ms': sorted_times[math.ceil(len(timings) * .95) - 1] * 1000,
        'maximum_ms': max(timings) * 1000, 'bytes_per_frame': statistics.mean(sizes),
        'payload_bytes_per_second_at_30_fps': statistics.mean(sizes) * 30,
        'target_frame_ms': 1000 / 30,
        'note': 'Checks only this PC decoder workload. Excludes device rendering, hook overhead, USB, GUI painting, and baseline repaint.'}
    folder = ROOT / 'evidence/software-tests'
    (folder / 'drawing-replay-benchmark.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
