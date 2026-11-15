"""Compact named controls. Favorites select operations, not keyboard keys."""

import json
import os
from pathlib import Path
from PySide6.QtCore import Qt, Signal, QStandardPaths
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QGridLayout,
                             QHBoxLayout, QLabel, QMessageBox, QPushButton,
                             QToolButton, QVBoxLayout, QWidget)
from .quick_actions import ACTIONS, DEFAULT_FAVORITES, MOTION_ACTIONS


def preferences_path():
    override = os.environ.get('DS100_QUICK_SETTINGS')
    if override:
        return Path(override)
    return Path(QStandardPaths.writableLocation(QStandardPaths.GenericConfigLocation)) / 'DS100-Remote' / 'quick-actions.json'


class QuickPanel(QWidget):
    requested = Signal(str)
    motion_started = Signal(str)
    motion_stopped = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('compact-controls')
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.available = False
        self.busy = False
        self.active_motion_button = None
        self.buttons = {}
        self.favorite_ids = list(DEFAULT_FAVORITES)
        try:
            saved = json.loads(preferences_path().read_text(encoding='utf-8'))
            ids = saved.get('favorites')
            if (isinstance(ids, list) and 1 <= len(ids) <= 4 and
                    all(isinstance(value, str) and value in ACTIONS for value in ids) and len(set(ids)) == len(ids)):
                self.favorite_ids = ids
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        column = QVBoxLayout(self)
        column.setContentsMargins(10, 7, 10, 7)
        column.setSpacing(7)
        favorites = QHBoxLayout()
        favorites.setSpacing(6)
        for index in range(4):
            button = QPushButton()
            button.setProperty('role', 'quick-action')
            button.clicked.connect(lambda checked=False, index=index: self.favorite_clicked(index))
            button.pressed.connect(lambda index=index, button=button: self.favorite_pressed(index, button))
            button.released.connect(lambda button=button: self.release_motion(button))
            self.buttons['favorite_' + str(index)] = button
            favorites.addWidget(button, 1)
        customize = QToolButton()
        customize.setText('⋯')
        customize.setToolTip('选择常用操作')
        customize.setAccessibleName('自定义常用按钮')
        customize.clicked.connect(self.customize)
        favorites.addWidget(customize)
        column.addLayout(favorites)
        steps = QHBoxLayout()
        steps.setSpacing(10)
        for caption, negative, positive, symbols in (
                ('时基', 'time_down', 'time_up', ('−', '+')),
                ('量程', 'range_down', 'range_up', ('−', '+')),
                ('水平', 'left', 'right', ('←', '→')),
                ('垂直', 'down', 'up', ('↓', '↑'))):
            row = QHBoxLayout()
            row.setSpacing(4)
            text = QLabel(caption)
            text.setProperty('role', 'muted')
            row.addWidget(text)
            for action_id, symbol in zip((negative, positive), symbols):
                button = QPushButton(symbol)
                button.setProperty('role', 'quick-step')
                button.setToolTip(ACTIONS[action_id].hint)
                button.setAccessibleName(ACTIONS[action_id].title)
                if action_id in MOTION_ACTIONS:
                    button.pressed.connect(lambda action_id=action_id, button=button: self.press_motion(action_id, button))
                    button.released.connect(lambda button=button: self.release_motion(button))
                else:
                    button.clicked.connect(lambda checked=False, action_id=action_id: self.requested.emit(action_id))
                self.buttons[action_id] = button
                row.addWidget(button, 1)
            steps.addLayout(row, 1)
        column.addLayout(steps)
        self.refresh_favorites()
        self.update_enabled()

    def refresh_favorites(self):
        for index in range(4):
            button = self.buttons['favorite_' + str(index)]
            visible = index < len(self.favorite_ids)
            button.setVisible(visible)
            if visible:
                action = ACTIONS[self.favorite_ids[index]]
                button.setText(action.title)
                button.setToolTip(action.hint)
                button.setAccessibleName(action.title)

    def set_available(self, available, busy=False):
        self.available, self.busy = bool(available), bool(busy)
        self.update_enabled()

    def update_enabled(self):
        if not self.available and self.active_motion_button is not None:
            self.motion_stopped.emit()
        for button in self.buttons.values():
            button.setEnabled(self.available)
        if not self.busy:
            self.active_motion_button = None

    def press_motion(self, action_id, button):
        if not self.available:
            return
        self.active_motion_button = button
        self.motion_started.emit(action_id)

    def release_motion(self, button):
        if button is self.active_motion_button:
            self.motion_stopped.emit()

    def favorite_pressed(self, index, button):
        action_id = self.favorite_ids[index]
        if action_id in MOTION_ACTIONS:
            self.press_motion(action_id, button)

    def favorite_clicked(self, index):
        action_id = self.favorite_ids[index]
        if action_id not in MOTION_ACTIONS:
            self.requested.emit(action_id)

    def save_favorites(self, ids):
        if not 1 <= len(ids) <= 4 or len(set(ids)) != len(ids) or any(value not in ACTIONS for value in ids):
            raise ValueError('Select one to four different quick actions.')
        path = preferences_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps({'favorites': ids}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        temporary.replace(path)
        self.favorite_ids = list(ids)
        self.refresh_favorites()

    def customize(self):
        dialog = QDialog(self)
        dialog.setWindowTitle('常用按钮')
        column = QVBoxLayout(dialog)
        column.addWidget(QLabel('选择 1–4 项放在第一行；下方调节按钮始终保留。'))
        grid = QGridLayout()
        checks = {}
        for index, (action_id, action) in enumerate(ACTIONS.items()):
            check = QCheckBox(action.title)
            check.setToolTip(action.hint)
            check.setChecked(action_id in self.favorite_ids)
            checks[action_id] = check
            grid.addWidget(check, index // 2, index % 2)
        column.addLayout(grid)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        column.addWidget(buttons)
        buttons.rejected.connect(dialog.reject)

        def save():
            # Keep the order of existing favorites, append new selections.
            selected = [value for value in self.favorite_ids if checks[value].isChecked()]
            selected += [value for value in ACTIONS if value not in selected and checks[value].isChecked()]
            try:
                self.save_favorites(selected)
            except (OSError, ValueError) as error:
                QMessageBox.information(dialog, '未保存', '请选择 1–4 项。' if isinstance(error, ValueError) else str(error))
                return
            dialog.accept()

        buttons.accepted.connect(save)
        dialog.exec()
