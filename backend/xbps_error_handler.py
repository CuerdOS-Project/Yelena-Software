# (código de salida + últimas líneas de salida) en errores clasificados,

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from typing import Optional

# Códigos de error conocidos
ERR_LOCKED     = "locked"
ERR_NETWORK    = "network"
ERR_SIGNATURE  = "signature"
ERR_DISK_FULL  = "disk_full"
ERR_DEPENDENCY = "dependency"
ERR_PERMISSION = "permission"
ERR_CORRUPT    = "corrupt"
ERR_NOT_FOUND  = "not_found"
ERR_CANCELLED  = "cancelled"
ERR_UNKNOWN    = "unknown"

# Errores que probablemente sean transitorios y merecen un reintento
# automático (con un pequeño retardo) antes de molestar al usuario.
TRANSIENT_ERRORS = {ERR_NETWORK, ERR_LOCKED}

_PATTERNS: list[tuple[str, "re.Pattern[str]"]] = [
    (ERR_LOCKED, re.compile(
        r"(cannot lock|already running|lock.*busy|another.*(instance|process)|"
        r"unable to lock|pkgdb.*lock)", re.I)),
    (ERR_NETWORK, re.compile(
        r"(could not (connect|fetch|resolve)|couldn'?t (connect|resolve)|"
        r"curl error|connection (timed out|refused|reset)|"
        r"name or service not known|network is unreachable|"
        r"temporary failure in name resolution|ssl (error|handshake)|"
        r"failed to fetch|no route to host|repository .* is not signed|"
        r"could not fetch data)", re.I)),
    (ERR_SIGNATURE, re.compile(
        r"(signature|pubkey|untrusted|unverified|hash mismatch|"
        r"failed to verify)", re.I)),
    (ERR_DISK_FULL, re.compile(
        r"(no space left|disk full|not enough (free )?space)", re.I)),
    (ERR_DEPENDENCY, re.compile(
        r"(broken dependenc|missing dependenc|conflicts? with|unresolvable|"
        r"shlib .*not found|dependency loop|transaction (aborted|failed)|"
        r"unable to locate .*dependenc)", re.I)),
    (ERR_PERMISSION, re.compile(
        r"(permission denied|operation not permitted|must be run as root|"
        r"not authorized)", re.I)),
    (ERR_CORRUPT, re.compile(
        r"(corrupt|checksum mismatch|unexpected end of (file|archive)|"
        r"failed to (unpack|extract)|archive.*damaged)", re.I)),
    (ERR_NOT_FOUND, re.compile(
        r"(not found in repository|unknown package|package .* not found|"
        r"transaction dictionary is empty)", re.I)),
]

_FRIENDLY_ES: dict[str, str] = {
    ERR_LOCKED:     "La base de datos de XBPS está bloqueada por otro proceso "
                     "(otro gestor de paquetes u otra instancia de yl-soft). "
                     "Ciérralo e inténtalo de nuevo.",
    ERR_NETWORK:    "No se pudo contactar con los repositorios. Comprueba tu "
                     "conexión a internet o la configuración de repositorios "
                     "en Ajustes.",
    ERR_SIGNATURE:  "Un paquete no superó la verificación de firma o checksum. "
                     "El índice del repositorio podría estar desincronizado; "
                     "prueba a limpiar la caché y vuelve a intentarlo.",
    ERR_DISK_FULL:  "No hay suficiente espacio en disco para completar la "
                     "operación. Libera espacio e inténtalo de nuevo.",
    ERR_DEPENDENCY: "Se detectó un conflicto de dependencias. Puede que "
                     "algunos paquetes necesiten actualizarse manualmente o "
                     "en otro orden.",
    ERR_PERMISSION: "Permiso denegado. yl-soft necesita autenticación de "
                     "administrador para instalar actualizaciones.",
    ERR_CORRUPT:    "Un paquete descargado está dañado o incompleto. Limpia "
                     "la caché de paquetes e inténtalo de nuevo.",
    ERR_NOT_FOUND:  "Uno de los paquetes ya no existe en el repositorio. "
                     "Vuelve a comprobar las actualizaciones para refrescar "
                     "la lista.",
    ERR_CANCELLED:  "Operación cancelada.",
    ERR_UNKNOWN:    "Ocurrió un error inesperado al ejecutar XBPS.",
}


@dataclass
class XbpsError:
    code: str
    message: str
    detail: str = ""
    retryable: bool = False


def classify_error(output_tail: str, returncode: int = 1) -> XbpsError:
    """Clasifica un fallo de XBPS a partir de las últimas líneas de salida
    del proceso (no hace falta el log completo, con las últimas ~15-20
    líneas suele bastar)."""
    text = output_tail or ""
    for code, pattern in _PATTERNS:
        if pattern.search(text):
            return XbpsError(
                code=code,
                message=_FRIENDLY_ES.get(code, _FRIENDLY_ES[ERR_UNKNOWN]),
                detail=text.strip(),
                retryable=code in TRANSIENT_ERRORS,
            )
    return XbpsError(
        code=ERR_UNKNOWN,
        message=_FRIENDLY_ES[ERR_UNKNOWN],
        detail=text.strip(),
        retryable=False,
    )


def cancelled_error() -> XbpsError:
    return XbpsError(code=ERR_CANCELLED, message=_FRIENDLY_ES[ERR_CANCELLED])


def check_xbps_health() -> Optional[XbpsError]:
    """
    Comprueba que XBPS esté en condiciones de operar ANTES de lanzar una
    tarea (evita arrancar un proceso condenado a fallar y dar un mensaje
    de error mucho más claro). Devuelve None si todo está bien, o un
    XbpsError describiendo el problema detectado.

    Nota: deliberadamente NO se comprueba aquí si hay otro proceso
    xbps-install/xbps-remove corriendo (vía pgrep). Un intento de "lock
    check" así es propenso a falsos positivos permanentes: un proceso
    privilegiado (lanzado con pkexec) que quede huérfano tras una
    cancelación, o simplemente otra ejecución legítima en curso,
    bloquearía TODAS las operaciones futuras sin ninguna forma de
    recuperarse salvo matar el proceso a mano. Si la base de datos de
    XBPS está realmente bloqueada, xbps-install ya lo reporta con un
    error claro que classify_error() reconoce (ERR_LOCKED) y que sí es
    reintentable de forma segura.
    """
    for binary in ("xbps-query", "xbps-install", "xbps-remove"):
        if not shutil.which(binary):
            return XbpsError(
                code=ERR_NOT_FOUND,
                message=(f"No se encontró «{binary}» en el sistema. "
                         "¿Está XBPS instalado correctamente?"),
                retryable=False,
            )

    # El directorio de la base de datos de paquetes debe existir y ser
    # legible (si no, cualquier operación fallará igualmente).
    db_dir = "/var/db/xbps"
    if os.path.isdir(db_dir) and not os.access(db_dir, os.R_OK):
        return XbpsError(
            code=ERR_PERMISSION,
            message=_FRIENDLY_ES[ERR_PERMISSION],
            retryable=False,
        )

    return None
