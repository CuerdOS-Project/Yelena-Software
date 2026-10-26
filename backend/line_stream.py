"""Lectura incremental de la salida de un comando, línea a línea.

Pensado para búsquedas de repositorio: permite mostrar cada resultado en
cuanto el proceso lo imprime y cancelar (matando el grupo de procesos) en
cuanto la búsqueda deja de interesar, sin esperar a que termine.
"""
from __future__ import annotations

import os
import select
import signal
import subprocess
import time
from typing import Callable, Iterator, Mapping


class StreamTimeout(Exception):
    """El comando superó el tiempo máximo permitido."""


def _kill_group(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    try:
        proc.wait(timeout=1.0)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


def iter_lines(
    command: list[str],
    *,
    should_stop: Callable[[], bool] | None = None,
    timeout: float = 45.0,
    env: Mapping[str, str] | None = None,
    yield_idle: bool = False,
) -> Iterator[str | None]:
    """Genera las líneas de stdout a medida que el proceso las escribe.

    - Si `should_stop()` devuelve True, el proceso se termina y el generador acaba.
    - Si pasa `timeout` segundos, se termina el proceso y se lanza StreamTimeout.
    - Si el binario no existe, el generador termina sin resultados.
    - Con `yield_idle=True` genera `None` cada ~100 ms sin salida nueva, para que
      el consumidor pueda vaciar lotes pendientes aunque el proceso esté callado.
    """
    try:
        proc = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            start_new_session=True, env=dict(env) if env is not None else None,
        )
    except OSError:
        return
    fd = proc.stdout.fileno()
    buffer = bytearray()
    deadline = time.monotonic() + timeout
    try:
        while True:
            if should_stop is not None and should_stop():
                return
            if time.monotonic() > deadline:
                raise StreamTimeout(" ".join(command[:2]))
            ready, _, _ = select.select([fd], [], [], 0.1)
            if not ready:
                if yield_idle:
                    yield None
                continue
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            buffer += chunk
            while (i := buffer.find(b"\n")) >= 0:
                line = bytes(buffer[:i]).decode("utf-8", errors="replace")
                del buffer[:i + 1]
                yield line
        if buffer:
            yield bytes(buffer).decode("utf-8", errors="replace")
    finally:
        _kill_group(proc)
        try:
            proc.stdout.close()
        except OSError:
            pass
        try:
            proc.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            pass
