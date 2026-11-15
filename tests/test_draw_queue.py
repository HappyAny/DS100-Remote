"""Compare the C sender with the independent Python drawing receiver."""

import ctypes as C
from pathlib import Path
import subprocess
import zlib
import numpy as np
import pytest
from ds100.drawing import DrawReplay

ROOT = Path(__file__).resolve().parents[1]
RowReader = C.CFUNCTYPE(None, C.c_void_p, C.c_uint16, C.POINTER(C.c_uint16))


class State(C.Structure):
    _fields_ = [('queue', C.c_void_p)] + [(name, C.c_uint32) for name in (
        'capacity', 'write_at', 'read_at', 'used', 'records', 'epoch', 'next_record',
        'frame_id', 'mode', 'dirty', 'fault', 'baseline_y', 'reset_pending', 'clipped', 'peak_used')]


@pytest.fixture(scope='module')
def sender_library(request):
    library = ROOT / ('build/draw_sender_' + request.module.__name__ + '.dll')
    subprocess.run(['gcc', '-std=c99', '-O2', '-Wall', '-Wextra', '-Werror', '-shared',
                    str(ROOT / 'experiments/draw-stream/draw_queue.c'),
                    str(ROOT / 'experiments/draw-stream/fast_crc.c'), '-o', str(library)], check=True)
    lib = C.CDLL(str(library))
    lib.ds100_draw_init.argtypes = [C.POINTER(State), C.c_void_p, C.c_uint32]
    lib.ds100_draw_start.argtypes = [C.POINTER(State), C.c_uint32]
    lib.ds100_draw_point.argtypes = [C.POINTER(State)] + [C.c_uint16] * 3
    lib.ds100_draw_fill.argtypes = [C.POINTER(State)] + [C.c_uint16] * 5
    lib.ds100_draw_span.argtypes = [C.POINTER(State)] + [C.c_uint16] * 3 + [C.c_void_p]
    lib.ds100_draw_pixels.argtypes = [C.POINTER(State)] + [C.c_uint16] * 3 + [C.c_int] * 2 + [C.c_void_p]
    lib.ds100_draw_bitmap.argtypes = [C.POINTER(State)] + [C.c_uint16] * 4 + [C.c_void_p]
    lib.ds100_draw_clear.argtypes = [C.POINTER(State), C.c_uint16]
    lib.ds100_draw_present.argtypes = [C.POINTER(State)]
    lib.ds100_draw_packet.argtypes = [C.POINTER(State), C.c_void_p, C.c_size_t, RowReader, C.c_void_p]
    lib.ds100_draw_packet.restype = C.c_size_t
    lib.ds100_crc32.argtypes = [C.c_void_p, C.c_size_t]
    lib.ds100_crc32.restype = C.c_uint32
    return lib


class Sender:
    def __init__(self, lib, capacity=16384):
        self.lib = lib
        self.state = State()
        self.storage = C.create_string_buffer(b'\xA7' * (capacity + 16), capacity + 16)
        lib.ds100_draw_init(C.byref(self.state), C.addressof(self.storage) + 8, capacity)
        self.expected = (np.arange(320, dtype=np.uint32)[:, None] * 257 +
                         np.arange(480, dtype=np.uint32)[None, :] * 119).astype('<u2')

        def read_row(_, y, out):
            C.memmove(out, self.expected[y].ctypes.data, 960)

        self.reader = RowReader(read_row)
        self.replay = DrawReplay()

    def pop(self, capacity=4076):
        out = C.create_string_buffer(b'\xC5' * (capacity + 16), capacity + 16)
        size = self.lib.ds100_draw_packet(C.byref(self.state), C.addressof(out) + 8,
                                           capacity, self.reader, None)
        assert out.raw[:8] == out.raw[-8:] == b'\xC5' * 8
        assert self.storage.raw[:8] == self.storage.raw[-8:] == b'\xA7' * 8
        return out.raw[8:8 + size]

    def baseline(self, epoch=1):
        self.lib.ds100_draw_start(C.byref(self.state), epoch)
        for _ in range(321):
            data = self.pop()
            assert data
            self.replay.apply(data)
            if self.replay.ready:
                break
        assert self.replay.ready and self.state.mode == 2
        np.testing.assert_array_equal(self.replay.last_frame, self.expected)

    def drain(self):
        frames = []
        while self.state.used:
            data = self.pop()
            assert data and len(data) <= 4076
            frames += self.replay.apply(data)
        return frames


def test_c_sender_baseline_pixels_and_mixed_draws(sender_library):
    sender = Sender(sender_library)
    sender.baseline()
    lib, state = sender.lib, C.byref(sender.state)
    lib.ds100_draw_point(state, 479, 319, 0xF800)
    sender.expected[319, 479] = 0xF800
    lib.ds100_draw_fill(state, 4, 5, 15, 18, 0x07E0)
    sender.expected[5:19, 4:16] = 0x07E0
    row = np.array([10] * 50 + [20] * 50 + list(range(100)), dtype='<u2')
    lib.ds100_draw_span(state, 20, 219, 30, row.ctypes.data)
    sender.expected[30, 20:220] = row
    lib.ds100_draw_present(state)
    frames = sender.drain()
    assert [i for i, _ in frames] == [1]
    np.testing.assert_array_equal(frames[-1][1], sender.expected)


def test_c_sender_clips_edge_and_keeps_original_bitmap_stride(sender_library):
    sender = Sender(sender_library)
    sender.baseline()
    pixels = np.arange(3 * 11, dtype='<u2').reshape(3, 11)
    sender.lib.ds100_draw_bitmap(C.byref(sender.state), 470, 318, 480, 320, pixels.ctypes.data)
    sender.expected[318:320, 470:480] = pixels[:2, :10]
    sender.lib.ds100_draw_present(C.byref(sender.state))
    frames = sender.drain()
    assert sender.state.clipped > 0
    np.testing.assert_array_equal(frames[-1][1], sender.expected)


def test_c_sender_queue_wraps_and_batches_presentations(sender_library):
    sender = Sender(sender_library, capacity=1024)
    sender.baseline()
    frames = []
    for frame in range(1, 81):
        for x in range(10):
            sender.lib.ds100_draw_point(C.byref(sender.state), x, frame, frame * 100 + x)
            sender.expected[frame, x] = frame * 100 + x
        sender.lib.ds100_draw_present(C.byref(sender.state))
        if frame % 5 == 0:
            frames.extend(sender.drain())
    assert len(frames) == 80 and frames[-1][0] == 80
    np.testing.assert_array_equal(frames[-1][1], sender.expected)


def test_overflow_and_bad_geometry_require_new_baseline(sender_library):
    sender = Sender(sender_library, capacity=1024)
    sender.baseline()
    for x in range(200):
        sender.lib.ds100_draw_point(C.byref(sender.state), x, 1, 123)
    assert sender.state.mode == 4 and sender.state.fault == 1
    assert sender.pop() == b''
    sender.baseline(epoch=2)
    sender.lib.ds100_draw_fill(C.byref(sender.state), 50, 0, 49, 1, 0)
    assert sender.state.mode == 4 and sender.state.fault == 2
    assert sender.pop() == b''


def test_clear_work_budget_is_respected_across_packets(sender_library):
    sender = Sender(sender_library)
    sender.baseline()
    for color in range(7):
        sender.lib.ds100_draw_clear(C.byref(sender.state), color)
        sender.lib.ds100_draw_present(C.byref(sender.state))
    frames = sender.drain()
    assert [i for i, _ in frames] == list(range(1, 8))
    assert np.all(frames[-1][1] == 6)


def test_vertical_pixels_and_reverse_rle_reconstruct_exactly(sender_library):
    sender = Sender(sender_library)
    sender.baseline()
    values = np.array([1, 1, 1, 2, 2, 3, 3, 3, 3, 3, 3], dtype='<u2')
    sender.lib.ds100_draw_pixels(C.byref(sender.state), 400, 12, len(values), 1, 1, values.ctypes.data)
    sender.lib.ds100_draw_present(C.byref(sender.state))
    sender.expected[12:23, 400] = values[::-1]
    frames = sender.drain()
    np.testing.assert_array_equal(frames[-1][1], sender.expected)


def test_table_crc_matches_zlib_for_partial_and_full_usb_blocks(sender_library):
    data = np.random.default_rng(73).integers(0, 256, 4096, dtype=np.uint8).tobytes()
    buffer = C.create_string_buffer(data)
    for size in list(range(129)) + [255, 256, 511, 512, 1023, 4076, 4096]:
        assert sender_library.ds100_crc32(buffer, size) == zlib.crc32(data[:size])
