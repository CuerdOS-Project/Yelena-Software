# Vera_Shop — XBPS backend (Void Linux)

from __future__ import annotations

import re
import shutil
import subprocess
from typing import Iterator, List, Optional

from .models import Package, PackageSource, PackageStatus, Task, TaskType, TaskState

_XBPS_QUERY   = shutil.which("xbps-query")
_XBPS_INSTALL = shutil.which("xbps-install")
_XBPS_REMOVE  = shutil.which("xbps-remove")


def xbps_available() -> bool:
    return bool(_XBPS_QUERY)


# Icon hints

_ICON_MAP: dict[str, str] = {
    "firefox":      "firefox",
    "chromium":     "chromium",
    "vlc":          "vlc",
    "gimp":         "gimp",
    "inkscape":     "inkscape",
    "libreoffice":  "libreoffice-writer",
    "code":         "com.visualstudio.code",
    "vim":          "vim",
    "neovim":       "nvim",
    "git":          "git",
    "python3":      "python3",
    "nodejs":       "nodejs",
    "obs":          "com.obsproject.Studio",
    "blender":      "blender",
    "krita":        "krita",
    "htop":         "utilities-system-monitor",
    "bash":         "utilities-terminal",
    "zsh":          "utilities-terminal",
    "curl":         "network-workgroup",
    "wget":         "network-workgroup",
    "mpv":          "mpv",
    "thunderbird":  "thunderbird",
    "telegram":     "telegram",
    "steam":        "steam",
    "wine":         "wine",
}


def _guess_icon(name: str) -> str:
    """Ícono de tema más probable para el paquete.

    Antes, cualquier nombre que no estuviera en _ICON_MAP (un puñado de
    alias a mano) caía directo al ícono genérico — es decir, casi todos
    los paquetes instalados terminaban mostrando el mismo engranaje sin
    importar cuál fueran. La mayoría de los temas de íconos nombran el
    ícono de una app igual que su binario/paquete (p.ej. "audacity",
    "ark", "gimp", "krita"), así que probamos primero un alias conocido
    y, si no hay, el nombre real del paquete tal cual — la capa de UI
    ya sabe qué hacer si tampoco eso resuelve contra el tema instalado
    (ver AppIconWidget: cae al ícono de categoría y por último al
    genérico)."""
    lower = name.lower()
    for k, v in _ICON_MAP.items():
        if k in lower:
            return v
    return name


# Íconos de respaldo por categoría (se guardan como tag "_category_icon:"
# y AppIconWidget los usa si ni el alias ni el nombre real del paquete
# están en el tema de íconos instalado — mismo mecanismo que ya usa
# flatpak_backend.py).
_CATEGORY_ICONS: dict[str, str] = {
    "Internet":      "network-workgroup",
    "Multimedia":    "multimedia-player",
    "Graphics":      "applications-graphics",
    "Games":         "applications-games",
    "Development":   "utilities-terminal",
    "Utilities":     "utilities-file-archiver",
    "System":        "applications-system",
    "Science":       "applications-science",
    "Education":     "applications-science",
    "Libraries":     "application-x-executable",
    "Other":         "application-x-executable",
}


def _category_icon_tag(category: str) -> str:
    icon = _CATEGORY_ICONS.get(category, "application-x-executable")
    return f"_category_icon:{icon}"


# Category mapping

_SECTION_MAP: dict[str, str] = {
    "www":           "Internet",
    "network":       "Internet",
    "mail":          "Internet",
    "multimedia":    "Multimedia",
    "audio":         "Multimedia",
    "video":         "Multimedia",
    "graphics":      "Graphics",
    "games":         "Games",
    "game":          "Games",
    "devel":         "Development",
    "development":   "Development",
    "editors":       "Development",
    "utils":         "Utilities",
    "utilities":     "Utilities",
    "system":        "System",
    "base-system":   "System",
    "science":       "Science",
    "education":     "Education",
    "fonts":         "Other",
    "libs":          "Libraries",
}


def _map_section(section: str) -> str:
    key = section.lower().strip()
    return _SECTION_MAP.get(key, "Other")


# Parsing

def _parse_xbps_show(output: str, pkg_name: str) -> Optional[Package]:
    """Parse `xbps-query -S <pkg>` output into a Package."""
    fields: dict[str, str] = {}
    for line in output.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            fields[k.strip().lower()] = v.strip()

    if not fields:
        return None

    name = fields.get("pkgname", pkg_name)
    version = fields.get("pkgver", "").split("-")[-1] if fields.get("pkgver") else ""
    summary = fields.get("short_desc", f"{name} package")
    desc = fields.get("long_desc", summary) or summary
    section = fields.get("categories", fields.get("repository", ""))
    category = _map_section(section)
    homepage = fields.get("homepage", "")
    license_ = fields.get("license", "")
    maintainer = fields.get("maintainer", "")

    size_str = fields.get("installed_size", "0")
    try:
        size_bytes = int(re.sub(r"[^0-9]", "", size_str))
    except ValueError:
        size_bytes = 0

    return Package(
        id=f"xbps:{name}",
        name=name,
        summary=summary,
        description=desc,
        version=version,
        source=PackageSource.XBPS,
        status=PackageStatus.NOT_INSTALLED,
        icon_name=_guess_icon(name),
        category=category,
        size_bytes=size_bytes,
        developer=maintainer,
        website=homepage,
        license=license_,
        tags=[_category_icon_tag(category)],
    )


# `xbps-query -l` (installed pkgs) and `xbps-query -Rs` (repo search) do NOT

def _parse_prop_line(line: str, props: List[str]) -> Optional[List[str]]:
    """Divide una línea de salida de `xbps-query -p a,b,c -l` en sus
    columnas. xbps-query separa las propiedades con tabulador; si el
    tabulador no está presente (versiones/locales distintas) probamos con
    2+ espacios como separador de respaldo."""
    if not line.strip():
        return None
    parts = line.split("\t")
    if len(parts) < len(props):
        parts = re.split(r"\s{2,}", line.strip())
    if len(parts) < len(props):
        return None
    return [p.strip() for p in parts[:len(props)]]


def _installed_props_map(props: List[str]) -> dict[str, dict[str, str]]:
    """Devuelve {pkgname: {prop: value}} para TODOS los paquetes instalados,
    con una sola llamada a xbps-query (sin importar cuántos paquetes haya
    instalados). `props` debe incluir "pkgname"."""
    if not _XBPS_QUERY:
        return {}
    if "pkgname" not in props:
        props = ["pkgname"] + list(props)
    try:
        # Repetir "-p" una vez por propiedad NO acumula: cada "-p" pisa al
        cmd = [_XBPS_QUERY, "-p", ",".join(props), "-l"]
        r = subprocess.run(
            cmd,
            capture_output=True, text=True, timeout=15,
        )
    except Exception:
        return {}

    result: dict[str, dict[str, str]] = {}
    for line in r.stdout.splitlines():
        cols = _parse_prop_line(line, props)
        if not cols:
            continue
        values = dict(zip(props, cols))
        name = values.get("pkgname")
        if name:
            result[name] = values
    return result


def _repo_props_map(props: List[str]) -> dict[str, dict[str, str]]:
    """Igual que `_installed_props_map` pero consultando los repositorios
    remotos (paquetes disponibles, instalados o no), también con una sola
    llamada a xbps-query."""
    if not _XBPS_QUERY:
        return {}
    if "pkgname" not in props:
        props = ["pkgname"] + list(props)
    try:
        # Ver nota en _installed_props_map: una sola "-p" con las
        # propiedades separadas por comas.
        cmd = [_XBPS_QUERY, "-R", "-p", ",".join(props), "-l"]
        r = subprocess.run(
            cmd,
            capture_output=True, text=True, timeout=25,
        )
    except Exception:
        return {}

    result: dict[str, dict[str, str]] = {}
    for line in r.stdout.splitlines():
        cols = _parse_prop_line(line, props)
        if not cols:
            continue
        values = dict(zip(props, cols))
        name = values.get("pkgname")
        if name:
            result[name] = values
    return result


def _size_from(values: Optional[dict[str, str]], *keys: str) -> int:
    """Extrae un tamaño en bytes de las primeras claves disponibles."""
    if not values:
        return 0
    for key in keys:
        raw = values.get(key, "")
        digits = re.sub(r"[^0-9]", "", raw)
        if digits:
            return int(digits)
    return 0


# Status check

def _check_installed(pkg_name: str) -> PackageStatus:
    if not _XBPS_QUERY:
        return PackageStatus.NOT_INSTALLED
    try:
        r = subprocess.run(
            [_XBPS_QUERY, pkg_name],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0 and "state: installed" in r.stdout.lower():
            return PackageStatus.INSTALLED
    except Exception:
        pass
    return PackageStatus.NOT_INSTALLED


# Public API

def search(query: str, limit: int = 5000) -> List[Package]:
    """Search XBPS repository for packages matching *query*."""
    if not _XBPS_QUERY:
        return []
    try:
        result = subprocess.run(
            [_XBPS_QUERY, "-Rs", query],
            capture_output=True, text=True, timeout=15,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []

    # Output format: [-] pkgname-version  short description
    packages: List[Package] = []
    for line in result.stdout.strip().splitlines()[:limit]:
        # e.g. "[-] vim-9.0.0472_1  Vi IMproved, a highly configurable text editor"
        m = re.match(r"^\[([^\]]*)\]\s+(\S+?)-(\d\S*)\s+(.*)", line)
        if not m:
            # fallback for simpler format
            parts = line.split(None, 2)
            if len(parts) < 2:
                continue
            installed_flag = parts[0]
            name_ver = parts[1]
            summary = parts[2] if len(parts) > 2 else ""
            name_parts = name_ver.rsplit("-", 1)
            name = name_parts[0]
            version = name_parts[1] if len(name_parts) > 1 else ""
            is_installed = installed_flag == "[*]"
        else:
            installed_flag, name, version, summary = m.group(1), m.group(2), m.group(3), m.group(4)
            is_installed = installed_flag == "*"

        status = PackageStatus.INSTALLED if is_installed else PackageStatus.NOT_INSTALLED
        category = "Other"
        packages.append(Package(
            id=f"xbps:{name}",
            name=name,
            summary=summary.strip(),
            description=summary.strip(),
            version=version,
            source=PackageSource.XBPS,
            status=status,
            icon_name=_guess_icon(name),
            category=category,
            tags=[_category_icon_tag(category)],
        ))

    # No hacer aquí _repo_props_map(): esa llamada con `xbps-query -R -l`
    # recorre todos los paquetes del repositorio remoto y puede tardar mucho.
    # La búsqueda debe emitir los resultados en cuanto termina `-Rs`; el tamaño
    # es un dato secundario y queda en 0 para que la UI simplemente lo omita.
    # Si se necesita, debe consultarse bajo demanda en la pantalla de detalle.
    return packages


def get_details(pkg_name: str) -> Optional[Package]:
    """Fetch full details for a single XBPS package."""
    if not _XBPS_QUERY:
        return None
    try:
        result = subprocess.run(
            [_XBPS_QUERY, "-S", pkg_name],
            capture_output=True, text=True, timeout=10,
        )
        pkg = _parse_xbps_show(result.stdout, pkg_name)
        if pkg:
            pkg.status = _check_installed(pkg_name)
        return pkg
    except Exception:
        return None


def list_installed() -> List[Package]:
    """Return all installed XBPS packages."""
    if not _XBPS_QUERY:
        return []
    try:
        r = subprocess.run(
            [_XBPS_QUERY, "-l"],
            capture_output=True, text=True, timeout=30,
        )
    except Exception:
        return []

    packages = []
    for line in r.stdout.splitlines():
        # ii name-version  description
        m = re.match(r"^ii\s+(\S+?)-(\d\S*)\s+(.*)", line)
        if not m:
            continue
        name, version, desc = m.group(1), m.group(2), m.group(3)
        category = "Other"
        packages.append(Package(
            id=f"xbps:{name}",
            name=name,
            summary=desc.strip(),
            version=version,
            source=PackageSource.XBPS,
            status=PackageStatus.INSTALLED,
            icon_name=_guess_icon(name),
            category=category,
            tags=[_category_icon_tag(category)],
        ))

    # Una única consulta extra (no una por paquete) para rellenar el tamaño
    # de TODOS los paquetes instalados de golpe.
    if packages:
        size_map = _installed_props_map(["pkgname", "installed_size"])
        for pkg in packages:
            pkg.size_bytes = _size_from(size_map.get(pkg.name), "installed_size")
    return packages


def list_upgradable() -> List[Package]:
    """Check for available XBPS updates (dry-run)."""
    if not _XBPS_INSTALL:
        return []
    try:
        r = subprocess.run(
            [_XBPS_INSTALL, "-un"],
            capture_output=True, text=True, timeout=20,
        )
    except Exception:
        return []

    packages = []
    for line in r.stdout.splitlines():
        # e.g. "Name-1.2.3_1 update ..."
        parts = line.split()
        if len(parts) < 1:
            continue
        name_ver = parts[0]
        name_parts = name_ver.rsplit("-", 1)
        name = name_parts[0]
        category = "Other"
        packages.append(Package(
            id=f"xbps:{name}",
            name=name,
            summary="Update available",
            source=PackageSource.XBPS,
            status=PackageStatus.UPDATE_AVAILABLE,
            icon_name=_guess_icon(name),
            category=category,
            tags=[_category_icon_tag(category)],
        ))
    return packages


def run_task(task: Task) -> Iterator[tuple[int, str]]:
    """
    Execute an XBPS task (install/remove/update/refresh).
    Yields (progress_percent, status_text) tuples.
    """
    from .agent_yelena import authorize, build_xbps_command

    from .admin_session import should_start_authentication

    # `name` es el nombre "bonito" para la UI; el identificador real que
    pkg_name = task.package.pkgname or task.package.name

    if task.task_type == TaskType.INSTALL:
        operation = "install"
        cmd = build_xbps_command("install", ["-y", pkg_name])
    elif task.task_type == TaskType.REMOVE:
        operation = "remove"
        cmd = build_xbps_command("remove", ["-y", pkg_name])
    elif task.task_type == TaskType.UPDATE:
        operation = "install"
        cmd = build_xbps_command("install", ["-yu", pkg_name])
    elif task.task_type == TaskType.REFRESH:
        operation = "install"
        cmd = build_xbps_command("install", ["-S"])
    else:
        return

    if task.task_type == TaskType.REMOVE:
        yield (0, f"Preparando desinstalación de «{pkg_name}»…")
    else:
        yield (0, f"Starting {task.task_type.value}…")

    from .xbps_error_handler import check_xbps_health
    health_issue = check_xbps_health()
    if health_issue is not None:
        task.state = TaskState.FAILED
        task.error_message = health_issue.message
        yield (100, f"Error: {health_issue.message}")
        return

    try:
        # Autoriza el proceso antes de lanzar pkexec para permitir que polkit
        # reutilice auth_admin_keep en operaciones posteriores.
        if should_start_authentication():
            authorize(operation)
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
    except FileNotFoundError as e:
        yield (100, f"Error: {e}")
        task.state = TaskState.FAILED
        task.error_message = str(e)
        return

    progress = 5
    is_remove = task.task_type == TaskType.REMOVE

    for line in proc.stdout:  # type: ignore
        line = line.rstrip()
        task.log_lines.append(line)

        if is_remove:
            # Mensajes específicos de desinstalación XBPS
            line_lower = line.lower()
            if "removing" in line_lower or "uninstalling" in line_lower:
                progress = min(progress + 12, 55)
                # xbps imprime "Removing `pkg-x.y_1' ..."
                pkg_display = pkg_name
                if "`" in line:
                    try:
                        pkg_display = line.split("`")[1].split("'")[0]
                    except IndexError:
                        pass
                yield (progress, f"Desinstalando paquete «{pkg_display}»…")
                continue
            elif "purging" in line_lower:
                progress = min(progress + 8, 70)
                yield (progress, "Eliminando archivos de configuración…")
                continue
            elif "running" in line_lower and "post-remove" in line_lower:
                progress = min(progress + 6, 80)
                yield (progress, "Ejecutando scripts de post-desinstalación…")
                continue
            elif "obsolete" in line_lower or "removing obsolete" in line_lower:
                progress = min(progress + 4, 85)
                yield (progress, "Eliminando entradas obsoletas…")
                continue
        else:
            if "Downloading" in line:
                progress = min(progress + 4, 60)
            elif "Unpacking" in line:
                progress = min(progress + 5, 80)
            elif "Configuring" in line:
                progress = min(progress + 5, 90)
            elif "installed" in line.lower():
                progress = min(progress + 3, 95)

        yield (progress, line)

    proc.wait()
    if proc.returncode == 0:
        if is_remove:
            # Informar actualización de la base de datos XBPS
            yield (90, f"Paquete «{pkg_name}» desinstalado. Actualizando base de datos…")
            # Sincronizar índices XBPS para reflejar que el paquete ya no existe
            if _XBPS_INSTALL:
                try:
                    subprocess.run(
                        [_XBPS_INSTALL, "-S"],
                        capture_output=True, timeout=15,
                    )
                except Exception:
                    pass
            yield (97, "Base de datos de paquetes XBPS actualizada.")
        yield (100, "Done.")
        task.state = TaskState.DONE
    else:
        from .xbps_error_handler import classify_error
        tail = "\n".join(task.log_lines[-15:])
        xbps_err = classify_error(tail, proc.returncode)
        task.state = TaskState.FAILED
        task.error_message = xbps_err.message
        yield (100, f"Failed (exit {proc.returncode}): {xbps_err.message}")


# Compatibility helpers (previously in backend_xbps.py)

def _get_xbps_installed_version(pkg_name: str) -> str:
    """Devuelve la versión instalada de un paquete XBPS (sin el nombre)."""
    if not _XBPS_QUERY:
        return "—"
    try:
        r = subprocess.run(
            [_XBPS_QUERY, "-p", "pkgver", pkg_name],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            pkgver = r.stdout.strip()   # "firefox-120.0_1"
            if "-" in pkgver:
                return pkgver.rsplit("-", 1)[1]
            return pkgver or "—"
    except Exception:
        pass
    return "—"


def _get_xbps_arch(pkg_name: str) -> str:
    """Devuelve la arquitectura de un paquete XBPS instalado."""
    if _XBPS_QUERY:
        try:
            r = subprocess.run(
                [_XBPS_QUERY, "-p", "architecture", pkg_name],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip()
        except Exception:
            pass
    # Fallback: arquitectura del sistema
    try:
        import platform
        return platform.machine()
    except Exception:
        return ""


def get_updates() -> List[dict]:
    """
    Devuelve actualizaciones pendientes como lista de dicts.

    `xbps-install -un` lista los paquetes que SE INSTALARÁN (versión NUEVA).
    Formatos posibles:
      Columnar (xbps >= 0.59):  Name\\tVersion\\tArch\\t...
        firefox  121.0_1  x86_64  ...
      Pkgver antiguo:
        firefox-121.0_1  ...

    La versión ACTUALMENTE INSTALADA y la arquitectura se obtienen con una
    consulta por paquete (xbps-query -p pkgver / -p architecture). El
    intento anterior de resolverlas de golpe con "-p prop1,prop2,..." no
    devolvía resultados fiables en todas las versiones/formatos de salida
    de xbps-query, así que se usa aquí el método simple y ya verificado.
    """
    if not _XBPS_INSTALL:
        return []
    try:
        r = subprocess.run(
            [_XBPS_INSTALL, "-un"],
            capture_output=True, text=True, timeout=20,
        )
    except Exception:
        return []

    # Patrón para detectar "nombre-version_rev" (formato pkgver antiguo)
    _pkgver_re = re.compile(r'^(.+?)-(\d\S+)$')

    updates = []
    for line in r.stdout.splitlines():
        parts = line.split()
        if not parts:
            continue

        first = parts[0]

        # Saltar líneas de cabecera o separadores
        if first in ("Name", "---", "=====") or first.startswith("-"):
            continue

        m = _pkgver_re.match(first)
        if m:
            # Formato pkgver: "firefox-121.0_1" → name="firefox", new="121.0_1"
            name    = m.group(1)
            new_ver = m.group(2)
        else:
            # Formato columnar: parts[0]=name, parts[1]=new_version
            name    = first
            new_ver = parts[1] if len(parts) > 1 else "Available"

        if not name:
            continue

        # Versión instalada actualmente (consulta individual, fiable)
        cur_ver = _get_xbps_installed_version(name)

        # Arquitectura del paquete instalado
        arch = _get_xbps_arch(name)

        updates.append({
            "name":            name,
            "current_version": cur_ver,
            "new_version":     new_ver,
            "type":            "xbps",
            "arch":            arch,
            "download_size":   0,
            "install_size":    0,
            "repo":            "XBPS",
            "icon_name":       _guess_icon(name),
        })

    return updates


# Refresh de repos con manejo de errores y reintentos

def refresh_repos_stream(max_retries: int = 2, retry_delay: float = 2.5,
                          is_cancelled=None):
    """
    Ejecuta `xbps-install -S` (sincroniza el índice/base de datos de los
    repos, con privilegios) emitiendo cada línea de salida en vivo, igual
    que antes, pero con dos mejoras:

    1. Comprueba la salud de XBPS (binarios presentes, DB no bloqueada)
       ANTES de lanzar el proceso, para dar un mensaje claro en vez de un
       fallo genérico.
    2. Si el intento falla por un motivo TRANSITORIO (red caída,
       repositorio bloqueado por otro proceso), reintenta automáticamente
       hasta *max_retries* veces con una pequeña espera entre intentos,
       en vez de rendirse al primer fallo de red.

    Yields: ("line", text) por cada línea de salida, o
            ("done", True/False) al terminar, o
            ("error", XbpsError) si falla definitivamente.

    *is_cancelled* es un callable opcional que, si devuelve True, corta
    el bucle de reintentos (para respetar la cancelación del usuario).
    """
    import time as _time
    from .xbps_error_handler import check_xbps_health, classify_error, cancelled_error
    from .agent_yelena import authorize, build_xbps_command

    health_issue = check_xbps_health()
    if health_issue is not None:
        yield ("error", health_issue)
        return

    if not _XBPS_INSTALL:
        from .xbps_error_handler import XbpsError, ERR_NOT_FOUND
        yield ("error", XbpsError(
            code=ERR_NOT_FOUND,
            message="No se encontró xbps-install en el sistema.",
        ))
        return

    # Sincronizar el índice de repos (`-S`) escribe en /var/db/xbps, que
    # normalmente pertenece a root. Sin elevar privilegios este comando
    # fallaba en silencio (permiso denegado) y por eso los repos nunca se
    # sincronizaban de verdad: solo se recalculaban updates sobre el
    # índice local ya desactualizado. Se usa el mismo mecanismo
    # (pkexec/doas/sudo…) que el resto de operaciones xbps de la app.
    #
    # Se usa `-S` a secas (no `-Sun`) porque lo único que debe hacer este
    # paso es refrescar la base de datos de paquetes de los repos
    # (`/var/db/xbps/*-repodata`). El cálculo de qué paquetes tienen
    # actualización pendiente se hace aparte, sin privilegios, con
    # `xbps-install -un` (ver get_updates), una vez el índice ya está
    # sincronizado.
    cmd = build_xbps_command("install", ["-S"])

    attempt = 0
    last_tail: list[str] = []
    while True:
        attempt += 1
        last_tail = []
        try:
            authorize("install")
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, start_new_session=True,
            )
        except Exception as exc:
            yield ("error", classify_error(str(exc)))
            return

        for line in iter(proc.stdout.readline, ""):
            if is_cancelled and is_cancelled():
                try:
                    proc.terminate()
                except Exception:
                    pass
                yield ("error", cancelled_error())
                return
            clean = line.rstrip()
            yield ("line", clean)
            if clean:
                last_tail.append(clean)
                del last_tail[:-20]

        proc.wait()
        if proc.returncode == 0:
            yield ("done", True)
            return

        xbps_err = classify_error("\n".join(last_tail), proc.returncode)
        if xbps_err.retryable and attempt <= max_retries:
            yield ("line", f"⚠ {xbps_err.message} — reintentando "
                            f"({attempt}/{max_retries})…")
            _time.sleep(retry_delay)
            continue

        yield ("error", xbps_err)
        return


def build_install_cmds(updates: List[dict]) -> List[List[str]]:
    """
    Construye el comando xbps-install (SIN pkexec/sudo) para instalar
    TODAS las actualizaciones pendientes como una única actualización
    completa del sistema (`-Syu`: sincroniza repos + actualiza todo lo
    instalado), en vez de enumerar paquete por paquete.

    Se devuelve un único comando a propósito (antes podía haber una
    pasada previa para paquetes "prioritarios"): quien ejecuta esto
    (`_run_commands` en updates_page.py) agrupa varios comandos root en
    un `sh -c "cmd1 && cmd2"` para pedir la contraseña una sola vez, y
    ese envoltorio hace que pkexec deje de reconocer el binario concreto
    (xbps-install) y muestre el diálogo de autenticación genérico en vez
    del de agent-yelena. Con un solo comando, pkexec se invoca
    directamente sobre xbps-install y respeta la política/el mensaje
    configurados para agent-yelena.

    Importante: este comando se devuelve "en crudo" (sin privilegio)
    porque quien lo ejecuta ya lo envuelve UNA sola vez con
    pkexec/sudo/doas para pedir la contraseña una única vez.
    """
    xbps_install = shutil.which("xbps-install") or "xbps-install"
    has_xbps_updates = any(u.get("type") == "xbps" for u in updates)
    if not has_xbps_updates:
        return []
    return [[xbps_install, "-y", "-Syu"]]


def build_install_selected_cmds(packages: List[str]) -> List[List[str]]:
    """
    Construye comandos xbps-install (SIN pkexec/sudo) para los paquetes
    indicados. Igual que build_install_cmds, respeta el orden de
    prioridad de data/provides_xbps.kn. Ver la nota de build_install_cmds
    sobre por qué NO se envuelven aquí con privilegios.
    """
    from . import provides as _provides

    xbps_install = shutil.which("xbps-install") or "xbps-install"
    if not packages:
        return []

    priority, rest = _provides.split_priority_xbps(packages)
    cmds: List[List[str]] = []
    if priority:
        cmds.append([xbps_install, "-y"] + priority)
    if rest:
        cmds.append([xbps_install, "-y"] + rest)
    return cmds
