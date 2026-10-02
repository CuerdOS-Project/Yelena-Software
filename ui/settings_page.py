# Yelena Software — Settings Page

from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import Signal, QTimer, QThread
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QCheckBox, QComboBox, QFrame, QScrollArea, QRadioButton,
    QButtonGroup, QTabWidget, QMessageBox, QSpinBox,
)

import core.settings as app_settings
import styles.theme as app_theme
from .native_widgets import InstallButton
from core.i18n import tr, set_language, SUPPORTED
from backend.system_detect import show_xbps, show_flatpak

# Ruta del PID file del applet (misma convención que applet.py)
_CONFIG_DIR     = Path.home() / ".config" / "yl-soft"
_APPLET_PID_FILE = _CONFIG_DIR / "applet.pid"

# Mismo path que usa main.py para el socket IPC de instancia única.
_IPC_SOCK_PATH = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "yl-soft.sock"


def _section_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("sectionHeader")
    f = lbl.font()
    f.setPointSize(11)
    f.setBold(True)
    lbl.setFont(f)
    return lbl


def _sep() -> QFrame:
    f = QFrame()
    f.setObjectName("separator")
    f.setFrameShape(QFrame.HLine)
    return f


def _read_applet_pid() -> int | None:
    """Lee el PID del applet desde el lock file. Devuelve None si no existe o no es válido."""
    try:
        text = _APPLET_PID_FILE.read_text().strip()
        pid = int(text)
        # Verificar que el proceso realmente existe
        os.kill(pid, 0)
        return pid
    except (FileNotFoundError, ValueError, ProcessLookupError, PermissionError):
        return None


def _restart_applet() -> None:
    """
    Mata el applet si está corriendo y lo relanza.
    - Envía SIGTERM para cierre limpio.
    - Relanza el mismo ejecutable con los mismos args que tenía.
    """
    pid = _read_applet_pid()
    if pid is not None:
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass

    # Localizar applet.py relativo al directorio del settings_page
    _root = Path(__file__).resolve().parent.parent
    applet_script = _root / "applet.py"

    try:
        subprocess.Popen(
            [sys.executable, str(applet_script)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as exc:
        print(f"[settings] Could not restart applet: {exc}")


def _restart_app() -> None:
    """
    Reinicia el proceso principal de Yelena Software preservando todos
    los argumentos de línea de comandos originales.
    """
    _restart_applet()
    # Usar QTimer para dar tiempo al diálogo de cerrarse antes de exec()
    QTimer.singleShot(150, _do_restart)


def _do_restart() -> None:
    """Reemplaza el proceso actual con una nueva instancia."""
    # execv conserva los descriptores abiertos; borrar el socket evita que
    # la nueva instancia crea que ya hay otro proceso activo.
    try:
        _IPC_SOCK_PATH.unlink(missing_ok=True)
    except Exception as exc:
        print(f"[settings] could not remove stale IPC socket: {exc}")
    try:
        os.execv(sys.executable, [sys.executable] + sys.argv)
    except Exception as exc:
        print(f"[settings] execv failed: {exc}")
        # Fallback: lanzar nueva instancia y salir
        try:
            subprocess.Popen(
                [sys.executable] + sys.argv,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except Exception:
            pass
        from PySide6.QtWidgets import QApplication
        QApplication.instance().quit()


class _SecurityInstallWorker(QThread):
    """Instala las políticas de agent-yelena en un hilo aparte para no
    congelar la interfaz mientras pkexec espera la contraseña."""
    finished_ = Signal(bool, str)

    def run(self):
        from backend import agent_yelena
        ok, msg = agent_yelena.install()
        self.finished_.emit(ok, msg)


class _AdminSessionAuthWorker(QThread):
    """Pide autenticación inmediatamente al activar una ventana temporal."""
    finished_ = Signal(bool, str)

    def run(self):
        from backend import agent_yelena
        try:
            agent_yelena.authorize("install", require_tool=True)
            self.finished_.emit(True, "")
        except Exception as exc:
            self.finished_.emit(False, str(exc))


class _RemoteAddWorker(QThread):
    """Ejecuta `flatpak remote-add` en un hilo aparte para no congelar la
    interfaz mientras flatpak espera la contraseña/polkit."""
    finished_ = Signal(str, bool, str)  # (key, ok, message)

    def __init__(self, key: str, parent=None):
        super().__init__(parent)
        self._key = key

    def run(self):
        from backend import flatpak_backend
        ok, msg = flatpak_backend.remote_add(self._key)
        self.finished_.emit(self._key, ok, msg)


class _RemoteActionWorker(QThread):
    """Ejecuta una operación de gestión sobre un remoto Flatpak."""
    finished_ = Signal(str, str, bool, str)  # (key, action, ok, message)

    def __init__(self, key: str, action: str, parent=None):
        super().__init__(parent)
        self._key = key
        self._action = action

    def run(self):
        from backend import flatpak_backend
        if self._action == "remove":
            ok, msg = flatpak_backend.remote_remove(self._key)
        else:
            ok, msg = flatpak_backend.remote_set_default(self._key)
        self.finished_.emit(self._key, self._action, ok, msg)


class SettingsPage(QWidget):
    """Settings / preferences page."""

    language_changed = Signal(str)   # new lang code
    sources_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("settingsPage")

        # Idioma activo al abrir la página (para detectar si cambió)
        self._original_lang = app_settings.get("language", "auto")
        self._original_theme = app_settings.get("theme", "auto")
        self._remote_workers: dict[str, QThread] = {}
        self._remote_action_workers: dict[tuple[str, str], "_RemoteActionWorker"] = {}
        self._flatpak_default_remote = ""
        # La recomendación de reinicio solo se muestra una vez por sesión,
        # aunque el usuario añada varios repositorios Flatpak.
        self._flatpak_restart_notice_shown = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        tabs = QTabWidget()
        tabs.setObjectName("settingsTabs")
        root.addWidget(tabs)

        general_page = QWidget()
        general_scroll = QScrollArea()
        general_scroll.setWidgetResizable(True)
        general_scroll.setFrameShape(QFrame.NoFrame)
        general_content = QWidget()
        general_v = QVBoxLayout(general_content)
        general_v.setContentsMargins(32, 28, 32, 28)
        general_v.setSpacing(16)
        general_scroll.setWidget(general_content)
        general_page_layout = QVBoxLayout(general_page)
        general_page_layout.setContentsMargins(0, 0, 0, 0)
        general_page_layout.addWidget(general_scroll)

        package_page = QWidget()
        package_scroll = QScrollArea()
        package_scroll.setWidgetResizable(True)
        package_scroll.setFrameShape(QFrame.NoFrame)
        package_content = QWidget()
        package_v = QVBoxLayout(package_content)
        package_v.setContentsMargins(32, 28, 32, 28)
        package_v.setSpacing(16)
        package_scroll.setWidget(package_content)
        package_page_layout = QVBoxLayout(package_page)
        package_page_layout.setContentsMargins(0, 0, 0, 0)
        package_page_layout.addWidget(package_scroll)

        security_page = QWidget()
        security_scroll = QScrollArea()
        security_scroll.setWidgetResizable(True)
        security_scroll.setFrameShape(QFrame.NoFrame)
        security_content = QWidget()
        security_v = QVBoxLayout(security_content)
        security_v.setContentsMargins(32, 28, 32, 28)
        security_v.setSpacing(16)
        security_scroll.setWidget(security_content)
        security_page_layout = QVBoxLayout(security_page)
        security_page_layout.setContentsMargins(0, 0, 0, 0)
        security_page_layout.addWidget(security_scroll)

        v = general_v

        # Language
        v.addWidget(_section_label(tr("settings_language")))

        lang_row = QHBoxLayout()
        lang_row.setSpacing(12)
        self._lang_combo = QComboBox()
        self._lang_combo.setObjectName("settingsCombo")
        self._lang_combo.setFixedWidth(200)

        lang_names = {
            "auto": tr("settings_language_auto"),
            "en": "English",
            "es": "Español",
            "ca": "Català",
            "pt": "Português",
            "ja": "日本語",
            "ko": "한국어",
            "tr": "Türkçe",
            "fr": "Français",
            "it": "Italiano",
            "pl": "Polski",
            "ru": "Русский",
            "gl": "Galego",
        }

        self._lang_combo.addItem(lang_names["auto"], "auto")
        for code in SUPPORTED:
            if code in lang_names:
                self._lang_combo.addItem(lang_names[code], code)

        current_lang = app_settings.get("language", "auto")
        idx = self._lang_combo.findData(current_lang)
        if idx >= 0:
            self._lang_combo.setCurrentIndex(idx)

        lang_row.addWidget(self._lang_combo)
        lang_row.addStretch()
        v.addLayout(lang_row)

        v.addWidget(_sep())

        # Tema visual
        v.addWidget(_section_label(tr("settings_theme_section")))

        theme_row = QHBoxLayout()
        theme_row.setSpacing(12)
        self._theme_combo = QComboBox()
        self._theme_combo.setObjectName("settingsCombo")
        self._theme_combo.setFixedWidth(220)
        for key in app_theme.THEME_KEYS:
            self._theme_combo.addItem(tr(app_theme.THEME_LABEL_KEYS[key]), key)
        idx_theme = self._theme_combo.findData(self._original_theme)
        if idx_theme >= 0:
            self._theme_combo.setCurrentIndex(idx_theme)
        theme_row.addWidget(self._theme_combo)
        theme_row.addStretch()
        v.addLayout(theme_row)

        theme_hint = QLabel(tr("settings_theme_hint"))
        theme_hint.setObjectName("sectionSub")
        theme_hint.setWordWrap(True)
        v.addWidget(theme_hint)

        # Package Sources
        v = package_v
        v.addWidget(_section_label(tr("settings_store")))

        # FPM ofrece búsqueda, inventario instalado y consulta de actualizaciones
        # mediante NISSA v2. Las transacciones siguen bajo la autorización polkit
        # de Yelena; una versión antigua deja disponible solo el motor nativo.
        v.addWidget(_section_label(tr("settings_package_engine")))
        self._package_engine_combo = QComboBox()
        self._package_engine_combo.setObjectName("settingsCombo")
        self._package_engine_combo.setFixedWidth(300)
        self._package_engine_combo.addItem(
            tr("settings_package_engine_native"), "yelena-buddies"
        )
        self._package_engine_combo.addItem(
            tr("settings_package_engine_fpm"), "fpm"
        )
        from backend.fpm_backend import supports_nissa_v2
        fpm_installed = supports_nissa_v2()
        fpm_index = self._package_engine_combo.findData("fpm")
        if not fpm_installed:
            self._package_engine_combo.model().item(fpm_index).setEnabled(False)
        saved_engine = app_settings.get("package_engine", "yelena-buddies")
        selected_engine = saved_engine if saved_engine == "fpm" and fpm_installed else "yelena-buddies"
        engine_index = self._package_engine_combo.findData(selected_engine)
        self._package_engine_combo.setCurrentIndex(max(0, engine_index))
        v.addWidget(self._package_engine_combo)
        self._package_engine_hint = QLabel(
            tr("settings_package_engine_hint_fpm")
            if selected_engine == "fpm" else tr("settings_package_engine_hint_native")
        )
        self._package_engine_hint.setObjectName("sectionSub")
        self._package_engine_hint.setWordWrap(True)
        v.addWidget(self._package_engine_hint)
        if not fpm_installed:
            fpm_unavailable = QLabel(tr("settings_package_engine_fpm_missing"))
            fpm_unavailable.setObjectName("sectionSub")
            fpm_unavailable.setWordWrap(True)
            v.addWidget(fpm_unavailable)
        self._package_engine_combo.currentIndexChanged.connect(self._on_package_engine_changed)
        v.addWidget(_sep())

        self._chk_flatpak = QCheckBox(tr("settings_show_flatpak"))
        self._chk_flatpak.setChecked(app_settings.get_bool("show_flatpak"))
        self._chk_flatpak.setVisible(show_flatpak())

        for chk in (self._chk_flatpak,):
            v.addWidget(chk)

        self._chk_dev_pkgs = QCheckBox(tr("settings_show_dev_packages"))
        self._chk_dev_pkgs.setChecked(app_settings.get_bool("show_dev_packages"))
        v.addWidget(self._chk_dev_pkgs)

        v.addWidget(_sep())

        # Flatpak remotes: instalar (registrar) cualquiera de los 3
        # repositorios más comunes. Antes esto era solo una preferencia
        # sin efecto real; ahora cada botón ejecuta `flatpak remote-add`.
        self._flatpak_remote_lbl = _section_label(tr("settings_flatpak_remote"))
        self._flatpak_remote_lbl.setVisible(show_flatpak())
        v.addWidget(self._flatpak_remote_lbl)

        self._remote_rows: dict[str, dict] = {}
        self._remote_widgets_container = QWidget()
        remote_v = QVBoxLayout(self._remote_widgets_container)
        remote_v.setContentsMargins(0, 0, 0, 0)
        remote_v.setSpacing(10)

        from backend import flatpak_backend
        try:
            already_added = flatpak_backend.list_remotes()
            self._flatpak_default_remote = (
                flatpak_backend.get_default_remote()
                or app_settings.get("flatpak_default_remote", "")
                or ""
            )
        except Exception:
            already_added = set()
            self._flatpak_default_remote = app_settings.get("flatpak_default_remote", "") or ""

        self._remote_default_group = QButtonGroup(self)
        self._remote_default_group.setExclusive(True)
        self._remote_default_group.buttonClicked.connect(self._on_default_remote_clicked)

        for key, info in flatpak_backend.KNOWN_REMOTES.items():
            row = QHBoxLayout()
            row.setSpacing(10)

            icon_lbl = QLabel()
            icon_lbl.setFixedSize(36, 36)
            icon_path = Path(__file__).resolve().parent.parent / "resources" / info.get("icon", "")
            if icon_path.exists():
                icon_lbl.setPixmap(QIcon(str(icon_path)).pixmap(32, 32))
            row.addWidget(icon_lbl)

            text_col = QVBoxLayout()
            text_col.setSpacing(2)
            remote_name = tr(info["name_key"])
            name_lbl = QLabel(remote_name)
            f = name_lbl.font()
            f.setBold(True)
            name_lbl.setFont(f)
            text_col.addWidget(name_lbl)
            desc_lbl = QLabel(tr(info["desc_key"]))
            desc_lbl.setObjectName("sectionSub")
            desc_lbl.setWordWrap(True)
            text_col.addWidget(desc_lbl)
            row.addLayout(text_col, stretch=1)

            status_lbl = QLabel()
            status_lbl.setObjectName("sectionSub")
            row.addWidget(status_lbl)

            default_btn = QRadioButton(tr("settings_flatpak_remote_default"))
            default_btn.setEnabled(key in already_added)
            default_btn.setChecked(key in already_added and key == self._flatpak_default_remote)
            default_btn.setProperty("remote_key", key)
            default_btn.setVisible(len(already_added & set(flatpak_backend.KNOWN_REMOTES)) > 1)
            self._remote_default_group.addButton(default_btn)
            row.addWidget(default_btn)

            action_btn = InstallButton(tr("settings_flatpak_remote_add"))
            action_btn.setMinimumWidth(0)
            action_btn.clicked.connect(lambda _checked, k=key: self._on_toggle_remote(k))
            row.addWidget(action_btn)

            remote_v.addLayout(row)

            self._remote_rows[key] = {
                "status_lbl": status_lbl, "btn": action_btn,
                "default_btn": default_btn,
                "label": remote_name,
            }
            self._set_remote_row_state(key, key in already_added)

        self._remote_widgets_container.setVisible(show_flatpak())
        v.addWidget(self._remote_widgets_container)

        self._flatpak_sep = _sep()
        self._flatpak_sep.setVisible(show_flatpak())
        v.addWidget(self._flatpak_sep)

        # Ancla el contenido de la pestaña "Paquetes" arriba: sin este
        # stretch, QVBoxLayout reparte el espacio sobrante entre los
        # widgets y las opciones terminan estiradas verticalmente en vez
        # de quedar compactas en la parte superior (igual que general_v).
        v.addStretch()

        # Estado de actualizaciones (punto de color en la barra lateral + aviso en la página)
        v = general_v
        v.addWidget(_section_label(tr("settings_update_status_section")))

        self._chk_update_status = QCheckBox(tr("settings_show_update_status"))
        self._chk_update_status.setChecked(app_settings.get_bool("show_update_status"))
        v.addWidget(self._chk_update_status)

        hint_lbl = QLabel(tr("settings_show_update_status_hint"))
        hint_lbl.setObjectName("sectionSub")
        hint_lbl.setWordWrap(True)
        v.addWidget(hint_lbl)

        v.addWidget(_sep())

        # Comportamiento general
        v.addWidget(_section_label(tr("settings_behavior_section")))

        self._chk_confirm_remove = QCheckBox(tr("settings_confirm_remove"))
        self._chk_confirm_remove.setChecked(app_settings.get_bool("confirm_before_remove"))
        v.addWidget(self._chk_confirm_remove)

        self._chk_confirm_install = QCheckBox(tr("settings_confirm_install"))
        self._chk_confirm_install.setChecked(app_settings.get_bool("confirm_before_install"))
        v.addWidget(self._chk_confirm_install)

        self._chk_check_startup = QCheckBox(tr("settings_check_updates_startup"))
        self._chk_check_startup.setChecked(app_settings.get_bool("check_updates_on_startup"))
        v.addWidget(self._chk_check_startup)

        v.addWidget(_sep())

        # La sección "Vista de paquetes instalados" se quitó: Instaladas
        # ya no ofrece cuadrícula, solo la lista (ver ui/installed_page.py).

        v.addStretch()

        # Seguridad: instalar/ver estado de las políticas polkit de
        # agent-yelena. Si no están instaladas, la app sigue funcionando
        # con el backend normal (autenticación genérica, sin identificar
        # a agent-yelena) — ver backend/agent_yelena.py.
        v = security_v
        v.addWidget(_section_label(tr("settings_security_section")))

        sec_hint = QLabel(tr("settings_security_hint"))
        sec_hint.setObjectName("sectionSub")
        sec_hint.setWordWrap(True)
        v.addWidget(sec_hint)

        sec_row = QHBoxLayout()
        sec_row.setSpacing(12)
        self._security_status_lbl = QLabel()
        sec_row.addWidget(self._security_status_lbl)
        sec_row.addStretch()
        self._security_install_btn = InstallButton(tr("settings_security_install_btn"))
        self._security_install_btn.clicked.connect(self._on_install_security)
        sec_row.addWidget(self._security_install_btn)
        v.addLayout(sec_row)

        v.addWidget(_sep())
        v.addWidget(_section_label(tr("settings_admin_expiry_section")))
        expiry_row = QHBoxLayout()
        expiry_row.setSpacing(12)
        self._admin_expiry_combo = QComboBox()
        self._admin_expiry_combo.setObjectName("settingsCombo")
        self._admin_expiry_combo.setFixedWidth(240)
        for key, label in (("always", tr("settings_admin_expiry_always")),
                           ("5m", tr("settings_admin_expiry_5m")),
                           ("10m", tr("settings_admin_expiry_10m")),
                           ("30m", tr("settings_admin_expiry_30m")),
                           ("custom", tr("settings_admin_expiry_custom"))):
            self._admin_expiry_combo.addItem(label, key)
        current_expiry = app_settings.get("admin_expiry_mode", "always")
        idx_expiry = self._admin_expiry_combo.findData(current_expiry)
        self._admin_expiry_combo.setCurrentIndex(idx_expiry if idx_expiry >= 0 else 0)
        expiry_row.addWidget(self._admin_expiry_combo)
        self._admin_custom_minutes = QSpinBox()
        self._admin_custom_minutes.setRange(1, 120)
        self._admin_custom_minutes.setSuffix(tr("settings_admin_expiry_minutes_suffix"))
        self._admin_custom_minutes.setValue(int(app_settings.get("admin_expiry_custom_minutes", 5)))
        self._admin_custom_minutes.setVisible(current_expiry == "custom")
        self._admin_previous_mode = current_expiry if current_expiry in ("always", "5m", "10m", "30m", "custom") else "always"
        self._admin_previous_minutes = self._admin_custom_minutes.value()
        expiry_row.addWidget(self._admin_custom_minutes)
        expiry_row.addStretch()
        v.addLayout(expiry_row)
        self._admin_expiry_status = QLabel()
        self._admin_expiry_status.setObjectName("sectionSub")
        self._admin_expiry_status.setWordWrap(True)
        v.addWidget(self._admin_expiry_status)
        self._admin_expiry_timer = QTimer(self)
        self._admin_expiry_timer.setInterval(1000)
        self._admin_expiry_timer.timeout.connect(self._refresh_admin_expiry)
        self._admin_expiry_timer.start()
        self._admin_expiry_combo.currentIndexChanged.connect(self._on_admin_expiry_changed)
        self._admin_custom_minutes.valueChanged.connect(self._on_admin_custom_minutes_changed)
        from backend import admin_session
        admin_session.configure(current_expiry if current_expiry in admin_session.MODES else "always",
                                self._admin_custom_minutes.value())
        self._refresh_admin_expiry()

        v.addStretch()
        self._refresh_security_status()

        tabs.addTab(general_page, tr("settings_tab_general"))
        tabs.addTab(package_page, tr("settings_tab_packages"))
        tabs.addTab(security_page, tr("settings_tab_security"))

        # Guardado automático: no hay botón ni línea divisora inferior.
        for combo in (self._lang_combo, self._theme_combo):
            combo.currentIndexChanged.connect(self._on_auto_save)
        for checkbox in (
            self._chk_flatpak,
            self._chk_dev_pkgs,
            self._chk_update_status,
            self._chk_confirm_remove,
            self._chk_confirm_install,
            self._chk_check_startup,
        ):
            checkbox.stateChanged.connect(self._on_auto_save)

    # Seguridad — agent-yelena

    def _refresh_security_status(self) -> None:
        from backend import agent_yelena
        installed = agent_yelena.is_installed()
        current = getattr(agent_yelena, "policy_action_is_current", lambda: installed)()
        self._security_status_lbl.setText(
            (tr("settings_security_installed") if current else tr("settings_security_update_available")) if installed
            else tr("settings_security_not_installed")
        )
        self._security_install_btn.setEnabled(not current)

    def _on_admin_expiry_changed(self, *_args) -> None:
        custom = self._admin_expiry_combo.currentData() == "custom"
        self._admin_custom_minutes.setVisible(custom)
        if custom:
            QMessageBox.warning(self, tr("settings_admin_expiry_warning_title"),
                                tr("settings_admin_expiry_warning_body"))
        self._activate_admin_session()

    def _on_admin_custom_minutes_changed(self, *_args) -> None:
        if self._admin_expiry_combo.currentData() == "custom":
            self._activate_admin_session()

    def _activate_admin_session(self) -> None:
        from backend import admin_session
        mode = self._admin_expiry_combo.currentData() or "always"
        minutes = self._admin_custom_minutes.value()
        admin_session.configure(mode, minutes)
        if mode == "always":
            self._admin_previous_mode = mode
            self._admin_previous_minutes = minutes
            self._on_auto_save()
            self._refresh_admin_expiry()
            return
        self._admin_expiry_combo.setEnabled(False)
        self._admin_custom_minutes.setEnabled(False)
        self._admin_auth_pending = True
        self._admin_expiry_status.setText(tr("settings_admin_expiry_authenticating"))
        worker = _AdminSessionAuthWorker(self)
        worker.finished_.connect(self._on_admin_session_auth_finished)
        self._admin_session_auth_worker = worker
        worker.start()

    def _on_admin_session_auth_finished(self, ok: bool, message: str) -> None:
        from backend import admin_session
        self._admin_auth_pending = False
        self._admin_expiry_combo.setEnabled(True)
        self._admin_custom_minutes.setEnabled(True)
        if ok:
            admin_session.activate_authenticated()
            self._admin_previous_mode = self._admin_expiry_combo.currentData() or "always"
            self._admin_previous_minutes = self._admin_custom_minutes.value()
            self._on_auto_save()
            self._refresh_admin_expiry()
            return
        old_mode = self._admin_previous_mode
        old_minutes = self._admin_previous_minutes
        idx = self._admin_expiry_combo.findData(old_mode)
        self._admin_expiry_combo.blockSignals(True)
        if idx >= 0:
            self._admin_expiry_combo.setCurrentIndex(idx)
        self._admin_expiry_combo.blockSignals(False)
        self._admin_custom_minutes.blockSignals(True)
        self._admin_custom_minutes.setValue(old_minutes)
        self._admin_custom_minutes.blockSignals(False)
        admin_session.configure(old_mode, old_minutes)
        self._refresh_admin_expiry()
        QMessageBox.warning(self, tr("settings_admin_expiry_auth_error_title"), message)

    def _refresh_admin_expiry(self) -> None:
        from backend import admin_session
        if getattr(self, "_admin_auth_pending", False):
            return
        mode = self._admin_expiry_combo.currentData() or "always"
        remaining = admin_session.remaining_seconds()
        if mode == "always":
            self._admin_expiry_status.setText(tr("settings_admin_expiry_status_always"))
        elif remaining is None:
            self._admin_expiry_status.setText(tr("settings_admin_expiry_status_waiting"))
        else:
            self._admin_expiry_status.setText(tr("settings_admin_expiry_status_remaining", minutes=remaining // 60, seconds=remaining % 60))

    def _on_install_security(self) -> None:
        answer = QMessageBox.question(
            self,
            tr("settings_security_confirm_title"),
            tr("settings_security_confirm_body"),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self._security_install_btn.setEnabled(False)
        self._security_status_lbl.setText(tr("settings_security_installing"))
        worker = _SecurityInstallWorker(self)
        worker.finished_.connect(self._on_security_install_finished)
        self._security_install_worker = worker
        worker.start()

    def _on_security_install_finished(self, ok: bool, message: str) -> None:
        self._refresh_security_status()
        if ok:
            QMessageBox.information(
                self,
                tr("settings_security_restart_title"),
                tr("settings_security_restart_body"),
            )
        else:
            QMessageBox.warning(
                self,
                tr("settings_security_error_title"),
                message,
            )

    # Repositorios Flatpak

    def _set_remote_row_state(self, key: str, added: bool, busy: bool = False) -> None:
        row = self._remote_rows.get(key)
        if row is None:
            return
        status_lbl = row["status_lbl"]
        btn = row["btn"]
        default_btn = row["default_btn"]
        if busy:
            status_lbl.setText(tr("settings_flatpak_remote_adding"))
            btn.setEnabled(False)
            default_btn.setEnabled(False)
        elif added:
            # Un solo estado visible: no repetir "Añadido" en la etiqueta y
            # en el botón, ni añadir checks o iconos de confirmación.
            status_lbl.setText("")
            btn.setEnabled(True)
            btn.setText(tr("settings_flatpak_remote_remove"))
            default_btn.setEnabled(True)
        else:
            status_lbl.setText("")
            btn.setEnabled(True)
            btn.setText(tr("settings_flatpak_remote_add"))
            default_btn.setEnabled(False)
            default_btn.setChecked(False)

    def _on_default_remote_clicked(self, button) -> None:
        key = str(button.property("remote_key") or "")
        if not key:
            return
        # Algunas versiones de Flatpak no soportan remote-modify --default.
        # El predeterminado se aplica al construir el comando de instalación.
        self._flatpak_default_remote = key
        app_settings.set_value("flatpak_default_remote", key)
        app_settings.save()

    def _on_toggle_remote(self, key: str) -> None:
        row = self._remote_rows.get(key)
        if row is None:
            return
        from backend import flatpak_backend
        if key in flatpak_backend.list_remotes():
            self._on_remove_remote(key)
        else:
            self._on_add_remote(key)

    def _on_remove_remote(self, key: str) -> None:
        row = self._remote_rows.get(key)
        if row is None or (key, "remove") in self._remote_action_workers:
            return
        answer = QMessageBox.question(
            self,
            tr("settings_flatpak_remote_remove_title"),
            tr("settings_flatpak_remote_remove_body", name=row["label"]),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self._set_remote_row_state(key, added=True, busy=True)
        worker = _RemoteActionWorker(key, "remove", parent=self)
        worker.finished_.connect(self._on_remote_action_finished)
        self._remote_action_workers[(key, "remove")] = worker
        worker.start()

    def _refresh_default_visibility(self) -> None:
        from backend import flatpak_backend
        available = len(flatpak_backend.list_remotes() & set(flatpak_backend.KNOWN_REMOTES))
        visible = available > 1
        for row in self._remote_rows.values():
            row["default_btn"].setVisible(visible)
            if not visible:
                row["default_btn"].setChecked(False)

    def _on_add_remote(self, key: str) -> None:
        if key in self._remote_workers:
            return  # ya en curso
        self._set_remote_row_state(key, added=False, busy=True)

        worker = _RemoteAddWorker(key, parent=self)
        worker.finished_.connect(self._on_remote_add_finished)
        self._remote_workers[key] = worker
        worker.start()

    def _on_remote_action_finished(self, key: str, action: str, ok: bool, message: str) -> None:
        self._remote_action_workers.pop((key, action), None)
        if ok and action == "remove":
            self._set_remote_row_state(key, added=False)
            self._refresh_default_visibility()
            if self._flatpak_default_remote == key:
                self._flatpak_default_remote = ""
                app_settings.set_value("flatpak_default_remote", "")
                app_settings.save()
            return
        if action == "remove":
            self._set_remote_row_state(key, added=True)
        for candidate in self._remote_default_group.buttons():
            candidate.setEnabled(True)
        QMessageBox.warning(
            self,
            tr("settings_flatpak_remote_error_title"),
            tr("settings_flatpak_remote_error_body", error=message),
        )

    def _on_remote_add_finished(self, key: str, ok: bool, message: str) -> None:
        self._remote_workers.pop(key, None)
        if ok:
            self._set_remote_row_state(key, added=True)
            self._refresh_default_visibility()
            if not self._flatpak_restart_notice_shown:
                self._flatpak_restart_notice_shown = True
                QMessageBox.information(
                    self,
                    tr("settings_flatpak_restart_title"),
                    tr("settings_flatpak_restart_body"),
                )
        else:
            self._set_remote_row_state(key, added=False)
            self._refresh_default_visibility()
            QMessageBox.warning(
                self,
                tr("settings_flatpak_remote_error_title"),
                tr("settings_flatpak_remote_error_body", error=message),
            )

    def _on_auto_save(self, *_args) -> None:
        self._on_save()

    def _on_package_engine_changed(self, *_args) -> None:
        if getattr(self, "_package_engine_hint", None) is not None:
            key = ("settings_package_engine_hint_fpm"
                   if self._package_engine_combo.currentData() == "fpm"
                   else "settings_package_engine_hint_native")
            self._package_engine_hint.setText(tr(key))
        self._on_save()

    def _on_save(self):
        new_lang = self._lang_combo.currentData()
        lang_changed = (new_lang != self._original_lang)

        new_theme = self._theme_combo.currentData()
        theme_changed = (new_theme != self._original_theme)

        # Guardar todo
        app_settings.set_value("language", new_lang)
        app_settings.set_value("theme", new_theme)
        app_settings.set_bool("show_flatpak", self._chk_flatpak.isChecked())
        app_settings.set_bool("show_dev_packages", self._chk_dev_pkgs.isChecked())
        app_settings.set_bool("show_update_status", self._chk_update_status.isChecked())
        app_settings.set_bool("confirm_before_remove", self._chk_confirm_remove.isChecked())
        app_settings.set_bool("confirm_before_install", self._chk_confirm_install.isChecked())
        app_settings.set_bool("check_updates_on_startup", self._chk_check_startup.isChecked())
        app_settings.set_value(
            "package_engine", self._package_engine_combo.currentData() or "yelena-buddies"
        )
        app_settings.set_value("admin_expiry_mode", self._admin_expiry_combo.currentData() or "always")
        app_settings.set_value("admin_expiry_custom_minutes", self._admin_custom_minutes.value())
        # InstalledPage persiste la vista; XBPS se muestra si está disponible.
        app_settings.set_bool("show_xbps", show_xbps())
        app_settings.save()

        try:
            from core import search_cache
            search_cache.invalidate("xbps:fpm")
            search_cache.invalidate("xbps:yelena-buddies")
        except Exception:
            pass

        # Emitir señal de fuentes aunque no haya reinicio
        self.sources_changed.emit()

        if lang_changed or theme_changed:
            self._ask_restart(new_lang, new_theme, theme_changed)
        else:
            # Sin cambios de idioma/tema: aplicar y guardar inmediatamente.
            set_language(new_lang)
            self.language_changed.emit(new_lang)

    def _ask_restart(self, new_lang: str, new_theme: str, theme_changed: bool) -> None:
        """
        Muestra un diálogo preguntando si reiniciar ahora (idioma y/o tema
        cambiaron). Si acepta: reinicia app + applet. Si cancela: aplica lo
        que se pueda en caliente (idioma siempre; tema solo si no requiere
        recargar QStyle) y muestra "Guardado".
        """
        dlg = QMessageBox(self)
        dlg.setWindowTitle(tr("settings_restart_title"))
        dlg.setText(tr("settings_restart_body"))
        dlg.setIcon(QMessageBox.Question)

        btn_now = dlg.addButton(tr("settings_restart_btn"), QMessageBox.AcceptRole)
        dlg.addButton(tr("settings_later_btn"), QMessageBox.RejectRole)
        dlg.setDefaultButton(btn_now)

        dlg.exec()
        clicked = dlg.clickedButton()

        if clicked is btn_now:
            # Reiniciar app completa (y applet)
            _restart_app()
        else:
            # Aplicar en caliente lo mejor posible y notificar
            set_language(new_lang)
            self._original_lang = new_lang
            if theme_changed:
                from PySide6.QtWidgets import QApplication
                app = QApplication.instance()
                if app is not None:
                    app_theme.apply_theme(app, new_theme)
                self._original_theme = new_theme
            self.language_changed.emit(new_lang)
            # Reiniciar el applet de todas formas para que recoja el nuevo idioma
            _restart_applet()
