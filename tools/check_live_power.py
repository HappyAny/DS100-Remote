"""Hold virtual P with lease renewals and record the expected power-off boundary."""

from datetime import datetime, timezone
from pathlib import Path
import argparse
import json
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from ds100.protocol import Client
from ds100.storage import find_drives
from ds100.windows_scsi import ScsiTransport
from probe_remote import save_pixels


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT):
        parser.error('Use a project output directory.')
    output.mkdir(parents=True, exist_ok=False)
    drives = find_drives()
    if len(drives) != 1:
        raise OSError('Connect one DS100 and close other screen clients.')
    client = Client(ScsiTransport(drives[0], timeout_seconds=3))
    report = {'started_utc': datetime.now(timezone.utc).isoformat(), 'physical_device_test': True,
              'flash_writes': False, 'lease_renewals': [], 'user_power_off_confirmation_pending': True}

    def save():
        (output / 'receipt.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    try:
        report['info'] = client.info()
        if report['info'].get('build') != 'background-r6-controls' or not client.input_supported:
            raise ValueError('The connected device does not run the reviewed R6 controls candidate.')
        save_pixels(output / 'before.png', client.screen())
        save()
        start = time.perf_counter()
        while time.perf_counter() - start < 2:
            client.queue_input(1)
            client.flush_inputs()
            report['lease_renewals'].append(time.perf_counter() - start)
            save()
            time.sleep(.15)
        client.queue_input(0)
        client.flush_inputs()
        report['released_after_two_seconds'] = True
    except Exception as error:
        report.update(request_error_type=type(error).__name__, request_error=str(error),
                      note='A disconnect during the P hold is expected if power-off occurs. Verify the USB identity and physical screen.')
    finally:
        if client.input_supported:
            try:
                client.queue_input(0)
                client.flush_inputs()
            except Exception as error:
                report['release_error'] = str(error)
        client.close()
        report['finished_utc'] = datetime.now(timezone.utc).isoformat()
        save()
    print(json.dumps({key: value for key, value in report.items() if key != 'info'}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
