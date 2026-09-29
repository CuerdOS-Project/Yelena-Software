# interfaz (fondos, bordes, botones, paneles). Todo lo que antes se pintaba

from __future__ import annotations

from PySide6.QtCore import (
    Qt, QSize, QRectF, QPointF, QEvent,
    QPropertyAnimation, QEasingCurve, Property,
)
from PySide6.QtGui import (
    QPainter, QColor, QBrush, QPen, QPalette, QIcon, QPixmap,
    QPainterPath, QTextLayout, QTextOption, QCursor, QGuiApplication,
)
from PySide6.QtWidgets import (
    QLabel, QFrame, QPushButton, QWidget, QProgressBar, QHBoxLayout,
    QVBoxLayout, QApplication, QSizePolicy, QToolTip,
    QGraphicsOpacityEffect, QGraphicsDropShadowEffect,
)


class SlidingLoadingBar(QWidget):
    """Barra fina indeterminada: un segmento se desliza de izquierda a
    derecha y rebota en bucle mientras `start()` está activo. Pensada
    para indicar "hay una búsqueda en curso" sin depender de un
    porcentaje real de progreso."""

    def __init__(self, parent=None, height: int = 3):
        super().__init__(parent)
        self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._frac = 0.0
        self._running = False

        self._anim = QPropertyAnimation(self, b"barFrac", self)
        self._anim.setDuration(1300)
        self._anim.setKeyValueAt(0.0, 0.0)
        self._anim.setKeyValueAt(0.5, 1.0)
        self._anim.setKeyValueAt(1.0, 0.0)
        self._anim.setEasingCurve(QEasingCurve.InOutSine)
        self._anim.setLoopCount(-1)

        self.setVisible(False)

    def _get_frac(self) -> float:
        return self._frac

    def _set_frac(self, v: float) -> None:
        self._frac = v
        self.update()

    barFrac = Property(float, _get_frac, _set_frac)

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self.setVisible(True)
        self._anim.start()

    def stop(self) -> None:
        self._running = False
        self._anim.stop()
        self._frac = 0.0
        self.setVisible(False)
        self.update()

    def paintEvent(self, event) -> None:
        if not self._running:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        track_color = QApplication.palette().color(QPalette.Mid)
        track_color.setAlpha(60)
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(track_color))
        p.drawRoundedRect(self.rect(), self.height() / 2, self.height() / 2)

        accent = QApplication.palette().color(QPalette.Highlight)
        seg_w = max(36.0, self.width() * 0.16)
        x = self._frac * max(0.0, self.width() - seg_w)
        p.setBrush(QBrush(accent))
        p.drawRoundedRect(QRectF(x, 0, seg_w, self.height()), self.height() / 2, self.height() / 2)


def animate_grid_reflow(widget: QWidget, duration: int = 180) -> None:
    """Anima una breve transición de opacidad (fade-in) al recolocar un
    grid — p.ej. tras un cambio en el nº de columnas por redimensionado.
    Evita que el reflow se sienta como un parpadeo/salto abrupto.
    """
    effect = QGraphicsOpacityEffect(widget)
    widget.setGraphicsEffect(effect)

    anim = QPropertyAnimation(effect, b"opacity", widget)
    anim.setDuration(duration)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.setEasingCurve(QEasingCurve.OutCubic)

    def _cleanup():
        widget.setGraphicsEffect(None)
        widget._reflow_anim = None

    anim.finished.connect(_cleanup)
    widget._reflow_anim = anim  # mantener viva la referencia
    anim.start()


# Iconos "symbolic" dibujados a mano
#
# QIcon.fromTheme() depende de que el tema de iconos activo del sistema
# (Breeze, Adwaita, hicolor, un tema GTK minimal en una instalación
# recién hecha de Void/CuerdOS...) traiga esos nombres concretos. En la
# práctica muchos no existen fuera de GNOME/KDE completos, así que dos
# botones distintos terminaban resolviendo ambos a "sin icono" y solo se
# distinguían por el color de fondo -o ni eso, si además compartían
# estilo-, pareciendo el mismo botón duplicado.
#
# Estos glifos son trazos vectoriales simples (stroke-only, un solo
# color) dibujados con QPainter: no dependen de ningún tema instalado,
# se ven igual en cualquier equipo, y cada "kind" es visualmente propio,
# como cualquier icono symbolic real.
_SYMBOLIC_CACHE: dict[tuple, QIcon] = {}


def _stroke_pen(color: QColor, width: float = 1.6) -> QPen:
    pen = QPen(color, width)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    return pen


def _paint_symbolic(painter: QPainter, kind: str, rect: QRectF, color: QColor) -> None:
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(_stroke_pen(color))
    painter.setBrush(Qt.NoBrush)
    x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
    cx, cy = x + w / 2, y + h / 2

    if kind == "reload":
        # Flecha circular (recargar / releer lista)
        r = w * 0.34
        rect_arc = QRectF(cx - r, cy - r, r * 2, r * 2)
        painter.drawArc(rect_arc, 40 * 16, 300 * 16)
        head = QPainterPath()
        ax = cx + r * 0.98
        ay = cy - r * 0.2
        head.moveTo(ax, ay)
        head.lineTo(ax + w * 0.16, ay - h * 0.02)
        head.lineTo(ax + w * 0.03, ay + h * 0.17)
        painter.setBrush(QBrush(color))
        painter.setPen(Qt.NoPen)
        painter.drawPath(head)

    elif kind == "cloud_check":
        # Buscar/comprobar actualizaciones: nube + check
        r = w * 0.16
        base_y = cy + h * 0.06
        painter.drawEllipse(QPointF(cx - r * 1.1, base_y), r, r)
        painter.drawEllipse(QPointF(cx + r * 0.5, base_y - r * 0.35), r * 1.15, r * 1.15)
        painter.drawLine(QPointF(cx - r * 2.2, base_y + r * 0.05),
                          QPointF(cx + r * 1.8, base_y + r * 0.05))
        path = QPainterPath()
        path.moveTo(cx - w * 0.14, cy + h * 0.30)
        path.lineTo(cx - w * 0.02, cy + h * 0.42)
        path.lineTo(cx + w * 0.20, cy + h * 0.14)
        painter.drawPath(path)

    elif kind == "install_all":
        # Dos flechas hacia abajo apiladas: instalar todo
        for dy in (-h * 0.18, h * 0.14):
            path = QPainterPath()
            path.moveTo(cx - w * 0.16, cy + dy - h * 0.08)
            path.lineTo(cx, cy + dy + h * 0.06)
            path.lineTo(cx + w * 0.16, cy + dy - h * 0.08)
            painter.drawPath(path)

    elif kind == "check":
        path = QPainterPath()
        path.moveTo(cx - w * 0.22, cy)
        path.lineTo(cx - w * 0.04, cy + h * 0.20)
        path.lineTo(cx + w * 0.26, cy - h * 0.20)
        painter.drawPath(path)

    elif kind == "cancel":
        m = w * 0.20
        painter.drawLine(QPointF(cx - m, cy - m), QPointF(cx + m, cy + m))
        painter.drawLine(QPointF(cx + m, cy - m), QPointF(cx - m, cy + m))

    elif kind == "broom":
        # Escoba: mango + cerdas en abanico
        painter.drawLine(QPointF(cx - w * 0.22, cy - h * 0.28),
                          QPointF(cx + w * 0.05, cy + h * 0.05))
        base = QPointF(cx + w * 0.05, cy + h * 0.05)
        for ang in (-18, -6, 6, 18):
            import math
            rad = math.radians(ang + 55)
            end = QPointF(base.x() + math.cos(rad) * w * 0.30,
                           base.y() + math.sin(rad) * h * 0.30)
            painter.drawLine(base, end)

    elif kind == "box":
        # Paquete/caja: usado para insignias de origen (Flatpak / XBPS)
        m_x, m_y = w * 0.20, h * 0.20
        r = QRectF(x + m_x, y + m_y, w - 2 * m_x, h - 2 * m_y)
        painter.drawRect(r)
        painter.drawLine(QPointF(r.left(), r.top() + r.height() * 0.35),
                          QPointF(r.right(), r.top() + r.height() * 0.35))
        painter.drawLine(QPointF(r.center().x(), r.top() + r.height() * 0.35),
                          QPointF(r.center().x(), r.bottom()))

    elif kind == "terminal":
        # Prompt ">_": usado para la insignia de paquetes nativos (XBPS)
        m_x, m_y = w * 0.16, h * 0.20
        r = QRectF(x + m_x, y + m_y, w - 2 * m_x, h - 2 * m_y)
        painter.drawRoundedRect(r, 2, 2)
        path = QPainterPath()
        path.moveTo(r.left() + r.width() * 0.18, r.top() + r.height() * 0.30)
        path.lineTo(r.left() + r.width() * 0.42, r.top() + r.height() * 0.5)
        path.lineTo(r.left() + r.width() * 0.18, r.top() + r.height() * 0.70)
        painter.drawPath(path)
        painter.drawLine(QPointF(r.left() + r.width() * 0.52, r.top() + r.height() * 0.70),
                          QPointF(r.left() + r.width() * 0.80, r.top() + r.height() * 0.70))

    elif kind == "warn":
        # Triángulo de aviso con exclamación (usado para amarillo/rojo)
        path = QPainterPath()
        path.moveTo(cx, cy - h * 0.32)
        path.lineTo(cx + w * 0.32, cy + h * 0.26)
        path.lineTo(cx - w * 0.32, cy + h * 0.26)
        path.closeSubpath()
        painter.drawPath(path)
        painter.drawLine(QPointF(cx, cy - h * 0.08), QPointF(cx, cy + h * 0.06))
        painter.setBrush(QBrush(color))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(QPointF(cx, cy + h * 0.17), w * 0.02, w * 0.02)

    elif kind == "arrow_up":
        path = QPainterPath()
        path.moveTo(cx - w * 0.18, cy + h * 0.14)
        path.lineTo(cx, cy - h * 0.18)
        path.lineTo(cx + w * 0.18, cy + h * 0.14)
        painter.drawPath(path)
        painter.drawLine(QPointF(cx, cy - h * 0.12), QPointF(cx, cy + h * 0.26))

    elif kind == "chevron_left":
        # Flecha de navegación del carrusel (izquierda). Se dibuja a mano
        # en vez de usar un glifo Unicode ("‹") porque su color/visibilidad
        # dependía del QStyle activo (auto/breeze/fusion no llevan QSS
        # propio) y en algunos temas nativos quedaba invisible.
        path = QPainterPath()
        path.moveTo(cx + w * 0.14, cy - h * 0.22)
        path.lineTo(cx - w * 0.14, cy)
        path.lineTo(cx + w * 0.14, cy + h * 0.22)
        painter.drawPath(path)

    elif kind == "chevron_right":
        path = QPainterPath()
        path.moveTo(cx - w * 0.14, cy - h * 0.22)
        path.lineTo(cx + w * 0.14, cy)
        path.lineTo(cx - w * 0.14, cy + h * 0.22)
        painter.drawPath(path)

    elif kind == "width_full":
        # Flecha doble horizontal: alternar tarjetas a ancho completo.
        painter.drawLine(QPointF(x + w * 0.08, cy), QPointF(x + w * 0.92, cy))
        for base_x, direction in ((x + w * 0.08, 1), (x + w * 0.92, -1)):
            head = QPainterPath()
            head.moveTo(base_x, cy)
            head.lineTo(base_x + direction * w * 0.16, cy - h * 0.15)
            painter.drawPath(head)
            head2 = QPainterPath()
            head2.moveTo(base_x, cy)
            head2.lineTo(base_x + direction * w * 0.16, cy + h * 0.15)
            painter.drawPath(head2)

    elif kind == "sort_az":
        # "A→Z": línea ascendente con etiqueta A arriba-izq y Z abajo-der
        # simplificada como dos barras crecientes + flecha hacia abajo.
        bar_w = w * 0.14
        for i, hh in enumerate((0.30, 0.52, 0.74)):
            bx = x + w * 0.14 + i * (bar_w + w * 0.08)
            by = cy + h * 0.30
            painter.drawLine(QPointF(bx, by), QPointF(bx, by - h * hh))
        path = QPainterPath()
        path.moveTo(x + w * 0.80, y + h * 0.12)
        path.lineTo(x + w * 0.80, y + h * 0.62)
        painter.drawPath(path)
        head = QPainterPath()
        head.moveTo(x + w * 0.72, y + h * 0.50)
        head.lineTo(x + w * 0.80, y + h * 0.66)
        head.lineTo(x + w * 0.88, y + h * 0.50)
        painter.drawPath(head)

    elif kind == "sort_za":
        bar_w = w * 0.14
        for i, hh in enumerate((0.74, 0.52, 0.30)):
            bx = x + w * 0.14 + i * (bar_w + w * 0.08)
            by = cy + h * 0.30
            painter.drawLine(QPointF(bx, by), QPointF(bx, by - h * hh))
        path = QPainterPath()
        path.moveTo(x + w * 0.80, y + h * 0.12)
        path.lineTo(x + w * 0.80, y + h * 0.62)
        painter.drawPath(path)
        head = QPainterPath()
        head.moveTo(x + w * 0.72, y + h * 0.50)
        head.lineTo(x + w * 0.80, y + h * 0.66)
        head.lineTo(x + w * 0.88, y + h * 0.50)
        painter.drawPath(head)


def symbolic_icon(kind: str, color: "QColor | None" = None, size: int = 18) -> QIcon:
    """Icono monocromo (estilo "symbolic") dibujado a mano, sin depender
    del tema de iconos del sistema. `color` por defecto es el color de
    texto/ventana del tema activo (para que se integre con claro/oscuro)."""
    if color is None:
        color = QApplication.palette().color(QPalette.WindowText)
    dpr = QGuiApplication.primaryScreen().devicePixelRatio() if QGuiApplication.primaryScreen() else 1.0
    key = (kind, color.name(QColor.HexArgb), size, dpr)
    cached = _SYMBOLIC_CACHE.get(key)
    if cached is not None:
        return cached
    phys = max(1, round(size * dpr))
    pm = QPixmap(phys, phys)
    pm.fill(Qt.transparent)
    pm.setDevicePixelRatio(dpr)
    painter = QPainter(pm)
    margin = size * 0.12
    _paint_symbolic(painter, kind, QRectF(margin, margin, size - 2 * margin, size - 2 * margin), color)
    painter.end()
    icon = QIcon(pm)
    _SYMBOLIC_CACHE[key] = icon
    return icon


def _theme_icon(names: list[str], standard_pixmap=None) -> QIcon:
    """Resuelve el primer icono de tema disponible entre `names`.

    Si el tema activo (Breeze, Adwaita, hicolor, Fusion...) no trae ninguno
    de esos nombres, se usa como último recurso un QStyle.StandardPixmap
    nativo (siempre disponible, lo dibuja el propio QStyle de la
    plataforma), para que el icono nunca quede vacío.
    """
    for name in names:
        icon = QIcon.fromTheme(name)
        if not icon.isNull():
            return icon
    if standard_pixmap is not None:
        style = QApplication.style()
        if style is not None:
            return style.standardIcon(standard_pixmap)
    return QIcon()


def themed_icon(names: list[str], fallback_kind: str, standard_pixmap=None,
                 color: "QColor | None" = None) -> QIcon:
    """Icono del tema de iconos del sistema (Breeze, Adwaita, hicolor...).

    Intenta resolver el primero de `names`. Si el tema activo no trae
    ninguno, usa un QStyle.StandardPixmap nativo de la plataforma y, como
    último recurso, el glifo symbolic dibujado a mano (`fallback_kind`) para
    que el icono nunca quede vacío.
    """
    icon = _theme_icon(names, standard_pixmap)
    if not icon.isNull():
        return icon
    return symbolic_icon(fallback_kind, color=color)


# ═══════════════════════════════════════════════════════════════════════════════

def _pt(widget, size: int, bold: bool = False):
    f = widget.font()
    f.setPointSize(size)
    if bold:
        f.setBold(True)
    widget.setFont(f)


def _mix(a: QColor, b: QColor, t: float) -> QColor:
    """Interpola linealmente entre dos colores (t=0 -> a, t=1 -> b)."""
    return QColor(
        int(a.red()   + (b.red()   - a.red())   * t),
        int(a.green() + (b.green() - a.green()) * t),
        int(a.blue()  + (b.blue()  - a.blue())   * t),
    )


def _tinted(widget: QWidget, accent: QColor, bg_t: float = 0.20, border_t: float = 0.45):
    """Mezcla un color "acento" semántico (verde/rojo/azul...) con el color
    base REAL del palette del widget, para que el resultado tenga contraste
    correcto tanto en temas claros como oscuros, en vez de asumir un fondo
    oscuro fijo."""
    base = widget.palette().color(QPalette.Base)
    return _mix(base, accent, bg_t), _mix(base, accent, border_t)


def _dot_icon(color: QColor, size: int = 14) -> QIcon:
    """Genera un pequeño icono de punto de color (para chips de categoría
    sin icono de tema disponible). El color en sí es un acento decorativo
    -como el color de una carpeta o una etiqueta-, no "chrome" de la app."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QBrush(color))
    p.setPen(Qt.NoPen)
    p.drawEllipse(1, 1, size - 2, size - 2)
    p.end()
    return QIcon(pm)


# ═══════════════════════════════════════════════════════════════════════════════

# Icono de tema (freedesktop) por cada tipo de "estado"/origen. Se prueban
def _letter_badge_pixmap(letter: str, color: QColor, size: int = 13) -> QPixmap:
    """Círculo relleno con una letra en negrita: usado para las
    insignias de origen (Flatpak/XBPS) en vez de un glifo vectorial.

    A 13px, trazos finos tipo "symbolic" (terminal, caja...) se vuelven
    ilegibles y se confunden con formas genéricas (p.ej. un círculo con
    una línea cruzada). Una letra rellena en negrita se reconoce sin
    ambigüedad a cualquier tamaño pequeño -es el mismo patrón que ya se
    usaba para las insignias F/!/▲ de la tabla de actualizaciones.
    """
    dpr = QGuiApplication.primaryScreen().devicePixelRatio() if QGuiApplication.primaryScreen() else 1.0
    phys = max(1, round(size * dpr))
    pm = QPixmap(phys, phys)
    pm.fill(Qt.transparent)
    pm.setDevicePixelRatio(dpr)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QBrush(color))
    p.setPen(Qt.NoPen)
    p.drawEllipse(0, 0, size, size)
    p.setPen(QColor("#FFFFFF"))
    f = p.font()
    f.setPixelSize(int(size * 0.62))
    f.setBold(True)
    p.setFont(f)
    p.drawText(QRectF(0, 0, size, size), Qt.AlignCenter, letter)
    p.end()
    return pm


_BADGE_LETTERS: dict[str, str] = {
    "flatpak": "F",
    "xbps":    "X",
}

_BADGE_KINDS: dict[str, str] = {
    "installed": "check",
    "update":    "arrow_up",
}

_BADGE_ACCENTS: dict[str, str] = {
    "flatpak":  "#3B82F6",
    "xbps":     "#A3873A",
    "appimage": "#8B5CF6",
    "local":    "#8B5CF6",
    "installed": "#4CAF50",
}


class BadgeLabel(QWidget):
    """Insignia de estado: icono real del tema del sistema + texto.

    Todo (fondo, icono y texto) se dibuja directamente en paintEvent, sin
    QLabel hijos ni layout interno. Esto es a propósito: un QLabel dentro
    de un QHBoxLayout anidado puede quedar recortado a cero por el motor
    de layout si el estilo Qt activo en el sistema del usuario (tema
    GTK/Breeze/qt6ct, etc.) impone su propia métrica de caja sobre los
    hijos; al pintar nosotros mismos el contenido, el texto siempre se ve
    -y si de verdad no hay espacio, se elide con "…" en vez de
    desaparecer."""

    def __init__(self, text: str = "", badge_type: str = "installed",
                 parent=None, tooltip: str = "", show_icon: bool = True):
        super().__init__(parent)
        self._badge_type = badge_type
        self._text = text
        self._icon_pm: QPixmap | None = None
        self._show_icon = show_icon

        f = self.font()
        f.setPointSize(8)
        f.setBold(True)
        self.setFont(f)

        # Se prefiere el tamaño completo (icono + texto sin recortar),
        # pero puede ceder espacio con gracia (elidiendo el texto) si la
        # fila/tarjeta que la contiene de verdad no tiene sitio.
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

        if tooltip:
            self.setToolTip(tooltip)

        self._apply_icon()
        self.updateGeometry()

    def enterEvent(self, event) -> None:
        super().enterEvent(event)
        tip = self.toolTip()
        if tip:
            # Sin el retardo por defecto de Qt (~700ms): al no haber texto
            # visible en la insignia, el tooltip ES la etiqueta, así que
            # debe aparecer al instante al pasar el ratón por encima.
            QToolTip.showText(QCursor.pos(), tip, self)

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        QToolTip.hideText()

    def _apply_icon(self) -> None:
        if not self._show_icon:
            self._icon_pm = None
            return
        if self._badge_type in _BADGE_LETTERS:
            self._icon_pm = _letter_badge_pixmap(
                _BADGE_LETTERS[self._badge_type], self._accent(), size=13
            )
            return
        kind = _BADGE_KINDS.get(self._badge_type)
        if kind is None:
            self._icon_pm = None
            return
        color = self._accent()
        icon = symbolic_icon(kind, color=color, size=13)
        self._icon_pm = icon.pixmap(QSize(13, 13))

    def set_badge_type(self, badge_type: str) -> None:
        self._badge_type = badge_type
        self._apply_icon()
        self.updateGeometry()
        self.update()

    def text(self) -> str:
        return self._text

    def setText(self, text: str) -> None:
        self._text = text
        self.updateGeometry()
        self.update()

    def _accent(self) -> QColor:
        hexcode = _BADGE_ACCENTS.get(self._badge_type)
        return QColor(hexcode) if hexcode else self.palette().color(QPalette.Mid)

    def _content_metrics(self):
        """(icon_w, text_full_w, pad_l, pad_r, gap) usados tanto por
        sizeHint como por paintEvent, para que ambos coincidan siempre."""
        pad_l, pad_r, gap = 8, 8, 4
        icon_w = 13 if self._icon_pm is not None else 0
        fm = self.fontMetrics()
        text_w = fm.horizontalAdvance(self._text) if self._text else 0
        return icon_w, text_w, pad_l, pad_r, gap, fm

    def sizeHint(self) -> QSize:
        icon_w, text_w, pad_l, pad_r, gap, fm = self._content_metrics()
        content = icon_w + (gap + text_w if self._text else 0)
        return QSize(pad_l + content + pad_r, fm.height() + 8)

    def minimumSizeHint(self) -> QSize:
        # Como mínimo absoluto: el icono solo (o, si no hay icono, un
        # par de caracteres elididos) — nunca cero.
        icon_w, _, pad_l, pad_r, gap, fm = self._content_metrics()
        if icon_w:
            return QSize(pad_l + icon_w + pad_r, fm.height() + 8)
        min_text = fm.horizontalAdvance("…")
        return QSize(pad_l + min_text + pad_r, fm.height() + 8)

    def paintEvent(self, _event):
        bg, border = _tinted(self, self._accent(), bg_t=0.16, border_t=0.4)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self.rect().adjusted(0, 0, -1, -1)
        p.setBrush(QBrush(bg))
        p.setPen(QPen(border, 1))
        p.drawRoundedRect(r, 6, 6)

        pad_l, pad_r, gap = 8, 8, 4
        x = pad_l
        cy = self.height() / 2

        if self._icon_pm is not None:
            y = cy - self._icon_pm.height() / 2
            p.drawPixmap(QPointF(x, y), self._icon_pm)
            x += self._icon_pm.width()

        if self._text:
            x += gap if self._icon_pm is not None else 0
            fm = self.fontMetrics()
            avail = self.width() - pad_r - x
            elided = fm.elidedText(self._text, Qt.ElideRight, max(0, avail))
            p.setFont(self.font())
            p.setPen(self.palette().color(self.foregroundRole()))
            ty = cy - fm.height() / 2 + fm.ascent()
            p.drawText(QPointF(x, ty), elided)

        p.end()


class UpdatesBadge(QLabel):
    """Contador numérico con el color de acento nativo del sistema
    (QPalette.Highlight), no un gris fijo."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _pt(self, 8, bold=True)

    def paintEvent(self, _event):
        if not self.text():
            return
        pal = self.palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self.rect().adjusted(0, 1, -1, -1)
        p.setBrush(QBrush(pal.color(QPalette.Highlight)))
        p.setPen(Qt.NoPen)
        rad = r.height() // 2
        p.drawRoundedRect(r, rad, rad)
        p.setPen(QPen(pal.color(QPalette.HighlightedText)))
        p.setFont(self.font())
        p.drawText(r, Qt.AlignCenter, self.text())
        p.end()

    def sizeHint(self) -> QSize:
        fm = self.fontMetrics()
        return QSize(max(fm.horizontalAdvance(self.text()) + 14, 20), max(fm.height() + 4, 18))

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()


# ═══════════════════════════════════════════════════════════════════════════════

STATUS_COLORS: dict[str, str] = {
    "gray":   "#9E9E9E",
    "green":  "#4CAF50",
    "yellow": "#FFC107",
    "red":    "#F44336",
}


class StatusDot(QWidget):
    """Punto circular indicador de estado, para la barra lateral."""

    def __init__(self, parent=None, diameter: int = 10):
        super().__init__(parent)
        self._status = "gray"
        self._diameter = diameter
        self.setFixedSize(diameter, diameter)
        self.setToolTip("")

    def set_status(self, status: str) -> None:
        if status not in STATUS_COLORS:
            status = "gray"
        if status != self._status:
            self._status = status
            self.update()

    def status(self) -> str:
        return self._status

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        color = QColor(STATUS_COLORS.get(self._status, "#9E9E9E"))
        r = self.rect().adjusted(1, 1, -1, -1)
        p.setBrush(QBrush(color))
        p.setPen(QPen(self.palette().color(QPalette.Window), 1))
        p.drawEllipse(r)
        p.end()

    def sizeHint(self) -> QSize:
        return QSize(self._diameter, self._diameter)


# ═══════════════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════════════

# Icono de tema por nivel de estado (nada de "puntos" de color: un icono
# de estado real, como el escudo/check de "Windows Update"). Se intentan
# varios nombres freedesktop y, si el tema activo no trae ninguno, se cae
# a un QStyle.StandardPixmap nativo.
_ALERT_KINDS: dict[str, tuple[str, str]] = {
    # (kind del glifo symbolic, color)
    "gray":   ("check", "#8A8F98"),
    "green":  ("cloud_check", "#4CAF50"),
    "yellow": ("warn", "#E0A82E"),
    "red":    ("warn", "#D9534F"),
}

_ALERT_ICON_SIZE = 30


class AlertBanner(QWidget):
    """Fila de estado tipo "Windows Update": un icono de sistema grande que
    representa el estado (al día / actualizaciones disponibles / error...)
    seguido de un título y un subtítulo, sin caja ni texto de color -el
    icono ya comunica el estado, igual que el escudo verde de Windows
    Update."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._status = "gray"
        self.setObjectName("alertBanner")

        self._icon_lbl = QLabel()
        self._icon_lbl.setFixedSize(_ALERT_ICON_SIZE, _ALERT_ICON_SIZE)
        self._icon_lbl.setScaledContents(True)
        self._icon_lbl.setAlignment(Qt.AlignTop | Qt.AlignHCenter)

        self._title_lbl = QLabel("")
        self._title_lbl.setObjectName("alertBannerTitle")
        _pt(self._title_lbl, 11, bold=True)

        self._msg_lbl = QLabel("")
        self._msg_lbl.setObjectName("alertBannerMessage")
        self._msg_lbl.setWordWrap(True)
        _pt(self._msg_lbl, 9)
        # Subtítulo en gris atenuado (como "Last checked: ...") en vez de
        # heredar el color de estado: el color ya no vive en el texto.
        pal = self._msg_lbl.palette()
        muted = _mix(pal.color(QPalette.WindowText), pal.color(QPalette.Window), 0.45)
        pal.setColor(QPalette.WindowText, muted)
        pal.setColor(QPalette.Text, muted)
        self._msg_lbl.setPalette(pal)

        text_col = QWidget()
        text_lay = QVBoxLayout(text_col)
        text_lay.setContentsMargins(0, 0, 0, 0)
        text_lay.setSpacing(2)
        text_lay.addWidget(self._title_lbl)
        text_lay.addWidget(self._msg_lbl)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 6, 2, 6)
        lay.setSpacing(12)
        lay.addWidget(self._icon_lbl, alignment=Qt.AlignTop)
        lay.addWidget(text_col, stretch=1)

    def set_alert(self, status: str, title: str, message: str = "") -> None:
        if status not in _ALERT_KINDS:
            status = "gray"
        self._status = status
        kind, color_hex = _ALERT_KINDS[status]
        icon = symbolic_icon(kind, color=QColor(color_hex), size=_ALERT_ICON_SIZE)
        self._icon_lbl.setPixmap(icon.pixmap(QSize(_ALERT_ICON_SIZE, _ALERT_ICON_SIZE)))
        self._title_lbl.setText(title)
        self._msg_lbl.setText(message)
        self._msg_lbl.setVisible(bool(message))


class MultilineElideLabel(QLabel):
    """QLabel que envuelve el texto hasta `max_lines` líneas y elide con
    "…" la línea que se pase de ancho, usando QTextLayout (el mecanismo
    nativo de Qt para esto), en vez de dejar que wordWrap desborde una
    3ª línea a medias que el recorte de altura fija terminaba "sangrando"
    sobre los widgets de abajo."""

    def __init__(self, text: str = "", max_lines: int = 2, parent=None):
        super().__init__(parent)
        self._full_text = text
        self._max_lines = max_lines
        self.setWordWrap(False)   # el wrap real lo hace _relayout(), no Qt
        fm = self.fontMetrics()
        self.setFixedHeight(fm.lineSpacing() * max_lines + 2)

    def setText(self, text: str) -> None:
        self._full_text = text
        self.setToolTip(text)
        super().setText("")
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(self.palette().color(self.foregroundRole()))
        p.setFont(self.font())

        fm = self.fontMetrics()
        line_height = fm.lineSpacing()
        width = max(1, self.width())

        layout = QTextLayout(self._full_text, self.font())
        layout.setTextOption(QTextOption(Qt.AlignLeft))
        layout.beginLayout()

        y = 0.0
        for line_no in range(self._max_lines):
            line = layout.createLine()
            if not line.isValid():
                break
            is_last_allowed = line_no == self._max_lines - 1
            line.setLineWidth(width)

            if is_last_allowed:
                # ¿Sobra texto tras esta línea? -> elidir con "…"
                next_start = line.textStart() + line.textLength()
                if next_start < len(self._full_text):
                    elided = fm.elidedText(
                        self._full_text[line.textStart():], Qt.ElideRight, width
                    )
                    p.drawText(QPointF(0, y + fm.ascent()), elided)
                    y += line_height
                    break

            line.draw(p, QPointF(0, y))
            y += line_height

        layout.endLayout()
        p.end()

    def sizeHint(self) -> QSize:
        fm = self.fontMetrics()
        return QSize(self.width(), fm.lineSpacing() * self._max_lines + 2)


# ═══════════════════════════════════════════════════════════════════════════════

class CardFrame(QFrame):
    """Tarjeta con borde dibujado a mano.

    Antes usaba QFrame.StyledPanel (borde "nativo" del QStyle activo)
    para el contorno. En estilos nativos minimalistas (p.ej. muchos
    entornos Linux sin un QStyle "completo" instalado) StyledPanel no
    pinta ningún borde visible, así que las tarjetas se veían "flotando"
    sin contorno. Ahora el borde se dibuja explícitamente en paintEvent,
    así siempre es visible sin depender de qué QStyle esté activo.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_Hover, True)
        self._hover = False

    def event(self, e: QEvent) -> bool:
        t = e.type()
        if t == QEvent.HoverEnter:
            self._hover = True
            self.update()
        elif t == QEvent.HoverLeave:
            self._hover = False
            self.update()
        return super().event(e)

    def paintEvent(self, _event):
        pal = self.palette()
        base = pal.color(QPalette.AlternateBase if self._hover else QPalette.Base)
        # Borde con contraste suficiente sobre el fondo en cualquier
        # tema claro/oscuro: se mezcla el color de texto con el de
        # fondo en vez de un gris fijo que podría desaparecer en según
        # qué paleta.
        border = _mix(pal.color(QPalette.WindowText), base, 0.55 if not self._hover else 0.35)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QBrush(base))
        p.setPen(QPen(border, 1.2))
        p.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 10, 10)
        p.end()


class FeaturedBannerFrame(QFrame):
    """Banner destacado: mismo panel nativo que CardFrame, con un ligero
    tinte del color de acento del sistema (QPalette.Highlight) para que
    resalte sin recurrir a colores fijos."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.StyledPanel)
        self.setFrameShadow(QFrame.Raised)

    def paintEvent(self, _event):
        pal = self.palette()
        bg, border = _tinted(self, pal.color(QPalette.Highlight), bg_t=0.10, border_t=0.30)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QBrush(bg))
        p.setPen(QPen(border, 1))
        p.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 12, 12)
        p.end()


# ═══════════════════════════════════════════════════════════════════════════════

class SidebarWidget(QWidget):
    """Barra lateral nativa, con una línea divisoria vertical que la separa
    del contenido. Se usa QPalette.Dark (más contraste que Mid en la
    mayoría de temas, tanto claros como oscuros) y se dibuja 1px hacia
    dentro del borde (r.right() - 1) porque en r.right() el trazo cae
    justo en el límite del widget y algunos backends de plataforma lo
    recortan, dejando la línea invisible."""

    def paintEvent(self, _event):
        p = QPainter(self)
        r = self.rect()
        pal = self.palette()
        p.fillRect(r, pal.color(QPalette.Window))
        p.setPen(QPen(pal.color(QPalette.Dark), 1))
        x = r.right() - 1
        p.drawLine(x, r.top(), x, r.bottom())
        p.end()


class TopBarWidget(QWidget):
    """Barra superior nativa, con borde inferior del color "mid" del palette."""

    def paintEvent(self, _event):
        p = QPainter(self)
        r = self.rect()
        pal = self.palette()
        p.fillRect(r, pal.color(QPalette.Window))
        p.setPen(QPen(pal.color(QPalette.Mid), 1))
        p.drawLine(r.left(), r.bottom(), r.right(), r.bottom())
        p.end()


# ═══════════════════════════════════════════════════════════════════════════════

class NavButton(QPushButton):
    """Botón de navegación de la barra lateral. Nativo y marcable
    (checkable) para indicar la página activa; el propio QStyle pinta el
    estado "checked"/"hover" con los colores del tema del sistema."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(36)
        _pt(self, 11)
        # Microtransición discreta: da respuesta al pasar el puntero sin
        # mover el layout ni provocar saltos en la barra superior.
        self._hover_effect = QGraphicsOpacityEffect(self)
        self._hover_effect.setOpacity(0.92)
        self.setGraphicsEffect(self._hover_effect)
        self._hover_anim = QPropertyAnimation(self._hover_effect, b"opacity", self)
        self._hover_anim.setDuration(140)
        self._hover_anim.setEasingCurve(QEasingCurve.OutCubic)

    def _animate_hover(self, opacity: float) -> None:
        self._hover_anim.stop()
        self._hover_anim.setStartValue(self._hover_effect.opacity())
        self._hover_anim.setEndValue(opacity)
        self._hover_anim.start()

    def enterEvent(self, event):
        self._animate_hover(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._animate_hover(0.92)
        super().leaveEvent(event)


class ChipNavButton(QPushButton):
    """Flechas de navegación del carrusel.

    Antes usaba los caracteres "‹"/"›" como `text()` del botón, pintados
    por el QStyle activo con QPalette.ButtonText. Eso funcionaba con el
    QSS propio de CSDS (que fuerza color: #ebebeb), pero en los temas
    "auto"/"breeze"/"fusion" -- que llaman a app.setStyleSheet("") y
    dejan el color en manos de la paleta nativa del sistema -- el glifo
    podía salir invisible (mismo tono que el fondo del botón, o un
    ButtonText que el tema nativo no pinta para botones "flat"). Por eso
    ahora se dibuja a mano con QPainter, igual que el resto de iconos
    "symbolic" de este módulo: color explícito tomado de la paleta,
    garantizado visible bajo cualquier tema."""

    def __init__(self, direction: str = "left", *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setCursor(Qt.PointingHandCursor)
        self.setFlat(True)
        self.setText("")
        self._kind = "chevron_left" if direction == "left" else "chevron_right"

    def paintEvent(self, _event):
        # Deja que el QStyle pinte fondo/hover/pressed como siempre
        # (respeta el tema activo), pero sin texto -- el glifo lo
        # dibujamos nosotros encima con color garantizado.
        super().paintEvent(_event)
        pal = self.palette()
        color = pal.color(QPalette.Highlight) if self.underMouse() else pal.color(QPalette.ButtonText)
        if color.alpha() == 0 or color == pal.color(self.backgroundRole()):
            color = pal.color(QPalette.WindowText)
        painter = QPainter(self)
        rect = QRectF(self.rect())
        _paint_symbolic(painter, self._kind, rect, color)


class ChipButton(QPushButton):
    """Botón tipo "chip"/filtro, marcable. Si se le da un `accent`, se usa
    solo como color decorativo de un icono de punto (igual que el color de
    una etiqueta), nunca como fondo del propio botón — el fondo, borde y
    estados hover/checked los sigue pintando el QStyle nativo."""

    def __init__(self, text: str = "", icon_name: str = "", accent: str = "",
                 radius: int | None = None, parent=None):
        super().__init__(text, parent)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        _pt(self, 10)
        icon = QIcon.fromTheme(icon_name) if icon_name else QIcon()
        if icon.isNull() and accent:
            icon = _dot_icon(QColor(accent))
        if not icon.isNull():
            self.setIcon(icon)
            self.setIconSize(QSize(14, 14))


class InstallButton(QPushButton):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setCursor(Qt.PointingHandCursor)
        _pt(self, 11, bold=True)
        self.setMinimumWidth(110)
        self.setMinimumHeight(34)
        icon = QIcon.fromTheme("list-add", QIcon.fromTheme("download"))
        if not icon.isNull():
            self.setIcon(icon)


class RemoveButton(QPushButton):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setCursor(Qt.PointingHandCursor)
        _pt(self, 11, bold=True)
        self.setMinimumWidth(80)
        self.setMinimumHeight(34)
        icon = QIcon.fromTheme("edit-delete", QIcon.fromTheme("list-remove"))
        if not icon.isNull():
            self.setIcon(icon)


class UpdateButton(QPushButton):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setCursor(Qt.PointingHandCursor)
        _pt(self, 11, bold=True)
        self.setMinimumWidth(110)
        self.setMinimumHeight(34)
        icon = QIcon.fromTheme("view-refresh", QIcon.fromTheme("system-software-update"))
        if not icon.isNull():
            self.setIcon(icon)


class BackButton(QPushButton):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setCursor(Qt.PointingHandCursor)
        _pt(self, 10)
        self.setMinimumHeight(30)
        icon = QIcon.fromTheme("go-previous")
        if not icon.isNull():
            self.setIcon(icon)


class TaskCancelButton(QPushButton):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setCursor(Qt.PointingHandCursor)
        _pt(self, 10)
        self.setFlat(True)


# ═══════════════════════════════════════════════════════════════════════════════

class NativeProgressBar(QProgressBar):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setTextVisible(False)


# ═══════════════════════════════════════════════════════════════════════════════
# Utilidades e icono/botón de categoría compartidos entre HomePage (grid
# 3x2 de "tiles" antes de "Explorar apps") y cualquier otro sitio que
# necesite un icono de tema o el mismo estilo de botón coloreado.

def find_theme_icon(names: list[str]) -> QIcon:
    for name in names:
        icon = QIcon.fromTheme(name)
        if not icon.isNull():
            return icon
    return QIcon()


def mix_color(hex_color: str, factor: float) -> str:
    """Aclara (factor>0) u oscurece (factor<0) un color hex ~factor."""
    c = QColor(hex_color)
    if factor >= 0:
        c = c.lighter(100 + int(factor * 100))
    else:
        c = c.darker(100 + int(-factor * 100))
    return c.name()


def svg_icon(svg_markup: str, size: int = 26) -> QIcon:
    """Renderiza un SVG embebido (string) a un QIcon cuadrado de `size`
    px, con fondo transparente. Se usa para los iconos de categoría:
    dibujados a mano en vez de depender de iconos de tema (que pueden
    no existir o venir en otro color según el tema de iconos del
    sistema)."""
    from PySide6.QtSvg import QSvgRenderer
    renderer = QSvgRenderer(bytearray(svg_markup, encoding="utf-8"))
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    p = QPainter(pixmap)
    renderer.render(p)
    p.end()
    return QIcon(pixmap)


def recolor_icon(icon: QIcon, color: QColor, size: int = 28) -> QIcon:
    """Repinta un icono de tema a un color sólido (aplicado sobre su
    canal alfa), para que sea legible sobre cualquier fondo de color
    sin depender de si el tema de iconos activo lo dibuja oscuro o
    claro."""
    if icon.isNull():
        return icon
    pixmap = icon.pixmap(QSize(size, size))
    if pixmap.isNull():
        return icon
    result = QPixmap(pixmap.size())
    result.fill(Qt.transparent)
    p = QPainter(result)
    p.drawPixmap(0, 0, pixmap)
    p.setCompositionMode(QPainter.CompositionMode_SourceIn)
    p.fillRect(result.rect(), color)
    p.end()
    return QIcon(result)


# Direcciones de degradado distintas para que cada tile tenga un
# diseño un poco propio además de su propia pareja de colores (se
# eligen cíclicamente por índice desde CategoryTileButton).
_GRADIENT_DIRECTIONS = [
    (0, 0, 1, 1),   # diagonal ↘
    (0, 1, 1, 0),   # diagonal ↗
    (0, 0, 0, 1),   # vertical
    (1, 0, 0, 1),   # diagonal ↙
]


class CategoryPill(QLabel):
    """Rótulo en forma de píldora (rectángulo muy redondeado) con el
    degradado propio de una categoría — usado en la cabecera de la
    página de categoría para indicar en cuál estamos, centrado sobre
    el grid."""

    def __init__(self, text: str, colors: tuple[str, str] = ("#6366F1", "#4347B0"), parent=None):
        super().__init__(text, parent)
        self.setObjectName("categoryPill")
        self.setAlignment(Qt.AlignCenter)
        f = self.font()
        f.setBold(True)
        f.setPointSize(11)
        self.setFont(f)
        self.setMinimumHeight(34)
        self.set_category(text, colors)

    def set_category(self, text: str, colors: tuple[str, str]) -> None:
        self.setText(text)
        c1, c2 = colors
        self.setStyleSheet(
            f"QLabel#categoryPill {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, "
            f"stop:0 {c1}, stop:1 {c2}); color: #FFFFFF; border-radius: 17px; "
            f"padding: 6px 22px; }}"
        )


def attach_hover_lift(widget: QWidget, max_blur: float = 26.0) -> None:
    """Sombra suave que crece/encoje con una animación corta al pasar
    el mouse — un detalle ligero de "elevación" para tarjetas y tiles,
    sin tocar el layout (no mueve ni redimensiona el widget)."""
    effect = QGraphicsDropShadowEffect(widget)
    effect.setColor(QColor(0, 0, 0, 90))
    effect.setOffset(0, 2)
    effect.setBlurRadius(0)
    widget.setGraphicsEffect(effect)

    anim = QPropertyAnimation(effect, b"blurRadius", widget)
    anim.setDuration(150)
    anim.setEasingCurve(QEasingCurve.OutCubic)
    widget._hover_lift_anim = anim
    widget._hover_lift_effect = effect

    def _animate_to(value: float):
        anim.stop()
        anim.setStartValue(effect.blurRadius())
        anim.setEndValue(value)
        anim.start()

    orig_enter = widget.enterEvent
    orig_leave = widget.leaveEvent

    def _enter(event):
        _animate_to(max_blur)
        orig_enter(event)

    def _leave(event):
        _animate_to(0.0)
        orig_leave(event)

    widget.enterEvent = _enter
    widget.leaveEvent = _leave


class CategoryTileButton(QPushButton):
    """Tile con degradado propio por categoría (icono y texto siempre
    centrados, icono repintado en blanco para que se vea igual con
    cualquier tema de iconos claro u oscuro), como los grupos Create/
    Work/Play/Socialise/Learn/Develop de GNOME Software. No es
    "checkable": simplemente navega a la categoría al pulsarla."""

    def __init__(self, icon_svg: str, label: str, colors: tuple[str, str] = ("#6366F1", "#4347B0"),
                 index: int = 0, parent=None):
        super().__init__(parent)
        self.setObjectName("catTileBtn")
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(76)
        self.setToolTip(label)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.setText("")  # el contenido se dibuja con labels internos centrados

        v = QVBoxLayout(self)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(6)
        v.setAlignment(Qt.AlignCenter)

        icon_lbl = QLabel()
        icon_lbl.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        icon_lbl.setStyleSheet("background: transparent; border: none;")
        icon_lbl.setAlignment(Qt.AlignCenter)
        icon = svg_icon(icon_svg, 28)
        if not icon.isNull():
            icon_lbl.setPixmap(icon.pixmap(QSize(28, 28)))
        v.addWidget(icon_lbl, alignment=Qt.AlignHCenter)

        text_lbl = QLabel(label)
        text_lbl.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        text_lbl.setAlignment(Qt.AlignCenter)
        text_lbl.setStyleSheet("background: transparent; border: none; color: #FFFFFF;")
        tf = text_lbl.font()
        tf.setPointSize(11)
        tf.setBold(True)
        text_lbl.setFont(tf)
        v.addWidget(text_lbl, alignment=Qt.AlignHCenter)

        self.setStyleSheet(self._style_for(colors, index))
        attach_hover_lift(self, max_blur=22.0)

    def _style_for(self, colors: tuple[str, str], index: int) -> str:
        c1, c2 = colors
        x1, y1, x2, y2 = _GRADIENT_DIRECTIONS[index % len(_GRADIENT_DIRECTIONS)]
        h1, h2 = mix_color(c1, 0.14), mix_color(c2, 0.14)
        p1, p2 = mix_color(c1, -0.14), mix_color(c2, -0.14)
        grad = (f"x1:{x1}, y1:{y1}, x2:{x2}, y2:{y2}, "
                f"stop:0 {{c1}}, stop:1 {{c2}}")
        return (
            f"QPushButton#catTileBtn {{ background: qlineargradient({grad.format(c1=c1, c2=c2)}); "
            f"color: #FFFFFF; border: none; border-radius: 14px; }}"
            f"QPushButton#catTileBtn:hover {{ background: qlineargradient({grad.format(c1=h1, c2=h2)}); }}"
            f"QPushButton#catTileBtn:pressed {{ background: qlineargradient({grad.format(c1=p1, c2=p2)}); }}"
        )
