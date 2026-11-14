# Deduplicación de resultados XBPS: si un mismo paquete aparece repetido
# (varios repositorios o varios motores), se omite uno y se prioriza el otro.

from __future__ import annotations

from typing import Iterable, List, Set

from .models import Package, PackageSource, PackageStatus

# Repositorios de procedencia, de mayor a menor prioridad.
_REPO_PRIORITY = ("", "current", "cuerdos", "multilib", "nonfree")


def _key(pkg: Package) -> str:
    return pkg.pkgname or pkg.id


def _score(pkg: Package) -> tuple:
    """Menor = mejor: instalado > repo con más prioridad > más datos."""
    installed = 0 if pkg.status == PackageStatus.INSTALLED else 1
    repo = (pkg.repository or "").casefold()
    repo_rank = next((i for i, r in enumerate(_REPO_PRIORITY) if repo == r.casefold()),
                     len(_REPO_PRIORITY))
    richness = -(bool(pkg.size_bytes) + bool(pkg.icon_url) + bool(pkg.version))
    return (installed, repo_rank, richness)


def dedupe_xbps(packages: Iterable[Package], known: Set[str] | None = None) -> List[Package]:
    """Quita duplicados XBPS conservando el orden. `known` son las claves
    ya mostradas (modo streaming): un duplicado tardío se omite. Los
    paquetes de otras fuentes pasan intactos."""
    seen = set(known or ())
    best: dict[str, Package] = {}
    order: List[object] = []
    for pkg in packages:
        if pkg.source != PackageSource.XBPS:
            order.append(pkg)
            continue
        k = _key(pkg)
        if k in seen:
            continue
        if k not in best:
            best[k] = pkg
            order.append(k)
        elif _score(pkg) < _score(best[k]):
            best[k] = pkg
    return [best[o] if isinstance(o, str) else o for o in order]


def xbps_keys(packages: Iterable[Package]) -> Set[str]:
    return {_key(p) for p in packages if p.source == PackageSource.XBPS}
