from pathlib import Path
import time
import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from PySide6.QtGui import QRawFont
from ds100.app import MainWindow

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def wait_for_task(app, window, timeout=5):
    deadline = time.monotonic() + timeout
    while window.worker is not None and time.monotonic() < deadline:
        app.processEvents()
        QTest.qWait(10)
    app.processEvents()
    assert window.worker is None


def test_window_simulator_mirror_wave_and_run_state(app):
    window = MainWindow()
    window.show()
    app.processEvents()
    font = QRawFont.fromFont(app.font())
    assert font.supportsCharacter(ord("A"))
    assert font.supportsCharacter(ord("示"))
    assert not window.screen_button.isEnabled()
    window.source.setCurrentIndex(2)
    window.connect_now()
    wait_for_task(app, window)
    assert window.info["source"] == "simulator"
    assert window.last_image.size().width() == 480
    assert "模拟器测试画面" in window.screen_note.text()
    evidence = ROOT / "evidence" / "software-tests"
    evidence.mkdir(parents=True, exist_ok=True)
    assert window.last_image.save(str(evidence / "simulator-frame.png"))
    window.grab().save(str(evidence / "app-mirror.png"))
    window.read_wave()
    wait_for_task(app, window)
    assert len(window.plot.x) == 4096
    window.grab().save(str(evidence / "app-waveform.png"))
    window.set_run(False)
    wait_for_task(app, window)
    assert window.client.transport.running is False
    window.mirror.setChecked(True)
    first_frame = window.mirror_count
    deadline = time.monotonic() + 5
    while window.mirror_count < first_frame + 2 and time.monotonic() < deadline:
        app.processEvents()
        QTest.qWait(10)
    assert window.mirror_count >= first_frame + 2
    assert window.mirror.isChecked()
    window.mirror.setChecked(False)
    wait_for_task(app, window)
    window.source.setCurrentIndex(0)
    assert window.client is None
    assert not window.timer.isActive()
    window.close()
    app.processEvents()
