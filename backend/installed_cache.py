# yl-soft — Caché de apps instaladas
#
# Guarda en disco el último listado conocido por fuente (xbps/flatpak/
# appimage) para poder mostrarlo instantáneamente al abrir la app,
# mientras el escaneo real corre en segundo plano y solo actualiza lo
# que cambió (se agregó o se desinstaló algo).

from __future__ import annotations

import json
import os
from dataclasses import asdict, fields
from pathlib import Path
from typing import Dict, List

from .models import Package, PackageSource, PackageStatus

_CACHE_VERSION = 1


def _cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache"))
    d = Path(base) / "yl-soft"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cache_file() -> Path:
    return _cache_dir() / "installed_cache.json"


_PKG_FIELDS = {f.name for f in fields(Package)}


def _pkg_to_dict(p: Package) -> dict:
    d = asdict(p)
    d["source"] = p.source.value
    d.pop("status", None)  # se recalcula siempre como INSTALLED al cargar
    return d


def _dict_to_pkg(d: dict) -> Package | None:
    try:
        source = PackageSource(d.get("source", "xbps"))
    except ValueError:
        return None
    kwargs = {k: v for k, v in d.items() if k in _PKG_FIELDS and k != "source"}
    return Package(source=source, status=PackageStatus.INSTALLED, **kwargs)


def load_cache() -> Dict[str, List[Package]]:
    """Devuelve {source_key: [Package, ...]} desde la última sesión.
    Vacío si no hay caché o está corrupta/desactualizada."""
    f = _cache_file()
    if not f.exists():
        return {}
    try:
        raw = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if raw.get("version") != _CACHE_VERSION:
        return {}

    out: Dict[str, List[Package]] = {}
    for src, items in raw.get("sources", {}).items():
        pkgs = [p for p in (_dict_to_pkg(d) for d in items) if p is not None]
        out[src] = pkgs
    return out


def save_cache(pkgs_by_source: Dict[str, List[Package]]) -> None:
    """Persiste el último listado real por fuente. Best-effort: si falla
    (disco lleno, sin permisos, etc.) no interrumpe la app."""
    raw = {
        "version": _CACHE_VERSION,
        "sources": {
            src: [_pkg_to_dict(p) for p in plist]
            for src, plist in pkgs_by_source.items()
        },
    }
    try:
        _cache_file().write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
