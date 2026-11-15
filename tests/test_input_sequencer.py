"""Independent native-input timing model: wheels may precede a new key edge."""

import json
import struct
from types import SimpleNamespace
import pytest
from ds100.protocol import Client, CDB, INFO, INPUT_STATE, make_response


class NativeInputModel:
    def __init__(self, clock):
        self.clock = clock
        self.remote = self.reported = 0
        self.pending = [0, 0]
        self.used = False
        self.last_tick = 0
        self.frozen = False
        self.offset_s = 75e-6
        self.offset_v = .15
        self.time_scale = 1e-3
        self.voltage_scale = 2.
        self.log = []

    def tick(self):
        if self.frozen or self.clock[0] - self.last_tick < .02:
            return
        self.last_tick = self.clock[0]
        # The adverse arrival phase: a wheel can be read before the newly
        # requested key edge reaches the GUI. A prior acknowledged key plus
        # an adoption interval prevents that ambiguity.
        if any(self.pending):
            if self.reported & 8:
                if self.pending[0]: self.time_scale *= 2
                if self.pending[1]: self.voltage_scale *= 2
                self.used = True
            self.pending = [0, 0]
        if self.remote != self.reported:
            if self.reported & 8 and not self.remote & 8 and not self.used:
                self.offset_s = self.offset_v = 0  # native quick-center
            if self.remote & 8 and not self.reported & 8:
                self.used = False
            self.reported = self.remote

    def exchange(self, cdb):
        _, cmd, _, _, sequence, keys, w1, w2, _ = CDB.unpack(cdb)
        self.tick()
        if cmd == INFO:
            payload = json.dumps({'protocol':1,'source':'simulator','screen_width':480,'screen_height':320,
                'capabilities':['input'],'input':{'command':8,'keys':['P','A','M','E'],'wheels':2,'lease_ms':600}}).encode()
        else:
            assert cmd == INPUT_STATE
            w1, w2 = struct.unpack('<hh', struct.pack('<HH', w1, w2))
            self.log.append((self.clock[0], keys, w1, w2))
            self.remote = keys
            self.pending[0] += w1; self.pending[1] += w2
            payload = struct.pack('<HHhh', keys, 600, *self.pending)
        return make_response(cmd, sequence, payload)


@pytest.fixture
def native_clock(monkeypatch):
    now = [0.]
    monkeypatch.setattr('ds100.protocol.time.monotonic', lambda: now[0])
    monkeypatch.setattr('ds100.protocol.time.sleep', lambda dt: now.__setitem__(0, now[0]+dt))
    return now


@pytest.mark.parametrize('wheel', [(1,0), (0,1)])
def test_E_range_change_preserves_numeric_offset_even_with_adverse_reader_phase(native_clock, wheel):
    native = NativeInputModel(native_clock); client = Client(native); client.info()
    client.queue_input(8, *wheel)
    client.queue_input(0)
    client.flush_inputs()
    native_clock[0] += .05; native.tick()
    assert native.offset_s == 75e-6 and native.offset_v == .15
    assert native.remote == native.reported == 0
    assert native.log[0][1:] == (8,0,0)
    first_wheel = next(item for item in native.log if item[2] or item[3])
    assert first_wheel[0] - native.log[0][0] >= .079
    assert native.time_scale == (2e-3 if wheel[0] else 1e-3)
    assert native.voltage_scale == (4. if wheel[1] else 2.)
    release = next(item for item in native.log if item[1] == 0)
    assert release[0] - native.log[0][0] >= .5
    assert max(b[0] - a[0] for a, b in zip(native.log, native.log[1:])) < .6


@pytest.mark.parametrize('keys', [0, 8])
def test_all_wheels_and_edges_wait_for_baseline_without_blocking_its_reader(native_clock, keys):
    native = NativeInputModel(native_clock); client = Client(native); client.info()
    client.draw_session = SimpleNamespace(replay=SimpleNamespace(ready=False))
    native.frozen = True
    client.queue_input(keys, 1, 0); client.queue_input(0)
    client.flush_inputs()
    assert native.log == [] and client.has_pending_input()
    native_clock[0] += .3
    client.request(INFO)
    assert native.log == []
    native.frozen = False
    client.draw_session = None
    client._modifier_ready_at = native_clock[0] + .08
    client.flush_inputs()
    native_clock[0] += .05; native.tick()
    assert native.time_scale == (2e-3 if keys else 1e-3) and native.offset_s == 75e-6
    assert native.reported == 0 and not client.has_pending_input()
