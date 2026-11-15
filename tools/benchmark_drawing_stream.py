"""Measure delivered R7 frames and waveform changes. Do not count empty polls."""

from datetime import datetime, timezone
from pathlib import Path
import argparse
import json
import statistics
import struct
import sys
import time
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from ds100.protocol import Client, DRAW_STREAM
from ds100.storage import find_drives
from ds100.windows_scsi import ScsiTransport
from probe_remote import save_pixels


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--seconds', type=float, default=10)
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT) or not 2 <= args.seconds <= 30:
        parser.error('Use a project output directory and a duration from 2 to 30 seconds.')
    output.mkdir(parents=True, exist_ok=False)
    drives = find_drives()
    if len(drives) != 1:
        raise OSError('Connect one DS100 and close other screen clients.')

    class Measured(ScsiTransport):
        requests = 0
        received_bytes = 0

        def exchange(self, cdb):
            reply = super().exchange(cdb)
            self.requests += 1
            self.received_bytes += len(reply)
            return reply

    transport = Measured(drives[0])
    client = Client(transport)
    report = {'started_utc': datetime.now(timezone.utc).isoformat(), 'physical_device_test': True,
              'scope': 'USB, decode, and delivered arrays. Qt painting is measured separately.',
              'success': False, 'frame_times': []}
    try:
        report['info'] = client.info()
        if report['info'].get('build') not in ('background-r7-draw-stream', 'background-r8-hardware-follow', 'background-r9-bounded-follow'):
            raise ValueError('The connected device does not run a reviewed drawing-stream build.')
        r7 = report['info']['build'] == 'background-r7-draw-stream'
        if r7:
            report['redraw_before_pointer_period_saved'] = struct.unpack('<3I', client.request(DRAW_STREAM, (6, 0, 0, 0)))
        else:
            report['redraw_policy'] = 'Native device timing; no redraw timer hook or write.'
        baseline_start = time.perf_counter()
        previous = client.screen()
        report['baseline_seconds'] = time.perf_counter() - baseline_start
        if client.screen_mode != 'draw-stream':
            raise ValueError('The drawing queue is unavailable.')
        if r7:
            report['redraw_active_pointer_period_saved'] = struct.unpack('<3I', client.request(DRAW_STREAM, (6, 0, 0, 0)))
        save_pixels(output / 'first.png', previous)
        frames = changed = wave_changed = 0
        start = time.perf_counter()
        requests, received = transport.requests, transport.received_bytes
        presentations = client.draw_session.presentations
        resyncs = client.draw_session.resyncs
        while time.perf_counter() - start < args.seconds:
            pixels = client.screen(cancelled=lambda: time.perf_counter() - start > args.seconds + 5)
            if pixels is None:
                continue
            frames += 1
            changed += int(not np.array_equal(previous, pixels))
            wave_changed += int(not np.array_equal(previous[24:292, 16:410], pixels[24:292, 16:410]))
            report['frame_times'].append(time.perf_counter() - start)
            previous = pixels
        elapsed = time.perf_counter() - start
        intervals = np.diff(report['frame_times'])
        report.update(success=True, seconds=elapsed, delivered_frames=frames,
            changed_frames=changed, waveform_changed_frames=wave_changed,
            device_presentations=client.draw_session.presentations - presentations,
            delivered_fps=frames / elapsed, changed_fps=changed / elapsed,
            waveform_changed_fps=wave_changed / elapsed,
            requests=transport.requests - requests, received_bytes=transport.received_bytes - received,
            resyncs=client.draw_session.resyncs - resyncs,
            median_interval_ms=float(np.median(intervals) * 1000) if len(intervals) else None,
            p95_interval_ms=float(np.percentile(intervals, 95) * 1000) if len(intervals) else None)
        report['transport_30_fps_target_met'] = report['waveform_changed_fps'] >= 30 and not report['resyncs']
        save_pixels(output / 'last.png', previous)
        client.request(DRAW_STREAM, (0, 0, 0, 0))
        if r7:
            report['redraw_after_pointer_period_saved'] = struct.unpack('<3I', client.request(DRAW_STREAM, (6, 0, 0, 0)))
    except Exception as error:
        report.update(error_type=type(error).__name__, error=str(error))
    finally:
        client.close()
        report['finished_utc'] = datetime.now(timezone.utc).isoformat()
        (output / 'receipt.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: v for k, v in report.items() if k not in ('info', 'frame_times')}, ensure_ascii=False, indent=2))
    return 0 if report['success'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
