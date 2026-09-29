# Yelena Software, Updates Page

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from typing import Optional

from PySide6.QtCore import Qt, QTimer, QSize, QPointF, QRectF, Slot, Signal, QObject
from PySide6.QtGui import (
    QFont, QColor, QIcon, QPixmap, QPainter, QBrush, QPen,
    QLinearGradient, QPainterPath,
)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QProgressBar, QTextEdit, QFrame, QMessageBox, QDialog,
    QStyle, QStackedWidget, QScrollArea,
)

from core.i18n import tr
import core.settings as app_settings
from backend import notifier
from backend.update_status import update_status
from backend.models import Package, PackageSource
from backend.blacklist import is_blacklisted
from backend import xbps_error_handler as _xbps_err
from .native_widgets import AlertBanner, symbolic_icon, themed_icon

# Backend imports
try:
    from backend.updates_db import updates_db as _updates_db
    from backend import xbps_backend as _xbps_be
    from backend import repo_sync as _repo_sync
    from backend.flatpak_backend import is_flatpak_available, get_flatpak_updates
    from backend.system_detect import detect_package_manager
    _BACKENDS_OK = True
except ImportError as _e:
    print(f"[Updates] Backend import error: {_e}")
    _BACKENDS_OK = False
    _updates_db = None
    _xbps_be = None
    _repo_sync = None

    def is_flatpak_available():
        return False

    def get_flatpak_updates():
        return []

    def detect_package_manager():
        return "unknown"


# Helpers

def _format_size(size_bytes: int) -> str:
    if not size_bytes or size_bytes == 0:
        return "—"
    for unit in ["B", "KB", "MB", "GB"]:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f} {unit}" if unit != "B" else f"{size_bytes} B"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} TB"


def _detect_pkg_mgr() -> str:
    if not _BACKENDS_OK:
        return "unknown"
    return detect_package_manager()


try:
    from backend.agent_yelena import build_privileged_command as _build_priv_cmd
    _HAS_POLKIT_HELPER = True
except ImportError:
    _HAS_POLKIT_HELPER = False
    _build_priv_cmd = None


def _build_root_cmd(cmd_list: list[str]) -> list[str]:
    """Construye un comando XBPS privilegiado vía agent_yelena."""
    if not cmd_list:
        return cmd_list
    if _HAS_POLKIT_HELPER and _build_priv_cmd is not None:
        return _build_priv_cmd(cmd_list[0], cmd_list[1:])
    raise RuntimeError("agent-yelena no está disponible; se rechaza el fallback inseguro")


# Parser de progreso de paquetes (best-effort, para la vista grafica de
# instalacion). Si una linea no encaja con ningun patron conocido, se
# ignora sin mas: la terminal emulada sigue mostrando la salida completa.

_badge_icon_cache: dict = {}


def _make_badge_icon(glyph: str, bg_hex: str, size: int = 18) -> QIcon:
    """Icono circular con un glifo, dibujado a mano (no depende del
    tema de iconos del sistema, asi se ve igual en cualquier equipo)."""
    key = (glyph, bg_hex, size)
    cached = _badge_icon_cache.get(key)
    if cached is not None:
        return cached
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QBrush(QColor(bg_hex)))
    p.setPen(Qt.NoPen)
    p.drawEllipse(0, 0, size, size)
    p.setPen(QColor("#FFFFFF"))
    f = QFont()
    f.setPixelSize(int(size * 0.55))
    f.setBold(True)
    p.setFont(f)
    p.drawText(pix.rect(), Qt.AlignCenter, glyph)
    p.end()
    icon = QIcon(pix)
    _badge_icon_cache[key] = icon
    return icon


# Qt signals helper

# Fila de actualización estilo tarjeta (icono + nombre, versiones
# apiladas, tipo, arch, checkbox) — reemplaza la vieja fila de
# QTableWidget para acercarse al diagrama: cada paquete es un bloque,
# no una fila de grilla plana.
class UpdateRow(QFrame):
    toggled = Signal()

    def __init__(self, upd: dict, locked: bool, is_crit: bool, parent=None):
        super().__init__(parent)
        self.upd = upd
        self.locked = locked
        self.is_crit = is_crit
        self.setObjectName("updateCard")
        self.setCursor(Qt.PointingHandCursor if not locked else Qt.ArrowCursor)
        # Sin huecos entre filas (ver _rows_layout.setSpacing(0)): se añade
        # una línea divisoria fina en vez de separación, para que la lista
        # se lea como un bloque único y continuo en vez de tarjetas sueltas.
        self.setStyleSheet(
            "QFrame#updateCard { border: none; border-bottom: 1px solid #3F3F46; }"
        )

        root = QHBoxLayout(self)
        root.setContentsMargins(12, 8, 12, 8)
        root.setSpacing(12)

        try:
            from .icon_widget import AppIconWidget
            # icon_name viene del backend (alias conocido o nombre del
            # paquete tal cual, ver _guess_icon / app-id de flatpak); si el
            # tema activo no tiene ese icono concreto, AppIconWidget cae
            # sola al icono genérico de paquete y, en último caso, a la
            # inicial con degradado — nunca queda vacío.
            icon = AppIconWidget(
                upd.get("name", "?"),
                icon_name=upd.get("icon_name", "") or "",
                size=32, radius=8,
            )
        except Exception:
            icon = QLabel("")
            icon.setFixedSize(32, 32)
        root.addWidget(icon)

        name_lbl = QLabel(upd.get("name", "Unknown"))
        name_lbl.setObjectName("cardAppName")
        if is_crit:
            f = name_lbl.font()
            f.setBold(True)
            name_lbl.setFont(f)
            name_lbl.setStyleSheet("color: #EF9A9A;")
        elif locked:
            name_lbl.setStyleSheet("color: #888888;")
        root.addWidget(name_lbl, stretch=1)

        # Versiones apiladas: actual arriba, nueva abajo (una sola
        # columna visual, como en el diagrama).
        ver_box = QVBoxLayout()
        ver_box.setSpacing(0)
        cur = upd.get("current_version", "") or "—"
        if cur in ("Installed", "installed", "-", ""):
            cur = "—"
        new_ver = upd.get("new_version", "Available")
        if new_ver == "Available" and upd.get("current_version"):
            new_ver = tr("updates_available_label")
        cur_lbl = QLabel(cur)
        cur_lbl.setObjectName("sectionSub")
        new_lbl = QLabel(new_ver)
        new_lbl.setStyleSheet(
            "color: #888888;" if locked else "color: #A5D6A7;"
        )
        ver_box.addWidget(cur_lbl)
        ver_box.addWidget(new_lbl)
        ver_wrap = QWidget()
        ver_wrap.setLayout(ver_box)
        ver_wrap.setFixedWidth(110)
        root.addWidget(ver_wrap)

        # Tipo: un solo icono por fila (seguridad / normal / flatpak)
        pkg_type_raw = upd.get("type", "unknown")
        if pkg_type_raw == "flatpak":
            badge_icon = _make_badge_icon("F", "#1ABC9C")
            type_label = tr("updates_type_flatpak")
        elif is_crit:
            badge_icon = _make_badge_icon("!", "#D9534F")
            type_label = tr("updates_type_security")
        else:
            badge_icon = _make_badge_icon("▲", "#4A90E2")
            type_label = tr("updates_type_normal")
        type_lbl = QLabel()
        type_lbl.setPixmap(badge_icon.pixmap(18, 18))
        type_lbl.setToolTip(type_label)
        type_lbl.setFixedWidth(28)
        type_lbl.setAlignment(Qt.AlignCenter)
        root.addWidget(type_lbl)

        arch_lbl = QLabel(upd.get("arch", "") or "")
        arch_lbl.setFixedWidth(64)
        arch_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        if locked:
            arch_lbl.setStyleSheet("color: #888888;")
        root.addWidget(arch_lbl)

        tip = tr("updates_col_tooltip_type").format(type=type_label)
        if locked:
            tip = f"{tip}\n{tr('updates_critical_lock_tooltip')}"
        self.setToolTip(tip)

    def is_checked(self) -> bool:
        return True

    def mousePressEvent(self, event):  # noqa: N802 (Qt override)
        super().mousePressEvent(event)


class _Signals(QObject):
    append_output = Signal(str)
    set_status    = Signal(str)
    op_done       = Signal(bool, str, str)
    pulse_start   = Signal()
    pulse_stop    = Signal()
    update_list   = Signal(list)


# Small native-painted status dot (no QSS): just a filled circle whose
# color is set via setColor(). Replaces the old "background-color: ...;
# border-radius: ..." inline stylesheet with a plain QPainter paintEvent.
class _StatusDot(QWidget):
    def __init__(self, color: QColor, parent=None):
        super().__init__(parent)
        self._color = color
        self.setFixedSize(8, 8)

    def setColor(self, color: QColor) -> None:
        self._color = color
        self.update()

    def paintEvent(self, event):  # noqa: N802 (Qt override)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        painter.setBrush(self._color)
        painter.drawEllipse(self.rect())


# Pantalla de carga a pantalla completa para la tabla de actualizaciones:
# no se muestra ninguna fila hasta que la tabla termina de llenarse por
# completo (ver _process_row_queue). El texto animado con puntos deja
# claro que sigue en curso sin trabar la GUI (se sigue insertando en
# tandas detrás de escena).
class _TableLoadingScreen(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignCenter)
        lay.setSpacing(6)

        self._lbl = QLabel(tr("updates_table_loading_tooltip"))
        self._lbl.setObjectName("loadingTitle")
        self._lbl.setAlignment(Qt.AlignCenter)
        lay.addWidget(self._lbl)

        self._sub_lbl = QLabel("")
        self._sub_lbl.setObjectName("sectionSub")
        self._sub_lbl.setAlignment(Qt.AlignCenter)
        lay.addWidget(self._sub_lbl)

        self._dot = 0
        self._timer = QTimer(self)
        self._timer.setInterval(180)
        self._timer.timeout.connect(self._tick)

    def set_progress(self, loaded: int, remaining: int) -> None:
        self._sub_lbl.setText(
            tr("updates_table_loading_progress", loaded=loaded, remaining=remaining)
        )

    def _tick(self):
        dots = "." * (self._dot % 4)
        base = tr("updates_table_loading_tooltip").rstrip(".…")
        self._lbl.setText(base + dots)
        self._dot += 1

    def showEvent(self, event):
        super().showEvent(event)
        self._dot = 0
        self._lbl.setText(tr("updates_table_loading_tooltip"))
        self._sub_lbl.setText("")
        self._timer.start()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._timer.stop()


def _format_last_checked(ts: float) -> str:
    """Texto relativo ('hace N min') a partir de un timestamp epoch. 0/None
    significa que todavía no se comprobó nunca en esta instalación."""
    if not ts:
        return tr("updates_last_checked_never")
    delta = max(0, int(time.time() - ts))
    if delta < 60:
        return tr("updates_last_checked_now")
    minutes = delta // 60
    if minutes < 60:
        return tr("updates_last_checked_minutes", n=minutes)
    hours = minutes // 60
    return tr("updates_last_checked_hours", n=hours)


# Ilustración "al día": sol + nubes + círculo con check, dibujada a mano
# (sin depender de assets externos) para que se vea igual en cualquier
# tema/equipo, igual que el resto de los iconos de esta página.
class _UpToDateIllustration(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(150, 150)

    def paintEvent(self, event):  # noqa: N802 (Qt override)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        cx, cy, r = 75, 75, 62

        circ_grad = QLinearGradient(cx - r, cy - r, cx + r, cy + r)
        circ_grad.setColorAt(0.0, QColor("#2F9E62"))
        circ_grad.setColorAt(1.0, QColor("#8BD450"))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(circ_grad))
        p.drawEllipse(QPointF(cx, cy), r, r)

        check = QPainterPath()
        check.moveTo(cx - 28, cy + 2)
        check.lineTo(cx - 7, cy + 24)
        check.lineTo(cx + 31, cy - 22)
        pen = QPen(QColor("#DFF7E6"), 9, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawPath(check)


class _UpToDatePage(QWidget):
    """Pantalla de pantalla completa que reemplaza la lista cuando no hay
    absolutamente ninguna actualización pendiente (ni normales ni de
    sistema): ilustración + título + hace cuánto se comprobó por última
    vez. Sustituye al antiguo mensaje discreto del badge."""

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignCenter)
        lay.setSpacing(4)

        lay.addWidget(_UpToDateIllustration(), alignment=Qt.AlignHCenter)
        lay.addSpacing(12)

        self._title = QLabel(tr("updates_up_to_date"))
        title_font = self._title.font()
        title_font.setPointSize(title_font.pointSize() + 5)
        title_font.setBold(True)
        self._title.setFont(title_font)
        self._title.setAlignment(Qt.AlignCenter)
        lay.addWidget(self._title)

        self._subtitle = QLabel("")
        self._subtitle.setObjectName("sectionSub")
        self._subtitle.setAlignment(Qt.AlignCenter)
        lay.addWidget(self._subtitle)

        self._timer = QTimer(self)
        self._timer.setInterval(30_000)
        self._timer.timeout.connect(self._refresh_subtitle)

    def _refresh_subtitle(self):
        ts = 0.0
        try:
            ts = float(app_settings.get("last_update_check", "0") or "0")
        except ValueError:
            ts = 0.0
        self._subtitle.setText(_format_last_checked(ts))

    def showEvent(self, event):
        super().showEvent(event)
        self._refresh_subtitle()
        self._timer.start()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._timer.stop()


# Spinner "radar" dibujado a partir de SVG (QSvgRenderer + rotación en
# paintEvent) en vez de un QMovie/GIF: así se ve nítido en cualquier
# escala de pantalla y no depende de ningún asset externo, igual que el
# resto de iconos dibujados a mano de esta página.
class _SearchingSvgSpinner(QWidget):
    _SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
      <circle cx="50" cy="50" r="40" fill="none" stroke="#3F3F46" stroke-width="9"/>
      <path d="M 50 10 A 40 40 0 0 1 88 60" fill="none"
            stroke="#8B5CF6" stroke-width="9" stroke-linecap="round"/>
    </svg>"""

    def __init__(self, size: int = 96, parent=None):
        super().__init__(parent)
        self._size = size
        self.setFixedSize(size, size)
        self._renderer = QSvgRenderer(bytearray(self._SVG), self)
        self._angle = 0

        self._timer = QTimer(self)
        self._timer.setInterval(16)  # ~60 fps
        self._timer.timeout.connect(self._tick)

    def _tick(self):
        self._angle = (self._angle + 6) % 360
        self.update()

    def showEvent(self, event):
        super().showEvent(event)
        self._angle = 0
        self._timer.start()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._timer.stop()

    def paintEvent(self, event):  # noqa: N802 (Qt override)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        center = self._size / 2
        painter.translate(center, center)
        painter.rotate(self._angle)
        painter.translate(-center, -center)
        self._renderer.render(painter, QRectF(0, 0, self._size, self._size))


class _SearchingPage(QWidget):
    """Pantalla de pantalla completa mostrada mientras se sincronizan
    los repositorios y se comprueba si hay actualizaciones (ver
    _refresh_updates). Antes, durante ese tiempo, no había ningún
    indicador central: solo la barra de estado inferior. Se reemplaza
    por la pantalla "al día" o por la lista de paquetes en cuanto
    _display() recibe el resultado de la búsqueda."""

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignCenter)
        lay.setSpacing(4)

        lay.addWidget(_SearchingSvgSpinner(), alignment=Qt.AlignHCenter)
        lay.addSpacing(16)

        self._title = QLabel(tr("updates_searching"))
        title_font = self._title.font()
        title_font.setPointSize(title_font.pointSize() + 5)
        title_font.setBold(True)
        self._title.setFont(title_font)
        self._title.setAlignment(Qt.AlignCenter)
        lay.addWidget(self._title)

        self._subtitle = QLabel(tr("updates_searching_subtitle"))
        self._subtitle.setObjectName("sectionSub")
        self._subtitle.setAlignment(Qt.AlignCenter)
        lay.addWidget(self._subtitle)

    def showEvent(self, event):
        super().showEvent(event)
        # Se retraducen por si el idioma cambió mientras la página no
        # estaba visible.
        self._title.setText(tr("updates_searching"))
        self._subtitle.setText(tr("updates_searching_subtitle"))


# Tarjeta "superpaquete": cuando hay más de _SYSTEM_INLINE_LIMIT
# actualizaciones de sistema/CuerdOS ocultas, en vez de listarlas sueltas
# (o de solo avisar con una línea de texto) se agrupan en una sola
# tarjeta grande, con su propio icono y botón "Ver detalles" que abre la
# base de datos de paquetes filtrados.
_SYSTEM_INLINE_LIMIT = 15


class _SuperpackageCard(QFrame):
    view_details = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("superpackageCard")
        self.setStyleSheet(
            "QFrame#superpackageCard { background: rgba(97, 189, 149, 0.12); "
            "border: 1px solid rgba(97, 189, 149, 0.45); border-radius: 10px; }"
        )
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(12)

        icon_lbl = QLabel()
        icon_lbl.setPixmap(_make_badge_icon("⟳", "#61BD95", size=34).pixmap(34, 34))
        lay.addWidget(icon_lbl)

        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        self._title_lbl = QLabel(tr("updates_superpackage_title"))
        f = self._title_lbl.font()
        f.setBold(True)
        self._title_lbl.setFont(f)
        text_col.addWidget(self._title_lbl)
        self._subtitle_lbl = QLabel("")
        self._subtitle_lbl.setObjectName("sectionSub")
        self._subtitle_lbl.setWordWrap(True)
        text_col.addWidget(self._subtitle_lbl)
        lay.addLayout(text_col, stretch=1)

        self._btn = QPushButton(tr("updates_view_details"))
        self._btn.setCursor(Qt.PointingHandCursor)
        self._btn.clicked.connect(self.view_details.emit)
        lay.addWidget(self._btn)

    def set_count(self, total: int) -> None:
        self._subtitle_lbl.setText(tr("updates_superpackage_subtitle", total=total))


# ProcessManager

class _ProcessManager:
    def __init__(self):
        self.current_process: Optional[subprocess.Popen] = None
        self.cancelled = False
        self.lock = threading.Lock()

    def cancel(self):
        with self.lock:
            self.cancelled = True
            if self.current_process and self.current_process.poll() is None:
                try:
                    pgid = os.getpgid(self.current_process.pid)
                    os.killpg(pgid, signal.SIGTERM)
                    time.sleep(0.4)
                    if self.current_process.poll() is None:
                        os.killpg(os.getpgid(self.current_process.pid), signal.SIGKILL)
                except Exception:
                    pass
        for prog in ("flatpak", "xbps-install"):
            try:
                subprocess.run(["pkill", "-KILL", "-f", prog], timeout=2, capture_output=True)
            except Exception:
                pass

    def is_running(self) -> bool:
        return self.current_process is not None and self.current_process.poll() is None


# Updates Page

class UpdatesPage(QWidget):
    """
    Página de actualizaciones integrada en Yelena Software.
    Muestra tabla de paquetes pendientes (XBPS / Flatpak),
    permite instalar todos o solo los seleccionados, y muestra
    el output del proceso en tiempo real.
    """

    count_changed = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("updatesPage")

        self._pkg_mgr = _detect_pkg_mgr()
        self._available: list[dict] = []
        self._proc_mgr = _ProcessManager()
        self._reload_lock = False
        self._busy = False
        self._last_count = -1

        self._signals = _Signals()
        self._signals.append_output.connect(self._on_append_output)
        self._signals.set_status.connect(self._on_set_status)
        self._signals.op_done.connect(self._on_op_done)
        self._signals.pulse_start.connect(self._start_pulse)
        self._signals.pulse_stop.connect(self._stop_pulse)
        self._signals.update_list.connect(self._on_update_list)

        self._pulse_timer = QTimer(self)
        self._pulse_timer.setInterval(60)
        self._pulse_timer.timeout.connect(
            lambda: self._progress.setValue((self._progress.value() + 1) % 100)
        )

        # Cola de filas pendientes de insertar en la tabla, procesada en
        # tandas pequeñas por un QTimer para que listas grandes de
        # actualizaciones no traben la GUI al construir la tabla de una
        # sola vez (ver _display / _process_row_queue).
        self._row_queue: list[tuple[dict, bool]] = []
        self._row_queue_total = 0
        self._row_queue_timer = QTimer(self)
        self._row_queue_timer.setInterval(0)
        self._row_queue_timer.timeout.connect(self._process_row_queue)

        self._build_ui()
        self.refresh_status_banner()

        # Estado inicial: mientras se carga la caché local (ver más abajo,
        # QTimer.singleShot(0, self._load_cached)) el QStackedWidget aún
        # no ha recibido ningún setCurrentWidget() explícito, así que por
        # defecto mostraría su primer widget añadido (_content_widget,
        # vacío: sin filas, sin banner útil) durante ese instante — se veía
        # como un parpadeo con la cabecera de tabla y los botones "en
        # crudo" cada vez que se entraba a esta pestaña por primera vez.
        # Se evita arrancando ya en la pantalla de búsqueda/carga.
        self._page_stack.setCurrentWidget(self._searching_page)

        QTimer.singleShot(0, self._load_cached)
        self._cache_timer = QTimer(self)
        self._cache_timer.setInterval(2000)
        self._cache_timer.timeout.connect(self._reload_from_cache)
        self._cache_timer.start()

        # MainWindow sincroniza al iniciar y actualiza esta página al terminar.

    # Build UI

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Contenido normal (lista/tarjetas) vs pantalla "al día" a pantalla
        # completa: se alternan según _display() encuentre o no
        # actualizaciones pendientes (ver _show_up_to_date).
        self._content_widget = QWidget()
        self._up_to_date_page = _UpToDatePage()
        self._searching_page = _SearchingPage()
        self._page_stack = QStackedWidget()
        self._page_stack.addWidget(self._content_widget)   # index 0
        self._page_stack.addWidget(self._up_to_date_page)  # index 1
        self._page_stack.addWidget(self._searching_page)   # index 2
        outer.addWidget(self._page_stack, stretch=1)

        root = QVBoxLayout(self._content_widget)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(16)

        # La barra de búsqueda ocupa el lugar del título.
        header_row = QHBoxLayout()

        # MainWindow aplica la búsqueda; se conserva el texto para filtrar tras recargar.
        self._filter_text = ""
        header_row.addStretch(1)

        # Botones de la barra de herramientas.
        #
        # Cada uno usa un icono del tema de iconos del sistema (Breeze,
        # Adwaita, hicolor...) a través de themed_icon(), que prueba varios
        # nombres simbólicos estándar y, solo si el tema activo no trae
        # ninguno, cae a un QStyle.StandardPixmap nativo o al glifo dibujado
        # a mano como último recurso (para que nunca falte el icono).
        # Además cada botón conserva su color de fondo propio para
        # distinguirse claramente a simple vista.
        self._btn_reload_list = QPushButton()
        self._btn_reload_list.setObjectName("btnReloadList")
        self._btn_reload_list.setIcon(themed_icon(
            ["view-refresh-symbolic", "view-refresh"], "reload",
            standard_pixmap=QStyle.SP_BrowserReload, color=QColor("#FFFFFF")))
        self._btn_reload_list.setToolTip(tr("applet_check_now"))
        self._btn_reload_list.setStyleSheet(
            "QPushButton#btnReloadList { background: #8B5CF6; border-radius: 8px; border: none; }"
            "QPushButton#btnReloadList:hover { background: #A17BF6; }"
            "QPushButton#btnReloadList:disabled { background: #3F3F46; }"
        )
        # La comprobación sincroniza los repos y muestra _searching_page mientras carga.
        self._btn_reload_list.clicked.connect(self._refresh_updates)

        # El botón "Comprobar actualizaciones" (antes aquí, verde) ya no
        # vive en esta página: su función la asume el botón de búsqueda
        # de la cabecera (MainWindow) cuando esta pestaña está activa,
        # cambiando de icono para dejar de parecer una lupa.

        # El botón "Instalar todo" ya no se duplica arriba: la misma
        # acción vive abajo como "Descargar mejoras" (self._btn_install_all_bottom).

        self._btn_cancel = QPushButton()
        self._btn_cancel.setObjectName("btnRemove")
        # Se usa siempre el glifo "X" dibujado a mano en blanco (symbolic_icon),
        # NO themed_icon: los iconos del tema del sistema (process-stop-symbolic)
        # traen su propio color fijo y no respetan el `color` que se les pasa,
        # así que en temas donde ese icono viene oscuro/rojizo desaparecía por
        # completo contra el fondo rojo del botón. El glifo a mano garantiza
        # buen contraste siempre, sin depender de qué temas tenga el sistema.
        self._btn_cancel.setIcon(symbolic_icon("cancel", color=QColor("#FFFFFF")))
        self._btn_cancel.setToolTip(tr("cancel"))
        self._btn_cancel.setStyleSheet(
            "QPushButton#btnRemove { background: #D9534F; border-radius: 8px; border: none; }"
            "QPushButton#btnRemove:hover { background: #E56A66; }"
            "QPushButton#btnRemove:disabled { background: #3F3F46; }"
        )
        self._btn_cancel.setEnabled(False)
        self._btn_cancel.setVisible(False)
        self._btn_cancel.clicked.connect(self._cancel_process)

        self._btn_clean = QPushButton()
        self._btn_clean.setObjectName("btnClean")
        self._btn_clean.setIcon(themed_icon(
            ["edit-clear-all-symbolic", "user-trash-symbolic", "edit-clear"], "broom",
            standard_pixmap=QStyle.SP_TrashIcon, color=QColor("#FFFFFF")))
        self._btn_clean.setToolTip(tr("btn_clean_cache"))
        self._btn_clean.setStyleSheet(
            "QPushButton#btnClean { background: #EC4899; border-radius: 8px; border: none; }"
            "QPushButton#btnClean:hover { background: #F172AC; }"
            "QPushButton#btnClean:disabled { background: #3F3F46; }"
        )
        self._btn_clean.clicked.connect(self._clean_cache)

        # Reload y Limpiar caché ya no van en la botonera de arriba: se
        # muestran junto con "Descargar mejoras", abajo, solo con icono
        # (ver bottom_bar más adelante). Arriba no queda ningún botón de
        # instalar: esa acción vive solo abajo, para no duplicarla.
        for btn in (self._btn_reload_list, self._btn_clean):
            btn.setFixedSize(38, 38)
            btn.setIconSize(QSize(18, 18))
            btn.setCursor(Qt.PointingHandCursor)

        header_row.setSpacing(8)
        root.addLayout(header_row)

        # Banner de alerta (4 colores: gris/verde/amarillo/rojo)
        self._alert_banner = AlertBanner()
        root.addWidget(self._alert_banner)

        # Separador
        sep = QFrame()
        sep.setObjectName("separator")
        sep.setFrameShape(QFrame.HLine)
        root.addWidget(sep)

        # El widget en sí ya no se muestra (ver comentario arriba), pero se
        # mantiene vivo -sin añadir al layout- porque otras partes del
        # código todavía le hacen setText() para llevar el estado interno.
        self._count_badge = QLabel(self)
        self._count_badge.setVisible(False)

        # Paquetes de sistema/CuerdOS ocultos por la base de paquetes
        # escondidos. Si son pocos (<= _SYSTEM_INLINE_LIMIT) se muestran
        # sueltos, como uno más de la lista; si son muchos se agrupan en
        # esta tarjeta ("Ver detalles" abre la base de datos de paquetes
        # filtrados, ver _open_filtered_updates_dialog).
        self._filtered_updates: list[dict] = []
        self._superpackage_card = _SuperpackageCard()
        self._superpackage_card.view_details.connect(self._open_filtered_updates_dialog)
        self._superpackage_card.setVisible(False)
        root.addWidget(self._superpackage_card)

        # Lista de tarjetas de actualización (antes: QTableWidget de 6
        # columnas). Cabecera de columnas propia (sin grilla) + scroll
        # con una UpdateRow por paquete — mucho más cerca del diagrama:
        # icono, nombre, versiones apiladas, tipo, arch.
        pkgs_layout = QVBoxLayout()
        pkgs_layout.setContentsMargins(0, 8, 0, 0)
        pkgs_layout.setSpacing(0)

        col_hdr = QWidget()
        col_hdr_lay = QHBoxLayout(col_hdr)
        col_hdr_lay.setContentsMargins(12, 4, 12, 4)
        col_hdr_lay.setSpacing(12)
        col_hdr_lay.addSpacing(32)  # icono
        pkg_hdr_lbl = QLabel(tr("updates_col_package"))
        pkg_hdr_lbl.setObjectName("sectionSub")
        col_hdr_lay.addWidget(pkg_hdr_lbl, stretch=1)
        ver_hdr_lbl = QLabel(f"{tr('updates_col_current')} / {tr('updates_col_new')}")
        ver_hdr_lbl.setObjectName("sectionSub")
        ver_hdr_lbl.setFixedWidth(110)
        col_hdr_lay.addWidget(ver_hdr_lbl)
        type_hdr_lbl = QLabel(tr("updates_col_type"))
        type_hdr_lbl.setObjectName("sectionSub")
        type_hdr_lbl.setFixedWidth(28)
        type_hdr_lbl.setAlignment(Qt.AlignCenter)
        col_hdr_lay.addWidget(type_hdr_lbl)
        arch_hdr_lbl = QLabel(tr("updates_col_arch"))
        arch_hdr_lbl.setObjectName("sectionSub")
        arch_hdr_lbl.setFixedWidth(64)
        arch_hdr_lbl.setAlignment(Qt.AlignRight)
        col_hdr_lay.addWidget(arch_hdr_lbl)
        col_hdr.setFixedWidth(760)
        pkgs_layout.addWidget(col_hdr, alignment=Qt.AlignHCenter)

        self._rows: list[UpdateRow] = []
        self._rows_scroll = QScrollArea()
        self._rows_scroll.setWidgetResizable(True)
        self._rows_scroll.setFrameShape(QFrame.NoFrame)

        self._rows_widget = QWidget()
        self._rows_layout = QVBoxLayout(self._rows_widget)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        # Sin espacio entre filas: los paquetes deben verse como una sola
        # lista continua centrada, no como tarjetas independientes con
        # huecos entre cada una.
        self._rows_layout.setSpacing(0)
        self._rows_layout.setAlignment(Qt.AlignHCenter)
        self._rows_layout.addStretch()
        self._rows_scroll.setWidget(self._rows_widget)

        # Pantalla de carga: mientras se insertan filas en tandas (ver
        # _process_row_queue) no se muestra nada de la lista —ni
        # siquiera parcialmente— para evitar el efecto de "va apareciendo
        # de a poco". Se revela solo cuando termina de cargar por completo.
        self._table_loading = _TableLoadingScreen()

        self._pkgs_stack = QStackedWidget()
        self._pkgs_stack.addWidget(self._rows_scroll)     # index 0
        self._pkgs_stack.addWidget(self._table_loading)   # index 1
        pkgs_layout.addWidget(self._pkgs_stack)
        root.addLayout(pkgs_layout, stretch=1)

        # Acción principal, siempre visible y con texto debajo de la lista.
        bottom_bar = QHBoxLayout()
        bottom_bar.addStretch(1)
        self._btn_reload_list.setFixedSize(38, 38)
        bottom_bar.addWidget(self._btn_reload_list)
        self._btn_clean.setFixedSize(38, 38)
        bottom_bar.addWidget(self._btn_clean)
        self._btn_install_all_bottom = QPushButton(tr("updates_download_improvements"))
        self._btn_install_all_bottom.setObjectName("btnInstallBottom")
        self._btn_install_all_bottom.setCursor(Qt.PointingHandCursor)
        self._btn_install_all_bottom.setMinimumHeight(38)
        self._btn_install_all_bottom.setStyleSheet(
            "QPushButton#btnInstallBottom { background: #F97316; color: #FFFFFF; "
            "border-radius: 8px; border: none; padding: 6px 22px; font-weight: 600; }"
            "QPushButton#btnInstallBottom:hover { background: #FB923C; }"
            "QPushButton#btnInstallBottom:disabled { background: #3F3F46; color: #888888; }"
        )
        self._btn_install_all_bottom.setEnabled(False)
        self._btn_install_all_bottom.clicked.connect(self._install_all)
        bottom_bar.addWidget(self._btn_install_all_bottom)
        root.addLayout(bottom_bar)

        # Ventana de salida; su contenido se actualiza incluso cuando está oculta.
        self._output_window = None

        self._terminal_frame = QFrame()
        self._terminal_frame.setObjectName("terminalFrame")
        term_layout = QVBoxLayout(self._terminal_frame)
        term_layout.setContentsMargins(0, 0, 0, 0)
        term_layout.setSpacing(0)

        # Barra de título (semáforo + título + estado en vivo)
        title_bar = QWidget()
        title_bar.setObjectName("terminalTitleBar")
        tb_layout = QHBoxLayout(title_bar)
        tb_layout.setContentsMargins(12, 8, 12, 8)
        tb_layout.setSpacing(6)

        tb_layout.addStretch()

        self._term_status_dot = _StatusDot(QColor("#555555"))
        self._term_status_dot.setObjectName("terminalStatusDot")
        self._term_status_label = QLabel(tr("updates_terminal_idle"))
        self._term_status_label.setObjectName("terminalStatusLabel")
        tb_layout.addSpacing(10)
        tb_layout.addWidget(self._term_status_dot)
        tb_layout.addWidget(self._term_status_label)

        # Cierra la ventana de salida sin detener la operación en curso.
        self._btn_close_output = QPushButton()
        self._btn_close_output.setObjectName("btnCloseOutput")
        self._btn_close_output.setIcon(symbolic_icon("cancel", color=QColor("#FFFFFF")))
        self._btn_close_output.setToolTip(tr("close"))
        self._btn_close_output.setFixedSize(26, 26)
        self._btn_close_output.setIconSize(QSize(13, 13))
        self._btn_close_output.setCursor(Qt.PointingHandCursor)
        self._btn_close_output.setStyleSheet(
            "QPushButton#btnCloseOutput { background: transparent; border: none; border-radius: 13px; }"
            "QPushButton#btnCloseOutput:hover { background: #3F3F46; }"
        )
        self._btn_close_output.clicked.connect(self._close_output_window)
        tb_layout.addSpacing(10)
        tb_layout.addWidget(self._btn_close_output)

        term_layout.addWidget(title_bar)

        self._output = QTextEdit()
        self._output.setReadOnly(True)
        self._output.setFont(QFont("Consolas, 'DejaVu Sans Mono', monospace", 10))
        self._output.setObjectName("processOutput")
        self._output.setFrameShape(QFrame.NoFrame)
        self._output.setPlaceholderText(tr("updates_output_placeholder"))
        term_layout.addWidget(self._output, stretch=1)

        # Parpadeo del indicador de estado mientras hay un proceso corriendo
        self._term_blink_on = True
        self._term_blink_timer = QTimer(self)
        self._term_blink_timer.setInterval(500)
        self._term_blink_timer.timeout.connect(self._toggle_term_blink)

        # Barra fija fuera del stack: queda visible en cualquier estado de la página.
        self._status_bar = self._build_status_bar()
        self._status_bar.setVisible(False)
        outer.addWidget(self._status_bar)

    def _build_status_bar(self) -> QFrame:
        """Barra inferior fija: qué se está haciendo + Cancelar + Ver
        salida (ventana aparte). Sustituye el viejo tab 'Proceso'."""
        bar = QFrame()
        bar.setObjectName("pendingStatusBar")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(10)

        self._status_dot = _StatusDot(QColor("#555555"))
        lay.addWidget(self._status_dot)

        self._status_text_lbl = QLabel(tr("updates_terminal_idle"))
        self._status_text_lbl.setObjectName("sectionSub")
        lay.addWidget(self._status_text_lbl, stretch=1)

        self._progress = QProgressBar()
        self._progress.setObjectName("taskProgress")
        self._progress.setRange(0, 100)
        self._progress.setMaximumHeight(6)
        self._progress.setFixedWidth(140)
        self._progress.setTextVisible(False)
        self._progress.setVisible(False)
        lay.addWidget(self._progress)

        self._btn_view_output = QPushButton(tr("updates_view_output"))
        self._btn_view_output.setCursor(Qt.PointingHandCursor)
        self._btn_view_output.clicked.connect(self._open_output_window)
        lay.addWidget(self._btn_view_output)

        self._btn_cancel = QPushButton()
        self._btn_cancel.setObjectName("btnRemove")
        self._btn_cancel.setIcon(symbolic_icon("cancel", color=QColor("#FFFFFF")))
        self._btn_cancel.setToolTip(tr("cancel"))
        self._btn_cancel.setFixedSize(32, 32)
        self._btn_cancel.setIconSize(QSize(16, 16))
        self._btn_cancel.setCursor(Qt.PointingHandCursor)
        self._btn_cancel.setStyleSheet(
            "QPushButton#btnRemove { background: #D9534F; border-radius: 8px; border: none; }"
            "QPushButton#btnRemove:hover { background: #E56A66; }"
        )
        self._btn_cancel.clicked.connect(self._cancel_process)
        lay.addWidget(self._btn_cancel)

        return bar

    def _open_output_window(self):
        """Abre (o trae al frente) una ventana aparte con la salida en
        vivo del proceso — antes era la pestaña 'Proceso' inline."""
        from PySide6.QtWidgets import QDialog

        if self._output_window is None:
            dlg = QDialog(self.window())
            dlg.setWindowTitle("agent-yelena")
            dlg.resize(720, 480)
            lay = QVBoxLayout(dlg)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.addWidget(self._terminal_frame)
            self._output_window = dlg
        self._output_window.show()
        self._output_window.raise_()
        self._output_window.activateWindow()

    def _close_output_window(self):
        """Cierra (oculta) la ventana de salida. El proceso, si sigue
        corriendo, no se interrumpe — solo se deja de mostrar la
        terminal; se puede reabrir con 'Ver salida'."""
        if self._output_window is not None:
            self._output_window.hide()

    def _show_error_with_output(self, message: str) -> None:
        """Error común con acceso directo a la salida de agent-yelena."""
        box = QMessageBox(QMessageBox.Critical, "Yelena Software", message, parent=self)
        output_btn = box.addButton(tr("updates_view_agent_output"), QMessageBox.ActionRole)
        box.addButton(QMessageBox.Close)
        box.exec()
        if box.clickedButton() is output_btn:
            self._open_output_window()

    # Status banner (alerta de 4 colores dentro de la sección)

    def refresh_status_banner(self):
        if not app_settings.get_bool("show_update_status"):
            self._alert_banner.setVisible(False)
            return
        self._alert_banner.setVisible(True)
        try:
            status = update_status.compute()
        except Exception:
            return
        title = tr(status.title_key)
        message = tr(status.message_key, count=status.count, important=status.important_count)
        if status.detail:
            message = f"{message} — {status.detail}"
        self._alert_banner.set_alert(status.level, title, message)

    # Cache / data

    def _load_cached(self):
        if not _BACKENDS_OK or _updates_db is None:
            self._on_set_status(tr("updates_backends_unavailable"))
            # Sin backends no hay forma de saber si hay o no actualizaciones:
            # se sale de la pantalla de búsqueda hacia la vista normal (que
            # mostrará el aviso correspondiente) en vez de quedarse
            # congelada ahí para siempre.
            self._page_stack.setCurrentWidget(self._content_widget)
            self.refresh_status_banner()
            return
        try:
            cached, _ = _updates_db.load_pending()
            # Mostrar también el estado "al día" cuando la caché está vacía.
            updates = self._normalize(cached) if cached else []
            self._available = updates
            self._display(updates)
        except Exception as exc:
            print(f"[Updates] load_cached: {exc}")
            self._page_stack.setCurrentWidget(self._content_widget)
        self._update_buttons()
        self.refresh_status_banner()

    def _reload_from_cache(self):
        # No leer la DB durante operaciones: se actualiza al terminar para evitar datos parciales.
        if self._busy:
            return
        if self._reload_lock or not _BACKENDS_OK or _updates_db is None:
            return
        self._reload_lock = True
        try:
            pending, count = _updates_db.load_pending()
            if count == self._last_count:
                return
            self._last_count = count
            updates = self._normalize(pending)
            self._available = updates
            self._display(updates)
            self._update_buttons()
            self.refresh_status_banner()
        except Exception as exc:
            print(f"[Updates] reload_from_cache: {exc}")
        finally:
            QTimer.singleShot(1000, lambda: setattr(self, "_reload_lock", False))

    def _force_reload_list(self):
        """Recarga la lista actual desde la caché local, sin volver a
        sincronizar repositorios (a diferencia de _refresh_updates)."""
        if not _BACKENDS_OK or _updates_db is None:
            return
        try:
            pending, count = _updates_db.load_pending()
            self._last_count = count
            updates = self._normalize(pending)
            self._available = updates
            self._display(updates)
            self._update_buttons()
            self.refresh_status_banner()
            self._signals.set_status.emit(tr("updates_reload_list_done"))
        except Exception as exc:
            print(f"[Updates] force_reload_list: {exc}")

    def _normalize(self, updates: list) -> list:
        out = []
        for u in updates:
            if not isinstance(u, dict) or "name" not in u:
                continue
            out.append({
                "name":            u.get("name", "Unknown"),
                "current_version": u.get("current_version") or u.get("cur_version", ""),
                "new_version":     u.get("new_version") or u.get("version", "Available"),
                "type":            u.get("type") or u.get("manager", "unknown"),
                "arch":            u.get("arch", ""),
                "download_size":   u.get("download_size", 0),
                "install_size":    u.get("install_size", 0),
                "repo":            u.get("repo", ""),
                "tier":            u.get("tier", 2),
                "status":          u.get("status", "pending"),
            })
        return out

    @staticmethod
    def _is_hidden_system_update(update: dict) -> bool:
        manager = (update.get("type") or update.get("manager") or "xbps").lower()
        source = PackageSource.FLATPAK if manager == "flatpak" else PackageSource.XBPS
        package = Package(
            id=f"{manager}:{update.get('name', '')}",
            name=update.get("name", ""),
            summary="",
            source=source,
        )
        try:
            return is_blacklisted(package)
        except Exception:
            return False

    def _update_filtered_banner(self, filtered: list[dict]) -> None:
        """Ordena la lista de paquetes de sistema/CuerdOS ocultos (para el
        diálogo de detalle) y refresca el contador de la tarjeta
        superpaquete. La visibilidad de la tarjeta la decide _display()
        según _SYSTEM_INLINE_LIMIT."""
        group_order = {
            "CuerdOS Base": 0,
            "Paquetes del escritorio": 1,
            "Otros componentes": 2,
        }
        self._filtered_updates = sorted(
            filtered,
            key=lambda u: (group_order.get(self._filtered_group(u.get("name", "")), 2),
                           u.get("name", "").lower()),
        )
        self._superpackage_card.set_count(len(self._filtered_updates))

    @staticmethod
    def _filtered_group(name: str) -> str:
        low = name.lower()
        if low.startswith(("linux", "gnu", "glibc", "musl")):
            return "CuerdOS Base"
        if low.startswith(("cuerdos", "yel-soft", "ramjet", "skycatcher")):
            return "CuerdOS Base"
        if any(token in low for token in ("xfce", "gnome", "kde", "plasma", "mate", "cinnamon")):
            return "Paquetes del escritorio"
        return "Otros componentes"

    def _open_filtered_updates_dialog(self) -> None:
        """Muestra los paquetes ocultos en una vista compacta tipo DB."""
        dlg = QDialog(self.window())
        dlg.setWindowTitle(tr("updates_filtered_database_title"))
        dlg.resize(680, 420)
        root = QVBoxLayout(dlg)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)
        title = QLabel(tr("updates_filtered_database_subtitle").format(
            total=len(self._filtered_updates)
        ))
        title.setWordWrap(True)
        root.addWidget(title)
        table = QTableWidget(len(self._filtered_updates), 3)
        table.setHorizontalHeaderLabels([
            tr("updates_filtered_col_package"),
            tr("updates_filtered_col_group"),
            tr("updates_filtered_col_version"),
        ])
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionMode(QAbstractItemView.NoSelection)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setStretchLastSection(True)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        for row, update in enumerate(self._filtered_updates):
            name = update.get("name", "Unknown")
            group = self._filtered_group(name)
            name_item = QTableWidgetItem(name)
            group_item = QTableWidgetItem(group)
            version_item = QTableWidgetItem(update.get("new_version", "Available"))
            if group == "CuerdOS Base":
                font = name_item.font()
                font.setBold(True)
                name_item.setFont(font)
                group_item.setFont(font)
            table.setItem(row, 0, name_item)
            table.setItem(row, 1, group_item)
            table.setItem(row, 2, version_item)
        root.addWidget(table, stretch=1)
        close_btn = QPushButton(tr("close"))
        close_btn.clicked.connect(dlg.accept)
        root.addWidget(close_btn, alignment=Qt.AlignRight)
        dlg.exec()

    # Table display

    def _display(self, updates: list):
        """Prepara la lista y encola las filas para insertarlas en tandas
        pequeñas (ver _process_row_queue), en vez de construirlas todas de
        golpe: con listas grandes de actualizaciones eso podía trabar la
        GUI durante la sincronización de repos.

        Mientras se insertan las filas, la lista permanece totalmente
        oculta detrás de una pantalla de carga (self._table_loading): no
        se muestra nada, ni siquiera parcialmente, hasta que termina de
        cargar por completo."""
        # Cancelar cualquier tanda de inserción todavía en curso de una
        # llamada anterior (p. ej. la caché se recarga mientras la lista
        # anterior aún se estaba insertando).
        self._row_queue_timer.stop()
        self._row_queue = []

        for row in self._rows:
            self._rows_layout.removeWidget(row)
            row.deleteLater()
        self._rows = []

        # Sin ninguna actualización pendiente (ni normales ni de sistema):
        # pantalla "al día" a pantalla completa en vez de la lista vacía.
        if not updates:
            self._superpackage_card.setVisible(False)
            self._update_filtered_banner([])
            self._show_up_to_date()
            self._filter_table(self._filter_text)
            self._update_buttons()
            return
        self._page_stack.setCurrentWidget(self._content_widget)

        # Todas las actualizaciones se instalarán juntas. La blacklist solo
        # controla la presentación: los paquetes del sistema/CuerdOS se
        # ocultan de la lista principal, pero siguen dentro de _available y
        # por tanto entran en "Actualizar todo".
        normal = [u for u in updates if not self._is_hidden_system_update(u)]
        system = [u for u in updates if self._is_hidden_system_update(u)]
        self._update_filtered_banner(system)

        # Pocos paquetes de sistema (<= _SYSTEM_INLINE_LIMIT): se muestran
        # sueltos, uno más de la lista. Muchos: se agrupan en la tarjeta
        # "Superpaquete de renovación del sistema" y se ocultan de la
        # lista principal (el detalle sigue disponible con "Ver detalles").
        if len(system) > _SYSTEM_INLINE_LIMIT:
            visible = normal
            self._superpackage_card.setVisible(True)
        else:
            visible = normal + system
            self._superpackage_card.setVisible(False)

        ordered = visible
        lock_others = False

        if not ordered:
            self._filter_table(self._filter_text)
            self._update_buttons()
            self._pkgs_stack.setCurrentWidget(self._rows_scroll)
            return

        self._row_queue = [(upd, lock_others) for upd in ordered]
        self._row_queue_total = len(self._row_queue)
        self._table_loading.set_progress(0, self._row_queue_total)
        self._pkgs_stack.setCurrentWidget(self._table_loading)
        self._row_queue_timer.start()

    def _show_up_to_date(self) -> None:
        self._page_stack.setCurrentWidget(self._up_to_date_page)
        self._up_to_date_page._refresh_subtitle()

    def _insert_update_row(self, upd: dict, lock_others: bool) -> None:
        row = UpdateRow(upd, False, False)
        row.setFixedWidth(760)
        row.toggled.connect(self._on_row_toggled)
        self._rows_layout.insertWidget(self._rows_layout.count() - 1, row)
        self._rows.append(row)

    def _process_row_queue(self, batch_size: int = 25):
        """Inserta hasta 'batch_size' filas por tick del timer, cediendo el
        control al event loop entre tandas para no trabar la GUI cuando hay
        muchas actualizaciones de golpe. La lista permanece oculta detrás
        de la pantalla de carga (self._table_loading) durante todo el
        proceso; solo se revela cuando la cola queda vacía."""
        for _ in range(batch_size):
            if not self._row_queue:
                self._row_queue_timer.stop()
                self._filter_table(self._filter_text)
                self._update_buttons()
                self._pkgs_stack.setCurrentWidget(self._rows_scroll)
                return
            upd, lock_others = self._row_queue.pop(0)
            self._insert_update_row(upd, lock_others)

        # Progreso: se refleja en la pantalla de carga (nunca en la lista,
        # que sigue oculta hasta terminar por completo).
        remaining = len(self._row_queue)
        loaded = getattr(self, "_row_queue_total", len(self._rows)) - remaining
        self._table_loading.set_progress(max(loaded, 0), remaining)
        self._update_buttons()

    def _filter_table(self, text: str):
        self._filter_text = text
        needle = text.strip().lower()
        for row in self._rows:
            hidden = bool(needle) and needle not in row.upd.get("name", "").lower()
            row.setVisible(not hidden)

    def _on_row_toggled(self):
        self._update_buttons()

    # Buttons / status

    def _update_buttons(self):
        count = len(self._available)
        self._btn_install_all_bottom.setEnabled(count > 0)
        if count == 0:
            self._count_badge.setText(tr("updates_up_to_date"))
        else:
            self._count_badge.setText(tr("updates_count_available").format(n=count))
        self.count_changed.emit(count)

    @Slot(str)
    def _on_set_status(self, text: str):
        self._count_badge.setText(text)
        self._status_text_lbl.setText(text)

    @Slot(str)
    def _on_append_output(self, text: str):
        from PySide6.QtGui import QTextCursor, QTextCharFormat, QColor as _QColor
        line = text if text else ""
        if line.startswith("$ "):
            color = "#6EE798"
            bold = True
        elif line.startswith("!! ") or "error" in line.lower() or "failed" in line.lower():
            color = "#F28B82"
            bold = False
        else:
            color = "#CFCFCF"
            bold = False

        fmt = QTextCharFormat()
        fmt.setForeground(_QColor(color))
        fmt.setFontWeight(QFont.Bold if bold else QFont.Normal)

        cursor = self._output.textCursor()
        cursor.movePosition(QTextCursor.End)
        if not cursor.atStart():
            cursor.insertBlock()
        cursor.setCharFormat(fmt)
        cursor.insertText(line)
        self._output.setTextCursor(cursor)
        self._output.moveCursor(QTextCursor.End)

    @Slot(list)
    def _on_update_list(self, updates: list):
        self._available = updates
        self._display(updates)
        self._update_buttons()

    # Progress

    @Slot()
    def _start_pulse(self):
        self._progress.setVisible(True)
        if not self._pulse_timer.isActive():
            self._pulse_timer.start()
        self._set_terminal_running(True)

    @Slot()
    def _stop_pulse(self):
        self._pulse_timer.stop()
        self._progress.setVisible(False)
        self._progress.setValue(0)
        self._set_terminal_running(False)

    def _set_terminal_running(self, running: bool):
        if running:
            self._term_status_label.setText(tr("updates_terminal_running"))
            if not self._term_blink_timer.isActive():
                self._term_blink_on = True
                self._term_blink_timer.start()
        else:
            self._term_blink_timer.stop()
            self._term_status_label.setText(tr("updates_terminal_idle"))
            self._term_status_dot.setColor(QColor("#555555"))
            self._status_dot.setColor(QColor("#555555"))

    def _toggle_term_blink(self):
        self._term_blink_on = not self._term_blink_on
        color = "#4ADE80" if self._term_blink_on else "#1E3A24"
        self._term_status_dot.setColor(QColor(color))
        self._status_dot.setColor(QColor(color))

    # Actions

    def _set_busy(self, busy: bool):
        # Durante un proceso (sincronizar, instalar, limpiar caché) se
        # ocultan por completo los botones de acción para que no se puedan
        # pulsar mientras corre algo — solo el de cancelar (rojo) queda
        # visible, ya que es la única acción válida en ese momento.
        self._busy = busy
        for btn in (self._btn_reload_list, self._btn_clean):
            btn.setEnabled(not busy)
        self._btn_install_all_bottom.setEnabled(not busy and len(self._available) > 0)
        self._status_bar.setVisible(busy)
        if not busy:
            QTimer.singleShot(1000, lambda: self._status_bar.setVisible(False))

    def _refresh_updates(self):
        if not _BACKENDS_OK:
            self._show_error_with_output(tr("updates_backends_unavailable"))
            return

        # Comprobación de salud previa (binarios, DB bloqueada...) para dar
        # un mensaje claro antes de intentar nada, en vez de un fallo a medias.
        health_issue = _xbps_err.check_xbps_health()
        if health_issue is not None and self._pkg_mgr == "xbps":
            self._show_error_with_output(health_issue.message)
            return

        self._output.clear()
        self._set_busy(True)
        self._signals.pulse_start.emit()
        self._signals.set_status.emit(tr("updates_searching"))
        self._proc_mgr.cancelled = False

        # Pantalla de búsqueda a pantalla completa (spinner SVG animado)
        # mientras se sincronizan los repos: se sustituye por la lista o
        # por "al día" en cuanto llega el resultado (ver _display /
        # _on_op_done). La barra de estado inferior (Cancelar / Ver
        # salida) sigue visible encima, ya que vive fuera de _page_stack.
        self._page_stack.setCurrentWidget(self._searching_page)

        def _thread():
            try:
                if not (self._pkg_mgr == "xbps" and _xbps_be):
                    self._signals.append_output.emit(
                        "(XBPS no disponible en este sistema, omitiendo sincronización de repos)"
                    )

                ok, all_updates, err = _repo_sync.sync_repos_and_refresh(
                    is_cancelled=lambda: self._proc_mgr.cancelled,
                    on_line=lambda line: self._signals.append_output.emit(line),
                )

                if self._proc_mgr.cancelled or err == "cancelled":
                    self._signals.op_done.emit(False, "OP_REFRESH", "CANCELLED")
                    return

                if not ok:
                    self._signals.op_done.emit(False, "OP_REFRESH", err)
                    return

                self._signals.update_list.emit(all_updates)
                n = len(all_updates)
                msg = f"{n} " + tr("updates_found_suffix") if n else tr("updates_up_to_date")
                self._signals.set_status.emit(msg)
                self._signals.op_done.emit(True, "OP_REFRESH", "")

            except Exception as exc:
                self._signals.pulse_stop.emit()
                self._signals.op_done.emit(False, "OP_REFRESH", str(exc))
            finally:
                with self._proc_mgr.lock:
                    self._proc_mgr.current_process = None

        threading.Thread(target=_thread, daemon=True).start()

    def _install_all(self):
        if not self._available:
            QMessageBox.warning(self, "Yelena Software", tr("updates_none_available"))
            return

        health_issue = _xbps_err.check_xbps_health()
        if health_issue is not None:
            self._show_error_with_output(health_issue.message)
            return

        total_dl = sum(u.get("download_size", 0) for u in self._available)
        msg = tr("updates_confirm_all").format(n=len(self._available))
        if total_dl > 0:
            msg += tr("updates_confirm_dl").format(size=_format_size(total_dl))
        if QMessageBox.question(
            self, "Yelena Software", msg,
            QMessageBox.Yes | QMessageBox.No
        ) != QMessageBox.Yes:
            return

        self._output.clear()
        self._set_busy(True)
        self._signals.pulse_start.emit()
        self._signals.set_status.emit(tr("updates_installing"))
        self._proc_mgr.cancelled = False

        # Todas las mejoras tienen la misma importancia para la interfaz:
        # no se separan ni se bloquean por prioridad.
        ordered = list(self._available)

        if self._pkg_mgr == "xbps":
            cmds = _xbps_be.build_install_cmds(ordered) if _xbps_be else []
        else:
            cmds = []
        if any(u.get("type") == "flatpak" for u in ordered):
            cmds.append(["flatpak", "update", "-y"])

        self._mark_db_installing(ordered)
        self._run_commands(cmds, "OP_INSTALL_ALL", packages=ordered)

    def _install_selected(self):
        selected = []
        for row in self._rows:
            if row.is_checked():
                u = row.upd
                selected.append({
                    "name": u.get("name", ""),
                    "type": u.get("type", "unknown"),
                    "version": u.get("new_version", ""),
                    "tier": u.get("tier", 2),
                })
        if not selected:
            QMessageBox.warning(self, "Yelena Software", tr("updates_none_selected"))
            return

        health_issue = _xbps_err.check_xbps_health()
        if health_issue is not None:
            self._show_error_with_output(health_issue.message)
            return

        msg = tr("updates_confirm_selected").format(n=len(selected))
        if QMessageBox.question(
            self, "Yelena Software", msg,
            QMessageBox.Yes | QMessageBox.No
        ) != QMessageBox.Yes:
            return

        self._output.clear()
        self._set_busy(True)
        self._signals.pulse_start.emit()
        self._signals.set_status.emit(tr("updates_installing_selected"))
        self._proc_mgr.cancelled = False

        # Orden de instalación: crítico → gestor de paquetes → normal
        selected.sort(key=lambda p: p.get("tier", 2))

        xbps_p = [p["name"] for p in selected if p["type"] == "xbps"]
        flat_p  = [p["name"] for p in selected if p["type"] == "flatpak"]

        cmds = []
        if xbps_p and _xbps_be:
            cmds.extend(_xbps_be.build_install_selected_cmds(xbps_p))
        for pkg in flat_p:
            cmds.append(["flatpak", "update", "-y", pkg])

        self._mark_db_installing(selected)
        self._run_commands(cmds, "OP_INSTALL_SEL", packages=selected)

    def _clean_cache(self):
        health_issue = _xbps_err.check_xbps_health()
        if health_issue is not None:
            self._show_error_with_output(health_issue.message)
            return
        msg = tr("updates_confirm_cache")
        if QMessageBox.question(
            self, "Yelena Software", msg,
            QMessageBox.Yes | QMessageBox.No
        ) != QMessageBox.Yes:
            return
        self._output.clear()
        self._set_busy(True)
        self._signals.pulse_start.emit()
        self._signals.set_status.emit(tr("updates_cleaning_cache"))
        self._proc_mgr.cancelled = False

        cmds = [
            ["/usr/bin/xbps-remove", "--clean-cache", "-y"],
        ]
        import shutil as _shutil_check
        if _shutil_check.which("flatpak"):
            cmds.append(["flatpak", "uninstall", "--unused", "-y"])
        self._run_commands(cmds, "OP_CLEAN")

    def _mark_db_installing(self, packages: list) -> None:
        """Marca en la base de datos los paquetes que están a punto de
        instalarse como 'installing', agrupados por gestor. Si la
        aplicación se cierra o se cae a mitad del proceso, al reabrirla
        sabremos exactamente qué tanda quedó a medias (ver
        UpdatesDB.load_incomplete)."""
        if _updates_db is None:
            return
        by_mgr: dict[str, list[str]] = {}
        for p in packages:
            mgr = p.get("type") or p.get("manager") or "unknown"
            by_mgr.setdefault(mgr, []).append(p.get("name", ""))
        for mgr, names in by_mgr.items():
            _updates_db.set_status_bulk(names, mgr, "installing")

    def _cancel_process(self):
        self._proc_mgr.cancel()
        self._signals.set_status.emit(tr("updates_cancelling"))
        self._btn_cancel.setEnabled(False)

    # Command runner

    def _run_commands(self, commands: list, operation: str,
                       packages: Optional[list] = None):
        """
        Ejecuta una lista de comandos.
        Los comandos root se agrupan en una sola invocacion privilegiada, asi el dialogo de autenticacion aparece una sola vez.
        Los comandos de flatpak se ejecutan aparte, sin root.
        Si algo falla, el error se clasifica con xbps_error_handler y los paquetes se marcan como failed.
        """
        import shlex as _shlex

        # Separar flatpak (sin root) de los comandos root
        flatpak_cmds = []
        root_cmds = []
        for cmd in commands:
            if cmd and cmd[0] in ("flatpak", "/usr/bin/flatpak"):
                flatpak_cmds.append(cmd)
            else:
                root_cmds.append(cmd)

        # Cada comando XBPS pasa directamente por pkexec. Nunca se construye
        # un `sh -c`: así la política de polkit sigue identificando el binario
        # concreto y no existe una vía para ejecutar shell arbitrario.
        grouped_root: list = []
        if root_cmds:
            grouped_root = [_build_root_cmd(cmd) for cmd in root_cmds]

        # Secuencia final: primero el grupo root (una sola auth), luego flatpak
        all_cmds = grouped_root + flatpak_cmds

        def _mark_failed(message: str):
            if _updates_db is None or not packages:
                return
            by_mgr: dict[str, list[str]] = {}
            for p in packages:
                mgr = p.get("type") or p.get("manager") or "unknown"
                by_mgr.setdefault(mgr, []).append(p.get("name", ""))
            for mgr, names in by_mgr.items():
                _updates_db.set_status_bulk(names, mgr, "failed", message)

        def _mark_done():
            if _updates_db is None or not packages:
                return
            by_mgr: dict[str, list[str]] = {}
            for p in packages:
                mgr = p.get("type") or p.get("manager") or "unknown"
                by_mgr.setdefault(mgr, []).append(p.get("name", ""))
            for mgr, names in by_mgr.items():
                _updates_db.set_status_bulk(names, mgr, "done")

        def _thread():
            last_lines: list[str] = []
            try:
                for cmd in all_cmds:
                    if self._proc_mgr.cancelled:
                        break
                    self._signals.pulse_start.emit()
                    self._signals.append_output.emit("$ " + _shlex.join(cmd))
                    proc = subprocess.Popen(
                        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, start_new_session=True,
                    )
                    with self._proc_mgr.lock:
                        self._proc_mgr.current_process = proc
                    for line in iter(proc.stdout.readline, ""):
                        if self._proc_mgr.cancelled:
                            try:
                                pgid = os.getpgid(proc.pid)
                                os.killpg(pgid, signal.SIGTERM)
                            except Exception:
                                pass
                            break
                        clean = line.rstrip()
                        self._signals.append_output.emit(clean)
                        if clean:
                            last_lines.append(clean)
                            del last_lines[:-20]   # nos basta con el tramo final para clasificar el error
                    proc.wait()
                    self._signals.pulse_stop.emit()
                    if self._proc_mgr.cancelled:
                        break
                    if proc.returncode != 0:
                        xbps_err = _xbps_err.classify_error(
                            "\n".join(last_lines), proc.returncode
                        )
                        _mark_failed(xbps_err.message)
                        raise RuntimeError(xbps_err.message)

                if self._proc_mgr.cancelled:
                    self._signals.op_done.emit(False, operation, "CANCELLED")
                    return

                _mark_done()
                self._signals.op_done.emit(True, operation, "")

            except Exception as exc:
                self._signals.pulse_stop.emit()
                self._signals.op_done.emit(False, operation, str(exc))
            finally:
                with self._proc_mgr.lock:
                    self._proc_mgr.current_process = None

        threading.Thread(target=_thread, daemon=True).start()

    # Finish operation

    @Slot(bool, str, str)
    def _retry_last_operation(self, operation: str):
        """Vuelve a lanzar la última operación fallida. Para instalaciones
        no hace falta recalcular nada: la selección/lista sigue en pie."""
        if operation == "OP_REFRESH":
            self._refresh_updates()
        elif operation == "OP_INSTALL_ALL":
            self._install_all()
        elif operation == "OP_INSTALL_SEL":
            self._install_selected()
        elif operation == "OP_CLEAN":
            self._clean_cache()

    def _on_op_done(self, success: bool, operation: str, error_msg: str):
        self._stop_pulse()
        self._set_busy(False)
        self._proc_mgr.cancelled = False

        # Si la búsqueda terminó en error/cancelación, _display() nunca
        # llegó a ejecutarse (update_list no se emitió), así que la
        # pantalla de búsqueda se quedaría congelada en pantalla: se
        # restaura la vista según lo que ya había antes de buscar.
        if operation == "OP_REFRESH" and not success:
            if self._available:
                self._page_stack.setCurrentWidget(self._content_widget)
            else:
                self._show_up_to_date()

        if success:
            update_status.clear_error()
            if operation in ("OP_INSTALL_ALL", "OP_INSTALL_SEL"):
                if operation == "OP_INSTALL_ALL":
                    n = len(self._available)
                    if _updates_db:
                        _updates_db.clear_all()
                    self._available = []
                    self._display([])
                else:
                    n = sum(1 for r in self._rows if r.is_checked())
                    self._remove_checked_from_list()
                self._signals.set_status.emit(
                    tr("updates_op_done_n").format(op=tr("updates_op_install_all") if operation == "OP_INSTALL_ALL" else tr("updates_op_install_sel"), n=n)
                )
                notifier.notify(
                    tr("updates_notif_installed_title"),
                    tr("updates_notif_installed_body").format(n=n),
                    icon="software-update-available",
                )
                self._last_count = -1
            elif operation == "OP_REFRESH":
                app_settings.set_value("last_update_check", str(time.time()))
                app_settings.save()
                self._signals.set_status.emit(tr("updates_op_done").format(op=tr("applet_check_now")))
                n = len(self._available)
                if n > 0:
                    notifier.notify(
                        tr("updates_found_notif_title"),
                        tr("updates_notif_found_body").format(n=n),
                        icon="software-update-available",
                    )
            elif operation == "OP_CLEAN":
                self._signals.set_status.emit(tr("updates_op_done").format(op=tr("updates_op_clean")))
                notifier.notify(
                    tr("updates_notif_cache_title"),
                    tr("updates_notif_cache_body"),
                    icon="edit-clear",
                )
        else:
            if error_msg and error_msg != "CANCELLED":
                op_label = {
                    "OP_INSTALL_ALL": tr("updates_op_install_all"),
                    "OP_INSTALL_SEL": tr("updates_op_install_sel"),
                    "OP_REFRESH":     tr("applet_check_now"),
                    "OP_CLEAN":       tr("updates_op_clean"),
                }.get(operation, operation)
                self._signals.set_status.emit(tr("updates_op_failed").format(op=op_label, err=error_msg))
                update_status.set_error(error_msg)
                notifier.notify(
                    op_label,
                    tr("updates_notif_error_body"),
                    icon="dialog-error",
                )
                box = QMessageBox(QMessageBox.Critical, "Yelena Software",
                                   tr("updates_op_failed").format(op=op_label, err=error_msg),
                                   parent=self)
                retry_btn = box.addButton(tr("updates_retry"), QMessageBox.AcceptRole)
                output_btn = box.addButton(
                    tr("updates_view_agent_output"), QMessageBox.ActionRole
                )
                box.addButton(QMessageBox.Close)
                box.exec()
                if box.clickedButton() is retry_btn:
                    self._retry_last_operation(operation)
                elif box.clickedButton() is output_btn:
                    # agent-yelena es el inicializador interno de paquetes;
                    # la ventana conserva toda la salida capturada aunque el
                    # proceso ya haya terminado.
                    self._open_output_window()
            else:
                op_label = {
                    "OP_INSTALL_ALL": tr("updates_op_install_all"),
                    "OP_INSTALL_SEL": tr("updates_op_install_sel"),
                    "OP_REFRESH":     tr("applet_check_now"),
                    "OP_CLEAN":       tr("updates_op_clean"),
                }.get(operation, operation)
                self._signals.set_status.emit(tr("updates_op_cancelled").format(op=op_label))

        self._update_buttons()
        self.refresh_status_banner()

    def _remove_checked_from_list(self):
        sel = set()
        for row in self._rows:
            if row.is_checked():
                name = row.upd.get("name", "")
                typ = row.upd.get("type", "unknown")
                sel.add((name, typ))
                if _updates_db:
                    _updates_db.remove_applied([name], typ)
        self._available = [
            u for u in self._available
            if (u["name"], u.get("type", "unknown")) not in sel
        ]
        self._display(self._available)
