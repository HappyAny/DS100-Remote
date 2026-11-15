"""Measure native draw traffic and USB payload throughput. Does not stream drawings."""

from datetime import datetime, timezone
from pathlib import Path
import argparse
import json
import statistics
import struct
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from ds100.protocol import Client, DRAW_PROFILE, LINK_PROBE
from ds100.windows_scsi import ScsiTransport
from ds100.storage import find_drives

FIELDS = ('magic', 'active', 'epoch', 'loop_passes', 'nonempty_passes', 'points', 'rectangles', 'spans',
          'bitmap_rows', 'pixels', 'encoded_bytes', 'raw_bytes', 'pass_bytes', 'peak_pass_bytes',
          'invalid_rectangles', 'reserved')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--seconds', type=float, default=10)
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT) or not 1 <= args.seconds <= 30:
        parser.error('Use a project output directory and a duration from 1 to 30 seconds.')
    output.mkdir(parents=True, exist_ok=True)
    drives = find_drives()
    if len(drives) != 1:
        raise OSError('Connect one DS100 and close other screen clients.')
    client = Client(ScsiTransport(drives[0]))
    report = {'started_utc': datetime.now(timezone.utc).isoformat(), 'physical_device_test': True,
              'success': False, 'changes_device_acquisition_settings': False, 'samples': [],
              'note': 'Nonempty loop passes are rendering batches. They are not a measurement of LCD refresh or a working 30 fps mirror.'}
    active = False
    try:
        report['info'] = client.info()
        if report['info'].get('build') not in ('background-r5-profile', 'background-r6-controls'):
            raise ValueError('The installed image does not implement the R5 drawing profile.')
        link_times = []
        expected = bytes(i & 255 for i in range(4076))
        for _ in range(30):
            start = time.perf_counter()
            reply = client.request(LINK_PROBE, (4076, 0, 0, 0))
            link_times.append(time.perf_counter() - start)
            if reply != expected:
                raise ValueError('The USB probe payload does not match the reference bytes.')
        report['link_probe'] = {'payload_bytes': 4076, 'responses': 30, 'seconds': link_times,
            'payload_bytes_per_second': 4076 * 30 / sum(link_times), 'median_ms': statistics.median(link_times) * 1000}
        client.request(DRAW_PROFILE, (1, 0, 0, 0)); active = True
        start = time.perf_counter()
        while time.perf_counter() - start < args.seconds:
            data = client.request(DRAW_PROFILE, (2, 0, 0, 0))
            sample = dict(zip(FIELDS, struct.unpack('<16I', data)))
            if sample['magic'] != 0x35505244 or not sample['active']:
                raise ValueError('The drawing profile state is invalid.')
            report['samples'].append({'elapsed': time.perf_counter() - start, **sample})
            time.sleep(0.1)
        data = client.request(DRAW_PROFILE, (0, 0, 0, 0)); active = False
        elapsed = time.perf_counter() - start
        state = dict(zip(FIELDS, struct.unpack('<16I', data)))
        report['summary'] = {**state, 'seconds': elapsed,
            'estimated_stream_bytes_per_second': state['encoded_bytes'] / elapsed,
            'nonempty_render_passes_per_second': state['nonempty_passes'] / elapsed,
            'estimated_bytes_per_nonempty_pass': state['encoded_bytes'] / max(1, state['nonempty_passes'])}
        report['success'] = True
    except Exception as error:
        report.update(error_type=type(error).__name__, error=str(error))
    finally:
        if active:
            try:
                client.request(DRAW_PROFILE, (0, 0, 0, 0))
            except Exception as error:
                report['stop_error'] = str(error)
        client.close()
        report['finished_utc'] = datetime.now(timezone.utc).isoformat()
        (output / 'receipt.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: v for k, v in report.items() if k not in ('samples', 'info')}, ensure_ascii=False, indent=2))
    return 0 if report['success'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
