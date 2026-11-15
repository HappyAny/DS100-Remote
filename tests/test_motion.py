from threading import Event
import time
import numpy as np
import pytest
from PySide6.QtCore import Qt, QEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from ds100.protocol import Client, ProtocolError
from ds100.simulator import SimulatorTransport
from ds100.quick_actions import execute_motion
from ds100.app import MainWindow


def make_client():
    client = Client(SimulatorTransport())
    client.info()
    return client


def pump(app, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.005)


@pytest.mark.parametrize('action, expected', [('left', (4, 1, 0)), ('right', (4, -1, 0)),
                                           ('down', (4, 0, -1)), ('up', (4, 0, 1))])
def test_fast_click_moves_once_and_releases_without_extra_post_release_delay(action, expected):
    client = make_client()
    stopped = Event(); stopped.set()
    now = [0.]
    execute_motion(client, action, stopped, clock=lambda: now[0], wait=lambda dt: now.__setitem__(0, now[0] + dt))
    assert [entry for entry in client.transport.input_log if entry[1] or entry[2]] == [expected]
    assert client.transport.input_log[0] == (4, 0, 0) and client.transport.input_log[-1] == (0, 0, 0)
    assert .08 <= now[0] < .083
    assert not client.modifier_gesture


def test_long_press_repeats_with_one_continuous_modifier_and_stops_at_release():
    client = make_client()
    now = [0.]

    class Stop:
        def is_set(self):
            return now[0] >= .37

    execute_motion(client, 'left', Stop(), clock=lambda: now[0], wait=lambda dt: now.__setitem__(0, now[0] + dt))
    log = client.transport.input_log
    assert len(log) >= 5
    assert all(item[0] == 4 for item in log[:-1])
    assert sum(bool(item[1]) for item in log) >= 4
    assert log[-1] == (0, 0, 0)
    assert .37 <= now[0] < .373
    assert client.transport.keys == 0


def test_cancelled_motion_releases_the_modifier():
    client = make_client()
    now = [0.]
    with pytest.raises(ProtocolError, match='cancelled'):
        execute_motion(client, 'right', Event(), cancelled=lambda: now[0] >= .12,
                       clock=lambda: now[0], wait=lambda dt: now.__setitem__(0, now[0] + dt))
    assert [entry for entry in client.transport.input_log if entry[1] or entry[2]] == [(4, -1, 0)]
    assert client.transport.input_log[-1] == (0, 0, 0)
    assert not client.modifier_gesture


def test_ui_keeps_pressed_motion_enabled_and_releases_on_mouse_up_and_focus_loss():
    app = QApplication.instance() or QApplication([])
    window = MainWindow(); window.discovery_timer.stop()
    client = make_client()
    window.experimental_connected((client, client.info()))
    window.set_raw_controls(False); window.show(); app.processEvents()
    button = window.quick_panel.buttons['left']
    QTest.mousePress(button, Qt.LeftButton)
    pump(app, .38)
    assert button.isEnabled()
    assert window.quick_panel.buttons['right'].isEnabled()
    assert len([entry for entry in client.transport.input_log if entry == (4, 1, 0)]) >= 3
    QTest.mouseRelease(button, Qt.LeftButton)
    deadline = time.monotonic() + 2
    while window.worker is not None and time.monotonic() < deadline:
        app.processEvents(); QTest.qWait(5)
    assert client.transport.keys == 0 and window.worker is None
    size = len(client.transport.input_log)
    QTest.qWait(120)
    assert len(client.transport.input_log) == size
    QTest.mousePress(button, Qt.LeftButton)
    pump(app, .10)
    QApplication.sendEvent(window, QEvent(QEvent.WindowDeactivate))
    deadline = time.monotonic() + 2
    while window.worker is not None and time.monotonic() < deadline:
        app.processEvents(); QTest.qWait(5)
    assert window.worker is None and client.transport.keys == 0
    QTest.mouseRelease(button, Qt.LeftButton)
    window.close()


def test_partial_menu_preview_does_not_count_as_a_complete_frame():
    app = QApplication.instance() or QApplication([])
    window = MainWindow(); window.discovery_timer.stop(); window.show()
    frame = np.zeros((320, 480), dtype=np.uint16)
    window.show_pixels(frame, .01); app.processEvents(); window.screen_label.grab()
    before = (window.mirror_count, window.screen_label.painted_frames)
    frame[150:158, 420:480] = 65535
    window.show_menu_preview(frame); app.processEvents(); window.screen_label.grab()
    assert window.screen_label.preview
    assert (window.mirror_count, window.screen_label.painted_frames) == before
    window.show_pixels(frame, .02); app.processEvents(); window.screen_label.grab()
    assert not window.screen_label.preview
    assert window.mirror_count == before[0] + 1
    window.close()
