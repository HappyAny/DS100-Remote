"""Safely remove the identified DS100 after a verified candidate transfer."""

from pathlib import Path
from datetime import datetime, timezone
import argparse
import ctypes as C
from ctypes import wintypes as W
import json
from prepare_flash import IMAGES

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("receipt", type=Path)
    args = parser.parse_args()
    report = args.receipt.resolve()
    if not report.is_relative_to(ROOT / "evidence/flash-attempt-20261115"):
        parser.error("The transfer receipt is outside the current experiment.")
    data = json.loads(report.read_text(encoding="utf-8"))
    checked_hashes = {item[1] for item in IMAGES.values()}
    if (data.get("phase") != "verified-awaiting-safe-removal-and-user-M-key" or
            data.get("candidate_sha256") not in checked_hashes or
            data.get("read_back_sha256") != data.get("candidate_sha256")):
        raise ValueError("The current candidate has no complete read-back verification.")
    api = C.WinDLL("cfgmgr32")
    api.CM_Locate_DevNodeW.argtypes = [C.POINTER(W.DWORD), W.LPWSTR, W.ULONG]
    api.CM_Locate_DevNodeW.restype = W.ULONG
    api.CM_Request_Device_EjectW.argtypes = [W.DWORD, C.POINTER(W.DWORD), W.LPWSTR, W.ULONG, W.ULONG]
    api.CM_Request_Device_EjectW.restype = W.ULONG
    instance = C.create_unicode_buffer("USB\\VID_28E9&PID_028F\\ATK-DSO-V1.0.0")
    node, veto = W.DWORD(), W.DWORD()
    name = C.create_unicode_buffer(260)
    locate = api.CM_Locate_DevNodeW(C.byref(node), instance, 0)
    result = api.CM_Request_Device_EjectW(node, C.byref(veto), name, len(name), 0) if locate == 0 else None
    data["safe_removal"] = {"timestamp_utc": datetime.now(timezone.utc).isoformat(), "locate_result": locate,
                            "eject_result": result, "veto_type": veto.value, "veto_name": name.value, "success": result == 0}
    report.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(data["safe_removal"], ensure_ascii=False, indent=2))
    return 0 if result == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
