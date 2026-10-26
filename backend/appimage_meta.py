# yl-soft — Tamaño y versión de apps am-FUSION que aún no están instaladas
"""El catálogo de AM (``am -l --appimages``) solo trae nombre y descripción.
Tamaño y versión de una app no instalada solo existen en su release remota,
así que se resuelven bajo demanda y se guardan en una caché en disco.

Fuente: el script de instalación de AM de la app declara ``SITE="dueño/repo"``;
con él se consulta la última release de GitHub y se toma el .AppImage x86_64.
Si algo falla (sin red, límite de la API, app sin GitHub) simplemente no se
completan los datos: nunca se inventa un valor.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, wait
from pathlib import Path
from typing import Iterable, Optional

_SCRIPT_URL = "https://raw.githubusercontent.com/ivan-hc/AM/main/programs/x86_64/{name}"
_API_LATEST = "https://api.github.com/repos/{site}/releases/latest"
_API_LIST = "https://api.github.com/repos/{site}/releases?per_page=8"
_SITE_RE = re.compile(r'^\s*SITE=["\']?([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)["\']?\s*$', re.MULTILINE)
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+-]*$")
_EXCLUDED = ("arm", "aarch64", "i386", "i686", "ia32", "riscv", "ppc", "s390", "zsync", "sha", "sig")
_OK_TTL = 7 * 24 * 3600      # datos correctos: una semana
_MISS_TTL = 6 * 3600         # fallos: reintentar más tarde (límite de API, etc.)
_TIMEOUT = 6

_lock = threading.Lock()
_cache: Optional[dict] = None


def _cache_path() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return Path(base) / "yelena-soft" / "appimage_meta.json"


def _load() -> dict:
    global _cache
    if _cache is None:
        try:
            data = json.loads(_cache_path().read_text(encoding="utf-8"))
            _cache = data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            _cache = {}
    return _cache


def _save() -> None:
    path = _cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".meta-")
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(_cache, handle)
        os.replace(tmp, path)
    except OSError:
        pass


def _key(name: str) -> str:
    base = (name or "").strip().casefold()
    return base[:-len("-appimage")] if base.endswith("-appimage") else base


def cached(name: str, max_age: Optional[float] = None) -> Optional[dict]:
    """Datos guardados y vigentes ({'size': int, 'version': str, 'asset': str}) o None.

    ``max_age`` (segundos) acorta la vigencia; se usa al buscar actualizaciones.
    """
    with _lock:
        entry = _load().get(_key(name))
    if not isinstance(entry, dict):
        return None
    ttl = _OK_TTL if entry.get("size") else _MISS_TTL
    if max_age is not None:
        ttl = min(ttl, max_age)
    if time.time() - float(entry.get("ts", 0)) > ttl:
        return None
    return entry


def _get(url: str) -> Optional[bytes]:
    request = urllib.request.Request(url, headers={
        "User-Agent": "yelena-soft", "Accept": "application/vnd.github+json, text/plain"})
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
            return response.read(2 * 1024 * 1024)
    except (urllib.error.URLError, OSError, ValueError):
        return None


def _pick_asset(assets: list) -> Optional[dict]:
    candidates = []
    for asset in assets:
        name = str(asset.get("name", ""))
        lowered = name.casefold()
        if not lowered.endswith(".appimage") or any(bad in lowered for bad in _EXCLUDED):
            continue
        preferred = any(tag in lowered for tag in ("x86_64", "amd64", "x64"))
        candidates.append((0 if preferred else 1, asset))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]


def _from_release(release: dict) -> Optional[dict]:
    asset = _pick_asset(release.get("assets") or [])
    if asset is None:
        return None
    try:
        size = max(0, int(asset.get("size") or 0))
    except (TypeError, ValueError):
        return None
    version = str(release.get("tag_name") or "").strip()
    if version[:1] in ("v", "V") and version[1:2].isdigit():
        version = version[1:]
    return {"size": size, "version": version, "asset": str(asset.get("name") or "")}


def _resolve(name: str) -> Optional[dict]:
    key = _key(name)
    if not _NAME_RE.match(key):
        return None
    script = _get(_SCRIPT_URL.format(name=key))
    if not script:
        return None
    match = _SITE_RE.search(script.decode("utf-8", errors="replace"))
    if not match:
        return None
    site = match.group(1)
    raw = _get(_API_LATEST.format(site=site))
    releases: list = []
    if raw:
        try:
            parsed = json.loads(raw)
            releases = [parsed] if isinstance(parsed, dict) else []
        except ValueError:
            releases = []
    result = _from_release(releases[0]) if releases else None
    if result is None:  # /latest no incluye pre-releases ni repos sin release estable
        raw = _get(_API_LIST.format(site=site))
        try:
            listing = json.loads(raw) if raw else []
        except ValueError:
            listing = []
        for release in listing if isinstance(listing, list) else []:
            result = _from_release(release)
            if result:
                break
    return result


def fetch(name: str, max_age: Optional[float] = None) -> Optional[dict]:
    """Devuelve (y guarda) tamaño/versión remotos de una app de am-FUSION."""
    entry = cached(name, max_age)
    if entry is not None:
        return entry if entry.get("size") else None
    result = _resolve(name)
    with _lock:
        _load()[_key(name)] = {
            "size": result["size"] if result else 0,
            "version": result["version"] if result else "",
            "asset": result.get("asset", "") if result else "",
            "ts": time.time(),
        }
        _save()
    return result


def fetch_many(names: Iterable[str], max_workers: int = 6, deadline: float = 8.0,
               max_age: Optional[float] = None) -> None:
    """Resuelve varias apps en paralelo, sin esperar más de ``deadline`` s."""
    pending = [n for n in dict.fromkeys(names) if n and cached(n, max_age) is None]
    if not pending:
        return
    pool = ThreadPoolExecutor(max_workers=max_workers)
    try:
        wait([pool.submit(fetch, name, max_age) for name in pending], timeout=deadline)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def _needs_data(pkg) -> bool:
    from .models import PackageSource
    return (pkg.source == PackageSource.APPIMAGE and pkg.repository == "am-FUSION"
            and not pkg.is_installed and (pkg.size_bytes <= 0 or not pkg.version))


def apply_cached(packages: list) -> bool:
    """Completa tamaño/versión desde la caché local (sin red). True si cambió algo."""
    changed = False
    for pkg in packages:
        if not _needs_data(pkg):
            continue
        entry = cached(pkg.pkgname or pkg.name)
        if not entry:
            continue
        if pkg.size_bytes <= 0 and entry.get("size"):
            pkg.size_bytes = int(entry["size"])
            changed = True
        if not pkg.version and entry.get("version"):
            pkg.version = str(entry["version"])
            changed = True
    return changed


def missing_names(packages: list, limit: int = 24) -> list[str]:
    """Nombres de paquetes AM sin datos y sin intento reciente (máx. ``limit``)."""
    names = []
    for pkg in packages:
        if _needs_data(pkg) and cached(pkg.pkgname or pkg.name) is None:
            names.append(pkg.pkgname or pkg.name)
            if len(names) >= limit:
                break
    return names


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").casefold())


def is_outdated(installed_version: str, release: Optional[dict]) -> bool:
    """True solo si hay evidencia clara de que la release remota es distinta.

    AM guarda como versión instalada la URL/etiqueta de la descarga, así que se
    busca la etiqueta (o el nombre del asset) remota dentro de ella. Ante la
    duda (sin versión, sin dígitos, sin datos remotos) NO se avisa de
    actualización, para no mostrar falsos positivos.
    """
    if not release or not release.get("size"):
        return False
    installed = _norm(installed_version)
    if not installed or not any(ch.isdigit() for ch in installed):
        return False
    tag = _norm(release.get("version", ""))
    asset = _norm(release.get("asset", ""))
    if not tag and not asset:
        return False
    if tag and tag in installed:
        return False
    if asset and asset in installed:
        return False
    return True
