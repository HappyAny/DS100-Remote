from pathlib import Path
import struct
import pytest
from ds100.firmware import decode, encode, keys, inspect_image, FirmwareFormatError, V48_SHA256

ROOT = Path(__file__).resolve().parents[1]


def test_official_image_has_known_vectors_and_usb_descriptor():
    encoded = (ROOT / "atk_dso_v_4_8.bin").read_bytes()
    plain = decode(encoded)
    assert encode(plain) == encoded
    report = inspect_image(encoded)
    assert report["encoded_sha256"] == V48_SHA256
    assert struct.unpack_from("<4I", plain) == (0x2002FF90, 0x080107C1, 0x080101C1, 0x080101C3)
    assert plain[0x625AC:0x625AC + 18] == bytes.fromhex("12 01 00 02 00 00 00 40 e9 28 8f 02 00 01 01 02 03 01")
    # This block failed under the early modulo-128 key-index hypothesis.
    assert plain[0x35680:0x35680 + 18] == bytes.fromhex("00 98 01 b0 0d 23 bd e8 f0 4f 26 4a 81 21 01 f0 7f b9")
    key = next(key for key in keys(encoded) if key.offset == 0x35680)
    assert (key.add, key.xor_index, key.xor) == (248, 124, 197)


def test_editing_a_selector_byte_rekeys_the_block():
    plain = bytearray(decode((ROOT / "atk_dso_v_4_8.bin").read_bytes()))
    plain[22] = 231
    plain[231 >> 1] = 91
    plain[0x35696] = 44
    assert decode(encode(bytes(plain))) == plain


@pytest.mark.parametrize("data", [b"", bytes(22), bytes(128) + bytes(3)])
def test_reject_incomplete_key_blocks(data):
    if not data:
        assert decode(data) == b""
    else:
        with pytest.raises(FirmwareFormatError):
            decode(data)
