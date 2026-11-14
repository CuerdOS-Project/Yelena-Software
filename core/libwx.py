# libwx — Núcleo de Yelena Software

from __future__ import annotations

import os
import shutil
import subprocess

# Versión del núcleo
LIBWX_VERSION = "1.0.0"
LIBWX_REQUIRED_INITS = ("systemd", "runit")


# Excepción de init incompatible

class LibwxInitError(RuntimeError):
    """Se lanza cuando el init system detectado no es compatible con libwx."""

    def __init__(self, detected_init: str):
        self.detected_init = detected_init
        super().__init__(
            f"Init system incompatible: '{detected_init}'.\n"
            f"libwx requiere systemd o runit para funcionar.\n"
            f"Los sistemas con OpenRC no están soportados."
        )


# Detección del init system

def _probe_systemd() -> bool:
    """Devuelve True si el sistema corre systemd como PID 1."""
    # /run/systemd/system existe solo en sesiones systemd reales
    if os.path.isdir("/run/systemd/system"):
        return True
    # Fallback: systemctl --version devuelve 0 solo con systemd activo
    if shutil.which("systemctl"):
        try:
            r = subprocess.run(
                ["systemctl", "is-system-running"],
                capture_output=True, text=True, timeout=3,
            )
            # Acepta cualquier salida (running/degraded/maintenance)
            # siempre que el proceso no falle completamente
            if r.returncode in (0, 1):
                return True
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
    return False


def _probe_runit() -> bool:
    """Devuelve True si el sistema corre runit como gestor de servicios."""
    # /run/runit/ es el socket de control de runit
    if os.path.isdir("/run/runit"):
        return True
    # Void Linux (sin systemd) tiene sv y runsv en PATH
    if shutil.which("sv") and shutil.which("runsv"):
        return True
    # /etc/runit/reboot es un indicador clásico
    if os.path.exists("/etc/runit/reboot"):
        return True
    return False


def _probe_openrc() -> bool:
    """Devuelve True si el sistema corre OpenRC."""
    # /run/openrc/ es la carpeta de estado de OpenRC
    if os.path.isdir("/run/openrc"):
        return True
    # openrc-run o rc-service en PATH
    if shutil.which("openrc-run") or shutil.which("rc-service"):
        return True
    # /sbin/openrc es el binario principal en Gentoo/Artix/Alpine
    if os.path.isfile("/sbin/openrc"):
        return True
    return False


def detect_init() -> str:
    """
    Detecta el init system activo.

    Returns:
        "systemd" | "runit" | "openrc" | "unknown"
    """
    if _probe_systemd():
        return "systemd"
    if _probe_runit():
        return "runit"
    if _probe_openrc():
        return "openrc"
    return "unknown"


# API pública

def check_system() -> str:
    """
    Valida que el init system sea compatible con libwx.

    Returns:
        El nombre del init system detectado ("systemd" o "runit").

    Raises:
        LibwxInitError: Si se detecta OpenRC u otro init no soportado.
    """
    init = detect_init()
    if init not in LIBWX_REQUIRED_INITS:
        raise LibwxInitError(init)
    return init


def get_version() -> str:
    """Devuelve la versión de libwx."""
    return LIBWX_VERSION


def get_data_dir() -> str:
    """Devuelve el directorio XDG_DATA_HOME de la aplicación."""
    xdg = os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))
    return os.path.join(xdg, "yelena-software")


def get_cache_dir() -> str:
    """Devuelve el directorio XDG_CACHE_HOME de la aplicación."""
    xdg = os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache"))
    return os.path.join(xdg, "yelena-software")


def get_config_dir() -> str:
    """Devuelve el directorio XDG_CONFIG_HOME de la aplicación."""
    xdg = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
    return os.path.join(xdg, "yelena-software")


def ensure_dirs() -> None:
    """Crea los directorios XDG necesarios si no existen."""
    for d in (get_data_dir(), get_cache_dir(), get_config_dir()):
        os.makedirs(d, exist_ok=True)
