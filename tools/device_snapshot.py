"""Read DS100 USB storage and save a local backup. This tool cannot flash firmware."""

from pathlib import Path
import argparse
import ctypes as C
from ctypes import wintypes as W
from datetime import datetime, timezone
import hashlib
import json
import os
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from ds100.storage import find_drives, read_cfg


def disk_inventory():
    script = r"""
    Get-CimInstance Win32_DiskDrive | Where-Object { $_.Model -eq 'ATK DSO100 USB Device' } |
        ForEach-Object {
            $disk = $_
            $letters = @($disk | Get-CimAssociatedInstance -ResultClassName Win32_DiskPartition |
                Get-CimAssociatedInstance -ResultClassName Win32_LogicalDisk |
                Select-Object -ExpandProperty DeviceID)
            [pscustomobject]@{Index=$disk.Index;Model=$disk.Model;Size=$disk.Size;
                PNPDeviceID=$disk.PNPDeviceID;DeviceID=$disk.DeviceID;Volumes=$letters}
        } | ConvertTo-Json -Depth 5 -Compress
    """
    result = subprocess.run(["powershell.exe", "-NoProfile", "-Command", script],
                            capture_output=True, check=True, text=True, timeout=30)
    if not result.stdout.strip():
        return []
    items = json.loads(result.stdout)
    return items if isinstance(items, list) else [items]


def read_disk(path, size):
    if not re.fullmatch(r"\\\\\.\\PHYSICALDRIVE\d+", path, re.IGNORECASE):
        raise ValueError("The disk path is invalid.")
    if size != 16 * 1024 * 1024:
        raise ValueError("The disk size does not match the checked DS100 storage size.")
    kernel = C.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [W.LPCWSTR, W.DWORD, W.DWORD, C.c_void_p, W.DWORD, W.DWORD, W.HANDLE]
    kernel.CreateFileW.restype = W.HANDLE
    kernel.ReadFile.argtypes = [W.HANDLE, C.c_void_p, W.DWORD, C.POINTER(W.DWORD), C.c_void_p]
    kernel.ReadFile.restype = W.BOOL
    kernel.CloseHandle.argtypes = [W.HANDLE]
    # GENERIC_READ only, shared read/write, OPEN_EXISTING. No write API is used.
    handle = kernel.CreateFileW(path, 0x80000000, 3, None, 3, 0, None)
    if handle == C.c_void_p(-1).value:
        raise C.WinError(C.get_last_error())
    parts = []
    try:
        for offset in range(0, size, 65536):
            count = min(65536, size - offset)
            buffer, got = C.create_string_buffer(count), W.DWORD()
            if not kernel.ReadFile(handle, buffer, count, C.byref(got), None):
                raise C.WinError(C.get_last_error())
            if got.value != count:
                raise OSError(f"The disk read stopped at byte {offset + got.value}.")
            parts.append(buffer.raw)
    finally:
        kernel.CloseHandle(handle)
    return b"".join(parts)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", action="store_true", help="Also read the identified 16 MiB USB disk.")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("This device snapshot requires Windows.")
    drives, disks = find_drives(), disk_inventory()
    if len(drives) != 1:
        parser.error("Connect one ATK-DSO volume and enter USB mode.")
    drive = drives[0]
    matches = [item for item in disks if str(drive)[:2].upper() in item["Volumes"]
               and "ATK-DSO-V1.0.0" in item["PNPDeviceID"]]
    if len(matches) != 1:
        parser.error("The volume does not map to one identified DS100 USB disk.")
    timestamp = datetime.now(timezone.utc)
    output = args.output or ROOT / "evidence" / ("device-" + timestamp.strftime("%Y%m%dT%H%M%SZ"))
    output.mkdir(parents=True, exist_ok=False)
    cfg = read_cfg(drive)
    (output / "CFG.original").write_bytes(cfg["bytes"])
    receipt = {
        "timestamp_utc": timestamp.isoformat(), "mode": "read-only USB storage",
        "volume": str(drive), "disk": matches[0],
        "root_files": [{"name": path.name, "size_bytes": path.stat().st_size}
                       for path in drive.iterdir() if path.is_file()],
        "cfg": {"size_bytes": cfg["size"], "sha256": cfg["sha256"]},
        "mcu_flash_backed_up": False, "device_writes_performed": False,
        "raw_usb_storage": {"requested": args.raw, "complete": False},
    }
    if args.raw:
        try:
            data = read_disk(matches[0]["DeviceID"], int(matches[0]["Size"]))
            (output / "usb-storage.img").write_bytes(data)
            receipt["raw_usb_storage"].update(complete=True, size_bytes=len(data),
                sha256=hashlib.sha256(data).hexdigest(), boot_signature=data[510:512].hex())
        except (OSError, ValueError) as error:
            receipt["raw_usb_storage"]["error"] = str(error)
    (output / "snapshot.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), **receipt}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
