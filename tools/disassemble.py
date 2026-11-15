"""Save ARM Thumb instructions from a decoded DS100 image."""

import argparse
import hashlib
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / ".tools")]
from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB
from ds100.firmware import V48_BASE


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("start", type=lambda value: int(value, 0))
    parser.add_argument("size", type=lambda value: int(value, 0))
    args = parser.parse_args()
    data = (ROOT / "analysis/v4.8/decoded.bin").read_bytes()
    offset = args.start - V48_BASE
    if offset < 0 or offset + args.size > len(data):
        parser.error("The address range is outside the image.")
    engine = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
    engine.skipdata = True
    lines = [f"# decoded_sha256: {hashlib.sha256(data).hexdigest()}",
             f"# range: {args.start:#010x} + {args.size:#x}"]
    lines += [f"{item.address:08x}  {item.bytes.hex():10s} {item.mnemonic:10s} {item.op_str}"
             for item in engine.disasm(data[offset:offset + args.size], args.start)]
    output = ROOT / "analysis/v4.8" / f"disasm-{args.start:08x}.txt"
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
