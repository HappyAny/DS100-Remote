"""Measure the screen service without flash writes or device setting changes."""

from datetime import datetime, timezone
from pathlib import Path
import argparse
import json
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from ds100.protocol import Client, INFO, SCREEN_READ, parse_tile
from ds100.storage import find_drives
from ds100.windows_scsi import ScsiTransport


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--frames", type=int, default=3)
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT) or not 0 <= args.frames <= 10:
        parser.error("Use a project output directory and 0 to 10 frames.")
    output.mkdir(parents=True, exist_ok=True)
    drives = find_drives()
    if len(drives) != 1:
        raise OSError("Connect one ATK-DSO device. Close other screen clients first.")
    class MeasuredTransport(ScsiTransport):
        requests = 0
        received_bytes = 0

        def exchange(self, cdb):
            reply = super().exchange(cdb)
            self.requests += 1
            self.received_bytes += len(reply)
            return reply

    transport = MeasuredTransport(drives[0])
    client = Client(transport)
    report = {"started_utc": datetime.now(timezone.utc).isoformat(),
              "physical_device_test": True, "write_operations": False, "cases": []}
    try:
        report["info"] = client.info()
        for width, height in [(0, 0), (1, 1), (8, 8), (32, 4), (32, 8), (64, 8)]:
            samples = []
            for _ in range(15):
                start = time.perf_counter()
                reply = client.request(SCREEN_READ, (0, 0, width, height)) if width else client.request(INFO)
                elapsed = time.perf_counter() - start
                if width:
                    parse_tile(reply)
                samples.append(elapsed)
            report["cases"].append({"command": "screen" if width else "info",
                "pixels": width * height, "seconds": samples,
                "median_ms": statistics.median(samples) * 1000,
                "minimum_ms": min(samples) * 1000})
        report["frame_seconds"] = []
        report["frame_metrics"] = []
        for index in range(args.frames):
            requests, received_bytes = transport.requests, transport.received_bytes
            start = time.perf_counter()
            pixels = client.screen()
            report["frame_seconds"].append(time.perf_counter() - start)
            report["frame_metrics"].append({"requests": transport.requests - requests,
                                             "received_bytes": transport.received_bytes - received_bytes})
            if index == 0:
                from probe_remote import save_pixels
                save_pixels(output / "screen.png", pixels)
        report["success"] = True
    finally:
        client.close()
        report["finished_utc"] = datetime.now(timezone.utc).isoformat()
        (output / "receipt.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"build": report["info"].get("build"), "cases": [
        {k: v for k, v in case.items() if k != "seconds"} for case in report["cases"]],
        "frame_seconds": report.get("frame_seconds", []),
        "frame_metrics": report.get("frame_metrics", [])}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
