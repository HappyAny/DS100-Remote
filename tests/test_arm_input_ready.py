"""Retain virtual detents until the original native wheel consumer is ready."""

import struct
import pytest
from test_arm_bounded_follow import BoundedArm
from test_arm_candidate import BUFFER, unicorn_windows_exception_handler
from ds100.protocol import INPUT_STATE, make_cdb, parse_response


class ReadyArm(BoundedArm):
    manifest_name = 'input-ready-manifest.json'
    image_name = 'v48-input-ready-decoded.bin'


MODEL, WIDGET, OUT = 0x10001000, 0x10002000, 0x20033000


def initialized(cls=ReadyArm):
    d = cls()
    d.mu.mem_write(0x200200AC, struct.pack('<I', MODEL))
    d.mu.mem_write(MODEL, struct.pack('<I', WIDGET))
    d.mu.mem_write(WIDGET, struct.pack('<I', 0x08072840))
    return d


def submit(d, keys=0, a=0, b=0):
    d.prepare(make_cdb(INPUT_STATE, 71, (keys,a&65535,b&65535,0)),state=0x7F)
    d.call(d.symbols['ds100_v48_poll_wrapper'])
    return struct.unpack('<HHhh',parse_response(d.replies[-1][3],INPUT_STATE,71))


def wheels(d):
    d.call(d.symbols['ds100_v48_read_wheels'],OUT)
    return bytes(d.mu.mem_read(OUT,8))


@pytest.mark.parametrize('flag', [0xE8,0x229])
def test_busy_native_widget_retains_virtual_wheel_until_ready(flag):
    d = initialized()
    d.mu.mem_write(WIDGET+flag,b'\x02')
    assert submit(d,0,1)[2:] == (1,0)
    assert wheels(d) == bytes(8)
    assert submit(d)[2:] == (1,0)
    d.mu.mem_write(WIDGET+flag,b'\x00')
    assert wheels(d) == struct.pack('<BBHBBH',1,0,1,0,0,0)
    assert wheels(d) == bytes(8)
    assert submit(d)[2:] == (0,0)


def test_modifier_edge_is_reported_before_its_virtual_wheel():
    d = initialized()
    submit(d,8,0,-1)
    assert wheels(d) == bytes(8)
    d.call(d.symbols['ds100_v48_read_keys'],OUT)
    assert d.mu.mem_read(OUT,2) == b'\x01\x03'
    assert wheels(d) == struct.pack('<BBHBBH',0,0,0,2,0,1)


def test_modal_menu_remains_operable_when_parent_is_dirty():
    d = initialized()
    d.mu.mem_write(WIDGET+0xE8,b'\x02')
    d.mu.mem_write(WIDGET+0x160,struct.pack('<I',0x10003000))
    submit(d,0,-1)
    assert wheels(d) == struct.pack('<BBHBBH',2,0,1,0,0,0)


@pytest.mark.parametrize('address,value', [(0x200200AC,0),(MODEL,0),(WIDGET,0x08072844)])
def test_unrecognized_widget_does_not_dereference_unchecked_memory_or_discard_wheel(address,value):
    d = initialized()
    d.mu.mem_write(address,struct.pack('<I',value))
    submit(d,0,1)
    assert wheels(d) == bytes(8)
    assert submit(d)[2:] == (1,0)


def test_physical_wheel_is_preserved_even_while_virtual_wheel_waits():
    d = initialized()
    d.mu.mem_write(WIDGET+0xE8,b'\x02')
    submit(d,0,1)
    d.mu.mem_write(0x2002CED8+12,struct.pack('<h',-3))
    assert wheels(d) == struct.pack('<BBHBBH',2,0,3,0,0,0)
    assert submit(d)[2:] == (1,0)


def test_one_50ms_deadline_still_bounds_total_capture_stall():
    d = initialized()
    p = d.activate_for_capture()
    capacity = struct.unpack('<I',d.mu.mem_read(p+4,4))[0]
    d.mu.mem_write(p+16,struct.pack('<I',capacity-990))
    d.after_first_reply=True; d.tick_step=1
    start=d.ticks
    for _ in range(20):
        d.call(d.symbols['ds100_stream_point'],1,2,1234)
    assert 50 <= d.ticks-start <= 52
    assert struct.unpack('<I',d.mu.mem_read(p+36,4))[0] == 4
