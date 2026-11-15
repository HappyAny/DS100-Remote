import struct
import numpy as np
import pytest
from ds100.protocol import (Client, ProtocolError, INFO, SCREEN_READ, WAVE_READ, SET_RUN,
                            SCREEN, RAW565, RLE565, make_cdb, make_response, parse_response, parse_tile)
from ds100.simulator import SimulatorTransport


def test_native_c_service_screen_and_waveform(native_transport):
    client = Client(native_transport)
    assert client.info()["source"] == "native-test"
    screen = client.screen()
    y, x = np.indices((320, 480))
    expected = (((x & 31) << 11) | ((y & 63) << 5) | ((x + y) & 31)).astype(np.uint16)
    assert np.array_equal(screen, expected)
    times, channels = client.waveform(4096, 3)
    assert len(times) == 4096
    assert np.isclose(times[-1], 0.004095)
    for index in (0, 1):
        expected = ((np.arange(4096) + index) & 255) * 0.02 - 2.56
        assert np.allclose(channels[index], expected, atol=1e-6)
    assert client.set_run(False) == b"\0"
    assert client.set_run(True) == b"\1"


@pytest.mark.parametrize("command,args", [(SCREEN_READ, (479, 0, 2, 1)), (SCREEN_READ, (0, 0, 64, 9)),
                                         (WAVE_READ, (4097, 3, 0, 0)), (WAVE_READ, (0, 1, 0, 0)),
                                         (SET_RUN, (2, 0, 0, 0)), (INFO, (1, 0, 0, 0))])
def test_native_service_rejects_invalid_requests(native_transport, command, args):
    response = native_transport.exchange(make_cdb(command, 123, args))
    with pytest.raises(ProtocolError):
        parse_response(response, command, 123)


def test_response_rejects_corruption_truncation_and_stale_sequence():
    response = make_response(INFO, 7, b'{"protocol":1}')
    for size in range(len(response)):
        with pytest.raises(ProtocolError):
            parse_response(response[:size], INFO, 7)
    corrupt = bytearray(response)
    corrupt[-1] ^= 1
    with pytest.raises(ProtocolError):
        parse_response(corrupt, INFO, 7)
    with pytest.raises(ProtocolError):
        parse_response(response, INFO, 8)


def test_rle_exact_pixel_count_and_bounds():
    header = SCREEN.pack(1, 0, 0, 4, 2, RLE565, 0, 0)
    tile = parse_tile(header + struct.pack("<HHHH", 3, 0xF800, 5, 0x07E0))
    assert tile.pixels == struct.pack("<H", 0xF800) * 3 + struct.pack("<H", 0x07E0) * 5
    for body in (struct.pack("<HH", 9, 1), struct.pack("<HH", 0, 1), struct.pack("<HH", 7, 1), b"\1"):
        with pytest.raises(ProtocolError):
            parse_tile(header + body)


def test_screen_rejects_mixed_frame_identifiers():
    class MixedFrame(SimulatorTransport):
        def exchange(self, cdb):
            from ds100.protocol import CDB
            _, command, _, _, seq, x, y, w, h = CDB.unpack(cdb)
            if command == SCREEN_READ:
                payload = SCREEN.pack(1 if x == 0 and y == 0 else 2, x, y, w, h, RAW565, 0, 0) + bytes(w * h * 2)
                return make_response(command, seq, payload)
            return super().exchange(cdb)
    with pytest.raises(ProtocolError, match="frame identifier"):
        Client(MixedFrame()).screen()


def test_screen_cancellation():
    with pytest.raises(ProtocolError, match="cancelled"):
        Client(SimulatorTransport()).screen(cancelled=lambda: True)
