#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# notifica al usuario y abre Yelena Software (zona updates) al hacer clic.

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import threading
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional

# Resolve project root
_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT))

# PySide6
from PySide6.QtCore import QObject, QTimer, Signal, Slot
from PySide6.QtGui import QAction, QIcon, QPalette
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QHBoxLayout,
    QLabel, QMenu, QSystemTrayIcon, QVBoxLayout,
)

# Backend imports
# Reusa los mismos backends que la página de actualizaciones de la tienda.
try:
    from backend.updates_db import updates_db as _updates_db
    from backend import xbps_backend as _xbps_be
    from backend.flatpak_backend import is_flatpak_available, get_flatpak_updates
    from backend.system_detect import detect_package_manager
    from backend.update_activity import (
        begin_system_update, end_system_update,
        is_system_update_active as _is_system_update_active,
    )
    from core.settings import load_config, save_config
    _BACKENDS_OK = True
except ImportError as _err:
    print(f"[applet] Backend import error: {_err}", file=sys.stderr)
    _BACKENDS_OK = False
    _updates_db = None
    _xbps_be = None

    def detect_package_manager():
        return "unknown"

    def is_flatpak_available():
        return False

    def get_flatpak_updates():
        return []

    def begin_system_update():
        return None

    def end_system_update(owner_pid=None):
        return False

    def _is_system_update_active():
        return False

    def load_config():
        return {}

    def save_config(cfg):
        pass

# i18n (usa el sistema de la tienda)
try:
    from core.i18n import tr, set_language, detect_system_language
    _HAS_I18N = True
except ImportError:
    _HAS_I18N = False

    def detect_system_language() -> str:  # type: ignore[misc]
        """Fallback: detects system language from env vars."""
        import locale as _locale
        for env_var in ("LANGUAGE", "LANG", "LC_ALL", "LC_MESSAGES"):
            val = os.environ.get(env_var, "")
            if val:
                code = val.split(".")[0].split("_")[0].lower()
                if code in ("es", "en", "ca", "pt", "ja", "ko", "tr", "fr", "it", "pl", "ru"):
                    return code
        try:
            loc = _locale.getlocale()[0]
            if loc:
                code = loc.split("_")[0].lower()
                if code in ("es", "en", "ca", "pt", "ja", "ko", "tr", "fr", "it", "pl", "ru"):
                    return code
        except Exception:
            pass
        return "en"

    def tr(key: str, *args, **kwargs) -> str:  # type: ignore[misc]
        _fallback = {
            "nav_updates":               "Updates",
            "applet_open":               "Open Yelena Software",
            "applet_check_now":          "Check for updates",
            "applet_quick_update":       "Quick update",
            "applet_settings":           "Interval settings",
            "applet_quit":               "Quit applet",
            "applet_updating_title":     "Updating…",
            "applet_updating_body":      "An update is in progress.",
            "updates_available":         "{0} updates available",
            "status_system_updated":     "System is up to date",
            "updates_found_notif_title": "Updates available",
            "updates_found_notif_body":  "{0} packages can be updated",
            "no_updates_notif_title":    "System is up to date",
            "error_applet_notif_title":  "Applet error",
            "error_applet_notif_body":   "Could not check for updates.",
            "config_dialog_title":       "Check interval",
            "config_dialog_description": "Check for updates every:",
            "config_dialog_save":        "Save",
            "config_dialog_cancel":      "Cancel",
            "config_dialog_saved_title": "Saved",
            "config_dialog_saved_body":  "Interval set to {0} seconds.",
            "interval_1h":  "Every hour",
            "interval_2h":  "Every 2 hours",
            "interval_4h":  "Every 4 hours",
            "interval_6h":  "Every 6 hours",
            "interval_12h": "Every 12 hours",
            "interval_24h": "Every 24 hours",
            "main_app_not_found": "yelena-software not found in PATH.",
            "applet_no_tray_title": "Yelena Software is running",
            "applet_no_tray_body": "No tray icon is available in this desktop session. You'll get notifications for updates instead.",
            "applet_no_tray_body_gnome": "GNOME needs an extension to show tray icons. You'll get notifications for updates in the meantime.",
            "applet_no_tray_install_ext": "Install extension",
            "applet_tooltip_checking":    "Checking for updates…",
            "applet_tooltip_updates":     "{count} updates available · Click to view",
        }
        text = _fallback.get(key, key)
        if kwargs:
            try:
                text = text.format(**kwargs)
            except Exception:
                pass
        elif args:
            try:
                text = text.format(*args)
            except Exception:
                pass
        return text

    def set_language(lang: str) -> None:  # type: ignore[misc]
        pass

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("yl-soft-applet")

# Paths
_CONFIG_DIR      = Path.home() / ".config" / "yl-soft"
_APPLET_CFG_FILE = _CONFIG_DIR / "applet_config.json"
_APPLET_PID_FILE = _CONFIG_DIR / "applet.pid"

_BACKGROUND_FLAG     = "--background-only"
_BG_INTERVAL_SECS    = 12 * 60 * 60   # 12 h en modo background
_MAX_STARTUP_CHECKS  = 3

# Cómo se abre la tienda (en modo updates).
# Se intenta en orden: ejecutable del sistema, luego python fallback.
_STORE_EXECS = ["yl-soft"]
_STORE_SCRIPT = str(_ROOT / "main.py")

# Socket IPC: mismo path que usa main.py para instancia única.
_IPC_SOCK_PATH = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "yl-soft.sock"


def _ipc_send(command: str) -> bool:
    """
    Envía un comando al proceso principal de la tienda via socket Unix.
    Devuelve True si la tienda estaba corriendo y recibió el comando.
    """
    import socket as _sock_mod
    try:
        s = _sock_mod.socket(_sock_mod.AF_UNIX, _sock_mod.SOCK_STREAM)
        s.settimeout(1.5)
        s.connect(str(_IPC_SOCK_PATH))
        s.sendall((command + "\n").encode())
        s.close()
        return True
    except Exception:
        return False


# Helpers

def is_applet_updating() -> bool:
    return _is_system_update_active()


def _write_pid_file() -> None:
    """Escribe el PID del proceso actual en el lock file."""
    try:
        _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        _APPLET_PID_FILE.write_text(str(os.getpid()))
    except Exception as exc:
        logger.warning(f"Could not write PID file: {exc}")


def _remove_pid_file() -> None:
    """Elimina el PID file al salir."""
    try:
        _APPLET_PID_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def _check_single_instance() -> bool:
    """
    Comprueba si ya hay una instancia del applet corriendo.
    Si la hay, devuelve True (debe salir). Si no, registra este PID.

    Usa el PID file como lock: lee el PID guardado y comprueba con
    os.kill(pid, 0) si el proceso sigue vivo. Si el proceso no existe,
    el PID file es huérfano y se sobreescribe.
    """
    try:
        text = _APPLET_PID_FILE.read_text().strip()
        existing_pid = int(text)
        os.kill(existing_pid, 0)   # lanza ProcessLookupError si no existe
        if existing_pid != os.getpid():
            logger.info(f"Applet already running (PID {existing_pid}), exiting.")
            return True            # ya hay una instancia viva → salir
    except (FileNotFoundError, ValueError):
        pass                       # no hay PID file → primera instancia
    except ProcessLookupError:
        logger.info("Stale PID file found, overwriting.")
    except PermissionError:
        logger.warning("PID file check: PermissionError, assuming another instance exists.")
        return True

    _write_pid_file()
    return False


@lru_cache(maxsize=1)
def _get_desktop_environment() -> str:
    """Mismo criterio que main.py: XDG_CURRENT_DESKTOP > DESKTOP_SESSION > XDG_SESSION_DESKTOP."""
    for var in ("XDG_CURRENT_DESKTOP", "DESKTOP_SESSION", "XDG_SESSION_DESKTOP"):
        val = os.environ.get(var, "").lower().strip()
        if val:
            return val.split(":")[0].strip()
    return ""


@lru_cache(maxsize=1)
def _has_status_notifier_watcher() -> bool:
    """
    Comprueba si hay un StatusNotifierWatcher vivo en el bus de sesión.
    GNOME "vanilla" no registra ninguno (QSystemTrayIcon.isSystemTrayAvailable()
    puede devolver True igualmente vía fallback xembed, pero el icono nunca
    llegaría a mostrarse); KDE, XFCE con indicador, LXQt, etc. sí lo registran.
    No añade dependencias nuevas: usa dbus-send, presente en cualquier sesión D-Bus.
    """
    try:
        result = subprocess.run(
            ["dbus-send", "--session", "--dest=org.freedesktop.DBus",
             "--type=method_call", "--print-reply",
             "/org/freedesktop/DBus", "org.freedesktop.DBus.NameHasOwner",
             "string:org.kde.StatusNotifierWatcher"],
            capture_output=True, text=True, timeout=1.5,
        )
        return "boolean true" in result.stdout.lower()
    except Exception:
        return False


@lru_cache(maxsize=1)
def _tray_actually_usable() -> bool:
    """
    Combina isSystemTrayAvailable() con la comprobación real de SNI.
    En GNOME sin la extensión "AppIndicator and KStatusNotifierItem
    Support", no hay watcher: se debe caer a notificaciones interactivas.
    """
    de = _get_desktop_environment()
    if not QSystemTrayIcon.isSystemTrayAvailable():
        return False
    if "gnome" in de and not _has_status_notifier_watcher():
        return False
    return True


# Tray icon helper: assets SVG propios, sin resolver nombres del tema del SO.
_APPLET_ICON_DIR = _ROOT / "resources" / "icons" / "applet"
_ICON_STATES = ("idle", "checking", "pending", "error", "updating")
_ICON_VARIANTS = ("light", "dark")
_ACTIVE_ICON_VARIANT = "light"
_STATE_ICONS: Dict[tuple[str, str], QIcon] = {}
_MENU_ICONS: Dict[tuple[str, str], QIcon] = {}
_MENU_ICON_NAMES = ("settings", "exit")


def _notification_icon() -> str:
    """Icono de Yelena Software a usar en las notificaciones (D-Bus y
    notify-send), en vez de un icono genérico del tema del sistema."""
    root = Path(__file__).parent
    for candidate in (
        root / "resources" / "icons" / "yl-soft.svg",
        Path("/usr/share/pixmaps/yl-soft.svg"),
        Path("/usr/share/icons/hicolor/scalable/apps/yl-soft.svg"),
    ):
        if candidate.exists() and candidate.is_file():
            return str(candidate)
    return str(_APPLET_ICON_DIR / _ACTIVE_ICON_VARIANT / "idle.svg")


def _detect_icon_variant() -> str:
    """Elige SVG claros/oscuros según el color de fondo de la paleta Qt."""
    try:
        background = QApplication.palette().color(QPalette.ColorRole.Window)
        return "dark" if background.lightnessF() < 0.5 else "light"
    except Exception:
        return "light"


def _build_state_icons() -> None:
    """Carga los cinco estados y acciones propios desde SVG locales."""
    global _ACTIVE_ICON_VARIANT
    _ACTIVE_ICON_VARIANT = _detect_icon_variant()
    _STATE_ICONS.clear()
    _MENU_ICONS.clear()
    for variant in _ICON_VARIANTS:
        for state in _ICON_STATES:
            path = _APPLET_ICON_DIR / variant / f"{state}.svg"
            _STATE_ICONS[(state, variant)] = QIcon(str(path)) if path.is_file() else QIcon()
        for name in _MENU_ICON_NAMES:
            path = _APPLET_ICON_DIR / variant / f"{name}.svg"
            _MENU_ICONS[(name, variant)] = QIcon(str(path)) if path.is_file() else QIcon()


def _state_icon(state: str) -> QIcon:
    return _STATE_ICONS.get((state, _ACTIVE_ICON_VARIANT), QIcon())


def _menu_icon(name: str) -> QIcon:
    if name in _ICON_STATES:
        return _state_icon(name)
    return _MENU_ICONS.get((name, _ACTIVE_ICON_VARIANT), _state_icon("idle"))


def _get_tray_icon(has_updates: bool) -> QIcon:
    return _state_icon("pending" if has_updates else "idle")


def _set_tray_icon_state(tray: "QSystemTrayIcon", state: str, tooltip: str) -> None:
    tray.setIcon(_state_icon(state if state in _ICON_STATES else "idle"))
    tray.setToolTip(tooltip)


# Update fetcher

def _fetch_updates(pkg_mgr: str, errors: Optional[List[str]] = None) -> List[Dict]:
    """Obtiene actualizaciones y registra fallos parciales de cada fuente."""
    updates: List[Dict] = []
    try:
        if pkg_mgr == "xbps" and _xbps_be:
            from engine.package_engine import get_updates_backend
            updates.extend(get_updates_backend().get_updates())
    except Exception as exc:
        logger.error(f"fetch {pkg_mgr}: {exc}")
        if errors is not None:
            errors.append(str(exc))
    try:
        if is_flatpak_available():
            updates.extend(get_flatpak_updates())
    except Exception as exc:
        logger.error(f"fetch flatpak: {exc}")
        if errors is not None:
            errors.append(str(exc))
    try:
        from backend import appimage_backend
        appimage = appimage_backend.get_updates()
        if appimage:
            updates.extend(appimage)
    except Exception as exc:
        logger.error(f"fetch appimage: {exc}")
        if errors is not None:
            errors.append(str(exc))
    return updates


# Config dialog

class _ConfigDialog(QDialog):
    _INTERVALS = [
        (3600,  "interval_1h"),
        (7200,  "interval_2h"),
        (14400, "interval_4h"),
        (21600, "interval_6h"),
        (43200, "interval_12h"),
        (86400, "interval_24h"),
    ]

    def __init__(self, current_interval: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("config_dialog_title"))
        self.setMinimumWidth(340)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(18, 18, 18, 14)

        layout.addWidget(QLabel(tr("config_dialog_description")))

        self._combo = QComboBox()
        for secs, key in self._INTERVALS:
            self._combo.addItem(tr(key), secs)
        idx = next(
            (i for i, (s, _) in enumerate(self._INTERVALS) if s == current_interval),
            2,
        )
        self._combo.setCurrentIndex(idx)
        layout.addWidget(self._combo)

        btns = QHBoxLayout()
        btns.addStretch()
        cancel_btn = _flat_button(tr("config_dialog_cancel"))
        save_btn   = _flat_button(tr("config_dialog_save"))
        save_btn.setDefault(True)
        cancel_btn.clicked.connect(self.reject)
        save_btn.clicked.connect(self.accept)
        btns.addWidget(cancel_btn)
        btns.addWidget(save_btn)
        layout.addLayout(btns)

    def selected_interval(self) -> int:
        return self._combo.currentData() or 14400


def _flat_button(label: str):
    from PySide6.QtWidgets import QPushButton
    btn = QPushButton(label)
    return btn


# Notificaciones interactivas (modo sin bandeja)
#
# Cuando no hay StatusNotifierWatcher (típicamente GNOME sin la extensión
# AppIndicator) el applet no puede anclar un icono, pero sigue pudiendo
# avisar al usuario con notificaciones de escritorio que incluyan botones
# de acción ("Abrir", "Actualizar ahora"). Esto se hace por D-Bus directo
# (org.freedesktop.Notifications) porque notify-send no permite capturar
# qué botón se pulsó; PySide6.QtDBus ya viene con PySide6 así que no se
# añade ninguna dependencia nueva.

try:
    from PySide6.QtDBus import QDBusConnection, QDBusInterface, QDBusMessage
    _HAS_QTDBUS = True
except ImportError:
    _HAS_QTDBUS = False


class _InteractiveNotifier(QObject):
    """Envía notificaciones con botones de acción vía D-Bus y despacha
    callbacks cuando el usuario pulsa uno de ellos."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._callbacks: Dict[int, Dict[str, callable]] = {}
        self._ready = False
        if not _HAS_QTDBUS:
            return
        try:
            self._bus = QDBusConnection.sessionBus()
            self._iface = QDBusInterface(
                "org.freedesktop.Notifications",
                "/org/freedesktop/Notifications",
                "org.freedesktop.Notifications",
                self._bus,
            )
            self._ready = self._iface.isValid()
            if self._ready:
                self._bus.connect(
                    "org.freedesktop.Notifications",
                    "/org/freedesktop/Notifications",
                    "org.freedesktop.Notifications",
                    "ActionInvoked",
                    self._on_action_invoked,
                )
        except Exception as exc:
            logger.warning(f"InteractiveNotifier init failed: {exc}")
            self._ready = False

    @property
    def available(self) -> bool:
        return self._ready

    def notify(self, title: str, body: str, icon: str,
               actions: Optional[Dict[str, tuple]] = None) -> None:
        """
        actions: {action_id: (label, callback)}. Si es None o falla el envío
        con acciones, degrada a notificación simple sin botones.
        """
        if not self._ready:
            self._fallback_notify_send(title, body, icon)
            return
        try:
            dbus_actions: List[str] = []
            cbs: Dict[str, callable] = {}
            for action_id, (label, cb) in (actions or {}).items():
                dbus_actions.extend([action_id, label])
                cbs[action_id] = cb

            reply = self._iface.call(
                "Notify",
                "Yelena Software",   # app_name
                0,                    # replaces_id
                icon,                 # app_icon
                title, body,
                dbus_actions,         # actions
                {"urgency": 1},       # hints
                8000,                 # expire_timeout ms
            )
            if reply.type() == QDBusMessage.MessageType.ErrorMessage:
                raise RuntimeError(reply.errorMessage())
            notif_id = reply.arguments()[0] if reply.arguments() else 0
            if cbs:
                self._callbacks[int(notif_id)] = cbs
        except Exception as exc:
            logger.warning(f"Interactive notify failed, fallback: {exc}")
            self._fallback_notify_send(title, body, icon)

    def _on_action_invoked(self, notif_id: int, action_id: str) -> None:
        cbs = self._callbacks.pop(int(notif_id), None)
        if cbs and action_id in cbs:
            try:
                cbs[action_id]()
            except Exception as exc:
                logger.error(f"action callback: {exc}")

    @staticmethod
    def _fallback_notify_send(title: str, body: str, icon: str) -> None:
        try:
            subprocess.run(
                ["notify-send", "-i", icon, "-a", "Yelena Software",
                 "-t", "8000", title, body],
                timeout=3, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass


# Qt signals bridge

class _Signals(QObject):
    update_icon       = Signal(int)    # count
    notify            = Signal(str, str)  # title, body
    check_finished    = Signal(bool, int, bool) # should_notify, count, has_error


# Applet

class YelenaApplet:
    """Applet de bandeja para Yelena Software."""

    def __init__(self, app: QApplication, background_mode: bool = False):
        self.app             = app
        self.background_mode = background_mode
        self._timers: List[QTimer] = []

        # Idioma — el applet es el primer proceso en arrancar, así que resuelve
        # "auto" y persiste el idioma detectado en settings.ini para que
        # main.py y cualquier otro proceso lean directamente el valor concreto.
        try:
            cfg  = load_config()
            lang = cfg.get("language", "auto") or "auto"
            if lang in ("auto", ""):
                detected = detect_system_language()
                set_language(detected)
                logger.info(f"Applet: idioma auto detectado → {detected}")
            else:
                set_language(lang)
                logger.info(f"Applet: idioma desde config → {lang}")
        except Exception as exc:
            logger.error(f"language init: {exc}")

        self._pkg_mgr = detect_package_manager() if _BACKENDS_OK else "unknown"
        logger.info(f"Package manager: {self._pkg_mgr}")

        self._updates:      List[Dict] = []
        self._count         = 0
        self._prev_count    = 0
        self._notified      = 0
        self._startup_checks = 0
        self._check_running = False
        self._check_thread: Optional[threading.Thread] = None
        self._recheck_scheduled = False
        self._emitter_refresh_pending = False
        self._shutting_down = False
        self._system_updating = False
        self._check_error = False
        self._update_status_notified = False
        self._suppress_check_notifications = False
        self._menu_icon_actions: List[tuple[QAction, str]] = []

        self._signals = _Signals()
        self._signals.update_icon.connect(self._slot_update_icon)
        self._signals.notify.connect(self._slot_notify)
        self._signals.check_finished.connect(self._slot_check_done)

        # Pre-cargar cache
        if _updates_db is not None:
            try:
                cached, count = _updates_db.load_pending()
                if count > 0:
                    self._updates    = cached
                    self._count      = count
                    self._prev_count = count
                    logger.info(f"Loaded {count} updates from cache")
            except Exception as exc:
                logger.error(f"pre-load cache: {exc}")

        # Intervalo
        cfg_data             = self._load_config()
        self._interval       = cfg_data.get("update_interval_seconds", 14400)
        if background_mode:
            self._interval = _BG_INTERVAL_SECS

        # Bandeja
        self._tray: Optional[QSystemTrayIcon] = None
        self._menu: Optional[QMenu]           = None
        self._status_action: Optional[QAction] = None
        self._notifier = _InteractiveNotifier()
        self._trayless_mode = False
        if not background_mode:
            _build_state_icons()   # requiere QApplication activa
            if _tray_actually_usable():
                self._setup_tray()
            else:
                self._trayless_mode = True
                de = _get_desktop_environment() or "desconocido"
                logger.warning(
                    f"No hay StatusNotifierWatcher activo (entorno: {de}). "
                    "El applet operará sin icono de bandeja, avisando por "
                    "notificaciones con acciones."
                )
                self._notify_no_tray_once()

        # Monitor de idioma: sincroniza con la GUI si el usuario cambia el idioma
        self._current_lang_key = self._read_lang_key()
        _lang_timer = QTimer()
        _lang_timer.setInterval(5000)   # cada 5 s
        _lang_timer.timeout.connect(self._sync_language)
        _lang_timer.start()
        self._timers.append(_lang_timer)

        # Sigue los cambios de tema del escritorio y alterna los SVG propios.
        if not background_mode:
            self._icon_theme_timer = QTimer()
            self._icon_theme_timer.setInterval(3000)
            self._icon_theme_timer.timeout.connect(self._sync_icon_theme)
            self._icon_theme_timer.start()
            self._timers.append(self._icon_theme_timer)

        # Timer principal de comprobación (periódico — nunca bloqueado por startup limit)
        self._check_timer = QTimer()
        self._check_timer.setInterval(self._interval * 1000)
        self._check_timer.timeout.connect(lambda: self._do_check(periodic=True))
        self._check_timer.start()
        self._timers.append(self._check_timer)

        # Vigila la transacción que puede estar ejecutando la tienda en otro
        # proceso. Mientras exista el marcador, anima el icono y silencia los
        # avisos periódicos para dejar solo el mensaje de estado.
        self._update_activity_timer = QTimer()
        self._update_activity_timer.setInterval(350)
        self._update_activity_timer.timeout.connect(self._poll_update_activity)
        self._update_activity_timer.start()
        self._timers.append(self._update_activity_timer)

        # Monitor de cambios en cache (solo en modo normal)
        if not background_mode:
            t = QTimer()
            t.setInterval(2000)
            t.timeout.connect(self._monitor_cache)
            t.start()
            self._timers.append(t)
            if self._count > 0 and self._tray:
                self._slot_update_icon(self._count)

        # Comprobación inicial
        logger.info("Initial check queued")
        self._poll_update_activity()
        self._do_check()

    # Language sync

    def _read_lang_key(self) -> str:
        """
        Lee la clave de idioma desde la configuración COMPARTIDA de la tienda
        (app_settings → settings.ini), no desde el JSON local del applet.
        Así el applet se sincroniza cuando el usuario cambia el idioma en la UI.
        """
        try:
            # load_config() está importado desde app_settings → settings.ini
            cfg = load_config()
            return cfg.get("language", "auto") or "auto"
        except Exception:
            return "auto"

    def _sync_language(self) -> None:
        """
        Comprueba si el idioma configurado en la GUI ha cambiado.
        Si es así, actualiza i18n y refresca menú y tooltip del applet.
        Esto mantiene el applet sincronizado con la preferencia del usuario
        sin necesidad de reiniciarlo.
        """
        if self._shutting_down:
            return
        new_key = self._read_lang_key()
        if new_key == self._current_lang_key:
            return
        self._current_lang_key = new_key
        try:
            if new_key in ("auto", ""):
                detected = detect_system_language()
                set_language(detected)
                logger.info(f"Language sync: auto → {detected}")
            else:
                set_language(new_key)
                logger.info(f"Language sync: {new_key}")
        except Exception as exc:
            logger.error(f"sync_language: {exc}")
            return
        # Refrescar textos del menú y tooltip
        if self._menu:
            self._rebuild_menu()
        if self._tray and not self._check_running:
            tip = self._tray_status_text()
            state = "error" if self._check_error else "pending" if self._count > 0 else "idle"
            _set_tray_icon_state(self._tray, state, tip)
        self._refresh_tray_status()

    def _sync_icon_theme(self) -> None:
        """Aplica la familia SVG clara/oscura si cambió la paleta del sistema."""
        if self._shutting_down:
            return
        variant = _detect_icon_variant()
        if variant == _ACTIVE_ICON_VARIANT:
            return
        _build_state_icons()
        self._refresh_tray_status()
        for action, icon_name in self._menu_icon_actions:
            action.setIcon(_menu_icon(icon_name))

    def _notify_no_tray_once(self) -> None:
        if is_applet_updating():
            return
        de = _get_desktop_environment()
        body = tr("applet_no_tray_body")
        actions = {"open": (tr("applet_open"), self._open_store)}
        if "gnome" in de:
            body = tr("applet_no_tray_body_gnome")
            actions["install-ext"] = (
                tr("applet_no_tray_install_ext"),
                self._open_gnome_extension_page,
            )
        self._notifier.notify(
            tr("applet_no_tray_title"), body, _notification_icon(), actions,
        )

    @staticmethod
    def _open_gnome_extension_page() -> None:
        try:
            subprocess.Popen(
                ["xdg-open",
                 "https://extensions.gnome.org/extension/615/"
                 "appindicator-support/"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except Exception as exc:
            logger.warning(f"xdg-open extension page: {exc}")

    # Tray setup

    def _setup_tray(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            logger.warning("System tray not available")
            return
        self._tray = QSystemTrayIcon(self.app)
        self._tray.activated.connect(self._on_tray_activated)
        self._menu = QMenu()
        self._rebuild_menu()
        self._tray.setContextMenu(self._menu)
        self._refresh_tray_status()
        self._tray.show()

    def _rebuild_menu(self) -> None:
        if not self._menu:
            return
        self._menu.clear()

        self._status_action = QAction(self._tray_status_text(), self._menu)
        self._status_action.setEnabled(False)
        self._status_action.setIcon(_menu_icon(self._current_tray_state()))
        self._menu.addAction(self._status_action)
        self._menu.addSeparator()
        self._menu_icon_actions = []

        def _add(label: str, icon_name: str, cb):
            act = QAction(_menu_icon(icon_name), label, self._menu)
            act.triggered.connect(cb)
            self._menu.addAction(act)
            self._menu_icon_actions.append((act, icon_name))

        _add(tr("applet_open"), "idle", self._open_store)
        _add(tr("applet_check_now"), "checking", lambda: self._do_check(manual=True))
        _add(tr("applet_quick_update"), "pending", self._quick_update)
        self._menu.addSeparator()
        _add(tr("applet_settings"), "settings", self._open_settings)
        self._menu.addSeparator()
        _add(tr("applet_quit"), "exit", self._quit)

    def _current_tray_state(self) -> str:
        if self._system_updating:
            return "updating"
        if self._check_running:
            return "checking"
        if self._check_error:
            return "error"
        return "pending" if self._count > 0 else "idle"

    def _tray_status_text(self) -> str:
        if self._system_updating:
            status = tr("applet_updating_title")
        elif self._check_running:
            status = tr("applet_tooltip_checking")
        elif self._check_error:
            status = tr("status_error_title")
        elif self._count > 0:
            status = tr("updates_available").format(self._count)
        else:
            status = tr("status_system_updated")
        return f"{tr('settings_update_status_section')}: {status}"

    def _refresh_tray_status(self) -> None:
        text = self._tray_status_text()
        if self._status_action:
            self._status_action.setText(text)
            self._status_action.setIcon(_menu_icon(self._current_tray_state()))
        if self._tray:
            _set_tray_icon_state(self._tray, self._current_tray_state(), text)

    def _on_tray_activated(self, reason) -> None:
        # El trigger de QSystemTrayIcon corresponde al clic izquierdo; el
        # clic derecho queda reservado al QMenu nativo/contextual.
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._open_store()

    # Slots

    @Slot(int)
    def _slot_update_icon(self, count: int) -> None:
        if self.background_mode or self._tray is None:
            return
        if self._system_updating or is_applet_updating():
            return
        tip = self._tray_status_text()
        state = "error" if self._check_error else "pending" if count > 0 else "idle"
        _set_tray_icon_state(self._tray, state, tip)
        self._refresh_tray_status()

    def _poll_update_activity(self) -> None:
        """Anima el tray y emite una sola notificación por transacción."""
        active = is_applet_updating()
        if active and not self._system_updating:
            self._system_updating = True
            self._suppress_check_notifications = True
            if hasattr(self, "_check_timer"):
                self._check_timer.stop()
            self._refresh_tray_status()
            logger.info("System update started")
            if not self._update_status_notified:
                self._signals.notify.emit(
                    tr("applet_updating_title"),
                    tr("applet_updating_body"),
                )
        elif not active and self._system_updating:
            self._system_updating = False
            self._update_status_notified = False
            logger.info("System update finished")
            self._slot_update_icon(self._count)
            # La consulta de antes de la transacción puede haber quedado
            # obsoleta; se realiza una sola comprobación limpia al terminar.
            QTimer.singleShot(0, self._schedule_recheck)

        # El estado «updating» se representa con su SVG propio.

    @Slot(str, str)
    def _slot_notify(self, title: str, body: str) -> None:
        if self._system_updating or is_applet_updating():
            # Una única notificación de estado; cualquier aviso de búsqueda,
            # error o actualización disponible se descarta hasta que termine.
            if title != tr("applet_updating_title") or self._update_status_notified:
                logger.debug(f"Notification suppressed during system update: {title}")
                return
            self._update_status_notified = True
        # En background_mode y en modo sin bandeja (GNOME sin extensión),
        # la notificación de escritorio es el único punto de contacto con
        # el usuario, así que se le da acción directa cuando aplica.
        icon = _notification_icon()
        actions = None
        if not self.background_mode and self._count > 0:
            actions = {"open": (tr("applet_open"), self._open_store)}
            if self._trayless_mode:
                actions["update"] = (
                    tr("applet_quick_update"), self._quick_update,
                )
        if self._notifier.available:
            self._notifier.notify(title, body, icon, actions)
        elif self._tray:
            self._tray.showMessage(
                title, body, QSystemTrayIcon.MessageIcon.Information, 5000
            )
        else:
            _InteractiveNotifier._fallback_notify_send(title, body, icon)

    @Slot(bool, int, bool)
    def _slot_check_done(self, should_notify: bool, count: int, has_error: bool) -> None:
        self._check_error = has_error
        if not has_error:
            self._count = count
        # Actualizar icono de bandeja al estado final
        if self._tray and not (self._system_updating or is_applet_updating()):
            tip = self._tray_status_text()
            state = "error" if has_error else "pending" if count > 0 else "idle"
            _set_tray_icon_state(self._tray, state, tip)
        self._refresh_tray_status()

        notifications_blocked = (
            self._system_updating
            or is_applet_updating()
            or self._suppress_check_notifications
        )
        if should_notify and count > 0 and not notifications_blocked:
            self._signals.notify.emit(
                tr("updates_found_notif_title"),
                tr("updates_found_notif_body").format(count),
            )
            self._notified = count
        elif should_notify and count == 0 and not notifications_blocked:
            self._signals.notify.emit(
                tr("no_updates_notif_title"),
                tr("status_system_updated"),
            )

        if not has_error:
            self._prev_count = count
            self._count = count
            self._emitter_refresh_pending = False
            # La base de datos ya se guardó en _do_check antes de emitirse
            # check_finished; la tienda compara el contenido, así que avisar
            # en cada comprobación correcta es barato y nunca queda desfasada.
            self._signal_store()
        if not self.background_mode and not has_error:
            self._signals.update_icon.emit(count)

    # Cache monitor

    def _monitor_cache(self) -> None:
        if self._shutting_down or _updates_db is None:
            return
        try:
            _, count = _updates_db.load_pending()
            if count != self._prev_count:
                cached, _ = _updates_db.load_pending()
                self._updates    = cached
                self._count      = count
                self._prev_count = count
                self._signals.update_icon.emit(count)
        except Exception as exc:
            logger.error(f"monitor_cache: {exc}")

    # Check logic

    def _do_check(self, manual: bool = False, periodic: bool = False) -> None:
        """
        Lanza una comprobación de actualizaciones en un hilo de fondo.

        - manual=True : iniciado por el usuario (sin límite, siempre notifica)
        - periodic=True: iniciado por el timer periódico (sin límite de startup)
        - ambos False  : comprobación automática de arranque (máx. _MAX_STARTUP_CHECKS)
        """
        if self._system_updating or is_applet_updating():
            self._suppress_check_notifications = True
            return
        # Los checks de arranque se limitan para no saturar el sistema al inicio.
        # Los checks periódicos (timer) y manuales NUNCA se bloquean por este límite.
        if not manual and not periodic and not self.background_mode:
            if self._startup_checks >= _MAX_STARTUP_CHECKS:
                return
        if self._check_running:
            return
        self._check_running = True
        # Solo los checks de arranque consumen el contador
        if not manual and not periodic:
            self._startup_checks += 1
        logger.info(
            f"{'Manual' if manual else 'Periodic' if periodic else 'Auto'} "
            f"check #{self._startup_checks}"
        )
        # Mostrar icono de "buscando" (solo si hay bandeja real)
        if self._tray:
            _set_tray_icon_state(self._tray, "checking",
                                 tr("applet_tooltip_checking"))
        self._refresh_tray_status()

        def _task():
            try:
                if not _BACKENDS_OK:
                    self._signals.check_finished.emit(False, self._count, True)
                    return

                fetch_errors: List[str] = []
                all_updates = _fetch_updates(self._pkg_mgr, fetch_errors)
                if fetch_errors:
                    logger.warning("Some update sources failed: %s", "; ".join(fetch_errors))
                    # No reemplazar la caché con un resultado incompleto: si
                    # un repositorio falla, conservamos la última lista fiable.
                    self._signals.check_finished.emit(False, self._count, True)
                    return
                count       = len(all_updates)
                logger.info(f"Check done: {count} updates")

                if _updates_db is not None:
                    _updates_db.save_pending(
                        all_updates,
                        replace_managers=("xbps", "flatpak")
                        if self._pkg_mgr == "xbps" else ("flatpak",),
                    )
                self._updates = all_updates

                # mismo número, _prev_count == count y nunca se enviaría la notificación.
                should_notify = manual or (count > 0 and count != self._notified)
                self._signals.check_finished.emit(should_notify, count, False)

            except Exception as exc:
                logger.error(f"check task: {exc}")
                if not self.background_mode:
                    self._signals.notify.emit(
                        tr("error_applet_notif_title"),
                        tr("error_applet_notif_body"),
                    )
                self._signals.check_finished.emit(False, self._count, True)
            finally:
                self._check_running = False

        self._check_thread = threading.Thread(target=_task, daemon=True)
        self._check_thread.start()

    # Signal store process

    def _signal_store(self) -> None:
        """
        Notifica al proceso principal de la tienda que recargue los datos de
        actualizaciones. Usa el socket IPC (canal principal); si no está
        disponible cae en SIGUSR1 como compatibilidad hacia atrás.
        """
        # Canal principal: socket IPC
        if _ipc_send("reload"):
            return
        # Fallback: SIGUSR1 (applets/scripts antiguos)
        try:
            result = subprocess.run(
                ["pgrep", "-f", "yl.soft|main.py"],
                capture_output=True, text=True, timeout=1,
            )
            my_pid = os.getpid()
            for pid_str in result.stdout.strip().splitlines():
                if pid_str.isdigit() and int(pid_str) != my_pid:
                    try:
                        os.kill(int(pid_str), signal.SIGUSR1)
                    except Exception:
                        pass
        except Exception as exc:
            logger.debug(f"signal_store: {exc}")

    # Actions

    def _open_store(self) -> None:
        """Abre Yelena Software directamente en la página de actualizaciones."""
        if self.background_mode:
            return
        if is_applet_updating():
            self._signals.notify.emit(
                tr("applet_updating_title"),
                tr("applet_updating_body"),
            )
            return

        # Si la tienda ya está corriendo, pedirle que se muestre (sin abrir otra).
        if _ipc_send("show-updates"):
            return

        # La tienda no está corriendo: lanzar una nueva instancia.
        # Intentar ejecutable del sistema primero
        for exe in _STORE_EXECS:
            try:
                result = subprocess.run(
                    ["which", exe], capture_output=True, timeout=1
                )
                if result.returncode == 0:
                    subprocess.Popen(
                        [exe, "--updates"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    return
            except Exception:
                pass

        # Fallback: lanzar main.py directamente
        try:
            subprocess.Popen(
                [sys.executable, _STORE_SCRIPT, "--updates"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            self._signals.notify.emit(
                tr("error_applet_notif_title"),
                tr("main_app_not_found"),
            )

    def _quick_update(self) -> None:
        """Abre el diálogo rápido de actualización (QuickUpdateDialog de CuerdToken)."""
        if self.background_mode:
            return
        if is_applet_updating():
            self._signals.notify.emit(
                tr("applet_updating_title"),
                tr("applet_updating_body"),
            )
            return
        updates = self._updates or (
            _updates_db.load_pending()[0] if _updates_db else []
        )
        if not updates:
            self._signals.notify.emit(
                tr("no_updates_notif_title"),
                tr("status_system_updated"),
            )
            return

        try:
            # Importar aquí para no ralentizar el arranque
            from cuerdtoken_qt.rc.quick_update import QuickUpdateDialog
        except ImportError:
            try:
                # Intentar desde el path original de cuerdtoken si está instalado
                import sys as _sys
                _ct_path = Path.home() / ".local" / "lib" / "cuerdtoken_qt" / "rc"
                if _ct_path.exists():
                    _sys.path.insert(0, str(_ct_path))
                    from quick_update import QuickUpdateDialog  # type: ignore
                else:
                    # No disponible: abrir la tienda
                    self._open_store()
                    return
            except ImportError:
                self._open_store()
                return

        def _on_done(success: bool):
            logger.info(f"Quick update done, success={success}")
            QTimer.singleShot(0, self._schedule_recheck)

        dlg = QuickUpdateDialog(
            parent=None,
            updates_list=updates,
            on_done_callback=_on_done,
            sys_pkg_mgr=self._pkg_mgr,
        )
        owner_pid = begin_system_update()
        if owner_pid is None:
            if is_applet_updating():
                self._signals.notify.emit(
                    tr("applet_updating_title"),
                    tr("applet_updating_body"),
                )
            else:
                logger.error("Could not create the system-update activity marker")
                self._open_store()
            return
        self._poll_update_activity()
        try:
            dlg.exec()
        finally:
            end_system_update(owner_pid)
            self._poll_update_activity()

    def _open_settings(self) -> None:
        if self.background_mode:
            return
        dlg = _ConfigDialog(self._interval)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            new_iv = dlg.selected_interval()
            if new_iv > 0:
                cfg = self._load_config()
                cfg["update_interval_seconds"] = new_iv
                self._save_config(cfg)
                self._interval = new_iv
                self._check_timer.setInterval(new_iv * 1000)
                logger.info(f"Interval updated: {new_iv} s")
                self._signals.notify.emit(
                    tr("config_dialog_saved_title"),
                    tr("config_dialog_saved_body").format(new_iv),
                )

    def _schedule_recheck(self) -> None:
        if self._shutting_down or self._recheck_scheduled:
            return
        self._recheck_scheduled = True
        self._emitter_refresh_pending = True
        logger.info("Re-check in 5 s")
        self._check_timer.stop()

        def _do():
            if self._shutting_down:
                self._recheck_scheduled = False
                self._emitter_refresh_pending = False
                return
            if is_applet_updating():
                QTimer.singleShot(1000, _do)
                return
            # No false reset of _check_running: the worker owns that state.
            # Wait until it exits, then run one fresh check after the FPM
            # transaction; repeated SIGUSR2 signals are coalesced above.
            if self._check_running:
                QTimer.singleShot(1000, _do)
                return
            self._recheck_scheduled = False
            self._notified = 0
            self._suppress_check_notifications = False
            self._do_check(periodic=True)
            self._check_timer.setInterval(self._interval * 1000)
            self._check_timer.start()

        QTimer.singleShot(5000, _do)

    def _quit(self) -> None:
        self.cleanup()
        self.app.quit()

    # Config persistence

    def _load_config(self) -> Dict:
        try:
            if _APPLET_CFG_FILE.exists():
                return json.loads(_APPLET_CFG_FILE.read_text())
        except Exception as exc:
            logger.error(f"load_config: {exc}")
        return {"update_interval_seconds": 14400}

    def _save_config(self, cfg: Dict) -> None:
        try:
            _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            _APPLET_CFG_FILE.write_text(json.dumps(cfg, indent=2))
        except Exception as exc:
            logger.error(f"save_config: {exc}")

    # Cleanup

    def cleanup(self) -> None:
        if self._shutting_down:
            return
        self._shutting_down = True
        logger.info("Shutting down…")
        for t in self._timers:
            if t and t.isActive():
                t.stop()
        self._timers.clear()
        self._check_running = False
        if self._check_thread and self._check_thread.is_alive():
            self._check_thread.join(timeout=1.5)
        if self._tray:
            self._tray.hide()
        _remove_pid_file()


# Entry point

def main() -> int:
    background_mode = _BACKGROUND_FLAG in sys.argv

    # Instancia única: solo en modo normal (no background)
    if not background_mode and _check_single_instance():
        sys.exit(0)

    # HiDPI + plataforma
    os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "1")
    if os.environ.get("WAYLAND_DISPLAY") or \
       os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland":
        os.environ.setdefault("QT_QPA_PLATFORM", "wayland;xcb")
    else:
        os.environ.setdefault("QT_QPA_PLATFORM", "xcb")

    app = QApplication(sys.argv)
    app.setApplicationName("yl-soft-applet")
    app.setQuitOnLastWindowClosed(False)

    # Icono de la aplicación tomado del conjunto propio, no del tema del SO.
    _build_state_icons()
    app.setWindowIcon(_state_icon("idle"))

    try:
        applet = YelenaApplet(app, background_mode=background_mode)

        def _sig_handler(signum, frame):
            logger.info(f"Signal {signum} received")
            applet.cleanup()
            app.quit()

        def _sig_usr2(signum, frame):
            """SIGUSR2: una actualización terminó → re-comprobar."""
            logger.info("SIGUSR2: update done, re-check scheduled")
            QTimer.singleShot(0, applet._schedule_recheck)

        def _sig_usr1(signum, frame):
            """SIGUSR1: la tienda pide refresco inmediato del cache."""
            QTimer.singleShot(0, lambda: applet._monitor_cache())

        signal.signal(signal.SIGTERM, _sig_handler)
        signal.signal(signal.SIGINT,  _sig_handler)
        try:
            signal.signal(signal.SIGUSR1, _sig_usr1)
            signal.signal(signal.SIGUSR2, _sig_usr2)
        except AttributeError:
            pass  # Windows

        logger.info(
            f"Yelena Software Applet started "
            f"({'background' if background_mode else 'tray'} mode, "
            f"pkg_mgr={applet._pkg_mgr})"
        )
        return app.exec()

    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        logger.error(f"Unexpected error: {exc}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
