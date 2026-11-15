"""Navigation and relative rotary controls for the DS100 workspace."""

import math
from PySide6.QtCore import Qt, QRectF, QSize, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QRadialGradient
from PySide6.QtWidgets import QAbstractButton, QWidget, QLabel, QPushButton
from .theme import ACCENT, MUTED, TEXT, draw_symbol


class BrandMark(QWidget):
    def sizeHint(self):
        return QSize(46, 46)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        side = min(self.width(), self.height()) - 4
        rect = QRectF((self.width() - side) / 2, (self.height() - side) / 2, side, side)
        p.setPen(QPen(QColor('#37584b'), 1))
        p.setBrush(QColor('#1c3028'))
        p.drawRoundedRect(rect, 12, 12)
        draw_symbol(p, 'wave', rect.adjusted(7, 7, -7, -7), ACCENT, 1.7)


class NavButton(QAbstractButton):
    def __init__(self, symbol, text, parent=None):
        super().__init__(parent)
        self.symbol = symbol
        self.setText(text)
        self.setCheckable(True)
        self.setFixedSize(62, 66)
        self.setCursor(Qt.PointingHandCursor)
        self.setAccessibleName(text)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self.isChecked() or self.underMouse():
            p.setPen(Qt.NoPen)
            p.setBrush(QColor('#20352c' if self.isChecked() else '#1b252e'))
            p.drawRoundedRect(QRectF(2, 2, self.width() - 4, self.height() - 4), 10, 10)
        color = ACCENT if self.isChecked() else MUTED
        draw_symbol(p, self.symbol, QRectF(20, 10, 23, 23), color)
        font = self.font()
        font.setPixelSize(10)
        p.setFont(font)
        p.setPen(QColor(color))
        p.drawText(QRectF(0, 38, self.width(), 20), Qt.AlignCenter, self.text())
        if self.isChecked():
            p.setPen(QPen(QColor(ACCENT), 2.5, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(3, 25, 3, 41)


class KeyButton(QPushButton):
    def __init__(self, key, description, parent=None, compact=False):
        super().__init__(parent)
        self.compact = compact
        self.key, self.description = key, description
        self.setText(key + ' · ' + description)
        self.setAccessibleName(self.text())
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(44 if compact else 66)
        self.setToolTip('P 短按运行/暂停，长按关机。' if key == 'P' else key + ' · ' + description)

    def sizeHint(self):
        return QSize(52, 44) if self.compact else QSize(110, 66)

    def minimumSizeHint(self):
        return self.sizeHint()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        active = self.isEnabled()
        down = self.isDown()
        p.setBrush(QColor('#2c4438' if down else '#25313b' if self.underMouse() and active else '#1e2730'))
        p.setPen(QPen(QColor('#79b895' if down else '#536474' if self.underMouse() and active else '#34414c'), 1))
        p.drawRoundedRect(QRectF(.5, .5, self.width() - 1, self.height() - 1), 9, 9)
        font = QFont(self.font())
        font.setPixelSize(17 if self.compact else 21)
        font.setWeight(QFont.DemiBold)
        p.setFont(font)
        p.setPen(QColor(ACCENT if down else '#f0d17c' if self.key == 'P' and active else TEXT if active else '#8996a3'))
        p.drawText(QRectF(0, 2 if self.compact else 8, self.width(), 23 if self.compact else 29), Qt.AlignCenter, self.key)
        font.setPixelSize(9 if self.compact else 10)
        font.setWeight(QFont.Normal)
        p.setFont(font)
        p.setPen(QColor('#c8d6e1' if active else '#87939f'))
        p.drawText(QRectF(0, 25 if self.compact else 39, self.width(), 16 if self.compact else 18), Qt.AlignCenter, self.description)
        if self.hasFocus():
            p.setPen(QPen(QColor(ACCENT), 1, Qt.DotLine))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(QRectF(4, 4, self.width() - 8, self.height() - 8), 6, 6)


class RotaryDial(QWidget):
    """Emit relative detents. The displayed angle is interaction feedback."""
    turned = Signal(int)

    def __init__(self, numeral, parent=None, size=106):
        super().__init__(parent)
        self.numeral = numeral
        self.angle = -125.0
        self._wheel = 0
        self.wheel_sign = 1
        self._drag = False
        self._last_angle = 0.0
        self._remainder = 0.0
        self.setFixedSize(size, size)
        self.setCursor(Qt.OpenHandCursor)
        self.setToolTip('滚动鼠标滚轮，或沿旋钮拖动。两侧按钮每次微调一步。')
        self.setAccessibleName('拨轮 ' + numeral)
        self.setFocusPolicy(Qt.StrongFocus)

    def turn(self, steps):
        if not self.isEnabled() or not steps:
            return
        steps = max(-127, min(127, int(steps)))
        self.angle = (self.angle + steps * 18) % 360
        self.update()
        self.turned.emit(steps)

    def wheelEvent(self, event):
        if self.isEnabled():
            self._wheel += event.angleDelta().y() * self.wheel_sign
            steps = int(self._wheel / 120)
            self._wheel -= steps * 120
            self.turn(steps)
        event.accept()

    def _position_angle(self, point):
        return math.degrees(math.atan2(point.y() - self.height() / 2, point.x() - self.width() / 2))

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.isEnabled():
            self._drag = True
            self._last_angle = self._position_angle(event.position())
            self._remainder = 0
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()

    def mouseMoveEvent(self, event):
        if self._drag and self.isEnabled():
            angle = self._position_angle(event.position())
            delta = (angle - self._last_angle + 180) % 360 - 180
            self._last_angle = angle
            self._remainder += delta
            steps = int(self._remainder / 15)
            self._remainder -= steps * 15
            self.turn(steps)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag = False
        self.setCursor(Qt.OpenHandCursor)
        event.accept()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Right, Qt.Key_Up, Qt.Key_Left, Qt.Key_Down):
            self.turn(1 if event.key() in (Qt.Key_Right, Qt.Key_Up) else -1)
            event.accept()
        else:
            super().keyPressEvent(event)

    def changeEvent(self, event):
        if not self.isEnabled():
            self._drag = False
            self._wheel = 0
            self._remainder = 0
        super().changeEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        center = self.rect().center()
        p.translate(center)
        p.scale(self.width() / 106, self.height() / 106)
        active = self.isEnabled()
        for index in range(28):
            angle = math.radians(index * 360 / 28)
            p.setPen(QPen(QColor('#536473' if active else '#35424d'), 1.3))
            p.drawLine(int(46 * math.cos(angle)), int(46 * math.sin(angle)),
                       int(49 * math.cos(angle)), int(49 * math.sin(angle)))
        gradient = QRadialGradient(-13, -17, 76)
        gradient.setColorAt(0, QColor('#41505e' if active else '#2d3943'))
        gradient.setColorAt(.6, QColor('#26333e' if active else '#222d36'))
        gradient.setColorAt(1, QColor('#172129'))
        p.setBrush(gradient)
        p.setPen(QPen(QColor('#5a6a77' if active else '#3a4853'), 1))
        p.drawEllipse(QRectF(-39, -39, 78, 78))
        p.setPen(QPen(QColor('#18232b'), 1))
        p.setBrush(QColor('#202b34'))
        p.drawEllipse(QRectF(-29, -29, 58, 58))
        angle = math.radians(self.angle)
        p.setPen(QPen(QColor(ACCENT if active else '#687d8a'), 3, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(int(27 * math.cos(angle)), int(27 * math.sin(angle)),
                   int(34 * math.cos(angle)), int(34 * math.sin(angle)))
        font = QFont(self.font())
        font.setPixelSize(19)
        font.setWeight(QFont.Medium)
        p.setFont(font)
        p.setPen(QColor('#cbd9e4' if active else '#7e8f9c'))
        p.drawText(QRectF(-25, -15, 50, 30), Qt.AlignCenter, self.numeral)


class ElidedLabel(QLabel):
    def setText(self, text):
        super().setText(text)
        self.setToolTip(text)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setPen(self.palette().windowText().color())
        p.setFont(self.font())
        text = self.fontMetrics().elidedText(self.text(), Qt.ElideRight, self.width())
        p.drawText(self.rect(), Qt.AlignVCenter | Qt.AlignLeft, text)
