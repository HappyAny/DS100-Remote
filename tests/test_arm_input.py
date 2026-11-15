import json
import struct
import pytest

from ds100.protocol import make_cdb, parse_response, INFO
from test_arm_candidate import ArmDevice, symbols, unicorn_windows_exception_handler

INPUT, OUTPUT, PHYSICAL, TICKS = 8, 0x20032000, 0x2002CED8, 0x20027E90


def initialized():
    device = ArmDevice(background=True, controls=True)
    device.mu.mem_write(0x2002FFF0, b'\xa5' * 16)
    device.call(symbols(controls=True)['ds100_v48_controls_init'])
    assert bytes(device.mu.mem_read(0x2002FFF0, 16)) == b'\xa5' * 16
    return device


def request_input(device, keys, wheel1=0, wheel2=0):
    cdb = make_cdb(INPUT, 601, (keys, wheel1 & 65535, wheel2 & 65535, 0))
    device.prepare(cdb, state=0x7F)
    device.call(symbols(controls=True)['ds100_v48_poll_wrapper'])
    return struct.unpack('<HHhh', parse_response(device.replies[-1][3], INPUT, 601))


def key_event(device):
    device.call(symbols(controls=True)['ds100_v48_read_keys'], OUTPUT)
    return bytes(device.mu.mem_read(OUTPUT, 2))


@pytest.mark.parametrize('key', range(4))
def test_virtual_keys_use_original_press_release_event_encoding(key):
    device = initialized()
    assert request_input(device, 1 << key)[:2] == (1 << key, 600)
    assert key_event(device) == bytes((1, key))
    assert key_event(device)[0] == 0
    request_input(device, 0)
    assert key_event(device) == bytes((2, key))
    assert key_event(device)[0] == 0


def test_physical_and_virtual_ownership_merge_without_releasing_physical_key():
    device = initialized()
    request_input(device, 4)
    device.mu.mem_write(PHYSICAL + 5, b'\x01')  # physical M press
    assert key_event(device) == b'\x01\x02'
    request_input(device, 0)
    assert key_event(device)[0] == 0  # physical M is still down
    device.mu.mem_write(PHYSICAL + 5, b'\x02')
    assert key_event(device) == b'\x02\x02'


def test_modifier_keys_and_both_wheels_are_consumed_in_original_priority_order():
    device = initialized()
    request_input(device, 12, 3, -2)
    assert key_event(device) == b'\x01\x02'
    assert key_event(device) == b'\x01\x03'
    reader = symbols(controls=True)['ds100_v48_read_wheels']
    device.call(reader, OUTPUT)
    assert struct.unpack('<BBHBBH', device.mu.mem_read(OUTPUT, 8)) == (1, 0, 3, 0, 0, 0)
    device.call(reader, OUTPUT)
    assert struct.unpack('<BBHBBH', device.mu.mem_read(OUTPUT, 8)) == (0, 0, 0, 2, 0, 2)
    device.call(reader, OUTPUT)
    assert bytes(device.mu.mem_read(OUTPUT, 8)) == bytes(8)


def test_physical_wheel_is_preserved_and_virtual_delta_is_not_lost():
    device = initialized()
    request_input(device, 0, -7, 0)
    device.mu.mem_write(PHYSICAL + 12, struct.pack('<h', 4))
    reader = symbols(controls=True)['ds100_v48_read_wheels']
    device.call(reader, OUTPUT)
    assert struct.unpack('<BBHBBH', device.mu.mem_read(OUTPUT, 8)) == (1, 0, 4, 0, 0, 0)
    device.call(reader, OUTPUT)
    assert struct.unpack('<BBHBBH', device.mu.mem_read(OUTPUT, 8)) == (2, 0, 7, 0, 0, 0)


@pytest.mark.parametrize('start', [0, 0xFFFFFF00])
def test_input_lease_releases_keys_and_discards_wheels_on_disconnect(start):
    device = initialized()
    device.mu.mem_write(TICKS, struct.pack('<I', start))
    request_input(device, 4, 9, 8)
    assert key_event(device) == b'\x01\x02'
    device.mu.mem_write(TICKS, struct.pack('<I', (start + 599) & 0xFFFFFFFF))
    assert key_event(device)[0] == 0
    device.mu.mem_write(TICKS, struct.pack('<I', (start + 600) & 0xFFFFFFFF))
    assert key_event(device) == b'\x02\x02'
    device.call(symbols(controls=True)['ds100_v48_read_wheels'], OUTPUT)
    assert bytes(device.mu.mem_read(OUTPUT, 8)) == bytes(8)


def test_usb_initializes_once_without_opening_usb_menu_and_info_reports_input():
    device = initialized()
    device.prepare(make_cdb(INFO, 602), state=0x7F)
    poll = symbols(controls=True)['ds100_v48_poll_wrapper']
    device.call(poll)
    info = json.loads(parse_response(device.replies[-1][3], INFO, 602))
    assert info['input'] == {'command': 8, 'keys': ['P', 'A', 'M', 'E'], 'wheels': 2, 'lease_ms': 600, 'auto_usb': True}
    assert info['capabilities'] == ['screen', 'input']
    device.call(poll)
    device.call(0x0804523C)  # opening USB again uses the same guarded entry
    assert device.usb_start_calls == 1
    assert bytes(device.mu.mem_read(0x2002FFF0, 16)) == b'\xa5' * 16


def test_screen_client_does_not_renew_another_clients_held_key():
    device = initialized()
    request_input(device, 4)
    assert key_event(device) == b'\x01\x02'
    device.mu.mem_write(TICKS, struct.pack('<I', 400))
    device.prepare(make_cdb(INFO, 603), state=0x7F)
    device.call(symbols(controls=True)['ds100_v48_poll_wrapper'])
    device.mu.mem_write(TICKS, struct.pack('<I', 600))
    assert key_event(device) == b'\x02\x02'
