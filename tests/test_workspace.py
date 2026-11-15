"""Check input gestures, image fidelity, and navigation in the workspace."""

import numpy as np
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QImage, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from ds100.app import MainWindow, ScreenLabel
from ds100.input_panel import InputPanel


def test_rotary_gestures_and_step_buttons_preserve_wheel_identity():
    app = QApplication.instance() or QApplication([])
    panel = InputPanel()
    panel.resize(620, 94)
    panel.set_available(True)
    events = []
    panel.changed.connect(lambda *args: events.append(args))
    panel.show()
    app.processEvents()
    dial = panel.dials[1]
    QTest.mousePress(dial, Qt.LeftButton, pos=QPoint(32, 5))
    QTest.mouseMove(dial, QPoint(59, 32))
    QTest.mouseRelease(dial, Qt.LeftButton, pos=QPoint(59, 32))
    assert events and all(mask == 0 and a == 0 and b > 0 for mask, a, b in events)
    events.clear()

    def scroll(amount):
        event = QWheelEvent(QPointF(32, 32), QPointF(dial.mapToGlobal(QPoint(32, 32))),
                            QPoint(0, 0), QPoint(0, amount), Qt.NoButton, Qt.NoModifier,
                            Qt.NoScrollPhase, False)
        QApplication.sendEvent(dial, event)

    scroll(60)
    assert events == []
    scroll(60)
    assert events == [(0, 0, 1)]
    panel.holds['M'].setChecked(True)
    events.clear()
    QTest.keyClick(dial, Qt.Key_Left)
    assert events == [(4, 0, -1)]
    step = next(button for button in panel.actions if button.accessibleName() == '拨轮 1 增加一步')
    QTest.mouseClick(step, Qt.LeftButton)
    assert events[-1] == (4, -1, 0)
    panel.set_available(False)
    events.clear()
    scroll(120)
    dial.turn(1)
    QTest.keyClick(dial, Qt.Key_Right)
    assert events == [] and panel.mask == 0
    panel.close()


def test_rotary_step_symbols_match_selection_pan_and_scale_modes():
    app = QApplication.instance() or QApplication([])
    panel = InputPanel(); panel.set_available(True)
    labels = lambda: [[button.text() for button in row] for row in panel.step_buttons]
    assert labels() == [['↑', '↓'], ['−', '+']]
    panel.press('M', 'test')
    assert labels() == [['←', '→'], ['↓', '↑']]
    events = []
    panel.changed.connect(lambda *args: events.append(args))
    panel.dials[0].turn(1)
    panel.dials[1].turn(1)
    assert events == [(4, -1, 0), (4, 0, 1)]
    panel.set_available(False); panel.set_available(True)
    panel.press('E', 'test')
    assert labels() == [['−', '+'], ['−', '+']]
    events.clear(); panel.dials[0].turn(1)
    assert events == [(8, 1, 0)]
    panel.close()


def test_background_mirror_keeps_page_and_minimum_layout_visible():
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.discovery_timer.stop()
    window.resize(640, 510)
    window.show()
    app.processEvents()
    assert window.size().width() == 640
    assert window.screen_label.width() >= 480 and window.screen_label.height() >= 320
    window.info = {'source': 'simulator'}
    window.tabs.setCurrentIndex(2)
    assert not window.control_modes.isVisible()
    pixels = np.zeros((320, 480), dtype=np.uint16)
    window.show_pixels(pixels, .02)
    assert window.tabs.currentIndex() == 2
    assert window.navigation[2].isChecked()
    assert window.origin_badge.text() == '演示数据 · 非实机'
    assert window.page_title.text() == '文件'
    window.navigation[0].trigger()
    app.processEvents()
    assert window.tabs.currentIndex() == 0
    assert window.control_modes.isVisible()
    assert window.input_panel.isVisible()
    assert not window.quick_panel.isVisible()
    window.set_raw_controls(False)
    app.processEvents()
    assert window.quick_panel.isVisible()
    window.set_raw_controls(True)
    app.processEvents()
    assert window.input_panel.isVisible()
    for control in window.input_panel.actions:
        if control.isVisible():
            assert window.input_panel.rect().contains(control.mapTo(window.input_panel, control.rect().topLeft()))
            assert window.input_panel.rect().contains(control.mapTo(window.input_panel, control.rect().bottomRight()))
    assert set(window.input_panel.holds) == set('PAME')
    assert set(window.input_panel.double_buttons) == set('PAME')
    label_rect = window.screen_label.rect()
    image_rect = window.screen_label.image_rect()
    assert label_rect.contains(image_rect.toRect())
    assert abs(image_rect.width() / image_rect.height() - 1.5) < .005
    window.close()
    app.processEvents()


def test_native_mirror_paints_source_pixels_without_changes():
    app = QApplication.instance() or QApplication([])
    pixels = ((np.arange(480 * 320, dtype=np.uint32) * 179) % 65536).astype(np.uint16)
    source = QImage(pixels.tobytes(), 480, 320, 960, QImage.Format_RGB16).copy()
    label = ScreenLabel()
    label.resize(640, 480)
    label.native_size = True
    label.set_image(source)
    label.show()
    app.processEvents()
    bounds = label.image_rect().toRect()
    assert bounds.size() == source.size()
    painted = label.grab().toImage().copy(bounds).convertToFormat(QImage.Format_RGB32)
    expected = source.convertToFormat(QImage.Format_RGB32)
    assert painted.sizeInBytes() == expected.sizeInBytes()
    assert bytes(painted.constBits()) == bytes(expected.constBits())
    assert label.painted_changed_frames == 1
    label.grab()
    assert label.painted_changed_frames == 1
    label.set_image(source.copy())
    label.grab()
    assert label.painted_changed_frames == 1
    label.close()
