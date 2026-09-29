# Vera_Shop — Flatpak backend

from __future__ import annotations

import json
import shutil
import subprocess
from typing import Iterator, List, Optional
from urllib.request import urlopen, Request
from urllib.error import URLError

from .models import Package, PackageSource, PackageStatus, Task, TaskType, TaskState
import core.settings as app_settings

_FLATPAK = shutil.which("flatpak")
_FLATHUB_API = "https://flathub.org/api/v1"  # Cambiado a v1

# Pre-built curated list so the home page works without network
CURATED_IDS = [
    "org.mozilla.firefox",
    "com.spotify.Client",
    "com.discordapp.Discord",
    "org.videolan.VLC",
    "org.gimp.GIMP",
    "org.inkscape.Inkscape",
    "com.obsproject.Studio",
    "org.blender.Blender",
    "org.kde.krita",
    "com.valvesoftware.Steam",
    "net.codelogics.shotcut",
    "org.gnome.Builder",
    "org.libreoffice.LibreOffice",
    "io.github.celluloid_player.Celluloid",
    "com.github.tchx84.Flatseal",
]

CATEGORY_ICONS = {
    "AudioVideo": "multimedia-player",
    "Audio":      "audio-x-generic",
    "Video":      "video-x-generic",
    "Development":"utilities-terminal",
    "Education":  "applications-science",
    "Game":       "applications-games",
    "Graphics":   "applications-graphics",
    "Network":    "network-workgroup",
    "Office":     "libreoffice-writer",
    "Science":    "applications-science",
    "Settings":   "preferences-system",
    "System":     "applications-system",
    "Utility":    "utilities-file-archiver",
}


def _flatpak_available() -> bool:
    return bool(_FLATPAK)


def _fetch_json(url: str, timeout: int = 10) -> Optional[dict | list]:
    try:
        req = Request(url, headers={"User-Agent": "Vera_Shop/1.0"})
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except URLError as e:
        print(f"[flatpak] URLError: {e}")
        return None
    except json.JSONDecodeError as e:
        print(f"[flatpak] JSONDecodeError: {e}")
        return None


def _app_from_flathub(data: dict, status: PackageStatus = PackageStatus.NOT_INSTALLED) -> Package:
    app_id = data.get("flatpakAppId", data.get("app_id", data.get("id", "unknown")))
    name = data.get("name", app_id.split(".")[-1].capitalize())
    summary = data.get("summary", "")
    description = data.get("description", summary)
    icon_url = data.get("icon", data.get("iconUrl", ""))
    developer = data.get("developerName", data.get("developer_name", data.get("developer", "")))
    version = data.get("currentReleaseVersion", data.get("version", ""))
    categories = data.get("categories", [])
    category = categories[0] if categories else "Other"
    size_bytes = data.get("downloadSize", data.get("size", 0))
    rating = float(data.get("rating", 0))
    website = data.get("website", "")
    license_ = data.get("projectLicense", data.get("license", ""))
    screenshots = [
        s.get("imgMobileUrl", s.get("imgDesktopUrl", s.get("url", "")))
        for s in data.get("screenshots", [])[:5]
    ]
    # Flatpak exporta iconos con el app-id (p.ej. "org.mozilla.firefox") al tema
    # del sistema. Usarlo primero permite que QIcon.fromTheme lo encuentre cuando
    # está instalado. El icono de categoría se guarda como fallback en tags.
    category_icon = CATEGORY_ICONS.get(category, "application-x-executable")
    icon_name = app_id  # icono del sistema por app-id (flatpak instala estos)
    return Package(
        id=f"flatpak:{app_id}",
        name=name,
        summary=summary,
        description=description,
        version=version,
        source=PackageSource.FLATPAK,
        status=status,
        icon_name=icon_name,
        icon_url=icon_url,
        category=category,
        size_bytes=size_bytes,
        developer=developer,
        website=website,
        license=license_,
        rating=rating,
        screenshots=screenshots,
        appstream_id=app_id,
        # "_category_icon:<name>" le indica a AppIconWidget qué icono de tema
        # usar como fallback si el app-id no está en el tema del sistema aún.
        tags=[f"_category_icon:{category_icon}"],
    )


def _installed_ids() -> set[str]:
    if not _FLATPAK:
        return set()
    try:
        r = subprocess.run(
            [_FLATPAK, "list", "--app", "--columns=application"],
            capture_output=True, text=True, timeout=10,
        )
        return {line.strip() for line in r.stdout.splitlines() if line.strip()}
    except Exception:
        return set()


def _search_via_cli(query: str, limit: int = 5000) -> List[Package]:
    """Search Flatpak using CLI with explicit columns to avoid format ambiguity."""
    if not _FLATPAK:
        return []

    try:
        # Force explicit column order — never rely on default output format
        # which varies between flatpak versions and distros.
        # Columns: name | description | application ID | version
        r = subprocess.run(
            [_FLATPAK, "search",
             "--columns=name,description,application,version",
             query],
            capture_output=True, text=True, timeout=15,
        )
    except Exception as e:
        print(f"[flatpak] Error en CLI search: {e}")
        return []

    installed = _installed_ids()
    packages = []

    for line in r.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        # Skip header row
        if line.lower().startswith("name\t"):
            continue

        parts = line.split("\t")
        if len(parts) < 3:
            continue

        name        = parts[0].strip()
        description = parts[1].strip() if len(parts) > 1 else ""
        app_id      = parts[2].strip() if len(parts) > 2 else ""
        version     = parts[3].strip() if len(parts) > 3 else ""

        if not app_id or not name:
            continue

        # not user-installable applications (app_id has ≥4 dot-segments).
        if app_id.count(".") >= 3:
            continue

        # Also skip by well-known extension markers in the app_id
        _lower_id = app_id.lower()
        if any(tok in _lower_id for tok in (
            ".plugin.", ".codec.", ".locale.", ".debug.",
            ".sources.", ".baseapp.", ".extension.", ".runtime.",
        )):
            continue

        # Evitar duplicados
        if any(p.appstream_id == app_id for p in packages):
            continue

        status = PackageStatus.INSTALLED if app_id in installed else PackageStatus.NOT_INSTALLED

        icon_url = (
            f"https://dl.flathub.org/repo/appstream/x86_64/icons/128x128/{app_id}.png"
        )

        packages.append(Package(
            id=f"flatpak:{app_id}",
            name=name,
            summary=description[:120] if description else f"{name} app",
            description=description,
            version=version,
            source=PackageSource.FLATPAK,
            status=status,
            icon_name="application-x-executable",
            icon_url=icon_url,
            appstream_id=app_id,
            category="Other",
        ))

        if len(packages) >= limit:
            break

    return packages


def search(query: str, limit: int = 5000) -> List[Package]:
    """Search Flathub using CLI (most reliable)."""
    # Usar CLI directamente - es más confiable que la API
    return _search_via_cli(query, limit)


def get_featured(limit: int = 12) -> List[Package]:
    """Return featured/popular apps using curated list + CLI search."""
    installed = _installed_ids()
    packages = []
    
    # Usar lista curada de apps populares
    for app_id in CURATED_IDS[:limit]:
        # Intentar obtener detalles completos via CLI
        details = get_details(app_id)
        if details:
            details.status = PackageStatus.INSTALLED if app_id in installed else PackageStatus.NOT_INSTALLED
            packages.append(details)
        else:
            # Crear paquete básico
            name = app_id.split(".")[-1].capitalize()
            status = PackageStatus.INSTALLED if app_id in installed else PackageStatus.NOT_INSTALLED
            packages.append(Package(
                id=f"flatpak:{app_id}",
                name=name,
                summary="Available on Flathub",
                description="",
                source=PackageSource.FLATPAK,
                status=status,
                icon_name="application-x-executable",
                appstream_id=app_id,
                category="Other",
            ))
    
    return packages


def get_details(app_id: str) -> Optional[Package]:
    """Fetch full app details using flatpak info CLI."""
    if not _FLATPAK:
        return None
    
    try:
        # Obtener información del paquete
        r = subprocess.run(
            [_FLATPAK, "info", app_id],
            capture_output=True, text=True, timeout=10,
        )
        
        if r.returncode != 0:
            return None
            
        # Parsear la salida de flatpak info
        info = {}
        for line in r.stdout.splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                info[key.strip().lower()] = value.strip()
        
        name = info.get("name", app_id.split(".")[-1].capitalize())
        summary = info.get("description", info.get("title", f"{name} package"))
        version = info.get("version", "")
        
        installed = _installed_ids()
        status = PackageStatus.INSTALLED if app_id in installed else PackageStatus.NOT_INSTALLED
        
        return Package(
            id=f"flatpak:{app_id}",
            name=name,
            summary=summary[:100] if summary else f"{name} app",
            description=summary,
            version=version,
            source=PackageSource.FLATPAK,
            status=status,
            icon_name="application-x-executable",
            appstream_id=app_id,
            category="Other",
        )
    except Exception as e:
        print(f"[flatpak] Error obteniendo detalles de {app_id}: {e}")
        return None


def list_installed() -> List[Package]:
    """List all installed Flatpak applications."""
    if not _FLATPAK:
        return []
    try:
        r = subprocess.run(
            [_FLATPAK, "list", "--app",
             "--columns=application,name,version,installation"],
            capture_output=True, text=True, timeout=10,
        )
    except Exception:
        return []

    packages = []
    for line in r.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        app_id = parts[0].strip()
        name = parts[1].strip() if len(parts) > 1 else app_id
        version = parts[2].strip() if len(parts) > 2 else ""
        icon_url = (
            f"https://dl.flathub.org/repo/appstream/x86_64/icons/128x128/{app_id}.png"
        )
        packages.append(Package(
            id=f"flatpak:{app_id}",
            name=name,
            summary="",
            version=version,
            source=PackageSource.FLATPAK,
            status=PackageStatus.INSTALLED,
            icon_name="application-x-executable",
            icon_url=icon_url,
            appstream_id=app_id,
        ))
    return packages


def list_updates() -> List[Package]:
    """Check for available Flatpak updates."""
    if not _FLATPAK:
        return []
    try:
        r = subprocess.run(
            [_FLATPAK, "remote-ls", "--updates", "--app",
             "--columns=application,name,version"],
            capture_output=True, text=True, timeout=20,
        )
    except Exception:
        return []

    packages = []
    for line in r.stdout.splitlines():
        parts = line.split("\t")
        if not parts:
            continue
        app_id = parts[0].strip()
        name = parts[1].strip() if len(parts) > 1 else app_id
        icon_url = (
            f"https://dl.flathub.org/repo/appstream/x86_64/icons/128x128/{app_id}.png"
        )
        packages.append(Package(
            id=f"flatpak:{app_id}",
            name=name,
            summary="Update available",
            source=PackageSource.FLATPAK,
            status=PackageStatus.UPDATE_AVAILABLE,
            icon_name="system-software-update",
            icon_url=icon_url,
            appstream_id=app_id,
        ))
    return packages


def run_task(task: Task) -> Iterator[tuple[int, str]]:
    """
    Execute a Flatpak task.
    Yields (progress_percent, status_text).
    System-wide installs automatically involve polkit via flatpak daemon.
    """
    if not _FLATPAK:
        yield (100, "flatpak not found.")
        task.state = TaskState.FAILED
        return

    app_id = task.package.appstream_id or task.package.name

    if task.task_type == TaskType.INSTALL:
        remotes = list_remotes()
        preferred = (app_settings.get("flatpak_default_remote", "") or "").strip()
        if preferred not in remotes:
            preferred = ""
        if not preferred:
            known_available = [name for name in KNOWN_REMOTES if name in remotes]
            if len(known_available) == 1:
                preferred = known_available[0]
        cmd = [_FLATPAK, "install", "--noninteractive", "-y"]
        if preferred:
            cmd.append(preferred)
        cmd.append(app_id)
    elif task.task_type == TaskType.REMOVE:
        cmd = [_FLATPAK, "remove", "--noninteractive", "-y", app_id]
    elif task.task_type == TaskType.UPDATE:
        cmd = [_FLATPAK, "update", "--noninteractive", "-y", app_id]
    elif task.task_type == TaskType.REFRESH:
        cmd = [_FLATPAK, "update", "--noninteractive", "-y"]
    else:
        return

    yield (0, f"Starting {task.task_type.value}…")

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
    except FileNotFoundError as e:
        yield (100, f"Error: {e}")
        task.state = TaskState.FAILED
        return

    progress = 5
    for line in proc.stdout:  # type: ignore
        line = line.rstrip()
        task.log_lines.append(line)
        if "Downloading" in line:
            progress = min(progress + 4, 70)
        elif "Installing" in line or "Updating" in line:
            progress = min(progress + 6, 90)
        yield (progress, line)

    proc.wait()
    if proc.returncode == 0:
        yield (100, "Done.")
        task.state = TaskState.DONE
    else:
        task.state = TaskState.FAILED
        task.error_message = "\n".join(task.log_lines[-3:])
        yield (100, f"Failed (exit {proc.returncode})")
        
    def is_installed(app_id: str) -> bool:
        return app_id in _installed_ids()

# Compatibility helpers (previously in pkg_manager.py)

def is_available() -> bool:
    """True si flatpak está disponible."""
    return bool(_FLATPAK)

# Alias para código que usa is_flatpak_available()
is_flatpak_available = is_available


def get_updates() -> List[dict]:
    """
    Devuelve actualizaciones flatpak como lista de dicts con versiones reales.
    """
    if not _FLATPAK:
        return []
    try:
        r = subprocess.run(
            [_FLATPAK, "remote-ls", "--updates", "--app",
             "--columns=application,name,version,branch,arch"],
            capture_output=True, text=True, timeout=20,
        )
    except Exception:
        return []

    # Construir mapa de versiones instaladas actualmente en un solo comando
    installed_versions: dict = {}
    try:
        r2 = subprocess.run(
            [_FLATPAK, "list", "--app", "--columns=application,version"],
            capture_output=True, text=True, timeout=10,
        )
        for ln in r2.stdout.splitlines():
            cols = ln.split("\t", 1)
            if len(cols) == 2:
                installed_versions[cols[0].strip()] = cols[1].strip()
    except Exception:
        pass

    updates = []
    for line in r.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        app_id  = parts[0].strip()
        new_ver = parts[2].strip() if len(parts) > 2 else "Available"
        # branch en parts[3], arch en parts[4]
        arch    = parts[4].strip() if len(parts) > 4 else ""

        cur_ver = installed_versions.get(app_id, "—") or "—"

        updates.append({
            "name":            app_id,
            "current_version": cur_ver,
            "new_version":     new_ver,
            "type":            "flatpak",
            "arch":            arch,
            "download_size":   0,
            "install_size":    0,
            "repo":            "Flathub",
            # El icono real de un Flatpak instalado suele registrarse en el
            # tema del sistema bajo su app-id; si no está, AppIconWidget
            # cae al icono genérico de "actualización" y por último a la
            # inicial con degradado.
            "icon_name":       app_id,
        })
    return updates

# Alias para mantener compatibilidad
get_flatpak_updates = get_updates


# Gestión de repositorios (remotes) Flatpak
#
# Yelena permite instalar el paquete DESDE distintos remotes, pero eso
# solo funciona si el remote ya está registrado en el sistema. Las
# siguientes funciones permiten listar los remotes configurados y añadir
# cualquiera de los 3 más comunes con un solo clic (flatpak se encarga de
# pedir privilegios vía polkit si hace falta, igual que en run_task()).

KNOWN_REMOTES: dict[str, dict[str, str]] = {
    "flathub": {
        "label": "Flathub",
        "url":   "https://flathub.org/repo/flathub.flatpakrepo",
                    "desc":  "El repositorio Flatpak principal, con miles de apps.",
            "icon":  "flathub.svg",

    },
    "flathub-beta": {
        "label": "Flathub Beta",
        "url":   "https://flathub.org/beta-repo/flathub-beta.flatpakrepo",
                    "desc":  "Versiones beta/en pruebas de apps publicadas en Flathub.",
            "icon":  "flathub.svg",

    },
    "gnome-nightly": {
        "label": "GNOME Nightly",
        "url":   "https://nightly.gnome.org/gnome-nightly.flatpakrepo",
                    "desc":  "Builds nocturnas de GNOME, actualizadas cada día.",
            "icon":  "gnome-nightly.svg",

    },
}


def list_remotes() -> set[str]:
    """Devuelve los nombres de los remotes Flatpak ya registrados en el
    sistema (o el usuario, si flatpak solo tiene instalación --user)."""
    if not _FLATPAK:
        return set()
    try:
        r = subprocess.run(
            [_FLATPAK, "remotes", "--columns=name"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode != 0:
            return set()
        return {line.strip() for line in r.stdout.splitlines() if line.strip()}
    except Exception:
        return set()


def is_remote_added(key: str) -> bool:
    """True si el remote (p.ej. 'flathub-beta') ya está registrado."""
    return key in list_remotes()


def get_default_remote() -> str | None:
    """Devuelve el remoto marcado como predeterminado por Flatpak, si existe."""
    if not _FLATPAK:
        return None
    try:
        r = subprocess.run(
            [_FLATPAK, "remotes", "--columns=name,options"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode != 0:
            return None
        for line in r.stdout.splitlines():
            parts = line.split("\t", 1)
            if len(parts) == 2 and "default" in parts[1].lower():
                return parts[0].strip() or None
    except Exception:
        pass
    return None


def remote_add(key: str) -> tuple[bool, str]:
    """Registra uno de los KNOWN_REMOTES mediante `flatpak remote-add`.

    Devuelve (ok, mensaje). flatpak gestiona la escalada de privilegios
    (polkit) por sí mismo cuando hace falta, igual que al instalar apps.
    """
    if not _FLATPAK:
        return False, "Flatpak no está instalado."
    remote = KNOWN_REMOTES.get(key)
    if remote is None:
        return False, f"Repositorio desconocido: {key}"
    try:
        r = subprocess.run(
            [_FLATPAK, "remote-add", "--if-not-exists", key, remote["url"]],
            capture_output=True, text=True, timeout=60,
        )
        if r.returncode == 0:
            return True, "OK"
        err = (r.stderr or r.stdout or "").strip()
        return False, err or f"flatpak remote-add salió con código {r.returncode}"
    except subprocess.TimeoutExpired:
        return False, "Tiempo de espera agotado."
    except Exception as e:
        return False, str(e)


def remote_remove(key: str) -> tuple[bool, str]:
    """Elimina un remoto Flatpak registrado."""
    if not _FLATPAK:
        return False, "Flatpak no está instalado."
    if key not in list_remotes():
        return False, "El repositorio no está instalado."
    try:
        r = subprocess.run(
            [_FLATPAK, "remote-delete", key],
            capture_output=True, text=True, timeout=60,
        )
        if r.returncode == 0:
            return True, "OK"
        err = (r.stderr or r.stdout or "").strip()
        return False, err or f"flatpak remote-delete salió con código {r.returncode}"
    except subprocess.TimeoutExpired:
        return False, "Tiempo de espera agotado."
    except Exception as e:
        return False, str(e)

