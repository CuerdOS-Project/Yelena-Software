# yl-soft — Main Window

from __future__ import annotations

import os
import subprocess

from PySide6.QtCore import (
    Qt, Signal, Slot, QTimer, QSize, QPoint,
    QPropertyAnimation, QEasingCurve, QParallelAnimationGroup,
)
from PySide6.QtGui import (
    QKeySequence, QShortcut, QIcon, QFont, QFontDatabase, QActionGroup,
)
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QLabel, QStackedWidget, QLineEdit,
    QFrame, QApplication, QButtonGroup, QPushButton, QGridLayout,
    QMenu, QMessageBox,
)

from backend.task_manager import TaskManager
from backend.models import Package, PackageSource, TaskType

from .home_page import HomePage
from .native_widgets import NavButton, StatusDot, CountBadge, STATUS_COLORS

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


class _MiddleClickNavButton(NavButton):
    """NavButton que además emite `middle_clicked` con el botón central."""

    middle_clicked = Signal()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MiddleButton:
            if self.rect().contains(e.position().toPoint()):
                self.middle_clicked.emit()
            e.accept()
            return
        super().mouseReleaseEvent(e)

class _AnimatedOptionsMenu(QMenu):
    """Menú de opciones que entra con un leve deslizamiento y fade-in."""

    def showEvent(self, event):
        super().showEvent(event)
        target = self.pos()
        start = target + QPoint(0, -8)
        self.setWindowOpacity(0.0)
        self.move(start)

        group = QParallelAnimationGroup(self)
        opacity = QPropertyAnimation(self, b"windowOpacity", group)
        opacity.setDuration(170)
        opacity.setStartValue(0.0)
        opacity.setEndValue(1.0)
        opacity.setEasingCurve(QEasingCurve.OutCubic)
        movement = QPropertyAnimation(self, b"pos", group)
        movement.setDuration(190)
        movement.setStartValue(start)
        movement.setEndValue(target)
        movement.setEasingCurve(QEasingCurve.OutCubic)
        group.addAnimation(opacity)
        group.addAnimation(movement)
        self._open_animation = group
        group.start()


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
        self.setMinimumSize(860, 500)

        self._app_icon = _load_app_icon()
        if not self._app_icon.isNull():
            self.setWindowIcon(self._app_icon)
            QApplication.instance().setWindowIcon(self._app_icon)

        self._apply_system_font()
        self._task_manager = TaskManager(self)
        self._task_activity_color = None
        self._task_activity_on = False
        self._task_activity_timer = QTimer(self)
        self._task_activity_timer.setInterval(420)
        self._task_activity_timer.timeout.connect(self._toggle_task_activity)
        self._task_manager.task_added.connect(self._on_task_activity_changed)
        self._task_manager.task_completed.connect(self._on_task_activity_completed)
        self._task_manager.task_cancelled.connect(self._on_task_cancelled)

        # Arrastrar-y-soltar un .AppImage sobre cualquier parte de la
        # ventana abre el asistente de instalación con el archivo ya
        # cargado (ver dragEnterEvent/dropEvent más abajo).
        self.setAcceptDrops(True)

        # Lazy page registry: None until first visit
        self._pages: list[QWidget | None] = [None] * _TOTAL_PAGES
        self._prev_page = PAGE_EXPLORE

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

        self._stack.currentChanged.connect(self._sync_search_filters)
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

    def show_runtime_error(self, details: str = "") -> None:
        """Reporta errores no controlados sin cerrar la tienda (un solo
        aviso a la vez; el detalle completo va al log)."""
        import logging
        logging.getLogger(__name__).error("Error no controlado: %s", details)
        if getattr(self, "_runtime_error_open", False):
            return
        self._runtime_error_open = True
        box = QMessageBox(QMessageBox.Warning, "Yelena Software",
                          "Ocurrió un problema. Cierra y vuelve a ejecutar la tienda.",
                          QMessageBox.Ok, self)
        if details:
            box.setDetailedText(details)
        box.finished.connect(lambda _r: setattr(self, "_runtime_error_open", False))
        box.setAttribute(Qt.WA_DeleteOnClose)
        box.open()

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
            return SearchPage(self._task_manager)
        if page_id == PAGE_INSTALLED:
            from .installed_page import InstalledPage
            return InstalledPage(self._task_manager)
        if page_id == PAGE_UPDATES:
            from .updates_page import UpdatesPage
            return UpdatesPage(self)
        if page_id == PAGE_TASKS:
            # El gestor comparte la ventana y el TaskManager principal.
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
            page.sources_changed.connect(self._on_sources_changed)

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
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(6)
        # Tres columnas: los laterales absorben exactamente el mismo espacio
        # sobrante, por lo que las pestañas quedan centradas en X=0 de la
        # ventana, no solo entre los botones visibles.
        layout.setColumnStretch(0, 1)
        layout.setColumnStretch(1, 0)
        layout.setColumnStretch(2, 1)

        # --- Izquierda: columna vacía del mismo ancho que la derecha, solo
        # para que el grupo búsqueda + pestañas quede centrado en la ventana.
        left_box = QWidget()
        left_box.setFixedWidth(140)
        layout.addWidget(left_box, 0, 0, Qt.AlignLeft | Qt.AlignVCenter)

        search_btn = _MiddleClickNavButton("")
        search_btn.setCheckable(False)
        search_btn.middle_clicked.connect(self._open_repo_search)
        search_btn.setIcon(_find_icon(["edit-find-symbolic", "edit-find", "system-search", "search"]))
        search_btn.setIconSize(QSize(16, 16))
        search_btn.setFixedSize(32, 32)
        search_btn.setToolTip(tr("nav_search_tooltip"))
        search_btn.clicked.connect(self._toggle_search_bar)
        self._search_toggle_btn = search_btn

        # --- Centro: búsqueda + pestañas (Explorar / Instaladas / Actualizaciones),
        # agrupadas en una sola barra para leerlas y usarlas como un conjunto ---
        tabs_frame = QFrame()
        tabs_frame.setObjectName("tabsBar")
        tabs_lay = QHBoxLayout(tabs_frame)
        tabs_lay.setContentsMargins(3, 2, 3, 2)
        tabs_lay.setSpacing(2)

        tabs_lay.addWidget(search_btn)
        sep = QFrame()
        sep.setFrameShape(QFrame.VLine)
        sep.setFrameShadow(QFrame.Plain)
        sep.setFixedSize(1, 18)
        sep.setStyleSheet("background: palette(mid); border: none;")
        tabs_lay.addSpacing(3)
        tabs_lay.addWidget(sep, alignment=Qt.AlignVCenter)
        tabs_lay.addSpacing(3)

        self._nav_group = QButtonGroup(self)
        self._nav_group.setExclusive(True)
        self._nav_buttons: dict[int, NavButton] = {}

        # Mismo ancho para las pestañas; deja espacio suficiente para el
        # rótulo más largo (Actualizaciones) y un margen visual adicional.
        _TAB_WIDTH = 154

        def add_tab(page: int, icon_names: list[str], label: str) -> NavButton:
            btn = _nav_button(icon_names, label)
            btn.setCheckable(True)
            btn.setFixedWidth(_TAB_WIDTH)
            btn.setFixedHeight(32)
            btn.setIconSize(QSize(16, 16))
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

        # Actualizaciones y Tareas son páginas distintas; el botón de Tareas
        # abre su gestor dentro del stack principal.
        self._updates_tasks_label = tr("nav_updates")
        updates_btn = add_tab(
            PAGE_UPDATES,
            ["software-update-available-symbolic", "system-software-update-symbolic", "update-manager"],
            self._updates_tasks_label,
        )
        updates_btn.clicked.connect(self._on_updates_tasks_clicked)
        self._updates_tasks_btn = updates_btn

        # Contador de actualizaciones pendientes (con el color del nivel de
        # estado). Con 0 se oculta y queda solo el punto de estado.
        self._updates_count_badge = CountBadge()
        tabs_lay.addWidget(self._updates_count_badge, alignment=Qt.AlignVCenter)

        self._updates_status_dot = StatusDot(diameter=9)
        self._updates_status_dot.setToolTip(tr("status_gray_title"))
        tabs_lay.addWidget(self._updates_status_dot)

        layout.addWidget(tabs_frame, 0, 1, Qt.AlignCenter)

        # --- Derecha: Tareas / Ajustes (ventana aparte) / Acerca de ---
        right_box = QWidget()
        right_box.setFixedWidth(140)
        right_lay = QHBoxLayout(right_box)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.setSpacing(4)
        right_lay.addStretch(1)

        tasks_btn = _nav_button(
            ["view-list-symbolic", "view-list", "system-run-symbolic", "task-due"],
            tr("nav_tasks"),
            icon_only=True,
        )
        tasks_btn.setCheckable(True)
        tasks_btn.setFixedSize(32, 32)
        tasks_btn.setIconSize(QSize(16, 16))
        tasks_btn.clicked.connect(self._open_tasks_page)
        self._tasks_btn = tasks_btn

        options_btn = _nav_button(
            ["preferences-other-symbolic", "view-more-symbolic", "open-menu-symbolic", "view-list-symbolic"],
            tr("nav_options_tooltip"),
            icon_only=True,
        )
        if options_btn.icon().isNull():
            options_btn.setText("⋯")
        options_btn.setAccessibleName(tr("nav_options_tooltip"))
        options_btn.setObjectName("catalogOptionsButton")
        options_btn.setCheckable(True)
        options_btn.setFixedSize(32, 32)
        options_btn.setIconSize(QSize(16, 16))
        options_btn.clicked.connect(self._show_catalog_options)
        self._options_btn = options_btn
        options_btn.setVisible(False)

        settings_btn = _nav_button(
            ["preferences-system-symbolic", "preferences-system", "configure"],
            tr("nav_settings"),
            icon_only=True,
        )
        settings_btn.setCheckable(False)
        settings_btn.setFixedSize(32, 32)
        settings_btn.setIconSize(QSize(16, 16))
        settings_btn.clicked.connect(self._open_settings_dialog)
        self._settings_btn = settings_btn

        about_btn = _nav_button(
            ["help-about-symbolic", "help-about", "dialog-information"],
            tr("nav_about"),
            icon_only=True,
        )
        about_btn.setFixedSize(32, 32)
        about_btn.setIconSize(QSize(16, 16))
        about_btn.clicked.connect(lambda: self._nav_to(PAGE_ABOUT))

        right_lay.addWidget(tasks_btn)
        right_lay.addWidget(options_btn)
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

    @staticmethod
    def _add_combo_actions(menu: QMenu, combo) -> None:
        """Representa las opciones de un QComboBox como acciones de menú."""
        group = QActionGroup(menu)
        group.setExclusive(True)
        for index in range(combo.count()):
            action = menu.addAction(combo.itemText(index))
            action.setCheckable(True)
            action.setChecked(index == combo.currentIndex())
            group.addAction(action)
            action.triggered.connect(
                lambda _checked=False, c=combo, i=index: c.setCurrentIndex(i)
            )

    def _show_catalog_options(self) -> None:
        """Abre los filtros y acciones que antes ocupaban la barra de búsqueda."""
        current = self._stack.currentIndex()
        menu = _AnimatedOptionsMenu(self)

        if current == PAGE_INSTALLED:
            page = self._pages[PAGE_INSTALLED] or self._installed_page
            source_combo = page._source_combo
            source_menu = menu.addMenu(tr("search_source_label"))
            self._add_combo_actions(source_menu, source_combo)

            right_layout = page._toolbar_right.layout()
            if right_layout is not None and right_layout.count():
                menu.addSeparator()
                add_appimage = menu.addAction(tr("add_appimage_btn"))
                add_appimage.triggered.connect(page._on_add_appimage)
        else:
            page = self._search_page
            source_menu = menu.addMenu(tr("search_source_label"))
            self._add_combo_actions(source_menu, page._source_combo)

            sort_title = tr("search_sort_label").rstrip(" :：")
            sort_menu = menu.addMenu(sort_title)
            self._add_combo_actions(sort_menu, page._sort_combo)

            menu.addSeparator()
            show_hidden = menu.addAction(tr("search_show_hidden"))
            show_hidden.setCheckable(True)
            show_hidden.setChecked(page._hidden_chk.isChecked())
            show_hidden.setToolTip(page._hidden_chk.toolTip())
            show_hidden.toggled.connect(page._hidden_chk.setChecked)

        menu.aboutToShow.connect(lambda: self._options_btn.setChecked(True))
        menu.aboutToHide.connect(lambda m=menu: self._options_menu_closed(m))
        self._options_menu = menu
        pos = self._options_btn.mapToGlobal(
            QPoint(0, self._options_btn.height() + 4)
        )
        menu.popup(pos)

    def _options_menu_closed(self, menu: QMenu) -> None:
        if getattr(self, "_options_menu", None) is menu:
            self._options_menu = None
        if hasattr(self, "_options_btn"):
            self._options_btn.setChecked(False)
        menu.deleteLater()

    def _sync_catalog_options_visibility(self) -> None:
        """Las opciones de filtro solo aparecen en resultados o con búsqueda abierta."""
        button = getattr(self, "_options_btn", None)
        if button is None:
            return
        page = self._stack.currentIndex() if hasattr(self, "_stack") else PAGE_EXPLORE
        search_row_open = (
            hasattr(self, "_search_bar_row")
            and not self._search_bar_row.isHidden()
        )
        visible = page == PAGE_SEARCH or (
            search_row_open and page == PAGE_EXPLORE
        )
        # Filtros y orden ahora viven en la barra local de cada página, así
        # que el menú de opciones superior ya no se muestra.
        button.setVisible(False)
        menu = getattr(self, "_options_menu", None)
        if not visible and menu is not None and menu.isVisible():
            menu.close()

    def _search_btn_icon(self, for_updates: bool) -> QIcon:
        if for_updates:
            return _find_icon([
                "software-update-available-symbolic", "system-software-update-symbolic",
                "system-software-update", "view-refresh-symbolic", "view-refresh",
            ])
        return _find_icon(["edit-find-symbolic", "edit-find", "system-search", "search"])

    def _build_search_bar_row(self) -> QWidget:
        """Fila centrada de búsqueda; filtros y orden se agrupan en el menú superior."""
        row = QFrame()
        row.setObjectName("searchBarRow")
        outer = QHBoxLayout(row)
        outer.setContentsMargins(16, 8, 16, 8)
        outer.setSpacing(10)
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

        # Aquí se ancla la barra de filtros de la página de resultados
        # (fuente, vista, ocultos, orden): buscador y filtros en UNA línea.
        self._search_filters_host = QWidget()
        host_lay = QHBoxLayout(self._search_filters_host)
        host_lay.setContentsMargins(0, 0, 0, 0)
        host_lay.setSpacing(8)
        self._search_filters_host.setVisible(False)
        inner_lay.addWidget(self._search_filters_host)

        close_btn = NavButton("")
        close_btn.setCheckable(False)
        close_btn.setIcon(_find_icon(["window-close-symbolic", "window-close"]))
        close_btn.setFixedSize(28, 28)
        close_btn.setToolTip(tr("nav_search_tooltip"))
        close_btn.clicked.connect(self._toggle_search_bar)
        inner_lay.addWidget(close_btn)

        outer.addWidget(inner, 0, Qt.AlignCenter)
        outer.addStretch(1)

        row.setVisible(False)
        return row

    @Slot()
    def _open_repo_search(self) -> None:
        """Clic central en la lupa: lleva a Explorar y abre la búsqueda de
        apps en los repos, desde cualquier página (salvo la de resultados)."""
        current = self._stack.currentIndex()
        if current == PAGE_SEARCH:
            return
        if current != PAGE_EXPLORE:
            self._nav_to(PAGE_EXPLORE)
        if self._search_bar_row.isVisible():
            self._search_edit.setFocus()
            self._search_edit.selectAll()
        else:
            self._open_search_bar()

    @Slot()
    def _toggle_search_bar(self) -> None:
        current = self._stack.currentIndex()
        if current == PAGE_INSTALLED:
            # Instaladas tiene su propio campo en la barra local, oculto por
            # defecto: la lupa lo muestra y lo oculta.
            page = self._pages[PAGE_INSTALLED]
            if page is not None:
                page.toggle_search()
            return
        if current == PAGE_TASKS:
            # Tareas tiene su propio campo de búsqueda local, oculto por
            # defecto: la lupa lo muestra y lo oculta (solo filtra tareas).
            page = self._pages[PAGE_TASKS]
            if page is not None:
                page.toggle_search()
            return
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

    def _sync_search_filters(self, *_args) -> None:
        """Ancla los filtros de resultados en la fila del buscador mientras se
        ven los resultados con el buscador abierto; si no, los deja en la página."""
        page = self._pages[PAGE_SEARCH]
        if page is None:
            return
        if (self._stack.currentIndex() == PAGE_SEARCH
                and not self._search_bar_row.isHidden()):
            page.attach_filter_bar(self._search_filters_host)
        else:
            page.detach_filter_bar()

    def _open_search_bar(self) -> None:
        self._search_bar_row.setVisible(True)
        self._sync_search_filters()
        self._sync_catalog_options_visibility()
        self._search_edit.clear()
        self._search_edit.setFocus()

    def _close_search_bar(self) -> None:
        self._search_bar_row.setVisible(False)
        self._sync_search_filters()
        self._sync_catalog_options_visibility()
        self._search_edit.clear()
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
        "comprobar actualizaciones"; en las demás páginas conserva la
        función de búsqueda. Los filtros están en el nuevo botón de opciones."""
        if page == PAGE_UPDATES:
            self._search_toggle_btn.setIcon(self._search_btn_icon(True))
            self._search_toggle_btn.setToolTip(tr("applet_check_now"))
            if self._search_bar_row.isVisible():
                self._search_bar_row.setVisible(False)
                self._search_edit.clear()
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
            page.sources_changed.connect(self._on_sources_changed)
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

    def _open_tasks_page(self) -> None:
        """Abre el gestor de tareas como una página de la ventana principal."""
        self._nav_to(PAGE_TASKS)

    def _on_task_activity_changed(self, task) -> None:
        self._refresh_task_activity()
        if task.package.source == PackageSource.APPIMAGE:
            # Las instalaciones AppImage suelen tardar y su descarga puede
            # emitir progreso por carriage-return; lleva al usuario al estado
            # vivo y abre la consola de esa tarea, en lugar de solo parpadear
            # el icono de Tareas.
            task_id = task.id
            self._nav_to(PAGE_TASKS)
            QTimer.singleShot(0, lambda tid=task_id: self._focus_appimage_task(tid))

    def _focus_appimage_task(self, task_id: str, attempts: int = 0) -> None:
        page = self._pages[PAGE_TASKS]
        if page is None:
            if attempts < 20:
                QTimer.singleShot(50, lambda tid=task_id, n=attempts + 1: self._focus_appimage_task(tid, n))
            return
        if not page.select_task(task_id, show_output=True) and attempts < 20:
            QTimer.singleShot(50, lambda tid=task_id, n=attempts + 1: self._focus_appimage_task(tid, n))

    def _on_task_activity_completed(self, _task_id: str, _success: bool, _message: str) -> None:
        self._refresh_task_activity()

    @Slot(str)
    def _on_task_cancelled(self, _task_id: str) -> None:
        """Detiene el blink si una cancelación dejó la cola sin tareas activas."""
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
        """Refleja qué página está activa en la navegación superior."""
        nav_page = page

        for p, btn in self._nav_buttons.items():
            btn.setChecked(p == nav_page)
        if nav_page not in self._nav_buttons:
            self._nav_group.setExclusive(False)
            for btn in self._nav_buttons.values():
                btn.setChecked(False)
            self._nav_group.setExclusive(True)

        for btn, btn_page in self._bottom_buttons:
            btn.setChecked(btn_page == page)

        tasks_btn = getattr(self, "_tasks_btn", None)
        if tasks_btn is not None:
            tasks_btn.setChecked(page == PAGE_TASKS)

        if hasattr(self, "_search_toggle_btn"):
            self._update_search_btn_icon(page if page is not None else -1)
        self._sync_catalog_options_visibility()

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
        self._sync_nav_selection(page)
        self._prev_page = self._stack.currentIndex()

        # Build page on first visit (shows placeholder briefly while constructing)
        if self._pages[page] is None:
            # Show placeholder while building
            self._stack.setCurrentIndex(page)
            QTimer.singleShot(0, lambda p=page: self._finish_nav(p))
        else:
            self._stack.setCurrentIndex(page)
        self._sync_catalog_options_visibility()

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

    @Slot()
    def reload_from_applet(self):
        """El applet terminó una comprobación: refresca estado y lista sin
        construir la página de Actualizaciones si todavía no existe."""
        self._refresh_status_dot()
        page = self._pages[PAGE_UPDATES]
        if page is not None:
            try:
                page._reload_from_cache()
            except Exception:
                pass

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
        # La barra de búsqueda no se muestra dentro del detalle del paquete;
        # se recuerda si estaba abierta para restaurarla al volver.
        if self._stack.currentIndex() != PAGE_DETAIL:
            self._search_row_before_detail = self._search_bar_row.isVisible()
        self._search_bar_row.setVisible(False)
        self._stack.setCurrentIndex(PAGE_DETAIL)
        self._sync_search_filters()
        self._sync_nav_selection(None)

    @Slot()
    def _go_back(self):
        if getattr(self, "_search_row_before_detail", False):
            self._search_bar_row.setVisible(True)
        self._search_row_before_detail = False
        self._stack.setCurrentIndex(self._prev_page)
        self._sync_search_filters()
        self._sync_catalog_options_visibility()
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
            if self._stack.currentIndex() != PAGE_SEARCH:
                self._prev_page = self._stack.currentIndex()
                self._stack.setCurrentIndex(PAGE_SEARCH)
                self._sync_nav_selection(None)
            self._sync_search_filters()
            QTimer.singleShot(0, self._sync_search_filters)

    @Slot()
    def _on_escape(self):
        if self._search_bar_row.isVisible():
            self._close_search_bar()

    @Slot()
    def _focus_search(self):
        if self._stack.currentIndex() == PAGE_INSTALLED:
            page = self._pages[PAGE_INSTALLED]
            if page is not None:
                page.show_search()      # Ctrl+F: mostrar y enfocar (no alternar)
            return
        if self._stack.currentIndex() == PAGE_TASKS:
            page = self._pages[PAGE_TASKS]
            if page is not None:
                page.show_search()
            return
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
            self._updates_count_badge.set_count(0)
            if self._pages[PAGE_UPDATES] is not None:
                try:
                    self._pages[PAGE_UPDATES].refresh_status_banner()
                except Exception:
                    pass
            return
        try:
            status = update_status.compute()
        except Exception:
            self._updates_status_dot.setVisible(True)
            return
        self._updates_status_dot.set_status(status.level)
        tooltip = tr(status.title_key)
        if status.count:
            tooltip = f"{tooltip} ({status.count})"
        self._updates_status_dot.setToolTip(tooltip)
        # Con paquetes pendientes manda el contador (coloreado por nivel);
        # sin ellos, el punto sigue informando del estado (al día, error...).
        self._updates_count_badge.set_count(
            status.count, STATUS_COLORS.get(status.level, STATUS_COLORS["gray"]))
        self._updates_count_badge.setToolTip(tooltip)
        self._updates_status_dot.setVisible(status.count <= 0)
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

    @Slot()
    def _on_sources_changed(self):
        """Refresh cached AM search results after a repository install/sync."""
        try:
            from core import search_cache
            search_cache.invalidate("appimage:am-FUSION")
            search_cache.invalidate("xbps:fpm")
        except Exception:
            pass
        pages = getattr(self, "_pages", [])
        page = pages[PAGE_SEARCH] if len(pages) > PAGE_SEARCH else None
        query = getattr(page, "_last_query", "").strip() if page is not None else ""
        if query:
            page._run_search()

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

    def _shutdown_detail_workers(self) -> None:
        detail_page = self._pages[PAGE_DETAIL]
        if detail_page is not None:
            try:
                detail_page.shutdown_screenshot_workers()
            except Exception:
                pass

    def closeEvent(self, event):
        if not self._has_work_in_progress():
            search_page = self._pages[PAGE_SEARCH]
            if search_page is not None:
                search_page.shutdown_workers()
            self._shutdown_detail_workers()
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
            search_page = self._pages[PAGE_SEARCH]
            if search_page is not None:
                search_page.shutdown_workers()
            self._shutdown_detail_workers()
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
