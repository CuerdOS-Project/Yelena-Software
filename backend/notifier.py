# Yelena Software — Notifier

from __future__ import annotations

import subprocess
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PySide6.QtWidgets import QSystemTrayIcon as _QSTIcon

_tray: "_QSTIcon | None" = None
_NOTIFY_SEND = shutil.which("notify-send")
_APP_NAME = "Yelena Software"

_ROOT = Path(__file__).parent.parent
_APP_ICON_CANDIDATES = [
    _ROOT / "resources" / "yl-soft.svg",
    _ROOT / "yl-soft.svg",
    _ROOT / "icons" / "yl-soft.svg",
    _ROOT / "data" / "yl-soft.svg",
    Path("/usr/share/pixmaps/yl-soft.svg"),
    Path("/usr/share/icons/hicolor/scalable/apps/yl-soft.svg"),
]
_app_icon_path_cache: "str | None" = ""  # "" = aún no resuelto


def _app_icon_path() -> "str | None":
    """Ruta al icono de Yelena Software, usado como icono de las
    notificaciones (tray y notify-send) en vez de iconos genéricos."""
    global _app_icon_path_cache
    if _app_icon_path_cache != "":
        return _app_icon_path_cache
    for path in _APP_ICON_CANDIDATES:
        if path.exists() and path.is_file():
            _app_icon_path_cache = str(path)
            return _app_icon_path_cache
    _app_icon_path_cache = None
    return None


def set_tray(tray) -> None:
    """Registra el QSystemTrayIcon para usarlo en notificaciones."""
    global _tray
    _tray = tray


def notify(title: str, body: str, icon: str = "dialog-information") -> None:
    """
    Muestra una notificación de escritorio.
    icon puede ser: 'dialog-information', 'dialog-error', 'dialog-warning',
                    o la ruta a un SVG/PNG.
    """
    app_icon_path = _app_icon_path()

    # 1. QSystemTrayIcon (disponible si la app ya lo creó): se usa el
    # icono del programa como imagen de la notificación en vez del
    # icono genérico de severidad.
    if _tray is not None:
        try:
            from PySide6.QtWidgets import QSystemTrayIcon
            from PySide6.QtGui import QIcon
            shown_icon = QIcon(app_icon_path) if app_icon_path else None
            if shown_icon and not shown_icon.isNull():
                _tray.showMessage(title, body, shown_icon, 4000)
            else:
                msg_icon = QSystemTrayIcon.Information
                if "error" in icon.lower() or "fail" in icon.lower():
                    msg_icon = QSystemTrayIcon.Critical
                elif "warning" in icon.lower():
                    msg_icon = QSystemTrayIcon.Warning
                _tray.showMessage(title, body, msg_icon, 4000)
            return
        except Exception:
            pass

    # 2. notify-send (funciona en entornos sin tray visible)
    if _NOTIFY_SEND:
        try:
            notif_icon = app_icon_path or icon
            args = [_NOTIFY_SEND, "-a", _APP_NAME, "-i", notif_icon, "-t", "5000", title, body]
            subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass


def notify_task_done(pkg_name: str, task_type_value: str, success: bool) -> None:
    """Notificación estándar al terminar una tarea."""
    action_map = {
        "Install": ("instalado", "instalar"),
        "Remove":  ("desinstalado", "desinstalar"),
        "Update":  ("actualizado", "actualizar"),
        "Refresh": ("actualizada", "actualizar la lista"),
    }
    past, infinitive = action_map.get(task_type_value, (task_type_value.lower(), task_type_value.lower()))

    if success:
        # Icono por acción: desinstalar usa trash, instalar/actualizar usa check
        if task_type_value == "Remove":
            icon = "user-trash"
        elif task_type_value in ("Install", "Update"):
            icon = "software-update-available"
        else:
            icon = "dialog-information"
        notify(
            f"{pkg_name} {past}",
            f"El paquete se {past} correctamente.",
            icon=icon,
        )
    else:
        notify(
            f"Error al {infinitive} {pkg_name}",
            "Revisa la pestaña Tareas para más detalles.",
            icon="dialog-error",
        )
