# repositorio. Este módulo lee data/provides_xbps.kn (recargable en caliente,

from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import List, Optional, Tuple

_DATA_DIR = Path(__file__).parent.parent / "data"
_PROVIDES_XBPS_PATH = _DATA_DIR / "provides_xbps.kn"

_xbps_rules_name: List[str] = []
_xbps_loaded = False
_xbps_mtime: Optional[float] = None


def _parse_file(path: Path) -> List[str]:
    names: List[str] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("name:"):
                    p = line[5:].strip().lower()
                    if p:
                        names.append(p)
    except OSError as e:
        print(f"[provides] Could not read {path}: {e}")
    return names


def _load_xbps() -> None:
    global _xbps_loaded, _xbps_mtime, _xbps_rules_name

    if not _PROVIDES_XBPS_PATH.exists():
        _xbps_loaded = True
        return

    try:
        mtime = _PROVIDES_XBPS_PATH.stat().st_mtime
    except OSError:
        _xbps_loaded = True
        return

    if _xbps_loaded and mtime == _xbps_mtime:
        return

    _xbps_mtime = mtime
    _xbps_rules_name = _parse_file(_PROVIDES_XBPS_PATH)
    _xbps_loaded = True


def is_priority_xbps(name: str) -> bool:
    """True si *name* debe actualizarse antes del resto del sistema (XBPS)."""
    _load_xbps()
    if not _xbps_rules_name:
        return False
    n = name.lower()
    return any(fnmatch.fnmatch(n, p) for p in _xbps_rules_name)


def split_priority_xbps(names: List[str]) -> Tuple[List[str], List[str]]:
    """
    Separa *names* en (prioritarios, resto) según provides_xbps.kn.
    Los prioritarios deben instalarse en una pasada separada ANTES del resto.
    """
    _load_xbps()
    if not _xbps_rules_name:
        return [], list(names)
    priority = [n for n in names if is_priority_xbps(n)]
    rest = [n for n in names if not is_priority_xbps(n)]
    return priority, rest


def provides_xbps_path() -> Path:
    return _PROVIDES_XBPS_PATH
