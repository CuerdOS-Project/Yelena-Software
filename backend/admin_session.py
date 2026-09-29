"""Sesión administrativa temporal para agent-yelena.

No almacena contraseñas ni sustituye la autenticación de polkit. Solo controla
cuándo la aplicación debe volver a iniciar una operación que permita a polkit
pedir autenticación; ``always`` es el valor predeterminado.
"""
from __future__ import annotations
import time
from dataclasses import dataclass

MODES = ("always", "5m", "10m", "30m", "custom")
DEFAULT_MODE = "always"
MIN_CUSTOM_MINUTES = 1
MAX_CUSTOM_MINUTES = 120

@dataclass
class AdminSession:
    mode: str = DEFAULT_MODE
    custom_minutes: int = 5
    started_at: float | None = None

    def duration_seconds(self) -> int | None:
        if self.mode == "always":
            return 0
        if self.mode in {"5m", "10m", "30m"}:
            return int(self.mode[:-1]) * 60
        return max(MIN_CUSTOM_MINUTES, min(MAX_CUSTOM_MINUTES, int(self.custom_minutes))) * 60

    def remaining_seconds(self, now: float | None = None) -> int | None:
        duration = self.duration_seconds()
        if duration == 0:
            return 0
        if self.started_at is None:
            return None
        left = duration - int((time.time() if now is None else now) - self.started_at)
        return max(0, left)

    def expired(self, now: float | None = None) -> bool:
        if self.mode == "always":
            return True
        left = self.remaining_seconds(now)
        return left is None or left <= 0

    def begin(self, now: float | None = None) -> None:
        if self.mode != "always" and (self.started_at is None or self.expired(now)):
            self.started_at = time.time() if now is None else now

    def activate(self, now: float | None = None) -> None:
        """Inicia una ventana tras una autenticación confirmada."""
        if self.mode != "always":
            self.started_at = time.time() if now is None else now

    def reset(self) -> None:
        self.started_at = None

    def label(self) -> str:
        if self.mode == "always":
            return "Siempre preguntar"
        if self.mode == "custom":
            return f"Personalizado ({self.custom_minutes} min)"
        return self.mode.replace("m", " min")

_session = AdminSession()

def configure(mode: str = DEFAULT_MODE, custom_minutes: int = 5) -> AdminSession:
    global _session
    if mode not in MODES:
        mode = DEFAULT_MODE
    minutes = max(MIN_CUSTOM_MINUTES, min(MAX_CUSTOM_MINUTES, int(custom_minutes)))
    # Cambiar el selector no debe renovar una sesión ya iniciada. Conservamos
    # el instante original para que la ventana siga siendo de tiempo fijo.
    previous = _session if '_session' in globals() else None
    started_at = (previous.started_at
                  if previous is not None
                  and previous.mode == mode
                  and previous.custom_minutes == minutes
                  else None)
    _session = AdminSession(mode, minutes, started_at)
    return _session

def session() -> AdminSession:
    return _session

def should_start_authentication() -> bool:
    """Indica si la siguiente operación debe iniciar una nueva sesión de app."""
    return _session.expired()

def mark_authenticated() -> None:
    """Marca el inicio de una ventana después de que pkexec haya terminado bien."""
    _session.begin()

def activate_authenticated() -> None:
    """Activa inmediatamente el modo elegido después de pkcheck exitoso."""
    _session.activate()

def remaining_seconds() -> int | None:
    return _session.remaining_seconds()
