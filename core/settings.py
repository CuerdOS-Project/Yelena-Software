# yl-soft — Application Settings

from __future__ import annotations

import configparser
import os
from typing import Optional

_CONFIG_DIR  = os.path.expanduser("~/.config/yl-soft")
_CONFIG_FILE = os.path.join(_CONFIG_DIR, "settings.ini")


def _system_defaults() -> dict:
    """Defaults que respetan el sistema detectado (XBPS + Flatpak)."""
    try:
        from backend.system_detect import show_xbps
        _show_xbps = "true" if show_xbps() else "false"
    except Exception:
        _show_xbps = "true"  # falla segura
    return {
        "language":                 "auto",
        "show_flatpak":             "true",
        "show_xbps":                _show_xbps,
        # Motor XBPS: FPM (CuerdOS) para búsqueda/catálogo o el motor nativo.
        "package_engine":           "yelena-buddies",
        "flatpak_remote":           "flathub",
        "sidebar_visible":          "true",
        "show_update_status":       "true",
        # Vista de la página de instalados: "list" o "grid" (4 columnas)
        "installed_view":           "list",
        # Pide confirmación antes de desinstalar un paquete
        "confirm_before_remove":    "true",
        # Sincroniza repos y comprueba actualizaciones automáticamente al
        # abrir el programa (antes desactivado por defecto)
        "check_updates_on_startup": "true",
        # Cierra la ventana a la bandeja del sistema en lugar de salir
        "close_to_tray":            "false",
        # Muestra paquetes de desarrollo/librerías (-devel, lib*) en instalados
        "show_dev_packages":        "true",
        # Confirma antes de instalar paquetes
        "confirm_before_install":   "false",
        # Tema visual: "auto" (nativo), "csds", "yelena_system", "breeze", "fusion"
        "theme":                    "auto",
        # Muestra en la búsqueda paquetes filtrados por la blacklist
        # (no recomendable: pueden ser paquetes de sistema, duplicados o inestables)
        "search_show_hidden":       "false",
        # Timestamp (epoch, string) de la última vez que se sincronizaron
        # repos y se comprobaron actualizaciones ("0" = nunca en este equipo)
        "last_update_check":        "0",
    }


_defaults = _system_defaults()

_cfg = configparser.ConfigParser()
_cfg["store"] = dict(_defaults)


def load() -> None:
    """Load settings from disk. Safe to call even if file doesn't exist."""
    if os.path.exists(_CONFIG_FILE):
        try:
            _cfg.read(_CONFIG_FILE, encoding="utf-8")
        except Exception as exc:
            print(f"[settings] Warning: could not read settings: {exc}")


def save() -> None:
    """Persist current settings to disk."""
    os.makedirs(_CONFIG_DIR, exist_ok=True)
    try:
        with open(_CONFIG_FILE, "w", encoding="utf-8") as f:
            _cfg.write(f)
    except Exception as exc:
        print(f"[settings] Warning: could not save settings: {exc}")


def get(key: str, fallback: Optional[str] = None) -> str:
    return _cfg.get("store", key, fallback=fallback or _defaults.get(key, ""))


def set_value(key: str, value) -> None:
    """Guarda un valor en la sección 'store'. configparser exige strings
    (en Python 3.14 esto pasó a validarse estrictamente y lanza TypeError
    si se le pasa un int/bool/float directamente, p.ej. desde un
    QSpinBox.value()), así que siempre se convierte aquí."""
    if "store" not in _cfg:
        _cfg["store"] = {}
    _cfg["store"][key] = str(value)


def get_bool(key: str) -> bool:
    return _cfg.getboolean("store", key, fallback=True)


def set_bool(key: str, value: bool) -> None:
    set_value(key, "true" if value else "false")


# Load on import
load()


# Compat wrappers

def load_config() -> dict:
    """Carga la configuración y la devuelve como dict."""
    load()
    return dict(_cfg["store"]) if "store" in _cfg else {}


def save_config(cfg: dict) -> None:
    """Guarda un dict de configuración."""
    for k, v in cfg.items():
        set_value(k, str(v))
    save()
