"""Ilustraciones chibi de Yelena guardadas como .kdi en resources/images."""

from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from core.kdi_image import decode_kdi

_IMAGES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "resources", "images")
_cache: dict[str, QPixmap] = {}


def load_chibi(name: str) -> QPixmap:
    """Devuelve el pixmap de resources/images/<name>.kdi (nulo si falla)."""
    if name not in _cache:
        pixmap = QPixmap()
        try:
            with open(os.path.join(_IMAGES_DIR, name + ".kdi"), "rb") as f:
                pixmap.loadFromData(decode_kdi(f.read()))
        except (OSError, ValueError):
            pass
        _cache[name] = pixmap
    return _cache[name]


class ChibiImage(QLabel):
    def __init__(self, name: str, size: int, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignCenter)
        self.setFixedSize(size, size)
        pixmap = load_chibi(name)
        self.available = not pixmap.isNull()
        if self.available:
            ratio = self.devicePixelRatioF()
            scaled = pixmap.scaled(int(size * ratio), int(size * ratio),
                                   Qt.KeepAspectRatio, Qt.SmoothTransformation)
            scaled.setDevicePixelRatio(ratio)
            self.setPixmap(scaled)


class ChibiPlaceholder(QWidget):
    """Imagen centrada con título y subtítulo, para estados vacíos."""

    def __init__(self, name: str, title: str = "", subtitle: str = "", size: int = 260, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignCenter)
        lay.setSpacing(4)
        lay.addWidget(ChibiImage(name, size), alignment=Qt.AlignHCenter)
        lay.addSpacing(8)
        self._title = QLabel(title)
        font = self._title.font()
        font.setPointSize(font.pointSize() + 4)
        font.setBold(True)
        self._title.setFont(font)
        self._title.setAlignment(Qt.AlignCenter)
        lay.addWidget(self._title)
        self._subtitle = QLabel(subtitle)
        self._subtitle.setObjectName("sectionSub")
        self._subtitle.setAlignment(Qt.AlignCenter)
        self._subtitle.setWordWrap(True)
        lay.addWidget(self._subtitle)

    def set_texts(self, title: str, subtitle: str = "") -> None:
        self._title.setText(title)
        self._subtitle.setText(subtitle)
