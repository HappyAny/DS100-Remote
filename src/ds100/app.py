"""DS100 desktop application."""

from pathlib import Path
from datetime import datetime
import time
import os
import sys
import numpy as np
from collections import deque
from threading import Event, Lock
from PySide6.QtCore import Qt, QThread, Signal, QTimer, QPointF, QRectF, QStandardPaths, QUrl, QEvent
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QDesktopServices
from PySide6.QtWidgets import QApplication, QFileDialog, QLabel, QMainWindow, QSizePolicy, QWidget
from .protocol import Client, ProtocolError
from .simulator import SimulatorTransport
from .storage import find_drives, list_files, load_csv, read_cfg
from .fonts import configure_fonts
from .theme import STYLESHEET, ACCENT, MUTED, draw_symbol, icon
from .quick_actions import ACTIONS, execute_action, execute_motion


class Task(QThread):
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, function, parent=None):
        super().__init__(parent)
        self.function = function

    def run(self):
        try:
            self.succeeded.emit(self.function())
        except Exception as error:
            self.failed.emit(str(error))


class WavePlot(QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumSize(480, 280)
        self.x = np.array([])
        self.channels = {}
        self.xlabel = "时间 / s"
        self.ylabel = "电压 / V"
        self.zoom = 1.0

    def set_data(self, x, channels, xlabel="时间 / s", ylabel="电压 / V"):
        self.x, self.channels = np.asarray(x), channels
        self.xlabel, self.ylabel = xlabel, ylabel
        self.zoom = 1.0
        self.update()

    def wheelEvent(self, event):
        self.zoom = min(128.0, max(1.0, self.zoom * (1.4 if event.angleDelta().y() > 0 else 1 / 1.4)))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#0c111a"))
        left, top, width, height = 66, 28, max(1, self.width() - 92), max(1, self.height() - 88)
        painter.setPen(QColor("#263244"))
        for index in range(11):
            x = left + width * index / 10
            painter.drawLine(QPointF(x, top), QPointF(x, top + height))
        for index in range(9):
            y = top + height * index / 8
            painter.drawLine(QPointF(left, y), QPointF(left + width, y))
        painter.setPen(QColor("#a6b3c8"))
        painter.drawText(left, self.height() - 12, self.xlabel + "   ·   滚轮缩放")
        painter.drawText(12, 18, self.ylabel)
        if not len(self.x):
            painter.drawText(left + 20, top + 36, "读取波形，或打开设备导出的 CSV 文件")
            return
        count = max(2, int(len(self.x) / self.zoom))
        start = max(0, (len(self.x) - count) // 2)
        end = min(len(self.x), start + count)
        xs = self.x[start:end]
        if len(xs) < 2:
            return
        xmin, xmax = float(xs.min()), float(xs.max())
        span = max(xmax - xmin, 1e-15)
        values = [np.asarray(v)[start:end] for v in self.channels.values()]
        ymin = min(float(v.min()) for v in values)
        ymax = max(float(v.max()) for v in values)
        pad = max((ymax - ymin) * 0.1, 0.01)
        ymin, ymax = ymin - pad, ymax + pad
        for index in range(9):
            value = ymax - (ymax - ymin) * index / 8
            painter.drawText(5, int(top + height * index / 8 + 4), f"{value:.3g}")
        painter.drawText(left, int(top + height + 24), f"{xmin:.6g}")
        painter.drawText(int(left + width - 85), int(top + height + 24), f"{xmax:.6g}")
        colors = ["#ffd05b", "#65ddae", "#80b7ff", "#e591d9", "#ea9674", "#b6ca72", "#8ed0d2"]
        for series, ((channel, _), ys) in enumerate(zip(self.channels.items(), values)):
            # Each horizontal bucket keeps its minimum and maximum sample.
            if len(xs) > int(width) * 2:
                buckets = min(int(width), len(xs))
                edges = np.linspace(0, len(xs), buckets + 1, dtype=int)
                indices = []
                for lo, hi in zip(edges[:-1], edges[1:]):
                    part = ys[lo:hi]
                    indices.extend(sorted((lo + int(part.argmin()), lo + int(part.argmax()))))
                idx = np.asarray(indices)
            else:
                idx = np.arange(len(xs))
            path = QPainterPath()
            for position, sample in enumerate(idx):
                point = QPointF(left + (xs[sample] - xmin) / span * width,
                                top + (ymax - ys[sample]) / (ymax - ymin) * height)
                path.moveTo(point) if position == 0 else path.lineTo(point)
            painter.setPen(QPen(QColor(colors[series % len(colors)]), 1.4))
            painter.drawPath(path)
            painter.drawText(int(left + 12 + series * 170), int(top + 18), f"CH{channel + 1}  Vpp={np.ptp(ys):.4g}")


class ScreenLabel(QLabel):
    def __init__(self, text=''):
        super().__init__(text)
        self.image = None
        self.image_generation = 0
        self.painted_generation = 0
        self.painted_frames = 0
        self.painted_changed_frames = 0
        self.painted_waveform_frames = 0
        self.last_painted_image = None
        self.last_painted_roi = None
        self.roi = None
        self.native_size = False
        self.preview = False
        self.setMinimumSize(480, 320)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_image(self, image, pixels=None):
        self.preview = False
        self.image = image
        self.image_generation += 1
        self.roi = None if pixels is None else pixels[24:292, 16:410].tobytes()
        self.fit_image()

    def fit_image(self):
        self.update()

    def image_rect(self):
        if self.image is None:
            return QRectF()
        size = self.image.size()
        available = self.size()
        if not self.native_size or size.width() > available.width() or size.height() > available.height():
            size.scale(available, Qt.KeepAspectRatio)
        return QRectF((self.width() - size.width()) / 2, (self.height() - size.height()) / 2,
                      size.width(), size.height())

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor('#090e12'))
        if self.image is not None:
            p.setRenderHint(QPainter.SmoothPixmapTransform, False)
            p.drawImage(self.image_rect(), self.image)
            if not self.preview and self.painted_generation != self.image_generation:
                self.painted_frames += 1
                if self.last_painted_image is None or self.last_painted_image != self.image:
                    self.painted_changed_frames += 1
                if self.roi is not None and self.last_painted_roi != self.roi:
                    self.painted_waveform_frames += 1
                self.last_painted_roi = self.roi
                self.last_painted_image = self.image
                self.painted_generation = self.image_generation
            return
        p.setRenderHint(QPainter.Antialiasing)
        center = self.rect().center()
        draw_symbol(p, 'screen', QRectF(center.x() - 29, center.y() - 84, 58, 58), '#4d655c', 1.1)
        font = self.font()
        font.setPixelSize(19)
        p.setFont(font)
        p.setPen(QColor('#c1cdc6'))
        p.drawText(QRectF(0, center.y() - 7, self.width(), 36), Qt.AlignCenter, '等待示波器画面')
        font.setPixelSize(12)
        p.setFont(font)
        p.setPen(QColor(MUTED))
        p.drawText(QRectF(0, center.y() + 33, self.width(), 26), Qt.AlignCenter, '连接 DS100，或选择离线演示预览界面')
        p.drawText(QRectF(0, center.y() + 58, self.width(), 26), Qt.AlignCenter, '480 × 320 · 原始像素镜像')

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fit_image()


class MainWindow(QMainWindow):
    drawing_sync = Signal(bool)
    quick_frame = Signal(object, float)
    menu_preview = Signal(object)
    quick_completed = Signal(str, object)

    def __init__(self):
        super().__init__()
        configure_fonts()
        self.setWindowTitle("DS100 Remote")
        self.setWindowIcon(icon('wave', ACCENT, 64))
        self.resize(736, 552)
        self.setMinimumSize(640, 510)
        self.client = None
        self.worker = None
        self.info = {}
        self.drive = None
        self.last_image = None
        self.file_paths = []
        self.mirror_count = 0
        self.changed_mirror_count = 0
        self.waveform_change_count = 0
        self.last_screen_pixels = None
        self.presented_at = deque(maxlen=20)
        self.last_scan_seconds = None
        self.display_origin = 'empty'
        self.disconnect_when_idle = False
        self.action_pending = None
        self.action_running = None
        self.queued_actions = deque()
        self.pending_motion_stop = None
        self.action_lock = Lock()
        self.mirror_stop = Event()
        self.mirror_worker = None
        self.reconnect_when_idle = False
        self.status_before_sync = None
        self.motion_stop = None
        self.drawing_sync.connect(self.drawing_sync_changed)
        self.quick_frame.connect(self.show_pixels)
        self.menu_preview.connect(self.show_menu_preview)
        self.quick_completed.connect(self.quick_action_completed)
        from .compact_ui import build_layout
        build_layout(self, ScreenLabel, WavePlot, os.environ.get('DS100_AUTO_CONNECT', '1') != '0')
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(5)
        self.timer.timeout.connect(self.read_screen)
        self.input_timer = QTimer(self)
        self.input_timer.setInterval(150)
        self.input_timer.timeout.connect(self.pump_input)
        self.input_timer.start()
        self.discovery_timer = QTimer(self)
        self.discovery_timer.setInterval(1000)
        self.discovery_timer.timeout.connect(self.discover_device)
        self.discovery_timer.start()
        self.source.currentIndexChanged.connect(self.source_changed)
        self.setStyleSheet(STYLESHEET)
        self.update_controls()

    def page_changed(self, index):
        if index != 0:
            self.stop_motion()
        self.navigation[index].setChecked(True)
        self.page_title.setText(('DS100', '波形', '文件', '信息')[index])
        self.control_modes.setVisible(index == 0)

    def set_raw_controls(self, checked):
        self.stop_motion()
        if self.input_panel.mask:
            self.input_panel.set_available(False)
            self.queue_input(0, 0, 0)
        self.control_modes.setCurrentIndex(1 if checked else 0)
        for item in (self.quick_toggle, self.quick_mode_action):
            previous = item.blockSignals(True)
            item.setChecked(not checked)
            item.blockSignals(previous)
        self.update_controls()

    def set_native_size(self, checked):
        self.screen_label.native_size = checked
        self.screen_label.update()

    def update_controls(self):
        busy = self.worker is not None
        caps = self.info.get("capabilities", [])
        for widget, feature in ((self.screen_button, "screen"), (self.wave_button, "waveform"),
                                (self.run_button, "run"), (self.stop_button, "run")):
            widget.setEnabled(not busy and self.client is not None and feature in caps)
        self.mirror.setEnabled(self.client is not None and "screen" in caps)
        self.connect_button.setEnabled(not busy or self.worker is self.mirror_worker)
        self.source.setEnabled(not busy or self.worker is self.mirror_worker)
        self.save_button.setEnabled(self.last_image is not None)
        session = self.client.draw_session if self.client is not None else None
        syncing = self.client is not None and self.client.draw_config is not None and self.client.screen_mode not in ('menu-readback', 'input-recovery') and (
            session is None or session.replay is None or not session.replay.ready)
        input_ready = self.client is not None and 'input' in caps and self.client.input_supported
        quick_busy = self.action_pending is not None or self.action_running is not None
        self.input_panel.set_available(input_ready and not quick_busy)
        self.quick_panel.set_available(input_ready, quick_busy)
        if syncing:
            self.input_panel.note.setText('正在同步完整画面，完成后启用按键和拨轮。')
        demo = self.info.get('source') == 'simulator'
        connected = self.client is not None or self.drive is not None
        self.connection_badge.setText('离线演示' if demo else '已连接' if connected else '未连接')
        self.connection_badge.setProperty('tone', 'demo' if demo else 'live' if connected else 'offline')
        self.connection_badge.style().unpolish(self.connection_badge)
        self.connection_badge.style().polish(self.connection_badge)
        self.device_name.setText('DS100 · 演示设备' if demo else 'DS100')
        self.device_detail.setText('生成的测试数据 · 不连接实机' if demo else
                                   str(self.info.get('build', '实验固件')) + ' · ' +
                                   ('读屏与按键' if 'input' in caps else '只读屏幕') if self.client else
                                   f'USB 文件 · {self.drive}' if self.drive else '等待设备连接')
        self.connect_button.setText('重连' if connected else '连接')
        if not connected:
            self.rate_label.clear()
            if self.display_origin in ('device', 'demo'):
                self.origin_badge.setText('演示数据 · 已断开' if self.display_origin == 'demo' else '上次画面 · 未连接')
                self.origin_badge.setProperty('tone', 'demo' if self.display_origin == 'demo' else 'offline')
                self.origin_badge.style().unpolish(self.origin_badge)
                self.origin_badge.style().polish(self.origin_badge)

    def discover_device(self):
        if (self.auto_connect.isChecked() and self.source.currentIndex() == 1 and
                self.client is None and self.worker is None and find_drives()):
            self.connect_now()

    def request_quick_action(self, action_id, continuous=False):
        if action_id not in ACTIONS or not self.quick_panel.available:
            return
        if self.input_panel.mask:
            self.status.setText('请先松开原机按键面板中保持的按键。')
            return
        motion_stop = Event() if continuous else None
        if self.motion_stop is not None:
            self.motion_stop.set()
        self.motion_stop = motion_stop
        with self.action_lock:
            if self.action_pending is not None or self.action_running is not None:
                if len(self.queued_actions) < 8:
                    self.queued_actions.append((action_id, motion_stop))
                    self.status.setText('已排队 · ' + ACTIONS[action_id].title)
                return
            self.action_pending = action_id
            self.pending_motion_stop = motion_stop
        self.status.setText('正在执行 · ' + ACTIONS[action_id].title)
        self.update_controls()
        self.start_quick_action()

    def start_quick_action(self):
        if self.action_pending is None or self.worker is not None or self.client is None:
            return
        action = self.take_quick_action()
        client = self.client
        self.start_task(lambda: self.perform_quick_action(client, action), lambda _: None)

    def take_quick_action(self):
        with self.action_lock:
            if self.action_pending is None or self.action_running is not None:
                return None
            action = self.action_pending, self.pending_motion_stop
            self.action_pending, self.pending_motion_stop = None, None
            self.action_running = action[0]
            return action

    def perform_quick_action(self, client, action):
        action_id, motion_stop = action
        options = dict(cancelled=lambda: QThread.currentThread().isInterruptionRequested(),
                       on_frame=self.quick_frame.emit)
        if motion_stop is not None:
            execute_motion(client, action_id, motion_stop, **options)
        else:
            execute_action(client, action_id, **options)
        with self.action_lock:
            self.action_running = None
            if self.queued_actions:
                self.action_pending, self.pending_motion_stop = self.queued_actions.popleft()
        self.quick_completed.emit(action_id, motion_stop)
        return action_id

    def quick_action_completed(self, action_id, motion_stop):
        if self.motion_stop is motion_stop:
            self.motion_stop = None
        self.status.setText('已执行 · ' + ACTIONS[action_id].title)
        self.update_controls()

    def stop_motion(self, reason='release'):
        if self.motion_stop is not None:
            self.last_motion_stop_reason = str(reason)
            self.motion_stop.set()

    def event(self, event):
        if event.type() == QEvent.WindowDeactivate and hasattr(self, 'motion_stop'):
            self.stop_motion('focus-loss')
        return super().event(event)

    def queue_input(self, keys, wheel1, wheel2):
        if self.client is None or not self.client.input_supported:
            return
        try:
            if keys & 4:
                self.status.setText('正在执行 M 操作…')
            self.client.queue_input(keys, wheel1, wheel2)
            self.pump_input()
        except ProtocolError as error:
            self.task_failed(str(error))

    def pump_input(self):
        if self.client is None or not self.client.input_supported:
            return
        if self.input_panel.mask and not self.mirror.isChecked() and not self.client.has_pending_input():
            self.client.queue_input(self.input_panel.mask)
        if self.worker is None and self.client.has_pending_input():
            client = self.client
            self.start_task(client.flush_inputs, lambda _: None)

    def start_task(self, function, success):
        if self.worker is not None:
            return
        task = Task(function, self)
        self.worker = task
        task.succeeded.connect(success)
        task.failed.connect(self.task_failed)
        task.finished.connect(self.task_finished)
        self.update_controls()
        task.start()

    def task_finished(self):
        task = self.worker
        self.worker = None
        if task is self.mirror_worker:
            self.mirror_worker = None
        if task is not None:
            task.deleteLater()
        if self.disconnect_when_idle:
            self.disconnect_when_idle = False
            self.disconnect()
        if self.reconnect_when_idle:
            self.reconnect_when_idle = False
            self.connect_now()
            return
        self.update_controls()
        if self.action_pending is None and self.queued_actions:
            self.action_pending, self.pending_motion_stop = self.queued_actions.popleft()
        if self.action_pending is not None and self.client is not None:
            self.start_quick_action()
            return
        if self.mirror.isChecked() and self.client is not None:
            self.timer.start(0 if self.client.draw_config is not None else 5)

    def task_failed(self, message):
        self.stop_motion()
        self.action_pending = self.action_running = None
        self.queued_actions.clear()
        self.quick_panel.set_available(False)
        self.mirror.setChecked(False)
        self.status.setText("操作失败：" + message)
        self.disconnect_when_idle = True
        self.input_panel.set_available(False)

    def disconnect(self):
        self.stop_motion()
        self.action_pending = self.action_running = None
        self.queued_actions.clear()
        self.quick_panel.set_available(False)
        self.mirror.setChecked(False)
        self.input_panel.set_available(False)
        if self.client:
            if self.client.input_supported:
                try:
                    self.client.queue_input(0)
                    self.client.flush_inputs()
                except (OSError, ProtocolError):
                    pass
            self.client.close()
        self.client, self.info, self.drive = None, {}, None
        self.files.clear()
        self.file_paths = []

    def source_changed(self):
        if self.worker is not None and self.worker is self.mirror_worker:
            self.disconnect_when_idle = True
            self.mirror.setChecked(False)
            return
        if self.worker is None:
            self.disconnect()
            self.status.setText("已切换来源 · 点击连接")
            self.update_controls()

    def connect_now(self):
        if self.worker is not None:
            if self.worker is self.mirror_worker:
                self.reconnect_when_idle = True
                self.mirror.setChecked(False)
            return
        self.disconnect()
        mode = self.source.currentIndex()
        if mode == 2:
            self.client = Client(SimulatorTransport())
            self.info = self.client.info()
            self.status.setText('离线演示 · 所有画面和操作均为测试数据')
            self.tabs.setCurrentIndex(0)
            self.update_controls()
            self.read_screen()
            return
        drives = find_drives()
        if not drives:
            self.status.setText("没有找到 ATK-DSO 磁盘。请检查电量和数据线，确认示波器已开机。")
            self.update_controls()
            return
        self.drive = drives[0]
        if mode == 0:
            self.file_paths = list_files(self.drive)
            self.files.addItems([f"{path.name}    {path.stat().st_size:,} 字节" for path in self.file_paths])
            try:
                cfg = read_cfg(self.drive)
                self.cfg_note.setText(f"CFG：{cfg['size']} 字节\nSHA-256：{cfg['sha256']}")
            except (OSError, ValueError) as error:
                self.cfg_note.setText(str(error))
            self.status.setText(f"已连接原厂存储模式 · {self.drive} · {len(self.file_paths)} 个波形／截图文件。实时功能需要固件服务。")
            self.tabs.setCurrentIndex(2)
            self.update_controls()
        else:
            drive = self.drive
            def connect_experimental():
                from .windows_scsi import ScsiTransport
                client = Client(ScsiTransport(drive))
                try:
                    return client, client.info()
                except Exception:
                    client.close()
                    raise
            self.start_task(connect_experimental, self.experimental_connected)

    def experimental_connected(self, result):
        self.client, self.info = result
        if self.info.get('source') == 'simulator':
            self.status.setText('离线演示 · 生成的测试数据，不代表实机画面和性能')
        else:
            self.status.setText('已连接 · 跟随示波器刷新')
        self.update_controls()
        if self.auto_connect.isChecked() and 'screen' in self.info.get('capabilities', []):
            self.mirror.setChecked(True)

    def toggle_mirror(self, checked):
        self.mirror.setText('暂停' if checked else '镜像')
        self.mirror.setIcon(icon('pause' if checked else 'play', ACCENT))
        self.presented_at.clear()
        if checked:
            self.tabs.setCurrentIndex(0)
            self.read_screen()
        else:
            self.mirror_stop.set()
            self.timer.stop()

    def drawing_sync_changed(self, active):
        if active:
            self.screen_note.setText('正在同步完整画面…')
            self.status_before_sync = self.status.text()
            self.status.setText('正在更新完整画面…')
        elif self.status.text() == '正在更新完整画面…':
            self.status.setText('画面已更新' if self.status_before_sync == '正在执行 M 操作…' else
                                self.status_before_sync or '已连接 · 跟随示波器刷新')
        self.update_controls()

    def read_screen(self):
        if self.worker is not None or self.client is None:
            return
        started = time.monotonic()
        self.client.on_draw_sync = self.drawing_sync.emit
        self.client.on_menu_preview = self.menu_preview.emit
        self.client.on_input_frame = self.quick_frame.emit
        if self.mirror.isChecked():
            self.mirror_stop.clear()
            client = self.client
            def mirror_loop():
                cancelled = lambda: QThread.currentThread().isInterruptionRequested()
                while not self.mirror_stop.is_set() and not cancelled():
                    action = self.take_quick_action()
                    if action is not None:
                        self.perform_quick_action(client, action)
                        continue
                    begin = time.monotonic()
                    pixels = client.screen(cancelled)
                    if pixels is not None:
                        self.quick_frame.emit(pixels, time.monotonic()-begin)
                    if client.draw_config is None:
                        self.mirror_stop.wait(.005)
            self.start_task(mirror_loop, lambda _: None)
            self.mirror_worker = self.worker
            self.update_controls()
            return
        self.start_task(lambda: self.client.screen(cancelled=lambda: QThread.currentThread().isInterruptionRequested()),
                        lambda pixels: self.show_pixels(pixels, time.monotonic() - started))

    def show_menu_preview(self, pixels):
        image = QImage(pixels.tobytes(), 480, 320, 960, QImage.Format_RGB16).copy()
        self.screen_label.preview = True
        self.screen_label.image = image
        self.screen_label.update()
        self.screen_note.setText('按键保持中 · 持续读屏，右侧设置优先更新')

    def show_pixels(self, pixels, elapsed):
        if pixels is None:
            return
        image = QImage(pixels.tobytes(), 480, 320, 960, QImage.Format_RGB16).copy()
        if self.last_image is None or self.last_image != image:
            self.changed_mirror_count += 1
        if self.last_screen_pixels is None or not np.array_equal(
                self.last_screen_pixels[24:292, 16:410], pixels[24:292, 16:410]):
            self.waveform_change_count += 1
        self.last_screen_pixels = pixels.copy()
        self.last_image = image
        self.mirror_count += 1
        self.last_scan_seconds = elapsed
        self.screen_label.set_image(image, pixels)
        demo = self.info.get('source') == 'simulator'
        self.display_origin = 'demo' if demo else 'device'
        origin = '模拟器测试画面' if demo else ('实机绘图重放' if self.client is not None and
                  self.client.screen_mode == 'draw-stream' else '示波器原始像素')
        self.screen_note.setText(f"{origin} · 480 × 320 · 第 {self.mirror_count} 帧 · 扫描 {elapsed * 1000:.0f} ms")
        self.screen_note.setToolTip(self.screen_note.text())
        self.origin_badge.setText('演示数据 · 非实机' if demo else '实机镜像')
        self.origin_badge.setProperty('tone', 'demo' if demo else 'live')
        self.origin_badge.style().unpolish(self.origin_badge)
        self.origin_badge.style().polish(self.origin_badge)
        self.metrics['resolution'].setText('480 × 320')
        self.metrics['frames'].setText(f'{self.mirror_count:,}')
        self.metrics['scan'].setText(f'{elapsed * 1000:.0f} ms')
        if self.mirror.isChecked():
            self.presented_at.append(time.monotonic())
        if len(self.presented_at) >= 2:
            duration = self.presented_at[-1] - self.presented_at[0]
            self.metrics['cadence'].setText(f'{(len(self.presented_at) - 1) / duration:.1f} fps' if duration > 0 else '—')
        else:
            self.metrics['cadence'].setText('—')
        self.rate_label.setText(('演示 · ' if demo else '') + self.metrics['cadence'].text())
        self.save_button.setEnabled(True)

    def read_wave(self):
        if self.client is not None and self.worker is None:
            self.start_task(self.client.waveform, self.show_wave)

    def show_wave(self, result):
        x, channels = result
        self.plot.set_data(x, channels)
        self.tabs.setCurrentIndex(1)

    def set_run(self, running):
        if self.client is not None and self.worker is None:
            self.start_task(lambda: self.client.set_run(running), lambda _: self.status.setText(
                ("模拟器" if self.info.get("source") == "simulator" else "实验固件") + ("：运行" if running else "：停止")))

    def choose_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "打开波形或截图", str(self.drive or Path.cwd()), "DS100 文件 (*.csv *.bmp);;CSV (*.csv);;BMP (*.bmp)")
        if path:
            self.open_file(Path(path))

    def open_file(self, path):
        try:
            self.mirror.setChecked(False)
            if Path(path).suffix.lower() == ".csv":
                x, channels, meta = load_csv(path)
                self.plot.set_data(x, channels, meta["xlabel"], meta["ylabel"])
                self.tabs.setCurrentIndex(1)
                if meta["format"] == "ds100-stock":
                    self.status.setText(f"原厂 CSV：{Path(path).name} · {len(x):,} 点／通道 · {len(channels)} 通道 · "
                                        f"{meta['sample_rate_hz']:g} Sa/s · 已按表头换算为 V。")
                else:
                    self.status.setText(f"已打开 CSV：{Path(path).name} · {len(x):,} 行。列名与单位按源文件显示。")
            else:
                image = QImage(str(path))
                if image.isNull():
                    raise ValueError("The BMP file is not a supported image.")
                self.last_image = image
                self.screen_label.set_image(image)
                self.display_origin = 'file'
                self.origin_badge.setText('已保存的截图')
                self.origin_badge.setProperty('tone', 'offline')
                self.origin_badge.style().unpolish(self.origin_badge)
                self.origin_badge.style().polish(self.origin_badge)
                self.metrics['resolution'].setText(f'{image.width()} × {image.height()}')
                for name in ('frames', 'scan', 'cadence'):
                    self.metrics[name].setText('—')
                self.screen_note.setText(f"已保存的设备截图 · {Path(path).name} · {image.width()} × {image.height()}")
                self.tabs.setCurrentIndex(0)
            self.update_controls()
        except Exception as error:
            self.task_failed(str(error))

    def save_screen(self):
        if self.last_image is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "保存屏幕", "ds100-screen.png", "PNG (*.png)")
        if path and not self.last_image.save(path, "PNG"):
            self.task_failed("Cannot save the PNG file.")

    def capture_folder(self):
        override = os.environ.get('DS100_CAPTURE_DIR')
        if override:
            return Path(override)
        pictures = QStandardPaths.writableLocation(QStandardPaths.PicturesLocation)
        return (Path(pictures) if pictures else Path.cwd()) / 'DS100'

    def quick_save_screen(self):
        if self.last_image is None:
            return
        try:
            folder = self.capture_folder()
            folder.mkdir(parents=True, exist_ok=True)
            name = 'DS100-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.png'
            path = folder / name
            if not self.last_image.save(str(path), 'PNG'):
                raise OSError('Cannot save the PNG file.')
            self.status.setText('截图已保存 · ' + str(path))
        except OSError as error:
            # A local save failure must not disconnect the oscilloscope.
            self.status.setText('截图保存失败 · ' + str(error))

    def open_capture_folder(self):
        try:
            folder = self.capture_folder()
            folder.mkdir(parents=True, exist_ok=True)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder.resolve())))
        except OSError as error:
            self.status.setText('无法打开截图文件夹 · ' + str(error))

    def closeEvent(self, event):
        self.stop_motion()
        self.timer.stop()
        self.discovery_timer.stop()
        self.input_timer.stop()
        if self.worker is not None and self.worker.isRunning():
            self.worker.requestInterruption()
            if not self.worker.wait(3000):
                self.status.setText("正在等待设备请求结束，请稍后再关闭。")
                self.discovery_timer.start()
                self.input_timer.start()
                event.ignore()
                return
        self.disconnect()
        event.accept()


def main():
    demo = '--demo' in sys.argv
    if demo:
        os.environ['DS100_AUTO_CONNECT'] = '0'
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    if demo:
        window.source.setCurrentIndex(2)
        window.connect_now()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
