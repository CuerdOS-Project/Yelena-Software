# Vera_Shop — App Detail Page

from __future__ import annotations

from urllib.request import Request, urlopen

from PySide6.QtCore import Qt, Signal, Slot, QThread, QSize
from PySide6.QtGui import QColor, QTextCursor, QTextBlockFormat, QTextCharFormat, QPixmap
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QScrollArea, QFrame, QSizePolicy, QTextBrowser, QComboBox, QPushButton,
)

from core.i18n import tr, tr_category
from backend.models import Package, PackageSource, PackageStatus, TaskType
from backend.task_manager import TaskManager
from .icon_widget import AppIconWidget
from .native_widgets import BadgeLabel, InstallButton, RemoveButton, UpdateButton, BackButton


def _pkg_fallback_icon(pkg: Package) -> str:
    """Fallback coherente por fuente, centralizado en Package."""
    return pkg.icon_fallback_name


def _make_sep():
    f = QFrame()
    f.setObjectName("separator")
    f.setFrameShape(QFrame.HLine)
    return f


def _meta_row(label: str, value: str) -> QWidget:
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 2, 0, 2)
    lbl = QLabel(label)
    lbl.setObjectName("detailMeta")
    lbl.setFixedWidth(110)
    val = QLabel(value or "—")
    val.setObjectName("detailMetaValue")
    val.setWordWrap(True)
    h.addWidget(lbl)
    h.addWidget(val, stretch=1)
    return w



class _StarsWidget(QWidget):
    """Valoración dibujada con estrellas vectoriales (sin caracteres de texto)."""
    _SIZE = 16

    def __init__(self, parent=None):
        super().__init__(parent)
        self._filled = -1
        self.setFixedSize(self._SIZE * 5 + 8, self._SIZE)
        self.setVisible(False)

    def set_filled(self, n: int) -> None:
        self._filled = n
        self.setVisible(n >= 0)
        self.update()

    def paintEvent(self, _event):
        from PySide6.QtGui import QPainter, QColor
        from PySide6.QtCore import QRectF
        from .native_widgets import _paint_symbolic
        if self._filled < 0:
            return
        p = QPainter(self)
        color = QColor("#F5B301")
        for i in range(5):
            r = QRectF(i * (self._SIZE + 2), 0, self._SIZE, self._SIZE)
            _paint_symbolic(p, "star_filled" if i < self._filled else "star", r, color)
        p.end()


class _AMMetaWorker(QThread):
    """Obtiene tamaño/versión (release remota) y ficha (`fpm aimg info`) de una
    app am-FUSION sin bloquear la interfaz."""

    resolved = Signal(str, dict)

    def __init__(self, pkg_id: str, name: str, parent=None):
        super().__init__(parent)
        self._pkg_id = pkg_id
        self._name = name

    def run(self):
        data: dict = {}
        try:
            from backend import appimage_backend, appimage_meta
            meta = appimage_meta.fetch(self._name)
            if meta:
                data.update(meta)
            info = appimage_backend.am_info(self._name)
            if info:
                data["info"] = info.get("text", "")
                data["website"] = info.get("website", "")
        except Exception:
            pass
        self.resolved.emit(self._pkg_id, data)


class _ScreenshotWorker(QThread):
    """Descarga capturas en segundo plano y limita tamaño/tiempo por imagen."""

    loaded = Signal(str, str, bytes)

    def __init__(self, pkg_id: str, urls: list[str], parent=None):
        super().__init__(parent)
        self._pkg_id = pkg_id
        self._urls = urls

    def run(self):
        max_bytes = 8 * 1024 * 1024
        for url in self._urls:
            if self.isInterruptionRequested():
                break
            data = b""
            try:
                request = Request(url, headers={"User-Agent": "Yelena Software"})
                with urlopen(request, timeout=8) as response:
                    content_type = response.headers.get_content_type()
                    if content_type.startswith("image/"):
                        data = response.read(max_bytes + 1)
                        if len(data) > max_bytes:
                            data = b""
            except Exception:
                pass
            self.loaded.emit(self._pkg_id, url, data)


class AppDetailPage(QWidget):
    """
    Full-screen app detail view.
    Emits `go_back` when the user presses Back.
    """

    go_back = Signal()

    def __init__(self, task_manager: TaskManager, parent=None):
        super().__init__(parent)
        self._tm = task_manager
        self._package: Package | None = None
        self._switching_source = False
        # Maps task_id -> TaskType so we know what completed
        self._pending: dict[str, TaskType] = {}
        self._meta_looked_up: set[str] = set()
        self._meta_workers: list[_AMMetaWorker] = []
        self._screenshot_workers: list[_ScreenshotWorker] = []
        self._screenshot_tiles: dict[str, QLabel] = {}
        self._screenshot_package_id: str | None = None

        self.setObjectName("detailPage")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Scroll container
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        root.addWidget(scroll)

        content = QWidget()
        content.setObjectName("detailPage")
        self._content_layout = QVBoxLayout(content)
        self._content_layout.setContentsMargins(32, 28, 32, 32)
        self._content_layout.setSpacing(18)
        scroll.setWidget(content)
        self._content_widget = content

        # Back button
        back_btn = BackButton(tr("back"))
        back_btn.setMinimumWidth(80)
        back_btn.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        back_btn.clicked.connect(self.go_back)
        self._content_layout.addWidget(back_btn, alignment=Qt.AlignLeft)

        # Hero row
        hero = QHBoxLayout()
        hero.setSpacing(20)

        self._icon = AppIconWidget("App", size=80, radius=18)
        hero.addWidget(self._icon)

        name_col = QVBoxLayout()
        name_col.setSpacing(4)

        self._name_lbl = QLabel(tr("details_app_name_placeholder"))
        self._name_lbl.setObjectName("detailName")
        name_col.addWidget(self._name_lbl)

        badge_row = QHBoxLayout()
        badge_row.setSpacing(8)
        self._source_badge = BadgeLabel("", "flatpak", show_icon=False)
        badge_row.addWidget(self._source_badge)

        self._installed_badge = BadgeLabel(tr("badge_installed"), "installed", show_icon=False)
        self._installed_badge.setVisible(False)
        badge_row.addWidget(self._installed_badge)

        self._arch_badge = BadgeLabel(
            tr("badge_32bit"), "architecture",
            tooltip=tr("warning_32bit"), show_icon=False,
        )
        self._arch_badge.setVisible(False)
        badge_row.addWidget(self._arch_badge)

        self._version_lbl = QLabel("")
        self._version_lbl.setObjectName("detailVersion")
        badge_row.addWidget(self._version_lbl)
        badge_row.addStretch()
        name_col.addLayout(badge_row)

        self._stars_lbl = _StarsWidget()
        self._stars_lbl.setObjectName("stars")
        name_col.addWidget(self._stars_lbl)

        self._source_selector = QComboBox()
        self._source_selector.setObjectName("detailSourceSelector")
        self._source_selector.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self._source_selector.setMinimumWidth(92)
        self._source_selector.setMaximumWidth(180)
        self._source_selector.setToolTip(tr("search_source_label"))
        self._source_selector.currentIndexChanged.connect(self._on_source_changed)
        self._source_selector.setVisible(False)
        name_col.addWidget(self._source_selector)

        hero.addLayout(name_col, stretch=1)

        # Action buttons
        btn_col = QVBoxLayout()
        btn_col.setSpacing(8)
        btn_col.setAlignment(Qt.AlignTop | Qt.AlignRight)

        self._install_btn = InstallButton(tr("install"))
        self._install_btn.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self._install_btn.clicked.connect(self._on_install)
        btn_col.addWidget(self._install_btn)

        self._remove_btn = RemoveButton(tr("remove"))
        self._remove_btn.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self._remove_btn.setVisible(False)
        self._remove_btn.clicked.connect(self._on_remove)
        btn_col.addWidget(self._remove_btn)

        self._update_btn = UpdateButton(tr("update"))
        self._update_btn.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self._update_btn.setVisible(False)
        self._update_btn.clicked.connect(self._on_update)
        btn_col.addWidget(self._update_btn)

        hero.addLayout(btn_col)
        self._content_layout.addLayout(hero)
        self._content_layout.addWidget(_make_sep())

        # Summary
        self._summary_lbl = QLabel()
        self._summary_lbl.setObjectName("detailDesc")
        self._summary_lbl.setWordWrap(True)
        self._content_layout.addWidget(self._summary_lbl)

        self._arch_warning_lbl = QLabel(tr("warning_32bit"))
        self._arch_warning_lbl.setObjectName("architectureWarning")
        self._arch_warning_lbl.setWordWrap(True)
        self._arch_warning_lbl.setStyleSheet(
            "QLabel#architectureWarning { color: #B45309; "
            "background-color: rgba(217, 119, 6, 28); "
            "border-left: 3px solid #D97706; border-radius: 4px; padding: 8px; }"
        )
        self._arch_warning_lbl.setVisible(False)
        self._content_layout.addWidget(self._arch_warning_lbl)

        # Cuerpo de la ficha: contenido editorial a la izquierda y detalles
        # técnicos del paquete a la derecha.
        self._screenshots_panel = QFrame()
        self._screenshots_panel.setObjectName("detailScreenshots")
        self._screenshots_panel.setMinimumHeight(150)
        self._screenshots_panel.setMaximumHeight(230)
        shot_lay = QVBoxLayout(self._screenshots_panel)
        shot_lay.setContentsMargins(10, 8, 10, 8)
        shot_lay.setSpacing(6)
        shot_title = QLabel(tr("details_screenshots"))
        shot_title.setObjectName("sectionSub")
        shot_lay.addWidget(shot_title)
        self._screenshot_scroll = QScrollArea()
        self._screenshot_scroll.setWidgetResizable(True)
        self._screenshot_scroll.setFrameShape(QFrame.NoFrame)
        self._screenshot_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._screenshot_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._screenshot_strip = QWidget()
        self._screenshot_strip_layout = QHBoxLayout(self._screenshot_strip)
        self._screenshot_strip_layout.setContentsMargins(0, 0, 0, 0)
        self._screenshot_strip_layout.setSpacing(8)
        self._screenshot_scroll.setWidget(self._screenshot_strip)
        shot_lay.addWidget(self._screenshot_scroll, stretch=1)
        self._screenshots_panel.setVisible(False)

        self._desc_browser = QTextBrowser()
        self._desc_browser.setObjectName("detailDesc")
        self._desc_browser.setFrameShape(QFrame.NoFrame)
        self._desc_browser.viewport().setAutoFillBackground(False)
        self._desc_browser.setMinimumHeight(130)
        self._desc_browser.setMaximumHeight(320)
        self._desc_browser.setOpenExternalLinks(True)

        self._changelog_title = QLabel(tr("details_changelog"))
        self._changelog_title.setObjectName("sectionHeader")
        self._changelog_browser = QTextBrowser()
        self._changelog_browser.setObjectName("detailChangelog")
        self._changelog_browser.setFrameShape(QFrame.NoFrame)
        self._changelog_browser.setMinimumHeight(80)
        self._changelog_browser.setMaximumHeight(220)

        left_col = QVBoxLayout()
        left_col.setSpacing(10)
        left_col.addWidget(self._screenshots_panel)
        self._description_title = QLabel(tr("details_description"))
        self._description_title.setObjectName("sectionHeader")
        description_font = self._description_title.font()
        description_font.setBold(True)
        self._description_title.setFont(description_font)
        left_col.addWidget(self._description_title, alignment=Qt.AlignLeft)
        left_col.addWidget(self._desc_browser)
        left_col.addWidget(self._changelog_title)
        left_col.addWidget(self._changelog_browser)

        details_card = QFrame()
        details_card.setObjectName("packageDetailsCard")
        details_card.setMinimumWidth(280)
        details_card.setMaximumWidth(390)
        details_layout = QVBoxLayout(details_card)
        details_layout.setContentsMargins(18, 16, 18, 16)
        details_layout.setSpacing(6)
        details_lbl = QLabel(tr("details_package"))
        details_lbl.setObjectName("sectionHeader")
        details_font = details_lbl.font()
        details_font.setBold(True)
        details_lbl.setFont(details_font)
        details_layout.addWidget(details_lbl)
        self._meta_container = QVBoxLayout()
        self._meta_container.setSpacing(4)
        details_layout.addLayout(self._meta_container)
        self._dependencies_button = QPushButton(tr("details_view_dependencies"))
        self._dependencies_button.setObjectName("detailsDependenciesButton")
        self._dependencies_button.clicked.connect(self._toggle_dependencies)
        details_layout.addWidget(self._dependencies_button)
        self._dependencies_browser = QTextBrowser()
        self._dependencies_browser.setObjectName("detailsDependencies")
        self._dependencies_browser.setMaximumHeight(180)
        self._dependencies_browser.setVisible(False)
        details_layout.addWidget(self._dependencies_browser)
        details_layout.addStretch()

        body = QHBoxLayout()
        body.setSpacing(22)
        body.addLayout(left_col, stretch=2)
        body.addWidget(details_card, stretch=1, alignment=Qt.AlignTop)
        self._content_layout.addLayout(body)

        self._content_layout.addStretch()

        # Connect task completion to update button states
        self._tm.task_completed.connect(self._on_task_finished)

    # Public

    def load_package(self, pkg: Package):
        self._package = pkg

        variants = []
        seen_sources = set()
        for variant in getattr(pkg, "_source_variants", (pkg,)):
            source_key = variant.source_label
            if source_key in seen_sources:
                continue
            seen_sources.add(source_key)
            variants.append(variant)
        self._source_selector.blockSignals(True)
        self._source_selector.clear()
        for variant in variants:
            self._source_selector.addItem(variant.source_label, variant)
            self._source_selector.setItemData(
                self._source_selector.count() - 1,
                variant.source_label,
                Qt.ToolTipRole,
            )
        self._source_selector.setVisible(len(variants) > 1)
        for index, variant in enumerate(variants):
            if variant.id == pkg.id:
                self._source_selector.setCurrentIndex(index)
                break
        self._source_selector.blockSignals(False)

        # Icon
        self._icon._app_name  = pkg.name
        self._icon._icon_name = pkg.icon_name
        self._icon._icon_url  = pkg.icon_url
        self._icon._fallback_icon_name = _pkg_fallback_icon(pkg)
        self._icon._render_gradient_fallback()          # show placeholder immediately
        self._icon._load_icon()                         # resolve real icon

        # Name & badges
        self._name_lbl.setText(pkg.name)
        self._version_lbl.setText(pkg.version or "")
        self._arch_badge.setVisible(pkg.is_32bit)
        self._arch_warning_lbl.setVisible(pkg.is_32bit)

        if pkg.source == PackageSource.FLATPAK:
            self._source_badge.set_badge_type("flatpak")
        else:
            self._source_badge.set_badge_type(pkg.badge_type)
        self._source_badge.setText(pkg.source_label)

        # Stars
        if pkg.rating > 0:
            filled = round(pkg.rating / 2)
            self._stars_lbl.set_filled(filled)
        else:
            self._stars_lbl.set_filled(-1)

        self._refresh_buttons()

        # Summary & description — evitar mostrar el mismo texto dos veces
        has_desc = bool(pkg.description and pkg.description.strip())

        if not has_desc:
            # Sin descripción extendida: no reservar ni mostrar esa sección.
            self._summary_lbl.setText(pkg.summary)
            self._summary_lbl.setVisible(True)
            self._description_title.setVisible(False)
            self._desc_browser.setVisible(False)
        else:
            # Hay descripción: comprobar si es duplicado del resumen
            summary_is_dup = (
                pkg.summary.strip() == pkg.description.strip() or
                pkg.description.strip().startswith(pkg.summary.strip())
            )
            self._summary_lbl.setText("" if summary_is_dup else pkg.summary)
            self._summary_lbl.setVisible(not summary_is_dup)
            self._description_title.setVisible(True)
            self._set_description_text(pkg.description)
            self._desc_browser.setVisible(True)

        changelog = getattr(pkg, "changelog", "") or ""
        self._changelog_browser.setPlainText(changelog)
        self._changelog_browser.setVisible(bool(getattr(pkg, "changelog", "")))
        self._changelog_title.setVisible(bool(getattr(pkg, "changelog", "")))

        self._prepare_screenshots(pkg)

        # Clear and rebuild metadata rows
        while self._meta_container.count():
            item = self._meta_container.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self._meta_container.addWidget(_meta_row(tr("meta_developer"), pkg.developer))
        self._meta_container.addWidget(_meta_row(tr("meta_version"), pkg.version))
        self._meta_container.addWidget(_meta_row(tr("details_download_size"), pkg.size_str))
        self._meta_container.addWidget(_meta_row(tr("details_disk_size"), pkg.disk_size_str if pkg.is_installed else tr("details_unknown")))
        self._meta_container.addWidget(_meta_row(tr("meta_type"), pkg.source_label))
        if pkg.architecture or pkg.is_32bit:
            arch_value = pkg.architecture or ("i686 / 32-bit" if pkg.is_32bit else "")
            self._meta_container.addWidget(_meta_row(tr("meta_architecture"), arch_value))
        if pkg.repository:
            self._meta_container.addWidget(_meta_row(tr("meta_repository"), pkg.repository))
        self._meta_container.addWidget(_meta_row(tr("meta_category"), tr_category(pkg.category)))
        if pkg.license:
            self._meta_container.addWidget(_meta_row(tr("meta_license"), pkg.license))
        if pkg.website:
            self._meta_container.addWidget(_meta_row(tr("meta_website"), pkg.website))
        if pkg.appstream_id:
            self._meta_container.addWidget(_meta_row(tr("meta_app_id"), pkg.appstream_id))

        dependencies = list(getattr(pkg, "dependencies", []) or [])
        self._dependencies_button.setText(
            tr("details_view_dependencies_count", count=len(dependencies)) if dependencies
            else tr("details_view_dependencies")
        )
        self._dependencies_browser.setPlainText(
            "\n".join(dependencies) if dependencies else tr("details_no_dependencies")
        )
        self._dependencies_browser.setVisible(False)

        self._start_am_meta_lookup(pkg)

    def _toggle_dependencies(self) -> None:
        visible = not self._dependencies_browser.isVisible()
        self._dependencies_browser.setVisible(visible)
        count = len(getattr(self._package, "dependencies", []) or []) if self._package else 0
        self._dependencies_button.setText(
            tr("details_hide_dependencies_count", count=count) if visible and count
            else tr("details_view_dependencies_count", count=count) if count
            else tr("details_hide_dependencies") if visible
            else tr("details_view_dependencies")
        )

    @Slot(int)
    def _on_source_changed(self, index: int) -> None:
        if self._switching_source or index < 0:
            return
        variant = self._source_selector.itemData(index)
        if not isinstance(variant, Package) or self._package is variant:
            return
        self._switching_source = True
        try:
            self.load_package(variant)
        finally:
            self._switching_source = False

    def _prepare_screenshots(self, pkg: Package) -> None:
        for worker in self._screenshot_workers:
            worker.requestInterruption()
        while self._screenshot_strip_layout.count():
            item = self._screenshot_strip_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._screenshot_tiles.clear()
        self._screenshot_package_id = pkg.id

        urls: list[str] = []
        for entry in (pkg.screenshots or [])[:5]:
            if isinstance(entry, dict):
                url = (entry.get("imgDesktopUrl") or entry.get("imgMobileUrl")
                       or entry.get("url") or entry.get("source") or "")
            else:
                url = str(entry or "")
            url = str(url).strip()
            if url.startswith(("https://", "http://")) and url not in urls:
                urls.append(url)

        self._screenshots_panel.setVisible(bool(urls))
        if not urls:
            return

        for url in urls:
            tile = QLabel(tr("details_screenshot_loading"))
            tile.setObjectName("detailScreenshotTile")
            tile.setAlignment(Qt.AlignCenter)
            tile.setFixedSize(220, 144)
            tile.setWordWrap(True)
            tile.setStyleSheet(
                "QLabel#detailScreenshotTile { border: 1px dashed rgba(120,130,145,150); "
                "border-radius: 8px; background: rgba(120,130,145,24); padding: 6px; }"
            )
            self._screenshot_strip_layout.addWidget(tile)
            self._screenshot_tiles[url] = tile

        worker = _ScreenshotWorker(pkg.id, urls, self)
        self._screenshot_workers.append(worker)
        worker.loaded.connect(self._on_screenshot_loaded)
        worker.finished.connect(lambda w=worker: self._retire_screenshot_worker(w))
        worker.finished.connect(worker.deleteLater)
        worker.start()

    @Slot(str, str, bytes)
    def _on_screenshot_loaded(self, pkg_id: str, url: str, data: bytes) -> None:
        if pkg_id != self._screenshot_package_id:
            return
        tile = self._screenshot_tiles.get(url)
        if tile is None:
            return
        pixmap = QPixmap()
        if not data or not pixmap.loadFromData(data):
            tile.hide()
            if not any(not label.isHidden() for label in self._screenshot_tiles.values()):
                self._screenshots_panel.hide()
            return
        target = QSize(tile.width() - 12, tile.height() - 12)
        tile.setText("")
        tile.setStyleSheet("QLabel#detailScreenshotTile { border: 0; background: transparent; }")
        tile.setPixmap(pixmap.scaled(target, Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def _retire_screenshot_worker(self, worker: _ScreenshotWorker) -> None:
        if worker in self._screenshot_workers:
            self._screenshot_workers.remove(worker)

    def shutdown_screenshot_workers(self) -> None:
        workers = list(self._screenshot_workers)
        for worker in workers:
            worker.requestInterruption()
        for worker in workers:
            if worker.isRunning():
                worker.wait(9000)

    # am-FUSION: datos que el catálogo de AM no trae

    def _start_am_meta_lookup(self, pkg: Package) -> None:
        if (pkg.source != PackageSource.APPIMAGE or pkg.repository != "am-FUSION"
                or pkg.id in self._meta_looked_up):
            return
        self._meta_looked_up.add(pkg.id)
        worker = _AMMetaWorker(pkg.id, pkg.pkgname or pkg.name, parent=self)
        self._meta_workers.append(worker)
        worker.resolved.connect(self._on_am_meta)
        worker.finished.connect(lambda w=worker: self._meta_workers.remove(w)
                                if w in self._meta_workers else None)
        worker.start()

    @Slot(str, dict)
    def _on_am_meta(self, pkg_id: str, data: dict) -> None:
        pkg = self._package
        if pkg is None or pkg.id != pkg_id or not data:
            return
        changed = False
        if pkg.size_bytes <= 0 and data.get("size"):
            pkg.size_bytes = int(data["size"])
            changed = True
        if not pkg.version and data.get("version"):
            pkg.version = str(data["version"])
            changed = True
        info = str(data.get("info") or "").strip()
        if info and (not pkg.description.strip() or pkg.description.strip() == pkg.summary.strip()):
            pkg.description = info
            changed = True
        if data.get("website") and not pkg.website:
            pkg.website = str(data["website"])
            changed = True
        if changed:
            self.load_package(pkg)

    # Button state helper

    def _set_description_text(self, description: str) -> None:
        """Rellena _desc_browser con texto plano, aplicando color, tamaño
        de fuente e interlineado con QFont/QTextBlockFormat nativos de Qt
        (sin HTML ni CSS: nada de setHtml() ni atributos style=...)."""
        self._desc_browser.clear()

        font = self._desc_browser.font()
        font.setPointSize(10)
        self._desc_browser.setFont(font)

        cursor = self._desc_browser.textCursor()
        block_fmt = QTextBlockFormat()
        # La firma de setLineHeight (y si el segundo argumento debe ser el
        _line_height_type = QTextBlockFormat.ProportionalHeight
        for args in (
            (160.0, _line_height_type),
            (160.0, getattr(_line_height_type, "value", _line_height_type)),
            (160, _line_height_type),
        ):
            try:
                block_fmt.setLineHeight(*args)
                break
            except TypeError:
                continue
        cursor.setBlockFormat(block_fmt)

        char_fmt = QTextCharFormat()
        char_fmt.setForeground(QColor("#7A8C5E"))
        cursor.setCharFormat(char_fmt)

        cursor.insertText(description or "")
        self._desc_browser.setTextCursor(cursor)
        self._desc_browser.moveCursor(QTextCursor.Start)

    def _refresh_buttons(self):
        """Sync button visibility/text to current package status."""
        if not self._package:
            return
        installed = self._package.is_installed
        has_update = self._package.status == PackageStatus.UPDATE_AVAILABLE
        # AppImage no ofrece versión remota; el usuario selecciona el reemplazo manualmente.
        is_appimage = self._package.source == PackageSource.APPIMAGE
        is_am_fusion = is_appimage and self._package.repository == "am-FUSION"
        # am-FUSION se actualiza solo desde la zona de Actualizaciones.
        show_update = not is_am_fusion and (has_update or (is_appimage and installed))

        self._installed_badge.setVisible(installed)
        self._install_btn.setVisible(not installed)
        self._install_btn.setEnabled(True)
        size_suffix = f" ({self._package.size_str})" if self._package.size_bytes > 0 else ""
        self._install_btn.setText(f"{tr('install')}{size_suffix}")
        self._remove_btn.setVisible(installed)
        self._remove_btn.setEnabled(True)
        self._remove_btn.setText(tr("remove"))
        self._update_btn.setVisible(show_update)
        self._update_btn.setEnabled(True)
        self._update_btn.setText(tr("update"))

    # Slots

    def _on_install(self):
        if not self._package:
            return
        import core.settings as app_settings
        if app_settings.get_bool("confirm_before_install"):
            from PySide6.QtWidgets import QMessageBox
            reply = QMessageBox.question(
                self,
                tr("install"),
                tr("confirm_install_message", name=self._package.name),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if reply != QMessageBox.Yes:
                return
        task = self._tm.submit(self._package, TaskType.INSTALL)
        self._pending[task.id] = TaskType.INSTALL
        self._install_btn.setEnabled(False)
        self._install_btn.setText(tr("installing"))

    def _on_remove(self):
        if not self._package:
            return
        import core.settings as app_settings
        if app_settings.get_bool("confirm_before_remove"):
            from PySide6.QtWidgets import QMessageBox
            reply = QMessageBox.warning(
                self,
                tr("remove"),
                tr("confirm_remove_message", name=self._package.name),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return
        task = self._tm.submit(self._package, TaskType.REMOVE)
        self._pending[task.id] = TaskType.REMOVE
        self._remove_btn.setEnabled(False)
        self._remove_btn.setText(tr("removing"))

    def _on_update(self):
        if not self._package:
            return
        if self._package.source == PackageSource.APPIMAGE:
            if self._package.repository != "am-FUSION":
                self._on_update_appimage()
            return
        task = self._tm.submit(self._package, TaskType.UPDATE)
        self._pending[task.id] = TaskType.UPDATE
        self._update_btn.setEnabled(False)
        self._update_btn.setText(tr("updating"))

    def _on_update_appimage(self):
        """Actualiza un AppImage ya instalado: pide el archivo .AppImage
        nuevo y reemplaza el viejo mediante aimg (no hay servidor de por
        medio, es un reemplazo local del archivo)."""
        from PySide6.QtWidgets import QFileDialog, QMessageBox
        from backend import appimage_backend
        if not appimage_backend.is_available():
            QMessageBox.warning(self, tr("update"), tr("add_appimage_error_no_fpm"))
            return

        path, _ = QFileDialog.getOpenFileName(
            self, tr("update_appimage_pick_file"), "",
            "AppImage (*.AppImage *.appimage)",
        )
        if not path:
            return

        self._package.source_path = path
        task = self._tm.submit(self._package, TaskType.UPDATE)
        self._pending[task.id] = TaskType.UPDATE
        self._update_btn.setEnabled(False)
        self._update_btn.setText(tr("updating"))

    @Slot(str, bool, str)
    def _on_task_finished(self, task_id: str, success: bool, message: str):
        task_type = self._pending.pop(task_id, None)
        if task_type is None or not self._package:
            return

        if success:
            if task_type == TaskType.INSTALL:
                self._package.status = PackageStatus.INSTALLED
            elif task_type == TaskType.REMOVE:
                self._package.status = PackageStatus.NOT_INSTALLED
            elif task_type == TaskType.UPDATE:
                self._package.status = PackageStatus.INSTALLED

        # Refresh button states (re-enable on failure too)
        self._refresh_buttons()

    # Ancho responsivo: mismo criterio que HomePage (80% del ancho de
    # la ventana), para que el detalle de un paquete no se estire de
    # punta a punta en pantallas anchas.

    def _content_width(self) -> int:
        return max(360, int(self.width() * 0.8))

    def _apply_responsive_layout(self):
        margin = max(20, (self.width() - self._content_width()) // 2)
        self._content_layout.setContentsMargins(margin, 28, margin, 32)

    def showEvent(self, event):
        super().showEvent(event)
        self._apply_responsive_layout()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_responsive_layout()
