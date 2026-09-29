# disco a propósito: el catálogo remoto puede cambiar entre ejecuciones del

from __future__ import annotations

import time
from typing import List, Optional

_TTL_SECONDS = 300  # 5 minutos

# (fuente, consulta_normalizada) -> (timestamp, lista_de_paquetes)
_cache: dict[tuple[str, str], tuple[float, list]] = {}


def _key(source: str, query: str) -> tuple[str, str]:
    return (source, query.strip().lower())


def get(source: str, query: str) -> Optional[List]:
    """Devuelve los resultados cacheados para (source, query) si todavía
    están frescos, o None si no hay caché o ya expiró."""
    entry = _cache.get(_key(source, query))
    if entry is None:
        return None
    timestamp, packages = entry
    if (time.time() - timestamp) > _TTL_SECONDS:
        _cache.pop(_key(source, query), None)
        return None
    return packages


def set_(source: str, query: str, packages: List) -> None:
    """Guarda los resultados de (source, query) en la caché."""
    _cache[_key(source, query)] = (time.time(), packages)


def invalidate(source: Optional[str] = None) -> None:
    """Limpia la caché por completo, o solo la de una fuente concreta.
    Útil tras instalar/desinstalar algo, para que la próxima búsqueda
    refleje el nuevo estado en vez de un resultado cacheado obsoleto."""
    if source is None:
        _cache.clear()
        return
    for key in [k for k in _cache if k[0] == source]:
        _cache.pop(key, None)
