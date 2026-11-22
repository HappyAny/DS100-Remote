"""Build the compact desktop layout. Device operations stay in MainWindow."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QActionGroup
from PySide6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QLabel,
                             QListWidget, QMenu, QPushButton, QSizePolicy,
                             QStackedWidget, QTextEdit, QToolButton, QVBoxLayout, QWidget)
from .input_panel import InputPanel
from .quick_panel import QuickPanel
from .theme import ACCENT, icon
from .workspace_widgets import ElidedLabel


def build_layout(window, screen_class, plot_class, auto_connect):
    w = window
    root = QWidget()
    root.setObjectName('compact')
    layout = QVBoxLayout(root)
    layout.setContentsMargins(8, 6, 8, 5)
    layout.setSpacing(6)

    def label(text, role='muted'):
        item = QLabel(text)
        item.setProperty('role', role)
        return item

    toolbar = QHBoxLayout()
    toolbar.setSpacing(5)
    w.page_title = label('DS100', 'compact-title')
    toolbar.addWidget(w.page_title)
    w.origin_badge = label('未连接', 'compact-badge')
    toolbar.addWidget(w.origin_badge)
    toolbar.addStretch()
    w.connect_button = QPushButton('连接')
    w.connect_button.setIcon(icon('link', ACCENT))
    w.connect_button.clicked.connect(w.connect_now)
    w.mirror = QPushButton('镜像')
    w.mirror.setCheckable(True)
    w.mirror.setProperty('role', 'mirror')
    w.mirror.setIcon(icon('play', ACCENT))
    w.mirror.toggled.connect(w.toggle_mirror)
    w.save_button = QPushButton('截图')
    w.save_button.setIcon(icon('save'))
    w.save_button.setToolTip('一键保存到图片 / DS100；另存为在更多菜单。')
    w.save_button.clicked.connect(w.quick_save_screen)
    for button in (w.connect_button, w.mirror, w.save_button):
        toolbar.addWidget(button)
    w.quick_toggle = QToolButton()
    w.quick_toggle.setText('快捷')
    w.quick_toggle.setCheckable(True)
    w.quick_toggle.setToolTip('切换一键操作 / 原机按键')
    w.quick_toggle.toggled.connect(lambda checked: w.set_raw_controls(not checked))
    toolbar.addWidget(w.quick_toggle)
    w.more_button = QToolButton()
    w.more_button.setText('···')
    w.more_button.setAccessibleName('更多功能')
    w.more_button.setToolTip('文件、显示和连接选项')
    w.more_button.setPopupMode(QToolButton.InstantPopup)
    w.more_menu = QMenu(w.more_button)
    w.more_button.setMenu(w.more_menu)
    toolbar.addWidget(w.more_button)
    layout.addLayout(toolbar)

    w.tabs = QStackedWidget()
    w.tabs.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
    screen = QWidget()
    screen_layout = QVBoxLayout(screen)
    screen_layout.setContentsMargins(0, 0, 0, 0)
    w.screen_label = screen_class()
    screen_layout.addWidget(w.screen_label)
    w.tabs.addWidget(screen)

    def page(title):
        widget = QWidget()
        column = QVBoxLayout(widget)
        column.setContentsMargins(6, 6, 6, 6)
        column.setSpacing(8)
        row = QHBoxLayout()
        row.addWidget(label(title, 'heading'))
        row.addStretch()
        back = QPushButton('返回屏幕')
        back.clicked.connect(lambda: w.tabs.setCurrentIndex(0))
        row.addWidget(back)
        column.addLayout(row)
        w.tabs.addWidget(widget)
        return column

    waves = page('波形与文件分析')
    row = QHBoxLayout()
    for attr, text, callback in (('wave_button', '读取波形', w.read_wave),
                                 ('run_button', '运行', lambda: w.set_run(True)),
                                 ('stop_button', '停止', lambda: w.set_run(False))):
        button = QPushButton(text)
        button.clicked.connect(callback)
        setattr(w, attr, button)
        row.addWidget(button)
    row.addStretch()
    waves.addLayout(row)
    w.plot = plot_class()
    waves.addWidget(w.plot, 1)

    files = page('设备文件')
    w.open_button = QPushButton('打开 CSV / BMP')
    w.open_button.clicked.connect(w.choose_file)
    files.addWidget(w.open_button, 0, Qt.AlignLeft)
    w.files = QListWidget()
    w.files.itemDoubleClicked.connect(lambda item: w.open_file(w.file_paths[w.files.row(item)]))
    files.addWidget(w.files, 1)
    w.cfg_note = label('设备配置：尚未读取')
    w.cfg_note.setWordWrap(True)
    files.addWidget(w.cfg_note)

    info = page('连接与信息')
    row = QHBoxLayout()
    w.source = QComboBox()
    w.source.addItems(['设备文件', '示波器', '离线演示'])
    w.source.setCurrentIndex(1)
    w.auto_connect = QCheckBox('自动连接并镜像')
    w.auto_connect.setChecked(auto_connect)
    row.addWidget(w.source)
    row.addWidget(w.auto_connect)
    row.addStretch()
    info.addLayout(row)
    row = QHBoxLayout()
    w.device_name = label('DS100')
    w.connection_badge = label('未连接')
    row.addWidget(w.device_name)
    row.addWidget(w.connection_badge)
    row.addStretch()
    info.addLayout(row)
    w.device_detail = label('等待设备连接')
    w.device_detail.setWordWrap(True)
    info.addWidget(w.device_detail)
    w.metrics = {}
    row = QHBoxLayout()
    for name, caption, initial in (('resolution', '分辨率', '480 × 320'),
                                   ('frames', '画面', '—'), ('scan', '读取', '—'),
                                   ('cadence', '接收', '—')):
        column = QVBoxLayout()
        column.addWidget(label(caption))
        w.metrics[name] = label(initial)
        column.addWidget(w.metrics[name])
        row.addLayout(column)
    info.addLayout(row)
    w.screen_note = ElidedLabel('尚未读取画面')
    w.screen_note.setMinimumWidth(0)
    w.screen_note.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    info.addWidget(w.screen_note)
    w.research = QTextEdit()
    w.research.setReadOnly(True)
    w.research.setPlainText(
        '镜像保持设备的 480 × 320 原始像素，刷新跟随实机。\n'
        'P / A / M / E 与拨轮沿用原机逻辑；按住 M / E 后可组合调节。\n'
        'P 长按关机；完全关机后，开机仍需按实机 P。\n\n'
        'R9：正常界面直接接入、按键控制和菜单像素对照已实测。\n'
        '测试参数重启保留，未按 AUTO 的启动采集已实测。\n'
        '原始波形服务、绝对运行 / 停止未绑定。\n'
        '主频策略由连接的固件决定；测量准确性与最高采集模式未验证。\n'
        '演示模式使用生成数据，不代表实机性能。\n'
        '文件模式读取已保存的 CSV / BMP。程序不提供刷机操作。')
    info.addWidget(w.research, 1)
    layout.addWidget(w.tabs, 1)

    w.control_modes = QStackedWidget()
    w.quick_panel = QuickPanel(w)
    w.quick_panel.requested.connect(w.request_quick_action)
    w.quick_panel.motion_started.connect(lambda action_id: w.request_quick_action(action_id, continuous=True))
    w.quick_panel.motion_stopped.connect(w.stop_motion)
    w.control_modes.addWidget(w.quick_panel)
    w.input_panel = InputPanel(w)
    w.input_panel.double_handler = lambda key: w.request_quick_action('double_' + key.lower())
    w.input_panel.changed.connect(w.queue_input)
    w.control_modes.addWidget(w.input_panel)
    w.control_modes.setCurrentIndex(1)
    layout.addWidget(w.control_modes)
    footer = QHBoxLayout()
    w.status = ElidedLabel('连接示波器即可使用')
    w.status.setProperty('role', 'muted')
    w.status.setMinimumWidth(0)
    w.status.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    footer.addWidget(w.status, 1)
    w.rate_label = label('')
    footer.addWidget(w.rate_label)
    layout.addLayout(footer)

    w.nav_group = QActionGroup(w)
    w.navigation = []
    for index, text in enumerate(('屏幕', '波形分析', '设备文件', '连接与信息')):
        action = w.more_menu.addAction(text)
        action.setCheckable(True)
        action.triggered.connect(lambda checked=False, index=index: w.tabs.setCurrentIndex(index))
        w.nav_group.addAction(action)
        w.navigation.append(action)
    w.navigation[0].setChecked(True)
    w.more_menu.addSeparator()
    quick = w.more_menu.addAction('显示一键快捷操作')
    w.quick_mode_action = quick
    quick.setCheckable(True)
    quick.toggled.connect(w.quick_toggle.setChecked)
    w.quick_toggle.toggled.connect(quick.setChecked)
    favorites = w.more_menu.addAction('自定义常用按钮…')
    favorites.triggered.connect(w.quick_panel.customize)
    save_as = w.more_menu.addAction('截图另存为…')
    save_as.triggered.connect(w.save_screen)
    w.save_as_action = save_as
    folder = w.more_menu.addAction('打开截图文件夹')
    folder.triggered.connect(w.open_capture_folder)
    action = w.more_menu.addAction('打开 CSV / BMP…')
    action.triggered.connect(w.choose_file)
    # Retain the refresh button API and share its enabled state with the menu.
    w.screen_button = QPushButton('刷新一帧', w)
    w.screen_button.hide()
    w.screen_button.clicked.connect(w.read_screen)
    refresh = w.more_menu.addAction('刷新一帧')
    refresh.triggered.connect(w.screen_button.click)
    w.refresh_action = refresh
    w.native = QCheckBox('1:1', w)
    w.native.hide()
    w.native.toggled.connect(w.set_native_size)
    native = w.more_menu.addAction('原始像素 1:1')
    native.setCheckable(True)
    native.toggled.connect(w.native.setChecked)
    w.native.toggled.connect(native.setChecked)
    sources = w.more_menu.addMenu('数据来源')
    group = QActionGroup(w)
    for index in range(3):
        action = sources.addAction(w.source.itemText(index))
        action.setCheckable(True)
        action.setChecked(index == 1)
        action.triggered.connect(lambda checked=False, index=index: w.source.setCurrentIndex(index))
        w.source.currentIndexChanged.connect(lambda current, index=index, action=action: action.setChecked(current == index))
        group.addAction(action)
    auto = w.more_menu.addAction('自动连接并镜像')
    auto.setCheckable(True)
    auto.setChecked(w.auto_connect.isChecked())
    auto.toggled.connect(w.auto_connect.setChecked)
    w.auto_connect.toggled.connect(auto.setChecked)
    def update_menu():
        refresh.setEnabled(w.screen_button.isEnabled())
        save_as.setEnabled(w.last_image is not None)
        sources.setEnabled(w.source.isEnabled())

    w.more_menu.aboutToShow.connect(update_menu)
    w.tabs.currentChanged.connect(w.page_changed)
    w.setCentralWidget(root)
