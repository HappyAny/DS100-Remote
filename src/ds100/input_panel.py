"""Virtual DS100 keys and wheels. Emit snapshots for one serialized USB worker."""

import time
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QWidget, QPushButton, QLabel, QCheckBox, QHBoxLayout,
                             QVBoxLayout)
from .workspace_widgets import KeyButton, RotaryDial

KEYS = {'P': 1, 'A': 2, 'M': 4, 'E': 8}


class InputPanel(QWidget):
    changed = Signal(int, int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.available = False
        self.sources = {}
        self.session = 0
        self.click_number = 0
        self.key_buttons = {}
        self.holds = {}
        self.dials = []
        self.step_buttons = []
        self.actions = []
        self.double_buttons = {}
        self.double_handler = None
        self.setObjectName('compact-controls')
        self.setAttribute(Qt.WA_StyledBackground, True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 5)
        layout.setSpacing(12)
        keys = QHBoxLayout()
        keys.setSpacing(5)
        for key, description in (('P', '运行'), ('A', '自动'), ('M', '菜单'), ('E', '退出')):
            button = KeyButton(key, description, compact=True)
            button.pressed.connect(lambda key=key: self.press(key, 'mouse'))
            button.released.connect(lambda key=key: self.release(key, 'mouse'))
            self.key_buttons[key] = button
            self.actions.append(button)
            key_column = QVBoxLayout()
            key_column.setSpacing(5)
            key_column.addWidget(button)
            below = QHBoxLayout()
            below.setSpacing(4)
            hold = QCheckBox('按住')
            hold.setAccessibleName('按住 ' + key)
            hold.setToolTip('保持 P 会按原机规则关机。再次点击松开。' if key == 'P' else
                            '保持 ' + key + '；再次点击松开。M / E 可与拨轮组合。')
            hold.toggled.connect(lambda down, key=key: self.press(key, 'hold') if down else self.release(key, 'hold'))
            self.holds[key] = hold
            self.actions.append(hold)
            below.addWidget(hold)
            double = QPushButton('双击')
            double.setProperty('role', 'small')
            double.setAccessibleName('双击 ' + key)
            double.setToolTip('双击 E · 切换通道' if key == 'E' else '发送两次 ' + key + ' 短按，含义由原机决定。')
            double.clicked.connect(lambda checked=False, key=key: self.double_key(key))
            self.double_buttons[key] = double
            self.actions.append(double)
            below.addWidget(double)
            key_column.addLayout(below)
            keys.addLayout(key_column, 1)
        layout.addLayout(keys, 1)
        layout.addSpacing(2)
        for index in range(2):
            column = QVBoxLayout()
            column.setSpacing(0)
            row = QHBoxLayout()
            row.setSpacing(3)
            pad = RotaryDial(('Ⅰ', 'Ⅱ')[index], size=64)
            pad.turned.connect(lambda delta, index=index: self.rotate(index, delta))
            self.dials.append(pad)
            self.actions.append(pad)
            minus, plus = QPushButton('−'), QPushButton('+')
            for button, delta in ((minus, -1), (plus, 1)):
                button.setProperty('role', 'compact-step')
                button.setAccessibleName(f'拨轮 {index + 1} ' + ('减少一步' if delta < 0 else '增加一步'))
                button.setAutoRepeat(True)
                button.setAutoRepeatDelay(250)
                button.setAutoRepeatInterval(60)
                button.clicked.connect(lambda checked=False, pad=pad, delta=delta: pad.turn(delta))
                self.actions.append(button)
            row.addWidget(minus)
            row.addWidget(pad)
            row.addWidget(plus)
            self.step_buttons.append((minus, plus))
            column.addLayout(row)
            layout.addLayout(column)
        self.availability = QLabel('未启用', self)
        self.note = QLabel('连接支持控制的固件后启用。', self)
        self.availability.hide()
        self.note.hide()
        for action in self.actions:
            action.setEnabled(False)
        self.update_wheel_direction()

    @property
    def mask(self):
        mask = 0
        for key, _ in self.sources:
            mask |= KEYS[key]
        return mask

    def set_available(self, available):
        available = bool(available)
        if self.available == available:
            return
        self.available = available
        if not available:
            self.session += 1
            self.sources.clear()
            for checkbox in self.holds.values():
                checkbox.blockSignals(True)
                checkbox.setChecked(False)
                checkbox.blockSignals(False)
        for action in self.actions:
            action.setEnabled(available)
        self.update_wheel_direction()
        self.availability.setText('可操作' if available else '未启用')
        self.note.setText('按住 M / E 后转动拨轮可组合操作。P 长按关机，开机需按实机 P。' if available else
                          '当前连接未提供按键和拨轮服务。连接支持控制的固件后启用。')

    def press(self, key, source):
        if not self.available:
            return
        before = self.mask
        self.sources[(key, source)] = time.monotonic()
        self.update_wheel_direction()
        if self.mask != before:
            self.changed.emit(self.mask, 0, 0)

    def release(self, key, source):
        identity = (key, source)
        started = self.sources.get(identity)
        if started is None:
            return
        remaining = 0.08 - (time.monotonic() - started)
        session = self.session

        def finish():
            if session != self.session or self.sources.get(identity) != started:
                return
            before = self.mask
            self.sources.pop(identity, None)
            self.update_wheel_direction()
            if self.mask != before:
                self.changed.emit(self.mask, 0, 0)

        if remaining > 0:
            QTimer.singleShot(int(remaining * 1000) + 1, self, finish)
        else:
            finish()

    def tap(self, key, duration=80):
        self.click_number += 1
        source = 'tap-' + str(self.click_number)
        session = self.session
        self.press(key, source)
        QTimer.singleShot(duration, self, lambda: self.release(key, source) if session == self.session else None)

    def double_key(self, key):
        if not self.available:
            return
        if self.double_handler is not None:
            self.double_handler(key)
            return
        session = self.session
        self.tap(key)
        QTimer.singleShot(180, self, lambda: self.tap(key) if session == self.session else None)

    def double_e(self):
        self.double_key('E')

    def rotate(self, index, delta):
        if self.available:
            if index == 0 and not self.mask & KEYS['E']:
                delta = -delta
            self.changed.emit(self.mask, delta if index == 0 else 0, delta if index == 1 else 0)

    def update_wheel_direction(self):
        if not self.dials:
            return
        sign = 1 if self.mask & KEYS['E'] else -1
        dial = self.dials[0]
        if dial.wheel_sign != sign:
            dial._wheel = 0
            dial.wheel_sign = sign
        dial.setToolTip('顺时针 / ＋：下一项；滚轮向下：下一项。按住 M 水平平移，按住 E 调时基。')
        if self.mask & KEYS['E']:
            labels = (('−', '+'), ('−', '+'))
            hints = ('时基：顺时针增大秒/格，逆时针减小。', '量程：顺时针增大伏/格，逆时针减小。')
        elif self.mask & KEYS['M']:
            labels = (('←', '→'), ('↓', '↑'))
            hints = ('水平平移：顺时针向右，逆时针向左。', '垂直平移：顺时针向上，逆时针向下。')
        else:
            labels = (('↑', '↓'), ('−', '+'))
            hints = ('选择：顺时针下一项，逆时针上一项；鼠标滚轮向上选择上一项。',
                     '调整选中项目：顺时针增加，逆时针减少。')
        for buttons, symbols, pad, hint in zip(self.step_buttons, labels, self.dials, hints):
            pad.setToolTip(hint + ' 两侧按钮支持长按连续调节。')
            for button, symbol in zip(buttons, symbols):
                button.setText(symbol)
                button.setToolTip(hint + ' 按住可连续调节。')
