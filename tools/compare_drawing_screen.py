"""Compare R7 replay with the stock LCD reader while the GUI is held still."""

from datetime import datetime, timezone
from pathlib import Path
import argparse
import json
import struct
import sys
import time
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from ds100.protocol import Client, DRAW_STREAM, SCREEN_READ, INFO, parse_tile
from ds100.storage import find_drives
from ds100.windows_scsi import ScsiTransport
from ds100.drawing import PACKET
from probe_remote import save_pixels


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--key', choices=('P', 'A', 'M', 'E'))
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT):
        parser.error('Use a project output directory.')
    output.mkdir(parents=True, exist_ok=False)
    drives = find_drives()
    if len(drives) != 1:
        raise OSError('Connect one DS100 and close other screen clients.')
    client = Client(ScsiTransport(drives[0]))
    report = {'started_utc': datetime.now(timezone.utc).isoformat(), 'physical_device_test': True,
              'reference': 'Stock LCD read-point function, with the GUI held and lease renewed.',
              'success': False, 'key': args.key}
    def phase(name):
        report['phase'] = name
        (output / 'receipt.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    phase('opening')
    frozen = False
    try:
        phase('info')
        report['info'] = client.info()
        if report['info'].get('build') not in ('background-r7-draw-stream', 'background-r8-hardware-follow', 'background-r9-bounded-follow', 'background-r10-input-ready', 'background-r11-input-ack'):
            raise ValueError('The connected device does not run a reviewed drawing-stream build.')
        phase('baseline')
        client.screen()
        if client.screen_mode != 'draw-stream':
            raise ValueError('The drawing queue is unavailable.')
        session = client.draw_session
        if args.key:
            phase('key-down')
            client.queue_input({'P': 1, 'A': 2, 'M': 4, 'E': 8}[args.key]); client.flush_inputs()
            # Keep draining the bounded stream while the key is held. Pausing
            # the receiver here can overflow a large menu repaint.
            deadline = time.monotonic() + .10
            phase('key-held-draining')
            while time.monotonic() < deadline:
                if client.screen_mode == 'menu-readback':
                    client.request(INFO)
                    time.sleep(.002)
                else:
                    client.screen()
            phase('key-up')
            client.queue_input(0); client.flush_inputs()
            deadline = time.monotonic() + .4
            phase('after-key-draining')
            while time.monotonic() < deadline:
                if client.screen_mode == 'menu-readback':
                    client.request(INFO)
                    time.sleep(.002)
                else:
                    client.screen()
            if client.menu_resets:
                phase('planned-menu-baseline')
                client.screen()
        phase('drain-before-freeze')
        # FREEZE cannot run inside an LCD paint callback. First drain to an
        # empty queue, rather than submitting it behind a full producer queue.
        for _ in range(128):
            payload = client.request(DRAW_STREAM,(2,0,0,0))
            session.accept_frames(session.replay.apply(payload))
            if not PACKET.unpack_from(payload)[3]:
                break
        else:
            raise RuntimeError('The drawing queue did not become idle before freezing.')
        phase('freeze')
        session.replay.apply(client.request(DRAW_STREAM, (3, 0, 0, 0)))
        frozen = True
        phase('frozen-queue-draining')
        for _ in range(64):
            status = struct.unpack('<8I', client.request(DRAW_STREAM, (5, 0, 0, 0)))
            if not status[3]:
                break
            session.replay.apply(client.request(DRAW_STREAM, (2, 0, 0, 0)))
        else:
            raise RuntimeError('The frozen drawing queue did not drain.')
        replay = session.replay.last_frame.copy()
        reference = np.zeros((320, 480), dtype='<u2')
        index = 0
        phase('stock-reference-scan')
        for y in range(0, 320, 8):
            for x in range(0, 480, 64):
                if index % 32 == 0:
                    client.request(DRAW_STREAM, (5, 0, 0, 0))
                width = min(64, 480 - x)
                tile = parse_tile(client.request(SCREEN_READ, (x, y, width, 8)))
                reference[y:y + 8, x:x + width] = np.frombuffer(tile.pixels, dtype='<u2').reshape(8, width)
                index += 1
        difference = replay != reference
        coordinates = np.argwhere(difference)
        report.update(compared_pixels=153600, different_pixels=int(difference.sum()),
                      first_differences_yx=coordinates[:40].tolist(), queue_status=list(status),
                      resyncs=session.resyncs, planned_menu_baselines=client.menu_resets,
                      success=not bool(difference.any()) and session.resyncs == 0)
        for name, pixels in (('replay', replay), ('reference', reference)):
            save_pixels(output / (name + '.png'), pixels)
            np.save(output / (name + '-rgb565.npy'), pixels)
        phase('compared')
    except Exception as error:
        report.update(error_type=type(error).__name__, error=str(error))
        try:
            report['draw_status_after_error'] = struct.unpack('<8I', client.request(DRAW_STREAM, (5, 0, 0, 0)))
            if client.draw_session is not None:
                report['resyncs'] = client.draw_session.resyncs
        except Exception as status_error:
            report['status_error'] = str(status_error)
    finally:
        if frozen:
            try:
                client.request(DRAW_STREAM, (4, 0, 0, 0))
            except Exception as error:
                report['thaw_error'] = str(error)
        client.close()
        report['finished_utc'] = datetime.now(timezone.utc).isoformat()
        (output / 'receipt.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: v for k, v in report.items() if k != 'info'}, ensure_ascii=False, indent=2))
    return 0 if report['success'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
