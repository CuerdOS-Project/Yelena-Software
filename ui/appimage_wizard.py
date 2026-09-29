# yl-soft — Asistente (wizard) para instalar AppImages

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import Qt, QSize, QThread, Signal
from PySide6.QtGui import QPixmap, QIcon
from PySide6.QtWidgets import (
    QWidget, QWizard, QWizardPage, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QLineEdit, QComboBox, QPushButton, QFileDialog,
    QDialog, QGridLayout, QScrollArea, QToolButton, QFrame, QMessageBox,
)

from core.i18n import tr

CATEGORIES = [
    "AudioVideo", "Audio", "Video", "Development", "Education", "Game",
    "Graphics", "Network", "Office", "Science", "Settings", "System", "Utility",
]

# Contextos de íconos (según el spec de temas freedesktop.org) que
# interesan para elegir un ícono de app: "apps" trae los íconos reales de
# los programas instalados (firefox, gimp, blender, etc.), "categories"
# trae los genéricos por rubro.
_ICON_CONTEXTS = ("apps", "categories")
_ICON_EXTS = (".png", ".svg", ".xpm")


def _icon_theme_search_dirs() -> list[Path]:
    """Directorios donde viven los packs de íconos (temas), en el orden
    en que los busca el propio Qt/freedesktop."""
    dirs: list[Path] = []
    try:
        for p in QIcon.themeSearchPaths():
            path = Path(p)
            if path not in dirs:
                dirs.append(path)
    except Exception:
        pass
    for p in ("/usr/share/icons", "/usr/local/share/icons",
              str(Path.home() / ".local/share/icons"), str(Path.home() / ".icons")):
        path = Path(p)
        if path not in dirs:
            dirs.append(path)
    return [d for d in dirs if d.is_dir()]


def _theme_inherit_chain(theme_name: str, search_dirs: list[Path],
                          seen: set | None = None) -> list[str]:
    """Sigue "Inherits=" en index.theme para incluir los temas padre
    (casi todos los temas heredan de "hicolor")."""
    if seen is None:
        seen = set()
    if not theme_name or theme_name in seen:
        return []
    seen.add(theme_name)
    chain = [theme_name]
    for d in search_dirs:
        idx = d / theme_name / "index.theme"
        if not idx.is_file():
            continue
        try:
            text = idx.read_text(errors="ignore")
        except OSError:
            continue
        m = re.search(r"^Inherits=(.+)$", text, re.MULTILINE)
        if m:
            for parent in m.group(1).split(","):
                parent = parent.strip()
                if parent:
                    chain += _theme_inherit_chain(parent, search_dirs, seen)
        break
    return chain


@lru_cache(maxsize=1)
def _scan_system_icon_names() -> tuple[str, ...]:
    """Escanea el pack de íconos que usa el sistema (tema activo + los
    temas de los que hereda, p.ej. hicolor) y devuelve TODOS los nombres
    de ícono disponibles en los contextos "apps"/"categories". Es lo que
    alimenta la galería, no una lista fija a mano."""
    dirs = _icon_theme_search_dirs()
    theme_name = QIcon.themeName() or ""
    chain = _theme_inherit_chain(theme_name, dirs) if theme_name else []
    if "hicolor" not in chain:
        chain.append("hicolor")

    names: set[str] = set()
    for theme in chain:
        for base in dirs:
            theme_dir = base / theme
            if not theme_dir.is_dir():
                continue
            for context in _ICON_CONTEXTS:
                for entry in theme_dir.glob(f"*/{context}"):
                    try:
                        for f in entry.iterdir():
                            if f.suffix.lower() in _ICON_EXTS:
                                names.add(f.stem)
                    except OSError:
                        continue
                # variante "scalable/apps" sin subcarpeta de tamaño intermedia
                for entry in theme_dir.glob(f"{context}"):
                    try:
                        for f in entry.iterdir():
                            if f.suffix.lower() in _ICON_EXTS:
                                names.add(f.stem)
                    except OSError:
                        continue

    # /usr/share/pixmaps: íconos sueltos que muchos paquetes instalan ahí
    # además (o en vez) del tema de íconos.
    pixmaps = Path("/usr/share/pixmaps")
    if pixmaps.is_dir():
        try:
            for f in pixmaps.iterdir():
                if f.suffix.lower() in _ICON_EXTS:
                    names.add(f.stem)
        except OSError:
            pass

    return tuple(sorted(names, key=str.lower))


def _format_size(n: int) -> str:
    if n <= 0:
        return "—"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
    return f"{n:.1f} GB"


def _guess_name(path: str) -> str:
    base = os.path.splitext(os.path.basename(path))[0]
    return base.replace("_", " ").replace("-", " ").strip().title()


# Página 1 — elegir el archivo .AppImage

class _FilePage(QWizardPage):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle(tr("wiz_file_title"))
        self.setSubTitle(tr("wiz_file_subtitle"))

        self.path = ""

        lay = QVBoxLayout(self)
        lay.setSpacing(12)

        row = QHBoxLayout()
        self._path_lbl = QLabel(tr("wiz_no_file"))
        self._path_lbl.setWordWrap(True)
        row.addWidget(self._path_lbl, 1)

        browse_btn = QPushButton(tr("wiz_browse"))
        browse_btn.clicked.connect(self._browse)
        row.addWidget(browse_btn)
        lay.addLayout(row)

        self._size_lbl = QLabel("")
        self._size_lbl.setObjectName("sectionSub")
        lay.addWidget(self._size_lbl)
        lay.addStretch()

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, tr("wiz_browse"), "", "AppImage (*.AppImage *.appimage)"
        )
        if not path:
            return
        self.set_path(path)

    def set_path(self, path: str):
        """Fija el archivo .AppImage seleccionado (usado tanto por el
        botón Examinar como al arrastrar-y-soltar un archivo)."""
        self.path = path
        self._path_lbl.setText(path)
        try:
            size = os.path.getsize(path)
            self._size_lbl.setText(tr("wiz_detected_size", size=_format_size(size)))
        except OSError:
            self._size_lbl.setText("")
        self.completeChanged.emit()

    def isComplete(self) -> bool:
        return bool(self.path) and os.path.isfile(self.path)

    def validatePage(self) -> bool:
        wiz = self.wizard()
        if isinstance(wiz, AppImageInstallWizard):
            wiz.data["path"] = self.path
            if not wiz.data.get("name"):
                wiz.data["name"] = _guess_name(self.path)
        return True


# Página 2 — nombre y descripción

class _DetailsPage(QWizardPage):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle(tr("wiz_details_title"))
        self.setSubTitle(tr("wiz_details_subtitle"))

        form = QFormLayout(self)

        self._name = QLineEdit()
        self._name.textChanged.connect(self.completeChanged)
        form.addRow(tr("add_appimage_name"), self._name)

        self._desc = QLineEdit()
        form.addRow(tr("wiz_description"), self._desc)

    def initializePage(self):
        wiz = self.wizard()
        if isinstance(wiz, AppImageInstallWizard) and not self._name.text():
            self._name.setText(wiz.data.get("name", ""))

    def isComplete(self) -> bool:
        return bool(self._name.text().strip())

    def validatePage(self) -> bool:
        wiz = self.wizard()
        if isinstance(wiz, AppImageInstallWizard):
            wiz.data["name"] = self._name.text().strip()
            wiz.data["description"] = self._desc.text().strip()
        return True


# Hilo: extrae en segundo plano el ícono embebido del AppImage, sin
# congelar la UI (la extracción invoca el propio binario con
# --appimage-extract y puede tardar según el tamaño del archivo).

class _IconExtractThread(QThread):
    done = Signal(str)

    def __init__(self, path: str, parent=None):
        super().__init__(parent)
        self._path = path

    def run(self):
        try:
            from backend.appimage_icon import extract_icon
            icon = extract_icon(self._path) or ""
        except Exception:
            icon = ""
        self.done.emit(icon)


class _IconScanThread(QThread):
    done = Signal(tuple)

    def run(self):
        try:
            names = _scan_system_icon_names()
        except Exception:
            names = ()
        self.done.emit(names)


# Galería de íconos: TODOS los íconos disponibles en el pack (tema) que
# usa el sistema (apps + categorías, siguiendo la herencia del tema
# hasta hicolor), con un mínimo curado como respaldo si el escaneo no
# encuentra nada (p.ej. sistema sin tema de íconos instalado).

_FALLBACK_ICONS = [
    "application-x-executable", "package-x-generic",
    "applications-games", "applications-graphics", "applications-internet",
    "applications-multimedia", "applications-office", "applications-development",
    "applications-science", "applications-system", "applications-utilities",
    "applications-education", "applications-accessories",
    "audio-x-generic", "video-x-generic", "image-x-generic", "text-x-generic",
    "preferences-system", "system-software-install", "utilities-terminal",
    "web-browser", "accessories-text-editor", "multimedia-player",
    "input-gaming", "folder", "drive-harddisk",
]

# Cuántos botones de ícono se dibujan como máximo a la vez. El pack de
# íconos del sistema puede tener miles de nombres; renderizarlos todos
# de una sentaría la UI, así que se cargan de a tandas y el buscador de
# arriba filtra sobre la lista COMPLETA (no solo sobre lo ya dibujado).
_GALLERY_RENDER_LIMIT = 240


class IconGalleryDialog(QDialog):
    """Selector de íconos: navega TODO el pack de íconos del tema activo
    del sistema (no una lista fija), con búsqueda por nombre."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("wiz_icon_gallery"))
        self.resize(460, 420)
        self.selected_icon: str = ""
        self._all_names: list[str] = []
        self._scan_thread: _IconScanThread | None = None

        lay = QVBoxLayout(self)
        lay.setSpacing(10)

        self._filter = QLineEdit()
        self._filter.setPlaceholderText(tr("search_placeholder"))
        self._filter.setEnabled(False)
        self._filter.textChanged.connect(self._apply_filter)
        lay.addWidget(self._filter)

        self._status_lbl = QLabel(tr("wiz_icon_gallery_loading"))
        self._status_lbl.setObjectName("sectionSub")
        lay.addWidget(self._status_lbl)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        self._container = QWidget()
        self._grid = QGridLayout(self._container)
        self._grid.setSpacing(10)
        scroll.setWidget(self._container)
        lay.addWidget(scroll, 1)

        # Escanea el pack de íconos en segundo plano (puede tardar la
        # primera vez; se cachea después) para no congelar el diálogo.
        self._scan_thread = _IconScanThread(self)
        self._scan_thread.done.connect(self._on_scanned)
        self._scan_thread.start()

    def _on_scanned(self, names: tuple):
        self._all_names = list(names) or list(_FALLBACK_ICONS)
        self._filter.setEnabled(True)
        self._render(self._all_names)

    def _apply_filter(self, text: str):
        text = text.strip().lower()
        if not text:
            self._render(self._all_names)
            return
        matches = [n for n in self._all_names if text in n.lower()]
        self._render(matches)

    def _render(self, names: list[str]):
        # Limpia la grilla actual
        while self._grid.count():
            item = self._grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        shown = names[:_GALLERY_RENDER_LIMIT]
        cols = 6
        row = col = 0
        drawn = 0
        for name in shown:
            icon = QIcon.fromTheme(name)
            if icon.isNull():
                continue
            btn = QToolButton()
            btn.setIcon(icon)
            btn.setIconSize(QSize(40, 40))
            btn.setToolTip(name)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _checked=False, n=name: self._pick(n))
            self._grid.addWidget(btn, row, col)
            drawn += 1
            col += 1
            if col >= cols:
                col = 0
                row += 1

        total = len(names)
        if total == 0:
            self._status_lbl.setText(tr("wiz_icon_gallery_empty"))
        elif total > len(shown):
            self._status_lbl.setText(
                tr("wiz_icon_gallery_count_truncated", shown=drawn, total=total)
            )
        else:
            self._status_lbl.setText(tr("wiz_icon_gallery_count", total=total))

    def _pick(self, name: str):
        self.selected_icon = name
        self.accept()

    def closeEvent(self, event):
        if self._scan_thread is not None and self._scan_thread.isRunning():
            self._scan_thread.wait(50)
        super().closeEvent(event)


# Página 3 — categoría e ícono

class _CategoryIconPage(QWizardPage):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle(tr("wiz_category_title"))
        self.setSubTitle(tr("wiz_category_subtitle"))

        self._auto_tried = False
        self._icon_thread: _IconExtractThread | None = None

        form = QFormLayout(self)

        self._category = QComboBox()
        self._category.addItems(CATEGORIES)
        form.addRow(tr("add_appimage_category"), self._category)

        icon_row = QHBoxLayout()
        self._icon = QLineEdit("application-x-executable")
        self._icon.textChanged.connect(lambda t: self._update_preview(t.strip()))
        icon_row.addWidget(self._icon, 1)
        icon_browse = QPushButton(tr("wiz_browse"))
        icon_browse.clicked.connect(self._browse_icon)
        icon_row.addWidget(icon_browse)
        icon_gallery = QPushButton(tr("wiz_icon_gallery"))
        icon_gallery.clicked.connect(self._open_gallery)
        icon_row.addWidget(icon_gallery)
        form.addRow(tr("wiz_icon"), icon_row)

        self._auto_lbl = QLabel("")
        self._auto_lbl.setObjectName("sectionSub")
        self._auto_lbl.setVisible(False)
        form.addRow("", self._auto_lbl)

        self._preview = QLabel()
        self._preview.setFixedSize(48, 48)
        self._preview.setAlignment(Qt.AlignCenter)
        form.addRow("", self._preview)

    def initializePage(self):
        wiz = self.wizard()
        if not isinstance(wiz, AppImageInstallWizard):
            return
        path = wiz.data.get("path", "")
        self._update_preview(self._icon.text().strip() or "application-x-executable")

        # Intento automático: solo una vez por asistente y solo si el
        # usuario no eligió ya un ícono manualmente.
        if (path and not self._auto_tried
                and self._icon.text().strip() in ("", "application-x-executable")):
            self._auto_tried = True
            self._auto_lbl.setText(tr("wiz_icon_detecting"))
            self._auto_lbl.setVisible(True)
            self._icon_thread = _IconExtractThread(path, self)
            self._icon_thread.done.connect(self._on_icon_detected)
            self._icon_thread.start()

    def _on_icon_detected(self, icon_path: str):
        self._auto_lbl.setVisible(False)
        if icon_path:
            self._icon.setText(icon_path)
        else:
            self._auto_lbl.setText(tr("wiz_icon_not_found"))
            self._auto_lbl.setVisible(True)

    def _browse_icon(self):
        path, _ = QFileDialog.getOpenFileName(
            self, tr("wiz_icon"), "", "Images (*.png *.svg *.xpm *.ico)"
        )
        if path:
            self._icon.setText(path)

    def _open_gallery(self):
        dlg = IconGalleryDialog(self)
        if dlg.exec() == QDialog.Accepted and dlg.selected_icon:
            self._icon.setText(dlg.selected_icon)

    def _update_preview(self, value: str):
        pix = QPixmap(value) if value else QPixmap()
        if pix.isNull() and value:
            icon = QIcon.fromTheme(value)
            if not icon.isNull():
                pix = icon.pixmap(48, 48)
        if not pix.isNull():
            self._preview.setPixmap(
                pix.scaled(48, 48, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )
        else:
            self._preview.clear()

    def validatePage(self) -> bool:
        wiz = self.wizard()
        if isinstance(wiz, AppImageInstallWizard):
            wiz.data["category"] = self._category.currentText()
            wiz.data["icon"] = self._icon.text().strip() or "application-x-executable"
        return True


# Página 4 — resumen y confirmación

class _SummaryPage(QWizardPage):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle(tr("wiz_summary_title"))
        self.setSubTitle(tr("wiz_summary_subtitle"))

        self._lbl = QLabel()
        self._lbl.setWordWrap(True)
        self._lbl.setTextFormat(Qt.RichText)

        lay = QVBoxLayout(self)
        lay.addWidget(self._lbl)
        lay.addStretch()

    def initializePage(self):
        wiz = self.wizard()
        if not isinstance(wiz, AppImageInstallWizard):
            return
        d = wiz.data
        self._lbl.setText(
            f"<b>{tr('add_appimage_name')}:</b> {d.get('name','')}<br>"
            f"<b>{tr('wiz_description')}:</b> {d.get('description','') or '—'}<br>"
            f"<b>{tr('add_appimage_category')}:</b> {d.get('category','')}<br>"
            f"<b>{tr('wiz_file_title')}:</b> {d.get('path','')}"
        )


class AppImageInstallWizard(QWizard):
    """Asistente de 4 pasos para instalar un AppImage vía FPM."""

    def __init__(self, parent=None, initial_path: str = ""):
        super().__init__(parent)
        self.setWindowTitle(tr("add_appimage_title"))
        self.setWizardStyle(QWizard.ModernStyle)
        self.setOption(QWizard.NoBackButtonOnStartPage, True)
        self.resize(480, 360)

        # Datos recolectados a través de las páginas
        self.data: dict = {"path": "", "name": "", "description": "",
                            "category": "Utility", "icon": "application-x-executable"}

        self._file_page = _FilePage(self)
        self.addPage(self._file_page)
        self.addPage(_DetailsPage(self))
        self.addPage(_CategoryIconPage(self))
        self.addPage(_SummaryPage(self))

        self.setButtonText(QWizard.FinishButton, tr("wiz_install_btn"))

        if initial_path:
            # Viene de arrastrar-y-soltar un .AppImage: precargamos el
            # archivo para que el usuario no tenga que buscarlo de nuevo.
            self._file_page.set_path(initial_path)


def run_appimage_install_wizard(parent, task_manager, initial_path: str = "") -> bool:
    """Abre el asistente de instalación de AppImage y, si el usuario lo
    confirma, encola la tarea de instalación en `task_manager`. Se usa
    tanto desde el botón "Agregar AppImage" como al soltar un archivo
    .AppImage sobre la ventana (arrastrar-y-soltar).

    Devuelve True si se encoló una instalación."""
    from backend import appimage_backend
    if not appimage_backend.is_available():
        QMessageBox.warning(parent, tr("add_appimage_title"), tr("add_appimage_error_no_fpm"))
        return False

    wizard = AppImageInstallWizard(parent, initial_path=initial_path)
    if wizard.exec() != AppImageInstallWizard.Accepted:
        return False

    d = wizard.data
    if not d.get("path") or not d.get("name"):
        return False

    from backend.models import Package, PackageSource, PackageStatus, TaskType
    pkg = Package(
        id=f"appimage:{d['name']}",
        name=d["name"],
        summary=d.get("category", "Utility"),
        description=d.get("description", ""),
        source=PackageSource.APPIMAGE,
        status=PackageStatus.NOT_INSTALLED,
        icon_name=d.get("icon", "application-x-executable"),
        category=d.get("category", "Utility"),
        appstream_id=d["name"],
        source_path=d["path"],
    )
    task_manager.submit(pkg, TaskType.INSTALL)
    return True
