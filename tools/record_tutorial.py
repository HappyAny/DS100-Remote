"""Record the application and Qt mouse events while controlling the real R11 device."""
# SPDX-License-Identifier: GPL-3.0-only

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
os.environ['DS100_AUTO_CONNECT'] = '0'
os.environ['QT_SCALE_FACTOR'] = '1'
import numpy as np
from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from ds100 import __version__
from ds100.app import MainWindow
from ds100.protocol import Client
from ds100.storage import find_drives
from ds100.windows_scsi import ScsiTransport


def finish_encoder(sink):
    try:
        sink.stdin.close()
    finally:
        try:
            result = sink.wait(timeout=20)
        except subprocess.TimeoutExpired:
            sink.terminate()
            try:
                sink.wait(timeout=5)
            except subprocess.TimeoutExpired:
                sink.kill()
                sink.wait(timeout=5)
            raise
    return result


def stop_recording_actions(window):
    window.setEnabled(False)
    window.auto_connect.setChecked(False)
    window.mirror.setChecked(False)
    window.mirror_stop.set()
    window.stop_motion()
    window.input_panel.set_available(False)
    window.quick_panel.set_available(False)
    window.reconnect_when_idle = False
    with window.action_lock:
        window.action_pending = None
        window.pending_motion_stop = None
        window.queued_actions.clear()
    # A rejected closeEvent restarts two timers. Block their signals as well
    # so processing a queued completion cannot start more device operations.
    for timer in (window.timer, window.discovery_timer, window.input_timer):
        timer.stop()
        timer.blockSignals(True)


def close_recording(window, app, timer, sink, *, close_attempts=5):
    try:
        if timer is not None:
            timer.stop()
        if window is not None:
            stop_recording_actions(window)
            if window.worker is not None:
                window.worker.requestInterruption()
        if sink is not None:
            finish_encoder(sink)
    finally:
        if window is not None:
            close_recording_window(window, app, close_attempts)


def close_recording_window(window, app, attempts):
    # closeEvent waits up to 3 s per attempt. The extra 1 s waits allow the
    # 12 s SCSI timeout to finish, with at most 19 s of worker waits in total.
    for attempt in range(attempts):
        stop_recording_actions(window)
        if window.worker is not None:
            window.worker.requestInterruption()
        accepted = window.close()
        stop_recording_actions(window)
        app.processEvents()
        if accepted:
            return
        if attempt + 1 < attempts and window.worker is not None:
            window.worker.requestInterruption()
            window.worker.wait(1000)
    raise TimeoutError('The recording window refused to close after bounded worker waits.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    folder = args.output.resolve()
    if not folder.is_relative_to(ROOT):
        parser.error('Use a project output directory.')
    folder.mkdir(parents=True, exist_ok=False)
    os.environ['DS100_CAPTURE_DIR'] = str(folder / 'screenshots')
    app = QApplication([])
    window = timer = sink = None
    try:
        app.setStyle('Fusion')
        window = MainWindow()
        window.discovery_timer.stop()
        window.resize(736, 552)
        window.show()
        app.processEvents()
        drives = find_drives()
        if len(drives) != 1:
            raise OSError('Connect one DS100 and close other clients.')
        client = Client(ScsiTransport(drives[0]))
        try:
            info = client.info()
            if info.get('build') != 'background-r11-input-ack':
                raise ValueError('The recording requires the checked R11 build.')
            window.drive = drives[0]
            window.experimental_connected((client, info))
        except BaseException:
            if window.client is not client:
                client.close()
            raise
        window.mirror.setChecked(True)
        report = {'created_utc': datetime.now(timezone.utc).isoformat(), 'success': False,
                  'physical_device': True, 'firmware_written': False, 'simulator': False,
                  'build': info['build'], 'client': __version__, 'capture_fps': 30,
                  'input_method': 'Qt mouse events on actual application controls; real USB device responses.',
                  'cursor': 'Visual pointer follows the recorded event coordinates. Click rings are an overlay.',
                  'scenes': []}
        scene = None
        sink = None
        cursor = [25.0, 20.0]

        def save():
            (folder / 'receipt.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

        def pump(seconds):
            end = time.monotonic() + seconds
            while time.monotonic() < end:
                app.processEvents()
                QTest.qWait(5)
                if window.client is None:
                    raise RuntimeError(window.status.text())

        def until(condition, seconds=12):
            end = time.monotonic() + seconds
            while not condition() and time.monotonic() < end:
                pump(.01)
            if not condition():
                raise TimeoutError('The real-device action did not complete: ' + (scene['id'] if scene else 'initial'))

        def image_pixels():
            q = window.screen_label.image.convertToFormat(QImage.Format_RGB16)
            return np.frombuffer(q.constBits(), dtype='<u2').reshape(320, 480).copy()

        def menu_visible():
            p = image_pixels()[20:285, 70:330]
            return int(np.count_nonzero(((p >> 11) > 15) & (((p >> 5) & 63) < 8) & ((p & 31) > 20))) > 2000

        def idle():
            return (window.action_pending is None and window.action_running is None and not window.queued_actions
                    and not client.has_pending_input() and client._input_keys == 0)

        def event(kind, x, y, label=''):
            if scene is not None:
                scene['events'].append({'time': time.monotonic() - scene['_start'], 'kind': kind,
                                        'x': float(x), 'y': float(y), 'label': label})

        def move(widget, label):
            if not widget.isVisible() or not widget.isEnabled():
                raise RuntimeError('The tutorial control is unavailable: ' + label)
            point = widget.mapTo(window, widget.rect().center())
            old = cursor.copy()
            for n in range(1, 13):
                x, y = [old[i] + (point.x() if i == 0 else point.y()) * n / 12 - old[i] * n / 12 for i in range(2)]
                cursor[:] = [x, y]
                local = widget.mapFrom(window, QPoint(round(x), round(y)))
                QTest.mouseMove(widget, local)
                event('move', x, y, label)
                pump(.012)

        def click(widget, label, hold=.10):
            move(widget, label)
            event('down', *cursor, label)
            QTest.mousePress(widget, Qt.LeftButton)
            pump(hold)
            QTest.mouseRelease(widget, Qt.LeftButton)
            event('up', *cursor, label)
            pump(.15)

        def frame():
            if sink is None or scene is None:
                return
            target = int((time.monotonic() - scene['_start']) * 30)
            if scene['frames'] > target:
                return
            q = window.grab().toImage().convertToFormat(QImage.Format_RGBA8888)
            if q.width() != 736 or q.height() != 552:
                q = q.scaled(736, 552, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
            data = bytes(q.constBits())
            while scene['frames'] <= target:
                sink.stdin.write(data)
                scene['frames'] += 1

        timer = QTimer(window)
        timer.setInterval(16)
        timer.timeout.connect(frame)

        def begin(name, seconds, title):
            nonlocal scene, sink
            path = folder / (name + '.mp4')
            log = (folder / (name + '-ffmpeg.log')).open('wb')
            sink = subprocess.Popen([shutil.which('ffmpeg') or 'ffmpeg', '-hide_banner', '-loglevel', 'error',
                '-y', '-f', 'rawvideo', '-pix_fmt', 'rgba', '-s', '736x552', '-r', '30', '-i', 'pipe:0',
                '-an', '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '18', '-pix_fmt', 'yuv420p', str(path)],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=log)
            log.close()
            scene = {'id': name, 'title': title, 'minimum_seconds': seconds, 'frames': 0,
                     'events': [], '_start': time.monotonic(), 'native_frames_before': window.mirror_count}
            event('move', *cursor)
            timer.start()
            frame()

        def finish():
            nonlocal scene, sink
            pump(max(0, scene['minimum_seconds'] - (time.monotonic() - scene['_start'])))
            timer.stop()
            frame()
            if finish_encoder(sink) != 0:
                raise RuntimeError('The application recording encoder failed.')
            scene.update(seconds=scene['frames'] / 30, native_frames_after=window.mirror_count,
                         actual_elapsed_seconds=time.monotonic() - scene.pop('_start'), success=True)
            window.grab().save(str(folder / (scene['id'] + '.png')))
            report['scenes'].append(scene)
            print(scene['id'], 'recorded', round(scene['seconds'], 2), 'seconds', flush=True)
            scene, sink = None, None
            save()

        try:
            until(lambda: window.mirror_count >= 3)
            if menu_visible():
                click(window.input_panel.key_buttons['E'], '退出原有菜单')
                until(lambda: not menu_visible())
            original = image_pixels()
            original_positions = original[144:197, 421:478].copy()
            original_time = original[:18, 75:140].copy()
            original_gain = original[:18, 286:350].copy()

            begin('connect', 12, '开机后直接连接')
            pump(2)
            report['initial_frame_received'] = True
            finish()

            begin('menu', 18, 'M 打开菜单，E 返回')
            pump(2)
            click(window.input_panel.key_buttons['M'], '点击 M · 打开菜单')
            until(menu_visible)
            pump(4)
            click(window.input_panel.key_buttons['E'], '点击 E · 返回波形')
            until(lambda: not menu_visible())
            finish()

            for key, name, title in [('M', 'held_m', '按住 M · 水平与垂直移动'), ('E', 'held_e', '按住 E · 时基与量程')]:
                begin(name, 24, title)
                panel = window.input_panel
                baseline = image_pixels()
                pump(1)
                click(panel.holds[key], '勾选 按住 ' + key)
                until(lambda: client._input_keys == {'M': 4, 'E': 8}[key])
                for dial, roi in ((0, (144, 161, 421, 478) if key == 'M' else (0, 18, 75, 140)),
                                  (1, (179, 197, 421, 478) if key == 'M' else (0, 18, 286, 350))):
                    y0, y1, x0, x1 = roi
                    before = image_pixels()[y0:y1, x0:x1]
                    click(panel.step_buttons[dial][1], '拨轮 ' + str(dial + 1) + ' · 增加一步')
                    until(lambda: not np.array_equal(image_pixels()[y0:y1, x0:x1], before))
                    if not panel.holds[key].isChecked():
                        raise AssertionError('The modifier was released before feedback.')
                    pump(3)
                    click(panel.step_buttons[dial][0], '拨轮 ' + str(dial + 1) + ' · 反向恢复')
                    until(lambda: np.array_equal(image_pixels()[y0:y1, x0:x1], before))
                    pump(2)
                click(panel.holds[key], '取消 按住 ' + key)
                until(idle)
                if not np.array_equal(image_pixels()[144:197, 421:478], baseline[144:197, 421:478]):
                    raise AssertionError('The held controls did not restore the positions.')
                finish()

            begin('quick', 28, '快捷按钮与按住连续移动')
            click(window.quick_toggle, '切换 快捷 面板')
            pump(1)
            baseline = image_pixels()[144:197, 421:478].copy()
            click(window.quick_panel.buttons['left'], '按住 ← · 连续移动', hold=1.)
            until(idle)
            count = client.last_motion['detents']
            if count < 2:
                raise AssertionError('The continuous movement did not deliver multiple detents.')
            report['continuous_detents'] = count
            pump(2)
            for _ in range(count):
                click(window.quick_panel.buttons['right'], '点击 → · 恢复位置')
            until(idle)
            until(lambda: np.array_equal(image_pixels()[144:197, 421:478], baseline))
            for action in ('time_up', 'time_down', 'range_up', 'range_down'):
                click(window.quick_panel.buttons[action], '快捷 · ' + action)
                until(idle)
                pump(.5)
            until(lambda: np.array_equal(image_pixels()[144:197, 421:478], baseline))
            finish()

            begin('channel', 14, '双击 E 切换通道')
            click(window.quick_toggle, '返回 原机按键')
            baseline = image_pixels()[40:60, 425:478].copy()
            click(window.input_panel.double_buttons['E'], '点击 E 下方 双击')
            until(idle)
            until(lambda: not np.array_equal(image_pixels()[40:60, 425:478], baseline))
            pump(3)
            click(window.input_panel.double_buttons['E'], '再次双击 E · 恢复通道')
            until(idle)
            until(lambda: np.array_equal(image_pixels()[40:60, 425:478], baseline))
            finish()

            begin('capture', 12, '一键保存原始 PNG')
            pump(2)
            click(window.save_button, '点击 截图 · 保存 PNG')
            until(lambda: any((folder / 'screenshots').glob('*.png')))
            finish()

            until(idle)
            current = image_pixels()
            report['settings_restored'] = (np.array_equal(current[144:197, 421:478], original_positions)
                and np.array_equal(current[:18, 75:140], original_time)
                and np.array_equal(current[:18, 286:350], original_gain))
            if not report['settings_restored']:
                raise AssertionError('The recorded controls did not restore the original settings.')
            report.update(success=True, resyncs=client.draw_session.resyncs, native_frames=window.mirror_count,
                          finished_utc=datetime.now(timezone.utc).isoformat())
            window.grab().save(str(folder / 'poster.png'))
            save()
        except Exception as error:
            report.update(error_type=type(error).__name__, error=str(error), partial_scene=scene and scene['id'])
            save()
            raise
        return 0
    finally:
        close_recording(window, app, timer, sink)



if __name__ == '__main__':
    raise SystemExit(main())
