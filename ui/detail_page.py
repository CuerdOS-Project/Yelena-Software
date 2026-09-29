# Vera_Shop — App Detail Page

from __future__ import annotations

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QColor, QTextCursor, QTextBlockFormat, QTextCharFormat
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QScrollArea, QFrame, QSizePolicy, QTextBrowser,
)

from core.i18n import tr
from backend.models import Package, PackageSource, PackageStatus, TaskType
from backend.task_manager import TaskManager
from .icon_widget import AppIconWidget
from .native_widgets import BadgeLabel, InstallButton, RemoveButton, UpdateButton, BackButton


def _pkg_fallback_icon(pkg: Package) -> str:
    """Ícono de respaldo por categoría, codificado como tag
    "_category_icon:<nombre>" por los backends (ver xbps_backend.py /
    flatpak_backend.py). Antes esta página lo ignoraba por completo."""
    for tag in pkg.tags:
        if tag.startswith("_category_icon:"):
            return tag[len("_category_icon:"):]
    return ""


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
        # Maps task_id -> TaskType so we know what completed
        self._pending: dict[str, TaskType] = {}

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

        self._name_lbl = QLabel("App Name")
        self._name_lbl.setObjectName("detailName")
        name_col.addWidget(self._name_lbl)

        badge_row = QHBoxLayout()
        badge_row.setSpacing(8)
        self._source_badge = BadgeLabel("", "flatpak", show_icon=False)
        badge_row.addWidget(self._source_badge)

        self._installed_badge = BadgeLabel(tr("badge_installed"), "installed", show_icon=False)
        self._installed_badge.setVisible(False)
        badge_row.addWidget(self._installed_badge)

        self._version_lbl = QLabel("")
        self._version_lbl.setObjectName("detailVersion")
        badge_row.addWidget(self._version_lbl)
        badge_row.addStretch()
        name_col.addLayout(badge_row)

        self._stars_lbl = QLabel("")
        self._stars_lbl.setObjectName("stars")
        name_col.addWidget(self._stars_lbl)

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

        # Description (rich text)
        self._desc_browser = QTextBrowser()
        self._desc_browser.setObjectName("detailDesc")
        self._desc_browser.setFrameShape(QFrame.NoFrame)
        self._desc_browser.viewport().setAutoFillBackground(False)
        self._desc_browser.setMinimumHeight(80)
        self._desc_browser.setMaximumHeight(200)
        self._desc_browser.setOpenExternalLinks(True)
        self._content_layout.addWidget(self._desc_browser)

        self._content_layout.addWidget(_make_sep())

        # Metadata grid
        meta_lbl = QLabel(tr("details_section"))
        meta_lbl.setObjectName("sectionHeader")
        f = meta_lbl.font(); f.setPointSize(14); meta_lbl.setFont(f)
        self._content_layout.addWidget(meta_lbl)

        self._meta_container = QVBoxLayout()
        self._meta_container.setSpacing(2)
        self._content_layout.addLayout(self._meta_container)

        self._content_layout.addStretch()

        # Connect task completion to update button states
        self._tm.task_completed.connect(self._on_task_finished)

    # Public

    def load_package(self, pkg: Package):
        self._package = pkg

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

        if pkg.source == PackageSource.FLATPAK:
            self._source_badge.set_badge_type("flatpak")
        else:
            self._source_badge.set_badge_type(pkg.badge_type)
        self._source_badge.setText(pkg.source_label)

        # Stars
        if pkg.rating > 0:
            filled = round(pkg.rating / 2)
            # ★ / ☆ son caracteres Unicode estándar (no emoji)
            stars_html = "★" * filled + "☆" * (5 - filled)
            self._stars_lbl.setText(stars_html)  # Use Unicode stars
        else:
            self._stars_lbl.setText("")

        self._refresh_buttons()

        # Summary & description — evitar mostrar el mismo texto dos veces
        has_desc = bool(pkg.description and pkg.description.strip())

        if not has_desc:
            # Sin descripción separada: mostrar sólo el resumen en el label
            self._summary_lbl.setText(pkg.summary)
            self._summary_lbl.setVisible(True)
            self._desc_browser.setVisible(False)
        else:
            # Hay descripción: comprobar si es duplicado del resumen
            summary_is_dup = (
                pkg.summary.strip() == pkg.description.strip() or
                pkg.description.strip().startswith(pkg.summary.strip())
            )
            self._summary_lbl.setText("" if summary_is_dup else pkg.summary)
            self._summary_lbl.setVisible(not summary_is_dup)
            self._set_description_text(pkg.description)
            self._desc_browser.setVisible(True)

        # Clear and rebuild metadata rows
        while self._meta_container.count():
            item = self._meta_container.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self._meta_container.addWidget(_meta_row(tr("meta_developer"), pkg.developer))
        self._meta_container.addWidget(_meta_row(tr("meta_version"), pkg.version))
        size_label = tr("meta_disk_size") if pkg.is_installed else tr("meta_size")
        self._meta_container.addWidget(_meta_row(size_label, pkg.size_str))
        self._meta_container.addWidget(_meta_row(tr("meta_type"), pkg.source_label))
        self._meta_container.addWidget(_meta_row(tr("meta_category"), pkg.category))
        if pkg.license:
            self._meta_container.addWidget(_meta_row(tr("meta_license"), pkg.license))
        if pkg.website:
            self._meta_container.addWidget(_meta_row(tr("meta_website"), pkg.website))
        if pkg.appstream_id:
            self._meta_container.addWidget(_meta_row(tr("meta_app_id"), pkg.appstream_id))

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
        show_update = has_update or (is_appimage and installed)

        self._installed_badge.setVisible(installed)
        self._install_btn.setVisible(not installed)
        self._install_btn.setEnabled(True)
        self._install_btn.setText(tr("install"))
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
            self._on_update_appimage()
            return
        task = self._tm.submit(self._package, TaskType.UPDATE)
        self._pending[task.id] = TaskType.UPDATE
        self._update_btn.setEnabled(False)
        self._update_btn.setText(tr("updating"))

    def _on_update_appimage(self):
        """Actualiza un AppImage ya instalado: pide el archivo .AppImage
        nuevo y reemplaza el viejo con FPM (no hay servidor de por
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
