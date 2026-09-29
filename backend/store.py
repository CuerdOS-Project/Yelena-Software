# Vera_Shop — Store Module

from __future__ import annotations

from pathlib import Path
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtCore import QSize

# Directorio raíz del proyecto
_ROOT = Path(__file__).parent.parent

# Rutas de búsqueda para el icono de la app
_APP_ICON_CANDIDATES = [
    _ROOT / "resources" / "yl-soft.svg",
    _ROOT / "yl-soft.svg",
    _ROOT / "icons" / "yl-soft.svg",
    _ROOT / "data" / "yl-soft.svg",
    Path("/usr/share/pixmaps/yl-soft.svg"),
    Path("/usr/share/icons/hicolor/scalable/apps/yl-soft.svg"),
]

# Cache global de iconos
_ICON_CACHE: dict[str, QIcon] = {}


def find_app_icon() -> QIcon:
    """
    Busca el icono de la aplicación en múltiples ubicaciones.
    Retorna QIcon válido o icono vacío.
    """
    if "app_icon" in _ICON_CACHE:
        return _ICON_CACHE["app_icon"]

    # Buscar archivo físico
    for path in _APP_ICON_CANDIDATES:
        if isinstance(path, str):
            path = Path(path)
        if path.exists() and path.is_file():
            icon = QIcon(str(path))
            if not icon.isNull():
                _ICON_CACHE["app_icon"] = icon
                return icon

    # Fallback: buscar en tema del sistema
    for theme_name in [
        "package-manager",
        "system-software-install",
        "applications-system",
        "yl-soft",
    ]:
        icon = QIcon.fromTheme(theme_name)
        if not icon.isNull():
            _ICON_CACHE["app_icon"] = icon
            return icon

    return QIcon()


def get_app_pixmap(size: int = 24) -> QPixmap | None:
    """Retorna un QPixmap del icono de la app en tamaño especificado."""
    icon = find_app_icon()
    if icon.isNull():
        return None
    px = icon.pixmap(QSize(size, size))
    return px if not px.isNull() else None


def get_theme_icon(names: list[str], size: int = 20) -> QIcon:
    """
    Busca un icono del tema del sistema por lista de nombres.
    Retorna el primer icono válido encontrado.
    """
    cache_key = f"theme_{','.join(names)}_{size}"
    if cache_key in _ICON_CACHE:
        return _ICON_CACHE[cache_key]

    for name in names:
        icon = QIcon.fromTheme(name)
        if not icon.isNull():
            _ICON_CACHE[cache_key] = icon
            return icon

    empty = QIcon()
    _ICON_CACHE[cache_key] = empty
    return empty


def clear_cache() -> None:
    """Limpia el caché de iconos."""
    _ICON_CACHE.clear()
