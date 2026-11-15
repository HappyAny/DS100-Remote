"""Check held-M previews and a bounded continuous pan; restore the position."""

from pathlib import Path
from datetime import datetime, timezone
from threading import Event, Timer
import argparse
import json
import struct
import sys
import time
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from ds100.protocol import Client
from ds100.quick_actions import execute_action, execute_motion
from ds100.windows_scsi import ScsiTransport
from ds100.storage import find_drives
from probe_remote import save_pixels


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--show-app', action='store_true')
    args = parser.parse_args()
    folder = args.output.resolve()
    if not folder.is_relative_to(ROOT):
        parser.error('Use a project output directory.')
    folder.mkdir(parents=True, exist_ok=False)
    report = {'created_utc': datetime.now(timezone.utc).isoformat(), 'physical_device_test': True,
              'success': False, 'input_packets': [], 'firmware_changed': False}

    def save():
        (folder / 'receipt.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    class Audited(ScsiTransport):
        def exchange(self, cdb):
            reply = super().exchange(cdb)
            if cdb[1] == 8:
                keys, a, b, _ = struct.unpack_from('<4H', cdb, 8)
                report['input_packets'].append([keys, a if a < 32768 else a-65536, b if b < 32768 else b-65536])
            return reply

    drives = find_drives()
    if len(drives) != 1:
        raise OSError('Close other clients and connect one DS100.')
    client = Client(Audited(drives[0]))
    info = None
    previews = []
    client.on_menu_preview = lambda pixels: previews.append((time.monotonic(), pixels))
    client.on_input_frame = lambda pixels, _: previews.append((time.monotonic(), pixels))
    navigation_steps = 0

    def capture():
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            pixels = client.screen()
            if pixels is not None:
                previews.append((time.monotonic(), pixels))
                return pixels
        raise RuntimeError('No complete screen arrived.')

    def readout(image):
        return image[144:161, 421:478]

    try:
        save()
        info = client.info(); report['info'] = info
        if info.get('build') != 'background-r9-bounded-follow':
            raise ValueError('This check requires the installed R9 build.')
        before = capture(); save_pixels(folder/'original-sidebar.png', before)
        reference = np.asarray(Image.open(ROOT/'evidence/live-device/r9-m-protected-latency-v2/before.png').convert('RGB'))
        template = np.all(reference[126:142,420:480] > 200, axis=2)

        def primary_sidebar(image):
            patch = image[126:142,420:480]
            white = ((patch >> 11) >= 25) & (((patch >> 5) & 63) >= 50) & ((patch & 31) >= 25)
            return np.mean(white != template) < .02

        while not primary_sidebar(before) and navigation_steps < 16:
            client.queue_input(0, 1, 0); client.flush_inputs()
            navigation_steps += 1
            before = capture()
        report['navigation_steps_to_primary_sidebar'] = navigation_steps
        if not primary_sidebar(before):
            raise RuntimeError('The position readouts are not visible; do not assume a fixed sidebar page.')
        save_pixels(folder/'before.png', before)
        report['phase'] = 'held-M'; save()
        client.queue_input(4); client.flush_inputs()
        client.screen()
        changed_at = time.monotonic()
        client.queue_input(4, 1, 0); client.flush_inputs()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            client.queue_input(4)  # renew only the held input, no extra detent
            pixels = client.screen()
            if pixels is not None: previews.append((time.monotonic(), pixels))
            if previews and not np.array_equal(readout(previews[-1][1]), readout(before)):
                break
        else:
            raise RuntimeError('The held-M position preview did not update.')
        report['held_M_preview_seconds'] = previews[-1][0] - changed_at
        report['preview_arrived_while_M_held'] = client._input_keys == 4
        save_pixels(folder/'held-M-preview.png', previews[-1][1])
        client.queue_input(4, -1, 0); client.flush_inputs()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            client.queue_input(4)
            pixels = client.screen()
            if pixels is not None: previews.append((time.monotonic(), pixels))
            if previews and np.array_equal(readout(previews[-1][1]), readout(before)):
                break
        client.queue_input(0); client.flush_inputs()
        restored = capture()
        report['held_M_readout_restored'] = bool(np.array_equal(readout(restored), readout(before)))
        if not report['held_M_readout_restored']:
            raise RuntimeError('The held-M reverse step did not restore the readout.')
        report['phase'] = 'offset-preservation'; save()
        execute_action(client, 'left'); execute_action(client, 'down')
        preset = capture(); save_pixels(folder/'offsets-before-range.png', preset)
        for name in ('time_up', 'time_down', 'range_up', 'range_down'):
            execute_action(client, name)
            shot = capture(); save_pixels(folder/(name+'.png'), shot)
        report['offset_readouts_after_range_roundtrip'] = bool(np.array_equal(preset[144:197,421:478], shot[144:197,421:478]))
        execute_action(client, 'right'); execute_action(client, 'up')
        restored = capture()
        report['offset_readouts_restored'] = bool(np.array_equal(before[144:197,421:478], restored[144:197,421:478]))
        report['phase'] = 'continuous-left'; save()
        begin = len(report['input_packets'])
        stopped = Event(); timer = Timer(.45, stopped.set); timer.start()
        started = time.monotonic()
        try:
            execute_motion(client, 'left', stopped)
        finally:
            stopped.set(); timer.cancel()
        report['continuous_seconds'] = time.monotonic() - started
        packets = report['input_packets'][begin:]
        detents = sum(item[1] for item in packets)
        report['continuous_detents'] = detents
        report['continuous_released'] = client._input_keys == 0
        after = capture(); save_pixels(folder/'continuous-left.png', after)
        for _ in range(detents):
            execute_action(client, 'right')
        restored = capture(); save_pixels(folder/'restored.png', restored)
        report['continuous_readout_restored'] = bool(np.array_equal(readout(restored), readout(before)))
        report['success'] = (report['preview_arrived_while_M_held'] and report['continuous_released'] and
                             report['continuous_readout_restored'] and detents > 1 and
                             report['offset_readouts_after_range_roundtrip'] and report['offset_readouts_restored'])
        report['phase'] = 'checks-finished'; save()
    except Exception as error:
        report.update(error=str(error), error_type=type(error).__name__); save()
    finally:
        try:
            client.queue_input(0); client.flush_inputs()
            for _ in range(navigation_steps):
                client.queue_input(0, -1, 0); client.flush_inputs()
        except Exception as error:
            report['release_error'] = str(error)
            report['success'] = False
            save()
    if args.show_app and info is not None and 'release_error' not in report:
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication
        from ds100.app import MainWindow
        app = QApplication.instance() or QApplication([])
        window = MainWindow(); window.drive = drives[0]
        window.experimental_connected((client, info)); window.show()
        if client.draw_session is not None and client.draw_session.last_pixels is not None:
            window.show_pixels(client.draw_session.last_pixels, 0)
        window.mirror.setChecked(True)
        observer = QTimer(window)

        def observe():
            report.update(mirror_frames=window.mirror_count, mirror_active=window.mirror.isChecked(),
                          desktop_status=window.status.text(), raw_M_held=bool(window.input_panel.mask & 4))
            window.grab().save(str(folder/'application.png'))
            save()

        observer.timeout.connect(observe); observer.start(1000)
        return app.exec()
    client.close(); save()
    print(json.dumps({k:v for k,v in report.items() if k not in ('info','input_packets')}, ensure_ascii=False, indent=2))
    return 0 if report['success'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
