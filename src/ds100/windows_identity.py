"""Check the Windows disk and USB identity before opening a remote transport."""

import json
import os
from pathlib import Path
import re
import subprocess

MODEL = "ATK DSO100 USB Device"
SERIAL = "ATK-DSO-V1.0.0"
DISK_SIZE = 16777216
QUERY_TIMEOUT_SECONDS = 15

# Read the validated drive letter from stdin, without composing shell commands.
# Follow the selected volume's associations, rather than finding an unrelated
# USB device with matching IDs elsewhere on the computer.
_IDENTITY_QUERY = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$drive = [Console]::In.ReadLine()
if ($drive -cnotmatch '^[A-Z]:$') { throw 'Invalid drive root.' }
$volume = @(Get-CimInstance -ClassName Win32_LogicalDisk -Filter "DeviceID='$drive'" -OperationTimeoutSec 5)
if ($volume.Count -ne 1) { throw 'The selected volume is not present.' }
$disks = @($volume | Get-CimAssociatedInstance -Association Win32_LogicalDiskToPartition -ResultClassName Win32_DiskPartition -OperationTimeoutSec 5 |
    Get-CimAssociatedInstance -Association Win32_DiskDriveToDiskPartition -ResultClassName Win32_DiskDrive -OperationTimeoutSec 5)
$records = @(foreach ($disk in $disks) {
    $instance = [string]$disk.PNPDeviceID
    $usb = ''
    for ($depth = 0; $depth -lt 5; $depth++) {
        $property = Get-PnpDeviceProperty -InstanceId $instance -KeyName 'DEVPKEY_Device_Parent'
        $instance = [string]$property.Data
        if ([string]::IsNullOrEmpty($instance)) { break }
        if ($instance.StartsWith('USB\VID_', [System.StringComparison]::OrdinalIgnoreCase)) {
            $usb = $instance
            break
        }
    }
    [pscustomobject]@{Index=$disk.Index; DeviceID=$disk.DeviceID; Model=$disk.Model;
        Size=$disk.Size; PNPDeviceID=$disk.PNPDeviceID; UsbDeviceID=$usb; Volumes=@($volume.DeviceID)}
})
ConvertTo-Json -InputObject $records -Depth 4 -Compress
"""


def drive_name(root):
    """Accept a drive root only; never interpolate an arbitrary path in a query."""
    match = re.fullmatch(r"([A-Za-z]):[\\/]?", str(root))
    if match is None:
        raise ValueError("Select a Windows drive root, such as E:/.")
    return match[1].upper() + ":"


def _query_disk_identity(drive):
    # Validate here as well, so this boundary is safe when called independently.
    drive = drive_name(drive)
    powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    try:
        result = subprocess.run(
            [str(powershell), "-NoProfile", "-NonInteractive", "-Command", _IDENTITY_QUERY],
            input=drive + "\n", capture_output=True, check=True, text=True,
            encoding="utf-8", timeout=QUERY_TIMEOUT_SECONDS,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired as error:
        raise OSError("The DS100 hardware identity query timed out. The transport was not opened.") from error
    except (OSError, subprocess.CalledProcessError, UnicodeError) as error:
        raise OSError("Cannot read the selected volume's hardware identity. The transport was not opened.") from error
    try:
        records = json.loads(result.stdout)
    except (TypeError, ValueError) as error:
        raise OSError("Windows returned invalid DS100 hardware identity data.") from error
    return records


def verify_device_identity(root):
    """Require one matching physical disk and its own USB parent; fail closed."""
    drive = drive_name(root)
    records = _query_disk_identity(drive)
    if not isinstance(records, list) or len(records) != 1 or not isinstance(records[0], dict):
        raise OSError("The selected volume must map to exactly one physical disk.")
    disk = records[0]
    volumes = disk.get("Volumes")
    if not isinstance(volumes, list) or volumes != [drive]:
        raise OSError("The physical disk does not map to the selected volume.")
    index = disk.get("Index")
    if (type(index) is not int or not 0 <= index <= 0xFFFFFFFF
            or not isinstance(disk.get("DeviceID"), str)
            or disk["DeviceID"].upper() != rf"\\.\PHYSICALDRIVE{index}"):
        raise OSError("The physical disk number and device path do not match.")
    if disk.get("Model") != MODEL:
        raise OSError("The disk model does not match the supported DS100.")
    size = disk.get("Size")
    if type(size) not in (int, str) or str(size) != str(DISK_SIZE):
        raise OSError("The physical disk size does not match the supported 16 MiB DS100.")
    pnp = disk.get("PNPDeviceID")
    match = re.fullmatch(r"USBSTOR\\DISK&[^\\]+\\([^\\]+)", pnp, re.IGNORECASE) if isinstance(pnp, str) else None
    if match is None:
        raise OSError("The disk PnP identity is not a USB storage device.")
    if match[1].upper() not in (SERIAL, SERIAL + "&0"):
        raise OSError("The disk serial does not match the supported DS100.")
    usb = disk.get("UsbDeviceID")
    match = re.fullmatch(r"USB\\VID_([0-9A-F]{4})&PID_([0-9A-F]{4})\\([^\\]+)", usb, re.IGNORECASE) if isinstance(usb, str) else None
    if match is None or (match[1].upper(), match[2].upper()) != ("28E9", "028F"):
        raise OSError("The disk's USB parent does not have the supported VID 28E9 and PID 028F.")
    if match[3].upper() != SERIAL:
        raise OSError("The USB serial does not match the supported DS100.")
    return disk
