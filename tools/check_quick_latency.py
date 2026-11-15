"""Measure and restore one-step M combinations on the connected R9."""

from pathlib import Path
from datetime import datetime, timezone
import argparse
import json
import sys
import time
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from ds100.protocol import Client
from ds100.quick_actions import execute_action
from ds100.storage import find_drives
from ds100.windows_scsi import ScsiTransport
from probe_remote import save_pixels


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    folder = args.output.resolve()
    if not folder.is_relative_to(ROOT):
        parser.error('Use a project output directory.')
    folder.mkdir(parents=True, exist_ok=False)
    drives = find_drives()
    if len(drives) != 1:
        raise OSError('Connect one DS100 and close other clients.')
    client = Client(ScsiTransport(drives[0]))
    report = {'created_utc': datetime.now(timezone.utc).isoformat(), 'physical_device_test': True,
              'success': False, 'cases': []}

    def save():
        (folder / 'receipt.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    def capture():
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            pixels = client.screen()
            if pixels is not None:
                return pixels
        raise RuntimeError('No complete frame arrived.')

    try:
        report['info'] = client.info()
        if report['info'].get('build') != 'background-r9-bounded-follow':
            raise ValueError('The test requires the installed R9 build.')
        before = capture()
        save_pixels(folder / 'before.png', before)
        for forward, reverse, roi in (('left', 'right', (144, 161, 421, 478)),
                                      ('down', 'up', (179, 197, 421, 478))):
            y0, y1, x0, x1 = roi
            case = {'forward': forward, 'reverse': reverse}
            report['cases'].append(case)
            save()
            start = time.monotonic()
            snapshots = []
            execute_action(client, forward, on_frame=lambda pixels, _: snapshots.append(pixels))
            case['gesture_seconds'] = time.monotonic() - start
            after = snapshots[-1] if snapshots else capture()
            # Find a completed image containing the new displayed position.
            deadline = time.monotonic() + 2
            while np.array_equal(before[y0:y1, x0:x1], after[y0:y1, x0:x1]) and time.monotonic() < deadline:
                after = capture()
            case['visible_seconds'] = time.monotonic() - start
            case['position_pixels_changed'] = bool(np.any(before[y0:y1, x0:x1] != after[y0:y1, x0:x1]))
            save_pixels(folder / (forward + '.png'), after)
            if not case['position_pixels_changed']:
                raise RuntimeError('The position readout did not change; do not issue an unverified reverse step.')
            execute_action(client, reverse)
            restored = capture()
            deadline = time.monotonic() + 2
            while not np.array_equal(before[y0:y1, x0:x1], restored[y0:y1, x0:x1]) and time.monotonic() < deadline:
                restored = capture()
            case['readout_restored'] = bool(np.array_equal(before[y0:y1, x0:x1], restored[y0:y1, x0:x1]))
            case['planned_menu_baselines'] = client.menu_resets
            case['resyncs'] = client.draw_session.resyncs
            save_pixels(folder / (reverse + '-restored.png'), restored)
            save()
            if not case['readout_restored']:
                raise RuntimeError('The reverse gesture did not restore the position readout.')
            before = restored
        report['success'] = all(case['readout_restored'] and case['planned_menu_baselines'] == 0 for case in report['cases'])
    except Exception as error:
        report.update(error=str(error), error_type=type(error).__name__)
    finally:
        client.close()
        report['finished_utc'] = datetime.now(timezone.utc).isoformat()
        save()
    print(json.dumps({k:v for k,v in report.items() if k != 'info'}, ensure_ascii=False, indent=2))
    return 0 if report['success'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
