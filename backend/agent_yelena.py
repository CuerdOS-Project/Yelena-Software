#!/usr/bin/env python3
"""agent-yelena: puente seguro y mínimo entre una GUI y polkit/pkexec.

Principios de seguridad:
* Nunca solicita, recibe, cifra, descifra ni almacena contraseñas.
* Nunca usa shell, sudo, doas, gksu ni ejecuta comandos arbitrarios.
* Solo permite xbps-install, xbps-remove y xbps-reconfigure mediante pkexec.
* La política instalada usa `auth_admin` (sin `_keep`) para las tres acciones:
  cada pkexec exige autenticación propia. La opción "recordar contraseña"
  de Ajustes (backend/admin_session.py) todavía no tiene un mecanismo real
  que la haga efectiva a nivel de polkit (el archivo referenciado en
  POLICY_RULE nunca se genera ni se instala) — usarla no debe interpretarse
  como una ventana de gracia garantizada. Antes la política usaba
  `auth_admin_keep`, lo que hacía que polkitd reutilizara la autenticación
  por su cuenta incluso con "Siempre preguntar" seleccionado en Ajustes.

Este archivo es deliberadamente independiente y puede copiarse a otras
herramientas de CuerdOS. La GUI debe pasar listas de argumentos, nunca una
cadena de shell.
"""
from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
from pathlib import Path
from typing import Sequence

CACHE_SECONDS = 300
POLICY_RULE = Path("/etc/polkit-1/rules.d/50-yelena-xbps.rules")
POLICY_ACTION_NAMES = (
    Path("/etc/polkit-1/actions/org.ylsoft.policy"),
    Path("/usr/share/polkit-1/actions/org.ylsoft.policy"),
)
_POLICY_SOURCE = Path(__file__).resolve().parents[1] / "resources" / "polkit" / "yl-soft.policy"
POLICY_VERSION = "3"
_POLICY_MARKER = f"agent-yelena-policy-version={POLICY_VERSION}"

_OPERATION_BINARIES = {
    "install": "xbps-install",
    "remove": "xbps-remove",
    "reconfigure": "xbps-reconfigure",
}
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


def _root_owned_0644(path: Path) -> bool:
    """Comprueba el formato de permisos solicitado para una política."""
    try:
        st = path.stat()
    except OSError:
        return False
    return (
        stat.S_ISREG(st.st_mode)
        and stat.S_IMODE(st.st_mode) == 0o644
        and st.st_uid == 0
        and st.st_gid == 0
    )


def policy_rule_is_well_formed(path: Path = POLICY_RULE) -> bool:
    """Valida presencia, tipo, propietario y modo de la regla local.

    Esto no pretende afirmar que polkitd ya la haya cargado: solo polkitd
    puede validar/cargar una regla JavaScript. La aplicación no debe habilitar
    una caché propia basándose en esta función.
    """
    return _root_owned_0644(path)


def policy_action_is_installed() -> bool:
    """Indica si existe una política de acción con metadatos seguros."""
    return any(_root_owned_0644(path) for path in POLICY_ACTION_NAMES)


def policy_action_is_current() -> bool:
    """Comprueba que la política instalada no sea de una versión antigua."""
    if not policy_action_is_installed():
        return False
    for path in POLICY_ACTION_NAMES:
        try:
            if _root_owned_0644(path) and _POLICY_MARKER in path.read_text(encoding="utf-8"):
                return True
        except (OSError, UnicodeError):
            pass
    return False


def policy_present() -> bool:
    """Compatibilidad: exige la regla solicitada o una acción instalada."""
    return policy_rule_is_well_formed() or policy_action_is_installed()


def is_installed() -> bool:
    """API compatible con Settings: solo informa del estado de la política."""
    return policy_present()


def install() -> tuple[bool, str]:
    """Instala o actualiza la política con pkexec, sin capturar credenciales."""
    was_old = policy_action_is_installed() and not policy_action_is_current()
    try:
        result = install_policy(_POLICY_SOURCE)
    except (AgentError, OSError) as exc:
        return False, str(exc)
    if result.returncode != 0:
        return False, (result.stderr or result.stdout or "La instalación fue cancelada o falló.").strip()
    return True, ("Políticas antiguas de agent-yelena actualizadas correctamente."
                  if was_old else "Políticas de agent-yelena instaladas correctamente.")


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
    kwargs.setdefault("text", True)
    kwargs.setdefault("check", False)
    kwargs.setdefault("close_fds", True)
    kwargs["shell"] = False
    return subprocess.run(command, **kwargs)


def popen(operation: str, args: Sequence[str], **kwargs) -> subprocess.Popen[str]:
    """Inicia XBPS para streaming de salida, también sin shell."""
    command = build_command(operation, args)
    kwargs.setdefault("text", True)
    kwargs.setdefault("close_fds", True)
    kwargs["shell"] = False
    return subprocess.Popen(command, **kwargs)


def install_policy(source: Path) -> subprocess.CompletedProcess[str]:
    """Instala una política ya empaquetada usando ``pkexec install``.

    No usa ``sh -c``. La fuente debe ser un archivo regular; la operación de
    instalación solo escribe en una ruta fija de polkit.
    """
    source = source.resolve()
    if not source.is_file() or "\x00" in str(source):
        raise AgentError("Fuente de política inválida")
    target = Path("/usr/share/polkit-1/actions/org.ylsoft.policy")
    pkexec = shutil.which("pkexec")
    installer = shutil.which("install")
    if not pkexec or not installer:
        raise AgentError("Se requieren pkexec e install")
    return subprocess.run(
        [pkexec, installer, "-o", "root", "-g", "root", "-m", "0644", str(source), str(target)],
        shell=False, close_fds=True, text=True, check=False,
    )


__all__ = [
    "AgentError", "CACHE_SECONDS", "POLICY_RULE", "POLICY_VERSION", "policy_present",
    "policy_action_is_current",
    "policy_rule_is_well_formed", "policy_action_is_installed",
    "is_installed", "install",
    "build_command", "build_xbps_command", "build_privileged_command",
    "authorize",
    "run", "popen", "install_policy",
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
