"""Compare burst pixels with the stock read-point function on a static screen."""

from datetime import datetime, timezone
from pathlib import Path
import argparse
import hashlib
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from ds100.protocol import Client, SCREEN_READ, SCREEN_STREAM, parse_tile
from ds100.storage import find_drives
from ds100.windows_scsi import ScsiTransport


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT):
        parser.error("The output directory must be inside the project.")
    output.mkdir(parents=True, exist_ok=True)
    drives = find_drives()
    if len(drives) != 1:
        raise OSError("Connect one DS100 and close other screen clients.")
    client = Client(ScsiTransport(drives[0]))
    report = {"started_utc": datetime.now(timezone.utc).isoformat(), "physical_device_test": True,
              "reference": "The original V4.8 single-pixel function, command 2.",
              "requires_static_screen": True, "success": False, "regions": []}
    try:
        report["info"] = client.info()
        if report["info"].get("build") != "background-r4-fast" or not client.stream_rows:
            raise ValueError("The connected device does not run the fast R4 screen service.")
        locations = [(x, y, 64, 8) for y in (0, 32, 120, 144, 160, 192, 208, 312)
                     for x in (0, 176, 240, 416)]
        locations += [(205, 145, 7, 3), (211, 157, 31, 7), (479, 319, 1, 1)]
        for index, region in enumerate(locations):
            slow = parse_tile(client.request(SCREEN_READ, region))
            fast = parse_tile(client.request(SCREEN_STREAM, region))
            if (fast.x, fast.y, fast.width, fast.height) != region:
                raise ValueError("The fast tile rectangle does not match the reference.")
            differing = sum(slow.pixels[i:i+2] != fast.pixels[i:i+2] for i in range(0, len(slow.pixels), 2))
            report["regions"].append({"rectangle": region, "different_pixels": differing,
                                      "reference_sha256": hashlib.sha256(slow.pixels).hexdigest(),
                                      "fast_sha256": hashlib.sha256(fast.pixels).hexdigest()})
            if differing:
                (output / f"mismatch-{index}-reference.rgb565").write_bytes(slow.pixels)
                (output / f"mismatch-{index}-fast.rgb565").write_bytes(fast.pixels)
        report["different_pixels"] = sum(item["different_pixels"] for item in report["regions"])
        report["checked_pixels"] = sum(item["rectangle"][2] * item["rectangle"][3] for item in report["regions"])
        report["success"] = report["different_pixels"] == 0
    except Exception as error:
        report.update(error_type=type(error).__name__, error=str(error))
    finally:
        client.close()
        report["finished_utc"] = datetime.now(timezone.utc).isoformat()
        (output / "receipt.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k not in ("regions", "info")}, ensure_ascii=False, indent=2))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
