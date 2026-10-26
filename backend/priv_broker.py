# yl-soft — Autorización única para los `sudo` internos de AM
"""Pide la contraseña de administrador UNA sola vez por sesión de la GUI.

AM / AppMan llama a ``sudo`` varias veces durante una instalación. Antes cada
llamada se traducía en un ``pkexec`` distinto y, por tanto, en un diálogo de
contraseña distinto. Aquí el primer ``sudo`` lanza (con un único ``pkexec``)
un pequeño *broker* root que escucha en un socket Unix privado; los ``sudo``
siguientes reutilizan esa autorización, igual que la caché de sudo.

Seguridad:
* El directorio de sesión es 0700 y pertenece al usuario; el socket es 0600.
* El broker comprueba con SO_PEERCRED que quien conecta es ese mismo UID.
* El broker caduca tras ``IDLE_SECONDS`` sin uso y al cerrar la aplicación.
* El código del broker viaja como argumento de ``python3 -I -c`` (no hay
  fichero modificable entre la autenticación y la ejecución).
* No se usa shell en ningún punto; los comandos se ejecutan como argv.
"""
from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

IDLE_SECONDS = 900  # 15 minutos sin uso -> el broker termina solo

_BROKER_CODE = r'''
import json, os, pwd, select, signal, socket, struct, subprocess, sys, threading, time
path, uid, idle = sys.argv[1], int(sys.argv[2]), float(sys.argv[3])
ALLOWED_ENV = ("LANG", "LANGUAGE", "TERM", "FPM_AIMG_EVENTS")
state = {"last": time.monotonic(), "active": 0, "quit": False}
lock = threading.Lock()

def serve(conn):
    try:
        data, fds, _flags, _addr = socket.recv_fds(conn, 1 << 20, 3)
        msg = json.loads(data.decode("utf-8"))
        if msg.get("op") == "quit":
            state["quit"] = True
            conn.sendall(b'{"rc":0}')
            return
        if msg.get("op") == "ping":
            conn.sendall(b'{"rc":0}')
            return
        argv = [str(a) for a in msg["argv"]]
        if len(fds) != 3 or not argv:
            conn.sendall(b'{"rc":2}')
            return
        env = {k: v for k, v in os.environ.items() if k in ("PATH", "SHELL")}
        for key, value in (msg.get("env") or {}).items():
            if key in ALLOWED_ENV or key.startswith("LC_"):
                env[str(key)] = str(value)
        kwargs = {}
        user = msg.get("user")
        if user:
            try:
                entry = pwd.getpwnam(user)
            except KeyError:
                os.write(fds[2], ("sudo: usuario desconocido: %s\n" % user).encode())
                conn.sendall(b'{"rc":1}')
                return
            kwargs["user"] = entry.pw_uid
            kwargs["group"] = entry.pw_gid
            env.update(HOME=entry.pw_dir, USER=user, LOGNAME=user)
        else:
            env.update(HOME="/root", USER="root", LOGNAME="root")
        cwd = msg.get("cwd") or "/"
        if not os.path.isdir(cwd):
            cwd = "/"
        try:
            proc = subprocess.Popen(argv, stdin=fds[0], stdout=fds[1], stderr=fds[2],
                                    cwd=cwd, env=env, start_new_session=True,
                                    close_fds=True, **kwargs)
        except OSError as error:
            os.write(fds[2], ("sudo: %s: %s\n" % (argv[0], error.strerror or error)).encode())
            conn.sendall(b'{"rc":127}')
            return
        finally:
            for fd in fds:
                try: os.close(fd)
                except OSError: pass
        fds = []
        while proc.poll() is None:
            ready, _, _ = select.select([conn], [], [], 0.25)
            if ready:
                try:
                    gone = conn.recv(1) == b""
                except OSError:
                    gone = True
                if gone:  # el cliente murió o se canceló la tarea
                    for sig in (signal.SIGTERM,):
                        try: os.killpg(proc.pid, sig)
                        except OSError: pass
                    try: proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        try: os.killpg(proc.pid, signal.SIGKILL)
                        except OSError: pass
                    break
        rc = proc.wait()
        try:
            conn.sendall(json.dumps({"rc": rc if rc >= 0 else 128 - rc}).encode())
        except OSError:
            pass
    except Exception as error:
        try: conn.sendall(json.dumps({"rc": 1, "error": str(error)}).encode())
        except OSError: pass
    finally:
        try: conn.close()
        except OSError: pass
        with lock:
            state["active"] -= 1
            state["last"] = time.monotonic()

def main():
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    previous = os.umask(0o177)
    try:
        srv.bind(path)
    finally:
        os.umask(previous)
    os.chown(path, uid, -1, follow_symlinks=False)
    srv.listen(16)
    while not state["quit"]:
        ready, _, _ = select.select([srv], [], [], 2.0)
        if not ready:
            with lock:
                if state["active"] == 0 and time.monotonic() - state["last"] > idle:
                    break
            continue
        conn, _ = srv.accept()
        try:
            _pid, peer_uid, _gid = struct.unpack("3i", conn.getsockopt(
                socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")))
        except OSError:
            conn.close()
            continue
        if peer_uid != uid:
            conn.close()
            continue
        with lock:
            state["active"] += 1
        threading.Thread(target=serve, args=(conn,), daemon=True).start()
    try: os.unlink(path)
    except OSError: pass

main()
'''

# Shim `sudo` (Python): traduce la CLI de sudo que usa AM a peticiones al broker.
_SHIM_TEMPLATE = r'''#!{python}
import fcntl, json, os, socket, subprocess, sys, time
SESSION = {session!r}
PKEXEC = {pkexec!r}
PYTHON = {python!r}
UID = {uid}
IDLE = {idle}
BROKER_CODE = {code!r}
SOCK = os.path.join(SESSION, "broker.sock")

def fail(message, code):
    sys.stderr.write("sudo: " + message + "\n")
    sys.exit(code)

def try_connect():
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.connect(SOCK)
        return s
    except OSError:
        s.close()
        return None

def connect():
    s = try_connect()
    if s:
        return s
    with open(os.path.join(SESSION, "lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)   # solo un sudo lanza pkexec; los demás esperan
        s = try_connect()
        if s:
            return s
        if os.environ.get("FPM_AIMG_EVENTS") == "1":
            sys.stderr.write("FPM_AIMG_EVENT\t" + json.dumps(
                {{"protocol": "fpm-aimg-v1", "event": "authorization", "method": "pkexec"}}) + "\n")
            sys.stderr.flush()
        proc = subprocess.Popen(
            [PKEXEC, PYTHON, "-I", "-c", BROKER_CODE, SOCK, str(UID), str(IDLE)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True)
        while True:
            s = try_connect()
            if s:
                return s
            code = proc.poll()
            if code is not None:
                if code in (126, 127):
                    fail("autorización cancelada o denegada (pkexec código %d)" % code, code)
                fail("no se pudo iniciar la autorización (pkexec código %d)" % code, code or 1)
            time.sleep(0.05)

def main(argv):
    user = None
    mode = None
    while argv:
        a = argv[0]
        if a in ("-S", "--stdin", "-A", "--askpass", "-n", "--non-interactive",
                 "-H", "--set-home", "-E", "--preserve-env", "-i", "-s", "-b"):
            argv = argv[1:]
        elif a in ("-p", "--prompt"):
            if len(argv) < 2: fail("falta el texto de -p", 2)
            argv = argv[2:]
        elif a in ("-u", "--user"):
            if len(argv) < 2: fail("falta el usuario de -u", 2)
            user = argv[1]; argv = argv[2:]
        elif a.startswith("--user="):
            user = a[7:]; argv = argv[1:]
        elif a.startswith("-u") and len(a) > 2:
            user = a[2:]; argv = argv[1:]
        elif a in ("-v", "--validate"):
            mode = "validate"; argv = argv[1:]
        elif a in ("-k", "-K", "--reset-timestamp", "--remove-timestamp"):
            sys.exit(0)
        elif a == "--":
            argv = argv[1:]; break
        elif a.startswith("-"):
            fail("opción no compatible con pkexec: " + a, 2)
        else:
            break
    if mode == "validate" and not argv:
        connect().close()
        sys.exit(0)
    if not argv:
        fail("falta el comando", 2)
    s = connect()
    request = {{"argv": argv, "cwd": os.getcwd(), "user": user,
               "env": {{k: v for k, v in os.environ.items()
                       if k in ("LANG", "LANGUAGE", "TERM", "FPM_AIMG_EVENTS") or k.startswith("LC_")}}}}
    socket.send_fds(s, [json.dumps(request).encode()], [0, 1, 2])
    # Reenviar señales de cancelación cerrando el socket (el broker mata al hijo).
    import signal
    def stop(_sig, _frm):
        try: s.close()
        finally: os._exit(143)
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, stop)
    chunks = b""
    while True:
        part = s.recv(4096)
        if not part:
            break
        chunks += part
    try:
        rc = int(json.loads(chunks.decode("utf-8")).get("rc", 1))
    except (ValueError, AttributeError):
        rc = 1
    sys.exit(rc)

main(sys.argv[1:])
'''

_lock = threading.Lock()
_session_dir: Optional[Path] = None
_session_key: Optional[tuple] = None


def _python_path() -> str:
    for candidate in ("/usr/bin/python3", "/bin/python3"):
        if os.path.exists(candidate):
            return candidate
    return shutil.which("python3") or sys.executable


def _create_session(pkexec: str) -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    base = runtime if runtime and os.path.isdir(runtime) and os.access(runtime, os.W_OK) else None
    directory = Path(tempfile.mkdtemp(prefix="yelena-aimg-", dir=base))
    directory.chmod(0o700)
    python = _python_path()
    shim = directory / "sudo"
    shim.write_text(_SHIM_TEMPLATE.format(
        python=python, session=str(directory), pkexec=pkexec, uid=os.getuid(),
        idle=IDLE_SECONDS, code=_BROKER_CODE), encoding="utf-8")
    shim.chmod(0o700)
    return directory


def _send_quit(directory: Path) -> None:
    import json
    import socket
    sock_path = directory / "broker.sock"
    if not sock_path.exists():
        return
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(2)
            s.connect(str(sock_path))
            socket.send_fds(s, [json.dumps({"op": "quit"}).encode()], [])
            s.recv(64)
    except OSError:
        pass


def shutdown() -> None:
    """Cierra el broker (si lo hay) y borra el directorio de sesión."""
    global _session_dir, _session_key
    with _lock:
        directory, _session_dir, _session_key = _session_dir, None, None
    if directory is None:
        return
    _send_quit(directory)
    shutil.rmtree(directory, ignore_errors=True)


atexit.register(shutdown)


@contextmanager
def session_environment(pkexec: str) -> Iterator[dict]:
    """Entorno con un ``sudo`` falso que comparte UNA autorización pkexec.

    La sesión (y la autorización) persiste entre tareas hasta ``shutdown()``
    o hasta que el broker caduque por inactividad.
    """
    global _session_dir, _session_key
    key = (pkexec, os.getuid())
    with _lock:
        if _session_dir is None or _session_key != key or not (_session_dir / "sudo").exists():
            stale = _session_dir
            _session_dir = _create_session(pkexec)
            _session_key = key
            if stale is not None:
                shutil.rmtree(stale, ignore_errors=True)
        directory = _session_dir
    env = os.environ.copy()
    env["PATH"] = str(directory) + os.pathsep + env.get("PATH", "/usr/bin:/bin")
    yield env
