"""Exercise the transport's hardware guard without opening any Windows device."""

import ctypes as C
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from ds100 import windows_identity as identity
from ds100 import windows_scsi as scsi
from ds100.protocol import INFO, make_cdb


@pytest.fixture
def disk(monkeypatch):
    record = {
        "Index": 11, "DeviceID": r"\\.\PHYSICALDRIVE11",
        "Model": "ATK DSO100 USB Device", "Size": 16777216,
        "PNPDeviceID": r"USBSTOR\DISK&VEN_ATK&PROD_DSO100&REV_1.00\ATK-DSO-V1.0.0&0",
        "UsbDeviceID": r"USB\VID_28E9&PID_028F\ATK-DSO-V1.0.0", "Volumes": ["Q:"],
    }
    monkeypatch.setattr(identity, "_usb_parent_id", lambda instance: record.get("UsbDeviceID", ""))
    return record


def test_exact_disk_and_usb_identity_is_allowed(monkeypatch, disk):
    query = Mock(return_value=[disk])
    monkeypatch.setattr(identity, "_query_disk_identity", query)
    assert identity.verify_device_identity(Path("q:/")) == disk
    query.assert_called_once_with("Q:")


def test_usb_parent_comes_from_the_selected_disk_node(monkeypatch, disk):
    records = [{**disk, "UsbDeviceID": r"USB\VID_FFFF&PID_FFFF\OTHER"}]
    monkeypatch.setattr(identity, "_query_disk_identity", lambda drive: records)
    parent = Mock(return_value=disk["UsbDeviceID"])
    monkeypatch.setattr(identity, "_usb_parent_id", parent)
    assert identity.verify_device_identity("Q:/") == disk
    parent.assert_called_once_with(disk["PNPDeviceID"])


@pytest.mark.parametrize("removed", [False, True])
def test_cfgmgr_walks_actual_parent_nodes_and_checks_errors(monkeypatch, removed):
    instance = r"USBSTOR\DISK&VEN_ATK&PROD_DSO100\ATK-DSO-V1.0.0&0"
    identifiers = {42: r"USBSTOR\OTHER\INTERMEDIATE", 43: r"USB\VID_28E9&PID_028F\ATK-DSO-V1.0.0"}
    visited = []

    def set_word(target, value):
        C.cast(target, C.POINTER(C.c_uint32)).contents.value = value

    def locate(target, query, flags):
        assert query == instance and flags == 0
        set_word(target, 41)
        return 0

    def parent(target, child, flags):
        assert flags == 0
        visited.append(child.value)
        if removed and child.value == 42:
            return 0xD
        set_word(target, child.value + 1)
        return 0

    def size(target, node, flags):
        assert flags == 0
        set_word(target, len(identifiers[node.value]))
        return 0

    def read_id(node, target, capacity, flags):
        assert capacity == len(identifiers[node.value]) + 1 and flags == 0
        target.value = identifiers[node.value]
        return 0

    manager = SimpleNamespace(CM_Locate_DevNodeW=Mock(side_effect=locate),
                              CM_Get_Parent=Mock(side_effect=parent),
                              CM_Get_Device_ID_Size=Mock(side_effect=size),
                              CM_Get_Device_IDW=Mock(side_effect=read_id))
    monkeypatch.setattr(identity.C, "WinDLL", Mock(return_value=manager), raising=False)
    if removed:
        with pytest.raises(OSError, match="ConfigMgr 0xd"):
            identity._usb_parent_id(instance)
        assert manager.CM_Get_Device_IDW.call_count == 1
    else:
        assert identity._usb_parent_id(instance) == identifiers[43]
    assert visited == [41, 42]


@pytest.mark.parametrize("field,value,message", [
    ("Volumes", ["R:"], "selected volume"),
    ("Volumes", "Q:", "selected volume"),
    ("Index", 12, "disk number"),
    ("Index", True, "disk number"),
    ("DeviceID", r"\\.\PHYSICALDRIVE9", "disk number"),
    ("DeviceID", r"\\.\Q:", "disk number"),
    ("Model", "Ordinary USB flash drive", "disk model"),
    ("Size", 16740352, "disk size"),
    ("Size", 16777216.1, "disk size"),
    ("PNPDeviceID", r"SCSI\DISK&VEN_ATK\ATK-DSO-V1.0.0", "PnP identity"),
    ("PNPDeviceID", r"USBSTOR\DISK&VEN_ATK\OTHER-ATK-DSO-V1.0.0&0", "disk serial"),
    ("UsbDeviceID", r"USB\VID_9999&PID_028F\ATK-DSO-V1.0.0", "VID 28E9"),
    ("UsbDeviceID", r"USB\VID_28E9&PID_9999\ATK-DSO-V1.0.0", "PID 028F"),
    ("UsbDeviceID", r"USB\VID_28E9&PID_028F\ANOTHER", "USB serial"),
    ("UsbDeviceID", r"USB\VID_28E9&PID_028F\ATK-DSO-V1.0.0-OTHER", "USB serial"),
    ("UsbDeviceID", "", "USB parent"),
])
def test_mismatched_disk_or_usb_identity_is_rejected(monkeypatch, disk, field, value, message):
    disk[field] = value
    monkeypatch.setattr(identity, "_query_disk_identity", lambda drive: [disk])
    with pytest.raises(OSError, match=message):
        identity.verify_device_identity("Q:/")


@pytest.mark.parametrize("field", ["Volumes", "Index", "DeviceID", "Model", "Size", "PNPDeviceID", "UsbDeviceID"])
def test_missing_identity_fields_fail_closed(monkeypatch, disk, field):
    del disk[field]
    monkeypatch.setattr(identity, "_query_disk_identity", lambda drive: [disk])
    with pytest.raises(OSError):
        identity.verify_device_identity("Q:/")


@pytest.mark.parametrize("records", [[], [{}, {}], {}, None, [None]])
def test_missing_or_ambiguous_physical_disk_fails_closed(monkeypatch, records):
    monkeypatch.setattr(identity, "_query_disk_identity", lambda drive: records)
    with pytest.raises(OSError, match="exactly one physical disk"):
        identity.verify_device_identity("Q:/")


@pytest.mark.parametrize("root", ["Q", "Q:folder", "Q:/folder", "Q:/../", "Q:\n", "Q:;echo bad", r"\\server\share", r"\\.\PHYSICALDRIVE0"])
def test_invalid_roots_never_launch_a_query(monkeypatch, root):
    run = Mock(side_effect=AssertionError("No process may be started."))
    monkeypatch.setattr(identity.subprocess, "run", run)
    with pytest.raises(ValueError, match="drive root"):
        identity.verify_device_identity(root)
    with pytest.raises(ValueError, match="drive root"):
        identity._query_disk_identity(root)
    run.assert_not_called()


def test_query_uses_static_script_stdin_timeout_and_hidden_console(monkeypatch, disk):
    run = Mock(return_value=SimpleNamespace(stdout=json.dumps([disk])))
    monkeypatch.setattr(identity.subprocess, "run", run)
    assert identity._query_disk_identity("z:/") == [disk]
    args, kwargs = run.call_args
    assert args[0][-1] == identity._IDENTITY_QUERY
    assert args[0][1:4] == ["-NoProfile", "-NonInteractive", "-Command"]
    assert kwargs["input"] == "Z:\n"
    assert kwargs["timeout"] == 15 and kwargs["check"] is True
    assert kwargs["creationflags"] == getattr(subprocess, "CREATE_NO_WINDOW", 0)
    assert not kwargs.get("shell", False)


@pytest.mark.parametrize("failure", [
    subprocess.TimeoutExpired("powershell", 15),
    subprocess.CalledProcessError(1, "powershell"),
    FileNotFoundError("powershell"),
])
def test_failed_identity_query_fails_closed(monkeypatch, failure):
    monkeypatch.setattr(identity.subprocess, "run", Mock(side_effect=failure))
    with pytest.raises(OSError, match="identity query timed out|Cannot read"):
        identity.verify_device_identity("Q:/")


def test_malformed_query_output_fails_closed(monkeypatch):
    monkeypatch.setattr(identity.subprocess, "run", Mock(return_value=SimpleNamespace(stdout="not JSON")))
    with pytest.raises(OSError, match="invalid DS100 hardware identity"):
        identity.verify_device_identity("Q:/")


def fake_kernel(events, disk_number=11, device_type=7, short=False, fail=False):
    def control(handle, code, request, request_size, output, output_size, returned, overlap):
        if code == 0x2D1080:
            events.append("disk-number")
            if fail:
                return False
            number = C.cast(output, C.POINTER(scsi.DeviceNumber)).contents
            number.DeviceType, number.DeviceNumber, number.PartitionNumber = device_type, disk_number, 1
            C.cast(returned, C.POINTER(scsi.W.DWORD)).contents.value = C.sizeof(number) - int(short)
        elif code == 0x4D014:
            events.append("vendor")
            packet = C.cast(request, C.POINTER(scsi.Packet)).contents
            packet.request.DataTransferLength = 0
        else:
            raise AssertionError(f"Unexpected IOCTL {code:#x}")
        return True

    return SimpleNamespace(
        CreateFileW=Mock(side_effect=lambda *args: events.append("open") or 123),
        DeviceIoControl=Mock(side_effect=control),
        CloseHandle=Mock(side_effect=lambda handle: events.append("close")),
    )


def mock_windows(monkeypatch, kernel):
    monkeypatch.setattr(scsi, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(scsi, "find_drives", lambda: [Path("Q:/")])
    loader = Mock(return_value=kernel)
    monkeypatch.setattr(scsi.C, "WinDLL", loader, raising=False)
    monkeypatch.setattr(scsi.C, "get_last_error", lambda: 5, raising=False)
    return loader


def test_spoofed_atk_dso_label_never_opens_transport(monkeypatch, disk):
    events = []
    loader = mock_windows(monkeypatch, fake_kernel(events))
    disk["Model"] = "Ordinary flash drive labeled ATK-DSO"
    monkeypatch.setattr(identity, "_query_disk_identity", lambda drive: [disk])
    with pytest.raises(OSError, match="disk model"):
        scsi.ScsiTransport("Q:/")
    loader.assert_not_called()
    assert events == []


def test_identity_and_opened_disk_are_checked_before_vendor_commands(monkeypatch, disk):
    events = []
    kernel = fake_kernel(events)
    mock_windows(monkeypatch, kernel)
    query = Mock(side_effect=lambda drive: events.append("identity") or [disk])
    monkeypatch.setattr(identity, "_query_disk_identity", query)
    transport = scsi.ScsiTransport("Q:/")
    assert events == ["identity", "open", "disk-number"]
    assert kernel.CreateFileW.call_args.args[0] == r"\\.\Q:"
    for sequence in range(3):
        assert transport.exchange(make_cdb(INFO, sequence)) == b""
    assert events == ["identity", "open", "disk-number", "vendor", "vendor", "vendor"]
    query.assert_called_once_with("Q:")
    transport.close()
    with pytest.raises(OSError, match="closed"):
        transport.exchange(make_cdb(INFO, 4))
    assert events[-1] == "close"


@pytest.mark.parametrize("options", [
    {"disk_number": 12}, {"device_type": 2}, {"short": True}, {"fail": True},
])
def test_wrong_or_unconfirmed_opened_disk_is_closed_without_vendor_requests(monkeypatch, disk, options):
    events = []
    kernel = fake_kernel(events, **options)
    mock_windows(monkeypatch, kernel)
    monkeypatch.setattr(identity, "_query_disk_identity", lambda drive: [disk])
    with pytest.raises(OSError, match="physical disk|disk number"):
        scsi.ScsiTransport("Q:/")
    assert events == ["open", "disk-number", "close"]
    kernel.CloseHandle.assert_called_once_with(123)
