"""Package-engine selection for the XBPS-backed system package source."""

from __future__ import annotations

ENGINE_YELENA_BUDDIES = "yelena-buddies"
ENGINE_FPM = "fpm"


def configured_engine() -> str:
    """Read the saved engine name, defaulting to the existing native engine."""
    try:
        import core.settings as settings
        value = settings.get("package_engine", ENGINE_YELENA_BUDDIES).strip().lower()
    except Exception:
        value = ENGINE_YELENA_BUDDIES
    return value if value in {ENGINE_YELENA_BUDDIES, ENGINE_FPM} else ENGINE_YELENA_BUDDIES


def get_active_engine() -> str:
    """Use FPM only when its complete NISSA v2 integration is available."""
    if configured_engine() == ENGINE_FPM:
        from .fpm_backend import supports_nissa_v2
        if supports_nissa_v2():
            return ENGINE_FPM
    return ENGINE_YELENA_BUDDIES


def get_search_backend():
    """Return the configured backend for catalog searches."""
    if get_active_engine() == ENGINE_FPM:
        from . import fpm_backend
        return fpm_backend
    from . import xbps_backend
    return xbps_backend


def get_inventory_backend():
    """Return the selected engine for installed-package inventory.

    Older FPM builds without NISSA v2 do not activate the FPM selector; the
    complete search/inventory/update integration falls back to native XBPS.
    """
    if configured_engine() == ENGINE_FPM:
        from . import fpm_backend
        if fpm_backend.supports_nissa_v2():
            return fpm_backend
    from . import xbps_backend
    return xbps_backend


def get_updates_backend():
    """Return the selected backend for pending-update discovery."""
    if configured_engine() == ENGINE_FPM:
        from . import fpm_backend
        if fpm_backend.supports_nissa_v2():
            return fpm_backend
    from . import xbps_backend
    return xbps_backend


def engine_label(engine: str | None = None) -> str:
    engine = engine or get_active_engine()
    if engine == ENGINE_FPM:
        return "FPM (CuerdOS)"
    return "Yelena Buddies (nativo)"
