"""Supervisión de procesos con salida incremental para tareas de la GUI.

El lector entiende tanto saltos LF como las barras de progreso CR que suelen
emitir instaladores shell, sin esperar a que aparezca un salto de línea. Los
procesos se aíslan en su propio grupo para poder cancelar FPM y sus hijos.
"""
from __future__ import annotations

import os
import pty
import re
import selectors
import signal
import subprocess
import time
from typing import Callable, Iterator, Mapping

# Eventos: (tipo, contenido). `output` trae texto; `heartbeat` trae segundos;
# `cancelled` confirma que la terminación fue solicitada.
ProcessEvent = tuple[str, str | float | int]

# Pregunta interactiva sin salto de línea final ("... type a number and press ENTER)?").
_CHOICE_PROMPT_RE = re.compile(r"type a number", re.IGNORECASE)


def _terminate_process(proc: subprocess.Popen[bytes], grace_seconds: float = 3.0) -> None:
    """Termina el proceso y sus descendientes sin dejar AM/AppMan huérfanos."""
    if proc.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGTERM)
        else:
            proc.terminate()
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        try:
            if os.name == "posix":
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                proc.kill()
        except ProcessLookupError:
            pass
        proc.wait()


def _drain_complete_lines(buffer: bytearray, final: bool = False) -> list[str]:
    """Extrae registros terminados en LF o CR, incluyendo CRLF como uno."""
    lines: list[str] = []
    start = 0
    i = 0
    while i < len(buffer):
        byte = buffer[i]
        if byte not in (10, 13):
            i += 1
            continue
        lines.append(bytes(buffer[start:i]).decode("utf-8", errors="replace"))
        if byte == 13 and i + 1 < len(buffer) and buffer[i + 1] == 10:
            i += 1
        i += 1
        start = i
    if start:
        del buffer[:start]
    if final and buffer:
        lines.append(bytes(buffer).decode("utf-8", errors="replace"))
        buffer.clear()
    return lines


def stream_process(
    command: list[str],
    *,
    cancel_event=None,
    process_callback: Callable[[subprocess.Popen | None], None] | None = None,
    heartbeat_seconds: float = 1.0,
    env: Mapping[str, str] | None = None,
    on_choice: Callable[[], str | None] | None = None,
) -> Iterator[ProcessEvent]:
    """Ejecuta argv sin shell y transmite output/heartbeat hasta exit.

    El evento final es `("exit", código)`. La cancelación solicita SIGTERM al
    grupo del proceso, espera un margen y escala a SIGKILL si fuese necesario.

    Con `on_choice`, si el proceso pide elegir un número, se llama al callback
    (bloqueante) y su respuesta se escribe en stdin; `None` cierra stdin.
    """
    # AM solo muestra el menú de variantes si su entrada es una terminal.
    master_fd = slave_fd = None
    if on_choice is not None and os.name == "posix":
        master_fd, slave_fd = pty.openpty()
    proc = subprocess.Popen(
        command,
        stdin=slave_fd if slave_fd is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=0,
        close_fds=True,
        start_new_session=(os.name == "posix"),
        env=dict(env) if env is not None else None,
    )
    if process_callback is not None:
        process_callback(proc)
    if slave_fd is not None:
        os.close(slave_fd)
    assert proc.stdout is not None
    selector = selectors.DefaultSelector()
    buffer = bytearray()
    cancelled = False
    started = time.monotonic()
    next_heartbeat = started + heartbeat_seconds
    stream_open = True
    try:
        os.set_blocking(proc.stdout.fileno(), False)
        selector.register(proc.stdout, selectors.EVENT_READ)
        while stream_open or proc.poll() is None:
            now = time.monotonic()
            if cancel_event is not None and cancel_event.is_set() and not cancelled:
                cancelled = True
                yield ("status", "cancel")
                _terminate_process(proc)
            wait = min(0.25, max(0.0, next_heartbeat - now))
            events = selector.select(wait) if stream_open else []
            if not stream_open and wait > 0:
                time.sleep(wait)
            for key, _ in events:
                try:
                    chunk = os.read(key.fd, 65536)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(key.fileobj)
                    stream_open = False
                    for line in _drain_complete_lines(buffer, final=True):
                        if line:
                            yield ("output", line)
                    break
                buffer.extend(chunk)
                for line in _drain_complete_lines(buffer):
                    if line:
                        yield ("output", line)
                if on_choice is not None and buffer and _CHOICE_PROMPT_RE.search(
                        bytes(buffer).decode("utf-8", errors="replace")):
                    question = bytes(buffer).decode("utf-8", errors="replace").strip()
                    buffer.clear()
                    yield ("output", question)
                    answer = on_choice()
                    try:
                        if master_fd is not None:
                            # Sin respuesta: Ctrl-D (EOF) para que el script aborte.
                            os.write(master_fd, (answer + "\n").encode() if answer is not None else b"\x04")
                    except OSError:
                        pass
                # Bound memory if a misbehaving child emits an unbroken record.
                while len(buffer) > 65536:
                    piece = bytes(buffer[:65536]).decode("utf-8", errors="replace")
                    del buffer[:65536]
                    yield ("output", piece)
            now = time.monotonic()
            if now >= next_heartbeat:
                yield ("heartbeat", now - started)
                next_heartbeat = now + heartbeat_seconds
        return_code = proc.wait()
        if cancelled:
            yield ("cancelled", return_code)
        yield ("exit", return_code)
    finally:
        selector.close()
        if proc.poll() is None:
            _terminate_process(proc)
        proc.stdout.close()
        if process_callback is not None:
            process_callback(None)
        if master_fd is not None:
            try:
                os.close(master_fd)
            except OSError:
                pass
