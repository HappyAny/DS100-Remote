"""Colors, icons, and styles for the desktop workspace."""

from PySide6.QtCore import Qt, QRectF, QSize
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

BG = '#101418'
SURFACE = '#171d23'
RAISED = '#202831'
LINE = '#2c3641'
TEXT = '#e8edf2'
MUTED = '#8b99a7'
ACCENT = '#79dfb5'


def draw_symbol(painter, name, rect, color=TEXT, width=1.6):
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing)
    painter.translate(rect.x(), rect.y())
    painter.scale(rect.width() / 24, rect.height() / 24)
    painter.setPen(QPen(QColor(color), width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    painter.setBrush(Qt.NoBrush)
    path = QPainterPath()
    if name in ('screen', 'wave'):
        if name == 'screen':
            painter.drawRoundedRect(QRectF(2.5, 3, 19, 15), 2.5, 2.5)
            painter.drawLine(9, 21, 15, 21)
            painter.drawLine(12, 18, 12, 21)
        path.moveTo(5, 12)
        path.lineTo(8, 12)
        path.lineTo(10, 7)
        path.lineTo(13, 15)
        path.lineTo(16, 10)
        path.lineTo(19, 10)
    elif name == 'files':
        path.moveTo(3, 8)
        path.lineTo(3, 19)
        path.quadTo(3, 21, 5, 21)
        path.lineTo(19, 21)
        path.quadTo(21, 21, 21, 19)
        path.lineTo(21, 8)
        path.closeSubpath()
        path.moveTo(3, 8)
        path.lineTo(3, 5)
        path.lineTo(10, 5)
        path.lineTo(12, 8)
        path.lineTo(21, 8)
    elif name == 'info':
        painter.drawEllipse(QRectF(3, 3, 18, 18))
        painter.drawLine(12, 11, 12, 17)
        painter.drawPoint(12, 7)
    elif name == 'link':
        path.moveTo(9, 8)
        path.cubicTo(5, 4, 0, 10, 5, 14)
        path.lineTo(8, 17)
        path.cubicTo(10, 19, 13, 18, 14, 16)
        path.moveTo(15, 16)
        path.cubicTo(19, 20, 24, 14, 19, 10)
        path.lineTo(16, 7)
        path.cubicTo(14, 5, 11, 6, 10, 8)
        path.moveTo(8, 12)
        path.lineTo(16, 12)
    elif name == 'save':
        painter.drawRoundedRect(QRectF(4, 3, 16, 18), 2, 2)
        path.moveTo(8, 3)
        path.lineTo(8, 9)
        path.lineTo(16, 9)
        path.lineTo(16, 3)
        path.moveTo(8, 21)
        path.lineTo(8, 14)
        path.lineTo(16, 14)
        path.lineTo(16, 21)
    elif name == 'refresh':
        path.moveTo(19, 7)
        path.cubicTo(14, 0, 3, 4, 4, 13)
        path.cubicTo(5, 21, 16, 23, 20, 15)
        path.moveTo(19, 3)
        path.lineTo(19, 8)
        path.lineTo(14, 8)
    elif name == 'play':
        path.moveTo(8, 5)
        path.lineTo(19, 12)
        path.lineTo(8, 19)
        path.closeSubpath()
    elif name == 'pause':
        painter.drawLine(8, 5, 8, 19)
        painter.drawLine(16, 5, 16, 19)
    elif name == 'power':
        painter.drawArc(QRectF(4, 4, 16, 16), 130 * 16, 280 * 16)
        painter.drawLine(12, 2, 12, 11)
    elif name == 'chevron':
        path.moveTo(8, 5)
        path.lineTo(15, 12)
        path.lineTo(8, 19)
    painter.drawPath(path)
    painter.restore()


def icon(name, color=TEXT, size=20):
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    draw_symbol(painter, name, QRectF(0, 0, size * 2, size * 2), color)
    painter.end()
    pixmap.setDevicePixelRatio(2)
    return QIcon(pixmap)


STYLESHEET = '''
QMainWindow, QWidget#workspace { background: #101418; color: #e8edf2; }
QWidget { color: #e8edf2; font-size: 12px; }
QFrame#rail { background: #0b1014; border-right: 1px solid #232c35; }
QFrame#header { border-bottom: 1px solid #252f38; }
QFrame#card, QWidget#card { background: #171d23; border: 1px solid #2b343e; border-radius: 12px; }
QFrame#divider { background: #2a333c; border: none; max-height: 1px; }
QLabel { background: transparent; border: none; }
QLabel[role="muted"] { color: #8b99a7; font-size: 11px; }
QLabel[role="section"] { color: #a5b1bd; font-size: 11px; }
QLabel[role="heading"] { font-size: 17px; font-weight: 600; color: #edf1f5; }
QLabel[role="page-title"] { font-size: 22px; font-weight: 600; color: #edf1f5; }
QLabel[role="metric"] { color: #dce5ed; font-family: "Consolas"; font-size: 18px; }
QLabel[role="badge"] { border: 1px solid #35404b; background: #202830; color: #a8b4c0; border-radius: 10px; padding: 4px 10px; font-size: 10px; }
QLabel[tone="live"] { color: #8ee6c2; background: #1b342c; border-color: #31594a; }
QLabel[tone="demo"] { color: #e8c27a; background: #362e21; border-color: #5a4b30; }
QLabel[tone="offline"] { color: #9cabb9; background: #222b34; border-color: #34414e; }
QPushButton, QToolButton { background: #202831; border: 1px solid #35414c; border-radius: 7px; padding: 8px 13px; color: #dbe3eb; }
QPushButton:hover, QToolButton:hover { background: #2a3540; border-color: #5b6c7b; }
QPushButton:pressed, QToolButton:pressed { background: #33414d; }
QToolButton:checked { background: #253f34; border-color: #4c8a6d; color: #9bedca; }
QPushButton:disabled, QToolButton:disabled { color: #7a8794; background: #1b2229; border-color: #2a333c; }
QPushButton[role="primary"] { background: #79dfb5; color: #10271d; border-color: #79dfb5; font-weight: 600; }
QPushButton[role="primary"]:hover { background: #94edc9; border-color: #94edc9; }
QPushButton[role="primary"]:disabled { background: #293d35; color: #7a9b8a; border-color: #365246; }
QPushButton[role="mirror"]:checked { background: #253f34; border-color: #4c8a6d; color: #9bedca; }
QPushButton[role="quiet"], QToolButton[role="quiet"] { background: transparent; border-color: transparent; color: #aebbc7; }
QPushButton[role="quiet"]:hover, QToolButton[role="quiet"]:hover { background: #26313a; border-color: #35414d; }
QPushButton[role="step"] { padding: 2px; min-width: 30px; max-width: 30px; min-height: 26px; max-height: 26px; font-size: 17px; }
QPushButton[role="secondary"] { background: #1c242c; color: #bdc9d3; }
QComboBox { background: #1b232b; border: 1px solid #35414c; border-radius: 7px; padding: 8px 28px 8px 11px; min-width: 110px; }
QComboBox:hover { border-color: #5b6c7b; }
QComboBox:disabled { color: #7a8794; border-color: #2a333c; }
QComboBox::drop-down { border: none; width: 24px; }
QComboBox QAbstractItemView { background: #202932; color: #dce5ed; selection-background-color: #34473f; border: 1px solid #40505d; padding: 5px; }
QCheckBox { spacing: 7px; color: #9eacb9; background: transparent; font-size: 11px; }
QCheckBox::indicator { width: 14px; height: 14px; background: #11181e; border: 1px solid #53616e; border-radius: 4px; }
QCheckBox::indicator:checked { background: #79dfb5; border: 3px solid #355c4a; }
QCheckBox:disabled { color: #7c8995; }
QCheckBox::indicator:disabled { background: #192128; border-color: #35414a; }
QListWidget, QTextEdit { background: #12191f; border: 1px solid #2c3741; border-radius: 8px; color: #b9c6d2; padding: 10px; selection-background-color: #2b483a; }
QListWidget::item { padding: 12px 8px; border-bottom: 1px solid #26313b; }
QListWidget::item:selected { background: #263c32; color: #d6f1e2; border-radius: 5px; }
QScrollArea { border: none; background: transparent; }
QScrollArea > QWidget > QWidget { background: transparent; }
QScrollBar:vertical { background: transparent; width: 6px; margin: 1px; }
QScrollBar::handle:vertical { background: #3d4b57; border-radius: 3px; min-height: 32px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QToolTip { background: #26323d; color: #e2eaf1; border: 1px solid #536475; padding: 6px; }
QMenu { background: #202a33; color: #dce6ef; border: 1px solid #43515e; padding: 6px; }
QMenu::item { padding: 8px 22px; border-radius: 5px; }
QMenu::item:selected { background: #34473f; }
QWidget#compact { background: #101418; }
QWidget#compact QPushButton, QWidget#compact QToolButton { padding: 5px 9px; border-radius: 5px; }
QWidget#compact QLabel[role="compact-title"] { color: #dce7e5; font-size: 13px; font-weight: 600; padding: 0 5px; }
QWidget#compact QLabel[role="compact-badge"] { color: #8b99a7; font-size: 10px; padding: 2px 4px; }
QWidget#compact QLabel[role="compact-badge"][tone="demo"] { color: #f1cb78; background: #30291e; }
QWidget#compact QLabel[role="compact-badge"][tone="live"] { color: #8ee6c2; background: transparent; }
QWidget#compact-controls { background: #171d23; border-top: 1px solid #303b44; border-radius: 5px; }
QWidget#compact QPushButton[role="small"] { padding: 2px 6px; font-size: 10px; }
QWidget#compact QPushButton[role="compact-step"] { padding: 0; min-width: 22px; max-width: 22px; min-height: 24px; max-height: 24px; font-size: 15px; }
QWidget#compact QPushButton[role="quick-action"] { padding: 7px 8px; color: #d9e9e1; background: #23352d; border-color: #3b5547; }
QWidget#compact QPushButton[role="quick-action"]:hover { background: #304b3c; border-color: #6a9980; }
QWidget#compact QPushButton[role="quick-action"]:disabled { background: #1c2621; color: #78897f; border-color: #2b3a31; }
QWidget#compact QPushButton[role="quick-step"] { padding: 2px 5px; min-width: 22px; min-height: 20px; font-size: 14px; }
QWidget#compact QCheckBox { spacing: 4px; font-size: 10px; }
QWidget#compact QCheckBox::indicator { width: 11px; height: 11px; border-radius: 3px; }
'''
