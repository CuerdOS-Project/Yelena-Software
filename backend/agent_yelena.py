#!/usr/bin/env python3
"""agent-yelena: puente seguro y mínimo entre una GUI y polkit/pkexec.

Principios de seguridad:
* Nunca solicita, recibe, cifra, descifra ni almacena contraseñas.
* Nunca usa shell, sudo, doas, gksu ni ejecuta comandos arbitrarios.
* Solo permite xbps-install, xbps-remove y xbps-reconfigure mediante pkexec,
  más el broker de AppImages (acción org.ylsoft.appimage.broker).

Este archivo es deliberadamente independiente y puede copiarse a otras
herramientas de CuerdOS. La GUI debe pasar listas de argumentos, nunca una
cadena de shell.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Sequence

POLICY_TARGET = Path("/usr/share/polkit-1/actions/org.ylsoft.policy")
_POLICY_SOURCE = Path(__file__).resolve().parents[1] / "resources" / "polkit" / "yl-soft.policy"
_POLICY_MARKER = "agent-yelena-policy-version=7"
_ensure_lock = threading.Lock()
_ensure_done = False

_OPERATION_BINARIES = {
    "install": "xbps-install",
    "remove": "xbps-remove",
    "reconfigure": "xbps-reconfigure",
}
# Acción polkit del broker de AppImages (am-FUSION); ver backend/priv_broker.py.
APPIMAGE_BROKER_ACTION = "org.ylsoft.appimage.broker"
_ACTION_IDS = {
    "install": "org.ylsoft.xbps.install",
    "remove": "org.ylsoft.xbps.remove",
    "reconfigure": "org.ylsoft.xbps.reconfigure",
}
# Opciones necesarias por la GUI. Se rechaza cualquier otra opción para evitar
# que este puente se convierta en una vía genérica hacia xbps.
_ALLOWED_OPTIONS = {
    "install": frozenset({"-y", "-S", "-u", "-yu", "-Su", "-Syu", "--yes"}),
    "remove": frozenset({"-y", "--yes", "-R", "-f", "--clean-cache"}),
    "reconfigure": frozenset({"-y", "--yes"}),
}
_PACKAGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9+._-]*$")


class AgentError(RuntimeError):
    """Error seguro y esperable del agente."""


def _policy_ok() -> bool:
    try:
        return _POLICY_MARKER in POLICY_TARGET.read_text(encoding="utf-8")
    except OSError:
        return False


def ensure_policy() -> None:
    """Instala la política polkit si falta o es antigua (una vez por ejecución).

    Se llama antes de la primera acción privilegiada: esa propia acción pide
    la contraseña de administrador para copiar la política y, desde entonces,
    pkexec usa las acciones org.ylsoft.xbps.* (auth_admin_keep). Si se cancela
    o falla, la operación continúa con la autenticación genérica de pkexec.
    """
    global _ensure_done
    with _ensure_lock:
        if _ensure_done:
            return
        _ensure_done = True
        if _policy_ok() or not _POLICY_SOURCE.is_file():
            return
        pkexec, installer = shutil.which("pkexec"), shutil.which("install")
        if not pkexec or not installer:
            return
        try:
            result = subprocess.run(
                [pkexec, installer, "-D", "-o", "root", "-g", "root", "-m", "0644",
                 str(_POLICY_SOURCE), str(POLICY_TARGET)],
                shell=False, close_fds=True, check=False,
            )
            if result.returncode != 0:
                # No iniciar después la operación real si el administrador
                # canceló o rechazó esta autorización previa.
                raise AgentError("Autorización administrativa cancelada.")
        except OSError:
            raise AgentError("No se pudo preparar la autorización administrativa.")


def _binary(operation: str) -> str:
    try:
        name = _OPERATION_BINARIES[operation]
    except KeyError as exc:
        raise AgentError(f"Operación XBPS no permitida: {operation!r}") from exc
    path = shutil.which(name)
    if not path:
        raise AgentError(f"No se encontró {name} en PATH")
    # Resolver symlinks evita que el PATH redirija silenciosamente a otro
    # ejecutable. Se permite /usr/bin o /bin, habituales en CuerdOS.
    resolved = os.path.realpath(path)
    if resolved not in {f"/usr/bin/{name}", f"/bin/{name}"}:
        raise AgentError(f"Binario XBPS no confiable: {resolved}")
    return resolved


def _validate_args(operation: str, args: Sequence[str]) -> list[str]:
    allowed = _ALLOWED_OPTIONS[operation]
    result: list[str] = []
    for raw in args:
        if not isinstance(raw, str) or "\x00" in raw:
            raise AgentError("Argumento inválido")
        if raw.startswith("-"):
            if raw not in allowed:
                raise AgentError(f"Opción no permitida para XBPS: {raw}")
        elif not _PACKAGE.fullmatch(raw):
            raise AgentError(f"Nombre de paquete no permitido: {raw!r}")
        result.append(raw)
    if operation != "install" and not any(not x.startswith("-") for x in result):
        # -S/-u sin paquete tiene sentido solo en install; no se permite
        # una operación vacía o global en remove/reconfigure.
        raise AgentError("Debe indicarse al menos un paquete")
    return result


def build_command(operation: str, args: Sequence[str]) -> list[str]:
    """Construye únicamente ``pkexec /ruta/xbps-* argumentos``."""
    binary = _binary(operation)
    safe_args = _validate_args(operation, args)
    pkexec = shutil.which("pkexec")
    if not pkexec:
        raise AgentError("No se encontró pkexec; no se ejecutará con fallback inseguro")
    return [pkexec, binary, *safe_args]


def authorize(operation: str, *, allow_user_interaction: bool = True,
              require_tool: bool = False) -> None:
    """Verifica la autorización polkit usando el proceso de la aplicación."""
    try:
        action = _ACTION_IDS[operation]
    except KeyError as exc:
        raise AgentError(f"Operación XBPS no permitida: {operation!r}") from exc
    pkcheck = shutil.which("pkcheck")
    if not pkcheck:
        if require_tool:
            raise AgentError("No se encontró pkcheck; no se puede activar ahora la sesión administrativa.")
        # pkexec hará su propia comprobación en instalaciones mínimas.
        return
    command = [pkcheck, "--action-id", action, "--process", str(os.getpid())]
    if allow_user_interaction:
        command.append("--allow-user-interaction")
    result = subprocess.run(command, text=True, capture_output=True,
                            close_fds=True, shell=False, check=False)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or
                  "Autorización administrativa cancelada.").strip()
        raise AgentError(detail)


def build_xbps_command(operation: str, xbps_args: Sequence[str]) -> list[str]:
    """Alias compatible con integraciones existentes."""
    return build_command(operation, xbps_args)


def build_privileged_command(binary: str, args: Sequence[str]) -> list[str]:
    """Compatibilidad estricta: solo acepta binarios XBPS conocidos.

    No es un envoltorio genérico. Las llamadas nuevas deben usar
    :func:`build_command`.
    """
    real = os.path.realpath(binary)
    for operation, name in _OPERATION_BINARIES.items():
        if real in {f"/usr/bin/{name}", f"/bin/{name}"}:
            return build_command(operation, args)
    raise AgentError("Solo se permite elevar un binario XBPS autorizado")


def run(operation: str, args: Sequence[str], **kwargs) -> subprocess.CompletedProcess[str]:
    """Ejecuta una operación XBPS sin shell y deja autenticar a pkexec."""
    command = build_command(operation, args)
    ensure_policy()
    kwargs.setdefault("text", True)
    kwargs.setdefault("check", False)
    kwargs.setdefault("close_fds", True)
    kwargs["shell"] = False
    return subprocess.run(command, **kwargs)


def popen(operation: str, args: Sequence[str], **kwargs) -> subprocess.Popen[str]:
    """Inicia XBPS para streaming de salida, también sin shell."""
    command = build_command(operation, args)
    ensure_policy()
    kwargs.setdefault("text", True)
    kwargs.setdefault("close_fds", True)
    kwargs["shell"] = False
    return subprocess.Popen(command, **kwargs)


__all__ = [
    "AgentError", "build_command", "build_xbps_command", "ensure_policy",
    "build_privileged_command", "authorize", "run", "popen",
    "APPIMAGE_BROKER_ACTION",
]

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Validador de agent-yelena")
    parser.add_argument("operation", choices=sorted(_OPERATION_BINARIES))
    parser.add_argument("args", nargs="*")
    ns = parser.parse_args()
    try:
        print(" ".join(build_command(ns.operation, ns.args)))
    except AgentError as exc:
        parser.error(str(exc))
