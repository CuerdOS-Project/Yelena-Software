# Yelena Software — Blacklist filter

from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import List, Optional

from .models import Package, PackageSource

_DATA_DIR = Path(__file__).parent.parent / "data"

# Flatpak blacklist
_BLACKLIST_PATH      = _DATA_DIR / "blacklist.kn"
# XBPS blacklist (Void Linux)
_BLACKLIST_XBPS_PATH = _DATA_DIR / "blacklist_xbps.kn"

# Parsed rule tables per blacklist file
_rules_name: List[str] = []
_rules_id:   List[str] = []
_rules_cat:  List[str] = []
_loaded      = False
_mtime: Optional[float] = None

_xbps_rules_name: List[str] = []
_xbps_loaded      = False
_xbps_mtime: Optional[float] = None


def _parse_file(path: Path) -> tuple[List[str], List[str], List[str]]:
    """Parse a .kn blacklist file and return (name_patterns, id_patterns, cat_patterns)."""
    names: List[str] = []
    ids:   List[str] = []
    cats:  List[str] = []
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
                elif line.startswith("id:"):
                    p = line[3:].strip().lower()
                    if p:
                        ids.append(p)
                elif line.startswith("cat:"):
                    c = line[4:].strip().lower()
                    if c:
                        cats.append(c)
    except OSError as e:
        print(f"[blacklist] Could not read {path}: {e}")
    return names, ids, cats


def _load() -> None:
    """Load/reload blacklist.kn (Flatpak rules)."""
    global _loaded, _mtime, _rules_name, _rules_id, _rules_cat

    if not _BLACKLIST_PATH.exists():
        _loaded = True
        return

    try:
        mtime = _BLACKLIST_PATH.stat().st_mtime
    except OSError:
        _loaded = True
        return

    if _loaded and mtime == _mtime:
        return

    _mtime = mtime
    _rules_name, _rules_id, _rules_cat = _parse_file(_BLACKLIST_PATH)
    _loaded = True


def _load_xbps() -> None:
    """Load/reload blacklist_xbps.kn (Void Linux rules)."""
    global _xbps_loaded, _xbps_mtime, _xbps_rules_name

    if not _BLACKLIST_XBPS_PATH.exists():
        _xbps_loaded = True
        return

    try:
        mtime = _BLACKLIST_XBPS_PATH.stat().st_mtime
    except OSError:
        _xbps_loaded = True
        return

    if _xbps_loaded and mtime == _xbps_mtime:
        return

    _xbps_mtime = mtime
    _xbps_rules_name, _, _ = _parse_file(_BLACKLIST_XBPS_PATH)
    _xbps_loaded = True


def _matches_any(value: str, patterns: List[str]) -> bool:
    v = value.lower()
    return any(fnmatch.fnmatch(v, p) for p in patterns)


def is_blacklisted(pkg: Package) -> bool:
    """Return True if *pkg* matches any rule in the appropriate blacklist file."""
    if pkg.source == PackageSource.XBPS:
        return _is_blacklisted_xbps(pkg)
    return _is_blacklisted_flatpak(pkg)


def _is_blacklisted_xbps(pkg: Package) -> bool:
    """Check XBPS package against blacklist_xbps.kn."""
    _load_xbps()
    if _xbps_rules_name and _matches_any(pkg.name, _xbps_rules_name):
        return True
    return False


def _is_blacklisted_flatpak(pkg: Package) -> bool:
    """Check Flatpak package against blacklist.kn."""
    _load()

    if _rules_name and _matches_any(pkg.name, _rules_name):
        return True

    if _rules_id and _matches_any(pkg.id, _rules_id):
        return True

    if _rules_id and pkg.appstream_id and _matches_any(pkg.appstream_id, _rules_id):
        return True

    # Flatpak extensions: ≥4 dot-segments in appstream_id → always block
    if pkg.appstream_id and pkg.appstream_id.count(".") >= 3:
        return True

    if _rules_cat and pkg.category.lower() in _rules_cat:
        return True

    return False


def filter_packages(packages: List[Package]) -> List[Package]:
    """Return a new list with all blacklisted packages removed."""
    return [p for p in packages if not is_blacklisted(p)]


def blacklist_path() -> Path:
    """Return the path to the Flatpak blacklist file."""
    return _BLACKLIST_PATH


def blacklist_xbps_path() -> Path:
    """Return the path to the XBPS blacklist file."""
    return _BLACKLIST_XBPS_PATH
