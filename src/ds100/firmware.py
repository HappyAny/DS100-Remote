"""Decode the DS100 update image. This module does not access a device."""

from dataclasses import dataclass
import hashlib
import struct

BLOCK_SIZE = 128
ADD_INDEX = 22
V48_SHA256 = "5030073c8ffe006d3727e4c13b80ca4aa89c3898a539ae7d4e4a2bf8bb216776"
V48_BASE = 0x08010000


class FirmwareFormatError(ValueError):
    pass


@dataclass(frozen=True)
class BlockKey:
    offset: int
    length: int
    add: int
    xor_index: int
    xor: int


def keys(data: bytes):
    for offset in range(0, len(data), BLOCK_SIZE):
        block = data[offset:offset + BLOCK_SIZE]
        if len(block) <= ADD_INDEX:
            raise FirmwareFormatError(f"Block at {offset:#x} has no key byte at offset 22.")
        add = block[ADD_INDEX]
        index = add if add < BLOCK_SIZE else add >> 1
        if index >= len(block):
            raise FirmwareFormatError(f"Block at {offset:#x} has an unavailable key byte at offset {index}.")
        yield BlockKey(offset, len(block), add, index, block[index])


def _transform(data: bytes, encode: bool) -> bytes:
    output = bytearray(data)
    for key in keys(data):
        for index in range(key.length):
            if index in (ADD_INDEX, key.xor_index):
                continue
            value = data[key.offset + index]
            output[key.offset + index] = ((value + key.add) & 255) ^ key.xor if encode else ((value ^ key.xor) - key.add) & 255
    return bytes(output)


def decode(data: bytes) -> bytes:
    return _transform(data, False)


def encode(data: bytes) -> bytes:
    return _transform(data, True)


def inspect_image(encoded: bytes) -> dict:
    plain = decode(encoded)
    rebuilt = encode(plain)
    words = struct.unpack_from("<16I", plain)
    return {
        "size_bytes": len(encoded),
        "encoded_sha256": hashlib.sha256(encoded).hexdigest(),
        "decoded_sha256": hashlib.sha256(plain).hexdigest(),
        "official_v48_hash_match": hashlib.sha256(encoded).hexdigest() == V48_SHA256,
        "encode_decode_identical": rebuilt == encoded,
        "first_different_byte": next((i for i, (a, b) in enumerate(zip(encoded, rebuilt)) if a != b), None),
        "block_count": len(list(keys(encoded))),
        "initial_sp": f"0x{words[0]:08x}",
        "reset_handler": f"0x{words[1]:08x}",
        "core_vectors": [f"0x{v:08x}" for v in words],
        "core_exception_vectors_in_range": all(v == 0 or (v & 1 and V48_BASE <= v < V48_BASE + len(plain)) for v in words[1:]),
        "flash_base_hypothesis": f"0x{V48_BASE:08x}",
        "codec": {
            "block_size": BLOCK_SIZE,
            "add_index": ADD_INDEX,
            "xor_index": "block[22] if block[22] < 128 else block[22] >> 1",
            "unchanged_indices": ["22", "xor_index"],
            "decode_other_bytes": "((stored_byte ^ block[xor_index]) - block[22]) & 0xff",
        },
        "limits": [
            "A byte-identical encode/decode cycle checks file reconstruction only.",
            "The device bootloader acceptance rules are not yet verified.",
            "The decoded image is not a backup of the connected device.",
        ],
        "device_access_performed": False,
    }
