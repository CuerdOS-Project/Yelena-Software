# Yelena Software — FPM NISSA client
#
# FPM remains a separate sibling program. NISSA v1 provides repository search;
# NISSA v2 adds read-only installed inventory, pending updates, and package
# details. Mutating operations still run through Yelena's existing polkit
# authorization bridge so the UI never falls back to sudo or a shell.
from __future__ import annotations

import json
import shutil
import subprocess
from functools import lru_cache
from typing import Any, Optional

from .models import Package, PackageSource, PackageStatus

_PROTOCOL = "NISSA"
_V1 = 1
_V2 = 2
_V2_CAPABILITIES = {"search", "installed", "updates", "info"}
_AM_REPOSITORY = "am-FUSION"


def _screenshots(payload: dict[str, Any]) -> list[str]:
    value = payload.get("screenshots") or []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            value = [part.strip() for part in value.split(",")]
    if not isinstance(value, (list, tuple)):
        return []
    urls: list[str] = []
    for entry in value:
        if isinstance(entry, dict):
            url = (entry.get("imgDesktopUrl") or entry.get("imgMobileUrl")
                   or entry.get("url") or entry.get("source") or "")
        else:
            url = entry
        url = str(url or "").strip()
        if url.startswith(("https://", "http://")) and url not in urls:
            urls.append(url)
        if len(urls) >= 5:
            break
    return urls


def fpm_path() -> Optional[str]:
    """Resolve the actual FPM executable currently available in PATH."""
    return shutil.which("fpm")


def _hello(binary: str, version: int) -> dict[str, Any] | None:
    try:
        result = subprocess.run(
            [binary, "nissa", f"v{version}", "hello"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
            close_fds=True,
        )
        if result.returncode != 0:
            return None
        message = json.loads(result.stdout.strip())
        if (not isinstance(message, dict)
                or message.get("protocol") != _PROTOCOL
                or message.get("version") != version
                or message.get("signal") != "hello"
                or message.get("ok") is not True):
            return None
        payload = message.get("payload")
        if not isinstance(payload, dict) or not isinstance(payload.get("capabilities"), list):
            return None
        return message
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError, AttributeError, TypeError):
        return None


@lru_cache(maxsize=4)
def _supports_nissa(binary: str) -> bool:
    message = _hello(binary, _V1)
    return bool(message and "search" in message["payload"]["capabilities"])


@lru_cache(maxsize=4)
def _supports_nissa_v2(binary: str) -> bool:
    message = _hello(binary, _V2)
    return bool(message and _V2_CAPABILITIES.issubset(set(message["payload"]["capabilities"])))


@lru_cache(maxsize=4)
def _supports_nissa_mirrors(binary: str) -> bool:
    message = _hello(binary, _V2)
    if not message or not _V2_CAPABILITIES.issubset(set(message["payload"]["capabilities"])):
        return False
    return "mirrors" in set(message["payload"]["capabilities"])


def supports_nissa() -> bool:
    """True only when the installed FPM binary speaks the search protocol."""
    binary = fpm_path()
    return bool(binary and _supports_nissa(binary))


def supports_nissa_v2() -> bool:
    """True when FPM exposes inventory, update, and package-detail calls."""
    binary = fpm_path()
    return bool(binary and _supports_nissa_v2(binary))


@lru_cache(maxsize=4)
def _supports_nissa_am_search(binary: str) -> bool:
    message = _hello(binary, _V2)
    if not message or not _V2_CAPABILITIES.issubset(set(message["payload"]["capabilities"])):
        return False
    return "search-am" in set(message["payload"]["capabilities"])


def supports_nissa_am_search() -> bool:
    """True when FPM NISSA v2 exposes the optional am-FUSION search source."""
    binary = fpm_path()
    return bool(binary and _supports_nissa_am_search(binary))


def supports_nissa_mirrors() -> bool:
    """True when FPM's optional NISSA v2 mirror-status query is available."""
    binary = fpm_path()
    return bool(binary and _supports_nissa_mirrors(binary))


def is_available() -> bool:
    """Compatibility alias used by source detection/settings."""
    return supports_nissa()


def _request(version: int, method: str, *args: str, timeout: int = 30) -> list[dict[str, Any]]:
    binary = fpm_path()
    if not binary or (version == _V1 and not _supports_nissa(binary)):
        return []
    if version == _V2 and not _supports_nissa_v2(binary):
        return []
    try:
        result = subprocess.run(
            [binary, "nissa", f"v{version}", method, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            close_fds=True,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    messages: list[dict[str, Any]] = []
    for raw_line in result.stdout.splitlines():
        if not raw_line.strip():
            continue
        try:
            msg = json.loads(raw_line)
        except json.JSONDecodeError:
            return []
        if (not isinstance(msg, dict) or msg.get("protocol") != _PROTOCOL
                or msg.get("version") != version):
            return []
        messages.append(msg)
    return messages


def _parse_result_stream(messages: list[dict[str, Any]], event: str,
                         version: int | None = None) -> list[dict[str, Any]] | None:
    records: list[dict[str, Any]] = []
    done = False
    for signal in messages:
        if done:
            return None
        if signal.get("protocol") != _PROTOCOL:
            return None
        if version is not None and signal.get("version") != version:
            return None
        kind = signal.get("signal")
        if kind == f"{event}.result":
            payload = signal.get("payload")
            if not isinstance(payload, dict):
                return None
            records.append(payload)
        elif kind == f"{event}.error" or signal.get("ok") is False:
            return None
        elif kind == f"{event}.done":
            payload = signal.get("payload")
            if (signal.get("ok") is not True or not isinstance(payload, dict)
                    or payload.get("ok") is not True
                    or payload.get("count") != len(records)):
                return None
            done = True
            break
        else:
            return None
    return records if done else None


def _parse_search_stream(messages: list[dict[str, Any]],
                         version: int) -> list[dict[str, Any]] | None:
    """Parse a search stream, keeping successful v2 sources when another
    optional source reports `search.source_error` and the stream is partial."""
    rows: list[dict[str, Any]] = []
    done = False
    for signal in messages:
        if done or signal.get("protocol") != _PROTOCOL or signal.get("version") != version:
            return None
        kind = signal.get("signal")
        if kind == "search.source_error" and version == _V2:
            payload = signal.get("payload")
            if signal.get("ok") is not False or not isinstance(payload, dict):
                return None
            continue
        if kind == "search.result":
            payload = signal.get("payload")
            if not isinstance(payload, dict):
                return None
            rows.append(payload)
        elif kind == "search.error" or signal.get("ok") is False:
            return None
        elif kind == "search.done":
            payload = signal.get("payload")
            if (signal.get("ok") is not True or not isinstance(payload, dict)
                    or payload.get("ok") is not True
                    or payload.get("count") != len(rows)):
                return None
            done = True
            break
        else:
            return None
    return rows if done else None


def _display_am_name(name: str) -> str:
    suffix = "-appimage"
    if name.casefold().endswith(suffix):
        return name[:-len(suffix)]
    return name


def _package_from_search_row(payload: dict[str, Any], version: int) -> Optional[Package]:
    name = str(payload.get("name") or "").strip()
    if not name:
        return None
    manager = str(payload.get("manager") or "xbps").strip().lower()
    installed = payload.get("installed") is True
    summary = str(payload.get("summary") or f"{name} package")
    if manager == "am":
        repository = str(payload.get("repository") or "")
        if version != _V2 or repository != _AM_REPOSITORY:
            return None
        display_name = _display_am_name(name)
        package_version = str(payload.get("version") or "")
        try:
            size_bytes = max(0, int(payload.get("size_bytes") or 0))
        except (TypeError, ValueError):
            size_bytes = 0
        return Package(
            id=f"appimage:{_AM_REPOSITORY}:{name}",
            name=display_name,
            summary=summary,
            description=summary,
            version=package_version,
            installed_version=package_version if installed else "",
            source=PackageSource.APPIMAGE,
            status=PackageStatus.INSTALLED if installed else PackageStatus.NOT_INSTALLED,
            icon_name=display_name,
            icon_url=str(payload.get("icon_url") or ""),
            category="Other",
            repository=_AM_REPOSITORY,
            pkgname=name,
            size_bytes=size_bytes,
            screenshots=_screenshots(payload),
        )
    if manager != "xbps":
        return None
    return Package(
        id=f"xbps:{name}",
        name=name,
        summary=summary,
        description=summary,
        version=str(payload.get("version") or ""),
        source=PackageSource.XBPS,
        status=PackageStatus.INSTALLED if installed else PackageStatus.NOT_INSTALLED,
        icon_name=name,
        category="Other",
        architecture=str(payload.get("architecture") or payload.get("arch") or ""),
        repository=str(payload.get("repository") or ""),
        pkgname=name,
        screenshots=_screenshots(payload),
    )


def _parse_nissa_search(output: str, limit: int = 5000, version: int = _V1) -> list[Package]:
    messages: list[dict[str, Any]] = []
    for raw_line in output.splitlines():
        if not raw_line.strip():
            continue
        try:
            signal = json.loads(raw_line)
        except json.JSONDecodeError:
            return []
        messages.append(signal)
    rows = _parse_search_stream(messages, version) or []
    packages: list[Package] = []
    for payload in rows[:max(0, limit)]:
        package = _package_from_search_row(payload, version)
        if package is None:
            return []
        packages.append(package)
    return packages


def search(query: str, limit: int = 5000) -> list[Package]:
    """Search FPM's configured XBPS repositories and optional am-FUSION source.

    Repository selection and architecture are controlled by the active XBPS
    configuration. A malformed NISSA stream falls back to native XBPS search
    instead of being misreported as an empty catalog.
    """
    query = query.strip()
    if not query:
        return []
    binary = fpm_path()
    if not binary:
        from . import xbps_backend
        return xbps_backend.search(query, limit)
    version = _V2 if _supports_nissa_v2(binary) else _V1
    messages = _request(version, "search", query)
    packages = _packages_from_search(messages, limit, version)
    if packages is None:
        from . import xbps_backend
        return xbps_backend.search(query, limit)
    return packages


def _packages_from_search(messages: list[dict[str, Any]], limit: int,
                          version: int) -> Optional[list[Package]]:
    rows = _parse_search_stream(messages, version)
    if rows is None:
        return None
    packages: list[Package] = []
    for payload in rows[:max(0, limit)]:
        package = _package_from_search_row(payload, version)
        if package is None:
            return None
        packages.append(package)
    return packages


def search_am(query: str, limit: int = 5000) -> list[Package]:
    """Search only the AM AppImage source through NISSA v2 when enabled."""
    query = query.strip()
    if not query or not supports_nissa_am_search():
        return []
    messages = _request(_V2, "search", query)
    packages = _packages_from_search(messages, limit, _V2)
    if packages is None:
        return []
    return [pkg for pkg in packages if pkg.source == PackageSource.APPIMAGE]


def _package_from_installed(payload: dict[str, Any]) -> Optional[Package]:
    name = str(payload.get("name") or "").strip()
    if not name:
        return None
    version = str(payload.get("version") or "")
    return Package(
        id=f"xbps:{name}",
        name=name,
        summary=str(payload.get("summary") or f"{name} package"),
        description=str(payload.get("summary") or ""),
        version=version,
        installed_version=version,
        source=PackageSource.XBPS,
        status=PackageStatus.INSTALLED,
        icon_name=name,
        category="Other",
        architecture=str(payload.get("architecture") or payload.get("arch") or ""),
        repository=str(payload.get("repository") or ""),
        pkgname=name,
    )


def list_installed() -> list[Package]:
    """Read the installed XBPS inventory from FPM's NISSA v2 interface."""
    messages = _request(_V2, "installed")
    rows = _parse_result_stream(messages, "installed", _V2)
    if rows is None:
        from . import xbps_backend
        return xbps_backend.list_installed()
    packages = [_package_from_installed(row) for row in rows]
    return [pkg for pkg in packages if pkg is not None]


def list_upgradable() -> list[Package]:
    """Return the update candidates reported by FPM/ XBPS."""
    messages = _request(_V2, "updates")
    rows = _parse_result_stream(messages, "updates", _V2)
    if rows is None:
        from . import xbps_backend
        return xbps_backend.list_upgradable()
    packages: list[Package] = []
    for row in rows:
        name = str(row.get("name") or "").strip()
        if not name:
            return []
        packages.append(Package(
            id=f"xbps:{name}",
            name=name,
            summary="Update available",
            version=str(row.get("new_version") or ""),
            installed_version=str(row.get("current_version") or ""),
            source=PackageSource.XBPS,
            status=PackageStatus.UPDATE_AVAILABLE,
            icon_name=name,
            category="Other",
            architecture=str(row.get("arch") or row.get("architecture") or ""),
            repository=str(row.get("repository") or ""),
            pkgname=name,
        ))
    return packages


def get_updates() -> list[dict[str, Any]]:
    """Return update rows in the same format consumed by Yelena's updates DB."""
    messages = _request(_V2, "updates")
    rows = _parse_result_stream(messages, "updates", _V2)
    if rows is None:
        from . import xbps_backend
        return xbps_backend.get_updates()
    return [
        {
            "name": str(row.get("name") or ""),
            "current_version": str(row.get("current_version") or ""),
            "new_version": str(row.get("new_version") or "Available"),
            "type": "xbps",
            "manager": "xbps",
            "arch": str(row.get("arch") or ""),
            "download_size": 0,
            "install_size": 0,
            "repo": str(row.get("repository") or "XBPS"),
            "icon_name": str(row.get("name") or ""),
        }
        for row in rows if row.get("name")
    ]


def get_details(pkg_name: str) -> Optional[Package]:
    """Fetch structured repository and installed metadata for an XBPS package."""
    messages = _request(_V2, "info", pkg_name)
    rows = _parse_result_stream(messages, "info", _V2)
    if rows is None or len(rows) != 1:
        from . import xbps_backend
        return xbps_backend.get_details(pkg_name)
    payload = rows[0]
    if not payload.get("name"):
        from . import xbps_backend
        return xbps_backend.get_details(pkg_name)
    installed = payload.get("installed") is True
    version = str(payload.get("version") or "")
    installed_version = str(payload.get("installed_version") or "")
    return Package(
        id=f"xbps:{payload['name']}",
        name=str(payload["name"]),
        summary=str(payload.get("summary") or ""),
        description=str(payload.get("description") or payload.get("summary") or ""),
        version=version,
        installed_version=installed_version,
        source=PackageSource.XBPS,
        status=PackageStatus.INSTALLED if installed else PackageStatus.NOT_INSTALLED,
        icon_name=str(payload["name"]),
        category="Other",
        size_bytes=int(payload.get("installed_size") or 0),
        website=str(payload.get("homepage") or ""),
        license=str(payload.get("license") or ""),
        architecture=str(payload.get("architecture") or payload.get("arch") or ""),
        repository=str(payload.get("repository") or ""),
        pkgname=str(payload["name"]),
        screenshots=_screenshots(payload),
    )


def get_mirror_status() -> Optional[dict[str, Any]]:
    """Return active Void mirror/fallback metadata through optional NISSA v2.

    Older FPM builds remain fully usable: this optional query returns None
    rather than downgrading the existing NISSA v2 inventory/search support.
    """
    binary = fpm_path()
    if not binary or not _supports_nissa_mirrors(binary):
        return None
    messages = _request(_V2, "mirrors")
    rows = _parse_result_stream(messages, "mirrors", _V2)
    if rows is None or not messages:
        return None
    done = messages[-1]
    payload = done.get("payload")
    if done.get("signal") != "mirrors.done" or not isinstance(payload, dict):
        return None
    fallback_urls = payload.get("fallback_urls", [])
    if not isinstance(fallback_urls, list) or not all(isinstance(url, str) for url in fallback_urls):
        return None
    return {
        "fallback_enabled": payload.get("fallback_enabled") is True,
        "fallback_urls": fallback_urls,
        "repositories": rows,
    }


def xbps_available() -> bool:
    """Search still targets XBPS, so retain the source availability check."""
    from . import xbps_backend
    return xbps_backend.xbps_available()


# Explicit aliases make the supported query scope clear to callers.
search_packages = search
fpm_available = is_available

__all__ = [
    "fpm_path", "supports_nissa", "supports_nissa_v2", "supports_nissa_mirrors", "is_available",
    "fpm_available", "search", "search_am", "supports_nissa_am_search", "search_packages", "list_installed",
    "list_upgradable", "get_updates", "get_details", "get_mirror_status", "xbps_available",
]
