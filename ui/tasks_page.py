# Yelena Software — Gestor de tareas
#
# Tabla operativa compacta: una fila por tarea con
#   [estado] PAQUETE | operación | origen | hora | duración | [Log v] [↻] [X]
# El log de la terminal se despliega en línea, justo debajo de su fila.
# Barra superior única: búsqueda, filtro, contadores y "Limpiar".
#
# Los colores salen de la paleta activa; solo los acentos semánticos
# (verde/rojo/ámbar) están fijados y se ajustan al brillo del tema.

from __future__ import annotations

import re
import time
from pathlib import Path

from PySide6.QtCore import Qt, Signal, Slot, QTimer, QSize, QEvent
from PySide6.QtGui import (
    QColor, QPalette, QPainter, QFontDatabase,
    QTextCharFormat, QSyntaxHighlighter, QGuiApplication, QCursor,
)
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QScrollArea, QFrame,
    QLineEdit, QPlainTextEdit, QPushButton, QStackedWidget, QFileDialog,
    QMessageBox, QToolTip, QSizePolicy,
)

from core.i18n import tr
from backend.models import Task, TaskState, TaskType
from backend.task_manager import TaskManager
from .native_widgets import (
    NativeProgressBar, CardFrame, BadgeLabel, SegmentedControl,
    MultilineElideLabel, GhostRemoveButton, symbolic_icon, _mix,
)
from .chibi_widget import ChibiPlaceholder
from .icon_widget import AppIconWidget

_ACTIVE   = (TaskState.PENDING, TaskState.RUNNING)
_FINISHED = (TaskState.DONE, TaskState.FAILED, TaskState.CANCELLED)

# Tope de líneas conservadas en la consola (evita congelar la UI).
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

# Tipo de insignia (color) y clave de traducción por estado
_STATE_BADGE = {
    TaskState.PENDING:   ("task_pending",   "task_pending"),
    TaskState.RUNNING:   ("task_running",   "task_running"),
    TaskState.DONE:      ("task_done",      "task_done"),
    TaskState.FAILED:    ("task_failed",    "task_failed"),
    TaskState.CANCELLED: ("task_cancelled", "task_cancelled"),
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
        TaskState.DONE:      ("#66E676", "#1B5E20"),
        TaskState.FAILED:    ("#FF7771", "#B71C1C"),
        TaskState.CANCELLED: ("#FFC247", "#7A4B00"),
        TaskState.PENDING:   ("#B9C0C9", "#50555D"),
    }
    d, l = table.get(state, table[TaskState.PENDING])
    return QColor(d if dark else l)


def _type_label(tt: TaskType) -> str:
    key = _TYPE_KEYS.get(tt)
    text = tr(key) if key else tt.value
    return tt.value if text == key else text


def _clean_line(raw: str) -> str:
    """Quita ANSI y se queda con el último tramo tras '\\r' (barras de progreso)."""
    if "\r" in raw:
        parts = [p for p in raw.split("\r") if p.strip()]
        raw = parts[-1] if parts else ""
    return _ANSI_RE.sub("", raw).rstrip()


def _fmt_duration(seconds: float) -> str:
    s = max(0, int(seconds))
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}m {s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m"


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
    return (task.finished_at or time.time()) - task.started_at


def _set_icon(btn: QPushButton, kind: str, size: int = 16,
              on_accent: bool = False) -> None:
    """Asigna un icono symbolic (SVG dibujado, color del tema) a un botón y
    recuerda su tipo para repintarlo si cambia la paleta."""
    btn.setProperty("iconKind", kind)
    btn.setProperty("iconOnAccent", on_accent)
    btn.setIconSize(QSize(size, size))
    pal = btn.palette()
    col = pal.color(QPalette.HighlightedText if on_accent else QPalette.ButtonText)
    btn.setIcon(symbolic_icon(kind, color=col, size=size))


def _refresh_icons(root: QWidget) -> None:
    for b in root.findChildren(QPushButton):
        kind = b.property("iconKind")
        if kind:
            _set_icon(b, kind, b.iconSize().width() or 16,
                      bool(b.property("iconOnAccent")))


def _flat_button(text: str, tip: str = "", w: int | None = None,
                 checkable: bool = False, h: int = 32) -> QPushButton:
    """Botón de texto compacto, sin autoDefault (Enter en el buscador no
    debe disparar 'Limpiar' ni 'Cancelar' por accidente)."""
    b = QPushButton(text)
    b.setAutoDefault(False)
    b.setDefault(False)
    b.setCursor(Qt.PointingHandCursor)
    b.setFocusPolicy(Qt.TabFocus)
    b.setCheckable(checkable)
    b.setFixedHeight(h)
    if tip:
        b.setToolTip(tip)
        b.setAccessibleName(tip)
    if w:
        b.setFixedWidth(w)
    return b


# ─────────────────────────────────────────────────────────────────────────────
# Etiqueta elidida
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
# Tarjeta de tarea (mismo contenedor que una app instalada) + log en línea
# ─────────────────────────────────────────────────────────────────────────────

class TaskRow(CardFrame):
    """Tarjeta: [icono] NOMBRE / tipo · hora (dur)  [fuente][estado][v][acción].
    Al pulsar [ v ] se expande hacia abajo mostrando la salida del proceso."""

    cancel_requested  = Signal(str)
    retry_requested   = Signal(str)
    dismiss_requested = Signal(str)

    def __init__(self, task: Task, parent=None):
        super().__init__(parent)
        self.task_id = task.id
        self._task = task
        self._shown = 0               # líneas de log ya volcadas en la consola
        self.setObjectName("appCard")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 8, 12, 8)
        root.setSpacing(6)

        # ── cabecera ─────────────────────────────────────────────────────────
        head = QHBoxLayout()
        head.setSpacing(12)
        pkg = task.package
        head.addWidget(AppIconWidget(
            pkg.name, icon_name=pkg.icon_name, icon_url=pkg.icon_url,
            size=36, radius=9, fallback_icon_name=pkg.icon_fallback_name))

        info = QVBoxLayout()
        info.setSpacing(1)
        self._name = MultilineElideLabel(pkg.name, max_lines=1)
        self._name.setObjectName("cardAppName")
        info.addWidget(self._name)
        self._sub = QLabel("")
        self._sub.setObjectName("cardAppDesc")
        info.addWidget(self._sub)
        head.addLayout(info, stretch=1)

        self._src_badge = BadgeLabel(pkg.source_label, pkg.badge_type, show_icon=False)
        head.addWidget(self._src_badge)
        self._state_badge = BadgeLabel("", "task_pending", show_icon=False)
        head.addWidget(self._state_badge)

        self._toggle = _flat_button("", tr("tasks_show_output"), 34, checkable=True)
        _set_icon(self._toggle, "chevron_down")
        self._toggle.toggled.connect(self._on_toggled)
        head.addWidget(self._toggle)

        self._retry_btn = _flat_button("", tr("tasks_retry"), 34)
        _set_icon(self._retry_btn, "reload")
        self._retry_btn.clicked.connect(lambda: self.retry_requested.emit(self.task_id))
        head.addWidget(self._retry_btn)

        self._cancel_btn = _flat_button("", tr("tasks_cancel"), 34)
        _set_icon(self._cancel_btn, "cancel")
        self._cancel_btn.clicked.connect(lambda: self.cancel_requested.emit(self.task_id))
        head.addWidget(self._cancel_btn)

        self._trash = GhostRemoveButton(tr("tasks_dismiss"))
        self._trash.setFixedWidth(34)
        self._trash.clicked.connect(lambda: self.dismiss_requested.emit(self.task_id))
        head.addWidget(self._trash)
        root.addLayout(head)

        # ── barra de progreso fina (solo activas) ────────────────────────────
        self._progress = NativeProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setFixedHeight(4)
        root.addWidget(self._progress)

        # ── salida desplegable ───────────────────────────────────────────────
        self._log_box = QWidget()
        lb = QVBoxLayout(self._log_box)
        lb.setContentsMargins(0, 2, 0, 0)
        lb.setSpacing(4)
        bar = QHBoxLayout()
        bar.setSpacing(6)
        self._log_icon = QLabel()
        self._log_icon.setFixedSize(16, 16)
        bar.addWidget(self._log_icon)
        self._log_title = QLabel("")
        self._log_title.setObjectName("cardAppDesc")
        bar.addWidget(self._log_title, stretch=1)
        self._copy_btn = _flat_button("", tr("tasks_copy_output"), 34, h=28)
        _set_icon(self._copy_btn, "copy")
        self._copy_btn.clicked.connect(self._copy)
        self._save_btn = _flat_button("", tr("tasks_save_output"), 34, h=28)
        _set_icon(self._save_btn, "save")
        self._save_btn.clicked.connect(self._save)
        bar.addWidget(self._copy_btn)
        bar.addWidget(self._save_btn)
        lb.addLayout(bar)

        self._console = QPlainTextEdit()
        self._console.setObjectName("taskConsole")
        self._console.setReadOnly(True)
        self._console.setLineWrapMode(QPlainTextEdit.NoWrap)
        self._console.setMaximumBlockCount(_MAX_LINES + 2)
        self._console.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        self._console.setUndoRedoEnabled(False)
        self._console.setFixedHeight(190)
        self._hl = _OutputHighlighter(self._console.document(), _is_dark(self.palette()))
        lb.addWidget(self._console)
        root.addWidget(self._log_box)
        self._log_box.setVisible(False)

        self.refresh()

    # ── API ──────────────────────────────────────────────────────────────────
    @property
    def task(self) -> Task:
        return self._task

    def log_open(self) -> bool:
        return self._toggle.isChecked()

    def open_log(self) -> None:
        if not self._toggle.isChecked():
            self._toggle.setChecked(True)

    def refresh(self) -> None:
        t = self._task
        st = t.state
        active = st in _ACTIVE
        badge_type, key = _STATE_BADGE[st]
        self._state_badge.set_badge_type(badge_type)
        self._state_badge.setText(tr(key).rstrip("….").strip())

        self._progress.setVisible(active)
        if active and t.progress_indeterminate:
            self._progress.setRange(0, 0)
        else:
            self._progress.setRange(0, 100)
            self._progress.setValue(t.progress)

        self._cancel_btn.setVisible(active)
        self._retry_btn.setVisible(st in _FINISHED)
        self._trash.setVisible(st in _FINISHED)
        self._name.setToolTip(t.package.id)
        self.refresh_sub()
        self._apply_palette()

    def refresh_sub(self) -> None:
        t = self._task
        text = f"{_type_label(t.task_type)} · {_fmt_clock(t.started_at or t.created_at)}"
        d = _task_duration(t)
        if d is not None:
            text += f" ({_fmt_duration(d)})"
        if t.state == TaskState.RUNNING:
            status = _clean_line(t.status_text)
            if status:
                text += f" · {status}"
        elif t.state == TaskState.FAILED:
            err = (t.error_message or "").strip().splitlines()
            if err:
                text += f" · {err[0]}"
        self._sub.setText(text)

    def on_progress(self) -> None:
        self.refresh()
        if self.log_open():
            self._sync_output()

    def on_finished(self) -> None:
        self.refresh()
        if self.log_open():
            self._reload_output()

    def resync(self) -> None:
        self.refresh()
        if self.log_open():
            self._sync_output()

    def changeEvent(self, e):
        if e.type() == QEvent.PaletteChange:
            self._apply_palette()
        super().changeEvent(e)

    def _apply_palette(self) -> None:
        pal = self.palette()
        dark = _is_dark(pal)
        bg, fg = ("#1b1d20", "#d6d9dd") if dark else ("#fbfbfc", "#24292f")
        border = _mix(pal.color(QPalette.Window), pal.color(QPalette.WindowText), 0.20).name()
        self._console.setStyleSheet(
            f"QPlainTextEdit#taskConsole {{ background: {bg}; color: {fg}; "
            f"border: 1px solid {border}; border-radius: 6px; padding: 6px; "
            f"selection-background-color: {pal.color(QPalette.Highlight).name()}; "
            f"selection-color: {pal.color(QPalette.HighlightedText).name()}; }}"
        )
        self._hl.set_dark(dark)
        _refresh_icons(self)
        pm = symbolic_icon("terminal", color=pal.color(QPalette.WindowText),
                           size=16).pixmap(QSize(16, 16))
        self._log_icon.setPixmap(pm)

    # ── despliegue ───────────────────────────────────────────────────────────
    def _on_toggled(self, on: bool) -> None:
        _set_icon(self._toggle, "chevron_up" if on else "chevron_down")
        self._log_box.setVisible(on)
        if on:
            self._reload_output()
        else:
            self._console.clear()
        self.updateGeometry()

    def mousePressEvent(self, e):
        from PySide6.QtWidgets import QAbstractButton
        if (e.button() == Qt.LeftButton
                and not isinstance(self.childAt(e.position().toPoint()), QAbstractButton)
                and e.position().y() < 56):
            self._toggle.toggle()
            return
        super().mousePressEvent(e)

    # ── salida ───────────────────────────────────────────────────────────────
    def _footer(self) -> str:
        t = self._task
        if t.state not in _FINISHED:
            return ""
        d = _task_duration(t)
        key = {
            TaskState.DONE:      "tasks_footer_done",
            TaskState.FAILED:    "tasks_footer_failed",
            TaskState.CANCELLED: "tasks_footer_cancelled",
        }[t.state]
        return tr(key, t=_fmt_duration(d) if d is not None else "—")

    def _reload_output(self) -> None:
        t = self._task
        raw = list(t.log_lines)
        self._shown = len(raw)
        lines = [_clean_line(l) for l in raw[-_MAX_LINES:]]
        if not lines:
            if t.state == TaskState.FAILED and (t.error_message or "").strip():
                lines = [t.error_message.strip()]
            elif t.state == TaskState.PENDING:
                lines = [tr("tasks_output_queued")]
        footer = self._footer()
        if footer:
            lines += ["", footer]
        self._console.setPlainText("\n".join(lines))
        sb = self._console.verticalScrollBar()
        sb.setValue(sb.maximum())
        self._update_counter()

    def _sync_output(self) -> None:
        t = self._task
        n = len(t.log_lines)
        if n < self._shown:
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
        self._log_title.setText(tr("tasks_output") + " ("
                                + tr("tasks_output_lines", n=self._shown) + "):")
        has = self._console.document().characterCount() > 1
        self._copy_btn.setEnabled(has)
        self._save_btn.setEnabled(has)

    def _copy(self) -> None:
        cur = self._console.textCursor()
        text = (cur.selectedText().replace("\u2029", "\n") if cur.hasSelection()
                else self._console.toPlainText() + "\n")
        QGuiApplication.clipboard().setText(text)
        QToolTip.showText(QCursor.pos(), tr("tasks_copied"), self._copy_btn)

    def _save(self) -> None:
        safe = re.sub(r"[^\w.\-]+", "_", self._task.package.name).strip("_") or "task"
        default = str(Path.home() / f"{safe}-{time.strftime('%Y%m%d-%H%M%S')}.log")
        path, _ = QFileDialog.getSaveFileName(
            self, tr("tasks_save_dialog_title"), default, "Log (*.log *.txt);;*")
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

_LIST_WIDTH_RATIO = 0.8          # las tarjetas ocupan el 80% del ancho


class TasksPage(QWidget):
    """
    Gestor de tareas con el mismo patrón que Instaladas: barra de chips +
    acción principal arriba y tarjetas centradas al 80% del ancho, con la
    salida del proceso desplegable en línea. `show_header` se conserva por
    compatibilidad con incrustaciones.
    """

    active_count_changed = Signal(int)

    def __init__(self, task_manager: TaskManager, parent=None, show_header: bool = True):
        super().__init__(parent)
        self._tm = task_manager
        self._rows: dict[str, TaskRow] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 20)
        root.setSpacing(10)

        # ── barra local: chips + búsqueda + acción principal ────────────────
        bar = QWidget()
        bar_lay = QHBoxLayout(bar)
        bar_lay.setContentsMargins(0, 0, 0, 0)
        bar_lay.setSpacing(10)

        self._chips = SegmentedControl([
            ("all",    tr("filter_all")),
            ("active", tr("tasks_filter_in_progress")),
            ("done",   tr("tasks_filter_completed")),
            ("failed", tr("tasks_filter_failed")),
        ])
        self._chips.changed.connect(lambda _k: self._apply_filter())
        bar_lay.addWidget(self._chips)

        self._search = QLineEdit()
        self._search.setObjectName("searchBar")
        self._search.setPlaceholderText(tr("tasks_search_placeholder"))
        self._search.setClearButtonEnabled(True)
        self._search.setMinimumWidth(180)
        self._search.setMaximumWidth(320)
        self._search.setFixedHeight(32)
        self._search.textChanged.connect(self._apply_filter)
        self._search.setVisible(False)       # la lupa del menú la muestra/oculta
        bar_lay.addWidget(self._search, stretch=1)
        bar_lay.addStretch(1)

        self._clear_btn = QPushButton(" " + tr("tasks_clear_finished"))
        _set_icon(self._clear_btn, "broom", on_accent=True)
        self._clear_btn.setObjectName("tasksClearFinished")
        self._clear_btn.setCursor(Qt.PointingHandCursor)
        self._clear_btn.setAutoDefault(False)
        self._clear_btn.setFixedHeight(32)
        self._clear_btn.setStyleSheet(
            "QPushButton#tasksClearFinished { background: palette(highlight); "
            "color: palette(highlighted-text); border: none; border-radius: 8px; "
            "padding: 0 14px; font-weight: bold; }"
            "QPushButton#tasksClearFinished:hover { background: palette(highlight); "
            "border: 2px solid rgba(255,255,255,70); }"
            "QPushButton#tasksClearFinished:disabled { background: palette(mid); "
            "color: palette(window); }"
        )
        self._clear_btn.clicked.connect(self._tm.clear_finished)
        bar_lay.addWidget(self._clear_btn)
        root.addWidget(bar)

        # ── lista de tarjetas centrada al 80% ───────────────────────────────
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self._list = QWidget()
        self._list_layout = QVBoxLayout(self._list)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(4)
        self._list_layout.addStretch(1)

        wrap = QWidget()
        wrap_lay = QHBoxLayout(wrap)
        wrap_lay.setContentsMargins(0, 0, 0, 0)
        wrap_lay.addStretch(1)
        wrap_lay.addWidget(self._list)
        wrap_lay.addStretch(1)
        self._scroll.setWidget(wrap)

        # Estado vacío: chibi + texto centrados en toda el área (horizontal
        # y verticalmente); la lista de tarjetas solo se ve si hay tareas.
        self._empty_lbl = ChibiPlaceholder(
            "yel-chibi-empty", tr("tasks_empty_list"), size=240)
        self._body = QStackedWidget()
        self._body.addWidget(self._scroll)
        self._body.addWidget(self._empty_lbl)
        root.addWidget(self._body, stretch=1)
        if not show_header:
            self._scroll.setMinimumHeight(360)

        # ── temporizador de duración ─────────────────────────────────────────
        self._clock = QTimer(self)
        self._clock.setInterval(1000)
        self._clock.timeout.connect(self._tick_clock)

        # ── señales del TaskManager ──────────────────────────────────────────
        tm = task_manager
        tm.task_added.connect(self._on_task_added)
        tm.task_started.connect(self._on_task_changed)
        tm.task_updated.connect(self._on_task_updated)
        tm.task_completed.connect(self._on_task_finished)
        tm.task_cancelled.connect(self._on_task_finished)
        tm.task_removed.connect(self._on_task_removed)

        for task in self._tm.all_tasks:        # más nuevas arriba
            self._add_row(task)
        self._apply_filter()
        self._refresh_counters()
        self._update_list_width()

    # ── ancho 80% ────────────────────────────────────────────────────────────
    def _update_list_width(self, page_width: int | None = None) -> None:
        w = self.width() if page_width is None else page_width
        if w > 0:
            self._list.setFixedWidth(max(int(w * _LIST_WIDTH_RATIO), 420))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._update_list_width(e.size().width())

    # ── ciclo de vida ────────────────────────────────────────────────────────
    def showEvent(self, e):
        super().showEvent(e)
        self._update_list_width()
        self._clock.start()
        for r in self._rows.values():
            r.resync()

    def hideEvent(self, e):
        super().hideEvent(e)
        self._clock.stop()

    # ── tarjetas ─────────────────────────────────────────────────────────────
    def _add_row(self, task: Task) -> TaskRow:
        row = TaskRow(task)
        row.cancel_requested.connect(self._tm.cancel)
        row.retry_requested.connect(self._retry)
        row.dismiss_requested.connect(self._tm.remove_task)
        self._rows[task.id] = row
        self._list_layout.insertWidget(0, row)     # arriba; el stretch final empuja hacia arriba
        return row

    def select_task(self, task_id: str, show_output: bool = False) -> bool:
        """Localiza una tarea por ID y, opcionalmente, despliega su log."""
        row = self._rows.get(task_id)
        if row is None:
            return False
        if show_output:
            row.open_log()
        QTimer.singleShot(0, lambda r=row: self._scroll.ensureWidgetVisible(r))
        return True

    # ── búsqueda (la lupa del menú superior la muestra / oculta) ─────────────
    def show_search(self) -> None:
        self._search.setVisible(True)
        self._search.setFocus()
        self._search.selectAll()

    def hide_search(self) -> None:
        """Oculta el campo y quita el filtro de texto que tuviera."""
        self._search.setVisible(False)
        if self._search.text():
            self._search.clear()

    def toggle_search(self) -> None:
        if self._search.isVisible():
            self.hide_search()
        else:
            self.show_search()

    def focus_search(self) -> None:
        self.show_search()

    def changeEvent(self, e):
        if e.type() == QEvent.PaletteChange:
            _refresh_icons(self)
        super().changeEvent(e)

    def _visible(self, task: Task) -> bool:
        q = self._search.text().strip().lower()
        if q and q not in task.package.name.lower() and q not in task.package.id.lower():
            return False
        return {
            "all":    True,
            "active": task.state in _ACTIVE,
            "done":   task.state == TaskState.DONE,
            "failed": task.state == TaskState.FAILED,
        }.get(self._chips.current(), True)

    def _apply_filter(self, *_):
        shown = 0
        for r in self._rows.values():
            v = self._visible(r.task)
            r.setVisible(v)
            shown += v
        self._body.setCurrentWidget(self._scroll if shown else self._empty_lbl)

    def _tick_clock(self) -> None:
        for r in self._rows.values():
            if r.task.state in _ACTIVE:
                r.refresh_sub()

    # ── slots del TaskManager ────────────────────────────────────────────────
    @Slot(object)
    def _on_task_added(self, task: Task):
        self._add_row(task)
        self._apply_filter()
        self._refresh_counters()

    @Slot(str)
    def _on_task_changed(self, task_id: str):
        r = self._rows.get(task_id)
        if r:
            r.refresh()
        self._apply_filter()
        self._refresh_counters()

    @Slot(str, int, str)
    def _on_task_updated(self, task_id: str, _progress: int, _status: str):
        r = self._rows.get(task_id)
        if r:
            r.on_progress()

    def _on_task_finished(self, *args):
        r = self._rows.get(args[0] if args else "")
        if r:
            r.on_finished()
        self._apply_filter()
        self._refresh_counters()

    @Slot(str)
    def _on_task_removed(self, task_id: str):
        r = self._rows.pop(task_id, None)
        if r:
            self._list_layout.removeWidget(r)
            r.deleteLater()
        self._apply_filter()
        self._refresh_counters()

    # ── acciones / contadores ────────────────────────────────────────────────
    def _retry(self, task_id: str) -> None:
        if self._tm.retry(task_id) is not None:
            self._scroll.verticalScrollBar().setValue(0)

    def _refresh_counters(self) -> None:
        states = [r.task.state for r in self._rows.values()]
        active = sum(s in _ACTIVE for s in states)
        done = states.count(TaskState.DONE)
        failed = states.count(TaskState.FAILED)
        self._chips.set_label("all", f"{tr('filter_all')} ({len(states)})")
        self._chips.set_label("active", f"{tr('tasks_filter_in_progress')} ({active})")
        self._chips.set_label("done", f"{tr('tasks_filter_completed')} ({done})")
        self._chips.set_label("failed", f"{tr('tasks_filter_failed')} ({failed})")
        self._clear_btn.setEnabled(len(states) - active > 0)
        self.active_count_changed.emit(active)
