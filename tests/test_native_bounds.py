import ctypes
import random
from ds100.protocol import make_cdb


def test_native_output_bounds_under_malformed_commands(native_library):
    randomizer = random.Random(0xD5100)
    for _ in range(1500):
        command = randomizer.choice((1, 2, 3, 4, 5, 6, 7, 8))
        args = tuple(randomizer.randrange(65536) for _ in range(4))
        cdb = make_cdb(command, 99, args)
        capacity = randomizer.choice((0, 20, 127, 128, 512, 1060, 4096, 16404))
        request = ctypes.create_string_buffer(cdb)
        storage = ctypes.create_string_buffer(b"\xa5" * (16404 + 64))
        count = native_library.ds100_fixture_exchange(request, storage, capacity)
        assert count <= capacity
        assert storage.raw[capacity:16404 + 64] == b"\xa5" * (16404 + 64 - capacity)
