# "red"    -> necesita atención (fallo al comprobar o instalar, o backend no disponible)

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Optional

try:
    from backend.updates_db import updates_db as _updates_db
except Exception:
    _updates_db = None

try:
    from backend.system_detect import detect_package_manager
except Exception:
    detect_package_manager = None


# Paquetes CRÍTICOS del sistema -> nivel ROJO (necesitan atención inmediata,
# romper estos paquetes puede dejar el sistema sin arrancar)
_CRITICAL_KEYWORDS = (
    "kernel", "linux-image", "linux-headers", "linux-firmware",
    "grub", "glibc", "systemd", "init", "dracut", "base-system",
    "initramfs",
)

# Paquetes del gestor XBPS o herramientas base de GNU -> nivel AMARILLO
# (importantes, pero no críticos de inmediato)
_GNU_KEYWORDS = (
    "xbps", "libxbps", "xbps-triggers", "xbps-static",
    "gnu", "bash", "coreutils", "binutils", "glibc-locales",
    "gcc", "make", "sudo", "openssl", "libssl", "polkit", "dbus",
    "pam", "xorg", "wayland",
)


def _is_critical(name: str) -> bool:
    n = (name or "").lower()
    return any(k in n for k in _CRITICAL_KEYWORDS)


def is_critical_update(name: str) -> bool:
    """API pública: indica si un paquete es crítico del sistema
    (kernel, glibc, systemd, grub...). Usado por la UI para
    priorizar y auto-seleccionar estas actualizaciones."""
    return _is_critical(name)


def _is_gnu_or_pm(name: str) -> bool:
    n = (name or "").lower()
    return any(k in n for k in _GNU_KEYWORDS)


@dataclass
class UpdateStatus:
    level: str            # "gray" | "green" | "yellow" | "red"
    count: int
    important_count: int  # paquetes críticos o de xbps/GNU detectados
    title_key: str        # clave de traducción del título
    message_key: str      # clave de traducción del mensaje
    detail: str = ""      # texto adicional (p.ej. mensaje de error)


class UpdateStatusTracker:
    """Singleton thread-safe que recuerda si la última operación falló."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last_error: Optional[str] = None

    def set_error(self, message: Optional[str]) -> None:
        """Marca (o limpia, con None) un error de la última comprobación/instalación."""
        with self._lock:
            self._last_error = message

    def clear_error(self) -> None:
        self.set_error(None)

    def has_error(self) -> bool:
        with self._lock:
            return self._last_error is not None

    def error_message(self) -> str:
        with self._lock:
            return self._last_error or ""

    def compute(self) -> UpdateStatus:
        # 1) Error explícito reportado por la UI (instalación/comprobación fallida)
        if self.has_error():
            return UpdateStatus(
                level="red", count=0, important_count=0,
                title_key="status_error_title",
                message_key="status_error_message",
                detail=self.error_message(),
            )

        # 2) Backend de paquetes no disponible / no detectado
        if _updates_db is None:
            return UpdateStatus(
                level="red", count=0, important_count=0,
                title_key="status_error_title",
                message_key="status_backend_unavailable",
            )

        if detect_package_manager is not None:
            try:
                if detect_package_manager() == "unknown":
                    return UpdateStatus(
                        level="red", count=0, important_count=0,
                        title_key="status_error_title",
                        message_key="status_backend_unavailable",
                    )
            except Exception:
                pass

        # 3) Leer pendientes
        try:
            pending, count = _updates_db.load_pending()
        except Exception as exc:
            return UpdateStatus(
                level="red", count=0, important_count=0,
                title_key="status_error_title",
                message_key="status_error_message",
                detail=str(exc),
            )

        if count == 0:
            return UpdateStatus(
                level="gray", count=0, important_count=0,
                title_key="status_gray_title",
                message_key="status_gray_message",
            )

        # 4) Paquetes críticos (kernel, glibc, systemd, grub...) -> ROJO
        critical = [u for u in pending if _is_critical(u.get("name", ""))]
        if critical:
            return UpdateStatus(
                level="red", count=count, important_count=len(critical),
                title_key="status_critical_title",
                message_key="status_critical_message",
            )

        # 5) xbps / herramientas base de GNU -> AMARILLO
        gnu_pm = [u for u in pending if _is_gnu_or_pm(u.get("name", ""))]
        if gnu_pm:
            return UpdateStatus(
                level="yellow", count=count, important_count=len(gnu_pm),
                title_key="status_yellow_title",
                message_key="status_yellow_message",
            )

        return UpdateStatus(
            level="green", count=count, important_count=0,
            title_key="status_green_title",
            message_key="status_green_message",
        )


# Singleton de módulo
update_status = UpdateStatusTracker()
