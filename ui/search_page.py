# Yelena Software — Search Page

from __future__ import annotations
from typing import List, Dict

from PySide6.QtCore import Qt, Signal, QThread, Slot, QTimer, QEvent, QSize
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QScrollArea, QFrame, QSizePolicy, QPushButton,
    QComboBox, QMessageBox,
)

from backend.models import Package, PackageSource
from backend.system_detect import show_xbps, show_flatpak
from core.i18n import tr
from .icon_widget import AppIconWidget
from .native_widgets import (
    BadgeLabel, CardFrame, MultilineElideLabel,
    SlidingLoadingBar, symbolic_icon,
)

_SEARCH_LIMIT = 5000

def _pkg_fallback_icon(pkg: Package) -> str:
    for tag in pkg.tags:
        if tag.startswith("_category_icon:"):
            return tag[len("_category_icon:"):]
    return ""


def _fmt_size(size_bytes: int) -> str:
    """Formatea tamaño de descarga en unidades legibles."""
    if size_bytes <= 0:
        return ""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 ** 2:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 ** 3:
        return f"{size_bytes / 1024 ** 2:.1f} MB"
    else:
        return f"{size_bytes / 1024 ** 3:.2f} GB"


# Worker con streaming por fuente

class SearchWorker(QThread):
    """
    Busca en cada backend de forma secuencial y emite resultados parciales
    en cuanto cada fuente termina — sin esperar a las demás.
    """
    source_ready = Signal(str, list)   # (source_key, packages)
    all_done     = Signal()

    _SOURCE_ORDER = [
        ("flatpak", "show_flatpak", "show_flatpak"),
        ("xbps",    "show_xbps",   "show_xbps"),
    ]

    def __init__(self, query: str, parent=None):
        super().__init__(parent)
        self._query = query

    def run(self):
        import core.settings as app_settings
        from core import search_cache
        q = self._query

        # Flatpak
        if not self.isInterruptionRequested() and show_flatpak() and app_settings.get_bool("show_flatpak"):
            pkgs = search_cache.get("flatpak", q)
            if pkgs is None:
                try:
                    from backend import flatpak_backend
                    pkgs = flatpak_backend.search(q, limit=_SEARCH_LIMIT)
                except Exception:
                    pkgs = []
                search_cache.set_("flatpak", q, pkgs)
            if not self.isInterruptionRequested():
                self.source_ready.emit("flatpak", pkgs)

        # XBPS
        if not self.isInterruptionRequested() and show_xbps() and app_settings.get_bool("show_xbps"):
            try:
                from backend.package_engine import get_active_engine
                engine = get_active_engine()
            except Exception:
                engine = "yelena-buddies"
            cache_source = f"xbps:{engine}"
            pkgs = search_cache.get(cache_source, q)
            if pkgs is None:
                try:
                    from backend.package_engine import get_search_backend
                    backend = get_search_backend()
                    available = backend.xbps_available()
                    pkgs = backend.search(q, limit=_SEARCH_LIMIT) if available else []
                except Exception:
                    pkgs = []
                search_cache.set_(cache_source, q, pkgs)
            if not self.isInterruptionRequested():
                self.source_ready.emit("xbps", pkgs)

        # Aplica blacklist y termina
        if not self.isInterruptionRequested():
            self.all_done.emit()


# Tarjeta de resultado

class SearchResultCard(CardFrame):
    clicked = Signal(object)

    def __init__(self, pkg: Package, parent=None):
        super().__init__(parent)
        self.pkg = pkg
        self.setObjectName("appCard")
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(70)
        self.setMaximumHeight(92)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(12)

        icon = AppIconWidget(
            pkg.name,
            icon_name=pkg.icon_name,
            icon_url=pkg.icon_url,
            size=40, radius=10,
            fallback_icon_name=_pkg_fallback_icon(pkg),
        )
        layout.addWidget(icon)

        info = QVBoxLayout()
        info.setSpacing(2)

        # Fila de nombre + badges
        name_row = QHBoxLayout()
        name_row.setSpacing(6)

        name_lbl = QLabel(pkg.name)
        name_lbl.setObjectName("cardAppName")
        name_row.addWidget(name_lbl)

        name_row.addWidget(BadgeLabel(pkg.source_label, pkg.badge_type, show_icon=False))

        if pkg.is_installed:
            name_row.addWidget(BadgeLabel(tr("badge_installed"), "installed", show_icon=False))

        name_row.addStretch()
        info.addLayout(name_row)

        # Descripción — 1 línea con elipsis nativo (la tarjeta es baja,
        # 2 líneas de wordWrap sin límite se comían la fila de meta-info).
        desc_lbl = MultilineElideLabel(pkg.summary, max_lines=1)
        desc_lbl.setObjectName("cardAppDesc")
        info.addWidget(desc_lbl)

        # Fila de meta-info (versión + tamaño)
        meta_row = QHBoxLayout()
        meta_row.setSpacing(12)
        meta_row.setContentsMargins(0, 0, 0, 0)

        if pkg.version:
            ver_lbl = QLabel(f"v{pkg.version}")
            ver_lbl.setObjectName("cardMetaVersion")
            meta_row.addWidget(ver_lbl)

        size_str = _fmt_size(pkg.size_bytes)
        if size_str:
            size_lbl = QLabel(f"↓ {size_str}")
            size_lbl.setObjectName("cardMetaSize")
            meta_row.addWidget(size_lbl)

        meta_row.addStretch()
        info.addLayout(meta_row)

        layout.addLayout(info, stretch=1)

        # Los QLabel/QWidget hijos (icono, nombre, badges, descripción...)
        for child in self.findChildren(QWidget):
            child.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            child.installEventFilter(self)

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.MouseButtonPress, QEvent.MouseButtonRelease):
            if event.button() == Qt.LeftButton and event.type() == QEvent.MouseButtonRelease:
                self.clicked.emit(self.pkg)
            return True
        return super().eventFilter(obj, event)

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        self.clicked.emit(self.pkg)


class _IconToggleButton(QPushButton):
    """Botón pequeño, marcable, con icono symbolic — usado para las
    opciones de orden y de ancho de tarjeta junto al checkbox de
    'mostrar ocultos'."""

    def __init__(self, icon_kind: str, tooltip: str, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setObjectName("searchIconToggle")
        self.setToolTip(tooltip)
        self.setFixedSize(28, 28)
        self.setIcon(symbolic_icon(icon_kind, size=16))
        self.setIconSize(QSize(16, 16))
        # Botón cuadrado solo-icono: el QSS global aplica padding
        # pensado para botones con texto, así que se recorta aquí.
        self.setStyleSheet("QPushButton#searchIconToggle { padding: 0px; }")


# Página de búsqueda

class SearchPage(QWidget):
    app_selected = Signal(object)
    # Texto ya formateado para la barra de estado inferior de la
    # ventana: "<N> paquetes | Flatpak: <n> XBPS: <n>".
    results_summary = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker: SearchWorker | None = None
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(350)
        self._debounce.timeout.connect(self._run_search)

        # Resultados acumulados por fuente
        self._results_by_source: Dict[str, List[Package]] = {}
        self._last_query   = ""
        self._active_source = "all"
        self._search_active = False

        try:
            import core.settings as app_settings
            self._show_hidden = app_settings.get_bool("search_show_hidden")
            self._sort_mode = app_settings.get("search_sort_mode", "name_asc") or "name_asc"
        except Exception:
            self._show_hidden = False
            self._sort_mode = "name_asc"

        # El resumen de resultados se muestra en la barra de estado inferior.

        # Layout principal
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(10)

        # MainWindow dispone estos filtros en la barra de búsqueda compartida.
        left = QWidget()
        left.setObjectName("searchToolbarLeft")
        left_lay = QHBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.setSpacing(8)

        # Selector de fuente: un desplegable único (Todos/XBPS/Flatpak),
        # no botones sueltos. "Flatpak" se ofrece aunque no haya
        # repositorio configurado, precisamente para poder avisar y
        # guiar a Ajustes al elegirlo (ver _on_source_combo_changed).
        self._source_combo = QComboBox()
        self._source_combo.setObjectName("searchSourceCombo")
        self._source_modes = ["all"]
        self._source_combo.addItem(tr("filter_all"), "all")
        if show_xbps():
            self._source_combo.addItem(tr("filter_xbps"), "xbps")
            self._source_modes.append("xbps")
        self._source_combo.addItem(tr("filter_flatpak"), "flatpak")
        self._source_modes.append("flatpak")
        self._source_combo.currentIndexChanged.connect(self._on_source_combo_changed)
        left_lay.addWidget(self._source_combo)

        right = QWidget()
        right.setObjectName("searchToolbarRight")
        right_lay = QHBoxLayout(right)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.setSpacing(8)

        # Antes era un QCheckBox con el texto completo
        # "Mostrar paquetes ocultos (no recomendable)", que ocupaba
        # demasiado espacio en la barra de herramientas. Ahora es un
        # botón compacto de solo icono (triángulo de alerta), marcable,
        # con toda la explicación en el tooltip.
        self._hidden_chk = _IconToggleButton(
            "warn",
            f"{tr('search_show_hidden')} — {tr('search_show_hidden_tooltip')}",
        )
        self._hidden_chk.setObjectName("searchShowHiddenCheck")
        self._hidden_chk.setChecked(self._show_hidden)
        self._hidden_chk.toggled.connect(self._on_hidden_toggle)
        right_lay.addWidget(self._hidden_chk)

        sort_lbl = QLabel(tr("search_sort_label"))
        sort_lbl.setObjectName("sectionSub")
        right_lay.addWidget(sort_lbl)

        # Orden como lista desplegable (antes 3 botones de icono).
        self._sort_combo = QComboBox()
        self._sort_combo.setObjectName("searchSortCombo")
        self._sort_modes = ["name_asc", "name_desc", "source"]
        self._sort_combo.addItem(tr("search_sort_az"))
        self._sort_combo.addItem(tr("search_sort_za"))
        self._sort_combo.addItem(tr("search_sort_source"))
        self._sort_combo.setCurrentIndex(
            self._sort_modes.index(self._sort_mode)
            if self._sort_mode in self._sort_modes else 0
        )
        self._sort_combo.currentIndexChanged.connect(self._on_sort_combo_changed)
        right_lay.addWidget(self._sort_combo)

        self._toolbar_left = left
        self._toolbar_right = right

        # Barra fina que se desliza izq/der mientras hay una búsqueda
        # activa — único indicador de "cargando" que queda en esta
        # página (el conteo detallado por fuente ahora se muestra en la
        # barra de estado inferior de la ventana, ver results_summary).
        self._loading_bar = SlidingLoadingBar(self)
        root.addWidget(self._loading_bar)

        # Área de resultados
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        root.addWidget(scroll)

        self._results_widget = QWidget()
        self._results_layout = QGridLayout(self._results_widget)
        self._results_layout.setContentsMargins(0, 0, 0, 0)
        self._results_layout.setHorizontalSpacing(8)
        self._results_layout.setVerticalSpacing(4)
        # El QGridLayout ocupa todo el viewport del QScrollArea. Sin esta
        # alineación, cuando hay pocos resultados Qt puede repartirlos o
        # dejarlos flotando verticalmente en vez de comenzar arriba.
        self._results_layout.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        scroll.setWidget(self._results_widget)

        self._grid_columns = 1
        self._last_results: List[Package] = []

    def get_toolbar_widgets(self) -> tuple[QWidget, QWidget]:
        """(izquierda, derecha): chips de fuente / ocultos+orden —
        MainWindow los coloca a cada lado del cuadro de búsqueda,
        mostrándolos solo mientras hay resultados de búsqueda activos."""
        return self._toolbar_left, self._toolbar_right

    def resizeEvent(self, event):
        super().resizeEvent(event)
        m = 12 if event.size().width() < 500 else 24
        self.layout().setContentsMargins(m, 20, m, 20)
        # Los resultados de búsqueda siempre van en una sola columna a
        # todo el ancho disponible — sin grilla de 2 columnas.
        QTimer.singleShot(0, self._update_search_card_widths)

    def _search_card_width(self) -> int:
        """Devuelve el 60% del ancho útil del área de resultados."""
        available = self._results_widget.contentsRect().width()
        if available <= 0:
            available = self.width()
        return max(1, int(available * 0.60))

    def _update_search_card_widths(self) -> None:
        """Mantiene todas las tarjetas buscadas en el 60% del eje X."""
        width = self._search_card_width()
        for i in range(self._results_layout.count()):
            item = self._results_layout.itemAt(i)
            card = item.widget() if item is not None else None
            if isinstance(card, SearchResultCard):
                card.setFixedWidth(width)

    # API pública

    def trigger_search(self, query: str):
        self._last_query = query
        self._debounce.start()

    def force_search(self):
        self._debounce.stop()
        self._run_search()

    # Selector de fuente

    def _flatpak_ready(self) -> bool:
        """True si Flatpak está instalado Y tiene al menos un
        repositorio (remote) registrado — sin eso, filtrar por Flatpak
        no muestra nada útil."""
        if not show_flatpak():
            return False
        try:
            from backend import flatpak_backend
            return bool(flatpak_backend.list_remotes())
        except Exception:
            return False

    def _on_source_combo_changed(self, index: int):
        if index < 0 or index >= len(self._source_modes):
            return
        key = self._source_modes[index]
        if key == "flatpak" and not self._flatpak_ready():
            QMessageBox.warning(
                self,
                tr("search_flatpak_missing_title"),
                tr("search_flatpak_missing_body"),
            )
            # No dejar seleccionado un filtro que no va a mostrar nada:
            # se vuelve a la fuente activa anterior.
            prev_idx = self._source_modes.index(self._active_source) \
                if self._active_source in self._source_modes else 0
            self._source_combo.blockSignals(True)
            self._source_combo.setCurrentIndex(prev_idx)
            self._source_combo.blockSignals(False)
            return
        self._active_source = key
        self._rerender()

    # Orden y ancho de tarjeta

    def _on_sort_combo_changed(self, index: int):
        if index < 0 or index >= len(self._sort_modes):
            return
        self._on_sort_changed(self._sort_modes[index])

    def _on_sort_changed(self, mode: str):
        self._sort_mode = mode
        try:
            import core.settings as app_settings
            app_settings.set_value("search_sort_mode", mode)
            app_settings.save()
        except Exception:
            pass
        self._rerender()



    # Búsqueda

    def _run_search(self):
        q = self._last_query.strip()
        if not q:
            self._loading_bar.stop()
            self._clear_results()
            self._results_by_source.clear()
            self._search_active = False
            self.results_summary.emit("")
            return

        # Resetear estado
        self._results_by_source.clear()
        self._search_active = True
        self._loading_bar.start()
        self._clear_results()

        # Cancelar worker anterior
        if self._worker is not None:
            self._worker.source_ready.disconnect()
            self._worker.all_done.disconnect()
            self._worker.requestInterruption()
            if self._worker.isRunning():
                self._worker.wait(400)
            self._worker = None

        self._worker = SearchWorker(q, parent=self)
        self._worker.source_ready.connect(self._on_source_ready)
        self._worker.all_done.connect(self._on_all_done)
        self._worker.start()

    @Slot(str, list)
    def _on_source_ready(self, source_key: str, packages: List[Package]):
        """Llega cuando UNA fuente termina — sin esperar a las demás."""
        try:
            from core.catalog import enrich_with_installed_status
            packages = enrich_with_installed_status(packages)
        except Exception:
            pass

        # Se guarda sin filtrar por blacklist: el filtro se aplica en
        # _filter_results según el estado del toggle "mostrar ocultos",
        # así activarlo/desactivarlo no exige repetir la búsqueda.
        self._results_by_source[source_key] = packages

        # Renderizar lo que hay hasta ahora
        self._rerender()

    @Slot()
    def _on_all_done(self):
        """Todas las fuentes terminaron."""
        self._loading_bar.stop()
        self._search_active = False
        self._rerender()

    # Filtrado y renderizado

    def _get_all_results(self) -> List[Package]:
        """Une los resultados de todas las fuentes."""
        merged: List[Package] = []
        for pkgs in self._results_by_source.values():
            merged.extend(pkgs)
        return merged

    def _apply_hidden_filter(self, results: List[Package]) -> List[Package]:
        """Quita los paquetes de la blacklist, salvo que el usuario haya
        activado 'mostrar paquetes ocultos'."""
        if self._show_hidden:
            return results
        try:
            from backend.blacklist import filter_packages
            return filter_packages(results)
        except Exception:
            return results

    def _filter_results(self, results: List[Package]) -> List[Package]:
        if self._active_source == "flatpak":
            results = [p for p in results if p.source == PackageSource.FLATPAK]
        elif self._active_source == "xbps":
            results = [p for p in results if p.source.value == "xbps"]
        results = self._apply_hidden_filter(results)
        return self._apply_sort(results)

    def _apply_sort(self, results: List[Package]) -> List[Package]:
        """Ordena alfabéticamente (asc/desc) o agrupa por fuente
        (Flatpak / XBPS), manteniendo orden alfabético como criterio
        secundario dentro de cada fuente."""
        if self._sort_mode == "name_desc":
            return sorted(results, key=lambda p: p.name.lower(), reverse=True)
        elif self._sort_mode == "source":
            return sorted(results, key=lambda p: (p.source.value, p.name.lower()))
        # "name_asc" por defecto
        return sorted(results, key=lambda p: p.name.lower())

    def _on_hidden_toggle(self, checked: bool):
        self._show_hidden = checked
        try:
            import core.settings as app_settings
            app_settings.set_bool("search_show_hidden", checked)
            app_settings.save()
        except Exception:
            pass
        self._rerender()

    def _emit_summary(self, filtered_count: int) -> None:
        """Arma y emite el texto para la barra de estado inferior de la
        ventana: '<N> paquetes | Flatpak: <n> XBPS: <n>'."""
        fp = len(self._apply_hidden_filter(self._results_by_source.get("flatpak", [])))
        xb = len(self._apply_hidden_filter(self._results_by_source.get("xbps", [])))
        self.results_summary.emit(
            tr("search_status_bar", n=filtered_count, flatpak=fp, xbps=xb)
        )

    def _rerender(self):
        """Actualiza la lista de resultados con lo que hay hasta ahora."""
        all_results = self._get_all_results()
        filtered    = self._filter_results(all_results)
        self._emit_summary(len(filtered))
        self._render_results(filtered)

    def _clear_results(self):
        while self._results_layout.count():
            item = self._results_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _render_results(self, results: List[Package]):
        self._last_results = results
        self._clear_results()
        cols = max(1, self._grid_columns)
        for i, pkg in enumerate(results):
            card = SearchResultCard(pkg)
            card.clicked.connect(self.app_selected)
            row, col = divmod(i, cols)
            self._results_layout.addWidget(card, row, col)
        for c in range(cols):
            self._results_layout.setColumnStretch(c, 1)
        self._update_search_card_widths()
