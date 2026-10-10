#!/usr/bin/env python3
import sys
import os

# Project root on sys.path
_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _ROOT)

# Dejar que Qt detecte automáticamente X11 o Wayland según la sesión.
# No forzamos plugins de plataforma ni bloqueamos escritorios/init systems.
os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "1")
os.environ.setdefault("QT_FONT_DPI", "95")

# Qt imports
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QCoreApplication

# App settings & i18n
import core.settings as app_settings
from core.i18n import set_language, tr

# Si el idioma es "auto" se detecta del sistema en runtime sin tocar settings.ini.
_configured_lang = app_settings.get("language", "auto")
if _configured_lang in ("auto", ""):
    from core.i18n import detect_system_language
    _configured_lang = detect_system_language()
set_language(_configured_lang)

from ui.main_window import MainWindow

# IPC: socket Unix para instancia única
import socket    as _socket
import threading as _threading
from pathlib import Path as _Path

_IPC_SOCK_PATH = _Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "yl-soft.sock"


def _ipc_send(command: str) -> bool:
    try:
        s = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
        s.settimeout(1.5)
        s.connect(str(_IPC_SOCK_PATH))
        s.sendall((command + "\n").encode())
        s.close()
        return True
    except (ConnectionRefusedError, FileNotFoundError, OSError):
        return False


def _ipc_start_server(window: "MainWindow") -> None:
    """
    Arranca el servidor IPC en un hilo daemon.
    Comandos soportados (una línea, terminada en \\n):
      show-updates          → trae ventana al frente + navega a actualizaciones
      reload                → recarga datos de actualizaciones (sin cambiar de página)
    """
    try:
        _IPC_SOCK_PATH.unlink(missing_ok=True)
    except Exception:
        pass

    try:
        srv = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
        srv.bind(str(_IPC_SOCK_PATH))
        srv.listen(8)
        srv.settimeout(1.0)
    except OSError:
        return

    def _serve():
        while True:
            try:
                conn, _ = srv.accept()
            except _socket.timeout:
                continue
            except OSError:
                break
            try:
                data = conn.recv(4096).decode(errors="replace").strip()
                conn.close()
                _ipc_dispatch(window, data)
            except Exception:
                pass

    t = _threading.Thread(target=_serve, daemon=True)
    t.name = "yl-soft-ipc"
    t.start()


def _ipc_dispatch(window: "MainWindow", cmd: str) -> None:
    from PySide6.QtCore import QTimer as _QT

    if cmd == "show-updates":
        _QT.singleShot(0, window.raise_and_show_updates)
    elif cmd == "show":
        # Traer la ventana al frente tal cual está (sin forzar la
        # navegación a Actualizaciones): usado cuando se relanza la app
        # normalmente (icono de escritorio, terminal) y ya hay una
        # instancia corriendo, a diferencia del applet, que sí quiere
        # saltar directo a Actualizaciones.
        _QT.singleShot(0, window.raise_only)
    elif cmd == "reload":
        def _reload():
            try:
                window._updates_page._reload_from_cache()
            except Exception:
                pass
        _QT.singleShot(0, _reload)


def _find_icon_path() -> str:
    """
    Busca yl-soft.svg en las ubicaciones estándar.
    ID canónico de la app: yl-soft
    """
    candidates = [
        os.path.join(_ROOT, "resources", "yl-soft.svg"),
        "/usr/share/pixmaps/yl-soft.svg",
        "/usr/share/icons/hicolor/scalable/apps/yl-soft.svg",
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return ""


def _parse_args():
    return {"updates": "--updates" in sys.argv[1:]}


def main():
    opts = _parse_args()

    # Respetar la petición explícita de abrir Actualizaciones al reutilizar la instancia.
    ipc_cmd = "show-updates" if opts["updates"] else "show"

    if _ipc_send(ipc_cmd):
        sys.exit(0)

    # Debe coincidir con el nombre del .desktop para que el compositor
    QCoreApplication.setApplicationName("yl-soft")
    QCoreApplication.setApplicationVersion("Pre-Stable v1.0.2")
    QCoreApplication.setOrganizationName("CuerdOS")

    app = QApplication(sys.argv)
    app.setApplicationDisplayName(tr("app_name"))

    # Tema visual (CSDS / Yelena-sistema / Breeze / Fusion / nativo).
    # Ver styles/theme.py para el detalle de cada uno.
    import styles.theme as app_theme
    app_theme.apply_theme(app)

    # Tema de iconos: en KDE/GNOME con XDG_CURRENT_DESKTOP bien configurado
    from PySide6.QtGui import QGuiApplication, QIcon
    if not QIcon.themeName():
        QIcon.setThemeName("breeze")
    if not QIcon.fallbackThemeName():
        QIcon.setFallbackThemeName("hicolor")

    QGuiApplication.setDesktopFileName("yl-soft")

    icon_path = _find_icon_path()
    if icon_path:
        app_icon = QIcon(icon_path)
        if not app_icon.isNull():
            app.setWindowIcon(app_icon)
    else:
        # Fallback al tema de iconos del sistema
        for theme_name in ["package-manager", "system-software-install",
                            "applications-system", "yl-soft"]:
            icon = QIcon.fromTheme(theme_name)
            if not icon.isNull():
                app.setWindowIcon(icon)
                break

    window = MainWindow()

    # Las excepciones inesperadas se registran y se reportan en la barra de
    # estado, sin terminar el event loop de la tienda.
    import traceback as _traceback
    from PySide6.QtCore import QObject as _QObject, Signal as _Signal

    class _RuntimeErrorRelay(_QObject):
        error = _Signal(str)

    _error_relay = _RuntimeErrorRelay(window)
    _error_relay.error.connect(window.show_runtime_error)

    def _report_exception(exc_type, exc_value, exc_tb):
        detail = "".join(_traceback.format_exception(exc_type, exc_value, exc_tb))
        try:
            sys.__excepthook__(exc_type, exc_value, exc_tb)
        except Exception:
            pass
        _error_relay.error.emit(detail)

    def _report_thread_exception(args):
        _report_exception(args.exc_type, args.exc_value, args.exc_traceback)

    sys.excepthook = _report_exception
    _threading.excepthook = _report_thread_exception

    if opts["updates"]:
        from ui.main_window import PAGE_UPDATES
        window._nav_to(PAGE_UPDATES)

    window.show()

    _ipc_start_server(window)

    import signal as _signal
    from PySide6.QtCore import QTimer as _QTimer

    _reload_event = _threading.Event()

    def _usr1_handler(signum, frame):
        _reload_event.set()

    try:
        _signal.signal(_signal.SIGUSR1, _usr1_handler)
    except (OSError, ValueError):
        pass

    _sig_poll = _QTimer()
    _sig_poll.setInterval(500)

    def _on_poll():
        if _reload_event.is_set():
            _reload_event.clear()
            try:
                window._updates_page._reload_from_cache()
            except Exception:
                pass

    _sig_poll.timeout.connect(_on_poll)
    _sig_poll.start()

    ret = app.exec()

    try:
        _IPC_SOCK_PATH.unlink(missing_ok=True)
    except Exception:
        pass

    sys.exit(ret)


if __name__ == "__main__":
    main()
