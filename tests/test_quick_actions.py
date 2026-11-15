import time
import numpy as np
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog
from ds100.app import MainWindow
from ds100.protocol import Client, ProtocolError, SCREEN_READ
from ds100.quick_actions import DEFAULT_FAVORITES, execute_action
from ds100.quick_panel import QuickPanel
from ds100.simulator import SimulatorTransport


def client_fixture():
    client = Client(SimulatorTransport())
    client.info()
    return client


def idle(app, window):
    deadline = time.monotonic() + 8
    while (window.worker is not None or window.action_pending is not None or window.action_running is not None) and time.monotonic() < deadline:
        app.processEvents()
        QTest.qWait(5)
    assert window.worker is None and window.action_pending is None and window.action_running is None


def without_lease_renewals(log):
    result = []
    previous_keys = None
    for keys, a, b in log:
        if keys != previous_keys or a or b:
            result.append((keys, a, b))
        previous_keys = keys
    return result


def test_named_gesture_preserves_modifier_until_wheel_ack():
    client = client_fixture()
    seen = []
    execute_action(client, 'time_down', wait=lambda seconds: seen.append((len(client.transport.input_log), seconds)))
    assert without_lease_renewals(client.transport.input_log) == [(8, 0, 0), (8, -1, 0), (0, 0, 0)]
    assert sum(seconds for ack, seconds in seen) == pytest.approx(.16)
    assert client.transport.keys == 0
    client.transport.input_log.clear()
    execute_action(client, 'channel', wait=lambda _: None)
    assert client.transport.input_log == [(8, 0, 0), (0, 0, 0), (8, 0, 0), (0, 0, 0)]


def test_cancel_between_press_and_wheel_releases_modifier():
    client = client_fixture()
    cancelled = [False]

    def wait(_):
        cancelled[0] = True

    with pytest.raises(ProtocolError, match='cancelled'):
        execute_action(client, 'left', cancelled=lambda: cancelled[0], wait=wait)
    assert client.transport.input_log == [(4, 0, 0), (4, 1, 0), (4, 0, 0), (0, 0, 0)]
    assert client.transport.keys == 0


def test_failed_wheel_ack_attempts_release_without_retrying_wheel():
    client = client_fixture()
    original = client.flush_inputs
    calls = []

    def flush():
        original()
        calls.append(1)
        if len(calls) == 1:
            raise ProtocolError('Lost acknowledgement.')

    client.flush_inputs = flush
    with pytest.raises(ProtocolError, match='Lost'):
        execute_action(client, 'range_up', wait=lambda _: None)
    assert without_lease_renewals(client.transport.input_log) == [(8, 0, 0), (8, 0, 1), (0, 0, 0)]
    assert client.transport.keys == 0


def test_favorites_persist_and_reject_unknown_or_excess_actions(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    path = tmp_path / 'favorites.json'
    monkeypatch.setenv('DS100_QUICK_SETTINGS', str(path))
    panel = QuickPanel()
    assert panel.favorite_ids == list(DEFAULT_FAVORITES)
    panel.save_favorites(['left', 'auto'])
    restored = QuickPanel()
    assert restored.favorite_ids == ['left', 'auto']
    assert restored.buttons['favorite_0'].text() == '水平 ←'
    with pytest.raises(ValueError):
        restored.save_favorites(['auto', 'auto'])
    with pytest.raises(ValueError):
        restored.save_favorites(['flash'])
    with pytest.raises(ValueError):
        restored.save_favorites(['auto', 'left', 'right', 'up', 'down'])
    path.write_text('{invalid', encoding='utf-8')
    assert QuickPanel().favorite_ids == list(DEFAULT_FAVORITES)
    panel.close()
    restored.close()


def test_quick_button_waits_for_screen_job_and_serializes_all_input():
    class SlowScreen(SimulatorTransport):
        def exchange(self, cdb):
            if cdb[1] == SCREEN_READ:
                time.sleep(.001)
            return super().exchange(cdb)

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.discovery_timer.stop()
    window.show()
    client = Client(SlowScreen())
    window.experimental_connected((client, client.info()))
    window.read_screen()
    assert window.worker is not None
    window.set_raw_controls(False)
    QTest.mouseClick(window.quick_panel.buttons['time_up'], Qt.LeftButton)
    assert window.action_pending == 'time_up'
    assert window.quick_panel.buttons['range_up'].isEnabled()
    idle(app, window)
    assert without_lease_renewals(client.transport.input_log) == [(8, 0, 0), (8, 1, 0), (0, 0, 0)]
    assert '已执行' in window.status.text()
    assert window.quick_panel.buttons['range_up'].isEnabled()
    window.close()
    app.processEvents()


def test_following_quick_clicks_are_queued_and_execute_after_key_release():
    app = QApplication.instance() or QApplication([])
    window = MainWindow(); window.discovery_timer.stop()
    client = client_fixture()
    window.experimental_connected((client, client.info()))
    window.set_raw_controls(False); window.show(); app.processEvents()
    QTest.mouseClick(window.quick_panel.buttons['time_up'], Qt.LeftButton)
    QTest.mouseClick(window.quick_panel.buttons['time_down'], Qt.LeftButton)
    assert len(window.queued_actions) == 1
    assert all(button.isEnabled() for button in window.quick_panel.buttons.values())
    idle(app, window)
    assert not window.queued_actions
    wheels = [item for item in client.transport.input_log if item[1] or item[2]]
    assert wheels == [(8, 1, 0), (8, -1, 0)]
    assert client.transport.keys == 0
    window.close()


def test_switching_to_named_controls_releases_old_hold():
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.discovery_timer.stop()
    client = client_fixture()
    window.experimental_connected((client, client.info()))
    window.set_raw_controls(True)
    window.input_panel.holds['M'].setChecked(True)
    idle(app, window)
    assert client.transport.keys == 4
    window.set_raw_controls(False)
    idle(app, window)
    assert client.transport.keys == 0 and window.input_panel.mask == 0
    assert not window.input_panel.holds['M'].isChecked()
    window.close()


def test_toolbar_capture_saves_original_pixels_without_dialog(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv('DS100_CAPTURE_DIR', str(tmp_path))
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *args: pytest.fail('Unexpected save dialog.'))
    window = MainWindow()
    window.discovery_timer.stop()
    pixels = np.arange(480 * 320, dtype=np.uint32).astype(np.uint16).reshape(320, 480)
    window.show_pixels(pixels, .02)
    window.save_button.click()
    paths = list(tmp_path.glob('DS100-*.png'))
    assert len(paths) == 1
    from PySide6.QtGui import QImage
    saved = QImage(str(paths[0])).convertToFormat(QImage.Format_RGB32)
    assert saved == window.last_image.convertToFormat(QImage.Format_RGB32)
    assert '截图已保存' in window.status.text()
    window.close()


@pytest.mark.parametrize('key, mask', [('P', 1), ('A', 2), ('M', 4), ('E', 8)])
def test_each_raw_key_has_independent_hold_and_paced_double_click(key, mask):
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.discovery_timer.stop()
    client = client_fixture()
    window.experimental_connected((client, client.info()))
    window.show()
    app.processEvents()
    panel = window.input_panel
    panel.holds[key].setChecked(True)
    idle(app, window)
    assert client.transport.keys == mask
    panel.holds[key].setChecked(False)
    # The native minimum press duration also applies to a fast checkbox click.
    QTest.qWait(100)
    idle(app, window)
    deadline = time.monotonic() + .5
    while client.transport.keys and time.monotonic() < deadline:
        app.processEvents(); QTest.qWait(5)
    assert client.transport.keys == 0
    client.transport.input_log.clear()
    QTest.mouseClick(panel.double_buttons[key], Qt.LeftButton)
    idle(app, window)
    assert client.transport.input_log == [(mask, 0, 0), (0, 0, 0), (mask, 0, 0), (0, 0, 0)]
    assert not any(checkbox.isChecked() for checkbox in panel.holds.values())
    window.close()
