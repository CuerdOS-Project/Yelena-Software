# yl-soft — Main Window

from __future__ import annotations

import os
import subprocess

from PySide6.QtCore import Qt, Slot, QTimer, QSize
from PySide6.QtGui import (
    QKeySequence, QShortcut, QIcon, QFont, QFontDatabase,
)
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QLabel, QStackedWidget, QLineEdit,
    QFrame, QApplication, QButtonGroup, QPushButton, QGridLayout,
    QStatusBar,
)

from backend.task_manager import TaskManager
from backend.models import Package, TaskType

from .home_page import HomePage
from .native_widgets import NavButton, StatusDot

from core.i18n import tr
import core.settings as app_settings
from backend.update_status import update_status


# Page indices
PAGE_EXPLORE   = 0
PAGE_SEARCH    = 1
PAGE_INSTALLED = 2
PAGE_UPDATES   = 3
PAGE_TASKS     = 4
PAGE_DETAIL    = 5
PAGE_SETTINGS  = 6
PAGE_ABOUT     = 7

_TOTAL_PAGES = 8


def _find_icon(names: list[str], size: int = 20) -> QIcon:
    for name in names:
        icon = QIcon.fromTheme(name)
        if not icon.isNull():
            return icon
    return QIcon()


def _nav_button(icon_names: list[str], label: str, icon_only: bool = False) -> NavButton:
    btn = NavButton()
    icon = _find_icon(icon_names)
    if not icon.isNull():
        btn.setIcon(icon)
        btn.setIconSize(QSize(18, 18))
        if not icon_only:
            btn.setText(f"  {label}")
    elif not icon_only:
        btn.setText(label)
    if icon_only:
        # Solo icono: sin texto, botón cuadrado y con tooltip para que la
        # acción siga siendo identificable sin ocupar espacio horizontal.
        btn.setToolTip(label)
        btn.setFixedSize(36, 36)
    return btn


def _load_app_icon() -> QIcon:
    from backend import store
    return store.find_app_icon()


# Loading placeholder shown while a page is being built

class _PageLoading(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignCenter)

        self._lbl = QLabel(tr("loading"))
        self._lbl.setObjectName("loadingTitle")
        self._lbl.setAlignment(Qt.AlignCenter)
        lay.addWidget(self._lbl)

        self._dot = 0
        self._timer = QTimer(self)
        self._timer.setInterval(350)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def _tick(self):
        dots = "." * (self._dot % 4)
        self._lbl.setText(tr("loading") + dots)
        self._dot += 1

    def stop(self):
        self._timer.stop()


# Header con reposicionamiento del overlay de búsqueda al redimensionar

class _HeaderWidget(QWidget):
    def __init__(self, on_resize, parent=None):
        super().__init__(parent)
        self._on_resize = on_resize

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._on_resize()


def _pt(widget, pt: int) -> None:
    f = widget.font()
    f.setPointSize(pt)
    widget.setFont(f)


# Main Window

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(tr("app_name"))
        self.resize(980, 642)
        self.setMinimumSize(800, 500)

        self._app_icon = _load_app_icon()
        if not self._app_icon.isNull():
            self.setWindowIcon(self._app_icon)
            QApplication.instance().setWindowIcon(self._app_icon)

        self._apply_system_font()
        self._task_manager = TaskManager(self)
        self._tasks_window = None
        self._task_activity_color = None
        self._task_activity_on = False
        self._task_activity_timer = QTimer(self)
        self._task_activity_timer.setInterval(420)
        self._task_activity_timer.timeout.connect(self._toggle_task_activity)
        self._task_manager.task_added.connect(self._on_task_activity_changed)
        self._task_manager.task_completed.connect(self._on_task_activity_completed)

        # Arrastrar-y-soltar un .AppImage sobre cualquier parte de la
        # ventana abre el asistente de instalación con el archivo ya
        # cargado (ver dragEnterEvent/dropEvent más abajo).
        self.setAcceptDrops(True)

        # Lazy page registry: None until first visit
        self._pages: list[QWidget | None] = [None] * _TOTAL_PAGES
        self._prev_page = PAGE_EXPLORE

        # Barra de estado inferior de la ventana: aquí se muestra el
        # resumen de resultados de búsqueda ("N paquetes | Flatpak: n
        # XBPS: n"), emitido por SearchPage.results_summary.
        status_bar = QStatusBar()
        status_bar.setObjectName("mainStatusBar")
        status_bar.setSizeGripEnabled(False)
        self._status_bar_lbl = QLabel("")
        self._status_bar_lbl.setObjectName("mainStatusBarLbl")
        status_bar.addWidget(self._status_bar_lbl)
        self.setStatusBar(status_bar)
        self._status_bar_widget = status_bar
        # Esta barra solo tiene sentido con resultados de búsqueda
        # activos (ver _show_search_toolbar). El resto del tiempo no
        # debe reservar espacio ni verse en ninguna otra pantalla
        # (Explorar, Instaladas, Actualizaciones, Ajustes...), así que
        # arranca oculta en vez de solo con el texto vacío.
        status_bar.setVisible(False)

        # Build shell (top header + category bar + stack)
        central = QWidget()
        central.setObjectName("centralWidget")
        self.setCentralWidget(central)

        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self._header = self._build_header()
        main_layout.addWidget(self._header)

        hsep1 = QFrame()
        hsep1.setObjectName("headerSeparator")
        hsep1.setFrameShape(QFrame.HLine)
        hsep1.setFrameShadow(QFrame.Sunken)
        hsep1.setFixedHeight(1)
        main_layout.addWidget(hsep1)

        # Fila de búsqueda: ocupa el mismo lugar que la barra de categorías
        # (debajo de las pestañas). Se activa/desactiva con el botón de
        # búsqueda de la cabecera y su comportamiento depende de la página
        # activa (ver _toggle_search_bar / _on_search_changed).
        self._search_bar_row = self._build_search_bar_row()
        main_layout.addWidget(self._search_bar_row)

        hsep2 = QFrame()
        hsep2.setObjectName("headerSeparator")
        hsep2.setFrameShape(QFrame.HLine)
        hsep2.setFrameShadow(QFrame.Sunken)
        hsep2.setFixedHeight(1)
        main_layout.addWidget(hsep2)

        self._stack = QStackedWidget()
        self._stack.setObjectName("pagesStack")

        # Fill stack with loading placeholders — real pages injected lazily
        self._placeholders: list[_PageLoading] = []
        for _ in range(_TOTAL_PAGES):
            ph = _PageLoading()
            self._placeholders.append(ph)
            self._stack.addWidget(ph)

        content_widget = QWidget()
        QVBoxLayout(content_widget).addWidget(self._stack)
        content_widget.layout().setContentsMargins(0, 0, 0, 0)
        content_widget.layout().setSpacing(0)
        main_layout.addWidget(content_widget, stretch=1)

        QShortcut(QKeySequence("Ctrl+F"), self).activated.connect(self._focus_search)
        QShortcut(QKeySequence("Escape"), self).activated.connect(self._on_escape)

        # Build home page eagerly (first visible page); all others are lazy
        QTimer.singleShot(0, self._init_home_page)

        # Polling en tiempo real del estado de actualizaciones (4 colores)
        self._refresh_status_dot()
        self._status_timer = QTimer(self)
        self._status_timer.setInterval(3000)
        self._status_timer.timeout.connect(self._refresh_status_dot)
        self._status_timer.start()

        # Sincronizar los repos al iniciar, sin depender de abrir Actualizaciones.
        self._auto_sync_running = False
        QTimer.singleShot(1200, self._maybe_auto_sync_repos)

    # Eager init for the first page only

    def _init_home_page(self):
        self._build_page(PAGE_EXPLORE)
        self._stack.setCurrentIndex(PAGE_EXPLORE)
        self._sync_nav_selection(PAGE_EXPLORE)

    # Lazy page factory

    def _build_page(self, page_id: int) -> QWidget:
        """Create a page, replace its placeholder, connect signals."""
        if self._pages[page_id] is not None:
            return self._pages[page_id]

        page = self._create_page(page_id)
        self._pages[page_id] = page
        self._connect_page_signals(page_id, page)

        # Swap placeholder → real page at same stack index
        old = self._placeholders[page_id]
        self._stack.insertWidget(page_id, page)
        self._stack.removeWidget(old)
        old.stop()
        old.deleteLater()

        return page

    def _create_page(self, page_id: int) -> QWidget:
        if page_id == PAGE_EXPLORE:
            return HomePage()
        if page_id == PAGE_SEARCH:
            from .search_page import SearchPage
            return SearchPage()
        if page_id == PAGE_INSTALLED:
            from .installed_page import InstalledPage
            return InstalledPage(self._task_manager)
        if page_id == PAGE_UPDATES:
            from .updates_page import UpdatesPage
            return UpdatesPage(self)
        if page_id == PAGE_TASKS:
            # Ya no se navega aquí directamente (ver PAGE_UPDATES), pero
            # se deja disponible por si algún caller viejo lo pide suelto.
            from .tasks_page import TasksPage
            return TasksPage(self._task_manager)
        if page_id == PAGE_DETAIL:
            from .detail_page import AppDetailPage
            return AppDetailPage(self._task_manager)
        if page_id == PAGE_SETTINGS:
            from .settings_page import SettingsPage
            return SettingsPage()
        if page_id == PAGE_ABOUT:
            from .about_page import AboutPage
            return AboutPage()
        raise ValueError(f"Unknown page_id: {page_id}")

    def _connect_page_signals(self, page_id: int, page: QWidget):
        if page_id == PAGE_EXPLORE:
            page.app_selected.connect(self._open_detail)
            page.explore_shown.connect(self._on_home_explore_shown)
            page.category_opened.connect(self._on_category_opened)
        elif page_id == PAGE_SEARCH:
            page.app_selected.connect(self._open_detail)
        elif page_id == PAGE_INSTALLED:
            page.app_selected.connect(self._open_detail)
        elif page_id == PAGE_DETAIL:
            page.go_back.connect(self._go_back)
        elif page_id == PAGE_TASKS:
            page.active_count_changed.connect(self._on_tasks_count)
        elif page_id == PAGE_SETTINGS:
            page.language_changed.connect(self._on_language_changed)

    def _ensure_page(self, page_id: int) -> QWidget:
        """Return the page, building it now if needed."""
        if self._pages[page_id] is None:
            return self._build_page(page_id)
        return self._pages[page_id]

    # Convenience accessors used by IPC and other callers

    @property
    def _updates_page(self):
        return self._ensure_page(PAGE_UPDATES)

    @property
    def _search_page(self):
        return self._ensure_page(PAGE_SEARCH)

    @property
    def _installed_page(self):
        return self._ensure_page(PAGE_INSTALLED)

    @property
    def _detail_page(self):
        return self._ensure_page(PAGE_DETAIL)

    # System font

    def _apply_system_font(self):
        kde_font = self._read_kde_font()
        if kde_font:
            QApplication.setFont(kde_font)
            return
        gtk_font = self._read_gtk_font()
        if gtk_font:
            QApplication.setFont(gtk_font)
            return
        db = QFontDatabase()
        for family in ("Inter", "Cantarell", "Noto Sans", "Ubuntu", "DejaVu Sans"):
            if family in db.families():
                QApplication.setFont(QFont(family, 10))
                return

    def _read_kde_font(self) -> QFont | None:
        import configparser
        kdeglobals = os.path.expanduser("~/.config/kdeglobals")
        if not os.path.exists(kdeglobals):
            return None
        try:
            cfg = configparser.ConfigParser()
            cfg.read(kdeglobals)
            font_str = cfg.get("General", "font", fallback="")
            if font_str:
                parts = font_str.split(",")
                return QFont(parts[0], int(parts[1]) if len(parts) > 1 else 10)
        except Exception:
            pass
        return None

    def _read_gtk_font(self) -> QFont | None:
        try:
            r = subprocess.run(
                ["gsettings", "get", "org.gnome.desktop.interface", "font-name"],
                capture_output=True, text=True, timeout=1,
            )
            val = r.stdout.strip().strip("'\"")
            if val:
                parts = val.rsplit(" ", 1)
                return QFont(parts[0], int(parts[1]) if len(parts) > 1 else 10)
        except Exception:
            pass
        return None

    # Header superior (búsqueda + pestañas + ajustes/acerca de)

    def _build_header(self) -> QWidget:
        header = _HeaderWidget(lambda: None)
        header.setObjectName("headerBar")

        layout = QGridLayout(header)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(12)
        # Tres columnas: los laterales absorben exactamente el mismo espacio
        # sobrante, por lo que las pestañas quedan centradas en X=0 de la
        # ventana, no solo entre los botones visibles.
        layout.setColumnStretch(0, 1)
        layout.setColumnStretch(1, 0)
        layout.setColumnStretch(2, 1)

        # --- Izquierda: el botón se sustituye por una caja de búsqueda
        # dentro del mismo espacio reservado. Nunca se dibuja por encima
        # de la barra ni de otros botones.
        left_box = QWidget()
        left_box.setFixedWidth(200)
        left_lay = QHBoxLayout(left_box)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.setSpacing(0)

        search_btn = NavButton("")
        search_btn.setCheckable(False)
        search_btn.setIcon(_find_icon(["edit-find-symbolic", "edit-find", "system-search", "search"]))
        search_btn.setIconSize(QSize(18, 18))
        search_btn.setFixedSize(36, 36)
        search_btn.setToolTip(tr("nav_search_tooltip"))
        search_btn.clicked.connect(self._toggle_search_bar)
        self._search_toggle_btn = search_btn
        left_lay.addWidget(search_btn)
        left_lay.addStretch(1)

        layout.addWidget(left_box, 0, 0, Qt.AlignLeft | Qt.AlignVCenter)

        # --- Centro: pestañas (Explorar / Instaladas / Actualizaciones) ---
        tabs_frame = QFrame()
        tabs_frame.setObjectName("tabsBar")
        tabs_lay = QHBoxLayout(tabs_frame)
        tabs_lay.setContentsMargins(4, 4, 4, 4)
        tabs_lay.setSpacing(4)

        self._nav_group = QButtonGroup(self)
        self._nav_group.setExclusive(True)
        self._nav_buttons: dict[int, NavButton] = {}

        _TAB_WIDTH = 150

        def add_tab(page: int, icon_names: list[str], label: str) -> NavButton:
            btn = _nav_button(icon_names, label)
            btn.setCheckable(True)
            btn.setFixedWidth(_TAB_WIDTH)
            self._nav_group.addButton(btn)
            tabs_lay.addWidget(btn)
            self._nav_buttons[page] = btn
            return btn

        # Iconos simbólicos (monocromos) primero, con alternativas por si
        # el tema del sistema no los trae.
        explore_btn = add_tab(
            PAGE_EXPLORE,
            ["compass-symbolic", "go-home-symbolic", "view-grid-symbolic", "compass"],
            tr("nav_explore"),
        )
        explore_btn.clicked.connect(self._on_explore_tab_clicked)

        installed_btn = add_tab(
            PAGE_INSTALLED,
            ["emblem-system-symbolic", "system-software-install-symbolic", "applications-system"],
            tr("nav_installed"),
        )
        installed_btn.clicked.connect(lambda: self._nav_to(PAGE_INSTALLED))

        # La antigua pestaña "Pendientes" ahora se llama Actualizaciones.
        # Las tareas tienen su propia ventana, accesible desde el botón de
        # actividad de la derecha.
        self._updates_tasks_label = tr("nav_updates")
        updates_btn = add_tab(
            PAGE_UPDATES,
            ["software-update-available-symbolic", "system-software-update-symbolic", "update-manager"],
            self._updates_tasks_label,
        )
        updates_btn.clicked.connect(self._on_updates_tasks_clicked)
        self._updates_tasks_btn = updates_btn

        self._updates_status_dot = StatusDot(diameter=9)
        self._updates_status_dot.setToolTip(tr("status_gray_title"))
        tabs_lay.addWidget(self._updates_status_dot)

        layout.addWidget(tabs_frame, 0, 1, Qt.AlignCenter)

        # --- Derecha: Tareas / Ajustes (ventanas aparte) / Acerca de ---
        right_box = QWidget()
        right_box.setFixedWidth(200)
        right_lay = QHBoxLayout(right_box)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.setSpacing(6)
        right_lay.addStretch(1)

        tasks_btn = _nav_button(
            ["view-list-symbolic", "view-list", "system-run-symbolic", "task-due"],
            tr("nav_tasks"),
            icon_only=True,
        )
        tasks_btn.setCheckable(False)
        tasks_btn.clicked.connect(self._open_tasks_window)
        self._tasks_btn = tasks_btn

        settings_btn = _nav_button(
            ["preferences-system-symbolic", "preferences-system", "configure"],
            tr("nav_settings"),
            icon_only=True,
        )
        settings_btn.setCheckable(False)
        settings_btn.clicked.connect(self._open_settings_dialog)
        self._settings_btn = settings_btn

        about_btn = _nav_button(
            ["help-about-symbolic", "help-about", "dialog-information"],
            tr("nav_about"),
            icon_only=True,
        )
        about_btn.clicked.connect(lambda: self._nav_to(PAGE_ABOUT))

        right_lay.addWidget(tasks_btn)
        right_lay.addWidget(settings_btn)
        right_lay.addWidget(about_btn)
        layout.addWidget(right_box, 0, 2, Qt.AlignRight | Qt.AlignVCenter)

        # Ajustes ya no es una "página" (abre ventana aparte): solo Acerca
        # de participa del resaltado de navegación.
        self._bottom_buttons: list[tuple[NavButton, int]] = [
            (about_btn, PAGE_ABOUT),
        ]

        self._nav_pages = [PAGE_EXPLORE, PAGE_INSTALLED, PAGE_UPDATES]

        return header

    def _search_btn_icon(self, for_updates: bool) -> QIcon:
        if for_updates:
            return _find_icon([
                "software-update-available-symbolic", "system-software-update-symbolic",
                "system-software-update", "view-refresh-symbolic", "view-refresh",
            ])
        return _find_icon(["edit-find-symbolic", "edit-find", "system-search", "search"])

    def _more_options_btn_icon(self) -> QIcon:
        """Icono de "más opciones" (menú): en Instaladas el botón de la
        cabecera nunca vuelve a buscar, siempre revela el desplegable
        de fuente + añadir AppImage (ver _open_search_bar), esté la
        barra abierta o cerrada."""
        return _find_icon([
            "open-menu-symbolic", "view-more-symbolic",
            "application-menu-symbolic", "view-list-symbolic",
        ])

    def _build_search_bar_row(self) -> QWidget:
        """Fila de búsqueda centrada (no a todo lo ancho) y de tamaño
        regular. Ocupa el mismo lugar que la barra de categorías mientras
        está activa en Explorar, y en Instaladas aparece en ese mismo
        lugar (debajo de las pestañas). En Actualizaciones esta fila ya
        no se usa: el botón de la cabecera se convierte en "comprobar
        actualizaciones" (ver _toggle_search_bar).

        Los chips de fuente (izquierda) y "mostrar ocultos"+orden
        (derecha) de SearchPage se reparentan a cada lado del cuadro de
        texto, EN LA MISMA FILA — y solo se muestran mientras hay
        resultados de búsqueda activos (página de resultados), no
        cuando la barra está abierta pero vacía en Explorar."""
        row = QFrame()
        row.setObjectName("searchBarRow")
        outer = QHBoxLayout(row)
        outer.setContentsMargins(16, 8, 16, 8)
        outer.setSpacing(10)

        # Contenedor izquierdo: chips de fuente (Todo/Flatpak/XBPS).
        self._search_toolbar_left_box = QWidget()
        self._search_toolbar_left_lay = QHBoxLayout(self._search_toolbar_left_box)
        self._search_toolbar_left_lay.setContentsMargins(0, 0, 0, 0)
        self._search_toolbar_left_box.setVisible(False)
        outer.addWidget(self._search_toolbar_left_box)

        outer.addStretch(1)

        inner = QWidget()
        inner_lay = QHBoxLayout(inner)
        inner_lay.setContentsMargins(0, 0, 0, 0)
        inner_lay.setSpacing(8)

        self._search_edit = QLineEdit()
        self._search_edit.setObjectName("searchBar")
        self._search_edit.setPlaceholderText(tr("search_placeholder"))
        self._search_edit.setMinimumWidth(320)
        self._search_edit.setMaximumWidth(460)
        self._search_edit.textChanged.connect(self._on_search_changed)
        self._search_edit.returnPressed.connect(self._on_search_enter)
        inner_lay.addWidget(self._search_edit)

        close_btn = NavButton("")
        close_btn.setCheckable(False)
        close_btn.setIcon(_find_icon(["window-close-symbolic", "window-close"]))
        close_btn.setFixedSize(28, 28)
        close_btn.setToolTip(tr("nav_search_tooltip"))
        close_btn.clicked.connect(self._toggle_search_bar)
        inner_lay.addWidget(close_btn)

        outer.addWidget(inner)
        outer.addStretch(1)

        # Contenedor derecho: "mostrar ocultos" + orden (desplegable).
        self._search_toolbar_right_box = QWidget()
        self._search_toolbar_right_lay = QHBoxLayout(self._search_toolbar_right_box)
        self._search_toolbar_right_lay.setContentsMargins(0, 0, 0, 0)
        self._search_toolbar_right_box.setVisible(False)
        outer.addWidget(self._search_toolbar_right_box)

        row.setVisible(False)
        return row

    @Slot()
    def _toggle_search_bar(self) -> None:
        current = self._stack.currentIndex()
        if current == PAGE_UPDATES:
            # En esta pestaña el botón ya no abre una barra de búsqueda:
            # se convirtió por completo en "comprobar actualizaciones"
            # (ver _update_search_btn_icon).
            if self._pages[PAGE_UPDATES] is not None:
                try:
                    self._pages[PAGE_UPDATES]._refresh_updates()
                except Exception:
                    pass
            return
        if self._search_bar_row.isVisible():
            self._close_search_bar()
        else:
            self._open_search_bar()

    def _detach_toolbar_widgets(self) -> None:
        """Quita del layout (sin borrar) cualquier widget de toolbar
        reparentado actualmente en las cajas izquierda/derecha del
        buscador — paso previo antes de reparentar el de otra página."""
        for lay in (self._search_toolbar_left_lay, self._search_toolbar_right_lay):
            while lay.count():
                item = lay.takeAt(0)
                w = item.widget()
                if w is not None:
                    w.setParent(None)

    def _attach_toolbar_widgets(self, left, right) -> None:
        self._detach_toolbar_widgets()
        self._search_toolbar_left_lay.addWidget(left)
        self._search_toolbar_right_lay.addWidget(right)
        # Igualar el ancho de ambas cajas para que el cuadro de texto
        # quede realmente centrado: si el contenido de un lado es más
        # ancho que el del otro, el QHBoxLayout con stretch a los dos
        # lados no alcanza a centrar el buscador por sí solo.
        w = max(left.sizeHint().width(), right.sizeHint().width())
        self._search_toolbar_left_box.setMinimumWidth(w)
        self._search_toolbar_right_box.setMinimumWidth(w)

    def _ensure_search_toolbar_attached(self) -> None:
        """Reparenta la barra de herramientas de SearchPage (chips de
        fuente, ocultos, orden) a cada lado del cuadro de búsqueda,
        y conecta su resumen de resultados a la barra de estado inferior.
        Las cajas izquierda/derecha son compartidas con InstalledPage
        (ver _ensure_installed_toolbar_attached): solo una página es
        "dueña" del contenido a la vez, según cuál esté activa."""
        if getattr(self, "_toolbar_owner", None) == "search":
            return
        sp = self._search_page  # fuerza construcción si hace falta
        left, right = sp.get_toolbar_widgets()
        self._attach_toolbar_widgets(left, right)
        if not getattr(self, "_search_summary_connected", False):
            sp.results_summary.connect(self._status_bar_lbl.setText)
            self._search_summary_connected = True
        self._toolbar_owner = "search"

    def _ensure_installed_toolbar_attached(self) -> None:
        """Reparenta el desplegable de fuente + botón de añadir AppImage
        de InstalledPage a cada lado del cuadro de búsqueda. Comparte las
        mismas cajas que SearchPage (ver _ensure_search_toolbar_attached),
        así que al salir de Instaladas el contenido se reemplaza —nunca
        queda el botón de añadir AppImage visible en otra pestaña."""
        if getattr(self, "_toolbar_owner", None) == "installed":
            return
        ip = self._installed_page  # fuerza construcción si hace falta
        left, right = ip.get_toolbar_widgets()
        self._attach_toolbar_widgets(left, right)
        self._toolbar_owner = "installed"

    def _show_search_toolbar(self, visible: bool) -> None:
        """Los chips/ocultos/orden solo se muestran mientras hay
        resultados de búsqueda activos (página de resultados) — no
        cuando la barra está abierta pero aún vacía en Explorar.
        La barra de estado inferior de la ventana solo tiene contenido
        aquí (resumen "N paquetes | Flatpak: n XBPS: n"), así que se
        oculta por completo (no solo el texto) en cualquier otra
        pantalla, para no dejar una franja vacía reservada abajo."""
        if visible:
            self._ensure_search_toolbar_attached()
        elif getattr(self, "_toolbar_owner", None) == "search":
            self._search_toolbar_left_box.setVisible(False)
            self._search_toolbar_right_box.setVisible(False)
            self._status_bar_lbl.setText("")
            self._status_bar_widget.setVisible(False)
            return
        self._search_toolbar_left_box.setVisible(visible)
        self._search_toolbar_right_box.setVisible(visible)
        self._status_bar_widget.setVisible(visible)
        if not visible:
            self._status_bar_lbl.setText("")

    def _show_installed_toolbar(self, visible: bool) -> None:
        """Desplegable de fuente + añadir AppImage de Instaladas: a
        diferencia de los chips de resultados de SearchPage, estos se
        muestran de inmediato al abrir la barra de búsqueda en esta
        página (no dependen de haber escrito nada todavía)."""
        if visible:
            self._ensure_installed_toolbar_attached()
        elif getattr(self, "_toolbar_owner", None) == "installed":
            self._search_toolbar_left_box.setVisible(False)
            self._search_toolbar_right_box.setVisible(False)
            return
        self._search_toolbar_left_box.setVisible(visible)
        self._search_toolbar_right_box.setVisible(visible)

    def _open_search_bar(self) -> None:
        current = self._stack.currentIndex()
        if current == PAGE_INSTALLED:
            # En Instaladas el desplegable de fuente + añadir AppImage
            # aparecen de inmediato junto al buscador (no hace falta
            # esperar a que haya resultados, a diferencia de Explorar/
            # Buscar).
            self._show_installed_toolbar(True)
        else:
            # Los chips/orden de SearchPage aparecen recién cuando hay
            # resultados (ver _on_search_enter), no al abrir la barra
            # todavía vacía.
            self._show_search_toolbar(False)
        self._search_bar_row.setVisible(True)
        self._search_edit.clear()
        self._search_edit.setFocus()

    def _close_search_bar(self) -> None:
        self._search_bar_row.setVisible(False)
        self._search_edit.clear()
        self._show_search_toolbar(False)
        self._show_installed_toolbar(False)
        current = self._stack.currentIndex()
        if current == PAGE_SEARCH:
            # Volver a Explorar si estábamos viendo resultados de búsqueda.
            self._nav_to(PAGE_EXPLORE)
            current = PAGE_EXPLORE
        if current == PAGE_INSTALLED and self._pages[PAGE_INSTALLED] is not None:
            try:
                self._pages[PAGE_INSTALLED]._on_search_changed("")
            except Exception:
                pass

    def _update_search_btn_icon(self, page: int) -> None:
        """El botón de búsqueda de la cabecera cambia de icono y función
        según la página activa: en Actualizaciones se convierte en
        "comprobar actualizaciones"; en Instaladas, en "más opciones"
        (revela fuente + añadir AppImage en vez de solo buscar); en el
        resto, la lupa normal."""
        if page == PAGE_UPDATES:
            self._search_toggle_btn.setIcon(self._search_btn_icon(True))
            self._search_toggle_btn.setToolTip(tr("applet_check_now"))
            if self._search_bar_row.isVisible():
                self._search_bar_row.setVisible(False)
                self._search_edit.clear()
        elif page == PAGE_INSTALLED:
            self._search_toggle_btn.setIcon(self._more_options_btn_icon())
            self._search_toggle_btn.setToolTip(tr("nav_more_options_tooltip"))
        else:
            self._search_toggle_btn.setIcon(self._search_btn_icon(False))
            self._search_toggle_btn.setToolTip(tr("nav_search_tooltip"))

    # Ajustes: ahora es una ventana/diálogo independiente, no una página
    # dentro del stack.

    @Slot()
    def _open_settings_dialog(self) -> None:
        if getattr(self, "_settings_dialog", None) is None:
            from PySide6.QtWidgets import QDialog
            from .settings_page import SettingsPage

            dlg = QDialog(self)
            dlg.setWindowTitle(tr("nav_settings"))
            dlg.resize(680, 540)
            lay = QVBoxLayout(dlg)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(0)

            page = SettingsPage()
            page.language_changed.connect(self._on_language_changed)
            lay.addWidget(page)

            # Botón "Cerrar y Aplicar" abajo a la derecha. Los ajustes ya
            # se guardan automáticamente al cambiarlos (ver
            # SettingsPage._on_auto_save); este botón solo cierra la
            # ventana, dejando claro que lo elegido ya quedó aplicado.
            btn_row = QHBoxLayout()
            btn_row.setContentsMargins(16, 8, 16, 12)
            btn_row.addStretch(1)
            close_apply_btn = QPushButton(tr("settings_close_apply"))
            close_apply_btn.setObjectName("btnSettingsCloseApply")
            close_apply_btn.setCursor(Qt.PointingHandCursor)
            close_apply_btn.setStyleSheet(
                "QPushButton#btnSettingsCloseApply { background: #8AAB4A; color: #14150F; "
                "border-radius: 8px; border: none; padding: 8px 20px; font-weight: 600; }"
                "QPushButton#btnSettingsCloseApply:hover { background: #9FC15F; }"
            )
            close_apply_btn.clicked.connect(dlg.close)
            btn_row.addWidget(close_apply_btn)
            lay.addLayout(btn_row)

            self._settings_dialog = dlg
            self._settings_page_widget = page

        self._settings_dialog.show()
        self._settings_dialog.raise_()
        self._settings_dialog.activateWindow()

    # Barra de categorías (segunda fila): solo iconos por defecto,
    # centrada, y se expande a icono+texto al pasar el mouse por encima
    # o al quedar seleccionada. Visible únicamente en la pestaña Explorar
    # (se oculta en Instaladas, Actualizaciones, Acerca de y mientras se
    # está buscando).

    # Navigation

    def _on_explore_tab_clicked(self):
        """Reclickear "Explorar" siempre vuelve a su vista raíz, aunque
        ya se esté en una categoría."""
        already_on_explore = self._stack.currentIndex() == PAGE_EXPLORE
        self._nav_to(PAGE_EXPLORE)
        if already_on_explore:
            home = self._pages[PAGE_EXPLORE]
            if home is not None:
                home.show_root()

    def _on_updates_tasks_clicked(self):
        """La pestaña antes llamada Pendientes abre solo Actualizaciones."""
        self._nav_to(PAGE_UPDATES)

    def _open_tasks_window(self) -> None:
        """Abre el gestor de tareas en una ventana independiente."""
        if self._tasks_window is None:
            from .tasks_window import TasksWindow
            self._tasks_window = TasksWindow(self._task_manager, self)
        self._tasks_window.show()
        self._tasks_window.raise_()
        self._tasks_window.activateWindow()

    def _on_task_activity_changed(self, _task) -> None:
        self._refresh_task_activity()

    def _on_task_activity_completed(self, _task_id: str, _success: bool, _message: str) -> None:
        self._refresh_task_activity()

    def _refresh_task_activity(self) -> None:
        """Parpadea verde al instalar y rojo al eliminar."""
        active = self._task_manager.active_tasks
        installing = any(t.task_type in (TaskType.INSTALL, TaskType.LOCAL_INSTALL,
                                         TaskType.LOCAL_REINSTALL) for t in active)
        removing = any(t.task_type in (TaskType.REMOVE, TaskType.LOCAL_REMOVE) for t in active)
        new_color = "install" if installing else "remove" if removing else None
        if new_color == self._task_activity_color:
            return
        self._task_activity_color = new_color
        self._task_activity_on = bool(new_color)
        if new_color:
            self._task_activity_timer.start()
        else:
            self._task_activity_timer.stop()
        self._apply_task_activity_style()

    def _toggle_task_activity(self) -> None:
        if self._task_activity_color:
            self._task_activity_on = not self._task_activity_on
            self._apply_task_activity_style()

    def _apply_task_activity_style(self) -> None:
        btn = getattr(self, "_tasks_btn", None)
        if btn is None:
            return
        color = {"install": "#35D879", "remove": "#FF5A5F"}.get(
            self._task_activity_color
        )
        if color and self._task_activity_on:
            btn.setStyleSheet(
                f"QPushButton {{ background: {color}; color: #102018; border-radius: 8px; }}"
                f"QPushButton:hover {{ background: {color}; }}"
            )
        else:
            btn.setStyleSheet("")

    def _has_active_tasks(self) -> bool:
        try:
            return bool(self._task_manager.active_tasks)
        except Exception:
            return False

    def _sync_nav_selection(self, page: int | None) -> None:
        """Refleja qué página está activa en la UI de navegación: las
        pestañas superiores o los botones de Ajustes/Acerca de."""
        nav_page = page
        if page == PAGE_TASKS:
            nav_page = PAGE_UPDATES  # misma pestaña unificada

        for p, btn in self._nav_buttons.items():
            btn.setChecked(p == nav_page)
        if nav_page not in self._nav_buttons:
            self._nav_group.setExclusive(False)
            for btn in self._nav_buttons.values():
                btn.setChecked(False)
            self._nav_group.setExclusive(True)

        for btn, btn_page in self._bottom_buttons:
            btn.setChecked(btn_page == page)

        if hasattr(self, "_search_toggle_btn"):
            self._update_search_btn_icon(page if page is not None else -1)

    @Slot()
    def _on_home_explore_shown(self):
        """Se volvió de una categoría a la vista principal de Explorar
        (botón 'Volver')."""
        self._sync_nav_selection(PAGE_EXPLORE)

    @Slot(str)
    def _on_category_opened(self, category: str):
        """Se pulsó un tile de categoría dentro de Explorar (HomePage ya
        navegó internamente a su página de categoría): la pestaña
        resaltada arriba queda sin marcar, como una sub-página."""
        self._nav_group.setExclusive(False)
        for btn in self._nav_buttons.values():
            btn.setChecked(False)
        self._nav_group.setExclusive(True)

    @Slot(int)
    def _nav_to(self, page: int):
        if getattr(self, "_search_bar_row", None) is not None and self._search_bar_row.isVisible():
            self._search_bar_row.setVisible(False)
            self._search_edit.clear()
            self._show_search_toolbar(False)
            self._show_installed_toolbar(False)
        self._sync_nav_selection(page)
        self._prev_page = self._stack.currentIndex()

        # Build page on first visit (shows placeholder briefly while constructing)
        if self._pages[page] is None:
            # Show placeholder while building
            self._stack.setCurrentIndex(page)
            QTimer.singleShot(0, lambda p=page: self._finish_nav(p))
        else:
            self._stack.setCurrentIndex(page)

    def _finish_nav(self, page: int):
        """Called after placeholder is visible — builds the real page.

        Se re-sincroniza también la selección de navegación: el
        singleShot(0) de _init_home_page (encolado en el constructor)
        puede ejecutarse DESPUÉS de esta llamada cuando la navegación
        inicial pide una página distinta de Explorar (p. ej. el applet
        abriendo la tienda con --updates), y ese init pisa la pestaña
        activa dejándola en Explorar aunque el contenido mostrado ya
        sea el correcto. Reafirmar aquí la selección evita ese
        desajuste entre pestaña resaltada y contenido visible."""
        self._build_page(page)
        self._stack.setCurrentIndex(page)
        self._sync_nav_selection(page)

    # IPC slots

    @Slot()
    def raise_only(self):
        """Trae la ventana al frente sin cambiar de página. Usado cuando
        se relanza main.py normalmente (no --updates) y ya hay una
        instancia corriendo (ver _ipc_dispatch en main.py)."""
        self.setWindowState(self.windowState() & ~Qt.WindowMinimized | Qt.WindowActive)
        self.show()
        self.raise_()
        self.activateWindow()

    @Slot()
    def raise_and_show_updates(self):
        self._nav_to(PAGE_UPDATES)
        try:
            self._updates_page._reload_from_cache()
        except Exception:
            pass
        self._refresh_status_dot()
        self.setWindowState(self.windowState() & ~Qt.WindowMinimized | Qt.WindowActive)
        self.show()
        self.raise_()
        self.activateWindow()

    @Slot(object)
    def _open_detail(self, pkg: Package):
        self._prev_page = self._stack.currentIndex()
        detail = self._ensure_page(PAGE_DETAIL)
        try:
            detail.load_package(pkg)
        except Exception:
            # página -antes, una excepción aquí abortaba silenciosamente
            import traceback
            traceback.print_exc()
        self._stack.setCurrentIndex(PAGE_DETAIL)
        self._sync_nav_selection(None)

    @Slot()
    def _go_back(self):
        self._stack.setCurrentIndex(self._prev_page)
        self._sync_nav_selection(self._prev_page)

    # Search

    @Slot(str)
    def _on_search_changed(self, text: str):
        """El comportamiento depende de la página activa:
        - Instaladas: filtra en vivo la lista instalada.
        - Actualizaciones: filtra en vivo la lista de actualizaciones.
        - Explorar: no busca en cada tecla (Enter dispara la búsqueda de
          repos, ver _on_search_enter); solo vuelve a Explorar si el
          cuadro queda vacío estando ya en la página de resultados.
        """
        current = self._stack.currentIndex()
        if current == PAGE_INSTALLED and self._pages[PAGE_INSTALLED] is not None:
            self._pages[PAGE_INSTALLED]._on_search_changed(text)
            return
        if not text.strip() and current == PAGE_SEARCH:
            self._stack.setCurrentIndex(PAGE_EXPLORE)
            self._sync_nav_selection(PAGE_EXPLORE)
            self._show_search_toolbar(False)

    @Slot()
    def _on_search_enter(self):
        current = self._stack.currentIndex()
        if current in (PAGE_INSTALLED, PAGE_UPDATES):
            # Esas páginas ya filtran en vivo con cada tecla: Enter no
            # abre una página de resultados aparte.
            return
        text = self._search_edit.text().strip()
        if text:
            self._search_page.trigger_search(text)
            self._search_page.force_search()
            self._show_search_toolbar(True)
            if self._stack.currentIndex() != PAGE_SEARCH:
                self._prev_page = self._stack.currentIndex()
                self._stack.setCurrentIndex(PAGE_SEARCH)
                self._sync_nav_selection(None)
        else:
            self._show_search_toolbar(False)

    @Slot()
    def _on_escape(self):
        if self._search_bar_row.isVisible():
            self._close_search_bar()

    @Slot()
    def _focus_search(self):
        if self._stack.currentIndex() == PAGE_UPDATES:
            self._toggle_search_bar()  # dispara "comprobar actualizaciones"
            return
        if not self._search_bar_row.isVisible():
            self._open_search_bar()
        else:
            self._search_edit.setFocus()
            self._search_edit.selectAll()

    # Auto-sync de repos al arrancar

    def _maybe_auto_sync_repos(self):
        try:
            if not app_settings.get_bool("check_updates_on_startup"):
                return
        except Exception:
            return
        if self._auto_sync_running:
            return
        self._auto_sync_running = True

        import threading

        def _thread():
            try:
                from backend import repo_sync as _repo_sync
                # Sin privilegios: no debe pedir contraseña solo por abrir
                # el programa. La sincronización real (con privilegios)
                # queda para cuando el usuario la pida explícitamente.
                _repo_sync.refresh_pending_updates_local()
            except Exception:
                pass
            finally:
                self._auto_sync_running = False
                QTimer.singleShot(0, self._on_auto_sync_done)

        threading.Thread(target=_thread, daemon=True).start()

    def _on_auto_sync_done(self):
        # Vuelve al hilo de GUI: refresca el punto de estado y, si la
        # página de Actualizaciones ya está construida, su lista también.
        self._refresh_status_dot()
        updates_page = self._pages[PAGE_UPDATES]
        if updates_page is not None:
            try:
                updates_page._reload_from_cache()
            except Exception:
                pass

    # Status dot (alerta de actualizaciones, 4 colores)

    def _refresh_status_dot(self):
        if not app_settings.get_bool("show_update_status"):
            self._updates_status_dot.setVisible(False)
            if self._pages[PAGE_UPDATES] is not None:
                try:
                    self._pages[PAGE_UPDATES].refresh_status_banner()
                except Exception:
                    pass
            return
        self._updates_status_dot.setVisible(True)
        try:
            status = update_status.compute()
        except Exception:
            return
        self._updates_status_dot.set_status(status.level)
        tooltip = tr(status.title_key)
        if status.count:
            tooltip = f"{tooltip} ({status.count})"
        self._updates_status_dot.setToolTip(tooltip)
        # Si la página de Updates ya existe, que también refresque su banner
        if self._pages[PAGE_UPDATES] is not None:
            try:
                self._pages[PAGE_UPDATES].refresh_status_banner()
            except Exception:
                pass

    # Tasks badge (pestaña unificada Actualizaciones + Tareas)

    @staticmethod
    def _set_nav_button_label(btn: NavButton, label: str) -> None:
        """Actualiza el texto de una pestaña conservando su icono."""
        if btn.icon().isNull():
            btn.setText(label)
        else:
            btn.setText(f"  {label}")

    @Slot(int)
    def _on_tasks_count(self, count: int):
        # El contador de tareas ya no se mezcla con Actualizaciones.
        btn = getattr(self, "_tasks_btn", None)
        if btn is not None:
            btn.setToolTip(tr("nav_tasks") if count <= 0 else f"{tr('nav_tasks')} ({count})")

    # Language change

    @Slot(str)
    def _on_language_changed(self, lang: str):
        self._search_edit.setPlaceholderText(tr("search_placeholder"))

        page_labels = {
            PAGE_EXPLORE:   tr("nav_explore"),
            PAGE_INSTALLED: tr("nav_installed"),
        }
        for page, label in page_labels.items():
            btn = self._nav_buttons.get(page)
            if btn is not None:
                self._set_nav_button_label(btn, label)

        self._updates_tasks_label = tr("nav_updates")
        try:
            count = len(self._task_manager.active_tasks)
        except Exception:
            count = 0
        self._on_tasks_count(count)

        self._settings_btn.setToolTip(tr("nav_settings"))
        if getattr(self, "_settings_dialog", None) is not None:
            self._settings_dialog.setWindowTitle(tr("nav_settings"))

        bottom_labels = {PAGE_ABOUT: tr("nav_about")}
        for btn, page in self._bottom_buttons:
            label = bottom_labels.get(page)
            if label is None:
                continue
            # Estos botones son solo-icono (ver _nav_button/icon_only): no
            # se les debe volver a poner texto al cambiar de idioma, solo
            # refrescar el tooltip traducido.
            btn.setToolTip(label)

    # Cierre de la aplicación — avisar si hay trabajo en curso

    def _has_work_in_progress(self) -> bool:
        try:
            if self._task_manager.active_tasks:
                return True
        except Exception:
            pass
        updates_page = self._pages[PAGE_UPDATES]
        if updates_page is not None:
            try:
                if updates_page._proc_mgr.is_running():
                    return True
            except Exception:
                pass
        return False

    def closeEvent(self, event):
        if not self._has_work_in_progress():
            event.accept()
            return
        from PySide6.QtWidgets import QMessageBox
        reply = QMessageBox.warning(
            self,
            tr("app_name"),
            tr("confirm_exit_busy_message"),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            event.accept()
        else:
            event.ignore()

    # Arrastrar-y-soltar AppImages — soltar un .AppImage en cualquier
    # parte de la ventana abre el asistente de instalación con el
    # archivo ya cargado.

    @staticmethod
    def _dropped_appimage_paths(event) -> list[str]:
        md = event.mimeData()
        if not md.hasUrls():
            return []
        paths = []
        for url in md.urls():
            if not url.isLocalFile():
                continue
            p = url.toLocalFile()
            if p.lower().endswith(".appimage"):
                paths.append(p)
        return paths

    def dragEnterEvent(self, event):
        if self._dropped_appimage_paths(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if self._dropped_appimage_paths(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        paths = self._dropped_appimage_paths(event)
        if not paths:
            event.ignore()
            return
        event.acceptProposedAction()
        from .appimage_wizard import run_appimage_install_wizard
        for path in paths:
            run_appimage_install_wizard(self, self._task_manager, initial_path=path)
