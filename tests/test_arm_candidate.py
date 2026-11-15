"""Execute the linked ARM candidate against modeled USB and LCD boundaries."""

from pathlib import Path
import faulthandler
import gc
import json
import os
import struct
import sys
import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".tools"))
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS, UC_PROT_READ, UC_PROT_WRITE, UC_PROT_EXEC, UC_HOOK_CODE, UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE
from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_R4, UC_ARM_REG_R5,
                               UC_ARM_REG_R6, UC_ARM_REG_R7, UC_ARM_REG_R8, UC_ARM_REG_R9, UC_ARM_REG_R10,
                               UC_ARM_REG_R11, UC_ARM_REG_SP, UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_XPSR)
from ds100.protocol import INFO, SCREEN_READ, make_cdb, parse_response, parse_tile

STATE, CDB_ADDRESS, BUFFER, CBW, CORE = 0x20021514, 0x2002D74C, 0x2002D75C, 0x2002E75C, 0x200215D8
SENTINEL, STACK = 0x0800F000, 0x2002FF90


@pytest.fixture(scope="module", autouse=True)
def unicorn_windows_exception_handler():
    # Unicorn 2.1.4 commits JIT pages in a Windows exception handler. Python's
    # handler reports these handled first-touch faults before Unicorn sees them.
    # https://github.com/unicorn-engine/unicorn/issues/2264
    restore = os.name == "nt" and faulthandler.is_enabled()
    if restore:
        faulthandler.disable()
    try:
        yield
    finally:
        gc.collect()
        if restore:
            faulthandler.enable()


class ArmDevice:
    def __init__(self, background=False, fast=False, profile=False, controls=False):
        stem = "v48-background-screen" if background else "v48-screen"
        if fast:
            stem = "v48-background-fast"
        if profile:
            stem = "v48-background-profile"
        if controls:
            stem = "v48-background-controls"
        image = (ROOT / "build/arm" / f"{stem}-decoded.bin").read_bytes()
        self.mu = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
        self.mu.mem_map(0x08000000, 0x80000, UC_PROT_READ | UC_PROT_EXEC)
        self.mu.mem_write(0x08010000, image)
        self.mu.mem_map(0x20000000, 0x40000, UC_PROT_READ | UC_PROT_WRITE)
        if profile or controls:
            self.mu.mem_map(0x10000000, 0x10000, UC_PROT_READ | UC_PROT_WRITE)
        self.mu.mem_map(0x60000000, 0x40000, UC_PROT_READ | UC_PROT_WRITE)
        self.mu.reg_write(UC_ARM_REG_XPSR, 0x01000000)
        self.replies = []
        self.pixel_calls = []
        self.idle_calls = 0
        self.deinit_calls = 0
        self.common_ui_calls = []
        self.usb_packets = []
        self.reset_on_pixel = False
        self.replacement_cdb = None
        self.use_actual_lcd = False
        self.use_actual_usb_stop = False
        self.read_values = []
        self.fast_lcd = fast
        self.cursor_calls = []
        self.lcd_writes = []
        self.usb_start_calls = 0
        self.stub_usb_start = controls
        self.usb_start_stub_address = symbols(controls=True).get('ds100_v48_original_usb_start') if controls else None
        self.mu.hook_add(UC_HOOK_CODE, self.on_code)
        self.mu.hook_add(UC_HOOK_MEM_READ, self.on_read, begin=0x60020000, end=0x60020001)
        self.mu.hook_add(UC_HOOK_MEM_WRITE, self.on_write, begin=0x6001FFFE, end=0x60020001)

    def return_from_stub(self):
        self.mu.reg_write(UC_ARM_REG_PC, self.mu.reg_read(UC_ARM_REG_LR))

    def on_code(self, mu, address, size, user):
        if self.stub_usb_start and address == self.usb_start_stub_address:
            self.usb_start_calls += 1
            mu.mem_write(0x200213F0, b'\x01')
            self.return_from_stub()
        elif address == 0x0801538A:
            self.idle_calls += 1
            self.return_from_stub()
        elif address == 0x080453FA:
            core, pointer, length = (mu.reg_read(reg) for reg in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2))
            assert length <= 4096
            data = bytes(mu.mem_read(pointer, length)) if length else b""
            self.replies.append((core, pointer, length, data))
            mu.reg_write(UC_ARM_REG_R0, 0)
            self.return_from_stub()
        elif address == 0x0804E0FA and not self.use_actual_lcd:
            x, y = mu.reg_read(UC_ARM_REG_R0), mu.reg_read(UC_ARM_REG_R1)
            assert x < 480 and y < 320
            self.pixel_calls.append((x, y))
            if self.reset_on_pixel:
                mu.mem_write(STATE + 1, b"\0")
            if self.replacement_cdb is not None:
                mu.mem_write(CDB_ADDRESS, self.replacement_cdb)
                self.replacement_cdb = None
            value = ((x & 31) << 11) | ((y & 63) << 5) | ((x + y) & 31)
            mu.reg_write(UC_ARM_REG_R0, value)
            self.return_from_stub()
        elif self.fast_lcd and address == 0x0803C58E:
            self.cursor_calls.append((mu.reg_read(UC_ARM_REG_R0), mu.reg_read(UC_ARM_REG_R1)))
            self.return_from_stub()
        elif self.use_actual_lcd and address in (0x0803C58E, 0x080101FA):
            if address == 0x0803C58E:
                self.cursor_calls.append((mu.reg_read(UC_ARM_REG_R0), mu.reg_read(UC_ARM_REG_R1)))
            self.return_from_stub()
        elif address == 0x080452EA and not self.use_actual_usb_stop:
            self.deinit_calls += 1
            self.return_from_stub()
        elif address == 0x080219B4:
            self.common_ui_calls.append(address)
            mu.reg_write(UC_ARM_REG_R0, 0x20032000)
            self.return_from_stub()
        elif address == 0x0802430C:
            self.common_ui_calls.append(address)
            assert mu.reg_read(UC_ARM_REG_R0) == 0x20032000
            self.return_from_stub()
        elif address == 0x08046990:
            core, endpoint, pointer, length = (mu.reg_read(reg) for reg in (
                UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3))
            self.usb_packets.append((core, endpoint, bytes(mu.mem_read(pointer, length))))
            self.return_from_stub()
        elif address == 0x080455B8:
            raise AssertionError("The USB write handler must not run in these tests.")

    def on_read(self, mu, access, address, size, value, user):
        if self.read_values:
            mu.mem_write(address, struct.pack("<H", self.read_values.pop(0)))

    def on_write(self, mu, access, address, size, value, user):
        self.lcd_writes.append((address, size, value))

    def call(self, address, r0=0, r1=0, r2=0, r3=0, stack_args=(), instruction_limit=2000000):
        call_stack = STACK - 32 if stack_args else STACK
        self.mu.reg_write(UC_ARM_REG_SP, call_stack)
        if stack_args:
            self.mu.mem_write(call_stack, struct.pack('<' + 'I' * len(stack_args), *stack_args))
        self.mu.reg_write(UC_ARM_REG_LR, SENTINEL | 1)
        self.mu.reg_write(UC_ARM_REG_R0, r0)
        self.mu.reg_write(UC_ARM_REG_R1, r1)
        self.mu.reg_write(UC_ARM_REG_R2, r2)
        self.mu.reg_write(UC_ARM_REG_R3, r3)
        self.mu.emu_start(address | 1, SENTINEL, count=instruction_limit)
        assert self.mu.reg_read(UC_ARM_REG_PC) == SENTINEL
        assert self.mu.reg_read(UC_ARM_REG_SP) == call_stack
        return self.mu.reg_read(UC_ARM_REG_R0)

    def prepare(self, cdb, state=0):
        self.mu.mem_write(CDB_ADDRESS, cdb)
        self.mu.mem_write(STATE + 1, bytes([state]))
        self.mu.mem_write(CBW + 8, struct.pack("<I", 16404))
        self.mu.mem_write(CBW + 12, b"\x80")
        self.mu.mem_write(STATE + 8, struct.pack("<I", CORE))


def symbols(background=False, fast=False, profile=False, controls=False):
    name = "background-manifest.json" if background else "manifest.json"
    if fast:
        name = "fast-manifest.json"
    if profile:
        name = "profile-manifest.json"
    if controls:
        name = "controls-manifest.json"
    manifest = json.loads((ROOT / "candidates" / name).read_text())
    return {name: int(value, 16) for name, value in manifest["symbols"].items()}


def test_arm_vendor_request_defers_lcd_access_to_display_task():
    device = ArmDevice()
    request = make_cdb(INFO, 42)
    device.prepare(request)
    device.call(0x08045476, CORE)
    assert bytes(device.mu.mem_read(STATE + 1, 1)) == b"\x7f"
    assert not device.pixel_calls and not device.replies
    device.call(symbols()["ds100_v48_poll_wrapper"])
    assert device.idle_calls == 1 and len(device.replies) == 1
    core, pointer, length, data = device.replies[0]
    assert core == CORE and pointer == BUFFER
    info = json.loads(parse_response(data, INFO, 42))
    assert info["software"] == "4.8" and info["capabilities"] == ["screen"]


def test_arm_screen_tile_uses_original_pixel_read_wrapper():
    device = ArmDevice()
    device.prepare(make_cdb(SCREEN_READ, 91, (416, 312, 64, 8)), state=0x7F)
    device.call(symbols()["ds100_v48_poll_wrapper"])
    assert len(device.pixel_calls) == 512
    tile = parse_tile(parse_response(device.replies[0][3], SCREEN_READ, 91))
    yy, xx = np.indices((8, 64))
    x, y = xx + 416, yy + 312
    expected = (((x & 31) << 11) | ((y & 63) << 5) | ((x + y) & 31)).astype("<u2")
    assert tile.pixels == expected.tobytes()


def test_arm_usb_reset_during_screen_read_cancels_reply():
    device = ArmDevice()
    device.reset_on_pixel = True
    device.prepare(make_cdb(SCREEN_READ, 5, (0, 0, 64, 8)), state=0x7F)
    device.call(symbols()["ds100_v48_poll_wrapper"])
    assert device.pixel_calls
    assert not device.replies


def test_arm_new_request_after_reset_does_not_receive_old_screen_reply():
    device = ArmDevice()
    device.replacement_cdb = make_cdb(INFO, 101)
    device.prepare(make_cdb(SCREEN_READ, 100, (0, 0, 64, 8)), state=0x7F)
    device.call(symbols()["ds100_v48_poll_wrapper"])
    assert device.pixel_calls and not device.replies
    assert bytes(device.mu.mem_read(STATE + 1, 1)) == b"\x7f"
    device.call(symbols()["ds100_v48_poll_wrapper"])
    assert len(device.replies) == 1
    assert json.loads(parse_response(device.replies[0][3], INFO, 101))["capabilities"] == ["screen"]


def test_arm_stock_scsi_inquiry_keeps_original_dispatch_and_registers():
    device = ArmDevice()
    request = bytearray(16)
    request[0], request[4] = 0x12, 36
    device.prepare(bytes(request))
    registers = (UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_R7,
                 UC_ARM_REG_R8, UC_ARM_REG_R9, UC_ARM_REG_R10, UC_ARM_REG_R11)
    for index, register in enumerate(registers):
        device.mu.reg_write(register, 0x12340000 + index)
    device.call(0x08045476, CORE)
    assert len(device.replies) == 1 and device.replies[0][0] == CORE and device.replies[0][2] == 36
    for index, register in enumerate(registers):
        assert device.mu.reg_read(register) == 0x12340000 + index


@pytest.mark.parametrize("state", [1, 3])
def test_arm_vendor_transfer_completion_reuses_stock_state_machine(state):
    device = ArmDevice()
    device.prepare(make_cdb(INFO, 7), state=state)
    device.call(0x08045476, CORE)
    assert len(device.replies) == 1 and device.replies[0][2] == 0
    assert not device.pixel_calls


def test_arm_original_lcd_reader_bounds_and_rgb565_path():
    device = ArmDevice()
    device.use_actual_lcd = True
    device.mu.mem_write(0x2002CEF0, struct.pack("<HHH", 480, 320, 0x7796))
    device.read_values = [0, 0xF81F]
    assert device.call(0x0804E0FA, 479, 319) == 0xF81F
    assert not device.read_values
    device.read_values = [0x1234]
    assert device.call(0x0804E0FA, 480, 319) == 0
    assert device.read_values == [0x1234]


@pytest.mark.parametrize("background,expected_deinit", [(False, 1), (True, 0)])
def test_arm_background_variant_keeps_usb_on_menu_exit(background, expected_deinit):
    device = ArmDevice(background)
    widget, event = 0x20032000, 0x20032100
    # The original USB dialog accepts this physical E-key event.
    device.mu.mem_write(event, b"\x03\x01")
    assert device.call(0x080306C0, widget, event) == 1
    assert device.deinit_calls == expected_deinit
    assert bytes(device.mu.mem_read(widget + 0x48, 1)) == b"\x01"


@pytest.mark.parametrize("background,expected_deinit", [(False, 1), (True, 0)])
def test_arm_background_variant_keeps_usb_in_common_ui_callback(background, expected_deinit):
    device = ArmDevice(background)
    device.call(0x080148CE)
    assert device.deinit_calls == expected_deinit
    assert device.common_ui_calls == [0x080219B4, 0x0802430C]


def test_arm_background_usb_reports_write_protected_media():
    device = ArmDevice(background=True)
    request = bytearray(16)
    request[0], request[4] = 0x1A, 8
    device.prepare(bytes(request))
    device.call(0x08045476, CORE)
    assert len(device.replies) == 1
    assert device.replies[0][2] == 8
    assert device.replies[0][3][2] & 0x80


def test_arm_background_usb_write10_returns_failed_csw_before_storage_access():
    device = ArmDevice(background=True)
    request = bytearray(16)
    request[0], request[8] = 0x2A, 1
    device.prepare(bytes(request))
    device.mu.mem_write(CBW + 12, b"\0")
    device.mu.mem_write(CBW + 4, struct.pack("<I", 123))
    device.call(0x08045476, CORE)
    assert len(device.usb_packets) == 1
    core, endpoint, csw = device.usb_packets[0]
    assert core == CORE and endpoint == 0x81
    assert csw[:4] == b"USBS" and len(csw) == 13 and csw[12] == 1
    assert struct.unpack_from("<I", csw, 4)[0] == 123


def test_arm_info_reports_build_and_actual_patch_bytes():
    device = ArmDevice(background=True)
    device.mu.mem_write(0x2002CEF4, struct.pack("<H", 0x7796))
    device.mu.mem_write(0x200203CC, struct.pack("<I", 168000000))
    device.prepare(make_cdb(INFO, 777), state=0x7F)
    device.call(symbols(True)["ds100_v48_poll_wrapper"])
    info = json.loads(parse_response(device.replies[0][3], INFO, 777))
    assert info["build"] == "background-r3"
    assert {key: info["diag"][key] for key in ("common", "dialog", "usb_stop", "lcd_id", "core_hz_hex")} == {
        "common": "bf00bf00", "dialog": "bf00bf00", "usb_stop": "4770", "lcd_id": "7796", "core_hz_hex": "0a037a00"}
    code = bytes(device.mu.mem_read(0x08035AD4, 4))
    assert info["diag"]["clock_hook"] == f"{struct.unpack('<I', code)[0]:08x}"
    assert len(device.replies[0][3]) < 512


def test_arm_background_direct_usb_stop_keeps_initialized_flag():
    device = ArmDevice(background=True)
    device.use_actual_usb_stop = True
    device.mu.mem_write(0x200213F0, b"\x01")
    device.call(0x080452EA)
    assert bytes(device.mu.mem_read(0x200213F0, 1)) == b"\x01"


@pytest.mark.parametrize("usb,requested,current,expected", [
    (1, 250000000, 168000000, 168000000), (1, 200000000, 168000000, 168000000),
    (1, 168000000, 168000000, 168000000), (0, 250000000, 250000000, 250000000)])
def test_arm_clock_hook_keeps_usb_compatible_frequency_and_original_fast_path(usb, requested, current, expected):
    device = ArmDevice(background=True)
    device.mu.mem_write(0x200213F0, bytes([usb]))
    device.mu.mem_write(0x200203CC, struct.pack("<I", current))
    assert device.call(0x08035AD4, requested) == expected
    assert struct.unpack("<I", device.mu.mem_read(0x200203CC, 4))[0] == current
