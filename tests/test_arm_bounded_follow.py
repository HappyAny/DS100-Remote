"""Bound total GUI blocking after a failed drawing-queue drain."""

import struct
import pytest
from test_arm_hardware_follow import FollowArm
from test_arm_candidate import STATE, unicorn_windows_exception_handler


class BoundedArm(FollowArm):
    manifest_name = 'bounded-manifest.json'
    image_name = 'v48-bounded-follow-decoded.bin'


@pytest.mark.parametrize('bounded', [False, True])
def test_first_drain_timeout_ends_capture_instead_of_blocking_every_record(bounded):
    d = BoundedArm() if bounded else FollowArm()
    pointer = d.activate_for_capture()
    capacity = struct.unpack('<I', d.mu.mem_read(pointer + 4, 4))[0]
    d.mu.mem_write(pointer + 16, struct.pack('<I', capacity - 990))
    d.mu.mem_write(STATE + 1, b'\0')
    d.after_first_reply = True
    d.tick_step = 1
    start = d.ticks
    for _ in range(20):
        d.call(d.symbols['ds100_stream_point'], 1, 2, 1234)
    elapsed = d.ticks - start
    mode, _, fault = struct.unpack('<3I', d.mu.mem_read(pointer + 36, 12))
    if bounded:
        assert elapsed <= 17 and (mode, fault) == (4, 1)
    else:
        assert elapsed >= 300 and mode == 2


def test_freeze_does_not_hide_a_full_queue_fault():
    d = BoundedArm()
    pointer = d.activate_for_capture()
    capacity = struct.unpack('<I', d.mu.mem_read(pointer + 4, 4))[0]
    d.mu.mem_write(pointer + 16, struct.pack('<I', capacity - 4))
    d.mu.mem_write(pointer + 40, struct.pack('<I', 1))
    with pytest.raises(ValueError, match='requires a new baseline'):
        d.command(3)
    assert struct.unpack('<I', d.mu.mem_read(pointer + 36, 4))[0] == 4


def test_bounded_follow_keeps_timer_untouched_and_releases_heap():
    d = BoundedArm()
    free = d.call(0x08010514)
    assert d.mu.mem_read(0x08027874, 4) == bytes.fromhex('4ff4fa70')
    d.command(1)
    d.command(0)
    assert d.call(0x08010514) == free
