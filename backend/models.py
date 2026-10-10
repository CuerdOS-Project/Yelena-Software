# Vera_Shop — Shared data models

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import List


class PackageSource(Enum):
    FLATPAK  = "flatpak"
    XBPS     = "xbps"
    LOCAL    = "local"     # paquete local (.xbps)
    APPIMAGE = "appimage"  # AppImage gestionado con el subcomando aimg de FPM


class PackageStatus(Enum):
    NOT_INSTALLED = auto()
    INSTALLED = auto()
    UPDATE_AVAILABLE = auto()
    INSTALLING = auto()
    REMOVING = auto()
    UPDATING = auto()
    ERROR = auto()


class TaskType(Enum):
    INSTALL         = "Install"
    REMOVE          = "Remove"
    UPDATE          = "Update"
    REFRESH         = "Refresh"
    LOCAL_INSTALL   = "Local Install"    # instalación de .deb / .xbps local
    LOCAL_REMOVE    = "Local Remove"     # desinstalación de paquete local
    LOCAL_REINSTALL = "Local Reinstall"  # reinstalación de paquete local


class TaskState(Enum):
    PENDING = auto()
    RUNNING = auto()
    DONE = auto()
    FAILED = auto()
    CANCELLED = auto()


@dataclass
class Package:
    id: str                          # unique id (e.g. "xbps:firefox" or "flatpak:org.mozilla.firefox")
    name: str
    summary: str
    description: str = ""
    version: str = ""
    installed_version: str = ""
    source: PackageSource = PackageSource.XBPS
    status: PackageStatus = PackageStatus.NOT_INSTALLED
    icon_name: str = "application-x-executable"
    icon_url: str = ""
    category: str = "Other"
    size_bytes: int = 0
    developer: str = ""
    website: str = ""
    license: str = ""
    tags: List[str] = field(default_factory=list)
    screenshots: List[str] = field(default_factory=list)
    rating: float = 0.0
    appstream_id: str = ""
    architecture: str = ""
    featured: bool = False
    top_app: bool = False
    # (case-sensitive, p.ej. "Thunar" o "Signal-Desktop"). Si está vacío,
    pkgname: str = ""
    # Ruta a un archivo local usado como origen de instalación/actualización
    # (ej. un .AppImage elegido por el usuario). Vacío si no aplica.
    source_path: str = ""
    # Repositorio XBPS de procedencia (current, nonfree, multilib, CuerdOS...).
    repository: str = ""

    @property
    def size_str(self) -> str:
        if self.size_bytes <= 0:
            return "Unknown"
        if self.size_bytes < 1024:
            return f"{self.size_bytes} B"
        elif self.size_bytes < 1024 ** 2:
            return f"{self.size_bytes / 1024:.1f} KB"
        elif self.size_bytes < 1024 ** 3:
            return f"{self.size_bytes / 1024**2:.1f} MB"
        else:
            return f"{self.size_bytes / 1024**3:.2f} GB"

    @property
    def source_label(self) -> str:
        labels = {
            PackageSource.FLATPAK:  "Flatpak",
            PackageSource.XBPS:     "XBPS",
            PackageSource.LOCAL:    "Local",
            PackageSource.APPIMAGE: "AppImage",
        }
        return labels.get(self.source, self.source.value.upper())

    @property
    def source_short_label(self) -> str:
        """Código corto para la insignia (el nombre completo va en el
        tooltip, ver source_label)."""
        labels = {
            PackageSource.FLATPAK:  "FPK",
            PackageSource.XBPS:     "XB",
            PackageSource.LOCAL:    "Local",
            PackageSource.APPIMAGE: "AppImg",
        }
        return labels.get(self.source, self.source.value.upper()[:3])

    @property
    def badge_type(self) -> str:
        """Clave usada por BadgeLabel para el color de acento de la
        insignia de origen (ver _BADGE_ACCENTS en ui/native_widgets.py)."""
        return {
            PackageSource.FLATPAK:  "flatpak",
            PackageSource.XBPS:     "xbps",
            PackageSource.LOCAL:    "local",
            PackageSource.APPIMAGE: "appimage",
        }.get(self.source, "xbps")

    @property
    def is_installed(self) -> bool:
        return self.status in (
            PackageStatus.INSTALLED,
            PackageStatus.UPDATE_AVAILABLE,
        )

    @property
    def icon_fallback_name(self) -> str:
        """Icono de respaldo común cuando no existe el icono específico.

        En XBPS se prefiere el icono neutro de paquete sobre un símbolo de
        categoría; las demás fuentes conservan el fallback de categoría.
        """
        if self.source == PackageSource.XBPS:
            return "package-x-generic"
        for tag in self.tags:
            if tag.startswith("_category_icon:"):
                return tag[len("_category_icon:"):]
        return ""

    @property
    def is_32bit(self) -> bool:
        """True solo con metadatos explícitos o un sufijo de paquete inequívoco."""
        arch = (self.architecture or "").strip().lower().replace("-", "_")
        if arch in {"i386", "i486", "i586", "i686", "x86_32", "x86_32bit", "32", "32bit", "32_bit"}:
            return True
        if any(tag.strip().lower() in {"32bit", "32-bit", "arch:32", "arch:i686", "architecture:i686"} for tag in self.tags):
            return True
        # Convención explícita usada por algunos repositorios para variantes
        # de 32 bits; no inferir arquitectura de subcadenas como "lib".
        name = (self.pkgname or self.name).strip().lower()
        return name.endswith(("-32bit", "-i686", "-i386"))


@dataclass
class Task:
    id: str
    package: Package
    task_type: TaskType
    state: TaskState = TaskState.PENDING
    progress: int = 0          # 0-100
    status_text: str = ""
    error_message: str = ""
    log_lines: List[str] = field(default_factory=list)
    # Marcas de tiempo (epoch, segundos). 0.0 = todavía no ocurrió.
    started_at: float = 0.0
    finished_at: float = 0.0
