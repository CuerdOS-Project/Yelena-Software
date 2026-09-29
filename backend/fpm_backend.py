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


def supports_nissa() -> bool:
    """True only when the installed FPM binary speaks the search protocol."""
    binary = fpm_path()
    return bool(binary and _supports_nissa(binary))


def supports_nissa_v2() -> bool:
    """True when FPM exposes inventory, update, and package-detail calls."""
    binary = fpm_path()
    return bool(binary and _supports_nissa_v2(binary))


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
    rows = _parse_result_stream(messages, "search", version) or []
    packages: list[Package] = []
    for payload in rows[:max(0, limit)]:
        name = str(payload.get("name") or "").strip()
        if not name:
            return []
        installed = payload.get("installed") is True
        packages.append(Package(
            id=f"xbps:{name}",
            name=name,
            summary=str(payload.get("summary") or f"{name} package"),
            description=str(payload.get("summary") or ""),
            version=str(payload.get("version") or ""),
            source=PackageSource.XBPS,
            status=PackageStatus.INSTALLED if installed else PackageStatus.NOT_INSTALLED,
            icon_name=name,
            category="Other",
            pkgname=name,
        ))
    return packages


def search(query: str, limit: int = 5000) -> list[Package]:
    """Search XBPS catalog through FPM's sibling-call NISSA protocol."""
    query = query.strip()
    if not query:
        return []
    binary = fpm_path()
    if not binary:
        return []
    version = _V2 if _supports_nissa_v2(binary) else _V1
    messages = _request(version, "search", query)
    return _packages_from_search(messages, limit, version)


def _packages_from_search(messages: list[dict[str, Any]], limit: int, version: int) -> list[Package]:
    rows = _parse_result_stream(messages, "search", version)
    packages: list[Package] = []
    for payload in rows[:max(0, limit)]:
        name = str(payload.get("name") or "").strip()
        if not name:
            return []
        installed = payload.get("installed") is True
        packages.append(Package(
            id=f"xbps:{name}",
            name=name,
            summary=str(payload.get("summary") or f"{name} package"),
            description=str(payload.get("summary") or ""),
            version=str(payload.get("version") or ""),
            source=PackageSource.XBPS,
            status=PackageStatus.INSTALLED if installed else PackageStatus.NOT_INSTALLED,
            icon_name=name,
            category="Other",
            pkgname=name,
        ))
    return packages


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
            "repo": "XBPS",
            "icon_name": str(row.get("name") or ""),
        }
        for row in rows if row.get("name")
    ]


def get_details(pkg_name: str) -> Optional[Package]:
    """Fetch structured repository and installed metadata for an XBPS package."""
    messages = _request(_V2, "info", pkg_name)
    rows = _parse_result_stream(messages, "info", _V2)
    if rows is None or len(rows) != 1:
        return None
    payload = rows[0]
    if not payload.get("name"):
        return None
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
        pkgname=str(payload["name"]),
    )


def xbps_available() -> bool:
    """Search still targets XBPS, so retain the source availability check."""
    from . import xbps_backend
    return xbps_backend.xbps_available()


# Explicit aliases make the supported query scope clear to callers.
search_packages = search
fpm_available = is_available

__all__ = [
    "fpm_path", "supports_nissa", "supports_nissa_v2", "is_available",
    "fpm_available", "search", "search_packages", "list_installed",
    "list_upgradable", "get_updates", "get_details", "xbps_available",
]
