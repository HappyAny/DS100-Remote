"""Transfer the authorized candidate to one identified DS100 bootloader volume."""

from datetime import datetime, timezone
from pathlib import Path
import argparse
import hashlib
import json
import os
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from ds100.storage import find_drives
from device_snapshot import disk_inventory
from prepare_flash import IMAGES, staging_directory


def save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", choices=IMAGES, default="r3")
    args = parser.parse_args()
    _, candidate_hash, image_size, _ = IMAGES[args.revision]
    if os.name != "nt":
        raise OSError("The device transfer requires Windows.")
    authorization_path = ROOT / "evidence/flash-attempt-20261115/authorization-and-handoff.json"
    if args.revision != "r3":
        authorization_path = authorization_path.with_name("authorization-" + args.revision + "-and-handoff.json")
    authorization = json.loads(authorization_path.read_text(encoding="utf-8"))
    if not authorization.get("accepted") or authorization.get("candidate_sha256") != candidate_hash:
        raise ValueError("No matching user authorization was recorded.")
    if authorization.get("confirmed_device_mode") != "A+P independent firmware update":
        raise ValueError("The independent update screen has not been confirmed.")
    source = staging_directory(args.revision) / 'candidate/atk_dso.bin'
    data = source.read_bytes()
    if len(data) != image_size or hashlib.sha256(data).hexdigest() != candidate_hash:
        raise ValueError("The local file differs from the reviewed candidate.")
    drives = find_drives()
    if len(drives) != 1:
        raise ValueError("The transfer requires exactly one ATK-DSO volume.")
    drive = drives[0]
    matches = [disk for disk in disk_inventory() if str(drive)[:2].upper() in disk["Volumes"]
               and disk["Model"] == "ATK DSO100 USB Device"
               and "ATK-DSO-V1.0.0" in disk["PNPDeviceID"] and int(disk["Size"]) == 16777216]
    if len(matches) != 1:
        raise ValueError("The volume does not map to the checked DS100 USB disk.")
    folder = ROOT / "evidence/flash-attempt-20261115" / ("transfer-" + args.revision + "-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    folder.mkdir(parents=True, exist_ok=False)
    backup = folder / "original-user-files"
    backup.mkdir()
    receipt = {"timestamp_utc": datetime.now(timezone.utc).isoformat(), "volume": str(drive),
               "disk": matches[0], "candidate_sha256": candidate_hash, "candidate_size_bytes": len(data),
               "device_mode": authorization["confirmed_device_mode"], "user_files": [],
               "device_writes_performed": False, "firmware_flash_started": False,
               "mcu_flash_backed_up": False, "phase": "backing-up-user-files"}
    report = folder / "transfer.json"
    save(report, receipt)
    try:
        for path in drive.iterdir():
            if not path.is_file():
                continue
            original = path.read_bytes()
            (backup / path.name).write_bytes(original)
            receipt["user_files"].append({"name": path.name, "size_bytes": len(original),
                                          "sha256": hashlib.sha256(original).hexdigest()})
        receipt["phase"] = "user-files-backed-up"
        save(report, receipt)
        if drive not in find_drives():
            raise OSError("The DS100 volume disconnected before the transfer.")
        temporary, target = drive / "DSOUPD.TMP", drive / "atk_dso.bin"
        if temporary.exists():
            raise FileExistsError("A previous temporary firmware file is present. Inspect it before another transfer.")
        if target.exists() and target.read_bytes() == data:
            receipt["phase"] = "matching-candidate-already-present"
        else:
            with temporary.open("xb", buffering=0) as stream:
                receipt["device_writes_performed"] = True
                receipt["phase"] = "writing-temporary-file"
                save(report, receipt)
                offset = 0
                while offset < len(data):
                    count = stream.write(data[offset:])
                    if not count:
                        raise OSError("The firmware file write stopped before completion.")
                    offset += count
                os.fsync(stream.fileno())
            if temporary.read_bytes() != data:
                raise OSError("The temporary firmware file failed its complete read-back check.")
            os.replace(temporary, target)
            with target.open("r+b", buffering=0) as stream:
                os.fsync(stream.fileno())
        read_back = target.read_bytes()
        if read_back != data:
            raise OSError("The final firmware file failed its complete read-back check.")
        receipt.update(phase="verified-awaiting-safe-removal-and-user-M-key",
                       read_back_sha256=hashlib.sha256(read_back).hexdigest(), read_back_size_bytes=len(read_back))
    except Exception as error:
        receipt["error"] = str(error)
        save(report, receipt)
        print(json.dumps({"receipt": str(report), **receipt}, ensure_ascii=False, indent=2))
        raise
    save(report, receipt)
    print(json.dumps({"receipt": str(report), **receipt}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
