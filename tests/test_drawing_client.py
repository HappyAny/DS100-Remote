"""Exercise C sender packets through the client and GUI-facing frame API."""

import ctypes as C
import json
import struct
import time
import numpy as np
import pytest
from ds100.protocol import CDB, Client, DRAW_STREAM, INFO, INPUT_STATE, SCREEN_READ, SCREEN, make_response
from ds100.drawing import PACKET
from test_draw_queue import Sender, sender_library


class StreamTransport:
    def __init__(self, lib):
        self.sender = Sender(lib)
        self.starts = 0
        self.closed = False
        self.force_overflow = False
        self.requests = []

    def device_info(self):
        return {'protocol': 1, 'source': 'simulator', 'screen_width': 480, 'screen_height': 320,
                'capabilities': ['screen', 'input'],
                'input': {'command': 8, 'keys': ['P', 'A', 'M', 'E'], 'wheels': 2, 'lease_ms': 600},
                'draw_stream': {'command': 9, 'packet_bytes': 4096, 'format': 'DRW1', 'queue_bytes': 16384}}

    def exchange(self, cdb):
        _, cmd, _, _, sequence, action, arg1, arg2, arg3 = CDB.unpack(cdb)
        self.requests.append((cmd, action, arg1, arg2, arg3))
        if cmd == INFO:
            payload = json.dumps(self.device_info()).encode()
        elif cmd == 8:
            payload = struct.pack('<HHhh', action, 600, 0, 0)
        elif cmd == SCREEN_READ:
            payload = SCREEN.pack(0, action, arg1, arg2, arg3, 0, 0, 0)
            payload += self.sender.expected[arg1:arg1 + arg3, action:action + arg2].astype('<u2').tobytes()
        else:
            assert cmd == DRAW_STREAM
            if action == 1:
                self.starts += 1
                self.sender.lib.ds100_draw_start(C.byref(self.sender.state), self.starts)
            if action == 0:
                self.closed = True
                return make_response(cmd, sequence, b'')
            if self.force_overflow:
                self.force_overflow = False
                return make_response(cmd, sequence, b'The drawing stream requires a new baseline.', error=True)
            payload = self.sender.pop()
            assert PACKET.size <= len(payload) <= 4076
        return make_response(cmd, sequence, payload)

    def close(self):
        pass


def test_client_returns_none_for_empty_polls_and_only_complete_new_frames(sender_library):
    transport = StreamTransport(sender_library)
    client = Client(transport)
    client.info()
    assert client.screen_mode == 'draw-stream'
    first = client.screen()
    np.testing.assert_array_equal(first, transport.sender.expected)
    assert client.draw_session.presentations == 1
    assert client.screen() is None
    assert client.draw_session.presentations == 1
    state = C.byref(transport.sender.state)
    transport.sender.lib.ds100_draw_point(state, 10, 20, 1234)
    assert client.screen() is None
    transport.sender.lib.ds100_draw_present(state)
    next_frame = client.screen()
    assert next_frame[20, 10] == 1234
    assert client.draw_session.presentations == 2
    assert client.draw_session.changed_frames == 2
    client.close()
    assert transport.closed


def test_client_resynchronizes_after_sender_queue_loss(sender_library):
    transport = StreamTransport(sender_library)
    client = Client(transport)
    client.info()
    client.screen()
    transport.force_overflow = True
    pixels = client.screen()
    np.testing.assert_array_equal(pixels, transport.sender.expected)
    assert transport.starts == 2 and client.draw_session.resyncs == 1
    client.close()


def test_gesture_poll_drains_without_starting_baseline_while_keys_are_held(sender_library):
    transport = StreamTransport(sender_library)
    client = Client(transport)
    client.info()
    client.screen()
    state = C.byref(transport.sender.state)
    transport.sender.lib.ds100_draw_point(state, 10, 20, 1234)
    transport.sender.lib.ds100_draw_present(state)
    frame, occupied = client.draw_session.poll_active()
    assert occupied and frame[20, 10] == 1234
    assert client.draw_session.presentations == 2
    assert client.draw_session.poll_active() == (None, False)
    transport.force_overflow = True
    assert client.draw_session.poll_active() == (None, False)
    assert client.draw_session.replay is None and client.draw_session.resyncs == 1
    assert transport.starts == 1
    assert client.draw_session.poll_active() == (None, False)
    assert transport.starts == 1
    client.close()


def test_client_drains_nonempty_baseline_packets_without_idle_delay(sender_library, monkeypatch):
    def unexpected_sleep(_):
        raise AssertionError('An occupied drawing packet must drain immediately.')
    monkeypatch.setattr('ds100.drawing_client.time.sleep', unexpected_sleep)
    client = Client(StreamTransport(sender_library))
    client.info()
    np.testing.assert_array_equal(client.screen(), client.transport.sender.expected)
    client.close()


def test_gui_enables_inputs_after_complete_baseline(sender_library):
    from PySide6.QtWidgets import QApplication
    from PySide6.QtTest import QTest
    from ds100.app import MainWindow
    app = QApplication.instance() or QApplication([])
    client = Client(StreamTransport(sender_library))
    window = MainWindow()
    window.auto_connect.setChecked(False)
    window.experimental_connected((client, client.info()))
    assert window.input_panel.available
    window.read_screen()
    deadline = time.monotonic() + 5
    while window.worker is not None and time.monotonic() < deadline:
        app.processEvents()
        QTest.qWait(10)
    assert window.worker is None and window.last_image is not None
    assert window.input_panel.available
    window.close()
    app.processEvents()


def test_menu_stops_old_epoch_before_input_and_resumes_after_release(sender_library, monkeypatch):
    clock = [100.]
    monkeypatch.setattr('ds100.protocol.time.monotonic', lambda: clock[0])
    monkeypatch.setattr('ds100.protocol.time.sleep', lambda dt: clock.__setitem__(0, clock[0] + dt))
    transport = StreamTransport(sender_library)
    client = Client(transport)
    client.info()
    client.screen()
    first = len(transport.requests)
    client.queue_input(4)
    client.flush_inputs()
    assert client.menu_resets == 0 and client.screen_mode == 'draw-stream'
    assert transport.requests[first][:2] == (8, 4)
    first = len(transport.requests)
    client.queue_input(0)
    client.flush_inputs()
    assert transport.requests[first][:2] == (DRAW_STREAM, 2)
    stopped = next(i for i in range(first, len(transport.requests)) if transport.requests[i][:2] == (DRAW_STREAM, 0))
    assert all(item[:2] == (DRAW_STREAM, 2) for item in transport.requests[first:stopped])
    assert transport.requests[stopped + 1][:2] == (8, 0)
    assert not any(item[0] == SCREEN_READ for item in transport.requests[first:])
    assert client.screen_mode == 'menu-readback' and client.menu_resets == 1
    clock[0] += .3
    np.testing.assert_array_equal(client.screen(), transport.sender.expected)
    assert client.screen_mode == 'draw-stream' and transport.starts == 2
    assert client.draw_session.resyncs == 0
    client.close()


def test_named_m_modifier_keeps_epoch_and_polls_during_hold(sender_library):
    from ds100.quick_actions import execute_action
    transport = StreamTransport(sender_library)
    client = Client(transport)
    client.info()
    client.screen()
    first = len(transport.requests)
    execute_action(client, 'left', wait=lambda _: None)
    assert client.menu_resets == 0 and client.screen_mode == 'draw-stream'
    assert not client.modifier_gesture
    states = [(item[1], item[2], item[3]) for item in transport.requests[first:] if item[0] == 8]
    assert states[0] == (4, 0, 0) and states[-1] == (0, 0, 0)
    assert [item for item in states if item[1] or item[2]] == [(4, 1, 0)]
    assert any(item[:2] == (DRAW_STREAM, 2) for item in transport.requests[first:])
    assert not any(item[:2] in ((DRAW_STREAM, 0), (DRAW_STREAM, 1)) for item in transport.requests[first:])
    client.close()


@pytest.mark.parametrize('action, dx, dy', [('left', -1, 0), ('up', 0, -1)])
def test_rle7_motion_publishes_moved_waveform_while_held(sender_library, monkeypatch, action, dx, dy):
    from ds100.quick_actions import execute_motion

    clock = [100.]
    monkeypatch.setattr('ds100.protocol.time.monotonic', lambda: clock[0])

    def advance(seconds):
        clock[0] += seconds

    monkeypatch.setattr('ds100.protocol.time.sleep', advance)

    class Rle7Transport(StreamTransport):
        def __init__(self, lib):
            super().__init__(lib)
            self.x, self.y = 160, 96
            self.sender.expected.fill(0)
            self.sender.expected[self.y, self.x] = 65535

        def device_info(self):
            info = super().device_info()
            del info['source']
            info['build'] = 'background-pc-rle7'
            info['input']['key_ack'] = 'reported-v1'
            return info

        def exchange(self, cdb):
            _, cmd, _, _, sequence, keys, wheel1, wheel2, _ = CDB.unpack(cdb)
            advance(.014 if cmd == SCREEN_READ else .002)
            response = super().exchange(cdb)
            if cmd != INPUT_STATE:
                return response
            if keys == 4 and (wheel1 or wheel2):
                state = C.byref(self.sender.state)
                self.sender.lib.ds100_draw_point(state, self.x, self.y, 0)
                self.sender.expected[self.y, self.x] = 0
                self.x -= wheel1
                self.y -= wheel2
                self.sender.lib.ds100_draw_point(state, self.x, self.y, 65535)
                self.sender.expected[self.y, self.x] = 65535
                self.sender.expected[150, 430] += 1
                self.sender.lib.ds100_draw_point(state, 430, 150, self.sender.expected[150, 430])
                self.sender.lib.ds100_draw_present(state)
            return make_response(cmd, sequence, struct.pack('<HHhh', keys | (keys << 8), 600, 0, 0))

    transport = Rle7Transport(sender_library)
    client = Client(transport)
    client.info()
    client.screen()
    start = len(transport.requests)
    deadline = clock[0] + .37

    class Released:
        def is_set(self):
            return clock[0] >= deadline

    positions = []

    def collect(pixels, _):
        assert client._input_keys == 4
        point, = np.argwhere(pixels[:, :416] == 65535)
        positions.append(tuple(point))

    client.on_input_frame = collect
    execute_motion(client, action, Released(), on_frame=collect, clock=lambda: clock[0], wait=advance)

    assert len(positions) >= 3
    assert positions == [(96 + dy * step, 160 + dx * step) for step in range(1, len(positions) + 1)]
    assert len(positions) == client.last_motion['detents']
    assert client.menu_resets == 0 and client.screen_mode == 'draw-stream'
    assert client._input_keys == 0
    assert [request for request in transport.requests[start:] if request[0] == INPUT_STATE][-1] == (
        INPUT_STATE, 0, 0, 0, 0)
    assert not any(cmd == SCREEN_READ or (cmd == DRAW_STREAM and action in (0, 1))
                   for cmd, action, *_ in transport.requests[start:])
    client.close()


def test_quick_gesture_waits_keep_stream_within_native_15ms_drain_deadline(sender_library, monkeypatch):
    from ds100.quick_actions import execute_action
    now = [100.]
    monkeypatch.setattr('ds100.protocol.time.monotonic', lambda: now[0])
    advance = lambda dt: now.__setitem__(0, now[0]+dt)
    monkeypatch.setattr('ds100.protocol.time.sleep', advance)

    class DeadlineTransport(StreamTransport):
        last_poll = None
        missed = 0
        def exchange(self, cdb):
            if cdb[1] == DRAW_STREAM and int.from_bytes(cdb[8:10], 'little') == 2:
                if self.last_poll is not None and now[0]-self.last_poll > .015:
                    self.force_overflow = True
                    self.missed += 1
                self.last_poll = now[0]
            return super().exchange(cdb)

    transport = DeadlineTransport(sender_library)
    client = Client(transport); client.info(); client.screen()
    for action in ('left', 'right', 'time_up', 'time_down', 'range_up', 'range_down'):
        execute_action(client, action, wait=advance)
    assert transport.missed == 0
    assert client.draw_session.resyncs == 0 and transport.starts == 1
    assert client._input_keys == 0
    client.close()


def test_fault_recovery_does_not_wait_indefinitely_for_modifier_release(sender_library):
    transport = StreamTransport(sender_library)
    client = Client(transport)
    client.info()
    client.screen()
    client.modifier_gesture = True
    client.queue_input(4, 1, 0)
    client.flush_inputs()
    transport.force_overflow = True
    assert client.screen() is None
    previews = []
    client.on_menu_preview = previews.append
    transport.sender.expected[144:160, 416:480] = 1234
    for _ in range(40):
        client.queue_input(4)
        client.screen()
    assert client.screen_mode == 'input-recovery'
    assert previews and previews[-1][150, 430] == 1234
    assert transport.starts == 1  # never freeze the native GUI with M held
    client.queue_input(4, -1, 0)
    client.flush_inputs()
    assert not client.has_pending_input()
    client.queue_input(0)
    client.flush_inputs()
    client.modifier_gesture = False
    assert client._input_keys == 0
    np.testing.assert_array_equal(client.screen(), transport.sender.expected)
    assert transport.starts == 2
    client.close()


def test_raw_m_hold_previews_live_readouts_before_release_and_can_finish_frame(sender_library):
    transport = StreamTransport(sender_library)
    client = Client(transport); client.info(); client.screen()
    client.fast_modifiers = False  # older firmware retains LCD fallback
    previews = []
    client.on_menu_preview = previews.append
    client.queue_input(4); client.flush_inputs()
    assert client.screen() is None
    transport.sender.expected[144:160, 416:480] = 1234
    client.queue_input(4, 1, 0)
    assert client.screen() is None
    assert previews[-1][150, 430] == 1234
    assert client._input_keys == 4
    completed = None
    for _ in range(40):
        completed = client.screen()
        if completed is not None:
            break
    assert completed is not None
    np.testing.assert_array_equal(completed, transport.sender.expected)
    assert client.menu_resets == 1
    client.queue_input(0); client.flush_inputs()
    assert client.screen() is None and client._menu_scan is None
    client.close()


def test_menu_transition_keeps_gui_hold_and_wheel_controls_available(sender_library):
    from PySide6.QtWidgets import QApplication
    from ds100.app import MainWindow
    app = QApplication.instance() or QApplication([])
    client = Client(StreamTransport(sender_library))
    info = client.info()
    client.screen()
    window = MainWindow()
    window.auto_connect.setChecked(False)
    window.experimental_connected((client, info))
    client.queue_input(4)
    client.flush_inputs()
    window.update_controls()
    assert client.screen_mode == 'draw-stream'
    assert window.input_panel.available
    client.queue_input(4, 1, 0)
    client.flush_inputs()
    assert client.menu_resets == 0
    client.queue_input(0)
    client.flush_inputs()
    window.close()
    app.processEvents()
