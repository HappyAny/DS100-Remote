"""Run the stock E timer and modifier handlers from the installed R9 image."""

import struct
from unicorn import UC_HOOK_CODE
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1
from test_arm_bounded_follow import BoundedArm
from test_arm_candidate import unicorn_windows_exception_handler


class NativeGestures(BoundedArm):
    widget, controller, event = 0x20031000, 0x20030000, 0x20033000

    def __init__(self):
        super().__init__()
        self.milliseconds = 0
        self.timers = {}
        self.actions = []
        self.mu.mem_write(self.controller, struct.pack('<I4B', 0x080729C0, 1, 1, 1, 1))
        self.mu.mem_write(self.widget + 0x211, b'\x01')  # E timer stopped
        self.mu.hook_add(UC_HOOK_CODE, self.native_boundary)

    def native_boundary(self, mu, address, size, user):
        if address == 0x08014968:
            mu.reg_write(UC_ARM_REG_R0, self.controller)
        elif address in (0x0801544C, 0x080154CA):
            timer = mu.reg_read(UC_ARM_REG_R0)
            start = address == 0x0801544C
            mu.mem_write(timer + 0x19, bytes([0 if start else 1]))
            if start:
                self.timers[timer] = self.milliseconds + 400
            else:
                self.timers.pop(timer, None)
        elif address in (0x08022D82, 0x0802283C, 0x08022852, 0x08022EC6, 0x08022F28):
            self.actions.append(address)
        else:
            return
        self.return_from_stub()

    def key(self, down):
        self.mu.mem_write(self.controller + 7, bytes([0 if down else 1]))
        self.mu.mem_write(self.event, bytes([3, 0 if down else 1]))
        self.call(0x080292CE, self.widget, self.event)

    def wheel(self):
        self.mu.mem_write(self.event, struct.pack('<B3xI', 0, 1))
        self.call(0x080295A4, self.widget, self.event)

    def advance(self, milliseconds):
        self.milliseconds += milliseconds
        for timer, deadline in list(self.timers.items()):
            if deadline <= self.milliseconds:
                self.timers.pop(timer)
                self.mu.mem_write(timer + 0x19, b'\x01')
                if timer == self.widget + 0x1F8:
                    self.call(0x080271D2, self.widget)


def test_stock_short_E_combo_schedules_center_even_after_wheel_was_handled():
    d = NativeGestures()
    d.key(True)
    d.advance(80)
    d.wheel()
    assert d.mu.mem_read(d.widget + 0x155, 1) == b'\x01'
    d.key(False)
    d.advance(400)
    assert d.mu.mem_read(d.widget + 0x22A, 1) == b'\x01'


def test_stock_E_combo_expires_timer_while_held_without_center_or_channel_switch():
    d = NativeGestures()
    for _ in range(3):
        d.key(True)
        d.advance(80)
        d.wheel()
        d.advance(420)
        d.key(False)
        d.advance(80)
    assert d.actions == [0x0802283C] * 3
    assert d.mu.mem_read(d.widget + 0x22A, 1) == b'\x00'


def test_stock_two_short_E_presses_switch_channel_inside_400ms():
    d = NativeGestures()
    d.key(True)
    d.advance(80)
    d.wheel()
    d.key(False)
    d.advance(100)
    d.key(True)
    assert 0x08022D82 in d.actions


def test_stock_busy_widget_discards_an_already_read_wheel():
    d = NativeGestures()
    d.key(True)
    d.mu.mem_write(d.widget+0xE8, b'\x02')
    d.wheel()
    assert d.actions == []
    assert d.mu.mem_read(d.widget+0x155,1) == b'\x00'
