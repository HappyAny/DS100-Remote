"""Check native-refresh, input drain, file-system and acquisition boot fixes."""

import json
import struct
import pytest
from test_arm_candidate import ArmDevice, ROOT, STATE, BUFFER, CORE, unicorn_windows_exception_handler
from unicorn import UC_HOOK_CODE
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2
from ds100.protocol import DRAW_STREAM, INPUT_STATE, make_cdb, parse_response
from test_arm_draw_stream import DrawArm, register_widget


def symbols():
    m = json.loads((ROOT / 'candidates/follow-manifest.json').read_text())
    return {k: int(v, 16) for k, v in m['symbols'].items()}


class FollowArm(DrawArm):
    manifest_name = 'follow-manifest.json'
    image_name = 'v48-hardware-follow-decoded.bin'

    def __init__(self):
        # Do not execute R7 and then overlay R8 at the same ROM addresses.
        # Unicorn can retain translated R7 blocks after a host mem_write.
        ArmDevice.__init__(self, controls=True)
        self.mu.mem_write(0x08010000, (ROOT / 'build/arm' / self.image_name).read_bytes())
        m = json.loads((ROOT / 'candidates' / self.manifest_name).read_text())
        self.symbols = {k: int(v, 16) for k, v in m['symbols'].items()}
        self.usb_start_stub_address = self.symbols['ds100_v48_original_usb_start']
        self.ticks = self.tick_step = 0
        self.after_first_reply = False
        self.feed_after_reply = None
        self.autopoll = False
        self.stub_acquisition_apply = True
        self.mu.mem_write(0x2002CEF0, struct.pack('<7H', 480, 320, 0x9488, 1, 0x2C, 0x2A, 0x2B))
        self.call(self.symbols['ds100_v48_controls_init'])

    def on_code(self, mu, address, size, user):
        if address == 0x0802D456 and self.stub_acquisition_apply:
            self.return_from_stub()
        else:
            super().on_code(mu, address, size, user)

    def command(self, action, sequence=1):
        self.prepare(make_cdb(DRAW_STREAM, sequence, (action, 0, 0, 0)), state=0x7F)
        n = self.call(self.symbols['ds100_stream_handle'], 0x2002D74C, BUFFER, 4096)
        return parse_response(bytes(self.mu.mem_read(BUFFER, n)), DRAW_STREAM, sequence)

    def activate_for_capture(self):
        self.command(1)
        pointer = self.call(self.symbols['ds100_stream_queue'])
        self.mu.mem_write(pointer + 36, struct.pack('<I', 2))
        self.mu.mem_write(STATE + 1, b'\0')
        return pointer


def test_r8_stream_keeps_original_redraw_period_in_every_state():
    device = FollowArm()
    widget, original = register_widget(device)
    assert device.mu.mem_read(0x08027874, 4) == bytes.fromhex('4ff4fa70')
    device.command(1)
    assert device.mu.mem_read(widget, len(original)) == original
    device.ticks = 1000
    device.call(symbols()['ds100_stream_tick'])
    assert device.mu.mem_read(widget, len(original)) == original


def test_r8_pending_input_can_acknowledge_then_drain_large_bitmap_without_lcd_reentry():
    device = FollowArm()
    pointer = device.activate_for_capture()
    # The actual device had room for an 8 KiB queue, not the preferred 16 KiB.
    device.mu.mem_write(pointer + 4, struct.pack('<I', 8192))
    source = 0x20000000
    pixels = struct.pack('<' + 'H' * (480 * 30), *[(i * 119) & 65535 for i in range(480 * 30)])
    device.mu.mem_write(source, pixels)
    device.pixel_calls.clear()
    device.replies.clear()
    device.lcd_writes.clear()
    device.autopoll = True
    device.prepare(make_cdb(INPUT_STATE, 42, (8, 0, 0, 0)), state=0x7F)
    device.call(symbols()['ds100_v48_profile_bitmap'], 0, 0, 479, 29,
                stack_args=(source,), instruction_limit=10000000)
    first = device.replies[0][3]
    assert parse_response(first, INPUT_STATE, 42) == struct.pack('<HHhh', 8, 600, 0, 0)
    assert len(device.replies) >= 5 and device.pixel_calls == []
    assert struct.unpack('<I', device.mu.mem_read(pointer + 36, 4))[0] == 2
    assert device.mu.mem_read(source, len(pixels)) == pixels
    writes = [v for a, _, v in device.lcd_writes if a == 0x60020000]
    assert struct.pack('<' + 'H' * len(writes), *writes) == pixels


@pytest.mark.parametrize('command,args', [(DRAW_STREAM, (0, 0, 0, 0)),
    (DRAW_STREAM, (1, 0, 0, 0)), (DRAW_STREAM, (3, 0, 0, 0)), (2, (0, 0, 8, 8))])
def test_r8_nested_capture_does_not_run_stop_start_freeze_or_lcd_read(command, args):
    device = FollowArm()
    pointer = device.activate_for_capture()
    device.prepare(make_cdb(command, 42, args), state=0x7F)
    assert device.call(symbols()['ds100_stream_service_poll']) == 0
    assert device.call(symbols()['ds100_stream_queue']) == pointer
    assert device.mu.mem_read(STATE + 1, 1) == b'\x7F'


@pytest.mark.parametrize('follow', [False, True])
def test_r8_actual_usb_initialization_preserves_mount_and_reapplies_current_acquisition_once(follow):
    device = FollowArm() if follow else DrawArm()
    from test_arm_draw_stream import symbols as r7_symbols
    syms = symbols() if follow else r7_symbols()
    device.stub_usb_start = False
    if follow:
        device.stub_acquisition_apply = False
    events = []
    helpers = {0x08012BA0, 0x08012546, 0x08012598, 0x080125EA,
               0x0801027C, 0x08012618, 0x08012E3C, 0x08012DE6,
               0x080467B0, 0x0801274A, 0x08035AD4}
    def board(mu, address, size, user):
        if address in (0x08035B84, 0x0802D456):
            events.append('unmount' if address == 0x08035B84 else 'reapply-current-acquisition')
            device.return_from_stub()
        elif address in helpers:
            device.return_from_stub()
    device.mu.hook_add(UC_HOOK_CODE, board)
    device.call(syms['ds100_v48_auto_usb'])
    assert device.mu.mem_read(0x200213F0, 1) == b'\x01'
    assert events == (['reapply-current-acquisition'] if follow else ['unmount'])
    device.call(syms['ds100_v48_auto_usb'])
    assert len(events) == 1


def test_original_config_save_preserves_payload_and_calibration_bytes():
    device = FollowArm()
    # Use opaque synthetic bytes so this check also runs from the source ZIP,
    # which deliberately excludes the user's configuration and calibration.
    original = bytearray((i * 73 + 19) & 255 for i in range(480))
    struct.pack_into('<I', original, 4, 28)
    device.mu.mem_write(0x20028250, bytes(original))
    written = []
    def file_save(mu, address, size, user):
        if address == 0x08035C9A:
            pointer = mu.reg_read(UC_ARM_REG_R0)
            length = mu.reg_read(UC_ARM_REG_R1)
            written.append(bytes(mu.mem_read(pointer, length)))
            device.return_from_stub()
    device.mu.hook_add(UC_HOOK_CODE, file_save)
    device.call(0x0802E5F8)
    assert len(written) == 1 and len(written[0]) == 480
    assert written[0][4:] == original[4:]
    low, high = struct.unpack_from('<HH', written[0])
    assert low ^ high == 65535
    device.mu.mem_write(0x20000000, b'\xff\xff')
    device.mu.mem_write(0x20000100, written[0][4:])
    device.call(0x0803548C, 0x20000000, 0x20000100, 476)
    assert low == int.from_bytes(device.mu.mem_read(0x20000000, 2), 'big')


@pytest.mark.parametrize('phase', [0, 1, 3])
def test_r8_defers_storage_reads_to_gui_task_and_preserves_stock_state(phase):
    device = FollowArm()
    cdb = bytearray(16)
    cdb[0] = 0x28
    device.prepare(bytes(cdb), state=phase)
    device.mu.mem_write(STATE + 8, struct.pack('<I', 0x20002000))
    calls = []
    def stock_read(mu, address, size, user):
        if address == symbols()['ds100_v48_original_scsi']:
            calls.append((mu.reg_read(UC_ARM_REG_R0), bytes(mu.mem_read(STATE + 1, 1)),
                          bytes(mu.mem_read(STATE + 8, 4))))
            device.return_from_stub()
    device.mu.hook_add(UC_HOOK_CODE, stock_read)
    device.call(symbols()['ds100_v48_scsi_hook'], CORE)
    assert calls == [] and device.mu.mem_read(STATE + 1, 1) == bytes([0x80 | phase])
    device.call(symbols()['ds100_v48_poll_wrapper'])
    assert calls == [(CORE, bytes([phase]), struct.pack('<I', 0x20002000))]


def test_r8_usb_reset_cancels_queued_storage_read():
    device = FollowArm()
    cdb = bytearray(16)
    cdb[0] = 0x28
    device.prepare(bytes(cdb))
    device.call(symbols()['ds100_v48_scsi_hook'], CORE)
    device.mu.mem_write(STATE + 1, b'\0')
    def unexpected_stock(mu, address, size, user):
        if address == symbols()['ds100_v48_original_scsi']:
            raise AssertionError('Do not run a storage read after USB reset cancels it.')
    device.mu.hook_add(UC_HOOK_CODE, unexpected_stock)
    device.call(symbols()['ds100_v48_poll_wrapper'])
