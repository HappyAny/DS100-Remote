import os
from pathlib import Path
import subprocess
import ctypes
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("DS100_AUTO_CONNECT", "0")
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def native_library():
    folder = ROOT / "build"
    folder.mkdir(exist_ok=True)
    output = folder / "ds100_agent_test.dll"
    subprocess.run(["gcc", "-std=c99", "-O2", "-Wall", "-Wextra", "-Werror", "-shared",
                    str(ROOT / "firmware/ds100_remote.c"), str(ROOT / "firmware/ds100_screen_stream.c"),
                    str(ROOT / "firmware/provider_fixture.c"), "-o", str(output)], check=True)
    lib = ctypes.CDLL(str(output))
    lib.ds100_fixture_exchange.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t]
    lib.ds100_fixture_exchange.restype = ctypes.c_size_t
    return lib


@pytest.fixture
def native_transport(native_library):
    class NativeTransport:
        def exchange(self, cdb):
            request = ctypes.create_string_buffer(cdb)
            output = ctypes.create_string_buffer(16404)
            count = native_library.ds100_fixture_exchange(request, output, len(output))
            return output.raw[:count]

        def close(self):
            pass
    return NativeTransport()
