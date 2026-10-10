# yl-soft — Backend de AppImages (subcomando aimg de FPM)

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Iterator, List, Optional

from .models import Package, PackageSource, PackageStatus, Task, TaskState, TaskType

_ICON_LINE_RE = re.compile(r"^Icon=(.+)$", re.MULTILINE)


def fpm_path() -> Optional[str]:
    """Resuelve el ejecutable FPM que expone el subcomando aimg."""
    return shutil.which("fpm")


def is_available() -> bool:
    """True si FPM está disponible para gestionar AppImages."""
    return bool(fpm_path())


def _desktop_icon(desktop_path: str) -> str:
    """Lee el icono registrado en el .desktop creado para el AppImage."""
    if not desktop_path:
        return ""
    try:
        text = Path(desktop_path).read_text(errors="ignore")
    except OSError:
        return ""
    match = _ICON_LINE_RE.search(text)
    return match.group(1).strip() if match else ""


def _parse_plain(output: str) -> List[Package]:
    """Parsea `aimg list --plain` (tabulados: nombre, bytes, rutas, categoría)."""
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
    """Lista AppImages usando la interfaz soportada `aimg list --plain`."""
    binary = fpm_path()
    if not binary:
        return []
    try:
        result = subprocess.run(
            [binary, "aimg", "list", "--plain"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
            close_fds=True,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        return []
    return _parse_plain(result.stdout)


def _task_command(binary: str, task: Task) -> Optional[list[str]]:
    """Build argv using FPM's aimg subcommand syntax."""
    pkg = task.package
    if task.task_type == TaskType.INSTALL:
        if not pkg.source_path:
            return None
        return [
            binary, "aimg", "install",
            "--name", pkg.name,
            "--desc", pkg.description or pkg.summary or "",
            "--category", pkg.category or "Utility",
            "--icon", pkg.icon_url or pkg.icon_name or "application-x-executable",
            pkg.source_path,
        ]
    if task.task_type == TaskType.REMOVE:
        return [binary, "aimg", "remove", "--name", pkg.name]
    if task.task_type == TaskType.UPDATE:
        if not pkg.source_path:
            return None
        return [binary, "aimg", "update", "--name", pkg.name, pkg.source_path]
    return []


def run_task(task: Task) -> Iterator[tuple[int, str]]:
    """Ejecuta operaciones AppImage mediante FPM y el subcomando aimg."""
    binary = fpm_path()
    if not binary:
        yield (100, "FPM no está disponible.")
        task.state = TaskState.FAILED
        return

    cmd = _task_command(binary, task)
    if cmd is None:
        message = (
            "Falta el archivo .AppImage de origen."
            if task.task_type in (TaskType.INSTALL, TaskType.UPDATE)
            else "Falta información requerida para la operación."
        )
        yield (100, message)
        task.error_message = message
        task.state = TaskState.FAILED
        return
    if not cmd:
        return

    yield (0, f"Iniciando {task.task_type.value}…")
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
    except OSError as error:
        task.error_message = str(error)
        task.state = TaskState.FAILED
        yield (100, f"Error al iniciar FPM: {error}")
        return

    progress = 10
    for line in proc.stdout or ():
        line = line.rstrip()
        if line:
            task.log_lines.append(line)
            progress = min(progress + 15, 90)
            yield (progress, line)

    proc.wait()
    if proc.returncode == 0:
        yield (100, "Listo.")
        task.state = TaskState.DONE
    else:
        task.state = TaskState.FAILED
        task.error_message = "\n".join(task.log_lines[-3:]) or f"FPM terminó con código {proc.returncode}"
        yield (100, f"Falló (código {proc.returncode})")
