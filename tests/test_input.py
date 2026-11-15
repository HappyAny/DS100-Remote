from pathlib import Path
import time
import pytest

from ds100.protocol import Client, ProtocolError, INPUT_STATE, SCREEN_READ
from ds100.simulator import SimulatorTransport


def test_queued_input_is_sent_between_screen_tiles():
    class Instrumented(SimulatorTransport):
        def __init__(self):
            super().__init__()
            self.commands = []
            self.tiles = 0

        def exchange(self, cdb):
            self.commands.append(cdb[1])
            if cdb[1] == SCREEN_READ:
                self.tiles += 1
                if self.tiles == 2:
                    client.queue_input(4, -3, 2)
            return super().exchange(cdb)

    transport = Instrumented()
    client = Client(transport)
    client.info()
    client.screen()
    assert transport.commands[1:7] == [SCREEN_READ, SCREEN_READ, INPUT_STATE, INPUT_STATE, INPUT_STATE, SCREEN_READ]
    assert [entry for entry in transport.input_log if entry[1] or entry[2]] == [(4, -3, 2)]
    client.queue_input(0)
    client.flush_inputs()
    assert transport.keys == 0


@pytest.mark.parametrize('args', [(16, 0, 0), (0, 128, 0), (0, 0, -128), (True, 0, 0)])
def test_input_arguments_rejected_before_usb(args):
    client = Client(SimulatorTransport())
    client.info()
    with pytest.raises(ProtocolError, match='invalid'):
        client.queue_input(*args)


def test_input_unavailable_on_screen_only_firmware(native_transport):
    client = Client(native_transport)
    client.info()
    with pytest.raises(ProtocolError, match='no key'):
        client.queue_input(1)


def test_gui_controls_operate_while_screen_worker_is_busy():
    from PySide6.QtWidgets import QApplication
    from PySide6.QtTest import QTest
    from PySide6.QtCore import Qt
    from ds100.app import MainWindow

    class SlowScreen(SimulatorTransport):
        def exchange(self, cdb):
            if cdb[1] == SCREEN_READ:
                time.sleep(.001)
            return super().exchange(cdb)

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.show()
    transport = SlowScreen()
    client = Client(transport)
    window.experimental_connected((client, client.info()))
    window.update_controls()
    window.read_screen()
    assert window.worker is not None
    assert window.input_panel.key_buttons['P'].isEnabled()
    QTest.mouseClick(window.input_panel.key_buttons['P'], Qt.LeftButton)
    deadline = time.monotonic() + 5
    while (window.worker is not None or window.input_panel.mask) and time.monotonic() < deadline:
        app.processEvents()
        QTest.qWait(10)
    assert transport.running is False
    assert transport.keys == 0
    assert any(item[0] == 1 for item in transport.input_log)
    window.input_panel.holds['M'].setChecked(True)
    window.input_panel.rotate(0, -4)
    deadline = time.monotonic() + 3
    while (client.has_pending_input() or window.worker is not None) and time.monotonic() < deadline:
        app.processEvents()
        QTest.qWait(10)
    assert (4, 4, 0) in transport.input_log
    window.grab().save(str(Path(__file__).resolve().parents[1] / 'evidence/software-tests/app-input.png'))
    window.input_panel.holds['M'].setChecked(False)
    window.close()
    app.processEvents()
    assert transport.keys == 0


def test_gui_automatic_connection_starts_mirror_without_manual_connect(monkeypatch):
    from PySide6.QtWidgets import QApplication
    from PySide6.QtTest import QTest
    import ds100.app as desktop
    import ds100.windows_scsi as windows
    app = QApplication.instance() or QApplication([])
    transport = SimulatorTransport()
    opened = []
    monkeypatch.setattr(desktop, 'find_drives', lambda: [Path('X:/')])

    def open_transport(path):
        opened.append(path)
        return transport

    monkeypatch.setattr(windows, 'ScsiTransport', open_transport)
    window = desktop.MainWindow()
    window.show()
    window.auto_connect.setChecked(True)
    deadline = time.monotonic() + 4
    while not window.mirror_count and time.monotonic() < deadline:
        app.processEvents()
        QTest.qWait(10)
    assert opened == [Path('X:/')]
    assert window.client is not None and window.mirror.isChecked()
    window.close()
    app.processEvents()


def test_double_e_and_long_p_timers_are_canceled_on_disconnect():
    from PySide6.QtWidgets import QApplication
    from PySide6.QtTest import QTest
    from ds100.input_panel import InputPanel
    app = QApplication.instance() or QApplication([])
    panel = InputPanel()
    events = []
    panel.changed.connect(lambda mask, a, b: events.append(mask))
    panel.set_available(True)
    panel.double_e()
    QTest.qWait(350)
    app.processEvents()
    assert events == [8, 0, 8, 0]
    panel.tap('P', 1300)
    panel.set_available(False)
    panel.set_available(True)
    events.clear()
    QTest.qWait(1350)
    app.processEvents()
    assert events == [] and panel.mask == 0
    panel.close()


def test_mirror_worker_keeps_reading_and_serializes_actions_without_gui_roundtrips():
    from PySide6.QtWidgets import QApplication
    from PySide6.QtTest import QTest
    from ds100.app import MainWindow
    from threading import get_ident

    class Counted(SimulatorTransport):
        count = 0
        threads = set()
        def exchange(self, cdb):
            if cdb[1] in (INPUT_STATE, SCREEN_READ):
                self.count += 1
                self.threads.add(get_ident())
            return super().exchange(cdb)

    app = QApplication.instance() or QApplication([])
    window = MainWindow(); window.discovery_timer.stop()
    transport = Counted(); client = Client(transport)
    window.experimental_connected((client, client.info())); window.show()
    window.mirror.setChecked(True)
    def until(condition):
        deadline = time.monotonic()+4
        while not condition() and time.monotonic()<deadline:
            app.processEvents(); QTest.qWait(5)
        assert condition()
    until(lambda: window.mirror_count>=3)
    worker = window.worker
    assert window.connect_button.isEnabled() and window.source.isEnabled()
    before = transport.count
    time.sleep(.15)  # Native reads must continue while the GUI cannot paint.
    assert transport.count > before+640
    window.set_raw_controls(False)
    window.request_quick_action('time_up')
    window.request_quick_action('time_down')
    until(lambda: window.action_pending is None and window.action_running is None and not window.queued_actions)
    assert window.worker is worker and len(transport.threads)==1
    assert [(a,b) for k,a,b in transport.input_log if a or b] == [(1,0),(-1,0)]
    assert transport.keys == 0
    window.mirror.setChecked(False)
    until(lambda: window.worker is None)
    window.close(); app.processEvents()
