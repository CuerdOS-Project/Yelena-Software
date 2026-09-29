#!/usr/bin/env python3
import sys
import os

# Project root on sys.path
_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _ROOT)

# Wayland / X11 platform selection
_WAYLAND_DISPLAY  = os.environ.get("WAYLAND_DISPLAY", "")
_XDG_SESSION_TYPE = os.environ.get("XDG_SESSION_TYPE", "").lower()
_on_wayland       = bool(_WAYLAND_DISPLAY) or _XDG_SESSION_TYPE == "wayland"

if _on_wayland:
    os.environ.setdefault("QT_QPA_PLATFORM",             "wayland;xcb")
    os.environ.setdefault("QT_WAYLAND_SHELL_INTEGRATION", "xdg-shell")
    os.environ.setdefault("GTK_USE_PORTAL",               "1")
    os.environ.setdefault("XCURSOR_SIZE",                 "24")
else:
    os.environ.setdefault("QT_QPA_PLATFORM", "xcb")

os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "1")
os.environ.setdefault("QT_FONT_DPI",                 "95")

# Qt imports
from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtCore    import Qt, QCoreApplication

# App settings & i18n
import core.settings as app_settings
from core.i18n import set_language, tr

# Si el idioma es "auto" se detecta del sistema en runtime sin tocar settings.ini.
_configured_lang = app_settings.get("language", "auto")
if _configured_lang in ("auto", ""):
    from core.i18n import detect_system_language
    _configured_lang = detect_system_language()
set_language(_configured_lang)

# libwx: núcleo del programa
import core.libwx as libwx

try:
    _detected_init = libwx.check_system()
except libwx.LibwxInitError as _libwx_err:
    _init_app = QApplication(sys.argv)
    _msg = QMessageBox()
    _msg.setIcon(QMessageBox.Critical)
    _msg.setWindowTitle("yl-soft — Init system incompatible")
    _msg.setTextFormat(Qt.PlainText)
    _msg.setText(
        "Este sistema no cumple los requisitos de libwx.\n\n"
        f"Init system detectado: {_libwx_err.detected_init.upper()}\n\n"
        "yl-soft requiere libwx para gestionar paquetes, "
        "notificaciones y autenticación polkit.\n\n"
        "libwx solo funciona con los siguientes gestores de inicio:\n"
        "• systemd (Debian, Ubuntu, Fedora, Arch, Void systemd…)\n"
        "• runit (Void Linux runit)\n\n"
        "Los sistemas con OpenRC (Artix, Gentoo, Alpine, etc.) "
        "no están soportados en esta versión."
    )
    _msg.setDetailedText(str(_libwx_err))
    _msg.setStandardButtons(QMessageBox.Ok)
    _msg.exec()
    sys.exit(1)

libwx.ensure_dirs()

# X11: validación del entorno de escritorio
_X11_SUPPORTED_DES = {"lxqt", "xfce", "mate", "cinnamon", "x-cinnamon"}


def _get_desktop_environment() -> str:
    for var in ("XDG_CURRENT_DESKTOP", "DESKTOP_SESSION", "XDG_SESSION_DESKTOP"):
        val = os.environ.get(var, "").lower().strip()
        if val:
            return val.split(":")[0].strip()
    return ""


if not _on_wayland:
    _current_de = _get_desktop_environment()
    _de_normalized = _current_de.replace("x-cinnamon", "cinnamon")
    if _de_normalized not in _X11_SUPPORTED_DES:
        _de_display = _current_de.upper() if _current_de else "Desconocido"
        _init_app2 = QApplication.instance() or QApplication(sys.argv)
        _warn = QMessageBox()
        _warn.setIcon(QMessageBox.Warning)
        _warn.setWindowTitle("Entorno de escritorio no compatible (X11)")
        _warn.setTextFormat(Qt.PlainText)
        _warn.setText(
            f"Escritorio detectado: {_de_display}\n\n"
            "En sesiones X11, yl-soft solo ofrece soporte oficial para:\n"
            "• LXQt\n"
            "• Xfce\n"
            "• MATE\n"
            "• Cinnamon\n\n"
            "La aplicación puede funcionar de forma limitada o mostrar "
            "problemas visuales en otros entornos X11.\n"
            "En Wayland no existe esta restricción."
        )
        _warn.setStandardButtons(QMessageBox.Ok | QMessageBox.Cancel)
        _warn.button(QMessageBox.Ok).setText("Continuar de todas formas")
        _warn.button(QMessageBox.Cancel).setText("Salir")
        if _warn.exec() == QMessageBox.Cancel:
            sys.exit(0)

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
        os.path.join(_ROOT, "yl-soft.svg"),
        os.path.join(_ROOT, "icons", "yl-soft.svg"),
        os.path.join(_ROOT, "data", "yl-soft.svg"),
        "/usr/share/pixmaps/yl-soft.svg",
        "/usr/share/icons/hicolor/scalable/apps/yl-soft.svg",
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return ""


def _parse_args():
    args   = sys.argv[1:]
    result = {"updates": False}
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--updates":
            result["updates"] = True
        i += 1
    return result



def main():
    opts = _parse_args()

    # Respetar la petición explícita de abrir Actualizaciones al reutilizar la instancia.
    ipc_cmd = "show-updates" if opts["updates"] else "show"

    if _ipc_send(ipc_cmd):
        sys.exit(0)

    # Debe coincidir con el nombre del .desktop para que el compositor
    QCoreApplication.setApplicationName("yl-soft")
    QCoreApplication.setApplicationVersion("1.0-beta10")
    QCoreApplication.setOrganizationName("CuerdOS")

    app = QApplication(sys.argv)
    app.setApplicationDisplayName(tr("app_name"))

    # Tema visual (CSDS / Yelena-sistema / Breeze / Fusion / nativo).
    # Ver styles/theme.py para el detalle de cada uno.
    import styles.theme as app_theme
    app_theme.apply_theme(app)

    # Tema de iconos: en KDE/GNOME con XDG_CURRENT_DESKTOP bien configurado
    from PySide6.QtGui import QIcon as _QIcon
    if not _QIcon.themeName():
        _QIcon.setThemeName("breeze")
    if not _QIcon.fallbackThemeName():
        _QIcon.setFallbackThemeName("hicolor")

    from PySide6.QtGui import QGuiApplication
    QGuiApplication.setDesktopFileName("yl-soft")

    from PySide6.QtGui import QIcon
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

    if opts["updates"]:
        from ui.main_window import PAGE_UPDATES
        window._nav_to(PAGE_UPDATES)

    window.show()

    _ipc_start_server(window)

    import signal as _signal

    _reload_event = _threading.Event()

    def _usr1_handler(signum, frame):
        _reload_event.set()

    try:
        _signal.signal(_signal.SIGUSR1, _usr1_handler)
    except (OSError, ValueError):
        pass

    from PySide6.QtCore import QTimer as _QTimer
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
