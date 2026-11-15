import json
import struct
import pytest

from ds100.protocol import DRAW_PROFILE, LINK_PROBE, INFO, make_cdb, parse_response
from test_arm_candidate import ArmDevice, symbols, unicorn_windows_exception_handler

PROFILE = 0x2002FF90


def initialized_profile():
    device = ArmDevice(background=True, profile=True)
    device.mu.mem_write(PROFILE + 64, b'\xa5' * 48)
    device.call(symbols(profile=True)['ds100_v48_profile_init'])
    assert bytes(device.mu.mem_read(PROFILE + 64, 48)) == b'\xa5' * 48
    assert struct.unpack('<II', device.mu.mem_read(PROFILE, 8)) == (0x35505244, 0)
    return device


def profile_request(device, action):
    device.prepare(make_cdb(DRAW_PROFILE, 502, (action, 0, 0, 0)), state=0x7F)
    device.call(symbols(profile=True)['ds100_v48_poll_wrapper'])
    return struct.unpack('<16I', parse_response(device.replies[-1][3], DRAW_PROFILE, 502))


@pytest.mark.parametrize('address,args,extra,field,count', [
    (0x0803CCB8, (9, 11, 0xF800, 0), (), 5, 1),
    (0x0803D622, (2, 4, 7, 6), (0x07E0,), 6, 1),
    (0x0803D688, (2, 4, 4, 5), (0x20032000,), 8, 2),
    (0x0804E03C, (2, 7, 4, 0x20032000), (), 7, 1),
    (0x0804E090, (2, 7, 4, 0x20032000), (), 7, 1),
])
def test_profile_hooks_preserve_original_lcd_writes_and_fifth_argument(address, args, extra, field, count):
    original, profiled = ArmDevice(), initialized_profile()
    profile_request(profiled, 1)
    for device in (original, profiled):
        device.use_actual_lcd = True
        device.mu.mem_write(0x2002CEF0, struct.pack('<HHHHH', 480, 320, 0x9488, 1, 0x2C))
        device.mu.mem_write(0x20032000, struct.pack('<6H', 0xF800, 0xF800, 0x07E0, 0x001F, 0x001F, 0xFFFF))
        device.call(address, *args, stack_args=extra)
    assert profiled.cursor_calls == original.cursor_calls
    assert profiled.lcd_writes == original.lcd_writes
    state = profile_request(profiled, 2)
    assert state[field] == count and state[10] > 0 and state[9] > 0
    assert state[4] == 1  # one nonempty modeled loop pass
    assert bytes(profiled.mu.mem_read(PROFILE + 64, 48)) == b'\xa5' * 48
    profile_request(profiled, 0)
    before = bytes(profiled.mu.mem_read(PROFILE, 64))
    profiled.call(0x0803CCB8, 1, 2, 0xFFFF)
    assert bytes(profiled.mu.mem_read(PROFILE, 64)) == before


def test_profile_info_state_initialization_and_usb_constant_probe():
    device = initialized_profile()
    device.prepare(make_cdb(INFO, 503), state=0x7F)
    device.call(symbols(profile=True)['ds100_v48_poll_wrapper'])
    info = json.loads(parse_response(device.replies[-1][3], INFO, 503))
    assert info['build'] == 'background-r5-profile'
    assert info['draw_profile']['command'] == DRAW_PROFILE
    assert info['capabilities'] == ['screen']
    device.prepare(make_cdb(LINK_PROBE, 504, (4076, 0, 0, 0)), state=0x7F)
    device.call(symbols(profile=True)['ds100_v48_poll_wrapper'])
    payload = parse_response(device.replies[-1][3], LINK_PROBE, 504)
    assert payload == bytes(i & 255 for i in range(4076))
    assert device.replies[-1][2] == 4096
