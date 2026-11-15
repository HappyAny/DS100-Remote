"""Encode one Thumb-2 branch for a checked firmware patch site."""

import struct


def branch(source, target, link=False):
    if source & 1 or target & 1:
        raise ValueError("Branch addresses must be halfword-aligned.")
    offset = target - source - 4
    if offset & 1 or not -(1 << 24) <= offset < (1 << 24):
        raise ValueError("The branch target is outside the Thumb-2 branch range.")
    value = offset & 0x1FFFFFF
    sign, i1, i2 = (value >> 24) & 1, (value >> 23) & 1, (value >> 22) & 1
    j1, j2 = (~(i1 ^ sign)) & 1, (~(i2 ^ sign)) & 1
    first = 0xF000 | (sign << 10) | ((value >> 12) & 0x3FF)
    second = (0xD000 if link else 0x9000) | (j1 << 13) | (j2 << 11) | ((value >> 1) & 0x7FF)
    return struct.pack("<HH", first, second)
