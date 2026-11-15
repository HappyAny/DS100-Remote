"""Execute only the stock scatter loader and save its initialized RAM model."""

from pathlib import Path
import hashlib
import json
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".tools"))
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS, UC_HOOK_MEM_WRITE
from unicorn.arm_const import UC_ARM_REG_XPSR, UC_ARM_REG_PC, UC_ARM_REG_SP


def main():
    image = (ROOT / "analysis/v4.8/decoded.bin").read_bytes()
    expected = "80b4bf7b5ced6c59a66aea4aa3a0cea36bc0a4ce6ce9ab90cd306a260878a082"
    if hashlib.sha256(image).hexdigest() != expected:
        raise ValueError("The decoded image does not match the checked V4.8 image.")
    mu = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
    mu.mem_map(0x08000000, 0x80000)
    mu.mem_write(0x08010000, image)
    mu.mem_map(0x20000000, 0x30000)
    mu.mem_map(0x10000000, 0x10000)
    mu.reg_write(UC_ARM_REG_XPSR, 0x01000000)
    writes = []

    def on_write(engine, access, address, size, value, user):
        writes.append((address, address + size))

    mu.hook_add(UC_HOOK_MEM_WRITE, on_write)
    # __main resets SP, runs __scatterload, then branches to stock main().
    # Stop before main: no clock, GPIO, ADC, USB, or GUI initialization executes.
    mu.emu_start(0x080101AD, 0x08010308, count=2000000)
    if mu.reg_read(UC_ARM_REG_PC) != 0x08010308:
        raise RuntimeError("The scatter-loader model did not reach the main entry.")
    merged = []
    for start, end in sorted(writes):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(end, merged[-1][1])
        else:
            merged.append([start, end])
    entries = [struct.unpack_from("<4I", image, address - 0x08010000)
               for address in (0x080782EC, 0x080782FC)]
    output = ROOT / "analysis/v4.8"
    (output / "initial-main-ram.bin").write_bytes(bytes(mu.mem_read(0x20000000, 0x30000)))
    (output / "initial-tcm-ram.bin").write_bytes(bytes(mu.mem_read(0x10000000, 0x10000)))
    report = {"decoded_sha256": expected, "physical_device_test": False,
              "execution": "Only __main and __scatterload. Stop before stock main().",
              "stack_pointer": hex(mu.reg_read(UC_ARM_REG_SP)),
              "scatter_entries": [{"source": hex(a), "destination": hex(b), "size": c,
                                   "function": hex(d)} for a, b, c, d in entries],
              "written_intervals": [[hex(a), hex(b)] for a, b in merged]}
    (output / "initial-ram-report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
