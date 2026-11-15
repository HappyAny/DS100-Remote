"""Check visible held controls, queued buttons, and menu recovery on R9."""

import argparse
import ctypes
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import struct
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
os.environ['DS100_AUTO_CONNECT'] = '0'
if '--offscreen' in sys.argv:
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import numpy as np
from PIL import Image
from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication
from ds100 import __version__
from ds100.app import MainWindow
from ds100.protocol import Client, parse_response
from ds100.storage import find_drives
from ds100.windows_scsi import ScsiTransport
from probe_remote import save_pixels


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--keep-open', action='store_true')
    parser.add_argument('--offscreen', action='store_true')
    parser.add_argument('--expect-build', default='background-r9-bounded-follow')
    args = parser.parse_args()
    folder = args.output.resolve()
    if not folder.is_relative_to(ROOT):
        parser.error('Use a project output directory.')
    folder.mkdir(parents=True, exist_ok=False)
    report = dict(version=__version__, created_utc=datetime.now(timezone.utc).isoformat(),
                  physical_device_test=True, firmware_changed=False, success=False,
                  administrator=bool(ctypes.windll.shell32.IsUserAnAdmin()), pid=os.getpid(),
                  input_packets=[], cases=[])
    start = time.monotonic()

    def save():
        (folder/'receipt.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')

    class Audited(ScsiTransport):
        def exchange(self, cdb):
            data = super().exchange(cdb)
            if cdb[1] == 8:
                keys, a, b, _ = struct.unpack_from('<4H', cdb, 8)
                ack = struct.unpack('<HHhh', parse_response(data, 8, struct.unpack_from('<I', cdb, 4)[0]))
                report['input_packets'].append(dict(seconds=time.monotonic()-start, keys=keys,
                    wheel1=a if a<32768 else a-65536, wheel2=b if b<32768 else b-65536,
                    pending=list(ack[2:])))
            return data

    drives = find_drives()
    if len(drives) != 1:
        raise OSError('Connect one DS100 and close other clients.')
    app = QApplication.instance() or QApplication([])
    client = Client(Audited(drives[0]))
    info = client.info()
    if info.get('build') != args.expect_build:
        client.close()
        raise ValueError('The running build differs from the expected build.')
    report['info'] = info
    window = MainWindow(); window.drive = drives[0]
    window.experimental_connected((client, info)); window.show()
    window.mirror.setChecked(True)

    def pump(seconds):
        deadline = time.monotonic()+seconds
        while time.monotonic() < deadline:
            app.processEvents(); QTest.qWait(5)
            if window.client is None:
                raise RuntimeError(window.status.text())

    def until(condition, timeout=4):
        deadline = time.monotonic()+timeout
        while not condition() and time.monotonic() < deadline:
            pump(.01)
        if not condition():
            report['timeout_state'] = dict(screen_mode=client.screen_mode,
                replay_ready=bool(client.draw_session and client.draw_session.replay and client.draw_session.replay.ready),
                scan_tiles=len(client._menu_scan_tiles) if client._menu_scan_tiles is not None else None,
                frames=window.mirror_count, preview=window.screen_label.preview,
                remote_keys=client._input_keys, queued_input=client.has_pending_input())
            raise RuntimeError('Control check timed out: '+report.get('phase', 'initial'))

    def snapshot(name):
        frame = window.last_screen_pixels.copy()
        save_pixels(folder/(name+'.png'), frame)
        return frame

    def action_done():
        return (window.action_pending is None and window.action_running is None and not window.queued_actions and
                not client.has_pending_input() and client._input_keys == 0)

    def displayed():
        image = window.screen_label.image.convertToFormat(QImage.Format_RGB16)
        return np.frombuffer(image.constBits(),dtype='<u2').reshape(320,480).copy()

    navigation = 0
    try:
        save(); until(lambda: window.mirror_count > 2)
        def menu_visible():
            p = displayed()[20:285,70:330]
            return np.count_nonzero(((p>>11)>15)&(((p>>5)&63)<8)&((p&31)>20))>2000
        if menu_visible():
            report['phase']='close-existing-menu'; save()
            QTest.mouseClick(window.input_panel.key_buttons['E'],Qt.LeftButton)
            until(lambda: not menu_visible())
        reference = np.asarray(Image.open(ROOT/'evidence/live-device/r9-m-protected-latency-v2/before.png').convert('RGB'))
        template = np.all(reference[126:142,420:480] > 200, axis=2)

        def primary():
            patch = window.last_screen_pixels[126:142,420:480]
            white = ((patch>>11)>=25) & (((patch>>5)&63)>=50) & ((patch&31)>=25)
            return np.mean(white != template)<.02

        report['phase'] = 'position-sidebar'; save()
        while not primary() and navigation < 16:
            frames = window.mirror_count
            client.queue_input(0, 1, 0); window.pump_input()
            navigation += 1
            until(lambda: not client.has_pending_input() and window.mirror_count>frames)
            pump(.2)
        if not primary():
            raise RuntimeError('The primary position sidebar is not visible.')
        report['navigation_steps'] = navigation
        before = snapshot('before')
        positions = lambda p: p[144:197,421:478]
        # Raw holds stay checked throughout both directions. Observe the image
        # before releasing the modifier, as the user does with the checkbox.
        for key, dial, roi in [('M',0,(144,161,421,478)), ('M',1,(179,197,421,478)),
                              ('E',0,(0,18,75,140)), ('E',1,(0,18,286,350))]:
            report['phase'] = f'held-{key}-wheel-{dial+1}'; save()
            baseline = window.last_screen_pixels.copy()
            y0,y1,x0,x1 = roi
            view = lambda: displayed()[y0:y1,x0:x1]
            panel = window.input_panel
            panel.holds[key].setChecked(True)
            until(lambda: client._input_keys == {'M':4,'E':8}[key])
            began = time.monotonic()
            QTest.mouseClick(panel.step_buttons[dial][1], Qt.LeftButton)
            until(lambda: not np.array_equal(view(), baseline[y0:y1,x0:x1]))
            seconds = time.monotonic()-began
            assert panel.holds[key].isChecked() and panel.available
            assert all(button.isEnabled() for button in panel.key_buttons.values())
            snapshot(report['phase']+'-changed')
            QTest.mouseClick(panel.step_buttons[dial][0], Qt.LeftButton)
            until(lambda: np.array_equal(view(), baseline[y0:y1,x0:x1]))
            panel.holds[key].setChecked(False)
            until(lambda: client._input_keys == 0)
            pump(.5)
            assert np.array_equal(view(), baseline[y0:y1,x0:x1])
            report['cases'].append(dict(name=report['phase'], visible_while_held=True,
                                        visible_seconds=seconds, restored=True, buttons_enabled=True))
            save()
        report['phase'] = 'queued-range-roundtrip'; save()
        window.set_raw_controls(False)
        zero = window.last_screen_pixels.copy()
        for name in ('left','down'):
            QTest.mouseClick(window.quick_panel.buttons[name],Qt.LeftButton)
        until(action_done)
        until(lambda: not np.array_equal(zero[144:161,421:478],window.last_screen_pixels[144:161,421:478]) and
              not np.array_equal(zero[179:197,421:478],window.last_screen_pixels[179:197,421:478]))
        baseline = snapshot('nonzero-offsets-before-queued-ranges')
        frames = window.mirror_count
        began = time.monotonic()
        for name in ('time_up','time_down','range_up','range_down'):
            QTest.mouseClick(window.quick_panel.buttons[name], Qt.LeftButton)
            assert all(button.isEnabled() for button in window.quick_panel.buttons.values())
        assert len(window.queued_actions) == 3
        until(action_done, 6); pump(.5)
        after = snapshot('after-queued-ranges')
        assert np.array_equal(positions(baseline), positions(after))
        assert np.array_equal(baseline[0:18,75:140], after[0:18,75:140])
        assert np.array_equal(baseline[0:18,286:350], after[0:18,286:350])
        report['cases'].append(dict(name=report['phase'], seconds=time.monotonic()-began,
            all_four_executed=True, position_restored=True, scale_restored=True,
            frames_during_actions=window.mirror_count-frames, buttons_enabled=True))
        report['phase']='separate-nonzero-range-steps'; save()
        for name,header in [('time_up',(0,18,75,140)),('time_down',(0,18,75,140)),
                            ('range_up',(0,18,286,350)),('range_down',(0,18,286,350))]:
            old=window.last_screen_pixels.copy()
            QTest.mouseClick(window.quick_panel.buttons[name],Qt.LeftButton)
            until(action_done)
            y0,y1,x0,x1=header
            until(lambda: not np.array_equal(old[y0:y1,x0:x1],window.last_screen_pixels[y0:y1,x0:x1]))
            pump(.25)
            snapshot('nonzero-'+name)
        until(lambda: np.array_equal(positions(baseline),positions(window.last_screen_pixels)))
        for name in ('right','up'):
            QTest.mouseClick(window.quick_panel.buttons[name],Qt.LeftButton)
        until(action_done)
        until(lambda: np.array_equal(positions(zero),positions(window.last_screen_pixels)))
        report['cases'].append(dict(name=report['phase'],nonzero_presets_used=True,
                                   individual_step_images_saved=True,original_positions_restored=True))
        report['phase'] = 'continuous-pan'; save()
        baseline = snapshot('before-continuous-pan')
        first = len(report['input_packets'])
        QTest.mousePress(window.quick_panel.buttons['left'], Qt.LeftButton)
        until(lambda: any(p['wheel1'] for p in report['input_packets'][first:]))
        pump(1.)
        QTest.mouseRelease(window.quick_panel.buttons['left'], Qt.LeftButton)
        until(action_done); pump(.2)
        snapshot('after-continuous-pan')
        detents = sum(p['wheel1'] for p in report['input_packets'][first:])
        report['continuous_detents'] = detents
        report['last_motion'] = getattr(client,'last_motion',None)
        report['last_motion_stop_reason'] = getattr(window,'last_motion_stop_reason',None)
        save()
        assert detents > 1 and client._input_keys == 0
        for _ in range(detents):
            QTest.mouseClick(window.quick_panel.buttons['right'], Qt.LeftButton)
        until(action_done)
        until(lambda: np.array_equal(positions(baseline), positions(window.last_screen_pixels)))
        snapshot('after-continuous-restored')
        assert np.array_equal(positions(baseline), positions(window.last_screen_pixels))
        report['cases'].append(dict(name=report['phase'], detents=detents, released=True, restored=True))
        report['phase'] = 'menu-and-exit'; save()
        window.set_raw_controls(True)
        resets, frames = client.menu_resets, window.mirror_count
        began = time.monotonic()
        QTest.mouseClick(window.input_panel.key_buttons['M'], Qt.LeftButton)
        until(lambda: menu_visible() and client.menu_resets>resets and client.screen_mode=='draw-stream' and
              client.draw_session.replay is not None and client.draw_session.replay.ready and
              window.mirror_count>frames)
        menu_seconds = time.monotonic()-began
        snapshot('menu')
        QTest.mouseClick(window.input_panel.key_buttons['E'], Qt.LeftButton)
        until(lambda: not menu_visible()); pump(.5)
        report['cases'].append(dict(name=report['phase'], menu_seconds=menu_seconds,
                                    returned_to_primary=True))
        report.update(success=True, phase='checks-passed', resyncs=client.draw_session.resyncs,
                      settings_restored=np.array_equal(positions(before), positions(window.last_screen_pixels)))
        snapshot('restored'); window.grab().save(str(folder/'application-verified.png')); save()
    except Exception as error:
        report.update(error=str(error), error_type=type(error).__name__)
        report['traceback'] = traceback.format_exc()
        window.grab().save(str(folder/'application-error.png')); save()
    finally:
        for hold in window.input_panel.holds.values():
            hold.setChecked(False)
        window.stop_motion()
        try:
            until(action_done,6)
            until(lambda: client._input_keys==0)
            for _ in range(navigation):
                client.queue_input(0,-1,0); window.pump_input(); pump(.15)
        except Exception as error:
            report.update(cleanup_error=str(error), success=False)
        report['finished_utc']=datetime.now(timezone.utc).isoformat(); save()
    print(json.dumps({k:v for k,v in report.items() if k not in ('info','input_packets')},ensure_ascii=False,indent=2),flush=True)
    if args.keep_open:
        observer = QTimer(window)
        def observe():
            report.update(mirror_active=window.mirror.isChecked(), mirror_frames=window.mirror_count,
                          desktop_status=window.status.text(), input_mask=window.input_panel.mask,
                          resyncs=client.draw_session.resyncs if client.draw_session else 0)
            if window.last_image is not None:
                window.last_image.save(str(folder/'latest-screen.png'))
            save()
        observer.timeout.connect(observe); observer.start(1000)
        return app.exec()
    window.close(); app.processEvents()
    return 0 if report['success'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
