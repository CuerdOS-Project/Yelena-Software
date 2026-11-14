# Yelena Software — Unified package blacklist

from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import List, Optional

from .models import Package, PackageSource

_DATA_DIR = Path(__file__).parent.parent / "resources" / "data"
# Única blacklist basada en archivo. Este archivo contiene las reglas generales
# de paquetes y es la fuente de verdad para XBPS y las demás fuentes de tienda.
_BLACKLIST_PATH = _DATA_DIR / "blacklist_xbps.kn"
_BLACKLIST_XBPS_PATH = _BLACKLIST_PATH  # compatibilidad con callers antiguos

# Tablas de reglas leídas desde la única blacklist.
_rules_name: List[str] = []
_rules_id: List[str] = []
_rules_cat: List[str] = []
_loaded = False
_mtime: Optional[int] = None
_size: Optional[int] = None
# Compatibilidad temporal para consumidores antiguos del nombre XBPS.
_xbps_rules_name: List[str] = _rules_name
_xbps_loaded = False
_xbps_mtime: Optional[int] = None

# Excepciones explícitas a reglas wildcard amplias como lib* y kde*.
_LIBRARY_PREFIX_APP_ALLOWLIST = {"libreoffice", "librewolf"}
_XBPS_APP_ALLOWLIST = _LIBRARY_PREFIX_APP_ALLOWLIST | {"kdenlive"}
_FLATPAK_COMPONENT_ID_PARTS = {
    "baseapp", "codec", "codecs", "debug", "extension", "extensions",
    "locale", "locales", "platform", "plugin", "plugins", "runtime",
    "runtimes", "sdk", "sources",
}


def _parse_file(path: Path) -> tuple[List[str], List[str], List[str]]:
    """Parsea reglas name:, id: y cat: de la blacklist unificada."""
    names: List[str] = []
    ids: List[str] = []
    cats: List[str] = []
    try:
        with path.open("r", encoding="utf-8") as stream:
            for raw in stream:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("name:"):
                    pattern = line[5:].strip().lower()
                    if pattern:
                        names.append(pattern)
                elif line.startswith("id:"):
                    pattern = line[3:].strip().lower()
                    if pattern:
                        ids.append(pattern)
                elif line.startswith("cat:"):
                    category = line[4:].strip().lower()
                    if category:
                        cats.append(category)
    except OSError:
        # Si el archivo temporalmente no está disponible, no bloquear la tienda.
        return [], [], []
    return names, ids, cats


def _load() -> None:
    """Carga/relee blacklist_xbps.kn cuando cambia, incluso en caliente."""
    global _loaded, _mtime, _size, _rules_name, _rules_id, _rules_cat
    global _xbps_rules_name, _xbps_loaded, _xbps_mtime

    try:
        stat = _BLACKLIST_PATH.stat()
        mtime, size = stat.st_mtime_ns, stat.st_size
    except OSError:
        if not _loaded or _mtime is not None:
            _rules_name, _rules_id, _rules_cat = [], [], []
            _xbps_rules_name = _rules_name
        _loaded = True
        _mtime = _size = None
        _xbps_loaded = True
        _xbps_mtime = None
        return

    if _loaded and mtime == _mtime and size == _size:
        return

    _rules_name, _rules_id, _rules_cat = _parse_file(_BLACKLIST_PATH)
    _xbps_rules_name = _rules_name
    _mtime, _size = mtime, size
    _loaded = _xbps_loaded = True
    _xbps_mtime = mtime


def _load_xbps() -> None:
    """Compatibilidad: la blacklist XBPS es ahora la única blacklist."""
    _load()


_matcher_cache: dict = {}


def _compiled_matcher(patterns: List[str]):
    """Precompila coincidencias exactas y comodines para reducir trabajo."""
    entry = _matcher_cache.get(id(patterns))
    if entry is not None and entry[0] is patterns and entry[1] == len(patterns):
        return entry[2], entry[3]
    import re
    exact = set()
    wild = []
    for pattern in patterns:
        if any(char in pattern for char in "*?["):
            wild.append(fnmatch.translate(pattern))
        else:
            exact.add(pattern)
    regex = re.compile("|".join(wild)) if wild else None
    _matcher_cache[id(patterns)] = (patterns, len(patterns), exact, regex)
    return exact, regex


def _matches_any(value: str, patterns: List[str]) -> bool:
    if not patterns:
        return False
    normalized = (value or "").strip().lower()
    if not normalized:
        return False
    exact, regex = _compiled_matcher(patterns)
    if normalized in exact:
        return True
    return regex is not None and regex.match(normalized) is not None


def is_flatpak_component_id(app_id: str) -> bool:
    """True para runtimes, SDKs, codecs, plugins, idiomas y extensiones."""
    normalized = (app_id or "").strip().lower()
    if normalized.startswith("flatpak:"):
        normalized = normalized[len("flatpak:"):]
    parts = set(normalized.split("."))
    return bool(parts & _FLATPAK_COMPONENT_ID_PARTS)


def _package_matches_rules(pkg: Package) -> bool:
    """Aplica la única tabla de reglas al nombre, ID, pkgname o categoría."""
    _load()
    name_candidates = {pkg.name.strip().lower(), (pkg.pkgname or "").strip().lower()}
    name_candidates.discard("")
    if any(_matches_any(name, _rules_name) for name in name_candidates):
        return True

    id_candidates = {pkg.id.strip().lower(), (pkg.appstream_id or "").strip().lower()}
    id_candidates.discard("")
    if any(_matches_any(value, _rules_id) for value in id_candidates):
        return True
    return bool(pkg.category and _matches_any(pkg.category.lower(), _rules_cat))


def is_blacklisted(pkg: Package) -> bool:
    """Aplica blacklist_xbps.kn; los componentes Flatpak también se detectan por ID."""
    if pkg.source == PackageSource.FLATPAK:
        if is_flatpak_component_id(pkg.appstream_id or pkg.id):
            return True
        if pkg.name.strip().lower() in _LIBRARY_PREFIX_APP_ALLOWLIST:
            return False
        return _package_matches_rules(pkg)
    if pkg.name.strip().lower() in _XBPS_APP_ALLOWLIST:
        return False
    return _package_matches_rules(pkg)


def _is_blacklisted_xbps(pkg: Package) -> bool:
    """Comprueba XBPS usando exclusivamente blacklist_xbps.kn."""
    if pkg.name.strip().lower() in _XBPS_APP_ALLOWLIST:
        return False
    return _package_matches_rules(pkg)


def _is_blacklisted_flatpak(pkg: Package) -> bool:
    """Compatibilidad: consulta la misma blacklist unificada."""
    if pkg.name.strip().lower() in _LIBRARY_PREFIX_APP_ALLOWLIST:
        return False
    return _package_matches_rules(pkg)


def filter_packages(packages: List[Package]) -> List[Package]:
    """Devuelve una lista sin los paquetes bloqueados."""
    return [package for package in packages if not is_blacklisted(package)]


def blacklist_revision() -> tuple[int | None, int | None]:
    """Identificador para invalidar decisiones cacheadas si cambia el archivo."""
    _load()
    return _mtime, _size


def blacklist_path() -> Path:
    """Devuelve la única blacklist de paquetes: blacklist_xbps.kn."""
    return _BLACKLIST_PATH


def blacklist_xbps_path() -> Path:
    """Alias explícito a la blacklist unificada."""
    return _BLACKLIST_PATH
