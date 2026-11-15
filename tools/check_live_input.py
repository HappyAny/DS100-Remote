"""Send one bounded input action and save before/after device screens."""

from datetime import datetime, timezone
from pathlib import Path
import argparse
import hashlib
import json
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from ds100.protocol import Client
from ds100.storage import find_drives
from ds100.windows_scsi import ScsiTransport
from probe_remote import save_pixels

KEYS = {'P': 1, 'A': 2, 'M': 4, 'E': 8}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--key', choices=KEYS)
    parser.add_argument('--double-e', action='store_true')
    parser.add_argument('--hold', choices=('M', 'E'))
    parser.add_argument('--wheel1', type=int, default=0)
    parser.add_argument('--wheel2', type=int, default=0)
    parser.add_argument('--simulate', action='store_true')
    parser.add_argument('--legacy-screen', action='store_true', help='Use stock LCD readback for configuration checks.')
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT):
        parser.error('Use a project output directory.')
    if any(not -8 <= delta <= 8 for delta in (args.wheel1, args.wheel2)):
        parser.error('Use at most eight steps per wheel in one check.')
    if (args.double_e and (args.key or args.hold or args.wheel1 or args.wheel2)) or (args.key and args.hold):
        parser.error('Use one key, a double E, or one held-key wheel action.')
    output.mkdir(parents=True, exist_ok=False)
    report = {'started_utc': datetime.now(timezone.utc).isoformat(), 'physical_device_test': not args.simulate,
              'command_acknowledgement_is_not_visual_verification': True, 'success': False, 'events': [],
              'flash_writes': False, 'sends_power_off': False, 'screens': {}}
    client = None

    def save():
        (output / 'receipt.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    def send(keys=0, wheel1=0, wheel2=0):
        start = time.perf_counter()
        client.queue_input(keys, wheel1, wheel2)
        client.flush_inputs()
        report['events'].append({'keys': keys, 'wheel1': wheel1, 'wheel2': wheel2,
                                 'seconds': time.perf_counter() - start, 'acknowledged': True})
        save()

    def capture(name):
        start = time.perf_counter()
        pixels = None
        while pixels is None:
            pixels = client.screen(cancelled=lambda: time.perf_counter() - start > 30)
        save_pixels(output / (name + '.png'), pixels)
        import numpy as np
        np.save(output / (name + '-rgb565.npy'), pixels)
        report['screens'][name] = {'seconds': time.perf_counter() - start,
                                   'rgb565_sha256': hashlib.sha256(pixels.tobytes()).hexdigest()}
        save()
        return pixels

    def wait(seconds):
        if client.screen_mode != 'draw-stream':
            time.sleep(seconds)
            return
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            client.screen()

    save()
    try:
        if args.simulate:
            from ds100.simulator import SimulatorTransport
            client = Client(SimulatorTransport())
        else:
            drives = find_drives()
            if len(drives) != 1:
                raise OSError('Connect one DS100 and close other screen clients.')
            client = Client(ScsiTransport(drives[0]))
        report['info'] = client.info()
        if not args.simulate and report['info'].get('build') not in ('background-r6-controls', 'background-r7-draw-stream', 'background-r8-hardware-follow', 'background-r9-bounded-follow'):
            raise ValueError('The connected device does not run a reviewed controls candidate.')
        if not client.input_supported:
            raise ValueError('The connected device has no input service.')
        if args.legacy_screen:
            client.draw_config = None
            client.screen_mode = 'legacy'
            report['screen_mode'] = 'legacy'
        before = capture('before')
        if args.double_e:
            for index in range(2):
                send(KEYS['E'])
                wait(.10)
                send()
                if index == 0:
                    wait(.08)
        elif args.key:
            send(KEYS[args.key])
            wait(.10)
            if args.wheel1 or args.wheel2:
                send(KEYS[args.key], args.wheel1, args.wheel2)
                wait(.10)
            send()
        elif args.hold:
            send(KEYS[args.hold])
            wait(.10)
            send(KEYS[args.hold], args.wheel1, args.wheel2)
            wait(.10)
            send()
        elif args.wheel1 or args.wheel2:
            send(0, args.wheel1, args.wheel2)
        wait(.15)
        after = capture('after')
        report.update(success=True, different_pixels=int((before != after).sum()),
                      note='Screen changes can include normal rendering. Inspect the images to verify the input effect.')
        if client.draw_session is not None:
            report['drawing_resyncs'] = client.draw_session.resyncs
    except Exception as error:
        report.update(error_type=type(error).__name__, error=str(error))
    finally:
        if client is not None:
            if client.input_supported:
                try:
                    send()
                except Exception as error:
                    report['release_error'] = str(error)
            client.close()
        report['finished_utc'] = datetime.now(timezone.utc).isoformat()
        save()
    print(json.dumps({key: value for key, value in report.items() if key != 'info'}, ensure_ascii=False, indent=2))
    return 0 if report['success'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
