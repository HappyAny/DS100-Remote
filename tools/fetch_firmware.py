"""Fetch the pinned official V4.8 image and check its hashes. Do not access a device."""

from pathlib import Path
import hashlib
import json
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from ds100.firmware import V48_SHA256

COMMIT = "46670a349c54e7b9f07a6f7e832abee5dfeb4e13"
BLOB = "72707f3da03af5d32e9ad9dac3a9a75bcc617a6e"
URL = f"https://raw.githubusercontent.com/alientek-tools/Digital-Oscilloscope_DS100/{COMMIT}/Firmware/atk_dso_v_4_8.bin"


def main():
    path = ROOT / "atk_dso_v_4_8.bin"
    if path.exists():
        data = path.read_bytes()
    else:
        request = urllib.request.Request(URL, headers={"User-Agent": "DS100-Remote-Research/0.1"})
        with urllib.request.urlopen(request, timeout=45) as response:
            data = response.read(2 * 1024 * 1024)
    digest = hashlib.sha256(data).hexdigest()
    blob = hashlib.sha1(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()
    if len(data) != 427752 or digest != V48_SHA256 or blob != BLOB:
        raise ValueError("The file does not match the pinned official V4.8 image. No file was written.")
    if not path.exists():
        path.write_bytes(data)
    receipt = {"repository": "https://github.com/alientek-tools/Digital-Oscilloscope_DS100",
               "commit": COMMIT, "path": "Firmware/atk_dso_v_4_8.bin", "url": URL,
               "size_bytes": len(data), "sha256": digest, "git_blob_sha1": blob,
               "git_blob_match_verified": True, "device_access_performed": False}
    output = ROOT / "evidence/sources"
    output.mkdir(parents=True, exist_ok=True)
    (output / "official-firmware.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
