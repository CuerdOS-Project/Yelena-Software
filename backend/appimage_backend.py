# yl-soft — Backend de AppImages (subcomando aimg de FPM)

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, List, Optional

from core.i18n import tr
from .models import Package, PackageSource, PackageStatus, Task, TaskState, TaskType
from . import priv_broker
from .process_stream import stream_process

_ICON_LINE_RE = re.compile(r"^Icon=(.+)$", re.MULTILINE)
_AM_REPOSITORY = "am-FUSION"
_FPM_EVENT_PREFIX = "FPM_AIMG_EVENT\t"
_PERCENT_RE = re.compile(r"(?<!\d)(\d{1,3})(?:\.\d+)?\s*%")
_MAX_TASK_LOG_LINES = 20000
_SUDO_PROMPT_ERRORS = (
    "sudo: a terminal is required to read the password",
    "sudo: no tty present and no askpass program specified",
    "sudo: a password is required",
    "sudo: no password was provided",
)


def _sudo_prompt_failed(lines: list[str]) -> bool:
    output = "\n".join(lines).casefold()
    return any(marker in output for marker in _SUDO_PROMPT_ERRORS)


def _pkexec_path() -> Optional[str]:
    return shutil.which("pkexec")


@contextmanager
def _pkexec_sudo_environment():
    """Redirige los `sudo` de AM a un único pkexec compartido (sin elevar FPM).

    La primera operación privilegiada pide la contraseña; las siguientes
    (dentro de la misma tarea o de otras) reutilizan esa autorización.
    """
    pkexec = _pkexec_path() if os.name == "posix" and os.geteuid() != 0 else None
    if not pkexec:
        yield None
        return
    try:  # prefijo agent-yelena en el diálogo de polkit (acción org.ylsoft.appimage.broker)
        from .agent_yelena import ensure_policy
        ensure_policy()
    except Exception:
        pass
    with priv_broker.session_environment(pkexec) as env:
        yield env


def _parse_fpm_event(line: str) -> Optional[dict]:
    if not line.startswith(_FPM_EVENT_PREFIX):
        return None
    try:
        event = json.loads(line[len(_FPM_EVENT_PREFIX):])
    except (TypeError, ValueError):
        return None
    if not isinstance(event, dict) or event.get("protocol") != "fpm-aimg-v1":
        return None
    if event.get("event") not in {"authorization", "download", "installed"}:
        return None
    return event


def _format_bytes(value: int) -> str:
    amount = max(0, int(value))
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    scaled = float(amount)
    for unit in units:
        if scaled < 1024 or unit == units[-1]:
            return f"{scaled:.1f} {unit}" if unit != "B" else f"{int(scaled)} B"
        scaled /= 1024
    return f"{amount} B"


@contextmanager
def _task_process_environment(is_am_package: bool):
    if not is_am_package:
        yield None
        return
    with _pkexec_sudo_environment() as env:
        process_env = env if env is not None else os.environ.copy()
        process_env["FPM_AIMG_EVENTS"] = "1"
        yield process_env


def fpm_path() -> Optional[str]:
    """Resuelve el ejecutable FPM que expone el subcomando aimg."""
    return shutil.which("fpm")


def is_available() -> bool:
    """True si FPM está disponible para gestionar AppImages."""
    return bool(fpm_path())


def _display_am_name(name: str) -> str:
    suffix = "-appimage"
    return name[:-len(suffix)] if name.casefold().endswith(suffix) else name


def _am_icon_url(name: str) -> str:
    slug = _display_am_name(name).strip().casefold()
    if not slug or not re.fullmatch(r"[a-z0-9._+-]+", slug):
        return ""
    return (
        "https://raw.githubusercontent.com/Portable-Linux-Apps/"
        f"Portable-Linux-Apps.github.io/main/icons/{slug}.png"
    )


def _appimage_identity(name: str) -> str:
    display_name = name[:-len(" (Local)")] if name.endswith(" (Local)") else name
    if display_name.casefold().endswith("-appimage"):
        display_name = _display_am_name(display_name)
    return re.sub(r"[^a-z0-9]+", "", display_name.casefold())


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
    """Parsea filas locales y AM de `aimg list --plain`."""
    local_packages: list[Package] = []
    am_packages: list[Package] = []
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) >= 5 and parts[3].strip() != _AM_REPOSITORY:
            name, size_s, appimage_path, desktop_path, category = parts[:5]
            try:
                size_bytes = int(size_s)
            except ValueError:
                size_bytes = 0
            icon = _desktop_icon(desktop_path) or "application-x-executable"
            local_packages.append(Package(
                id=f"appimage:{name}",
                name=name,
                summary=category or "AppImage",
                source=PackageSource.APPIMAGE,
                status=PackageStatus.INSTALLED,
                icon_name=icon,
                category=category or "Other",
                size_bytes=size_bytes,
                appstream_id=name,
                pkgname=name,
                source_path=appimage_path,
                repository="FPM-local",
            ))
        elif len(parts) >= 4 and parts[3].strip() == _AM_REPOSITORY:
            name, version, _package_type, repository = (part.strip() for part in parts[:4])
            display_name = _display_am_name(name)
            try:
                size_bytes = int(parts[4].strip()) if len(parts) > 4 else 0
            except ValueError:
                size_bytes = 0
            am_packages.append(Package(
                id=f"appimage:{_AM_REPOSITORY}:{name}",
                name=display_name,
                summary=f"AppImage de {repository}",
                description=f"AppImage instalado desde {repository}",
                version=version,
                installed_version=version,
                source=PackageSource.APPIMAGE,
                status=PackageStatus.INSTALLED,
                icon_name=display_name or "application-x-executable",
                icon_url=_am_icon_url(name),
                category="Other",
                size_bytes=size_bytes,
                appstream_id=name,
                pkgname=name,
                repository=repository,
            ))

    am_keys = {_appimage_identity(package.name) for package in am_packages}
    for package in local_packages:
        if _appimage_identity(package.name) in am_keys:
            package.name = f"{_display_am_name(package.name)} (Local)"
    return local_packages + am_packages


def list_installed() -> List[Package]:
    """Lista AppImages locales y los instalados desde AM por FPM."""
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


_UPDATE_CHECK_MAX_AGE = 6 * 3600


def get_updates() -> Optional[list[dict]]:
    """Actualizaciones pendientes de apps instaladas desde am-FUSION.

    Nativo (sin root): compara la versión instalada que informa `fpm aimg list`
    con la última release publicada. Devuelve ``None`` si no se pudo consultar
    (FPM ausente, repo inactivo): así el llamador conserva lo que ya tenía en
    vez de borrar actualizaciones por un fallo puntual.
    """
    status = am_fusion_status()
    if not status.get("available") or not status.get("enabled"):
        return None
    from . import appimage_meta
    installed = [p for p in list_installed() if p.repository == _AM_REPOSITORY]
    names = [p.pkgname or p.name for p in installed]
    appimage_meta.fetch_many(names, deadline=12.0, max_age=_UPDATE_CHECK_MAX_AGE)
    updates: list[dict] = []
    for package, name in zip(installed, names):
        release = appimage_meta.cached(name, _UPDATE_CHECK_MAX_AGE)
        if not appimage_meta.is_outdated(package.installed_version or package.version, release):
            continue
        updates.append({
            "name": name,
            "current_version": package.installed_version or package.version,
            "new_version": release.get("version") or "Available",
            "type": "appimage",
            "manager": "appimage",
            "arch": "",
            "download_size": int(release.get("size") or 0),
            "install_size": 0,
            "repo": _AM_REPOSITORY,
            "icon_name": _display_am_name(name),
        })
    return updates


def update_command(name: str) -> Optional[list[str]]:
    """argv de `fpm aimg update` para una app am-FUSION (sin shell)."""
    binary = fpm_path()
    if not binary or not name:
        return None
    return [binary, "aimg", "update", "--repo", _AM_REPOSITORY, "--assume-yes", name]


def process_environment():
    """Contexto con el entorno (sudo→broker único) para ejecutar `fpm aimg`."""
    return _task_process_environment(True)


def am_fusion_status() -> dict[str, bool]:
    """Devuelve si FPM soporta am-FUSION y si el repo está activo."""
    binary = fpm_path()
    if not binary:
        return {"available": False, "enabled": False}
    try:
        result = subprocess.run(
            [binary, "aimg", "repo", "list"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
            close_fds=True,
        )
    except (OSError, subprocess.SubprocessError):
        return {"available": False, "enabled": False}
    if result.returncode != 0:
        return {"available": False, "enabled": False}
    for line in result.stdout.splitlines():
        fields = line.split("\t")
        if len(fields) >= 2 and fields[0].strip() == _AM_REPOSITORY:
            return {"available": True, "enabled": fields[1].strip().casefold() == "activo"}
    return {"available": False, "enabled": False}


_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_URL_RE = re.compile(r"https?://[^\s)>\"']+")


def am_info(name: str) -> dict[str, str]:
    """Ficha de una app am-FUSION vía `fpm aimg info` ({'text', 'website'}).

    Devuelve {} si FPM no responde; el texto se limpia de secuencias ANSI.
    """
    binary = fpm_path()
    if not binary or not name:
        return {}
    try:
        result = subprocess.run(
            [binary, "aimg", "info", "--repo", _AM_REPOSITORY, name],
            capture_output=True, text=True, timeout=20, check=False, close_fds=True,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    if result.returncode != 0:
        return {}
    lines = [_ANSI_RE.sub("", line).rstrip() for line in result.stdout.splitlines()]
    lines = [line for line in lines if line.strip()][:40]
    if not lines:
        return {}
    text = "\n".join(lines)[:2000]
    match = _URL_RE.search(text)
    return {"text": text, "website": match.group(0).rstrip(".,") if match else ""}


def install_am_fusion_events() -> Iterator[tuple[str, object]]:
    """Instala am-FUSION y transmite salida/heartbeat a la GUI de Ajustes."""
    binary = fpm_path()
    if not binary:
        yield ("result", (False, "FPM no está disponible."))
        return
    command = [binary, "aimg", "repo", "install", _AM_REPOSITORY,
               "--backend", "appman"]
    yield ("status", tr("appimage_task_starting"))
    messages: list[str] = []
    exit_code: int | None = None
    try:
        with _task_process_environment(True) as process_env:
            for kind, value in stream_process(command, heartbeat_seconds=1.0, env=process_env):
                if kind == "output":
                    line = str(value).strip()
                    if not line:
                        continue
                    fpm_event = _parse_fpm_event(line)
                    if fpm_event and fpm_event.get("event") == "authorization":
                        yield ("status", tr("appimage_task_pkexec"))
                        continue
                    messages.append(line)
                    yield ("output", line)
                elif kind == "heartbeat":
                    yield ("status", tr("appimage_task_running", seconds=max(1, int(float(value)))))
                elif kind == "exit":
                    exit_code = int(value)
    except OSError as error:
        yield ("result", (False, str(error)))
        return
    message = "\n".join(messages[-8:])
    if exit_code == 0 and not _sudo_prompt_failed(messages):
        yield ("result", (True, message or tr("appimage_task_done")))
    else:
        if _sudo_prompt_failed(messages):
            message = f"{tr('appimage_task_privilege_required')}\n{message}".strip()
        yield ("result", (False, message or f"FPM terminó con código {exit_code}."))


def uninstall_am_fusion() -> tuple[bool, str]:
    """Desinstala am-pack: desactiva el repositorio am-FUSION en FPM.

    FPM no ofrece borrar AM/AppMan, solo `aimg repo disable`; es la vía
    soportada, no pide contraseña y NO elimina las apps ya instaladas.
    """
    binary = fpm_path()
    if not binary:
        return False, "FPM no está disponible."
    try:
        result = subprocess.run(
            [binary, "aimg", "repo", "disable", _AM_REPOSITORY],
            capture_output=True, text=True, timeout=30, check=False, close_fds=True,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return False, str(error)
    output = _ANSI_RE.sub("", (result.stdout or "") + (result.stderr or "")).strip()
    if result.returncode != 0:
        return False, output or f"FPM terminó con código {result.returncode}."
    return True, output


def install_am_fusion() -> tuple[bool, str]:
    """Compatibilidad síncrona; la interfaz usa install_am_fusion_events()."""
    result = (False, "FPM no está disponible.")
    for kind, value in install_am_fusion_events():
        if kind == "result":
            result = value
    return result


def _task_command(binary: str, task: Task) -> Optional[list[str]]:
    """Build argv using FPM's aimg subcommand syntax."""
    pkg = task.package
    package_name = pkg.pkgname or pkg.name
    is_am_package = pkg.source == PackageSource.APPIMAGE and pkg.repository == _AM_REPOSITORY
    if task.task_type == TaskType.INSTALL:
        if is_am_package:
            return [binary, "aimg", "install", "--repo", _AM_REPOSITORY,
                    "--assume-yes", package_name]
        if not pkg.source_path:
            return None
        return [
            binary, "aimg", "install",
            "--name", package_name,
            "--desc", pkg.description or pkg.summary or "",
            "--category", pkg.category or "Utility",
            "--icon", pkg.icon_url or pkg.icon_name or "application-x-executable",
            pkg.source_path,
        ]
    if task.task_type == TaskType.REMOVE:
        if is_am_package:
            return [binary, "aimg", "remove", "--repo", _AM_REPOSITORY,
                    "--assume-yes", package_name]
        return [binary, "aimg", "remove", "--name", package_name]
    if task.task_type == TaskType.UPDATE:
        if is_am_package:
            return [binary, "aimg", "update", "--repo", _AM_REPOSITORY,
                    "--assume-yes", package_name]
        if not pkg.source_path:
            return None
        return [binary, "aimg", "update", "--name", package_name, pkg.source_path]
    return []


_CHOICE_OPTION_RE = re.compile(r"^\s*(\d+)\.\s+(.+)$")


def _ask_variant(ask, lines: list[str]) -> Optional[str]:
    """Extrae las opciones numeradas del instalador AM y deja elegir al usuario."""
    clean = [_ANSI_RE.sub("", line) for line in lines]
    start = max((i for i, l in enumerate(clean) if "choose which version" in l.lower()), default=0)
    title = clean[start].strip() if clean else ""
    options: list[list[str]] = []
    for line in clean[start:]:
        match = _CHOICE_OPTION_RE.match(line)
        if match:
            options.append([match.group(1), match.group(2).strip()])
        elif options and line.strip().lower().startswith("source:"):
            options[-1][1] += " — " + line.strip()[7:].strip()
    if not options:
        return "1"
    return ask(title, options)


def _record_output(task: Task, line: str) -> None:
    task.log_lines.append(line)
    if len(task.log_lines) > _MAX_TASK_LOG_LINES:
        del task.log_lines[:-_MAX_TASK_LOG_LINES]


def run_task(task: Task) -> Iterator[tuple[int, str]]:
    """Ejecuta AppImages vía FPM y transmite salida real a la GUI.

    La CLI no siempre conoce un porcentaje; en ese caso el modelo conserva un
    progreso indeterminado y el worker emite latidos para que la GUI indique
    que el proceso sigue vivo, aun si el backend no imprime durante la descarga.
    """
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

    task.progress = 0
    task.progress_indeterminate = True
    task.status_text = tr("appimage_task_starting")
    yield (0, task.status_text)
    is_am_package = task.package.source == PackageSource.APPIMAGE and task.package.repository == _AM_REPOSITORY
    attempt_lines: list[str] = []
    try:
        with _task_process_environment(is_am_package) as process_env:
            process_kwargs = {
                "cancel_event": task.cancel_event,
                "process_callback": lambda proc: setattr(task, "active_process", proc),
            }
            if process_env is not None:
                process_kwargs["env"] = process_env
            ask = task.ask_choice
            if ask is not None:
                process_kwargs["on_choice"] = lambda: _ask_variant(ask, attempt_lines)
            events = stream_process(cmd, **process_kwargs)
            exit_code = None
            was_cancelled = False
            for kind, value in events:
                if kind == "output":
                    line = str(value).strip()
                    if not line:
                        continue
                    fpm_event = _parse_fpm_event(line)
                    if fpm_event:
                        event_type = fpm_event.get("event")
                        if event_type == "authorization":
                            task.status_text = tr("appimage_task_pkexec")
                            yield (task.progress, task.status_text)
                            continue
                        size = fpm_event.get("downloaded_bytes", fpm_event.get("size_bytes", 0))
                        try:
                            size = max(0, int(size or 0))
                        except (TypeError, ValueError):
                            size = 0
                        total = fpm_event.get("download_total_bytes", 0)
                        try:
                            task.download_total_bytes = max(0, int(total or 0))
                        except (TypeError, ValueError):
                            task.download_total_bytes = 0
                        task.downloaded_bytes = max(task.downloaded_bytes, size)
                        version = str(fpm_event.get("version") or "").strip()
                        if version:
                            task.detected_version = version
                        if event_type == "installed":
                            if version:
                                task.package.version = version
                                task.package.installed_version = version
                            if size:
                                task.package.size_bytes = size
                        if task.download_total_bytes > 0:
                            percent = min(99, round(100 * task.downloaded_bytes / task.download_total_bytes))
                            task.progress = max(task.progress, percent)
                            task.progress_indeterminate = False
                        size_text = _format_bytes(task.downloaded_bytes) if task.downloaded_bytes else "—"
                        status = tr("appimage_task_download_meta", size=size_text, version=task.detected_version or "—")
                        task.status_text = status
                        yield (task.progress, status)
                        continue
                    attempt_lines.append(line)
                    _record_output(task, line)
                    match = _PERCENT_RE.search(line)
                    if match:
                        percent = min(99, int(match.group(1)))
                        task.progress = max(task.progress, percent)
                        task.progress_indeterminate = False
                    task.status_text = line
                    yield (task.progress, line)
                elif kind == "heartbeat":
                    elapsed = max(1, int(float(value)))
                    task.status_text = tr("appimage_task_running", seconds=elapsed)
                    yield (task.progress, task.status_text)
                elif kind == "status" and value == "cancel":
                    task.status_text = tr("appimage_task_cancelling")
                    yield (task.progress, task.status_text)
                elif kind == "cancelled":
                    was_cancelled = True
                elif kind == "exit":
                    exit_code = int(value)
    except OSError as error:
        task.error_message = str(error)
        task.state = TaskState.FAILED
        yield (100, f"Error al iniciar FPM: {error}")
        return
    if was_cancelled or task.cancel_event.is_set():
        task.state = TaskState.CANCELLED
        task.progress_indeterminate = False
        task.status_text = tr("appimage_task_cancelled")
        yield (task.progress, task.status_text)
        return
    sudo_failed = _sudo_prompt_failed(attempt_lines)
    if exit_code == 0 and not sudo_failed:
        task.progress = 100
        task.progress_indeterminate = False
        yield (100, tr("appimage_task_done"))
        task.state = TaskState.DONE
        try:
            from core import search_cache
            search_cache.invalidate("appimage:am-FUSION")
            search_cache.invalidate("xbps:fpm")
        except Exception:
            pass
    else:
        task.state = TaskState.FAILED
        task.error_message = "\n".join(task.log_lines[-5:]) or f"FPM terminó con código {exit_code}"
        task.progress_indeterminate = False
        if sudo_failed:
            task.status_text = tr("appimage_task_privilege_required")
        else:
            task.status_text = tr("appimage_task_failed", code=exit_code)
        yield (task.progress, task.status_text)
