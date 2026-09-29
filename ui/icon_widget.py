# Vera_Shop — AppIconWidget

from __future__ import annotations

import os
import hashlib
from pathlib import Path

from PySide6.QtCore import Qt, QSize, QRectF, QTimer, QThread, Signal
from PySide6.QtGui import (
    QPainter, QColor, QBrush, QPen, QIcon, QPixmap,
    QFont, QLinearGradient, QGuiApplication,
)
from PySide6.QtWidgets import QLabel


def _screen_dpr(widget=None) -> float:
    """Factor de escala HiDPI a usar para renderizar iconos.

    Sin esto, todos los pixmaps se generaban y cacheaban a la resolución
    física del tamaño lógico pedido (p.ej. 48x48 físicos para un widget
    de 48x48 lógicos), sin marcar devicePixelRatio. En pantallas HiDPI
    (factor 2x, 1.5x, fraccional...) Qt vuelve a escalar ese pixmap "de
    1x" hacia arriba para llenar el widget, produciendo iconos borrosos
    y, en temas con capas superpuestas (badge + base), un efecto de
    "doble icono" visible al no coincidir el pixel grid. Renderizamos
    siempre al tamaño físico real de la pantalla y marcamos
    setDevicePixelRatio() en el resultado para que Qt lo trate como un
    pixmap "@Nx" y no vuelva a reescalarlo.
    """
    try:
        screen = widget.screen() if widget is not None else None
        if screen is None:
            app = QGuiApplication.instance()
            screen = app.primaryScreen() if app else None
        if screen is not None:
            dpr = screen.devicePixelRatio()
            if dpr and dpr > 0:
                return float(dpr)
    except Exception:
        pass
    return 1.0

_GRADIENTS = [
    ("#7C3AED", "#A78BFA"),
    ("#0E7490", "#38BDF8"),
    ("#065F46", "#34D399"),
    ("#92400E", "#FCD34D"),
    ("#9D174D", "#F472B6"),
    ("#1E3A8A", "#60A5FA"),
    ("#4C1D95", "#C084FC"),
    ("#7F1D1D", "#FCA5A5"),
    ("#134E4A", "#5EEAD4"),
    ("#1E1B4B", "#818CF8"),
]

# Iconos alternativos, del más específico al genérico.
_GENERIC_ICON_NAMES = [
    "package-x-generic",
    "application-x-executable",
    "applications-other",
    "application-default-icon",
]

# Disk cache directory for downloaded icons
_CACHE_DIR = Path(os.path.expanduser("~/.cache/vera_shop/icons"))

# Global cache: theme icon name → QPixmap (hit) or None (miss).
_ICON_CACHE: dict[str, "QPixmap | None"] = {}


class _IconLoadScheduler:
    """
    Throttles deferred icon loading.

    When dozens/hundreds of AppIconWidget instances are created at once
    (e.g. the Instaladas list with 100+ apps), firing QTimer.singleShot(0)
    on all of them floods the event loop in a single burst, causing visible
    stutter/garbled repaints in the scroll area. Instead, widgets register
    themselves here and get processed in small batches over time.
    """
    _queue: list = []
    _timer: "QTimer | None" = None
    _BATCH = 6

    @classmethod
    def schedule(cls, widget) -> None:
        cls._queue.append(widget)
        if cls._timer is None:
            cls._timer = QTimer()
            cls._timer.setInterval(4)
            cls._timer.timeout.connect(cls._process)
        if not cls._timer.isActive():
            cls._timer.start()

    @classmethod
    def _process(cls) -> None:
        for _ in range(cls._BATCH):
            if not cls._queue:
                cls._timer.stop()
                return
            widget = cls._queue.pop(0)
            try:
                widget._load_icon()
            except RuntimeError:
                # Widget was deleted before its turn came up
                pass
            except Exception:
                import traceback
                traceback.print_exc()


def _color_for_name(name: str) -> tuple[str, str]:
    total = sum(ord(c) for c in name)
    return _GRADIENTS[total % len(_GRADIENTS)]


def _resolve_icon(name: str, size: int, dpr: float = 1.0) -> "QPixmap | None":
    """Look up one theme icon name; return pixmap or None. Result is cached.

    Siempre se pide al tema el tamaño disponible más grande (icon.availableSizes()
    puede incluir variantes de hasta 256/512px) y luego se reescala hacia abajo
    con SmoothTransformation. Pedir directamente el tamaño del widget hace que
    Qt seleccione/rasterice el icono más pequeño disponible (p.ej. 48px) aunque
    exista una versión de mayor calidad, dando bordes borrosos al escalar.

    `dpr` es el factor HiDPI de la pantalla: el pixmap final se genera a
    `size * dpr` píxeles físicos y se marca con setDevicePixelRatio(dpr),
    para que ocupe exactamente `size` píxeles lógicos sin que Qt tenga que
    reescalarlo (lo que en HiDPI producía iconos borrosos/duplicados).
    """
    cache_key = f"{name}@{size}@{dpr}"
    if cache_key in _ICON_CACHE:
        return _ICON_CACHE[cache_key]
    icon = QIcon.fromTheme(name)
    if icon.isNull():
        _ICON_CACHE[cache_key] = None
        return None

    phys_size = max(1, round(size * dpr))

    available = icon.availableSizes()
    if available:
        best = max(available, key=lambda s: s.width())
        request_size = best if best.width() > phys_size else QSize(phys_size, phys_size)
    else:
        # Icono vectorial puro sin tamaños fijos declarados: pedir grande.
        request_size = QSize(max(phys_size * 4, 256), max(phys_size * 4, 256))

    px = icon.pixmap(request_size)
    if px.isNull() or px.width() <= 0:
        px = icon.pixmap(QSize(phys_size, phys_size))

    if px.isNull() or px.width() <= 0:
        _ICON_CACHE[cache_key] = None
        return None

    if px.width() != phys_size or px.height() != phys_size:
        px = px.scaled(QSize(phys_size, phys_size), Qt.KeepAspectRatio, Qt.SmoothTransformation)

    px.setDevicePixelRatio(dpr)
    _ICON_CACHE[cache_key] = px
    return px


def _cache_path_for_url(url: str) -> Path:
    """Return the disk cache path for a given URL."""
    digest = hashlib.md5(url.encode()).hexdigest()
    return _CACHE_DIR / f"{digest}.png"


class _IconDownloadThread(QThread):
    """Background thread: downloads an icon from URL and saves to disk cache."""
    done = Signal(str)   # emits local cache path on success

    def __init__(self, url: str, dest: Path, parent=None):
        super().__init__(parent)
        self._url = url
        self._dest = dest

    def run(self):
        try:
            from urllib.request import urlopen, Request
            req = Request(self._url, headers={"User-Agent": "Vera_Shop/1.0"})
            with urlopen(req, timeout=8) as resp:
                data = resp.read()
            self._dest.parent.mkdir(parents=True, exist_ok=True)
            self._dest.write_bytes(data)
            self.done.emit(str(self._dest))
        except Exception:
            pass


class AppIconWidget(QLabel):
    """
    Displays an app icon.

    Startup is never blocked: the gradient placeholder is shown instantly,
    then the real icon resolves after the event loop starts (QTimer delay=0).
    Resolution order:
      1. QIcon.fromTheme (cached globally)
      2. Disk-cached downloaded icon (if icon_url provided)
      3. Download icon_url in background thread + cache to disk
      4. Generic theme icon fallback
      5. Gradient placeholder (kept if nothing else works)
    """

    def __init__(
        self,
        app_name: str,
        icon_name: str = "",
        icon_url: str = "",
        size: int = 48,
        radius: int = 12,
        fallback_icon_name: str = "",   # icono de categoría cuando icon_name no está en el tema
        parent=None,
    ):
        super().__init__(parent)
        self._app_name          = app_name
        self._icon_name         = icon_name
        self._icon_url          = icon_url
        self._size              = size
        self._radius            = radius
        self._fallback_icon_name = fallback_icon_name
        self._dl_thread: _IconDownloadThread | None = None

        self.setFixedSize(QSize(size, size))
        self.setAlignment(Qt.AlignCenter)
        self._dpr = _screen_dpr(self)

        # Show gradient placeholder immediately — zero filesystem I/O
        self._render_gradient_fallback()

        # Defer real icon lookup, throttled across all icon widgets so a
        # large list (100+ apps) doesn't load every icon in one burst.
        _IconLoadScheduler.schedule(self)

    # Icon loading

    def _load_icon(self):
        try:
            self._do_load_icon()
        except RuntimeError:
            # Widget was deleted before the deferred load fired
            pass
        except Exception:
            # Cualquier otro fallo al resolver el icono (tema roto, fuente
            import traceback
            traceback.print_exc()

    def _do_load_icon(self):
        size = self._size
        dpr = self._dpr = _screen_dpr(self)

        # icon_name también puede ser una ruta local; comprobarla antes del tema.
        if self._icon_name and os.path.isabs(self._icon_name) and os.path.isfile(self._icon_name):
            px = QPixmap(self._icon_name)
            if not px.isNull() and px.width() > 0:
                self._set_pixmap_rounded(px)
                return

        # 1. Try the specific icon from the catalog (theme).
        #    Para Flatpaks, icon_name es el app-id (p.ej. "org.mozilla.firefox");
        #    si el paquete está instalado su icono aparece en el tema del sistema.
        if self._icon_name:
            px = _resolve_icon(self._icon_name, size, dpr)
            if px is not None:
                self._set_pixmap_rounded(px, already_dpr=True)
                return

        # 1b. Fallback de categoría (para Flatpaks no instalados cuyo app-id
        #     todavía no está en el tema del sistema).
        if self._fallback_icon_name:
            px = _resolve_icon(self._fallback_icon_name, size, dpr)
            if px is not None:
                self._set_pixmap_rounded(px, already_dpr=True)
                # No hacemos return: si hay icon_url seguimos para descargar
                # el icono real en background y reemplazar el de categoría.
                if not self._icon_url:
                    return

        # 2. Try disk-cached downloaded icon (from previous session)
        if self._icon_url:
            cache_path = _cache_path_for_url(self._icon_url)
            if cache_path.exists():
                px = QPixmap(str(cache_path))
                if not px.isNull() and px.width() > 0:
                    self._set_pixmap_rounded(px)
                    return
            # 3. Start background download
            self._start_download(cache_path)
            # Show generic icon while downloading (don't block)
            for generic in _GENERIC_ICON_NAMES:
                px = _resolve_icon(generic, size, dpr)
                if px is not None:
                    self._set_pixmap_rounded(px, already_dpr=True)
                    return
            return

        # 4. Fall back to a generic system icon (cached after first hit)
        for generic in _GENERIC_ICON_NAMES:
            px = _resolve_icon(generic, size, dpr)
            if px is not None:
                self._set_pixmap_rounded(px, already_dpr=True)
                return

        # 5. Nothing found — keep the gradient already displayed

    def _start_download(self, dest: Path):
        """Start a background download thread for the icon URL."""
        self._dl_thread = _IconDownloadThread(self._icon_url, dest)
        self._dl_thread.done.connect(self._on_download_done)
        self._dl_thread.start()

    def _on_download_done(self, path: str):
        """Called from the download thread when the icon file is saved."""
        try:
            px = QPixmap(path)
            if not px.isNull() and px.width() > 0:
                self._set_pixmap_rounded(px)
        except RuntimeError:
            pass

    # Rendering

    def _render_gradient_fallback(self):
        s = self._size
        dpr = getattr(self, "_dpr", None) or _screen_dpr(self)
        phys = max(1, round(s * dpr))
        pixmap = QPixmap(phys, phys)
        pixmap.fill(Qt.transparent)
        pixmap.setDevicePixelRatio(dpr)

        c1, c2 = _color_for_name(self._app_name)
        initial = (self._app_name[0] if self._app_name else "?").upper()

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)

        grad = QLinearGradient(0, 0, s, s)
        grad.setColorAt(0, QColor(c1))
        grad.setColorAt(1, QColor(c2))
        painter.setBrush(QBrush(grad))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(0, 0, s, s, self._radius, self._radius)

        font = QFont()
        font.setPointSize(max(8, int(s * 0.36)))
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QPen(QColor(255, 255, 255, 220)))
        # rect en coordenadas lógicas (s x s), no físicas: con
        # devicePixelRatio ya fijado, QPainter mapea correctamente.
        painter.drawText(QRectF(0, 0, s, s).toRect(), Qt.AlignCenter, initial)
        painter.end()

        self.setPixmap(pixmap)

    def _set_pixmap_rounded(self, src: QPixmap, already_dpr: bool = False):
        """Recorta `src` en un cuadrado redondeado de tamaño lógico
        self._size. Si `already_dpr` es True, `src` ya viene renderizado
        al tamaño físico correcto con devicePixelRatio marcado (p.ej.
        desde _resolve_icon) y no se vuelve a escalar — solo se recorta.
        En caso contrario (iconos de disco/descargados de tamaño
        arbitrario) se escala primero a physical = size * dpr.
        """
        s = self._size
        dpr = getattr(self, "_dpr", None) or _screen_dpr(self)
        phys = max(1, round(s * dpr))

        if already_dpr and src.devicePixelRatio() == dpr and src.width() == phys:
            scaled = src
        else:
            scaled = src.scaled(
                QSize(phys, phys),
                Qt.KeepAspectRatioByExpanding,
                Qt.SmoothTransformation,
            )
            scaled.setDevicePixelRatio(dpr)

        out = QPixmap(phys, phys)
        out.fill(Qt.transparent)
        out.setDevicePixelRatio(dpr)
        painter = QPainter(out)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(QBrush(scaled))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(0, 0, s, s, self._radius, self._radius)
        painter.end()
        self.setPixmap(out)
