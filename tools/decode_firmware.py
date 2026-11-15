"""Create analysis files from a DS100 update image. Do not flash the output."""

import argparse
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from ds100.firmware import decode, inspect_image, V48_BASE


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", nargs="?", type=Path, default=ROOT / "atk_dso_v_4_8.bin")
    parser.add_argument("--output", type=Path, default=ROOT / "analysis" / "v4.8")
    args = parser.parse_args()
    encoded = args.input.read_bytes()
    plain = decode(encoded)
    report = inspect_image(encoded)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "decoded.bin").write_bytes(plain)
    (args.output / "codec-report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    strings = [{"offset": f"0x{m.start():x}", "address": f"0x{V48_BASE + m.start():08x}", "text": m.group().decode("ascii")}
               for m in re.finditer(rb"[\x20-\x7e]{5,}", plain)]
    (args.output / "strings.json").write_text(json.dumps(strings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
