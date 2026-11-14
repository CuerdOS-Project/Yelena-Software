# Vera_Shop — Detección del sistema de paquetes

from __future__ import annotations
import shutil

def _has(cmd: str) -> bool:
    return bool(shutil.which(cmd))

def has_xbps() -> bool:
    return _has("xbps-query")

def has_flatpak() -> bool:
    return _has("flatpak")

def has_fpm() -> bool:
    return _has("fpm")

def show_xbps() -> bool:
    return has_xbps()

def show_flatpak() -> bool:
    return has_flatpak()

def show_appimage() -> bool:
    return has_fpm()

def active_sources() -> list[str]:
    """Lista de fuentes activas: subset de ['flatpak','xbps','appimage']."""
    srcs = []
    if show_flatpak():  srcs.append("flatpak")
    if show_xbps():     srcs.append("xbps")
    if show_appimage(): srcs.append("appimage")
    return srcs


def detect_package_manager() -> str:
    """Devuelve el gestor de paquetes primario del sistema ('xbps' o 'unknown')."""
    if has_xbps():
        return "xbps"
    return "unknown"
