"""Run R7 ARM bytes with modeled USB, time, and LCD boundaries."""

import json
from pathlib import Path
import struct
import pytest
from unicorn import UC_HOOK_CODE
from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,
    UC_ARM_REG_R3, UC_ARM_REG_R4, UC_ARM_REG_R12, UC_ARM_REG_XPSR)
from ds100.protocol import DRAW_STREAM, make_cdb, parse_response
from ds100.drawing import PACKET, RECORD
from test_arm_candidate import (ArmDevice, ROOT, STATE, BUFFER, CORE,
                                unicorn_windows_exception_handler)


def symbols():
    report = json.loads((ROOT / 'candidates/draw-stream-manifest.json').read_text())
    return {name: int(value, 16) for name, value in report['symbols'].items()}


class DrawArm(ArmDevice):
    def __init__(self):
        super().__init__(controls=True)
        image = (ROOT / 'build/arm/v48-background-draw-stream-decoded.bin').read_bytes()
        self.mu.mem_write(0x08010000, image)
        self.usb_start_stub_address = symbols()['ds100_v48_original_usb_start']
        self.ticks = 0
        self.tick_step = 0
        self.after_first_reply = False
        self.feed_after_reply = None
        self.autopoll = False
        self.mu.mem_write(0x2002CEF0, struct.pack('<7H', 480, 320, 0x9488, 1, 0x2C, 0x2A, 0x2B))
        self.call(symbols()['ds100_v48_controls_init'])

    def on_code(self, mu, address, size, user):
        if address == 0x08015384:
            if self.after_first_reply:
                self.ticks += self.tick_step
            mu.reg_write(UC_ARM_REG_R0, self.ticks)
            self.return_from_stub()
        elif address == 0x0803C58E:
            self.cursor_calls.append((mu.reg_read(UC_ARM_REG_R0), mu.reg_read(UC_ARM_REG_R1)))
            self.return_from_stub()
        else:
            super().on_code(mu, address, size, user)
            if address == 0x080453FA and len(self.replies) == 1:
                self.after_first_reply = True
                if self.feed_after_reply is not None:
                    self.prepare(make_cdb(DRAW_STREAM, 2, (self.feed_after_reply, 0, 0, 0)), state=0x7F)
            if address == 0x080453FA and self.autopoll:
                self.prepare(make_cdb(DRAW_STREAM, 100 + len(self.replies), (2, 0, 0, 0)), state=0x7F)

    def command(self, action, sequence=1):
        self.prepare(make_cdb(DRAW_STREAM, sequence, (action, 0, 0, 0)), state=0x7F)
        n = self.call(symbols()['ds100_stream_handle'], 0x2002D74C, BUFFER, 4096)
        data = bytes(self.mu.mem_read(BUFFER, n))
        return parse_response(data, DRAW_STREAM, sequence)

    def activate_for_capture(self):
        self.command(1)
        pointer = self.call(symbols()['ds100_stream_queue'])
        assert 0x10000008 <= pointer < 0x10010000
        self.mu.mem_write(pointer + 36, struct.pack('<I', 2))
        self.mu.mem_write(STATE + 1, b'\0')
        return pointer


def test_r7_start_allocates_stock_heap_and_emits_actual_arm_baseline_rows():
    device = DrawArm()
    free_before = device.call(0x08010514)
    payload = device.command(1)
    magic, epoch, first, count, flags = PACKET.unpack_from(payload)
    assert (magic, epoch, first, count, flags) == (b'DRW1', 1, 0, 5, 1)
    assert device.pixel_calls == [(x, y) for y in range(5) for x in range(480)]
    # Four rows fit; one additional row is read to determine packet capacity.
    offset = PACKET.size + 6
    for y in range(4):
        op, options, length = RECORD.unpack_from(payload, offset)
        assert op == 3 and options == 0 and length == 968
        assert struct.unpack_from('<HHHBB', payload, offset + 4) == (0, y, 480, 0, 0)
        expected = [((x & 31) << 11) | ((y & 63) << 5) | ((x + y) & 31) for x in range(480)]
        assert payload[offset + 12:offset + 972] == struct.pack('<480H', *expected)
        offset += 972
    assert device.call(0x08010514) < free_before
    assert device.command(0, 2) == b''
    assert device.call(0x08010514) == free_before


def test_r7_baseline_holds_whole_gui_call_until_stop_request():
    device = DrawArm()
    device.feed_after_reply = 0
    device.prepare(make_cdb(DRAW_STREAM, 1, (1, 0, 0, 0)), state=0x7F)
    device.call(symbols()['ds100_v48_poll_wrapper'])
    assert device.idle_calls == 1
    assert len(device.replies) == 2
    assert device.call(symbols()['ds100_stream_queue']) == 0


def test_r7_lost_host_releases_hold_and_returns_all_heap_memory():
    device = DrawArm()
    free_before = device.call(0x08010514)
    device.tick_step = 250
    device.prepare(make_cdb(DRAW_STREAM, 1, (1, 0, 0, 0)), state=0x7F)
    device.call(symbols()['ds100_v48_poll_wrapper'])
    assert device.idle_calls == 1 and len(device.replies) == 1
    assert device.call(symbols()['ds100_stream_queue']) == 0
    assert device.call(0x08010514) == free_before


@pytest.mark.parametrize('name,args,stack_args,count,color', [
    ('ds100_v48_profile_point', (3, 4, 0x1234, 0), (), 1, 0x1234),
    ('ds100_v48_profile_rect', (1, 1, 3, 2), (0xABCD,), 6, 0xABCD),
])
def test_r7_capture_keeps_original_lcd_writes_and_stack_arguments(name, args, stack_args, count, color):
    device = DrawArm()
    device.activate_for_capture()
    device.lcd_writes.clear()
    device.call(symbols()[name], *args, stack_args=stack_args)
    device.call(symbols()['ds100_v48_profile_pass'])
    writes = [value for address, size, value in device.lcd_writes if address == 0x60020000]
    assert writes == [color] * count
    payload = device.command(2, 3)
    _, _, first, records, _ = PACKET.unpack_from(payload)
    assert first == 5 and records == 2


def test_r7_clear_trampoline_keeps_color_and_native_pixel_count():
    device = DrawArm()
    device.activate_for_capture()
    device.mu.mem_write(0x2002CEF0, struct.pack('<2H', 3, 2))
    device.lcd_writes.clear()
    device.call(0x0803D2C8, 0x5A3C)
    writes = [value for address, size, value in device.lcd_writes if address == 0x60020000]
    assert writes == [0x5A3C] * 6
    device.call(symbols()['ds100_v48_profile_pass'])
    payload = device.command(2, 3)
    assert payload[PACKET.size:PACKET.size + 6] == struct.pack('<BBHH', 0, 0, 2, 0x5A3C)


def test_r7_transposed_scan_captures_vertical_pixels_in_baseline_coordinates():
    device = DrawArm()
    device.activate_for_capture()
    device.lcd_writes.clear()
    device.call(0x0803C9E2, 4)
    assert struct.unpack('<2H', device.mu.mem_read(0x2002CEF0, 4)) == (320, 480)
    # The original scan function writes MADCTL=0x88 for this mode.
    assert device.lcd_writes[0] == (0x6001FFFE, 2, 0x36)
    assert device.lcd_writes[1] == (0x60020000, 2, 0x88)
    source = 0x20000000
    device.mu.mem_write(source, struct.pack('<3H', 123, 456, 789))
    device.lcd_writes.clear()
    device.call(symbols()['ds100_v48_profile_span_a'], 20, 22, 400, source)
    assert [v for a, _, v in device.lcd_writes if a == 0x60020000] == [123, 456, 789]
    device.call(symbols()['ds100_v48_profile_pass'])
    payload = device.command(2, 3)
    assert payload[PACKET.size:PACKET.size + 18] == struct.pack('<BBHHHHBB3H', 5, 0, 14, 400, 20, 3, 0, 0, 123, 456, 789)


def test_r7_drains_queue_without_reentering_lcd_reads_during_large_bitmap():
    device = DrawArm()
    pointer = device.activate_for_capture()
    source = 0x20000000
    pixels = struct.pack('<' + 'H' * (480 * 40), *[(i * 119) & 65535 for i in range(480 * 40)])
    device.mu.mem_write(source, pixels)
    device.replies.clear()
    device.pixel_calls.clear()
    device.lcd_writes.clear()
    device.autopoll = True
    device.prepare(make_cdb(DRAW_STREAM, 100, (2, 0, 0, 0)), state=0x7F)
    # This high-entropy case encodes and drains 38400 bytes, then executes the
    # original bitmap writer. It exceeds the small-tile instruction budget.
    device.call(symbols()['ds100_v48_profile_bitmap'], 0, 0, 479, 39, stack_args=(source,), instruction_limit=10000000)
    assert len(device.replies) >= 5
    assert device.pixel_calls == []
    assert device.mu.mem_read(source, len(pixels)) == pixels
    assert struct.unpack('<I', device.mu.mem_read(pointer + 36, 4))[0] == 2
    assert struct.unpack('<I', device.mu.mem_read(pointer + 44, 4))[0] == 0
    writes = [value for address, _, value in device.lcd_writes if address == 0x60020000]
    assert struct.pack('<' + 'H' * len(writes), *writes) == pixels


def register_widget(device, *, vtable=0x08072840, context=None, callback=0x080271F7, period=500):
    widget = device.call(0x08010490, 0x240)
    assert 0x10000008 <= widget < 0x10010000 - 0x240
    data = bytearray(b'\xA5' * 0x240)
    struct.pack_into('<I', data, 0, vtable)
    struct.pack_into('<I', data, 0x1D4, period)
    struct.pack_into('<II', data, 0x1F0, widget if context is None else context, callback)
    device.mu.mem_write(widget, bytes(data))
    device.mu.reg_write(UC_ARM_REG_R4, widget)
    return widget, data


def test_r7_redraw_constructor_preserves_registers_and_stock_default_then_gates_acceleration():
    device = DrawArm()
    widget, original = register_widget(device)
    device.mu.reg_write(UC_ARM_REG_R12, 0x12345678)
    device.mu.reg_write(UC_ARM_REG_XPSR, 0xA1000000)
    assert device.call(symbols()['ds100_stream_redraw_constructor'], 17, 23, 31, 41) == 500
    assert [device.mu.reg_read(r) for r in (UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_R4, UC_ARM_REG_R12)] == [23, 31, 41, widget, 0x12345678]
    assert device.mu.reg_read(UC_ARM_REG_XPSR) & 0xF8000000 == 0xA0000000
    assert device.mu.mem_read(widget, len(original)) == original
    assert struct.unpack('<3I', device.command(6)) == (widget + 0x1D4, 500, 0)
    device.command(1)
    accelerated = bytearray(original)
    struct.pack_into('<I', accelerated, 0x1D4, 25)
    assert device.mu.mem_read(widget, len(original)) == accelerated
    assert struct.unpack('<3I', device.command(6)) == (widget + 0x1D4, 25, 500)
    device.command(0)
    assert device.mu.mem_read(widget, len(original)) == original
    assert struct.unpack('<3I', device.command(6)) == (widget + 0x1D4, 500, 0)


@pytest.mark.parametrize('invalid', [{'vtable': 0x08072844}, {'context': 0x1000FFFC}, {'callback': 0x080271FF}])
def test_r7_timer_registration_rejects_wrong_widget_identity(invalid):
    device = DrawArm()
    widget, original = register_widget(device, **invalid)
    device.call(symbols()['ds100_stream_redraw_constructor'])
    assert struct.unpack('<3I', device.command(6)) == (0, 0, 0)
    device.command(1)
    device.command(0)
    assert device.mu.mem_read(widget, len(original)) == original


def test_r7_timer_guard_rejects_unrelated_pointer_and_changed_period():
    device = DrawArm()
    device.mu.reg_write(UC_ARM_REG_R4, 0x60020000)
    device.call(symbols()['ds100_stream_redraw_constructor'])
    assert device.lcd_writes == []
    assert struct.unpack('<3I', device.command(6)) == (0, 0, 0)
    widget, original = register_widget(device, period=400)
    device.call(symbols()['ds100_stream_redraw_constructor'])
    device.command(1)
    assert struct.unpack('<3I', device.command(6)) == (widget + 0x1D4, 400, 0)
    device.command(0)
    assert device.mu.mem_read(widget, len(original)) == original


def test_r7_lease_restores_redraw_period_and_heap_without_overwriting_native_changes():
    device = DrawArm()
    widget, original = register_widget(device)
    device.call(symbols()['ds100_stream_redraw_constructor'])
    free_before = device.call(0x08010514)
    device.command(1)
    device.ticks = 1000
    device.call(symbols()['ds100_stream_tick'])
    assert device.mu.mem_read(widget, len(original)) == original
    assert device.call(0x08010514) == free_before
    device.command(1)
    device.mu.mem_write(widget + 0x1D4, struct.pack('<I', 40))
    device.command(0)
    assert struct.unpack('<I', device.mu.mem_read(widget + 0x1D4, 4))[0] == 40


def test_r7_restoration_does_not_touch_reused_widget_memory():
    device = DrawArm()
    widget, _ = register_widget(device)
    device.call(symbols()['ds100_stream_redraw_constructor'])
    device.command(1)
    device.mu.mem_write(widget, b'\xEF\xBE\xAD\xDE')
    device.command(0)
    assert struct.unpack('<I', device.mu.mem_read(widget + 0x1D4, 4))[0] == 25


@pytest.mark.parametrize('pointer', [0x60020000, 0xFFFFFFFC])
def test_r7_rejects_allocator_result_outside_bounded_heap_without_dereference(pointer):
    device = DrawArm()
    def unexpected_allocator(mu, address, size, user):
        if address == 0x08010490:
            mu.reg_write(UC_ARM_REG_R0, pointer)
            device.return_from_stub()
        elif address == 0x080103F6:
            raise AssertionError('Do not free an invalid allocator result.')
    device.mu.hook_add(UC_HOOK_CODE, unexpected_allocator)
    with pytest.raises(ValueError, match='heap has no room'):
        device.command(1)
    assert device.call(symbols()['ds100_stream_queue']) == 0
