# yl-soft — Installed Apps page

from __future__ import annotations
from typing import List

from PySide6.QtCore import Qt, Signal, QThread, Slot, QTimer, QPropertyAnimation, QEasingCurve
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QScrollArea, QFrame, QSizePolicy, QStackedWidget,
    QGridLayout, QMessageBox, QGraphicsOpacityEffect, QComboBox,
)

from core.i18n import tr
import core.settings as app_settings
from backend.models import Package, PackageSource, TaskType
from backend.task_manager import TaskManager
from backend.system_detect import show_xbps, show_flatpak, show_appimage
from .icon_widget import AppIconWidget
from .native_widgets import (
    BadgeLabel, CardFrame, ChipButton, RemoveButton, MultilineElideLabel,
    animate_grid_reflow,
)

GRID_COLUMNS = 4          # valor por defecto / fallback antes del primer resize
GRID_COLUMN_CHOICES = (2, 3, 4, 6)  # cuadrícula adaptable: 2/3/4/6 columnas
GRID_TILE_MIN_WIDTH = 190  # ancho mínimo aproximado de una tarjeta (usado para decidir cuántas columnas caben)


def _is_dev_package(pkg: Package) -> bool:
    """Heurística para identificar paquetes de desarrollo/librerías."""
    n = pkg.name.lower()
    return (
        n.endswith(("-devel", "-dev", "-dbg", "-doc"))
        or n.startswith("lib")
        or "-devel-" in n
    )


def _pkg_key(pkg: Package) -> tuple:
    return (pkg.source.value, pkg.name)


def _pkg_fallback_icon(pkg: Package) -> str:
    for tag in pkg.tags:
        if tag.startswith("_category_icon:"):
            return tag[len("_category_icon:"):]
    return ""


def _child_is_button(widget, pos) -> bool:
    from PySide6.QtWidgets import QAbstractButton
    return isinstance(widget.childAt(pos), QAbstractButton)


# Installed Row

class InstalledRow(CardFrame):
    open_detail      = Signal(object)
    remove_requested = Signal(object)

    def __init__(self, pkg: Package, parent=None):
        super().__init__(parent)
        self.pkg = pkg
        self.setObjectName("appCard")
        self.setMinimumHeight(56)
        self.setMaximumHeight(72)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(12)

        icon = AppIconWidget(
            pkg.name, icon_name=pkg.icon_name, icon_url=pkg.icon_url,
            size=36, radius=9, fallback_icon_name=_pkg_fallback_icon(pkg)
        )
        layout.addWidget(icon)

        info = QVBoxLayout()
        info.setSpacing(1)
        name_lbl = MultilineElideLabel(pkg.name, max_lines=1)
        name_lbl.setObjectName("cardAppName")
        info.addWidget(name_lbl)

        ver_lbl = QLabel(pkg.version or pkg.summary)
        ver_lbl.setObjectName("cardAppDesc")
        info.addWidget(ver_lbl)
        layout.addLayout(info, stretch=1)

        badge = BadgeLabel(pkg.source_label, pkg.badge_type, show_icon=False)
        layout.addWidget(badge)

        installed_badge = BadgeLabel(tr("badge_installed"), "installed", show_icon=False)
        layout.addWidget(installed_badge)

        self._remove_btn = RemoveButton(tr("remove"))
        self._remove_btn.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self._remove_btn.clicked.connect(self._on_remove)
        layout.addWidget(self._remove_btn)

        self.setCursor(Qt.PointingHandCursor)

    def _on_remove(self):
        self._remove_btn.setEnabled(False)
        self._remove_btn.setText(tr("removing"))
        self.remove_requested.emit(self.pkg)

    def mousePressEvent(self, event):
        if _child_is_button(self, event.position().toPoint()):
            super().mousePressEvent(event)
            return
        super().mousePressEvent(event)
        self.open_detail.emit(self.pkg)


# Installed Tile (vista de cuadrícula)

class InstalledTile(CardFrame):
    """Tarjeta compacta usada en la vista de cuadrícula (4 columnas)."""

    open_detail      = Signal(object)
    remove_requested = Signal(object)

    def __init__(self, pkg: Package, parent=None):
        super().__init__(parent)
        self.pkg = pkg
        self.setObjectName("appTile")
        self.setMinimumHeight(150)
        self.setMaximumHeight(170)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 10)
        layout.setSpacing(6)
        layout.setAlignment(Qt.AlignTop)

        top_row = QHBoxLayout()
        icon = AppIconWidget(
            pkg.name, icon_name=pkg.icon_name, icon_url=pkg.icon_url,
            size=40, radius=10, fallback_icon_name=_pkg_fallback_icon(pkg)
        )
        top_row.addWidget(icon)
        top_row.addStretch()

        badge = BadgeLabel(pkg.source_label, pkg.badge_type, show_icon=False)
        top_row.addWidget(badge, 0, Qt.AlignTop)

        installed_badge = BadgeLabel(tr("badge_installed"), "installed", show_icon=False)
        top_row.addWidget(installed_badge, 0, Qt.AlignTop)
        layout.addLayout(top_row)

        # Una sola línea elidida: con wordWrap sin límite un nombre largo
        # se envolvía a 2-3 líneas y desbordaba la altura fija de la
        # tarjeta, cortándose contra la fila/tarjeta siguiente.
        name_lbl = MultilineElideLabel(pkg.name, max_lines=1)
        name_lbl.setObjectName("cardAppName")
        layout.addWidget(name_lbl)

        ver_lbl = QLabel(pkg.version or pkg.summary)
        ver_lbl.setObjectName("cardAppDesc")
        layout.addWidget(ver_lbl)

        layout.addStretch()

        self._remove_btn = RemoveButton(tr("remove"))
        self._remove_btn.clicked.connect(self._on_remove)
        layout.addWidget(self._remove_btn)

        self.setCursor(Qt.PointingHandCursor)

    def _on_remove(self):
        self._remove_btn.setEnabled(False)
        self._remove_btn.setText(tr("removing"))
        self.remove_requested.emit(self.pkg)

    def mousePressEvent(self, event):
        if _child_is_button(self, event.position().toPoint()):
            super().mousePressEvent(event)
            return
        super().mousePressEvent(event)
        self.open_detail.emit(self.pkg)


# Loader — emite paquetes por fuente a medida que cada backend responde

class InstalledLoader(QThread):
    # Emite cada vez que un backend termina: (source_key, packages)
    source_ready = Signal(str, list)
    # Emite cuando todos los backends terminaron
    all_done     = Signal()

    def run(self):
        import core.settings as app_settings

        sources = []
        if show_flatpak() and app_settings.get_bool("show_flatpak"):
            sources.append(("flatpak", self._load_flatpak))
        if show_xbps() and app_settings.get_bool("show_xbps"):
            sources.append(("xbps", self._load_xbps))
        if show_appimage():
            sources.append(("appimage", self._load_appimage))

        for key, fn in sources:
            try:
                pkgs = fn()
            except Exception:
                pkgs = []
            self.source_ready.emit(key, pkgs)

        self.all_done.emit()

    def _load_flatpak(self) -> list:
        from backend import flatpak_backend
        return flatpak_backend.list_installed()

    def _load_xbps(self) -> list:
        from backend.package_engine import get_inventory_backend
        backend = get_inventory_backend()
        if backend.xbps_available():
            return backend.list_installed()
        return []

    def _load_appimage(self) -> list:
        from backend import appimage_backend
        if appimage_backend.is_available():
            return appimage_backend.list_installed()
        return []


class _FilterLoading(QWidget):
    """Animated 'searching…' placeholder shown briefly while the installed
    list is being filtered."""

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignCenter)

        self._lbl = QLabel(tr("loading"))
        self._lbl.setObjectName("loadingTitle")
        self._lbl.setAlignment(Qt.AlignCenter)
        self._lbl.setToolTip(tr("installed_filtering_tooltip"))
        lay.addWidget(self._lbl)

        self._dot = 0
        self._timer = QTimer(self)
        self._timer.setInterval(180)
        self._timer.timeout.connect(self._tick)

    def _tick(self):
        dots = "." * (self._dot % 4)
        self._lbl.setText(tr("loading") + dots)
        self._dot += 1

    def showEvent(self, event):
        super().showEvent(event)
        self._dot = 0
        self._lbl.setText(tr("loading"))
        self._timer.start()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._timer.stop()


# Installed Page

class InstalledPage(QWidget):
    app_selected = Signal(object)

    def __init__(self, task_manager: TaskManager, parent=None):
        super().__init__(parent)
        self._tm = task_manager
        self._all_pkgs: List[Package] = []
        self._pending_sources: set[str] = set()
        # Las fuentes pueden terminar antes que la cola visual de tarjetas.
        # Se mantiene separado para no mostrar un progreso obsoleto después
        # de que el backend ya terminó.
        self._sources_done = False
        self._current_source = "all"
        self._search_query   = ""

        # key -> InstalledRow / InstalledTile, persistent across filters and
        self._row_widgets: dict[tuple, "InstalledRow"] = {}
        self._tile_widgets: dict[tuple, "InstalledTile"] = {}
        self._pkgs_by_source: dict[str, list] = {}

        # Vista única en filas (estilo GNOME Software): ya no se ofrece
        # cuadrícula. El código de InstalledTile/_grid_* queda sin usar
        # pero se conserva por si se necesita reactivar más adelante.
        self._view_mode = "list"

        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(250)
        self._filter_timer.timeout.connect(self._apply_filter)

        # Cola de creación de widgets, procesada en tandas pequeñas (ver
        # _process_create_queue) para que agregar muchas apps de golpe
        # (primer arranque sin caché, o una fuente grande como XBPS) no
        # trabe la interfaz creando cientos de tarjetas de una sola vez.
        self._create_queue: List[Package] = []
        self._create_timer = QTimer(self)
        self._create_timer.setInterval(0)
        self._create_timer.timeout.connect(self._process_create_queue)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 20)
        root.setSpacing(10)

        # MainWindow coloca estos controles en la barra compartida de búsqueda.
        left = QWidget()
        left.setObjectName("installedToolbarLeft")
        left_lay = QHBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.setSpacing(8)

        self._source_combo = QComboBox()
        self._source_combo.setObjectName("installedSourceCombo")
        self._source_modes = ["all"]
        self._source_combo.addItem(tr("filter_all"), "all")
        if show_flatpak():
            self._source_combo.addItem(tr("filter_flatpak"), "flatpak")
            self._source_modes.append("flatpak")
        if show_xbps():
            self._source_combo.addItem(tr("filter_xbps"), "xbps")
            self._source_modes.append("xbps")
        if show_appimage():
            self._source_combo.addItem(tr("filter_appimage"), "appimage")
            self._source_modes.append("appimage")
        self._source_combo.currentIndexChanged.connect(self._on_source_combo_changed)
        left_lay.addWidget(self._source_combo)

        right = QWidget()
        right.setObjectName("installedToolbarRight")
        right_lay = QHBoxLayout(right)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.setSpacing(8)

        if show_appimage():
            add_ai_btn = ChipButton(icon_name="list-add-symbolic")
            add_ai_btn.setCheckable(False)
            add_ai_btn.setToolTip(tr("add_appimage_btn"))
            add_ai_btn.setFixedSize(34, 34)
            add_ai_btn.clicked.connect(self._on_add_appimage)
            right_lay.addWidget(add_ai_btn)

        self._toolbar_left = left
        self._toolbar_right = right

        # Status / loading label
        self._status_lbl = QLabel(tr("loading"))
        self._status_lbl.setObjectName("sectionSub")
        # El estado solo se muestra durante la carga real; al finalizar se
        # oculta por completo para no dejar un mensaje residual en pantalla.
        self._status_lbl.setToolTip(tr("loading"))
        root.addWidget(self._status_lbl)

        # Vista de lista
        list_scroll = QScrollArea()
        list_scroll.setWidgetResizable(True)
        list_scroll.setFrameShape(QFrame.NoFrame)

        self._list = QWidget()
        self._list_layout = QVBoxLayout(self._list)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(4)
        self._list_layout.addStretch()

        # Las tarjetas ocupan el 60% del ancho disponible, centradas
        # horizontalmente (antes iban de borde a borde). self._list
        # recibe un ancho fijo recalculado en resizeEvent; los stretch
        # de los lados hacen el centrado (ver _update_list_width).
        list_wrap = QWidget()
        list_wrap_layout = QHBoxLayout(list_wrap)
        list_wrap_layout.setContentsMargins(0, 0, 0, 0)
        list_wrap_layout.addStretch(1)
        list_wrap_layout.addWidget(self._list)
        list_wrap_layout.addStretch(1)
        list_scroll.setWidget(list_wrap)
        self._list_scroll = list_scroll

        # Vista de cuadrícula (4 columnas)
        grid_scroll = QScrollArea()
        grid_scroll.setWidgetResizable(True)
        grid_scroll.setFrameShape(QFrame.NoFrame)

        self._grid_container = QWidget()
        self._grid_layout = QGridLayout(self._grid_container)
        self._grid_layout.setContentsMargins(0, 0, 0, 0)
        self._grid_layout.setSpacing(10)
        self._grid_columns = GRID_COLUMNS
        for c in range(self._grid_columns):
            self._grid_layout.setColumnStretch(c, 1)

        # El grid por sí solo se estira para llenar el alto del scroll area,
        # lo que centra verticalmente las tarjetas cuando hay pocas. Lo
        # envolvemos en un contenedor con un "stretch" al final para que
        # las tarjetas queden ancladas arriba y el espacio sobrante quede
        # abajo, sin perder el estiramiento horizontal de las columnas.
        grid_wrap = QWidget()
        grid_wrap_layout = QVBoxLayout(grid_wrap)
        grid_wrap_layout.setContentsMargins(0, 0, 0, 0)
        grid_wrap_layout.setSpacing(0)
        grid_wrap_layout.addWidget(self._grid_container)
        grid_wrap_layout.addStretch(1)
        grid_scroll.setWidget(grid_wrap)
        self._grid_scroll = grid_scroll

        self._view_stack = QStackedWidget()
        self._view_stack.addWidget(list_scroll)   # index 0
        self._view_stack.addWidget(grid_scroll)   # index 1 (sin usar)
        self._view_stack.setCurrentIndex(0)

        self._filter_loading = _FilterLoading()

        # Estado vacío: se muestra cuando el filtro/fuente/búsqueda actual
        # no tiene ningún resultado, en vez de dejar la lista en blanco
        # sin explicación (p.ej. filtrar por "XBPS" cuando no hay ningún
        # paquete XBPS instalado).
        self._empty_state = QWidget()
        empty_layout = QVBoxLayout(self._empty_state)
        empty_layout.setContentsMargins(0, 40, 0, 0)
        empty_layout.addStretch()
        self._empty_lbl = QLabel(tr("installed_empty"))
        self._empty_lbl.setObjectName("sectionSub")
        self._empty_lbl.setAlignment(Qt.AlignCenter)
        self._empty_lbl.setWordWrap(True)
        empty_layout.addWidget(self._empty_lbl)
        empty_layout.addStretch()

        self._content_stack = QStackedWidget()
        self._content_stack.addWidget(self._view_stack)     # idx 0
        self._content_stack.addWidget(self._filter_loading) # idx 1
        self._content_stack.addWidget(self._empty_state)    # idx 2
        root.addWidget(self._content_stack)

        self._start_loader(use_cache_first=True)
        self._tm.task_completed.connect(self._on_task_completed)
        self._update_list_width()

    def _load_from_cache(self):
        """Muestra instantáneamente el último listado conocido (guardado en
        disco la sesión anterior) mientras el escaneo real corre atrás."""
        from backend import installed_cache
        cached = installed_cache.load_cache()
        if not cached:
            return False
        self._pkgs_by_source = cached
        self._all_pkgs = [p for plist in cached.values() for p in plist]
        if not self._reconcile_rows():
            self._apply_filter()
        self._status_lbl.setText(tr("installed_count").format(count=len(self._all_pkgs)))
        return True

    def _start_loader(self, use_cache_first: bool = False):
        if use_cache_first:
            self._load_from_cache()

        self._status_lbl.setVisible(True)
        self._pending_sources = set()
        self._sources_done = False
        # Determinar qué fuentes se van a cargar para el aviso de estado
        import core.settings as app_settings
        parts = []
        if show_flatpak() and app_settings.get_bool("show_flatpak"):
            self._pending_sources.add("flatpak")
            parts.append("Flatpak")
        if show_xbps()    and app_settings.get_bool("show_xbps"):
            self._pending_sources.add("xbps")
            parts.append("XBPS")
        if show_appimage():
            self._pending_sources.add("appimage")
            parts.append("AppImage")

        if parts:
            self._status_lbl.setText(
                tr("loading") + f" ({', '.join(parts)}…)"
            )
        else:
            self._status_lbl.setText(tr("loading"))

        loader = InstalledLoader(self)
        loader.source_ready.connect(self._on_source_ready)
        loader.all_done.connect(self._on_all_done)
        loader.start()

    @Slot(str, list)
    def _on_source_ready(self, source_key: str, pkgs: list):
        """Agrega paquetes de un backend y actualiza la lista sin recrear
        las filas ya existentes (solo añade/quita lo que cambió)."""
        self._pending_sources.discard(source_key)

        try:
            from backend.blacklist import filter_packages
            pkgs = filter_packages(pkgs)
        except Exception:
            pass

        self._pkgs_by_source[source_key] = pkgs
        self._all_pkgs = [p for plist in self._pkgs_by_source.values() for p in plist]
        if not self._reconcile_rows():
            self._apply_filter()

        # Actualizar aviso: fuentes que aún faltan
        if self._pending_sources:
            self._status_lbl.setText(
                tr("loading") + f" ({', '.join(s.upper() for s in self._pending_sources)}…)"
            )

    @Slot()
    def _on_all_done(self):
        self._sources_done = True
        # Flatpak con cero resultados también es una fuente terminada. No
        # mantener ningún indicador de carga visible después de all_done.
        self._status_lbl.setVisible(False)
        count = len(self._all_pkgs)
        # El loader ya terminó; la cola visual puede continuar unos ticks,
        # pero no debemos volver a presentar "cargando apps" como si faltara
        # una fuente del sistema.
        self._status_lbl.setText(tr("installed_count").format(count=count))
        try:
            from backend import installed_cache
            installed_cache.save_cache(self._pkgs_by_source)
        except Exception:
            pass

    @Slot(str, bool, str)
    def _on_task_completed(self, task_id: str, success: bool, _msg: str):
        task = self._tm._tasks.get(task_id)
        if task and task.task_type in (TaskType.REMOVE, TaskType.INSTALL) and success:
            # Invalidar la caché de búsquedas: una instalación/desinstalación
            # puede cambiar el tamaño (installed_size cambia tras instalar)
            # o el estado "instalado" que se muestra en los resultados.
            try:
                from core import search_cache
                search_cache.invalidate()
            except Exception:
                pass
            self._start_loader()

    def get_toolbar_widgets(self) -> tuple[QWidget, QWidget]:
        """(izquierda, derecha): desplegable de fuente / botón de añadir
        AppImage — MainWindow los coloca a cada lado del cuadro de
        búsqueda, mostrándolos solo mientras la barra de búsqueda está
        abierta y esta página (Instaladas) está activa."""
        return self._toolbar_left, self._toolbar_right

    def _on_add_appimage(self):
        from .appimage_wizard import run_appimage_install_wizard
        run_appimage_install_wizard(self, self._tm)

    def _on_source_combo_changed(self, index: int):
        if index < 0 or index >= len(self._source_modes):
            return
        self._filter_source(self._source_modes[index])

    def _filter_source(self, key: str):
        self._current_source = key
        if key in self._source_modes:
            idx = self._source_modes.index(key)
            if self._source_combo.currentIndex() != idx:
                self._source_combo.blockSignals(True)
                self._source_combo.setCurrentIndex(idx)
                self._source_combo.blockSignals(False)
        self._show_filtering()

    def _on_search_changed(self, text: str):
        self._search_query = text.strip().lower()
        self._show_filtering()

    def _show_filtering(self):
        """Hide the list and show a loading animation while the filter is
        (re)applied, restarting the debounce on every keystroke/click."""
        self._content_stack.setCurrentWidget(self._filter_loading)
        # Force the loading screen to paint before we schedule the filter work
        self._content_stack.repaint()
        self._filter_timer.start()

    def _apply_filter(self):
        """Show/hide existing rows according to current filter/search —
        never recreates widgets, so it stays smooth on rapid input.
        We do the heavy work in a singleShot so the loading animation
        has at least one full paint cycle before the list re-appears."""
        QTimer.singleShot(80, self._do_apply_filter)

    def _filtered(self) -> List[Package]:
        pkgs = self._all_pkgs
        if self._current_source == "flatpak":
            pkgs = [p for p in pkgs if p.source == PackageSource.FLATPAK]
        elif self._current_source == "xbps":
            pkgs = [p for p in pkgs if p.source.value == "xbps"]
        elif self._current_source == "appimage":
            pkgs = [p for p in pkgs if p.source == PackageSource.APPIMAGE]
        if not app_settings.get_bool("show_dev_packages"):
            pkgs = [p for p in pkgs if not _is_dev_package(p)]
        if self._search_query:
            q = self._search_query
            pkgs = [
                p for p in pkgs
                if q in p.name.lower() or q in (p.summary or "").lower()
            ]
        return pkgs

    def _set_view_mode(self, mode: str) -> None:
        if mode not in ("list", "grid") or mode == self._view_mode:
            # Aunque no cambie, mantenemos los botones sincronizados
            self._view_list_btn.setChecked(self._view_mode == "list")
            self._view_grid_btn.setChecked(self._view_mode == "grid")
            return
        self._view_mode = mode
        self._view_list_btn.setChecked(mode == "list")
        self._view_grid_btn.setChecked(mode == "grid")
        self._view_stack.setCurrentIndex(1 if mode == "grid" else 0)

        # Guardar la preferencia para la próxima vez que se abra el programa
        app_settings.set_value("installed_view", mode)
        app_settings.save()

        self._show_filtering()

    def _do_apply_filter(self):
        visible_pkgs = self._filtered()
        visible_keys = {_pkg_key(p) for p in visible_pkgs}

        if self._view_mode == "grid":
            self._layout_grid(visible_pkgs, visible_keys)
        else:
            self._list.setUpdatesEnabled(False)
            for key, row in self._row_widgets.items():
                row.setVisible(key in visible_keys)
            self._list.setUpdatesEnabled(True)

        if not visible_pkgs:
            self._empty_lbl.setText(self._empty_state_text())
            self._content_stack.setCurrentWidget(self._empty_state)
        else:
            self._content_stack.setCurrentWidget(self._view_stack)

    def _empty_state_text(self) -> str:
        """Mensaje de estado vacío según el filtro/búsqueda actuales:
        distingue entre 'no hay nada instalado en absoluto' y 'no hay
        nada instalado que coincida con este filtro/búsqueda'."""
        if self._search_query:
            return tr("installed_empty_search", q=self._search_query)
        if self._current_source != "all":
            source_labels = {
                "flatpak":  tr("filter_flatpak"),
                "xbps":     tr("filter_xbps"),
                "appimage": tr("filter_appimage"),
            }
            source = source_labels.get(self._current_source, self._current_source)
            return tr("installed_empty_filtered", source=source)
        return tr("installed_empty")

    def _compute_grid_columns(self) -> int:
        """Calcula cuántas columnas caben en el ancho disponible del área
        de scroll de la cuadrícula, eligiendo entre 2/3/4/6 (la opción más
        grande que aún deja cada tarjeta con al menos GRID_TILE_MIN_WIDTH
        de ancho)."""
        viewport = getattr(self, "_grid_scroll", None)
        width = viewport.viewport().width() if viewport is not None else self.width()
        if width <= 0:
            width = self.width()
        spacing = self._grid_layout.spacing() if self._grid_layout is not None else 10

        best = GRID_COLUMN_CHOICES[0]
        for cols in GRID_COLUMN_CHOICES:
            total_spacing = spacing * (cols - 1)
            tile_width = (width - total_spacing) / cols
            if tile_width >= GRID_TILE_MIN_WIDTH:
                best = cols
            else:
                break
        return best

    def _update_grid_columns(self) -> bool:
        """Recalcula el número de columnas según el ancho actual. Devuelve
        True si cambió (y por lo tanto conviene re-layoutear la cuadrícula)."""
        new_cols = self._compute_grid_columns()
        if new_cols == self._grid_columns:
            return False

        old_cols = self._grid_columns
        self._grid_columns = new_cols

        # Quitar el stretch de las columnas que sobran / añadir a las nuevas
        if new_cols < old_cols:
            for c in range(new_cols, old_cols):
                self._grid_layout.setColumnStretch(c, 0)
        for c in range(new_cols):
            self._grid_layout.setColumnStretch(c, 1)
        return True

    def _layout_grid(self, visible_pkgs: List[Package], visible_keys: set) -> None:
        """Recoloca las tarjetas visibles en la cuadrícula, con un número
        de columnas (2/3/4/6) que se adapta al ancho disponible.
        Las tarjetas no visibles se ocultan y se quitan del layout para que
        no dejen huecos."""
        cols_changed = self._update_grid_columns()
        self._grid_container.setUpdatesEnabled(False)

        # Sacar todas las tarjetas del grid layout (no las destruye)
        for key, tile in self._tile_widgets.items():
            self._grid_layout.removeWidget(tile)
            tile.setVisible(key in visible_keys)

        for idx, pkg in enumerate(visible_pkgs):
            key = _pkg_key(pkg)
            tile = self._tile_widgets.get(key)
            if tile is None:
                continue
            row, col = divmod(idx, self._grid_columns)
            self._grid_layout.addWidget(tile, row, col)

        self._grid_container.setUpdatesEnabled(True)
        if cols_changed:
            animate_grid_reflow(self._grid_container)

    def _confirm_and_remove(self, pkg: Package, widget) -> None:
        if app_settings.get_bool("confirm_before_remove"):
            reply = QMessageBox.warning(
                self,
                tr("remove"),
                tr("confirm_remove_message", name=pkg.name),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                # Restaurar el botón de la fila/tarjeta, ya que se había
                # deshabilitado de forma optimista al pulsar "Quitar"
                try:
                    widget._remove_btn.setEnabled(True)
                    widget._remove_btn.setText(tr("remove"))
                except Exception:
                    pass
                return
        self._tm.submit(pkg, TaskType.REMOVE)

    def _reconcile_rows(self):
        """Quita widgets de paquetes que ya no existen (desinstalados) y
        encola la creación de los nuevos, procesada en tandas pequeñas
        por _process_create_queue para no trabar la GUI cuando hay muchos
        de golpe (primer arranque sin caché, o una fuente grande)."""
        current_keys = {_pkg_key(p) for p in self._all_pkgs}

        for key in list(self._row_widgets.keys()):
            if key not in current_keys:
                row = self._row_widgets.pop(key)
                self._list_layout.removeWidget(row)
                row.deleteLater()
        for key in list(self._tile_widgets.keys()):
            if key not in current_keys:
                tile = self._tile_widgets.pop(key)
                self._grid_layout.removeWidget(tile)
                tile.deleteLater()

        pending_keys = {_pkg_key(p) for p in self._create_queue}
        for pkg in self._all_pkgs:
            key = _pkg_key(pkg)
            if key not in self._row_widgets and key not in pending_keys:
                self._create_queue.append(pkg)
                pending_keys.add(key)

        if self._create_queue and not self._create_timer.isActive():
            self._create_timer.start()
            return True  # hay tarjetas nuevas encolándose

        return False  # nada que crear: se puede refrescar el filtro ya

    def _process_create_queue(self, batch_size: int = 12):
        """Crea hasta 'batch_size' filas/tarjetas por tick del timer,
        cediendo el control al event loop entre tandas. A propósito NO
        oculta la lista/grid real detrás de la pantalla de carga mientras
        crea: ocultarla hacía que Qt no recalculara bien el layout al
        revelarla de nuevo, dejando la vista vacía hasta cambiar de
        pestaña y volver. El progreso se muestra en la etiqueta de
        estado en su lugar."""
        total = len(self._all_pkgs)
        for _ in range(batch_size):
            if not self._create_queue:
                self._create_timer.stop()
                break
            pkg = self._create_queue.pop(0)
            key = _pkg_key(pkg)
            if key in self._row_widgets:
                continue  # ya creada (ej. re-encolada por una recarga)

            try:
                row = InstalledRow(pkg)
                row.open_detail.connect(self.app_selected)
                row.remove_requested.connect(
                    lambda p, w=row: self._confirm_and_remove(p, w)
                )
                self._row_widgets[key] = row
                self._list_layout.insertWidget(self._list_layout.count() - 1, row)

                tile = InstalledTile(pkg)
                tile.open_detail.connect(self.app_selected)
                tile.remove_requested.connect(
                    lambda p, w=tile: self._confirm_and_remove(p, w)
                )
                self._tile_widgets[key] = tile
                tile.setVisible(False)  # se coloca/muestra en _layout_grid
            except Exception:
                # Un paquete puntual con datos inválidos no debe tumbar el
                # resto de la tanda ni el timer; se descarta y se sigue.
                import traceback
                traceback.print_exc()
                self._row_widgets.pop(key, None)
                self._tile_widgets.pop(key, None)
                continue

        remaining = len(self._create_queue)
        if remaining:
            done = max(total - remaining, 0)
            if self._sources_done:
                # Las apps ya fueron cargadas; solo quedan widgets por pintar.
                # No mostrar un "cargando" engañoso ni sobrescribir el estado
                # final emitido por _on_all_done.
                progress_text = tr("installed_count").format(count=total)
            else:
                progress_text = tr("installed_loading_progress", done=done, total=total)
            self._status_lbl.setText(progress_text)
            self._status_lbl.setToolTip(progress_text)
        else:
            self._create_timer.stop()
            # Recién ahora, con todas las tarjetas creadas, se hace un
            # único layout/filtrado (antes se hacía en cada tanda y el
            # grid "parpadeaba" al reordenarse de a poco).
            self._apply_filter()
            self._status_lbl.setText(tr("installed_count").format(count=total))
            self._status_lbl.setToolTip("")

    def showEvent(self, event):
        """Al volver a esta página, Qt a veces deja el grid/lista con un
        layout desactualizado si algo se agregó mientras estaba oculta
        (por ejemplo una fuente lenta que terminó de cargar en segundo
        plano). Forzamos un re-layout completo al reaparecer, salvo que
        todavía se estén creando tarjetas (ahí ya se revela solo al
        terminar, ver _process_create_queue)."""
        super().showEvent(event)
        self._update_list_width()
        if not self._create_timer.isActive():
            self._apply_filter()

    def _update_list_width(self, page_width: int | None = None) -> None:
        """Fija el ancho de las tarjetas de la vista de lista al 60% del
        ancho disponible; los stretch a los lados (ver list_wrap) hacen
        que queden centradas horizontalmente."""
        if page_width is None:
            page_width = self.width()
        if page_width <= 0:
            return
        self._list.setFixedWidth(max(int(page_width * 0.6), GRID_TILE_MIN_WIDTH))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        m = 12 if event.size().width() < 500 else 20
        self.layout().setContentsMargins(m, 16, m, 20)
        self._update_list_width(event.size().width())

        # Solo relayoutear la cuadrícula si el ancho actual implicaría un
        # número de columnas distinto (2/3/4/6) al que está usando ahora;
        # evita recalcular en cada píxel mientras se arrastra el borde de
        # la ventana. Debounced con un QTimer para no trabar el redimensionado.
        if self._view_mode == "grid" and self._compute_grid_columns() != self._grid_columns:
            if not hasattr(self, "_grid_resize_timer"):
                self._grid_resize_timer = QTimer(self)
                self._grid_resize_timer.setSingleShot(True)
                self._grid_resize_timer.setInterval(120)
                self._grid_resize_timer.timeout.connect(self._relayout_grid_on_resize)
            self._grid_resize_timer.start()

    def _relayout_grid_on_resize(self):
        if self._view_mode != "grid":
            return
        visible_pkgs = self._filtered()
        visible_keys = {_pkg_key(p) for p in visible_pkgs}
        self._layout_grid(visible_pkgs, visible_keys)
        self._fade_in_grid()
        self._show_transient_status(tr("installed_grid_resizing"), duration_ms=500)

    def _show_transient_status(self, text: str, duration_ms: int = 600) -> None:
        """Muestra brevemente un mensaje de estado (p. ej. 'reacomodando
        cuadrícula…') y restaura el texto anterior al terminar. También se
        deja como tooltip en la etiqueta de estado por si el mensaje
        desaparece antes de que el usuario llegue a leerlo."""
        previous = self._status_lbl.text()
        self._status_lbl.setText(text)
        self._status_lbl.setToolTip(text)

        timer = getattr(self, "_status_restore_timer", None)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            self._status_restore_timer = timer
        else:
            timer.stop()
            try:
                timer.timeout.disconnect()
            except (TypeError, RuntimeError):
                pass

        def _restore():
            self._status_lbl.setText(previous)
            self._status_lbl.setToolTip("")

        timer.timeout.connect(_restore)
        timer.start(duration_ms)

    def _fade_in_grid(self):
        """Transición ligera (fade) al recolocar la cuadrícula tras un
        cambio de columnas, para que el reacomodo no se sienta abrupto.
        Es puramente cosmética: si algo falla, se ignora sin más."""
        try:
            effect = self._grid_container.graphicsEffect()
            if not isinstance(effect, QGraphicsOpacityEffect):
                effect = QGraphicsOpacityEffect(self._grid_container)
                self._grid_container.setGraphicsEffect(effect)

            anim = getattr(self, "_grid_fade_anim", None)
            if anim is not None and anim.state() == QPropertyAnimation.Running:
                anim.stop()

            effect.setOpacity(0.35)
            anim = QPropertyAnimation(effect, b"opacity", self)
            anim.setDuration(160)
            anim.setStartValue(0.35)
            anim.setEndValue(1.0)
            anim.setEasingCurve(QEasingCurve.OutCubic)
            self._grid_fade_anim = anim
            anim.start()
        except Exception:
            pass
