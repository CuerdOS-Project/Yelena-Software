# Yelena Software — Search Page

from __future__ import annotations
import re
import threading
import time
from typing import List, Dict

from PySide6.QtCore import Qt, Signal, QThread, Slot, QTimer, QEvent, QSize
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QScrollArea, QFrame, QSizePolicy, QPushButton,
    QComboBox, QMessageBox,
)

from backend.models import Package, PackageSource
from backend.system_detect import show_xbps, show_flatpak, show_appimage
from core.i18n import tr
from .icon_widget import AppIconWidget
from .chibi_widget import ChibiPlaceholder
from PySide6.QtGui import QPalette
from .native_widgets import (
    BadgeLabel, CardFrame, MultilineElideLabel, CompactInstallButton,
    SlidingLoadingBar, symbolic_icon, highlight_html,
)

# El backend puede devolver muchos paquetes, pero no tiene sentido crear miles
# de widgets en una búsqueda interactiva. El resumen sigue mostrando el total
# recibido; la UI solo materializa una ventana manejable.
# Sin topes: se muestran todos los paquetes encontrados. Las tarjetas se crean
# por lotes (ver _process_render_batch) para que la GUI no se congele.
_SEARCH_LIMIT = 10_000_000

# Los resultados llegan "a goteo": el primero se emite al instante y el resto
# se agrupa en lotes para no inundar la GUI con una señal por paquete.
_BATCH_MAX = 24          # paquetes por lote como máximo
_BATCH_INTERVAL = 0.08   # segundos máximos que un lote espera antes de salir
_INSTALLED_INDEX_WAIT = 2.0

def _pkg_fallback_icon(pkg: Package) -> str:
    return pkg.icon_fallback_name


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


# Worker con streaming por fuente y por paquete

class SearchWorker(QThread):
    """
    Busca en todas las fuentes A LA VEZ (Flatpak, XBPS, AppImage) y emite cada
    paquete en cuanto su backend lo imprime, en lugar de esperar a que termine
    la fuente entera. Una fuente lenta ya no retrasa a las rápidas.

    Señales:
      packages_found(fuente, lote)  -> AÑADIR lote a esa fuente (streaming)
      source_ready(fuente, lista)   -> REEMPLAZAR la lista de esa fuente
                                       (caché, FPM/NISSA y AppImage)
      all_done()                    -> todas las fuentes terminaron
    """
    source_ready   = Signal(str, list)
    packages_found = Signal(str, list)
    all_done       = Signal()

    def __init__(self, query: str, parent=None):
        super().__init__(parent)
        self._query = query
        self._installed_index: dict | None = None
        self._index_ready = threading.Event()

    # utilidades

    def _stopped(self) -> bool:
        return self.isInterruptionRequested()

    def _load_installed_index(self) -> None:
        """Calcula qué hay instalado UNA vez, en segundo plano y en paralelo a
        la búsqueda (antes se hacía en el hilo de la GUI en cada respuesta)."""
        try:
            from core.catalog import load_installed_index
            self._installed_index = load_installed_index()
        except Exception:
            self._installed_index = None
        finally:
            self._index_ready.set()

    def _mark_installed(self, packages: List[Package]) -> List[Package]:
        if self._index_ready.wait(_INSTALLED_INDEX_WAIT) and self._installed_index:
            try:
                from core.catalog import apply_installed_index
                apply_installed_index(packages, self._installed_index)
            except Exception:
                pass
        return packages

    def _stream(self, source_key: str, packages, sink: List[Package]) -> None:
        """Consume un iterador de Package emitiendo lotes. `sink` acumula todo
        lo recibido (incluso si el iterador falla a medias). Los `None` son
        latidos del backend: sirven para vaciar un lote pendiente."""
        batch: List[Package] = []
        last_emit = 0.0

        def flush() -> None:
            nonlocal batch, last_emit
            if batch and not self._stopped():
                self.packages_found.emit(source_key, self._mark_installed(batch))
            batch = []
            last_emit = time.monotonic()

        try:
            for pkg in packages:
                if self._stopped():
                    return
                if pkg is not None:
                    batch.append(pkg)
                    sink.append(pkg)
                if batch and (len(batch) >= _BATCH_MAX
                              or time.monotonic() - last_emit >= _BATCH_INTERVAL):
                    flush()
        finally:
            flush()

    def _emit_appimage(self, packages: List[Package]) -> None:
        """Emite los AppImage y completa tamaño/versión que AM no da en el catálogo.

        1) Muestra de inmediato lo que ya hay (más lo guardado en caché).
        2) Consulta las releases de los primeros resultados sin datos y
           vuelve a emitir con los valores encontrados.
        """
        from backend import appimage_meta
        appimage_meta.apply_cached(packages)
        self.source_ready.emit("appimage", packages)
        names = appimage_meta.missing_names(packages)
        if not names or self.isInterruptionRequested():
            return
        appimage_meta.fetch_many(names)
        if not self.isInterruptionRequested() and appimage_meta.apply_cached(packages):
            self.source_ready.emit("appimage", packages)

    # fuentes (cada una corre en su propio hilo)

    def _search_flatpak(self, q: str) -> None:
        import core.settings as app_settings
        from core import search_cache
        if not (show_flatpak() and app_settings.get_bool("show_flatpak")):
            return
        cached = search_cache.get("flatpak", q)
        if cached is not None:
            self.source_ready.emit("flatpak", self._mark_installed(cached))
            return
        from backend import flatpak_backend
        found: List[Package] = []
        complete = True
        try:
            self._stream("flatpak",
                         flatpak_backend.iter_search(q, _SEARCH_LIMIT, self._stopped),
                         found)
        except Exception:      # incluye FlatpakSearchTimeout: se conserva lo parcial
            complete = False
        if complete and not self._stopped():
            search_cache.set_("flatpak", q, found)

    def _search_xbps(self, q: str, engine: str) -> bool:
        """Devuelve True si FPM ya devolvió también los AppImage (NISSA)."""
        import core.settings as app_settings
        from core import search_cache
        if not (show_xbps() and app_settings.get_bool("show_xbps")):
            return False
        cache_source = f"xbps:{engine}"
        cached = search_cache.get(cache_source, q)
        if cached is not None:
            self._emit_xbps_and_appimage(cached)
            return engine == "fpm"

        if engine != "fpm":
            # Motor nativo: xbps-query -Rs en streaming, paquete a paquete.
            from backend import xbps_backend
            if not xbps_backend.xbps_available():
                return False
            found: List[Package] = []
            self._stream("xbps",
                         xbps_backend.iter_search(q, _SEARCH_LIMIT, self._stopped),
                         found)
            if not self._stopped():
                search_cache.set_(cache_source, q, found)
            return False

        # FPM/NISSA valida el flujo completo antes de aceptarlo: llega de una vez.
        fpm_used = False
        pkgs: List[Package] = []
        try:
            from engine.package_engine import get_search_backend
            backend = get_search_backend()
            available = backend.xbps_available()
            pkgs = backend.search(q, limit=_SEARCH_LIMIT) if available else []
            fpm_used = available
            if available:
                search_cache.set_(cache_source, q, pkgs)
        except Exception:
            pkgs = []
        if not self._stopped():
            self._emit_xbps_and_appimage(pkgs)
            if fpm_used:
                search_cache.set_("appimage:am-FUSION", q,
                                  [p for p in pkgs if p.source == PackageSource.APPIMAGE])
        return fpm_used

    def _emit_xbps_and_appimage(self, pkgs: List[Package]) -> None:
        if self._stopped():
            return
        xbps_packages = [p for p in pkgs if p.source != PackageSource.APPIMAGE]
        appimage_packages = [p for p in pkgs if p.source == PackageSource.APPIMAGE]
        self.source_ready.emit("xbps", self._mark_installed(xbps_packages))
        if appimage_packages:
            self._emit_appimage(appimage_packages)

    def _search_appimage(self, q: str) -> None:
        # AM-FUSION se consulta aunque el usuario conserve el motor XBPS nativo.
        if not show_appimage():
            return
        try:
            from core import search_cache
            from backend import fpm_backend
            if not fpm_backend.supports_nissa_am_search():
                return
            pkgs = search_cache.get("appimage:am-FUSION", q)
            if pkgs is None:
                pkgs = fpm_backend.search_am(q, limit=_SEARCH_LIMIT)
                search_cache.set_("appimage:am-FUSION", q, pkgs)
            if not self._stopped():
                self._emit_appimage(pkgs)
        except Exception:
            pass

    def run(self):
        q = self._query
        try:
            from engine.package_engine import get_active_engine
            engine = get_active_engine()
        except Exception:
            engine = "yelena-buddies"

        def guarded(fn, *args):
            def target():
                try:
                    fn(*args)
                except Exception:
                    pass
            return target

        def xbps_then_maybe_appimage():
            fpm_used = self._search_xbps(q, engine)
            # Con FPM, AppImage solo se pide aparte si NISSA no lo devolvió ya.
            if engine == "fpm" and not fpm_used and not self._stopped():
                self._search_appimage(q)

        targets = [
            self._load_installed_index,
            guarded(self._search_flatpak, q),
            guarded(xbps_then_maybe_appimage),
        ]
        if engine != "fpm":
            targets.append(guarded(self._search_appimage, q))

        threads = [threading.Thread(target=t, daemon=True) for t in targets]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        if not self.isInterruptionRequested():
            self.all_done.emit()


# Tarjeta de resultado

class _ResultCardBase(CardFrame):
    """Lógica común de las tarjetas de resultado (lista y cuadrícula):
    nombre con coincidencias resaltadas, chip de fuente y botón de acción
    directa (Instalar / Ver) sin tener que abrir el detalle."""
    clicked = Signal(object)
    install_requested = Signal(object)   # emite la propia tarjeta

    def _name_label(self, pkg: Package, query: str) -> QLabel:
        lbl = QLabel()
        lbl.setObjectName("cardAppName")
        lbl.setTextFormat(Qt.RichText)
        accent = self.palette().color(QPalette.Highlight).name()
        lbl.setText(highlight_html(pkg.name, query, accent))
        return lbl

    def _source_chip(self, pkg: Package) -> QWidget:
        chip = QWidget()
        chip.setObjectName("cardSourceChip")
        chip.setStyleSheet(
            "QWidget#cardSourceChip { background: rgba(100, 116, 139, 42); "
            "border-radius: 7px; }"
        )
        lay = QHBoxLayout(chip)
        lay.setContentsMargins(7, 2, 7, 2)
        lbl = QLabel(pkg.source_label)
        lbl.setObjectName("cardSource")
        lbl.setToolTip(pkg.source_label)
        lay.addWidget(lbl)
        return chip

    def _action_button(self) -> CompactInstallButton:
        self._action_btn = CompactInstallButton(tr("install"))
        self._action_btn.clicked.connect(self._on_action)
        self.refresh_action()
        return self._action_btn

    def refresh_action(self) -> None:
        btn = self._action_btn
        btn.setEnabled(True)
        if self.pkg.is_installed:
            btn.set_secondary(tr("badge_installed"))
        else:
            btn.setText(tr("install"))
            btn.setStyleSheet(CompactInstallButton(tr("install")).styleSheet())

    def set_installing(self) -> None:
        self._action_btn.setEnabled(False)
        self._action_btn.setText(tr("installing"))

    def _on_action(self) -> None:
        if self.pkg.is_installed:
            self.clicked.emit(self.pkg)       # ya instalada: ver detalle
        else:
            self.install_requested.emit(self)

    def _wire_clicks(self) -> None:
        # Los hijos pasivos dejan pasar el clic a la tarjeta; el botón de
        # acción conserva su propio manejo.
        for child in self.findChildren(QWidget):
            if child is self._action_btn:
                continue
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


class SearchResultCard(_ResultCardBase):
    """Fila horizontal (vista de lista)."""

    def __init__(self, pkg: Package, query: str = "", parent=None):
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

        layout.addWidget(AppIconWidget(
            pkg.name, icon_name=pkg.icon_name, icon_url=pkg.icon_url,
            size=40, radius=10, fallback_icon_name=_pkg_fallback_icon(pkg),
        ))

        info = QVBoxLayout()
        info.setSpacing(2)

        name_row = QHBoxLayout()
        name_row.setSpacing(6)
        name_row.addWidget(self._name_label(pkg, query))
        name_row.addWidget(self._source_chip(pkg))
        if pkg.is_32bit:
            name_row.addWidget(BadgeLabel(
                tr("badge_32bit"), "architecture",
                tooltip=tr("warning_32bit"), show_icon=False,
            ))
        name_row.addStretch()
        info.addLayout(name_row)

        desc_lbl = MultilineElideLabel(pkg.summary, max_lines=1)
        desc_lbl.setObjectName("cardAppDesc")
        desc_lbl.set_highlight(query)
        info.addWidget(desc_lbl)

        meta_row = QHBoxLayout()
        meta_row.setSpacing(12)
        meta_row.setContentsMargins(0, 0, 0, 0)
        if pkg.version:
            ver_lbl = QLabel(f"v{pkg.version}")
            ver_lbl.setObjectName("cardMetaVersion")
            meta_row.addWidget(ver_lbl)
        size_str = _fmt_size(pkg.size_bytes)
        if size_str:
            from .native_widgets import symbolic_icon
            arrow_lbl = QLabel()
            arrow_lbl.setPixmap(symbolic_icon("arrow_down", size=12).pixmap(12, 12))
            meta_row.addWidget(arrow_lbl)
            meta_row.setSpacing(4)
            size_lbl = QLabel(size_str)
            size_lbl.setObjectName("cardMetaSize")
            meta_row.addWidget(size_lbl)
        meta_row.addStretch()
        info.addLayout(meta_row)
        layout.addLayout(info, stretch=1)

        # Acción directa, alineada a la derecha de la fila.
        layout.addWidget(self._action_button(), alignment=Qt.AlignVCenter)
        self._wire_clicks()


class SearchResultTile(_ResultCardBase):
    """Tarjeta vertical con icono grande (vista de cuadrícula)."""

    def __init__(self, pkg: Package, query: str = "", parent=None):
        super().__init__(parent)
        self.pkg = pkg
        self.setObjectName("appTile")
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(210)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 12)
        layout.setSpacing(6)

        top = QHBoxLayout()
        top.addWidget(AppIconWidget(
            pkg.name, icon_name=pkg.icon_name, icon_url=pkg.icon_url,
            size=56, radius=14, fallback_icon_name=_pkg_fallback_icon(pkg),
        ))
        top.addStretch()
        top.addWidget(self._source_chip(pkg), 0, Qt.AlignTop)
        layout.addLayout(top)

        name = self._name_label(pkg, query)
        name.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        layout.addWidget(name)

        desc_lbl = MultilineElideLabel(pkg.summary, max_lines=2)
        desc_lbl.setObjectName("cardAppDesc")
        desc_lbl.set_highlight(query)
        layout.addWidget(desc_lbl)
        layout.addStretch()

        bottom = QHBoxLayout()
        if pkg.version:
            ver_lbl = QLabel(f"v{pkg.version}")
            ver_lbl.setObjectName("cardMetaVersion")
            bottom.addWidget(ver_lbl)
        bottom.addStretch()
        bottom.addWidget(self._action_button())
        layout.addLayout(bottom)
        self._wire_clicks()


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
    # Contadores para la barra de estado clicable: {"shown", "flatpak",
    # "xbps", "appimage", "modes", "active"}; dict vacío = sin resultados.
    counts_changed = Signal(dict)

    def __init__(self, task_manager=None, parent=None):
        super().__init__(parent)
        self._tm = task_manager
        self._installing: dict[str, str] = {}   # clave de paquete -> task id
        self._view_mode = "list"
        try:
            import core.settings as _s
            if (_s.get("search_view", "list") or "list") == "grid":
                self._view_mode = "grid"
        except Exception:
            pass
        self._worker: SearchWorker | None = None
        # Se retienen workers cancelados hasta que realmente finalicen.
        # flatpak search puede tardar varios segundos; destruir el QThread
        # tras un wait corto provoca "QThread: Destroyed while thread is still running".
        self._workers: list[SearchWorker] = []
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(350)
        self._debounce.timeout.connect(self._run_search)

        # Resultados acumulados por fuente
        self._results_by_source: Dict[str, List[Package]] = {}
        self._last_query   = ""
        self._active_source = "all"
        self._search_active = False
        self._search_generation = 0

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
        root.setContentsMargins(24, 12, 24, 20)
        root.setSpacing(10)

        # Controles de filtro/orden ocultos visualmente; el menú superior
        # los lee y cambia sus selecciones para reutilizar la lógica existente.
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
        if show_appimage():
            try:
                from backend import fpm_backend
                if fpm_backend.supports_nissa_am_search():
                    self._source_combo.addItem(tr("filter_appimage"), "appimage")
                    self._source_modes.append("appimage")
            except Exception:
                pass
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
        self._sort_lbl = sort_lbl
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
        # Los controles siguen disponibles para el menú superior, pero ya
        # no se reparentan a la fila de búsqueda.
        left.setVisible(False)
        right.setVisible(False)

        # Barra de filtros inmediata: fuente (con contadores), vista, ocultos
        # y orden — todo a un clic, sin menús flotantes.
        bar = QWidget()
        bar.setObjectName("searchFilterBar")
        bar_lay = QHBoxLayout(bar)
        bar_lay.setContentsMargins(0, 0, 0, 0)
        bar_lay.setSpacing(8)

        # Una sola línea compacta: [Fuente (n) ▾] ··· [lista][cuadrícula] [⚠] [Orden ▾].
        # La fuente es un desplegable (con contador) en vez de una fila de
        # pestañas, y la etiqueta "Ordenar" pasa a tooltip: así la barra cabe
        # en la misma línea del buscador (ver attach_filter_bar).
        self._source_names = {"all": tr("filter_all"), "xbps": tr("filter_xbps"),
                              "flatpak": tr("filter_flatpak"), "appimage": tr("filter_appimage")}
        self._source_combo.setMinimumContentsLength(11)
        self._source_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        bar_lay.addWidget(self._source_combo)
        bar_lay.addStretch(1)

        self._view_list_btn = _IconToggleButton("view_list", tr("view_list"))
        self._view_grid_btn = _IconToggleButton("view_grid", tr("view_grid"))
        self._view_list_btn.clicked.connect(lambda: self._set_view_mode("list"))
        self._view_grid_btn.clicked.connect(lambda: self._set_view_mode("grid"))
        self._view_list_btn.setChecked(self._view_mode == "list")
        self._view_grid_btn.setChecked(self._view_mode == "grid")
        bar_lay.addWidget(self._view_list_btn)
        bar_lay.addWidget(self._view_grid_btn)
        bar_lay.addSpacing(6)
        bar_lay.addWidget(self._hidden_chk)
        self._sort_combo.setToolTip(tr("search_sort_label").rstrip(" :："))
        bar_lay.addWidget(self._sort_combo)
        self._filter_bar = bar
        self._root_layout = root
        root.addWidget(bar)

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
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        root.addWidget(scroll)
        self._scroll = scroll
        # El área visible (viewport) es la referencia para adaptar columnas y
        # anchos: se reacciona a SU redimensionado, no al de la página.
        scroll.viewport().installEventFilter(self)
        self._reflow_timer = QTimer(self)
        self._reflow_timer.setSingleShot(True)
        self._reflow_timer.setInterval(30)
        self._reflow_timer.timeout.connect(self._reflow)

        self._empty = ChibiPlaceholder("yel-chibi-search")
        self._empty.setVisible(False)
        root.addWidget(self._empty, 1)

        self._results_widget = QWidget()
        self._results_layout = QGridLayout(self._results_widget)
        self._results_layout.setContentsMargins(0, 10, 0, 10)
        self._results_layout.setHorizontalSpacing(8)
        self._results_layout.setVerticalSpacing(4)
        # El QGridLayout ocupa todo el viewport del QScrollArea. Sin esta
        # alineación, cuando hay pocos resultados Qt puede repartirlos o
        # dejarlos flotando verticalmente en vez de comenzar arriba.
        self._results_layout.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        scroll.setWidget(self._results_widget)

        self._grid_columns = 1 if self._view_mode == "list" else 3
        self._last_results: List[Package] = []
        self._render_queue: List[Package] = []
        self._render_index = 0
        self._rendered_card_count = 0
        # IDs ya presentes o en cola. Permite añadir respuestas parciales sin
        # destruir las tarjetas que el usuario ya está viendo.
        self._rendered_keys: set[str] = set()
        self._render_batch_size = 24
        self._render_timer = QTimer(self)
        self._render_timer.setInterval(8)
        self._render_timer.timeout.connect(self._process_render_batch)

    def attach_filter_bar(self, host: QWidget) -> None:
        """Mueve la barra de filtros al contenedor `host` (la fila del
        buscador de la ventana), ahorrando una línea de alto en la página."""
        bar = self._filter_bar
        if bar.parent() is not host:
            self._root_layout.removeWidget(bar)
            host.layout().addWidget(bar)
        bar.setVisible(True)
        host.setVisible(True)
        # Forzar que la fila del buscador recalcule su tamaño: sin esto la
        # barra quedaba sin espacio (invisible) la primera vez que se
        # mostraban los resultados y solo aparecía al volver de otra página.
        self._relayout_host(host)
        QTimer.singleShot(0, lambda h=host: self._relayout_host(h))

    @staticmethod
    def _relayout_host(host: QWidget) -> None:
        w = host
        while w is not None:
            lay = w.layout()
            if lay is not None:
                lay.invalidate()
                lay.activate()
            w.updateGeometry()
            w = w.parentWidget()
            if w is not None and w.objectName() == "pagesStack":
                break

    def detach_filter_bar(self) -> None:
        """Devuelve la barra a su sitio original, dentro de la página."""
        bar = self._filter_bar
        if bar.parent() is self:
            return
        host = bar.parentWidget()
        if host is not None and host.layout() is not None:
            host.layout().removeWidget(bar)
            host.setVisible(False)
        bar.setParent(self)
        self._root_layout.insertWidget(0, bar)
        bar.setVisible(True)

    def get_toolbar_widgets(self) -> tuple[QWidget, QWidget]:
        """Compatibilidad: devuelve los contenedores ocultos de los filtros."""
        return self._toolbar_left, self._toolbar_right

    def resizeEvent(self, event):
        super().resizeEvent(event)
        m = 12 if event.size().width() < 500 else 24
        self.layout().setContentsMargins(m, 12, m, 20)
        self._reflow_timer.start()

    def eventFilter(self, obj, event):
        if obj is self._scroll.viewport() and event.type() == QEvent.Resize:
            self._reflow_timer.start()
        return super().eventFilter(obj, event)

    def _viewport_width(self) -> int:
        return max(1, self._scroll.viewport().width())

    def _compute_columns(self) -> int:
        if self._view_mode != "grid":
            return 1
        return max(1, min(4, int(self._viewport_width() * 0.96) // 250))

    def _search_card_width(self) -> int:
        """Lista: 60% del área visible (mín. 360 px, máx. el área), centrada.
        Cuadrícula: ~96% repartido entre las columnas."""
        avail = self._viewport_width()
        if self._view_mode == "grid":
            cols = max(1, self._grid_columns)
            spacing = self._results_layout.horizontalSpacing()
            return max(1, (int(avail * 0.96) - spacing * (cols - 1)) // cols)
        return max(1, min(avail - 8, max(360, int(avail * 0.60))))

    def _reflow(self) -> None:
        """Adapta en caliente columnas y ancho de las tarjetas ya creadas
        (sin reconstruirlas) al tamaño actual de la ventana."""
        self._grid_columns = self._compute_columns()
        cols = self._grid_columns
        widgets = []
        while self._results_layout.count():
            item = self._results_layout.takeAt(0)
            if item.widget() is not None:
                widgets.append(item.widget())
        width = self._search_card_width()
        for i, w in enumerate(widgets):
            row, col = divmod(i, cols)
            if isinstance(w, _ResultCardBase):
                w.setFixedWidth(width)
            self._results_layout.addWidget(w, row, col)
        for c in range(8):
            self._results_layout.setColumnStretch(c, 0)
        self._rendered_card_count = len(widgets)

    def _update_search_card_widths(self) -> None:
        self._reflow()

    def _set_view_mode(self, mode: str) -> None:
        self._view_list_btn.setChecked(mode == "list")
        self._view_grid_btn.setChecked(mode == "grid")
        if mode == self._view_mode:
            return
        self._view_mode = mode
        try:
            import core.settings as app_settings
            app_settings.set_value("search_view", mode)
            app_settings.save()
        except Exception:
            pass
        self._grid_columns = self._compute_columns()
        self._rerender()   # cambia el tipo de tarjeta: hay que recrearlas

    def _make_card(self, pkg: Package) -> _ResultCardBase:
        cls = SearchResultTile if self._view_mode == "grid" else SearchResultCard
        card = cls(pkg, self._last_query.strip())
        card.clicked.connect(self.app_selected)
        card.install_requested.connect(self._on_install_requested)
        if self._package_key(pkg) in self._installing:
            card.set_installing()
        return card

    def _on_install_requested(self, card) -> None:
        if self._tm is None:
            self.app_selected.emit(card.pkg)   # sin gestor: abrir detalle
            return
        import core.settings as app_settings
        if app_settings.get_bool("confirm_before_install"):
            reply = QMessageBox.question(
                self, tr("install"),
                tr("confirm_install_message", name=card.pkg.name),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
            )
            if reply != QMessageBox.Yes:
                return
        from backend.models import TaskType
        task = self._tm.submit(card.pkg, TaskType.INSTALL)
        self._installing[self._package_key(card.pkg)] = task.id
        card.set_installing()
        if not getattr(self, "_tm_connected", False):
            self._tm.task_completed.connect(self._on_task_completed)
            self._tm_connected = True

    @Slot(str, bool, str)
    def _on_task_completed(self, task_id: str, success: bool, _msg: str) -> None:
        keys = [k for k, t in self._installing.items() if t == task_id]
        if not keys:
            return
        for k in keys:
            self._installing.pop(k, None)
        for i in range(self._results_layout.count()):
            item = self._results_layout.itemAt(i)
            card = item.widget() if item is not None else None
            if isinstance(card, _ResultCardBase) and self._package_key(card.pkg) in keys:
                if success:
                    from backend.models import PackageStatus
                    card.pkg.status = PackageStatus.INSTALLED
                card.refresh_action()

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

    def set_source(self, key: str) -> None:
        """Cambia el filtro de fuente (usado por la barra de estado)."""
        if key in self._source_modes:
            self._source_combo.setCurrentIndex(self._source_modes.index(key))

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
        self._search_generation += 1
        if not q:
            self._loading_bar.stop()
            self._clear_results()
            self._results_by_source.clear()
            self._search_active = False
            self._set_empty_visible(False)
            self.results_summary.emit("")
            self.counts_changed.emit({})
            self._set_source_counts({})
            return

        # Resetear estado
        self._results_by_source.clear()
        self._search_active = True
        self._loading_bar.start()
        self._clear_results()

        # Cancelar worker anterior
        if self._worker is not None:
            self._worker.requestInterruption()

        worker = SearchWorker(q, parent=self)
        self._worker = worker
        self._workers.append(worker)
        # Las señales ya encoladas de un worker cancelado pueden llegar
        # después de iniciar la búsqueda nueva. Capturar la instancia evita
        # que esos resultados viejos borren o mezclen la lista actual.
        worker.source_ready.connect(
            lambda source, packages, w=worker: self._on_source_ready_from(w, source, packages)
        )
        worker.packages_found.connect(
            lambda source, batch, w=worker: self._on_packages_found_from(w, source, batch)
        )
        worker.all_done.connect(lambda w=worker: self._on_all_done_from(w))
        worker.finished.connect(lambda w=worker: self._retire_worker(w))
        worker.start()

    def _on_source_ready_from(self, worker, source_key: str, packages: List[Package]):
        if worker is not self._worker:
            return
        self._on_source_ready(source_key, packages)

    def _on_packages_found_from(self, worker, source_key: str, batch: List[Package]):
        if worker is not self._worker:
            return
        self._on_packages_found(source_key, batch)

    def _on_all_done_from(self, worker):
        if worker is not self._worker:
            return
        self._on_all_done()

    def _retire_worker(self, worker: SearchWorker) -> None:
        if worker in self._workers and not worker.isRunning():
            self._workers.remove(worker)
        if self._worker is worker:
            self._worker = None

    def shutdown_workers(self) -> None:
        """Interrumpe y espera todos los workers antes de destruir la página."""
        workers = list(self._workers)
        for worker in workers:
            worker.requestInterruption()
        for worker in workers:
            if worker.isRunning():
                worker.wait()
        self._workers.clear()
        self._worker = None

    @Slot(str, list)
    def _on_source_ready(self, source_key: str, packages: List[Package]):
        """Reemplaza la lista completa de una fuente (caché, FPM, AppImage).

        El estado "instalado" ya viene calculado por el worker: aquí no se
        lanza ningún subproceso, así la GUI nunca se queda esperando."""
        # Se guarda sin filtrar por blacklist: el filtro se aplica en
        # _filter_results según el estado del toggle "mostrar ocultos",
        # así activarlo/desactivarlo no exige repetir la búsqueda.
        self._results_by_source[source_key] = packages

        # Añadir únicamente lo nuevo. Antes se llamaba a _rerender(), que
        # borraba todas las tarjetas y reconstruía la página cada vez que
        # terminaba una fuente.
        self._render_incremental(source_key)

    @Slot(str, list)
    def _on_packages_found(self, source_key: str, batch: List[Package]):
        """Llega un lote de paquetes de una fuente que sigue buscando: se
        añaden a la lista al instante, sin esperar a que la fuente termine."""
        self._results_by_source.setdefault(source_key, []).extend(batch)
        self._render_incremental(source_key)

    @Slot()
    def _on_all_done(self):
        """Todas las fuentes terminaron."""
        self._loading_bar.stop()
        self._search_active = False
        self._render_incremental()
        self._settle_order()
        self._set_empty_visible(
            not self._last_results and bool(self._last_query.strip()),
            self._last_query.strip(),
        )

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
        elif self._active_source == "appimage":
            results = [p for p in results if p.source == PackageSource.APPIMAGE]
        results = self._apply_hidden_filter(results)
        # No ocultar variantes en la búsqueda: cada fuente permanece visible
        # como resultado propio. Solo se enlazan entre sí para que el detalle
        # pueda ofrecer el selector de fuentes.
        self._attach_source_variants(results)
        return self._apply_sort(results)

    @staticmethod
    def _duplicate_aliases(pkg: Package) -> tuple[str, ...]:
        """Alias de identidad que suelen variar entre catálogos."""
        values = (pkg.appstream_id, pkg.pkgname, pkg.name)
        return tuple(
            alias for value in values
            if (alias := re.sub(r"[^a-z0-9]+", "", (value or "").casefold()))
        )

    def _attach_source_variants(self, results: List[Package]) -> None:
        """Enlaza variantes sin eliminarlas de la lista de búsqueda."""
        grouped: list[list[Package]] = []
        aliases_to_group: dict[str, int] = {}
        for pkg in results:
            aliases = self._duplicate_aliases(pkg)
            group_index = next(
                (aliases_to_group[alias] for alias in aliases if alias in aliases_to_group),
                None,
            )
            if group_index is None:
                group_index = len(grouped)
                grouped.append([])
            grouped[group_index].append(pkg)
            for alias in aliases:
                aliases_to_group.setdefault(alias, group_index)

        for variants in grouped:
            source_variants = tuple(variants)
            # Atributo dinámico para no cambiar el formato persistido de
            # Package; el detalle lo usa para ofrecer el selector de fuente.
            for package in variants:
                package._source_variants = source_variants

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
        ai = len(self._apply_hidden_filter(self._results_by_source.get("appimage", [])))
        self.results_summary.emit(
            tr("search_status_bar", n=filtered_count, flatpak=fp, xbps=xb, appimage=ai)
        )
        counts = {"all": fp + xb + ai, "flatpak": fp, "xbps": xb, "appimage": ai}
        self._set_source_counts(counts)
        self.counts_changed.emit({
            "shown": filtered_count, "flatpak": fp, "xbps": xb, "appimage": ai,
            "modes": list(self._source_modes), "active": self._active_source,
        })

    def _set_source_counts(self, counts: dict) -> None:
        """Pone el contador en cada opción del desplegable de fuente."""
        for i, key in enumerate(self._source_modes):
            name = self._source_names[key]
            self._source_combo.setItemText(i, f"{name} ({counts[key]})" if key in counts else name)

    def _rerender(self):
        """Actualiza la lista de resultados con lo que hay hasta ahora."""
        all_results = self._get_all_results()
        filtered    = self._filter_results(all_results)
        self._emit_summary(len(filtered))
        self._render_results(filtered)

    def _clear_results(self):
        self._render_timer.stop()
        self._render_queue.clear()
        self._render_index = 0
        self._rendered_card_count = 0
        self._rendered_keys.clear()
        while self._results_layout.count():
            item = self._results_layout.takeAt(0)
            if item.widget():
                item.widget().hide()   # que no se vea mientras llega el deleteLater
                item.widget().deleteLater()

    def _set_empty_visible(self, empty: bool, query: str = "") -> None:
        if empty:
            self._empty.set_texts(tr("search_package_not_found"),
                                  tr("search_no_results", q=query))
        self._empty.setVisible(empty)
        self._scroll.setVisible(not empty)

    def _render_results(self, results: List[Package]):
        self._last_results = results
        self._clear_results()
        query = self._last_query.strip()
        self._set_empty_visible(not results and bool(query) and not self._search_active, query)
        # Mantener la lista completa para el conteo y el orden, pero crear solo
        # una ventana inicial de tarjetas. Esto evita que una consulta amplia
        # convierta la GUI en miles de QWidget y provoque congelamientos.
        self._render_queue = list(results)
        self._rendered_keys = {self._package_key(pkg) for pkg in self._render_queue}
        if self._render_queue:
            self._render_timer.start()

    @staticmethod
    def _package_key(pkg: Package) -> str:
        """Clave estable, conservando una tarjeta por fuente."""
        package_id = str(getattr(pkg, "id", "") or pkg.name)
        return f"{pkg.source.value}:{package_id}"

    def _render_incremental(self, source_key: str | None = None) -> None:
        """Encola solo apps nuevas, sin reiniciar ni duplicar la lista.

        La fuente que acaba de responder no se representa como una tarjeta
        adicional: las variantes se conservan en el paquete único y se
        muestran al abrir su detalle.
        """
        all_results = self._get_all_results()
        filtered = self._filter_results(all_results)
        self._last_results = filtered
        self._emit_summary(len(filtered))

        # Todas las variantes permanecen visibles; solo se evita repetir la
        # misma tarjeta cuando un backend vuelve a emitir el mismo resultado.
        for pkg in filtered:
            key = self._package_key(pkg)
            if key in self._rendered_keys:
                continue
            self._rendered_keys.add(key)
            self._render_queue.append(pkg)

        self._set_empty_visible(
            not filtered and bool(self._last_query.strip()) and not self._search_active,
            self._last_query.strip(),
        )
        if self._render_queue and not self._render_timer.isActive():
            self._render_timer.start()

    def _settle_order(self) -> None:
        """Al terminar, deja las tarjetas en el orden elegido (A-Z, Z-A, fuente).

        Durante el goteo los paquetes aparecen según llegan; si el usuario ya
        se desplazó por la lista no se toca nada, para no moverle el suelo."""
        if self._search_active or self._scroll.verticalScrollBar().value() > 0:
            return
        if self._render_queue:     # aún se están creando tarjetas: reintentar
            gen = self._search_generation
            QTimer.singleShot(120, lambda: gen == self._search_generation and self._settle_order())
            return
        shown = [self._package_key(c.pkg) for c in self._cards()]
        wanted = [self._package_key(p) for p in self._last_results]
        if shown and wanted and shown != wanted:
            self._rerender()

    def _cards(self) -> list:
        out = []
        for i in range(self._results_layout.count()):
            item = self._results_layout.itemAt(i)
            w = item.widget() if item is not None else None
            if isinstance(w, _ResultCardBase):
                out.append(w)
        return out

    def _process_render_batch(self) -> None:
        """Crea unas pocas tarjetas por turno para mantener fluida la GUI."""
        cols = max(1, self._grid_columns)
        end = min(self._render_index + self._render_batch_size, len(self._render_queue))
        for i in range(self._render_index, end):
            pkg = self._render_queue[i]
            card = self._make_card(pkg)
            row, col = divmod(self._rendered_card_count, cols)
            card.setFixedWidth(self._search_card_width())
            self._results_layout.addWidget(card, row, col)
            self._rendered_card_count += 1
        self._render_index = end
        if self._render_index >= len(self._render_queue):
            self._render_timer.stop()
            self._render_queue.clear()
            self._render_index = 0
            self._update_search_card_widths()
