"""Read vendor SCSI responses through the Windows mass-storage driver."""

import ctypes as C
from ctypes import wintypes as W
import os
from .protocol import CDB, MAX_PAYLOAD, HEADER
from .storage import find_drives
from .windows_identity import drive_name, verify_device_identity


class SPTD(C.Structure):
    _fields_ = [("Length", W.USHORT), ("ScsiStatus", C.c_ubyte), ("PathId", C.c_ubyte),
                ("TargetId", C.c_ubyte), ("Lun", C.c_ubyte), ("CdbLength", C.c_ubyte),
                ("SenseInfoLength", C.c_ubyte), ("DataIn", C.c_ubyte),
                ("DataTransferLength", W.ULONG), ("TimeOutValue", W.ULONG),
                ("DataBuffer", C.c_void_p), ("SenseInfoOffset", W.ULONG), ("Cdb", C.c_ubyte * 16)]


class Packet(C.Structure):
    _fields_ = [("request", SPTD), ("padding", W.ULONG), ("sense", C.c_ubyte * 32)]


class DeviceNumber(C.Structure):
    _fields_ = [("DeviceType", W.DWORD), ("DeviceNumber", W.DWORD), ("PartitionNumber", W.DWORD)]


class ScsiTransport:
    def __init__(self, root, timeout_seconds=12):
        if os.name != "nt":
            raise OSError("The SCSI transport requires Windows.")
        if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 30:
            raise ValueError('The SCSI timeout must be an integer from 1 to 30 seconds.')
        self.timeout_seconds = timeout_seconds
        self.handle = None
        drive = drive_name(root)
        if drive not in [drive_name(path) for path in find_drives()]:
            raise OSError("The selected volume is not labeled ATK-DSO.")
        # Query once per opened transport, before any device handle or vendor CDB.
        self.identity = verify_device_identity(drive)
        self.kernel = C.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateFileW.restype = W.HANDLE
        self.kernel.CreateFileW.argtypes = [W.LPCWSTR, W.DWORD, W.DWORD, C.c_void_p, W.DWORD, W.DWORD, W.HANDLE]
        self.kernel.DeviceIoControl.argtypes = [W.HANDLE, W.DWORD, C.c_void_p, W.DWORD, C.c_void_p, W.DWORD, C.POINTER(W.DWORD), C.c_void_p]
        self.kernel.DeviceIoControl.restype = W.BOOL
        self.kernel.CloseHandle.argtypes = [W.HANDLE]
        path = "\\\\.\\" + drive
        self.handle = self.kernel.CreateFileW(path, 0xC0000000, 3, None, 3, 0, None)
        if self.handle == C.c_void_p(-1).value:
            self.handle = None
            raise OSError(C.get_last_error(), "Cannot open the checked DS100 volume. Close other clients and reconnect the device.")
        try:
            # Bind the opened handle to the checked disk, even if a drive letter
            # was reassigned during the identity query. This standard IOCTL is
            # read-only and does not send a vendor SCSI command.
            number, returned = DeviceNumber(), W.DWORD()
            if not self.kernel.DeviceIoControl(self.handle, 0x2D1080, None, 0, C.byref(number), C.sizeof(number), C.byref(returned), None):
                raise OSError(C.get_last_error(), "Cannot confirm the opened DS100 disk number.")
            if (returned.value != C.sizeof(number) or number.DeviceType != 7
                    or number.DeviceNumber != self.identity["Index"]):
                raise OSError("The opened volume does not match the checked physical disk.")
        except BaseException:
            self.close()
            raise

    def exchange(self, cdb):
        if self.handle is None:
            raise OSError("The DS100 transport is closed.")
        if len(cdb) != 16:
            raise ValueError("The vendor command must contain 16 bytes.")
        opcode, command, version, flags, sequence, arg0, arg1, arg2, arg3 = CDB.unpack(cdb)
        if opcode != 0xC0 or command not in (1, 2, 3, 4, 5, 6, 7, 8, 9) or version != 1 or flags:
            raise ValueError("The transport rejected a command outside the remote protocol.")
        data = C.create_string_buffer(MAX_PAYLOAD + HEADER.size)
        packet = Packet()
        request = packet.request
        request.Length = C.sizeof(SPTD)
        request.CdbLength = 16
        request.SenseInfoLength = 32
        request.DataIn = 1
        request.DataTransferLength = len(data)
        if command == 7 and 1 <= arg0 <= 4076 and not (arg1 or arg2 or arg3):
            # This response has a known size. Request that size so a 4096-byte
            # reply completes without a USB short packet or zero-length packet.
            request.DataTransferLength = HEADER.size + arg0
        elif command == 9:
            request.DataTransferLength = 4096
        # Stock AUTO can hold the display loop for several seconds. The remote
        # queue is serviced by that loop, so short requests must also wait.
        request.TimeOutValue = self.timeout_seconds
        request.DataBuffer = C.addressof(data)
        request.SenseInfoOffset = Packet.sense.offset
        request.Cdb[:] = cdb
        returned = W.DWORD()
        if not self.kernel.DeviceIoControl(self.handle, 0x4D014, C.byref(packet), C.sizeof(packet), C.byref(packet), C.sizeof(packet), C.byref(returned), None):
            raise OSError(C.get_last_error(), "The SCSI request failed.")
        if request.ScsiStatus:
            raise OSError("The device rejected the remote command. The stock firmware has no remote service.")
        if request.DataTransferLength > len(data):
            raise OSError("The driver reported an invalid transfer length.")
        return data.raw[:request.DataTransferLength]

    def close(self):
        if self.handle is not None:
            self.kernel.CloseHandle(self.handle)
            self.handle = None
