"""Check R9 M/E latency with drawing polls. Close other USB clients first."""

from pathlib import Path
from datetime import datetime, timezone
import argparse
import json
import struct
import sys
import time
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from ds100.protocol import Client, DRAW_STREAM, INPUT_STATE, SCREEN_READ, parse_tile, ProtocolError
from ds100.windows_scsi import ScsiTransport
from ds100.storage import find_drives
from probe_remote import save_pixels


class IncrementalInputClient(Client):
    """Experimental policy for the installed R9 nested-input handler."""
    def flush_inputs(self):
        while True:
            with self._input_lock:
                if not self._inputs:
                    return
                keys, wheel1, wheel2 = self._inputs.popleft()
            payload = self._request_raw(INPUT_STATE, (keys, wheel1 & 65535, wheel2 & 65535, 0))
            if len(payload) != 8 or struct.unpack_from('<HH', payload) != (keys, 600):
                raise ProtocolError('The input acknowledgement is invalid.')
            self._input_keys = keys


class BarrierInputClient(Client):
    """Place M at a frozen GUI boundary, preserve the drawing epoch."""
    def flush_inputs(self):
        while True:
            with self._input_lock:
                if not self._inputs:
                    return
                keys, wheel1, wheel2 = self._inputs.popleft()
            frozen = False
            try:
                if self.draw_session is not None and keys & 4 and not self._input_keys & 4:
                    self.draw_session.drain_before_stop()
                    payload = self._request_raw(DRAW_STREAM, (3, 0, 0, 0))
                    frozen = True
                    self.draw_session.accept_frames(self.draw_session.replay.apply(payload))
                payload = self._request_raw(INPUT_STATE, (keys, wheel1 & 65535, wheel2 & 65535, 0))
                if len(payload) != 8 or struct.unpack_from('<HH', payload) != (keys, 600):
                    raise ProtocolError('The input acknowledgement is invalid.')
                self._input_keys = keys
            finally:
                if frozen:
                    self._request_raw(DRAW_STREAM, (4, 0, 0, 0))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    # Failed prototype classes remain for audit; their USB-disconnecting
    # policies are not exposed by this diagnostic CLI.
    parser.add_argument('--policy', choices=('protected',), default='protected')
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT):
        parser.error('Use a project output directory.')
    output.mkdir(parents=True, exist_ok=False)
    drives = find_drives()
    if len(drives) != 1:
        raise OSError('Connect one DS100 and close other clients.')
    report = {'created_utc': datetime.now(timezone.utc).isoformat(), 'physical_device_test': True,
              'policy': args.policy, 'success': False, 'events': [], 'requests': []}

    class Audited(ScsiTransport):
        def exchange(self, cdb):
            started = time.monotonic()
            data = super().exchange(cdb)
            report['requests'].append({'cmd': cdb[1], 'args': list(struct.unpack_from('<4H', cdb, 8)),
                                       'seconds': time.monotonic() - started, 'bytes': len(data)})
            return data

    client_class = {'protected': Client, 'direct': IncrementalInputClient, 'barrier': BarrierInputClient}[args.policy]
    client = client_class(Audited(drives[0]))
    frozen = False

    def save():
        (output / 'receipt.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    def drain(seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            client.screen()

    def compare(name):
        nonlocal frozen
        session = client.draw_session
        session.replay.apply(client.request(DRAW_STREAM, (3, 0, 0, 0)))
        frozen = True
        for _ in range(128):
            status = struct.unpack('<8I', client.request(DRAW_STREAM, (5, 0, 0, 0)))
            if not status[3]:
                break
            session.replay.apply(client.request(DRAW_STREAM, (2, 0, 0, 0)))
        else:
            raise RuntimeError('The frozen queue did not drain.')
        replay = session.replay.last_frame.copy()
        reference = np.zeros((320, 480), dtype='<u2')
        for index, (x, y) in enumerate((x, y) for y in range(0, 320, 8) for x in range(0, 480, 64)):
            if index % 32 == 0:
                client.request(DRAW_STREAM, (5, 0, 0, 0))
            width = min(64, 480 - x)
            tile = parse_tile(client.request(SCREEN_READ, (x, y, width, 8)))
            reference[y:y+8, x:x+width] = np.frombuffer(tile.pixels, dtype='<u2').reshape(8, width)
        save_pixels(output / (name + '-replay.png'), replay)
        save_pixels(output / (name + '-reference.png'), reference)
        report[name] = {'different_pixels': int(np.count_nonzero(replay != reference)),
                        'pixels': 153600, 'resyncs': session.resyncs}
        client.request(DRAW_STREAM, (4, 0, 0, 0))
        frozen = False
        save()

    try:
        save()
        report['info'] = client.info()
        if report['info'].get('build') != 'background-r9-bounded-follow':
            raise ValueError('This test requires the installed R9 build.')
        save_pixels(output / 'before.png', client.screen())
        drain(.2)
        for key, mask in (('M', 4), ('E', 8)):
            start = time.monotonic()
            client.queue_input(mask); client.flush_inputs()
            down = time.monotonic() - start
            drain(.1)
            client.queue_input(0); client.flush_inputs()
            released = time.monotonic() - start
            before = client.draw_session.presentations
            deadline = time.monotonic() + 3
            while client.draw_session.presentations == before and time.monotonic() < deadline:
                client.screen()
            first_frame = time.monotonic() - start
            drain(.15)
            report['events'].append({'key': key, 'down_ack_seconds': down,
                                     'release_ack_seconds': released, 'next_frame_seconds': first_frame})
            save()
            compare(key)
        report['success'] = all(report[key]['different_pixels'] == 0 and report[key]['resyncs'] == 0 for key in ('M','E'))
    except Exception as error:
        report.update(error=str(error), error_type=type(error).__name__)
    finally:
        if frozen:
            try:
                client.request(DRAW_STREAM, (4, 0, 0, 0))
            except Exception:
                pass
        try:
            client.queue_input(0); client.flush_inputs()
        except Exception:
            pass
        client.close()
        report['finished_utc'] = datetime.now(timezone.utc).isoformat()
        save()
    print(json.dumps({k:v for k,v in report.items() if k not in ('requests','info')}, ensure_ascii=False, indent=2))
    return 0 if report['success'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
