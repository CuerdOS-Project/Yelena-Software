# yl-soft — Backend de AppImages (puente hacia la CLI de FPM)

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Iterator, List

from .models import Package, PackageSource, PackageStatus, Task, TaskType, TaskState

_FPM = shutil.which("fpm")
_ICON_LINE_RE = re.compile(r"^Icon=(.+)$", re.MULTILINE)


def is_available() -> bool:
    """True si el binario 'fpm' está disponible en PATH."""
    return bool(_FPM)


def _desktop_icon(desktop_path: str) -> str:
    """Lee la clave Icon= del .desktop que FPM genera al instalar el
    AppImage (ahí quedó el ícono real elegido/auto-detectado durante la
    instalación — antes se descartaba y siempre se mostraba el genérico)."""
    if not desktop_path:
        return ""
    try:
        text = Path(desktop_path).read_text(errors="ignore")
    except OSError:
        return ""
    m = _ICON_LINE_RE.search(text)
    return m.group(1).strip() if m else ""


def _parse_plain(output: str) -> List[Package]:
    """Parsea la salida de 'fpm appimage list --plain':
    nombre \\t bytes \\t ruta_appimage \\t ruta_desktop \\t categoria"""
    packages = []
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) < 5:
            continue
        name, size_s, appimage_path, desktop_path, category = parts[:5]
        try:
            size_bytes = int(size_s)
        except ValueError:
            size_bytes = 0
        icon = _desktop_icon(desktop_path) or "application-x-executable"
        packages.append(Package(
            id=f"appimage:{name}",
            name=name,
            summary=category or "AppImage",
            source=PackageSource.APPIMAGE,
            status=PackageStatus.INSTALLED,
            icon_name=icon,
            category=category or "Other",
            size_bytes=size_bytes,
            appstream_id=name,
            source_path=appimage_path,
        ))
    return packages


def list_installed() -> List[Package]:
    """Lista los AppImages instalados vía 'fpm appimage list --plain'."""
    if not _FPM:
        return []
    try:
        r = subprocess.run(
            [_FPM, "appimage", "list", "--plain"],
            capture_output=True, text=True, timeout=10,
        )
    except Exception:
        return []
    return _parse_plain(r.stdout)


def run_task(task: Task) -> Iterator[tuple[int, str]]:
    """Ejecuta install/remove/update de un AppImage vía FPM.
    Yields (progreso_pct, texto_estado)."""
    if not _FPM:
        yield (100, "fpm no está disponible.")
        task.state = TaskState.FAILED
        return

    pkg = task.package

    if task.task_type == TaskType.INSTALL:
        if not pkg.source_path:
            yield (100, "Falta el archivo .AppImage de origen.")
            task.state = TaskState.FAILED
            return
        cmd = [
            _FPM, "appimage", "install", pkg.source_path,
            "--name", pkg.name,
            "--desc", pkg.description or pkg.summary or "",
            "--category", pkg.category or "Utility",
            "--icon", pkg.icon_url or pkg.icon_name or "application-x-executable",
        ]
    elif task.task_type == TaskType.REMOVE:
        cmd = [_FPM, "appimage", "remove", pkg.name]
    elif task.task_type == TaskType.UPDATE:
        if not pkg.source_path:
            yield (100, "Falta el archivo .AppImage nuevo.")
            task.state = TaskState.FAILED
            return
        cmd = [_FPM, "appimage", "update", pkg.name, pkg.source_path]
    else:
        return

    yield (0, f"Iniciando {task.task_type.value}…")

    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
    except FileNotFoundError as e:
        yield (100, f"Error: {e}")
        task.state = TaskState.FAILED
        return

    progress = 10
    for line in proc.stdout:  # type: ignore
        line = line.rstrip()
        task.log_lines.append(line)
        progress = min(progress + 15, 90)
        yield (progress, line)

    proc.wait()
    if proc.returncode == 0:
        yield (100, "Listo.")
        task.state = TaskState.DONE
    else:
        task.state = TaskState.FAILED
        task.error_message = "\n".join(task.log_lines[-3:])
        yield (100, f"Falló (código {proc.returncode})")
