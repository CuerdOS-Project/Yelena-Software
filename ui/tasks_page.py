# Yelena Software — Gestor de tareas
#
# Vista maestro/detalle:
#   · Izquierda: historial de tareas (más nuevas arriba) con búsqueda.
#   · Derecha:   detalle de la tarea elegida (estado, progreso, tiempos,
#                error) y, si el usuario lo pide, la SALIDA en vivo del
#                proceso (con copiar y guardar).
#
# Todos los colores salen de la paleta activa (claro/oscuro/CSDS/Breeze...);
# solo los acentos semánticos (verde/rojo/ámbar) están fijados, y se ajustan
# al brillo del tema para mantener el contraste.

from __future__ import annotations

import re
import time
from pathlib import Path

from PySide6.QtCore import Qt, Signal, Slot, QTimer, QSize, QRectF, QEvent
from PySide6.QtGui import (
    QColor, QPalette, QPainter, QPen, QFontDatabase, QPixmap, QIcon,
    QTextCharFormat, QSyntaxHighlighter, QGuiApplication, QCursor,
)
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QScrollArea, QFrame,
    QSplitter, QLineEdit, QPlainTextEdit,
    QPushButton, QStackedWidget, QFileDialog, QMessageBox, QToolTip,
    QSizePolicy,
)

from core.i18n import tr
from backend.models import Task, TaskState, TaskType
from backend.task_manager import TaskManager
from .native_widgets import (
    NativeProgressBar, TaskCancelButton, symbolic_icon, themed_icon, _mix,
)

_ACTIVE   = (TaskState.PENDING, TaskState.RUNNING)
_FINISHED = (TaskState.DONE, TaskState.FAILED, TaskState.CANCELLED)

# Tope de líneas que se conservan en la consola (evita congelar la UI con
# salidas enormes; el archivo guardado y "copiar" usan lo mismo).
_MAX_LINES = 20000

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")

_TYPE_KEYS = {
    TaskType.INSTALL:         "task_type_install",
    TaskType.REMOVE:          "task_type_remove",
    TaskType.UPDATE:          "task_type_update",
    TaskType.REFRESH:         "task_type_refresh",
    TaskType.LOCAL_INSTALL:   "task_type_local_install",
    TaskType.LOCAL_REMOVE:    "task_type_local_remove",
    TaskType.LOCAL_REINSTALL: "task_type_local_reinstall",
}


# ─────────────────────────────────────────────────────────────────────────────
# Utilidades
# ─────────────────────────────────────────────────────────────────────────────

def _is_dark(pal: QPalette) -> bool:
    return pal.color(QPalette.Window).lightness() < 128


def _state_color(state: TaskState, pal: QPalette) -> QColor:
    """Color de acento por estado, con contraste correcto en claro y oscuro."""
    if state == TaskState.RUNNING:
        return pal.color(QPalette.Highlight)
    dark = _is_dark(pal)
    table = {
        TaskState.DONE:      ("#4CAF50", "#2E7D32"),
        TaskState.FAILED:    ("#EF5350", "#C62828"),
        TaskState.CANCELLED: ("#E0A82E", "#B26A00"),
        TaskState.PENDING:   ("#8A8F98", "#6B7079"),
    }
    d, l = table.get(state, table[TaskState.PENDING])
    return QColor(d if dark else l)


def _state_text(state: TaskState) -> str:
    key = {
        TaskState.PENDING:   "task_pending",
        TaskState.RUNNING:   "task_running",
        TaskState.DONE:      "task_done",
        TaskState.FAILED:    "task_failed",
        TaskState.CANCELLED: "task_cancelled",
    }[state]
    return tr(key).rstrip("….").strip()


def _type_label(tt: TaskType) -> str:
    key = _TYPE_KEYS.get(tt)
    text = tr(key) if key else tt.value
    return tt.value if text == key else text


def _clean_line(raw: str) -> str:
    """Quita secuencias ANSI y se queda con el último tramo tras '\\r'
    (barras de progreso que reescriben la misma línea)."""
    if "\r" in raw:
        parts = [p for p in raw.split("\r") if p.strip()]
        raw = parts[-1] if parts else ""
    return _ANSI_RE.sub("", raw).rstrip()


def _fmt_duration(seconds: float) -> str:
    s = max(0, int(seconds))
    if s < 60:
        return f"{s} s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m} min {s:02d} s"
    h, m = divmod(m, 60)
    return f"{h} h {m:02d} min"


def _fmt_clock(ts: float) -> str:
    if not ts:
        return "—"
    lt = time.localtime(ts)
    if time.strftime("%Y%m%d", lt) == time.strftime("%Y%m%d"):
        return time.strftime("%H:%M:%S", lt)
    return time.strftime("%d/%m %H:%M:%S", lt)


def _task_duration(task: Task) -> float | None:
    if not task.started_at:
        return None
    end = task.finished_at or time.time()
    return end - task.started_at


def _dpr() -> float:
    scr = QGuiApplication.primaryScreen()
    return scr.devicePixelRatio() if scr else 1.0


def _ring_pixmap(color: QColor, size: int) -> QPixmap:
    dpr = _dpr()
    pm = QPixmap(round(size * dpr), round(size * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QPen(color, 1.6))
    m = size * 0.2
    p.drawEllipse(QRectF(m, m, size - 2 * m, size - 2 * m))
    p.end()
    return pm


def _state_pixmap(state: TaskState, color: QColor, size: int) -> QPixmap:
    kind = {
        TaskState.DONE:      "check",
        TaskState.FAILED:    "warn",
        TaskState.CANCELLED: "cancel",
        TaskState.RUNNING:   "reload",
    }.get(state)
    if kind is None:
        return _ring_pixmap(color, size)
    return symbolic_icon(kind, color=color, size=size).pixmap(QSize(size, size), _dpr())


def _button(text: str, icon_names: list[str] | None = None,
            fallback_kind: str | None = None) -> QPushButton:
    """Botón de diálogo seguro: sin autoDefault (Enter en un campo de texto
    no debe disparar 'Limpiar' ni 'Cancelar' por accidente)."""
    b = QPushButton(text)
    b.setAutoDefault(False)
    b.setDefault(False)
    b.setCursor(Qt.PointingHandCursor)
    if icon_names:
        if fallback_kind:
            ic = themed_icon(icon_names, fallback_kind)
        else:
            ic = QIcon()
            for n in icon_names:
                ic = QIcon.fromTheme(n)
                if not ic.isNull():
                    break
        if not ic.isNull():
            b.setIcon(ic)
    return b


# ─────────────────────────────────────────────────────────────────────────────
# Widgets pequeños
# ─────────────────────────────────────────────────────────────────────────────

class _ElidedLabel(QLabel):
    """Etiqueta de una línea que elide con '…' en vez de forzar el ancho
    mínimo de su contenedor. `muted` la atenúa mezclándola con el fondo."""

    def __init__(self, text: str = "", parent=None, muted: bool = False,
                 pt: int | None = None, bold: bool = False,
                 align: Qt.AlignmentFlag = Qt.AlignLeft):
        super().__init__(parent)
        self._full = text
        self._muted = muted
        self._align = align
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        if pt or bold:
            f = self.font()
            if pt:
                f.setPointSize(pt)
            f.setBold(bold)
            self.setFont(f)
        self._sync_tip()

    def set_full_text(self, text: str) -> None:
        if text != self._full:
            self._full = text
            self._sync_tip()
            self.update()

    def full_text(self) -> str:
        return self._full

    def _sync_tip(self) -> None:
        elided = self.fontMetrics().horizontalAdvance(self._full) > max(0, self.width())
        self.setToolTip(self._full if elided and self._full else "")

    def sizeHint(self) -> QSize:
        return QSize(40, self.fontMetrics().height() + 2)

    def minimumSizeHint(self) -> QSize:
        return QSize(16, self.fontMetrics().height() + 2)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._sync_tip()

    def paintEvent(self, _e):
        p = QPainter(self)
        pal = self.palette()
        col = pal.color(QPalette.WindowText)
        if self._muted:
            col = _mix(col, pal.color(QPalette.Window), 0.42)
        p.setPen(col)
        p.setFont(self.font())
        txt = self.fontMetrics().elidedText(self._full, Qt.ElideRight, self.width())
        p.drawText(self.rect(), int(self._align | Qt.AlignVCenter), txt)
        p.end()


class _StatePill(QWidget):
    """Etiqueta redondeada con el estado, tintada con su color de acento."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state = TaskState.PENDING
        self._text = ""
        f = self.font()
        f.setPointSize(max(7, f.pointSize() - 1))
        f.setBold(True)
        self.setFont(f)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

    def set_state(self, state: TaskState) -> None:
        self._state = state
        self._text = _state_text(state)
        self.updateGeometry()
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(self.fontMetrics().horizontalAdvance(self._text) + 18,
                     self.fontMetrics().height() + 6)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = _state_color(self._state, self.palette())
        fill = QColor(c); fill.setAlpha(44)
        edge = QColor(c); edge.setAlpha(120)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(edge, 1))
        p.setBrush(fill)
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        p.setPen(c)
        p.setFont(self.font())
        p.drawText(self.rect(), Qt.AlignCenter, self._text)
        p.end()


class _StateIcon(QWidget):
    """Icono de estado; el de 'en ejecución' gira."""

    def __init__(self, size: int, parent=None):
        super().__init__(parent)
        self._size = size
        self._state = TaskState.PENDING
        self._angle = 0
        self._pm: QPixmap | None = None
        self.setFixedSize(size, size)

    def set_state(self, state: TaskState) -> None:
        self._state = state
        self._pm = _state_pixmap(state, _state_color(state, self.palette()), self._size)
        if state != TaskState.RUNNING:
            self._angle = 0
        self.update()

    def advance(self) -> None:
        if self._state == TaskState.RUNNING:
            self._angle = (self._angle + 24) % 360
            self.update()

    def changeEvent(self, e):
        if e.type() == QEvent.PaletteChange:
            self.set_state(self._state)
        super().changeEvent(e)

    def paintEvent(self, _e):
        if self._pm is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        c = self._size / 2
        p.translate(c, c)
        p.rotate(self._angle)
        p.translate(-c, -c)
        p.drawPixmap(0, 0, self._pm)
        p.end()


class _MetaItem(QWidget):
    """Par 'título pequeño / valor' para la fila de metadatos."""

    def __init__(self, caption: str, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(1)
        self._cap = _ElidedLabel(caption, muted=True, pt=8)
        self._val = _ElidedLabel("—", pt=10)
        lay.addWidget(self._cap)
        lay.addWidget(self._val)

    def set_value(self, t: str, expand: bool = False) -> None:
        self._val.set_full_text(t)
        if not expand:
            # Las etiquetas elididas no aportan ancho propio: se reserva el
            # que necesita el texto (con tope) para que nunca se colapsen.
            w = max(self._cap.fontMetrics().horizontalAdvance(self._cap.full_text()),
                    self._val.fontMetrics().horizontalAdvance(t))
            self.setMinimumWidth(max(self.minimumWidth(), min(w + 4, 220)))


class _TintBox(QFrame):
    """Caja con fondo tintado del color de acento (avisos de error)."""

    def __init__(self, accent_state: TaskState, parent=None):
        super().__init__(parent)
        self._state = accent_state

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pal = self.palette()
        c = _state_color(self._state, pal)
        base = pal.color(QPalette.Base)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(_mix(base, c, 0.45), 1))
        p.setBrush(_mix(base, c, 0.14))
        p.drawRoundedRect(r, 8, 8)
        p.end()


# ─────────────────────────────────────────────────────────────────────────────
# Resaltado de la salida
# ─────────────────────────────────────────────────────────────────────────────

_RE_ERR   = re.compile(r"(?i)\b(error|errors|failed|failure|fatal|cannot|can't|unable|denied|"
                       r"no such|not found|traceback|falló|fallo|no se pudo|denegado)\b")
_RE_WARN  = re.compile(r"(?i)\b(warning|warn|deprecated|aviso|advertencia)\b")
_RE_STAGE = re.compile(r"(?i)^\s*(\[\d+/\d+\]\s*)?(downloading|unpacking|configuring|installing|"
                       r"updating|removing|fetching|purging|verifying|checking|extracting|"
                       r"descargando|instalando|actualizando|desinstalando|eliminando)\b")
_RE_OK    = re.compile(r"(?i)\b(done|complete|completed|installed|finished|success|successfully|"
                       r"listo|completad[ao]|correctamente)\b")


class _OutputHighlighter(QSyntaxHighlighter):
    def __init__(self, doc, dark: bool):
        super().__init__(doc)
        self.set_dark(dark, rehighlight=False)

    def set_dark(self, dark: bool, rehighlight: bool = True) -> None:
        def fmt(color: str, italic=False) -> QTextCharFormat:
            f = QTextCharFormat()
            f.setForeground(QColor(color))
            f.setFontItalic(italic)
            return f
        if dark:
            self._err, self._warn = fmt("#F87171"), fmt("#FBBF24")
            self._ok, self._stage = fmt("#4ADE80"), fmt("#7DD3FC")
            self._foot = fmt("#8B949E", italic=True)
        else:
            self._err, self._warn = fmt("#B91C1C"), fmt("#92400E")
            self._ok, self._stage = fmt("#15803D"), fmt("#0369A1")
            self._foot = fmt("#6E7781", italic=True)
        if rehighlight:
            self.rehighlight()

    def highlightBlock(self, text: str) -> None:
        if text.startswith("──"):
            self.setFormat(0, len(text), self._foot)
        elif _RE_ERR.search(text):
            self.setFormat(0, len(text), self._err)
        elif _RE_WARN.search(text):
            self.setFormat(0, len(text), self._warn)
        elif _RE_STAGE.search(text):
            self.setFormat(0, len(text), self._stage)
        elif _RE_OK.search(text):
            self.setFormat(0, len(text), self._ok)


# ─────────────────────────────────────────────────────────────────────────────
# Tarjeta de la lista
# ─────────────────────────────────────────────────────────────────────────────

class TaskCard(QFrame):
    clicked          = Signal(str)
    cancel_requested = Signal(str)

    def __init__(self, task: Task, parent=None):
        super().__init__(parent)
        self.task_id = task.id
        self._task = task
        self._selected = False
        self._hover = False
        self.setObjectName("taskCard")
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        root = QHBoxLayout(self)
        root.setContentsMargins(16, 10, 10, 10)
        root.setSpacing(10)

        self._icon = _StateIcon(22)
        root.addWidget(self._icon, alignment=Qt.AlignTop)

        col = QVBoxLayout()
        col.setSpacing(2)
        top = QHBoxLayout()
        top.setSpacing(8)
        self._name = _ElidedLabel(task.package.name, pt=11, bold=True)
        top.addWidget(self._name, stretch=1)
        self._pill = _StatePill()
        top.addWidget(self._pill)
        col.addLayout(top)

        self._sub = _ElidedLabel("", muted=True, pt=9)
        col.addWidget(self._sub)
        self._status = _ElidedLabel("", muted=True, pt=9)
        col.addWidget(self._status)

        self._progress = NativeProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setFixedHeight(5)
        col.addWidget(self._progress)
        root.addLayout(col, stretch=1)

        self._cancel = TaskCancelButton("")
        self._cancel.setIcon(themed_icon(["window-close", "process-stop"], "cancel"))
        self._cancel.setFixedSize(26, 26)
        self._cancel.setToolTip(tr("task_cancel_tooltip"))
        self._cancel.setAutoDefault(False)
        self._cancel.clicked.connect(lambda: self.cancel_requested.emit(self.task_id))
        root.addWidget(self._cancel, alignment=Qt.AlignTop)

        self.refresh()

    @property
    def task(self) -> Task:
        return self._task

    @property
    def icon(self) -> _StateIcon:
        return self._icon

    def set_selected(self, sel: bool) -> None:
        if sel != self._selected:
            self._selected = sel
            self.update()

    def refresh(self) -> None:
        t = self._task
        st = t.state
        self._icon.set_state(st)
        self._pill.set_state(st)
        active = st in _ACTIVE
        self._progress.setVisible(active)
        self._progress.setValue(t.progress)
        self._cancel.setVisible(active)

        self._sub.set_full_text(f"{_type_label(t.task_type)} · {t.package.source_label}")

        if st == TaskState.PENDING:
            msg = tr("tasks_card_queued")
        elif st == TaskState.RUNNING:
            msg = _clean_line(t.status_text) or tr("task_running").rstrip("….")
        elif st == TaskState.DONE:
            d = _task_duration(t)
            msg = tr("tasks_card_done", t=_fmt_duration(d)) if d is not None else tr("task_completed_success")
        elif st == TaskState.CANCELLED:
            msg = tr("tasks_card_cancelled")
        else:
            err = (t.error_message or "").strip()
            msg = err.splitlines()[0] if err else tr("tasks_error_title")
        self._status.set_full_text(msg)
        self.update()

    def enterEvent(self, e):
        self._hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False
        self.update()
        super().leaveEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit(self.task_id)
        super().mouseReleaseEvent(e)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.clicked.emit(self.task_id)
            return
        super().keyPressEvent(e)

    def focusInEvent(self, e):
        self.update()
        super().focusInEvent(e)

    def focusOutEvent(self, e):
        self.update()
        super().focusOutEvent(e)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pal = self.palette()
        base, win = pal.color(QPalette.Base), pal.color(QPalette.Window)
        txt, hi = pal.color(QPalette.WindowText), pal.color(QPalette.Highlight)
        if self._selected:
            fill, edge = _mix(base, hi, 0.16), _mix(win, hi, 0.80)
        elif self._hover:
            fill, edge = _mix(base, txt, 0.06), _mix(win, txt, 0.28)
        else:
            fill, edge = base, _mix(win, txt, 0.14)
        if self.hasFocus() and not self._selected:
            edge = _mix(win, hi, 0.55)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(edge, 1))
        p.setBrush(fill)
        p.drawRoundedRect(r, 9, 9)
        # Barra de acento del estado
        p.setPen(Qt.NoPen)
        p.setBrush(_state_color(self._task.state, pal))
        p.drawRoundedRect(QRectF(r.left() + 5, r.top() + 9, 3, r.height() - 18), 1.5, 1.5)
        p.end()


# ─────────────────────────────────────────────────────────────────────────────
# Panel de detalle + consola de salida
# ─────────────────────────────────────────────────────────────────────────────

class TaskDetail(QWidget):
    cancel_requested  = Signal(str)
    retry_requested   = Signal(str)
    dismiss_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._task: Task | None = None
        self._shown = 0                  # nº de log_lines ya volcadas en la consola

        self._stack = QStackedWidget(self)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 6, 0, 0)
        outer.addWidget(self._stack)

        # ── página vacía ─────────────────────────────────────────────────────
        empty = QWidget()
        el = QVBoxLayout(empty)
        el.setAlignment(Qt.AlignCenter)
        el.setSpacing(10)
        self._empty_icon = QLabel()
        self._empty_icon.setAlignment(Qt.AlignCenter)
        el.addWidget(self._empty_icon)
        self._empty_lbl = QLabel(tr("tasks_select_hint"))
        self._empty_lbl.setAlignment(Qt.AlignCenter)
        self._empty_lbl.setWordWrap(True)
        el.addWidget(self._empty_lbl)
        self._stack.addWidget(empty)

        # ── página de detalle ────────────────────────────────────────────────
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        head = QHBoxLayout()
        head.setSpacing(12)
        self._icon = _StateIcon(34)
        head.addWidget(self._icon, alignment=Qt.AlignTop)
        tcol = QVBoxLayout()
        tcol.setSpacing(1)
        self._name = _ElidedLabel("", pt=15, bold=True)
        self._subtitle = _ElidedLabel("", muted=True, pt=10)
        tcol.addWidget(self._name)
        tcol.addWidget(self._subtitle)
        head.addLayout(tcol, stretch=1)
        self._pill = _StatePill()
        head.addWidget(self._pill, alignment=Qt.AlignTop)
        root.addLayout(head)

        prow = QHBoxLayout()
        prow.setSpacing(10)
        self._progress = NativeProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setFixedHeight(6)
        prow.addWidget(self._progress, stretch=1)
        self._percent = _ElidedLabel("", muted=True, pt=9, align=Qt.AlignRight)
        self._percent.setFixedWidth(44)
        prow.addWidget(self._percent)
        self._prow_widgets = (self._progress, self._percent)
        root.addLayout(prow)

        self._status = _ElidedLabel("", muted=True, pt=10)
        root.addWidget(self._status)

        meta = QHBoxLayout()
        meta.setSpacing(24)
        self._m_start = _MetaItem(tr("tasks_meta_started"))
        self._m_dur   = _MetaItem(tr("tasks_meta_duration"))
        self._m_src   = _MetaItem(tr("tasks_meta_source"))
        self._m_pkg   = _MetaItem(tr("tasks_meta_package"))
        for w in (self._m_start, self._m_dur, self._m_src):
            meta.addWidget(w)
        meta.addWidget(self._m_pkg, stretch=1)
        root.addLayout(meta)

        self._err_box = _TintBox(TaskState.FAILED)
        el2 = QVBoxLayout(self._err_box)
        el2.setContentsMargins(12, 9, 12, 9)
        el2.setSpacing(2)
        self._err_title = QLabel(tr("tasks_error_title"))
        f = self._err_title.font()
        f.setBold(True)
        self._err_title.setFont(f)
        self._err_msg = QLabel("")
        self._err_msg.setWordWrap(True)
        self._err_msg.setTextInteractionFlags(Qt.TextSelectableByMouse)
        el2.addWidget(self._err_title)
        el2.addWidget(self._err_msg)
        root.addWidget(self._err_box)

        # Contenedor de la salida (solo visible si el usuario lo pide)
        self._out_box = QWidget()
        obox = QVBoxLayout(self._out_box)
        obox.setContentsMargins(0, 0, 0, 0)
        obox.setSpacing(8)

        orow = QHBoxLayout()
        orow.setSpacing(8)
        o_lbl = QLabel(tr("tasks_output"))
        of = o_lbl.font()
        of.setBold(True)
        of.setPointSize(of.pointSize() + 1)
        o_lbl.setFont(of)
        orow.addWidget(o_lbl)
        self._lines_lbl = _ElidedLabel("", muted=True, pt=9)
        self._lines_lbl.setMinimumWidth(60)
        orow.addWidget(self._lines_lbl, stretch=1)
        obox.addLayout(orow)

        self._console = QPlainTextEdit()
        self._console.setObjectName("taskConsole")
        self._console.setReadOnly(True)
        self._console.setLineWrapMode(QPlainTextEdit.NoWrap)
        self._console.setMaximumBlockCount(_MAX_LINES + 2)
        self._console.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        self._console.setUndoRedoEnabled(False)
        self._hl = _OutputHighlighter(self._console.document(), _is_dark(self.palette()))
        obox.addWidget(self._console, stretch=1)

        # Copiar / guardar: debajo de la consola
        trow = QHBoxLayout()
        trow.setSpacing(8)
        trow.addStretch(1)
        self._copy_btn = _button(tr("tasks_copy_output"), ["edit-copy"])
        self._copy_btn.clicked.connect(self._copy)
        trow.addWidget(self._copy_btn)
        self._save_btn = _button(tr("tasks_save_output"), ["document-save", "document-save-as"])
        self._save_btn.clicked.connect(self._save)
        trow.addWidget(self._save_btn)
        obox.addLayout(trow)

        root.addWidget(self._out_box, stretch=1)
        self._out_box.setVisible(False)
        root.addStretch(1)
        self._spacer_idx = root.count() - 1
        self._root_lay = root

        # Barra inferior: [Ver/Ocultar salida] ........ [acciones de la tarea]
        arow = QHBoxLayout()
        arow.setSpacing(8)
        self._out_toggle = _button(tr("tasks_show_output"), ["utilities-terminal", "terminal"], "terminal")
        self._out_toggle.setCheckable(True)
        self._out_toggle.toggled.connect(self._on_output_toggled)
        arow.addWidget(self._out_toggle)
        arow.addStretch(1)
        self._btn_cancel  = _button(tr("tasks_cancel"), ["process-stop", "window-close"], "cancel")
        self._btn_retry   = _button(tr("tasks_retry"), ["view-refresh", "reload"], "reload")
        self._btn_dismiss = _button(tr("tasks_dismiss"), ["edit-delete", "list-remove"])
        self._btn_cancel.clicked.connect(lambda: self._task and self.cancel_requested.emit(self._task.id))
        self._btn_retry.clicked.connect(lambda: self._task and self.retry_requested.emit(self._task.id))
        self._btn_dismiss.clicked.connect(lambda: self._task and self.dismiss_requested.emit(self._task.id))
        for b in (self._btn_cancel, self._btn_retry, self._btn_dismiss):
            arow.addWidget(b)
        root.addLayout(arow)

        self._stack.addWidget(page)
        self._style_console()
        self._update_empty_icon()
        self._stack.setCurrentIndex(0)

    # ── estilo ───────────────────────────────────────────────────────────────
    def _style_console(self) -> None:
        pal = self.palette()
        dark = _is_dark(pal)
        bg, fg = ("#1b1d20", "#d6d9dd") if dark else ("#fbfbfc", "#24292f")
        border = _mix(pal.color(QPalette.Window), pal.color(QPalette.WindowText), 0.20).name()
        self._console.setStyleSheet(
            f"QPlainTextEdit#taskConsole {{ background: {bg}; color: {fg}; "
            f"border: 1px solid {border}; border-radius: 8px; padding: 8px; "
            f"selection-background-color: {pal.color(QPalette.Highlight).name()}; "
            f"selection-color: {pal.color(QPalette.HighlightedText).name()}; }}"
        )
        self._hl.set_dark(dark)

    def _update_empty_icon(self) -> None:
        pal = self.palette()
        col = _mix(pal.color(QPalette.WindowText), pal.color(QPalette.Window), 0.6)
        pm = symbolic_icon("terminal", color=col, size=56).pixmap(QSize(56, 56), _dpr())
        self._empty_icon.setPixmap(pm)

    def changeEvent(self, e):
        if e.type() == QEvent.PaletteChange:
            self._style_console()
            self._update_empty_icon()
        super().changeEvent(e)

    # ── API pública ──────────────────────────────────────────────────────────
    @property
    def icon(self) -> _StateIcon:
        return self._icon

    def _output_open(self) -> bool:
        return self._out_toggle.isChecked()

    def _on_output_toggled(self, on: bool) -> None:
        self._out_toggle.setText(tr("tasks_hide_output") if on else tr("tasks_show_output"))
        self._out_box.setVisible(on)
        self._root_lay.setStretch(self._spacer_idx, 0 if on else 1)
        if on:
            self._reload_output()
        else:
            self._console.clear()

    def set_task(self, task: Task | None) -> None:
        self._task = task
        if task is None:
            self._stack.setCurrentIndex(0)
            return
        self._stack.setCurrentIndex(1)
        self.refresh()
        if self._output_open():
            self._reload_output()

    def refresh(self) -> None:
        """Actualiza cabecera, metadatos y botones (no toca la consola)."""
        t = self._task
        if t is None:
            return
        st = t.state
        self._icon.set_state(st)
        self._pill.set_state(st)
        self._name.set_full_text(t.package.name)
        sub = f"{_type_label(t.task_type)} · {t.package.source_label}"
        ver = t.package.version or t.package.installed_version
        if ver:
            sub += f" · {ver}"
        self._subtitle.set_full_text(sub)

        active = st in _ACTIVE
        for w in self._prow_widgets:
            w.setVisible(active)
        self._progress.setValue(t.progress)
        self._percent.set_full_text(f"{t.progress} %")

        if st == TaskState.PENDING:
            status = tr("tasks_card_queued")
        elif st == TaskState.RUNNING:
            status = _clean_line(t.status_text) or tr("task_running").rstrip("….")
        else:
            status = ""                 # el estado final ya lo dice la etiqueta
        self._status.set_full_text(status)
        self._status.setVisible(bool(status))

        self._m_start.set_value(_fmt_clock(t.started_at) if t.started_at else tr("tasks_meta_queued"))
        self._m_src.set_value(t.package.source_label)
        self._m_pkg.set_value(t.package.id, expand=True)
        self._tick_duration()

        show_err = st == TaskState.FAILED
        self._err_box.setVisible(show_err)
        if show_err:
            self._err_msg.setText((t.error_message or "").strip() or tr("tasks_error_title"))

        self._console.setPlaceholderText(
            tr("tasks_output_queued") if st == TaskState.PENDING else tr("tasks_output_empty"))

        self._btn_cancel.setVisible(active)
        self._btn_retry.setVisible(st in _FINISHED)
        self._btn_dismiss.setVisible(st in _FINISHED)

    def tick(self) -> None:
        """Llamado cada segundo: mantiene viva la duración."""
        if self._task and self._task.state in _ACTIVE:
            self._tick_duration()

    def on_progress(self) -> None:
        if self._task is None:
            return
        self.refresh()
        if self._output_open():
            self._sync_output()

    def on_finished(self) -> None:
        if self._task is None:
            return
        self.refresh()
        if self._output_open():
            self._reload_output()

    def resync(self) -> None:
        """Se llama al volver a mostrar la ventana: se pudo perder salida."""
        if self._task is not None:
            self.refresh()
            if self._output_open():
                self._sync_output()

    def _tick_duration(self) -> None:
        d = _task_duration(self._task) if self._task else None
        self._m_dur.set_value(_fmt_duration(d) if d is not None else "—")

    # ── salida ───────────────────────────────────────────────────────────────
    def _footer(self) -> str:
        t = self._task
        if t is None or t.state not in _FINISHED:
            return ""
        d = _task_duration(t)
        key = {
            TaskState.DONE:      "tasks_footer_done",
            TaskState.FAILED:    "tasks_footer_failed",
            TaskState.CANCELLED: "tasks_footer_cancelled",
        }[t.state]
        return tr(key, t=_fmt_duration(d) if d is not None else "—")

    def _reload_output(self) -> None:
        """Vuelca toda la salida de la tarea (más el pie si ya terminó)."""
        t = self._task
        if t is None:
            return
        raw = list(t.log_lines)
        self._shown = len(raw)
        lines = [_clean_line(l) for l in raw[-_MAX_LINES:]]
        footer = self._footer()
        if footer:
            lines += ["", footer]
        self._console.setPlainText("\n".join(lines))
        sb = self._console.verticalScrollBar()
        sb.setValue(sb.maximum())
        self._update_counter()

    def _sync_output(self) -> None:
        """Añade solo las líneas nuevas (la tarea sigue en curso)."""
        t = self._task
        if t is None:
            return
        n = len(t.log_lines)
        if n < self._shown:              # la lista se reinició
            self._reload_output()
            return
        if n == self._shown:
            return
        new = [_clean_line(l) for l in t.log_lines[self._shown:n]]
        self._shown = n
        sb = self._console.verticalScrollBar()
        at_bottom = sb.value() >= sb.maximum() - 4
        self._console.appendPlainText("\n".join(new))
        if at_bottom:
            sb.setValue(sb.maximum())
        self._update_counter()

    def _update_counter(self) -> None:
        self._lines_lbl.set_full_text(tr("tasks_output_lines", n=self._shown))
        has = self._shown > 0
        self._copy_btn.setEnabled(has)
        self._save_btn.setEnabled(has)

    # ── copiar / guardar ─────────────────────────────────────────────────────
    def _copy(self) -> None:
        cur = self._console.textCursor()
        text = (cur.selectedText().replace("\u2029", "\n") if cur.hasSelection()
                else self._console.toPlainText() + "\n")
        QGuiApplication.clipboard().setText(text)
        QToolTip.showText(QCursor.pos(), tr("tasks_copied"), self._copy_btn)

    def _save(self) -> None:
        t = self._task
        if t is None:
            return
        safe = re.sub(r"[^\w.\-]+", "_", t.package.name).strip("_") or "task"
        default = str(Path.home() / f"{safe}-{time.strftime('%Y%m%d-%H%M%S')}.log")
        path, _ = QFileDialog.getSaveFileName(
            self, tr("tasks_save_dialog_title"), default, "Log (*.log *.txt);;*"
        )
        if not path:
            return
        try:
            Path(path).write_text(self._console.toPlainText() + "\n", encoding="utf-8")
        except OSError as e:
            QMessageBox.warning(self, tr("tasks_save_dialog_title"),
                                tr("tasks_save_failed", error=str(e)))


# ─────────────────────────────────────────────────────────────────────────────
# Página principal
# ─────────────────────────────────────────────────────────────────────────────

class TasksPage(QWidget):
    """
    Gestor de tareas: historial a la izquierda y detalle con salida en vivo
    a la derecha. Se usa tanto en la ventana independiente (TasksWindow)
    como embebida (show_header=False).
    """

    active_count_changed = Signal(int)

    def __init__(self, task_manager: TaskManager, parent=None, show_header: bool = True):
        super().__init__(parent)
        self._tm = task_manager
        self._cards: dict[str, TaskCard] = {}
        self._selected_id: str | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(10)

        # ── cabecera ─────────────────────────────────────────────────────────
        if show_header:
            hdr_row = QHBoxLayout()
            hdr = QLabel(tr("nav_tasks"))
            hdr.setObjectName("sectionHeader")
            hdr_row.addWidget(hdr)
            hdr_row.addStretch()
            self._clear_btn = _button(tr("tasks_clear_finished"), ["edit-clear-all", "edit-clear"], "broom")
            self._clear_btn.clicked.connect(self._clear_finished)
            hdr_row.addWidget(self._clear_btn)
            root.addLayout(hdr_row)
        else:
            self._clear_btn = None

        self._status_lbl = QLabel(tr("tasks_no_active"))
        self._status_lbl.setObjectName("sectionSub")
        root.addWidget(self._status_lbl)

        sep = QFrame()
        sep.setObjectName("separator")
        sep.setFrameShape(QFrame.HLine)
        root.addWidget(sep)

        # ── cuerpo: lista | detalle ──────────────────────────────────────────
        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        split.setHandleWidth(8)
        root.addWidget(split, stretch=1)
        if not show_header:
            split.setMinimumHeight(360)

        left = QWidget()
        left.setMinimumWidth(290)
        left.setMaximumWidth(480)
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 4, 0)
        ll.setSpacing(8)

        frow = QHBoxLayout()
        frow.setSpacing(6)
        self._search = QLineEdit()
        self._search.setPlaceholderText(tr("tasks_search_placeholder"))
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._apply_search)
        frow.addWidget(self._search, stretch=1)
        ll.addLayout(frow)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._list_widget = QWidget()
        self._list_layout = QVBoxLayout(self._list_widget)
        self._list_layout.setContentsMargins(0, 0, 6, 0)
        self._list_layout.setSpacing(6)
        self._empty_lbl = QLabel(tr("tasks_empty_list"))
        self._empty_lbl.setAlignment(Qt.AlignCenter)
        self._empty_lbl.setContentsMargins(0, 30, 0, 0)
        self._list_layout.addWidget(self._empty_lbl)
        self._list_layout.addStretch(1)
        scroll.setWidget(self._list_widget)
        ll.addWidget(scroll, stretch=1)
        split.addWidget(left)

        self._detail = TaskDetail()
        self._detail.setMinimumWidth(420)
        self._detail.cancel_requested.connect(self._tm.cancel)
        self._detail.retry_requested.connect(self._retry)
        self._detail.dismiss_requested.connect(self._tm.remove_task)
        split.addWidget(self._detail)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([360, 640])

        # ── temporizadores ───────────────────────────────────────────────────
        self._spin = QTimer(self)
        self._spin.setInterval(90)
        self._spin.timeout.connect(self._tick_spin)
        self._clock = QTimer(self)
        self._clock.setInterval(1000)
        self._clock.timeout.connect(self._detail.tick)

        # ── señales del TaskManager ──────────────────────────────────────────
        tm = task_manager
        tm.task_added.connect(self._on_task_added)
        tm.task_started.connect(self._on_task_started)
        tm.task_updated.connect(self._on_task_updated)
        tm.task_completed.connect(self._on_task_completed)
        tm.task_cancelled.connect(self._on_task_cancelled)
        tm.task_removed.connect(self._on_task_removed)

        # Tareas que ya existían antes de abrir la ventana (más nuevas arriba)
        for task in self._tm.all_tasks:
            self._add_card(task)
        if self._cards:
            self._select(next(iter(reversed(self._cards))))
        self._apply_search()
        self._refresh_status()

    # ── ciclo de vida ────────────────────────────────────────────────────────
    def showEvent(self, e):
        super().showEvent(e)
        self._clock.start()
        self._ensure_spin()
        self._detail.resync()

    def hideEvent(self, e):
        super().hideEvent(e)
        self._clock.stop()
        self._spin.stop()

    # ── tarjetas ─────────────────────────────────────────────────────────────
    def _add_card(self, task: Task) -> TaskCard:
        card = TaskCard(task)
        card.clicked.connect(self._select)
        card.cancel_requested.connect(self._tm.cancel)
        self._cards[task.id] = card
        self._list_layout.insertWidget(1, card)      # 0 = etiqueta "vacío"
        return card

    def _select(self, task_id: str) -> None:
        card = self._cards.get(task_id)
        if card is None:
            return
        self._selected_id = task_id
        for tid, c in self._cards.items():
            c.set_selected(tid == task_id)
        self._detail.set_task(card.task)
        self._ensure_spin()

    def _visible(self, task: Task) -> bool:
        q = self._search.text().strip().lower()
        if q and q not in task.package.name.lower() and q not in task.package.id.lower():
            return False
        return True

    def _apply_search(self, *_):
        shown = 0
        for c in self._cards.values():
            v = self._visible(c.task)
            c.setVisible(v)
            shown += v
        self._empty_lbl.setVisible(shown == 0)

    # ── animación ────────────────────────────────────────────────────────────
    def _any_running(self) -> bool:
        return any(c.task.state == TaskState.RUNNING for c in self._cards.values())

    def _ensure_spin(self) -> None:
        if self.isVisible() and self._any_running():
            if not self._spin.isActive():
                self._spin.start()
        else:
            self._spin.stop()

    def _tick_spin(self) -> None:
        running = False
        for c in self._cards.values():
            if c.task.state == TaskState.RUNNING:
                running = True
                if c.isVisible():
                    c.icon.advance()
        if self._selected_id:
            t = self._tm.get(self._selected_id)
            if t and t.state == TaskState.RUNNING:
                self._detail.icon.advance()
        if not running:
            self._spin.stop()

    # ── slots del TaskManager ────────────────────────────────────────────────
    @Slot(object)
    def _on_task_added(self, task: Task):
        self._add_card(task)
        self._apply_search()
        self._refresh_status()
        if self._selected_id is None:
            self._select(task.id)

    @Slot(str)
    def _on_task_started(self, task_id: str):
        c = self._cards.get(task_id)
        if c:
            c.refresh()
        if task_id == self._selected_id:
            self._detail.refresh()
        self._ensure_spin()
        self._apply_search()
        self._refresh_status()

    @Slot(str, int, str)
    def _on_task_updated(self, task_id: str, progress: int, status: str):
        c = self._cards.get(task_id)
        if c:
            c.refresh()
        if task_id == self._selected_id:
            self._detail.on_progress()
        self._ensure_spin()

    @Slot(str, bool, str)
    def _on_task_completed(self, task_id: str, success: bool, message: str):
        self._after_finish(task_id)

    @Slot(str)
    def _on_task_cancelled(self, task_id: str):
        self._after_finish(task_id)

    def _after_finish(self, task_id: str) -> None:
        c = self._cards.get(task_id)
        if c:
            c.refresh()
        if task_id == self._selected_id:
            self._detail.on_finished()
        self._apply_search()
        self._refresh_status()
        self._ensure_spin()

    @Slot(str)
    def _on_task_removed(self, task_id: str):
        c = self._cards.pop(task_id, None)
        if c:
            self._list_layout.removeWidget(c)
            c.deleteLater()
        if task_id == self._selected_id:
            self._selected_id = None
            self._detail.set_task(None)
            nxt = next((tid for tid, cc in self._cards.items()
                        if self._visible(cc.task)), None)
            if nxt:
                self._select(nxt)
        self._apply_search()
        self._refresh_status()

    # ── acciones ─────────────────────────────────────────────────────────────
    def _retry(self, task_id: str) -> None:
        new = self._tm.retry(task_id)
        if new is not None:
            self._select(new.id)

    def _clear_finished(self) -> None:
        self._tm.clear_finished()

    def _refresh_status(self) -> None:
        active = len(self._tm.active_tasks)
        total  = len(self._tm.all_tasks)
        if active > 0:
            self._status_lbl.setText(tr("tasks_active_summary").format(active=active, total=total))
        elif total > 0:
            self._status_lbl.setText(tr("tasks_no_active_with_finished").format(total=total))
        else:
            self._status_lbl.setText(tr("tasks_no_active"))
        if self._clear_btn is not None:
            self._clear_btn.setEnabled(total - active > 0)
        self.active_count_changed.emit(active)
