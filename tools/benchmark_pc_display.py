"""Measure PC decode and visible Qt painting. Do not open a DS100 or USB device."""

from datetime import datetime, timezone
from pathlib import Path
import argparse
import json
import math
import os
import platform
import struct
import sys
import time
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
os.environ['DS100_AUTO_CONNECT'] = '0'

from ds100.drawing import DrawReplay, packet, record, CLEAR, FILL, SPAN, PRESENT, BASELINE
from ds100.protocol import DRAW_STREAM, SCREEN, make_response, parse_response


def summarize_ms(values):
    a = np.asarray(values, dtype=float) * 1000
    return {'median': float(np.median(a)), 'p95': float(np.percentile(a, 95)),
            'maximum': float(a.max())} if len(a) else None


def baseline():
    replay = DrawReplay()
    replay.apply(packet(1, 0, [record(CLEAR, bytes(2)),
                 record(PRESENT, bytes(4), BASELINE)], reset=True))
    return replay


def make_frames(kind, count):
    rng = np.random.default_rng(8675309)
    result, sequence = [], 2
    for number in range(1, count + 1):
        if kind == 'noise-segments':
            records = [record(FILL, struct.pack('<5H', 20, 40, 419, 279, 0))]
            previous = 160
            for x, y in zip(range(20, 420), rng.integers(90, 230, 400)):
                y = int(y)
                records.append(record(FILL, struct.pack('<5H', x, min(previous, y), x, max(previous, y), 0xFFE0)))
                previous = y
        else:
            pixels = rng.integers(0, 65536, (320, 480), dtype=np.uint16)
            records = [record(SPAN, struct.pack('<HHHBB', 0, y, 480, 0, 0) +
                              pixels[y].astype('<u2').tobytes()) for y in range(320)]
        records.append(record(PRESENT, struct.pack('<I', number)))
        groups, group, used = [], [], 20
        for item in records:
            if used + len(item) > 4076:
                groups.append(group)
                group, used = [], 20
            group.append(item)
            used += len(item)
        if group:
            groups.append(group)
        replies = []
        for group in groups:
            data = packet(1, sequence, group)
            replies.append((sequence, make_response(DRAW_STREAM, sequence, data)))
            sequence += len(group)
        result.append(replies)
    return result


def decode_frame(replay, replies):
    frames = []
    for sequence, response in replies:
        frames += replay.apply(parse_response(response, DRAW_STREAM, sequence))
    if len(frames) != 1:
        raise RuntimeError('Each test input must produce exactly one complete frame.')
    return frames[0][1]


def decoder_check(frames):
    replay, times, previous, changed = baseline(), [], None, 0
    started = time.perf_counter()
    for replies in frames:
        before = time.perf_counter()
        pixels = decode_frame(replay, replies)
        times.append(time.perf_counter() - before)
        changed += previous is None or not np.array_equal(previous, pixels)
        previous = pixels
    elapsed = time.perf_counter() - started
    return {'frames': len(frames), 'changed_frames': changed, 'seconds': elapsed,
            'throughput_fps': len(frames) / elapsed, 'decode_crc_ms': summarize_ms(times),
            'mean_wire_bytes_per_frame': sum(len(data) for f in frames for _, data in f) / len(frames),
            'packets_per_frame': len(frames[0]), 'includes': 'CRC, protocol checks, drawing decode and completed RGB565 arrays',
            'excludes': 'Device, USB, input generation and Qt painting'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--seconds', type=float, default=4)
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT) or not 2 <= args.seconds <= 10:
        parser.error('Use a project output directory and a duration from 2 to 10 seconds.')
    output.mkdir(parents=True, exist_ok=False)
    from PySide6.QtCore import QTimer, Qt
    from PySide6.QtWidgets import QApplication
    from ds100.app import MainWindow, ScreenLabel
    from ds100.fonts import configure_fonts

    report = {'started_utc': datetime.now(timezone.utc).isoformat(), 'physical_device_test': False,
              'usb_opened': False, 'input': 'Generated noise test data. Not a captured device stream.',
              'python': sys.version, 'platform': platform.platform(), 'success': False,
              'limits': ['Qt paint events do not prove physical monitor scanout or DWM presentation.',
                         'No USB, firmware, acquisition or hardware FPS is measured.']}
    (output / 'receipt.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    # Generate outside timed sections. Each frame has a distinct noise pattern.
    noise = make_frames('noise-segments', 2048)
    raw = make_frames('raw-noise', 128)
    report['decoder'] = {'noise_segments': decoder_check(noise[:600]), 'raw_full_screen': decoder_check(raw)}

    app = QApplication.instance() or QApplication([])
    configure_fonts()
    window = MainWindow()
    window.auto_connect.setChecked(False)
    window.source.setCurrentIndex(2)
    window.info = {'source': 'simulator', 'capabilities': [], 'build': 'PC benchmark'}
    window.setWindowTitle('DS100 · 电脑显示性能测试 · 生成噪声，未连接设备')
    window.update_controls()
    window.status.setText('生成噪声测试数据 · 仅测试电脑，不访问示波器')
    window.show()
    app.processEvents()

    class ObservedScreen(ScreenLabel):
        def __init__(self):
            super().__init__()
            self.events = []
            self.submitted = {}

        def paintEvent(self, event):
            generation = self.image_generation
            new = generation != self.painted_generation
            before = time.perf_counter()
            super().paintEvent(event)
            if new and self.painted_generation == generation:
                finished = time.perf_counter()
                self.events.append({'generation': generation, 'finished': finished,
                                    'paint_seconds': finished - before,
                                    'submit_to_paint_seconds': finished - self.submitted.get(generation, before)})

    old = window.screen_label
    screen = ObservedScreen()
    parent = old.parentWidget()
    parent.layout().replaceWidget(old, screen)
    old.hide()
    old.deleteLater()
    window.screen_label = screen
    screen.show()
    app.processEvents()
    report['display'] = {'window_pixels': [window.width(), window.height()],
                         'screen_widget_pixels': [screen.width(), screen.height()],
                         'device_pixel_ratio': screen.devicePixelRatioF(),
                         'qt_reported_refresh_hz': window.screen().refreshRate()}
    report['gui'] = []
    rates = [30, 60, None]
    active = {}
    timer = QTimer(window)
    timer.setTimerType(Qt.PreciseTimer)

    def save_report():
        (output / 'receipt.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    def next_case():
        if not rates:
            report.update(success=True, finished_utc=datetime.now(timezone.utc).isoformat())
            save_report()
            window.close()
            app.quit()
            return
        target = rates.pop(0)
        active.clear()
        active.update(target=target, replay=baseline(), index=0, decode=[], present=[],
                      started=time.perf_counter(), cpu_started=time.process_time(),
                      event_start=len(screen.events), painted_start=screen.painted_frames,
                      changed_start=screen.painted_waveform_frames)
        window.status.setText('生成噪声测试 · ' + (f'目标 {target} fps' if target else '测电脑上限') + ' · 未连接示波器')
        timer.start(1 if target else 0)

    def finish_case():
        events = screen.events[active['event_start']:]
        intervals = np.diff([e['finished'] for e in events])
        seconds = active['elapsed']
        entry = {'target_fps': active['target'], 'seconds': seconds, 'submitted_frames': active['index'],
                 'painted_frames': screen.painted_frames - active['painted_start'],
                 'painted_waveform_changed_frames': screen.painted_waveform_frames - active['changed_start'],
                 'decode_crc_ms': summarize_ms(active['decode']), 'submit_ms': summarize_ms(active['present']),
                 'paint_ms': summarize_ms([e['paint_seconds'] for e in events]),
                 'submit_to_paint_ms': summarize_ms([e['submit_to_paint_seconds'] for e in events]),
                 'paint_interval_ms': summarize_ms(intervals), 'cpu_one_core_percent': active['cpu_seconds'] / seconds * 100}
        entry['painted_changed_fps'] = entry['painted_waveform_changed_frames'] / seconds
        entry['paint_coalesced_frames'] = max(0, entry['submitted_frames'] - entry['painted_frames'])
        entry['target_within_5_percent'] = None if not active['target'] else entry['painted_changed_fps'] >= active['target'] * .95
        report['gui'].append(entry)
        window.grab().save(str(output / (f'gui-{active["target"] or "unlimited"}.png')))
        save_report()
        QTimer.singleShot(100, next_case)

    def feed():
        elapsed = time.perf_counter() - active['started']
        if elapsed >= args.seconds or active['index'] == len(noise):
            timer.stop()
            active['elapsed'] = elapsed
            active['cpu_seconds'] = time.process_time() - active['cpu_started']
            QTimer.singleShot(30, finish_case)
            return
        target = active['target']
        if target and elapsed < active['index'] / target:
            return
        before = time.perf_counter()
        pixels = decode_frame(active['replay'], noise[active['index']])
        active['decode'].append(time.perf_counter() - before)
        before = time.perf_counter()
        window.show_pixels(pixels, active['decode'][-1])
        screen.submitted[screen.image_generation] = before
        active['present'].append(time.perf_counter() - before)
        active['index'] += 1

    timer.timeout.connect(feed)
    QTimer.singleShot(100, next_case)
    app.exec()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['success'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
