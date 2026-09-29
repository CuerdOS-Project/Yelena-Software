# yl-soft — Sincronización de repos compartida
#
# Centraliza la sincronización de índices y el recálculo de actualizaciones
# pendientes, tanto para el usuario como para el chequeo automático.

from __future__ import annotations

import logging
from typing import Callable, List, Optional, Tuple

logger = logging.getLogger(__name__)


def detect_pkg_mgr() -> str:
    try:
        from .system_detect import detect_package_manager
        return detect_package_manager()
    except Exception:
        return "unknown"


def _selected_xbps_backend():
    from .package_engine import get_updates_backend
    return get_updates_backend()


def _persist_snapshot(updates: List[dict], pkg_mgr: str) -> List[dict]:
    """Replace both supported sources for a full XBPS-system snapshot.

    Flatpak can be independently enabled while XBPS is unavailable, in which
    case only its own rows are replaced. An empty source result is meaningful
    and clears stale update rows; query failures are handled before this helper.
    """
    from .updates_db import updates_db
    managers = ("xbps", "flatpak") if pkg_mgr == "xbps" else ("flatpak",)
    updates_db.save_pending(updates, replace_managers=managers)
    result, _ = updates_db.load_pending()
    return result


def refresh_pending_updates_local() -> Tuple[bool, List[dict], str]:
    """Recalculate pending XBPS + Flatpak updates without refreshing repos.

    This uses the already synchronized local repository metadata and does not
    prompt for administrator credentials. Returns ``(ok, updates, error)``.
    """
    pkg_mgr = detect_pkg_mgr()
    updates: List[dict] = []

    try:
        if pkg_mgr == "xbps":
            updates.extend(_selected_xbps_backend().get_updates())

        try:
            from .flatpak_backend import is_flatpak_available, get_flatpak_updates
            if is_flatpak_available():
                updates.extend(get_flatpak_updates())
        except Exception:
            pass

        try:
            updates = _persist_snapshot(updates, pkg_mgr)
        except Exception as exc:
            logger.warning("repo_sync: no se pudo guardar en updates_db: %s", exc)

        return True, updates, ""
    except Exception as exc:
        logger.error("repo_sync.refresh_pending_updates_local: %s", exc)
        return False, [], str(exc)


def sync_repos_and_refresh(
    is_cancelled: Optional[Callable[[], bool]] = None,
    on_line: Optional[Callable[[str], None]] = None,
) -> Tuple[bool, List[dict], str]:
    """Refresh XBPS repository indexes, then save a full updates snapshot."""
    pkg_mgr = detect_pkg_mgr()
    updates: List[dict] = []

    try:
        if pkg_mgr == "xbps":
            from . import xbps_backend as xbps_be

            for kind, payload in xbps_be.refresh_repos_stream(is_cancelled=is_cancelled):
                if kind == "line":
                    if on_line:
                        on_line(payload)
                elif kind == "error":
                    msg = getattr(payload, "message", str(payload))
                    return False, [], msg
                elif kind == "done" and not payload:
                    return False, [], "xbps-install -S failed"

            if is_cancelled and is_cancelled():
                return False, [], "cancelled"

            updates.extend(_selected_xbps_backend().get_updates())

        try:
            from .flatpak_backend import is_flatpak_available, get_flatpak_updates
            if is_flatpak_available():
                updates.extend(get_flatpak_updates())
        except Exception:
            pass

        try:
            updates = _persist_snapshot(updates, pkg_mgr)
        except Exception as exc:
            logger.warning("repo_sync: no se pudo guardar en updates_db: %s", exc)

        return True, updates, ""
    except Exception as exc:
        logger.error("repo_sync.sync_repos_and_refresh: %s", exc)
        return False, [], str(exc)
