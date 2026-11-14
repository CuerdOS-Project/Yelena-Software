from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import (QColor, QFont, QFontDatabase, QGuiApplication,
                           QPainter, QPalette, QPen)
from PySide6.QtWidgets import QFrame, QLabel, QWidget


def is_dark() -> bool:
    return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark


def scaled_font(base: QFont, scale: float = 1.0, bold: bool = False, italic: bool = False) -> QFont:
    font = QFont(base)
    font.setPointSizeF(base.pointSizeF() * scale)
    font.setBold(bold)
    font.setItalic(italic)
    return font


def mono_font(base: QFont, scale: float = 1.0) -> QFont:
    font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    font.setPointSizeF(base.pointSizeF() * scale)
    return font


def muted_label(text: str, scale: float = 0.9, italic: bool = False) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setForegroundRole(QPalette.ColorRole.PlaceholderText)
    label.setFont(scaled_font(label.font(), scale, italic=italic))
    return label


def blend(a: QColor, b: QColor, t: float) -> QColor:
    return QColor(
        round(a.red() + (b.red() - a.red()) * t),
        round(a.green() + (b.green() - a.green()) * t),
        round(a.blue() + (b.blue() - a.blue()) * t),
    )


def semantic_color(kind: str, widget: QWidget) -> QColor:
    palette = widget.palette()
    if kind == "paused":
        return palette.color(QPalette.ColorRole.PlaceholderText)
    dark = palette.color(QPalette.ColorRole.Window).lightness() < 128
    on_dark, on_light = {
        "error":   ("#ef7b7b", "#c4262e"),
        "warning": ("#f5a524", "#b45309"),
        "success": ("#4ade80", "#15803d"),
    }[kind]
    return QColor(on_dark if dark else on_light)


def set_text_color(widget: QWidget, color: QColor) -> None:
    palette = widget.palette()
    palette.setColor(QPalette.ColorRole.WindowText, color)
    palette.setColor(QPalette.ColorRole.ButtonText, color)
    widget.setPalette(palette)


def tint_fill(widget: QWidget, tint: QColor, amount: float) -> QColor:
    return blend(widget.palette().color(QPalette.ColorRole.Window), tint, amount)


class Surface(QFrame):
    def __init__(self, parent=None, radius: float = 8.0, tint: float = 0.06,
                 color: QColor | None = None, border: float = 0.15, left_bar: bool = False,
                 role: QPalette.ColorRole | None = None):
        super().__init__(parent)
        self._radius = radius
        self._tint = tint
        self._color = color
        self._border = border
        self._left_bar = left_bar
        self._role = role

    def set_tint_color(self, color: QColor | None) -> None:
        self._color = color
        self.update()

    def paintEvent(self, event):
        palette = self.palette()
        window = palette.color(QPalette.ColorRole.Window)
        source = self._color or palette.color(self._role or QPalette.ColorRole.WindowText)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(blend(window, source, self._tint))
        painter.setPen(QPen(blend(window, source, self._tint + self._border), 1))
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.drawRoundedRect(rect, self._radius, self._radius)
        if self._left_bar:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(source)
            painter.drawRoundedRect(QRectF(rect.left(), rect.top() + 2, 3, rect.height() - 4), 1.5, 1.5)
        painter.end()
        super().paintEvent(event)


class TintedLabel(QLabel):
    def __init__(self, text: str = "", parent=None, radius: float = 8.0, tint: float = 0.1,
                 color: QColor | None = None, border: float = 0.2,
                 dashed: bool = False, left_bar: bool = False):
        super().__init__(text, parent)
        self._radius = radius
        self._tint = tint
        self._color = color
        self._border = border
        self._dashed = dashed
        self._left_bar = left_bar
        self._plain = False

    def set_tint_color(self, color: QColor | None) -> None:
        self._color = color
        self.update()

    def set_plain(self, plain: bool) -> None:
        self._plain = plain
        self.update()

    def paintEvent(self, event):
        if not self._plain:
            palette = self.palette()
            window = palette.color(QPalette.ColorRole.Window)
            source = self._color or palette.color(QPalette.ColorRole.WindowText)
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setBrush(blend(window, source, self._tint))
            pen = QPen(blend(window, source, self._tint + self._border), 1)
            if self._dashed:
                pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
            radius = min(self._radius, rect.height() / 2)
            painter.drawRoundedRect(rect, radius, radius)
            if self._left_bar:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(source)
                painter.drawRoundedRect(QRectF(rect.left(), rect.top() + 2, 3, rect.height() - 4), 1.5, 1.5)
            painter.end()
        super().paintEvent(event)
