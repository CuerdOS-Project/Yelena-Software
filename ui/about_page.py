# yl-soft — About Page (v2: banner de acento + grilla de información)

from __future__ import annotations

import os

from PySide6 import __version__ as _pyside_version
from PySide6.QtCore import Qt, QSize, QCoreApplication, qVersion
from PySide6.QtCore import QRectF
from PySide6.QtGui import (
    QColor, QGuiApplication, QIcon, QPainter, QPainterPath, QPalette, QPen, QPixmap,
)
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
)

from core.i18n import tr
from .native_widgets import CardFrame, FeaturedBannerFrame

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _set_font(widget, pt: int, bold: bool = False, italic: bool = False):
    """Aplica tamaño/negrita/cursiva con QFont (sin QSS)."""
    f = widget.font()
    f.setPointSize(pt)
    if bold:
        f.setBold(True)
    if italic:
        f.setItalic(True)
    widget.setFont(f)


def _load_app_icon(size: int = 64) -> QPixmap | None:
    """Carga yl-soft.svg desde el directorio raíz; fallback al tema."""
    from backend import store
    icon = store.find_app_icon()
    if not icon.isNull():
        px = icon.pixmap(QSize(size, size))
        if not px.isNull() and px.width() > 0:
            return px
    return None


def _theme_pixmap(names: list[str], size: int = 18) -> QPixmap | None:
    for name in names:
        icon = QIcon.fromTheme(name)
        if not icon.isNull():
            px = icon.pixmap(QSize(size, size))
            if not px.isNull() and px.width() > 0:
                return px
    return None


class _InfoCard(CardFrame):
    def __init__(self, icon_names, label, value_html, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(10)

        px = _theme_pixmap(icon_names, 20)
        icon_lbl = QLabel()
        icon_lbl.setFixedSize(20, 20)
        icon_lbl.setAlignment(Qt.AlignTop)
        if px:
            icon_lbl.setPixmap(px)
        lay.addWidget(icon_lbl)

        text_col = QVBoxLayout()
        text_col.setSpacing(2)

        lbl = QLabel(label)
        lbl.setObjectName("sectionSub")
        _set_font(lbl, 9)
        text_col.addWidget(lbl)

        val = QLabel(value_html)
        val.setTextFormat(Qt.RichText)
        val.setOpenExternalLinks(True)
        val.setWordWrap(True)
        _set_font(val, 10, bold=True)
        text_col.addWidget(val)

        lay.addLayout(text_col, 1)


class _SpeechBubble(QWidget):
    """Globo de diálogo con cola hacia abajo, coloreado según la paleta."""

    _TAIL = 12

    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 10, 16, 10 + self._TAIL)
        lbl = QLabel(text)
        lbl.setWordWrap(True)
        lbl.setAlignment(Qt.AlignCenter)
        _set_font(lbl, 11, bold=True)
        lay.addWidget(lbl)

    def paintEvent(self, _e):
        pal = self.palette()
        base, txt = pal.color(QPalette.Base), pal.color(QPalette.WindowText)
        edge = QColor(txt)
        edge.setAlpha(110)
        w, h = self.width(), self.height()
        body = QRectF(0.5, 0.5, w - 1, h - self._TAIL - 1)
        path = QPainterPath()
        path.addRoundedRect(body, 14, 14)
        tail = QPainterPath()
        cx, by = w / 2, body.bottom()
        tail.moveTo(cx - 10, by)
        tail.lineTo(cx, by + self._TAIL)
        tail.lineTo(cx + 10, by)
        tail.closeSubpath()
        path = path.united(tail)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(edge, 1))
        p.setBrush(base)
        p.drawPath(path)
        p.end()


class _YelenaGreeting(QWidget):
    """Yelena saludando: dibujo con un globo de diálogo, sin tarjeta."""

    _ART_HEIGHT = 330

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(230)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)

        lay.addWidget(_SpeechBubble(tr("about_yelena_greeting")))

        art = QPixmap(os.path.join(_ROOT, "resources", "yelena.png"))
        if not art.isNull():
            dpr = QGuiApplication.primaryScreen().devicePixelRatio()
            art = art.scaledToHeight(round(self._ART_HEIGHT * dpr), Qt.SmoothTransformation)
            art.setDevicePixelRatio(dpr)
            pic = QLabel()
            pic.setPixmap(art)
            pic.setAlignment(Qt.AlignCenter)
            lay.addWidget(pic)


class AboutPage(QWidget):
    """Página de información de la aplicación."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("aboutPage")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        outer = QHBoxLayout()
        outer.setSpacing(24)
        outer.addStretch()

        self._yelena = _YelenaGreeting()
        outer.addWidget(self._yelena, alignment=Qt.AlignVCenter)

        col = QVBoxLayout()
        col.setSpacing(16)

        # ─── Banner de encabezado (ícono grande + nombre + versión) ───
        banner = FeaturedBannerFrame()
        banner.setFixedWidth(520)
        banner_v = QVBoxLayout(banner)
        banner_v.setContentsMargins(32, 28, 32, 28)
        banner_v.setSpacing(6)
        banner_v.setAlignment(Qt.AlignCenter)

        icon_lbl = QLabel()
        icon_lbl.setAlignment(Qt.AlignCenter)
        px = _load_app_icon(size=72)
        if px:
            icon_lbl.setPixmap(px)
        banner_v.addWidget(icon_lbl)

        name_lbl = QLabel(tr("about_title"))
        name_lbl.setObjectName("detailName")
        name_lbl.setAlignment(Qt.AlignCenter)
        _set_font(name_lbl, 23, bold=True)
        banner_v.addWidget(name_lbl)

        ver_lbl = QLabel(f"{tr('about_version')} {QCoreApplication.applicationVersion()}")
        ver_lbl.setObjectName("detailVersion")
        ver_lbl.setAlignment(Qt.AlignCenter)
        _set_font(ver_lbl, 10, italic=True)
        banner_v.addWidget(ver_lbl)

        col.addWidget(banner)

        # ─── Descripción ───
        desc_card = CardFrame()
        desc_card.setFixedWidth(520)
        desc_v = QVBoxLayout(desc_card)
        desc_v.setContentsMargins(24, 18, 24, 18)
        desc = QLabel(tr("about_description"))
        desc.setObjectName("detailDesc")
        desc.setWordWrap(True)
        desc.setAlignment(Qt.AlignCenter)
        desc_v.addWidget(desc)
        col.addWidget(desc_card)

        # ─── Grilla de información: licencia, copyleft, autores, web ───
        grid = QGridLayout()
        grid.setSpacing(12)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)

        grid.addWidget(_InfoCard(
            ["text-x-script", "text-plain", "dialog-information"],
            tr("about_license_label"), tr("about_license_value"),
        ), 0, 0)

        grid.addWidget(_InfoCard(
            ["applications-system", "preferences-system", "dialog-information"],
            tr("about_qt_label"),
            f"Qt {qVersion()} / PySide {_pyside_version}",
        ), 0, 1)

        grid.addWidget(_InfoCard(
            ["system-users", "user-identity", "im-user"],
            tr("about_authors_label"), tr("about_authors_value"),
        ), 1, 0)

        grid.addWidget(_InfoCard(
            ["applications-internet", "web-browser", "accessories-text-editor"],
            tr("about_source_label"),
            f'<a href="https://cuerdos.github.io">{tr("about_source")}</a>',
        ), 1, 1)

        grid_wrap = QWidget()
        grid_wrap.setFixedWidth(520)
        grid_wrap.setLayout(grid)
        col.addWidget(grid_wrap)

        # ─── Pie: leyenda copyleft corta, centrada y discreta ───
        footer = QLabel(tr("about_footer"))
        footer.setObjectName("sectionSub")
        footer.setAlignment(Qt.AlignCenter)
        _set_font(footer, 9)
        col.addWidget(footer)

        outer.addLayout(col)
        outer.addStretch()

        root.addStretch()
        root.addLayout(outer)
        root.addStretch()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        # En ventanas estrechas se oculta la ilustración para no apretar el contenido.
        self._yelena.setVisible(self.width() >= 920)
