import ctypes
import json
import struct
import numpy as np
import pytest

from ds100.protocol import (Client, INFO, SCREEN_STREAM, ProtocolError, SCREEN, RLE565,
    make_cdb, make_response, parse_response, parse_tile)
from test_arm_candidate import unicorn_windows_exception_handler


def test_bounded_native_stream_assembles_exact_full_screen(native_library):
    class SmallBuffer:
        def __init__(self):
            self.commands = []

        def exchange(self, cdb):
            self.commands.append(cdb[1])
            storage = ctypes.create_string_buffer(b"\xa5" * 4160, 4160)
            count = native_library.ds100_fixture_exchange(ctypes.create_string_buffer(cdb), storage, 4096)
            assert 0 < count <= 4096
            assert storage.raw[4096:] == b"\xa5" * 64
            return storage.raw[:count]

    transport = SmallBuffer()
    client = Client(transport)
    assert client.info()["screen_stream"]["command"] == SCREEN_STREAM
    result = client.screen()
    y, x = np.indices((320, 480))
    expected = (((x & 31) << 11) | ((y & 63) << 5) | ((x + y) & 31)).astype("<u2")
    np.testing.assert_array_equal(result, expected)
    assert transport.commands[1:] == [SCREEN_STREAM] * 160


@pytest.mark.parametrize("rows", [0, 17, True, "16"])
def test_invalid_fast_screen_configuration_rejected(rows):
    class BadInfo:
        def exchange(self, cdb):
            body = {"protocol": 1, "screen_width": 480, "screen_height": 320,
                    "screen_stream": {"command": 5, "max_rows": rows, "encoding": "rle565"}}
            return make_response(INFO, struct.unpack_from("<I", cdb, 4)[0], json.dumps(body).encode())
    with pytest.raises(ProtocolError, match="configuration"):
        Client(BadInfo()).info()


@pytest.mark.parametrize("height", [0, 17])
def test_stream_rejects_nonprogress_or_extra_rows(height):
    class BadTile:
        def exchange(self, cdb):
            seq = struct.unpack_from("<I", cdb, 4)[0]
            body = SCREEN.pack(0, 0, 0, 480, height, RLE565, 0, 0)
            if height:
                body += struct.pack("<HH", 480 * height, 0)
            return make_response(SCREEN_STREAM, seq, body)
    client = Client(BadTile())
    client.stream_rows = 16
    with pytest.raises(ProtocolError):
        client.screen()


def bus_words(pixels):
    """Pack two RGB565 reference pixels into the LCD's RG/BR/GB bus words."""
    values = [0]  # one dummy read per row
    for offset in range(0, len(pixels), 2):
        a = int(pixels[offset])
        b = int(pixels[offset + 1]) if offset + 1 < len(pixels) else 0
        r1, g1, b1 = (a >> 11) << 3, ((a >> 5) & 63) << 2, (a & 31) << 3
        r2, g2, b2 = (b >> 11) << 3, ((b >> 5) & 63) << 2, (b & 31) << 3
        values.extend([(r1 << 8) | g1, (b1 << 8) | r2])
        if offset + 1 < len(pixels):
            values.append((g2 << 8) | b2)
    return values


def run_arm_tile(pixels, requested_height=None):
    from test_arm_candidate import ArmDevice, symbols, BUFFER
    height, width = pixels.shape
    device = ArmDevice(background=True, fast=True)
    device.mu.mem_write(0x2002CEF4, struct.pack("<H", 0x9488))
    for row in pixels:
        device.read_values.extend(bus_words(row))
    device.prepare(make_cdb(SCREEN_STREAM, 204, (0, 0, width, requested_height or height)), state=0x7F)
    # prepare() changes the CBW, which starts immediately after the MSC buffer.
    canary = bytes(device.mu.mem_read(BUFFER + 4096, 64))
    device.call(symbols(fast=True)["ds100_v48_poll_wrapper"])
    assert bytes(device.mu.mem_read(BUFFER + 4096, 64)) == canary
    assert not device.pixel_calls
    return device, parse_tile(parse_response(device.replies[0][3], SCREEN_STREAM, 204))


def test_arm_fast_stream_rgb_and_odd_width():
    pixels = np.array([[0xF800, 0x07E0, 0x001F], [0xFFFF, 0x0000, 0xF81F]], dtype="<u2")
    device, tile = run_arm_tile(pixels)
    assert tile.pixels == pixels.tobytes()
    assert device.cursor_calls == [(0, 0), (0, 1)]
    assert not device.read_values


def test_arm_fast_stream_uniform_sixteen_rows():
    pixels = np.full((16, 480), 0xF81F, dtype="<u2")
    device, tile = run_arm_tile(pixels)
    assert (tile.width, tile.height) == (480, 16)
    assert tile.pixels == pixels.tobytes()
    assert len(device.replies[0][3]) == 40  # response + tile header + one run


def test_arm_stream_overflow_rolls_back_complete_rows_and_last_run():
    pixels = np.tile([0xF800, 0x001F], (3, 240)).astype("<u2")
    # The third row extends the preceding run before it overflows the buffer.
    pixels[2, 0] = pixels[1, -1]
    device, tile = run_arm_tile(pixels, requested_height=16)
    assert tile.height == 2
    assert tile.pixels == pixels[:2].tobytes()
    assert device.read_values  # an incomplete third row was discarded


def test_arm_fast_info_and_other_lcd_fallback():
    from test_arm_candidate import ArmDevice, symbols
    for lcd, fast_available in ((0x9488, True), (0x7796, False)):
        device = ArmDevice(background=True, fast=True)
        device.mu.mem_write(0x2002CEF4, struct.pack("<H", lcd))
        device.prepare(make_cdb(INFO, 205), state=0x7F)
        device.call(symbols(fast=True)["ds100_v48_poll_wrapper"])
        info = json.loads(parse_response(device.replies[0][3], INFO, 205))
        assert info["build"] == "background-r4-fast"
        assert ("screen_stream" in info) == fast_available
