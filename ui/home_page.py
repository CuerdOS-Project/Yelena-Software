# Yelena Software — Home / Explore page

from __future__ import annotations
from typing import List

from PySide6.QtCore import Qt, Signal, QThread, Slot, QTimer
from PySide6.QtGui import QPainter, QBrush, QPalette
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QScrollArea, QFrame, QGridLayout, QSizePolicy,
    QStackedWidget,
)

from backend.models import Package
from backend.system_detect import show_xbps, show_flatpak
from .icon_widget import AppIconWidget
from .native_widgets import (
    BadgeLabel, CardFrame, FeaturedBannerFrame, ChipNavButton,
    MultilineElideLabel, BackButton, animate_grid_reflow,
    CategoryTileButton, CategoryPill, attach_hover_lift,
)
from core.i18n import tr, tr_category, get_current_language


def _pkg_fallback_icon(pkg: Package) -> str:
    """Extrae el icono de categoría guardado en los tags del paquete."""
    for tag in pkg.tags:
        if tag.startswith("_category_icon:"):
            return tag[len("_category_icon:"):]
    return ""


def _child_is_button(widget, pos) -> bool:
    from PySide6.QtWidgets import QAbstractButton
    return isinstance(widget.childAt(pos), QAbstractButton)


CATEGORIES = [
    "All", "Create", "Work", "Play", "Socialise", "Learn", "Develop",
]

# SVG propios (line-icons, trazo blanco) para cada categoría: no
# dependen del tema de iconos del sistema (que puede no traer
# "applications-graphics" y similares, o traerlos en un color que no
# contraste con el degradado del tile).
_SVG_HEAD = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
             'fill="none" stroke="#FFFFFF" stroke-width="1.8" '
             'stroke-linecap="round" stroke-linejoin="round">')

CATEGORY_ICONS_SVG: dict[str, str] = {
    # Crear: lápiz / edición
    "Create": (_SVG_HEAD +
               '<path d="M12 20h9"/>'
               '<path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/>'
               '</svg>'),
    # Trabajo: maletín
    "Work": (_SVG_HEAD +
             '<path d="M3 8.5h18v9a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 17.5v-9Z"/>'
             '<path d="M8.5 8.5V6a2 2 0 0 1 2-2h3a2 2 0 0 1 2 2v2.5"/>'
             '<path d="M3 12.5h18"/>'
             '</svg>'),
    # Jugar: mando de videojuegos
    "Play": (_SVG_HEAD +
              '<path d="M7 9h10a3.5 3.5 0 0 1 3.5 3.5v1.8a2.2 2.2 0 0 1-3.9 1.4L15 14H9l-1.6 1.7a2.2 2.2 0 0 1-3.9-1.4v-1.8A3.5 3.5 0 0 1 7 9Z"/>'
              '<path d="M8 11v2.4M6.8 12.2h2.4"/>'
              '<path d="M16 11.6h.01M17.6 13.2h.01"/>'
              '</svg>'),
    # Socializar: burbuja de chat
    "Socialise": (_SVG_HEAD +
                  '<path d="M4 5.5A2.5 2.5 0 0 1 6.5 3h11A2.5 2.5 0 0 1 20 5.5v7A2.5 2.5 0 0 1 17.5 15H9l-4 4v-4H6.5A2.5 2.5 0 0 1 4 12.5v-7Z"/>'
                  '</svg>'),
    # Aprender: libro abierto
    "Learn": (_SVG_HEAD +
              '<path d="M12 6.5C10.5 5 8 4 4 4v13c4 0 6.5 1 8 2.5"/>'
              '<path d="M12 6.5C13.5 5 16 4 20 4v13c-4 0-6.5 1-8 2.5"/>'
              '<path d="M12 6.5v13"/>'
              '</svg>'),
    # Desarrollar: corchetes de código "</>"
    "Develop": (_SVG_HEAD +
                '<path d="M9 8 4.5 12 9 16"/>'
                '<path d="M15 8l4.5 4-4.5 4"/>'
                '</svg>'),
}

# Pareja de colores (degradado) por categoría, usados en los tiles 3x2
# de "Explorar". Mismos 6 grupos que GNOME Software (Create, Work,
# Play, Socialise, Learn, Develop), cada uno con su propio degradado
# para que se distingan a simple vista.
CATEGORY_META: dict[str, tuple[str, str]] = {
    "All":       ("#8AAB4A", "#5F8A31"),  # verde CuerdOS
    "Create":    ("#9C6ADE", "#6C4AB6"),
    "Work":      ("#E0B93B", "#C98A2E"),
    "Play":      ("#E1487A", "#B92E5C"),
    "Socialise": ("#F97F6A", "#E85A48"),
    "Learn":     ("#34C77B", "#1F9F5E"),
    "Develop":   ("#5B5B68", "#33333D"),
}

# Cada paquete conserva su categoría granular original (Internet,
# Graphics, System, etc., asignada por los backends) para mostrarla en
# el detalle; para agrupar/filtrar en la UI la reducimos a los 6 grupos
# de arriba.
CATEGORY_GROUP_MAP: dict[str, str] = {
    "Internet":    "Socialise",
    "Multimedia":  "Create",
    "Graphics":    "Create",
    "Games":       "Play",
    "Development": "Develop",
    "Utilities":   "Work",
    "System":      "Work",
    "Science":     "Learn",
    "Education":   "Learn",
    "Libraries":   "Develop",
    "Other":       "Work",
}


def _pkg_group(pkg: Package) -> str:
    """Grupo (uno de los 6 de CATEGORIES) al que pertenece un paquete,
    a partir de su categoría granular original."""
    return CATEGORY_GROUP_MAP.get(pkg.category, "Work")

_GRID_COLS    = 4
_CARD_H       = 160
_CARD_SPACING = 10
_POPULAR_APPS_LIMIT = 30

_SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]


# Loading screen

class _LoadingScreen(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("loadingScreen")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addStretch(2)

        inner = QVBoxLayout()
        inner.setSpacing(14)
        inner.setAlignment(Qt.AlignHCenter)

        self._frame_idx = 0
        self._spinner_lbl = QLabel(_SPINNER_FRAMES[0])
        self._spinner_lbl.setAlignment(Qt.AlignCenter)
        self._spinner_lbl.setObjectName("loadingSpinner")
        inner.addWidget(self._spinner_lbl, alignment=Qt.AlignHCenter)

        dots_row = QWidget()
        dots_lay = QHBoxLayout(dots_row)
        dots_lay.setContentsMargins(0, 0, 0, 0)
        dots_lay.setSpacing(8)
        self._dot_lbls = []
        for _ in range(3):
            dot = QLabel("●")
            dot.setAlignment(Qt.AlignCenter)
            dot.setObjectName("loadingDots")
            dots_lay.addWidget(dot)
            self._dot_lbls.append(dot)
        self._set_dots_colors(0)
        inner.addWidget(dots_row, alignment=Qt.AlignHCenter)

        title = QLabel(tr("loading_catalog"))
        title.setAlignment(Qt.AlignCenter)
        title.setObjectName("loadingTitle")
        inner.addWidget(title, alignment=Qt.AlignHCenter)

        self._sub = QLabel(tr("loading_preparing"))
        self._sub.setAlignment(Qt.AlignCenter)
        self._sub.setObjectName("loadingSubtitle")
        inner.addWidget(self._sub, alignment=Qt.AlignHCenter)

        # Indicadores por módulo eliminados (no necesarios en la UI)
        self._mod_labels: dict[str, QLabel] = {}

        outer.addLayout(inner)
        outer.addStretch(3)

        self._spin_timer = QTimer(self)
        self._spin_timer.setInterval(80)
        self._spin_timer.timeout.connect(self._tick_spinner)
        self._spin_timer.start()

        self._dots_timer = QTimer(self)
        self._dots_timer.setInterval(500)
        self._dots_timer.setProperty("_step", 0)
        self._dots_timer.timeout.connect(self._tick_dots)
        self._dots_timer.start()

    def set_module_loading(self, src_key: str):
        """No-op: indicadores de módulo eliminados."""

    def set_module_done(self, src_key: str, count: int):
        """No-op: indicadores de módulo eliminados."""

    def _tick_spinner(self):
        self._frame_idx = (self._frame_idx + 1) % len(_SPINNER_FRAMES)
        self._spinner_lbl.setText(_SPINNER_FRAMES[self._frame_idx])

    def _set_dots_colors(self, active_idx: int) -> None:
        for i, dot in enumerate(self._dot_lbls):
            pal = dot.palette()
            active = i == active_idx
            color = pal.color(QPalette.WindowText if active else QPalette.Mid)
            pal.setColor(QPalette.WindowText, color)
            dot.setPalette(pal)

    def _tick_dots(self):
        step = self._dots_timer.property("_step")
        self._set_dots_colors(step % 3)
        self._dots_timer.setProperty("_step", step + 1)

    def stop(self):
        self._spin_timer.stop()
        self._dots_timer.stop()


# App cards

class AppCard(CardFrame):
    clicked = Signal(object)

    def __init__(self, pkg: Package, parent=None):
        super().__init__(parent)
        self.pkg = pkg
        self.setObjectName("appCard")
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(_CARD_H)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        attach_hover_lift(self, max_blur=18.0)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(4)

        # Icono + badge
        top = QHBoxLayout()
        icon = AppIconWidget(
            pkg.name, icon_name=pkg.icon_name, icon_url=pkg.icon_url,
            size=40, radius=10,
            fallback_icon_name=_pkg_fallback_icon(pkg),
        )
        top.addWidget(icon)
        top.addStretch()

        badge = BadgeLabel(pkg.source_label, pkg.badge_type, show_icon=False)
        top.addWidget(badge)
        layout.addLayout(top)

        # El nombre ocupa una línea y se elide si no cabe.
        name_lbl = MultilineElideLabel(pkg.name, max_lines=1)
        name_lbl.setObjectName("cardAppName")
        layout.addWidget(name_lbl)

        # Descripción — se envuelve a exactamente 2 líneas y se elide con
        desc_lbl = MultilineElideLabel(pkg.summary, max_lines=2)
        desc_lbl.setObjectName("cardAppDesc")
        layout.addWidget(desc_lbl)

        # Versión y peso
        meta_row = QHBoxLayout()
        meta_row.setSpacing(8)
        if pkg.version:
            ver_lbl = QLabel(f"v{pkg.version}")
            ver_lbl.setObjectName("cardVersion")
            meta_row.addWidget(ver_lbl)
        if pkg.size_bytes > 0:
            size_lbl = QLabel(pkg.size_str)
            size_lbl.setObjectName("cardSize")
            meta_row.addWidget(size_lbl)
        meta_row.addStretch()
        if pkg.is_installed:
            ins = BadgeLabel(tr("badge_installed"), "installed", show_icon=False)
            meta_row.addWidget(ins)
        layout.addLayout(meta_row)

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        self.clicked.emit(self.pkg)


class FeaturedBanner(FeaturedBannerFrame):
    clicked = Signal(object)

    def __init__(self, pkg: Package, parent=None):
        super().__init__(parent)
        self.pkg = pkg
        self.setObjectName("featuredBanner")
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(110)
        self.setMaximumHeight(150)
        attach_hover_lift(self, max_blur=20.0)

        # "ICONO + NOMBRE" en una fila, y la descripción debajo — todo
        # centrado como bloque (sin tag, sin versión/peso, sin botón).
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 14, 20, 14)
        layout.setSpacing(6)
        layout.setAlignment(Qt.AlignCenter)

        head_row = QHBoxLayout()
        head_row.setSpacing(10)

        icon = AppIconWidget(
            pkg.name, icon_name=pkg.icon_name, icon_url=pkg.icon_url,
            size=40, radius=10,
            fallback_icon_name=_pkg_fallback_icon(pkg),
        )
        head_row.addWidget(icon)

        name = QLabel(pkg.name)
        name.setObjectName("featuredTitle")
        name_font = name.font()
        name_font.setPointSize(18)
        name_font.setBold(True)
        name.setFont(name_font)
        head_row.addWidget(name)

        head_wrap = QWidget()
        head_wrap.setLayout(head_row)
        layout.addWidget(head_wrap, alignment=Qt.AlignHCenter)

        desc = QLabel(pkg.summary)
        desc.setObjectName("featuredDesc")
        desc.setWordWrap(True)
        desc.setAlignment(Qt.AlignCenter)
        layout.addWidget(desc)

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        self.clicked.emit(self.pkg)


class _CarouselDots(QWidget):
    """Indicador de página del carrusel (puntitos), dibujado a mano con
    QPainter para mantener el mismo esquema de theming del resto de la app
    (nada de QSS)."""
    dot_clicked = Signal(int)

    _DOT_D      = 7
    _DOT_GAP    = 8

    def __init__(self, parent=None):
        super().__init__(parent)
        self._count = 0
        self._current = 0
        self._hover_index = -1
        self.setFixedHeight(self._DOT_D + 6)
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)

    def set_count(self, count: int):
        self._count = count
        self._current = 0
        self._update_width()
        self.update()

    def set_current(self, index: int):
        self._current = index
        self.update()

    def _update_width(self):
        if self._count <= 0:
            self.setFixedWidth(0)
            return
        w = self._count * self._DOT_D + (self._count - 1) * self._DOT_GAP
        self.setFixedWidth(w)

    def _dot_rect(self, i: int):
        x = i * (self._DOT_D + self._DOT_GAP)
        y = (self.height() - self._DOT_D) // 2
        return x, y, self._DOT_D, self._DOT_D

    def _index_at(self, x: int) -> int:
        step = self._DOT_D + self._DOT_GAP
        if step <= 0:
            return -1
        idx = x // step
        return idx if 0 <= idx < self._count else -1

    def mouseMoveEvent(self, event):
        self._hover_index = self._index_at(event.position().x())
        self.update()

    def leaveEvent(self, event):
        self._hover_index = -1
        self.update()

    def mousePressEvent(self, event):
        idx = self._index_at(event.position().x())
        if idx >= 0:
            self.dot_clicked.emit(idx)

    def paintEvent(self, _event):
        if self._count <= 1:
            return
        pal = self.palette()
        color_on = pal.color(QPalette.Highlight)
        # QPalette.Mid puede ser casi invisible sobre fondos oscuros (se
        # confunde con el propio fondo). Usamos el color de texto con
        # transparencia para que el punto inactivo siempre tenga contraste,
        # sea cual sea el tema/paleta activos.
        color_off = pal.color(QPalette.WindowText)
        color_off.setAlpha(90)
        color_hover = pal.color(QPalette.WindowText)
        color_hover.setAlpha(180)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        for i in range(self._count):
            x, y, w, h = self._dot_rect(i)
            if i == self._current:
                color = color_on
            elif i == self._hover_index:
                color = color_hover
            else:
                color = color_off
            p.setBrush(QBrush(color))
            p.drawEllipse(x, y, w, h)
        p.end()


class TopAppsCarousel(QWidget):
    """Carrusel de apps destacadas: un único banner grande (mismo estilo
    que el destacado de portada) que se pasa de página con flechas o con
    los puntitos — no son 3 tarjetas separadas."""
    app_selected = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pkgs: List[Package] = []
        self._index = 0

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)

        row = QHBoxLayout()
        row.setSpacing(6)

        self._btn_prev = ChipNavButton("left")
        self._btn_prev.setFixedSize(28, 110)
        self._btn_prev.clicked.connect(self._go_prev)
        row.addWidget(self._btn_prev)

        self._stack = QStackedWidget()
        row.addWidget(self._stack, stretch=1)

        self._btn_next = ChipNavButton("right")
        self._btn_next.setFixedSize(28, 110)
        self._btn_next.clicked.connect(self._go_next)
        row.addWidget(self._btn_next)

        outer.addLayout(row)

        self._dots = _CarouselDots()
        self._dots.dot_clicked.connect(self._go_to)
        outer.addWidget(self._dots, alignment=Qt.AlignHCenter)

    def set_packages(self, pkgs: List[Package]):
        while self._stack.count():
            w = self._stack.widget(0)
            self._stack.removeWidget(w)
            w.deleteLater()

        self._pkgs = pkgs
        for pkg in pkgs:
            banner = FeaturedBanner(pkg)
            banner.clicked.connect(self.app_selected)
            self._stack.addWidget(banner)

        self._index = 0
        self._dots.set_count(len(pkgs))
        multi = len(pkgs) > 1
        self._btn_prev.setVisible(multi)
        self._btn_next.setVisible(multi)
        self._update_page()

    def _go_prev(self):
        if self._pkgs:
            self._go_to((self._index - 1) % len(self._pkgs))

    def _go_next(self):
        if self._pkgs:
            self._go_to((self._index + 1) % len(self._pkgs))

    def _go_to(self, index: int):
        if not self._pkgs:
            return
        self._index = index % len(self._pkgs)
        self._update_page()

    def _update_page(self):
        self._stack.setCurrentIndex(self._index)
        self._dots.set_current(self._index)


# Catalog loader thread

class CatalogLoader(QThread):
    """
    Carga el catálogo de aplicaciones progresivamente:
    1. Lee data.kn al instante → emite apps_ready para mostrar la UI
    2. Enriquece con datos vivos de cada backend → emite source_done(key, count)
       para que la pantalla de carga muestre el progreso por módulo.
    """
    apps_ready  = Signal(list)          # resultado inicial (data.kn)
    source_done = Signal(str, int)      # (source_key, count) al terminar cada módulo

    def run(self):
        import sys, os
        sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

        # 1. Catálogo local (data.kn) — rápido
        apps = []
        try:
            from core.catalog import load_catalog, enrich_with_installed_status
            apps = load_catalog(lang=get_current_language())
            if apps:
                apps = enrich_with_installed_status(apps)
        except Exception as e:
            print(f"[catalog] {e}")

        # Fallback a Flatpak featured si el catálogo está vacío
        if not apps:
            try:
                from backend import flatpak_backend
                apps = flatpak_backend.get_featured(limit=12)
            except Exception:
                apps = []

        self.apps_ready.emit(apps)

        # 2. Actualización viva por módulo (no bloquea la UI)
        # Cada módulo carga independientemente; si uno tarda, los demás
        # ya muestran sus datos.
        if self.isInterruptionRequested():
            return

        import core.settings as app_settings
        from backend.system_detect import show_xbps, show_flatpak

        if show_flatpak() and app_settings.get_bool("show_flatpak"):
            if not self.isInterruptionRequested():
                try:
                    from backend import flatpak_backend
                    fp_pkgs = [p for p in apps if p.source.value == "flatpak"]
                    self.source_done.emit("flatpak", len(fp_pkgs))
                except Exception:
                    self.source_done.emit("flatpak", 0)

        if show_xbps() and app_settings.get_bool("show_xbps"):
            if not self.isInterruptionRequested():
                try:
                    xb_pkgs = [p for p in apps if p.source.value == "xbps"]
                    self.source_done.emit("xbps", len(xb_pkgs))
                except Exception:
                    self.source_done.emit("xbps", 0)


# Main page

class HomePage(QWidget):
    app_selected = Signal(object)
    explore_shown = Signal()  # se volvió a la vista principal (no categoría)
    category_opened = Signal(str)  # se pulsó un tile de categoría

    _PAGE_LOADING  = 0
    _PAGE_CONTENT  = 1
    _PAGE_CATEGORY = 2

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_category = "All"
        self._all_packages: List[Package] = []
        self._cards_cache: List[AppCard] = []
        self._category_cards_cache: List[AppCard] = []
        self._grid_cols = _GRID_COLS

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._stack = QStackedWidget()
        root.addWidget(self._stack)

        self._loading_screen = _LoadingScreen()
        self._stack.addWidget(self._loading_screen)       # idx 0

        content_page = QWidget()
        content_layout = QVBoxLayout(content_page)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        content_layout.addWidget(self._scroll)

        container = QWidget()
        self._v = QVBoxLayout(container)
        self._v.setContentsMargins(20, 16, 20, 20)
        self._v.setSpacing(12)
        self._scroll.setWidget(container)

        # Top apps (destacadas): fila con hasta 3 apps top_app = true.
        # Sin encabezado de sección: el propio banner (nombre grande +
        # descripción, ver FeaturedBanner) ya deja claro qué es.
        self._top_apps_carousel = TopAppsCarousel()
        self._top_apps_carousel.app_selected.connect(self.app_selected)
        self._v.addWidget(self._top_apps_carousel)

        # Nota: la barra de categorías (chips) se movió a la sidebar
        # (ver main_window._build_sidebar); ya no se dibuja acá.

        # Grid 3x2 de categorías (estilo GNOME Software: Create, Work,
        # Play, Socialise, Learn, Develop), antes de "Explorar apps".
        # Ya no vive en la cinta superior de main_window: esa fila queda
        # ahora dedicada solo a la búsqueda.
        self._cat_tiles_container = QWidget()
        self._cat_tiles_grid = QGridLayout(self._cat_tiles_container)
        self._cat_tiles_grid.setSpacing(_CARD_SPACING)
        for cat in CATEGORIES[1:]:  # sin "All"
            c1, c2 = CATEGORY_META.get(cat, ("#6366F1", "#4347B0"))
            icon_svg = CATEGORY_ICONS_SVG.get(cat, "")
            idx = CATEGORIES[1:].index(cat)
            tile = CategoryTileButton(icon_svg, tr_category(cat), (c1, c2), idx)
            tile.clicked.connect(lambda checked=False, c=cat: self._on_tile_clicked(c))
            self._cat_tiles_grid.addWidget(tile, idx // 3, idx % 3)
        for col in range(3):
            self._cat_tiles_grid.setColumnStretch(col, 1)
        self._v.addWidget(self._cat_tiles_container)

        # Section header
        self._section_hdr = QLabel(tr("browse_apps"))
        self._section_hdr.setObjectName("sectionHeader")
        self._v.addWidget(self._section_hdr)

        # Grid 4 columns — solo las apps más populares (top N), no la
        # biblioteca completa; para eso están ahora las páginas de categoría.
        self._grid_container = QWidget()
        self._grid_container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self._grid = QGridLayout(self._grid_container)
        self._grid.setSpacing(_CARD_SPACING)
        self._grid.setAlignment(Qt.AlignTop)
        for col in range(self._grid_cols):
            self._grid.setColumnStretch(col, 1)
        self._v.addWidget(self._grid_container)
        self._v.addStretch()

        self._stack.addWidget(content_page)              # idx 1

        # Página de categoría: cabecera (volver + título) + grid con toda
        # la biblioteca de esa categoría.
        category_page = QWidget()
        category_layout = QVBoxLayout(category_page)
        category_layout.setContentsMargins(0, 0, 0, 0)
        category_layout.setSpacing(0)

        cat_header = QWidget()
        cat_header_lay = QHBoxLayout(cat_header)
        cat_header_lay.setContentsMargins(20, 16, 20, 8)
        cat_header_lay.setSpacing(12)
        # Guardado como atributo para poder actualizar su margen izquierdo/
        # derecho en _apply_responsive_layout y que "Volver" quede siempre
        # alineado con el margen real de las tarjetas (que se centran al
        # 80% del ancho de la ventana), en vez de quedar fijo en 20px.
        self._cat_header_lay = cat_header_lay
        self._cat_back_btn = BackButton(tr("back"))
        self._cat_back_btn.clicked.connect(self._show_explore)
        cat_header_lay.addWidget(self._cat_back_btn)
        cat_header_lay.addStretch()
        # Píldora centrada con el nombre y el degradado de la categoría
        # activa (ver _open_category), para que quede claro en cuál
        # estamos aunque se haya entrado por un tile o por el buscador.
        self._cat_pill = CategoryPill(tr_category("Create"), CATEGORY_META["Create"])
        cat_header_lay.addWidget(self._cat_pill)
        cat_header_lay.addStretch()
        # Espaciador del mismo ancho que el botón "Volver" para que la
        # píldora quede realmente centrada en la fila, no solo entre
        # los dos stretch.
        cat_header_lay.addSpacing(self._cat_back_btn.sizeHint().width())
        category_layout.addWidget(cat_header)

        self._cat_scroll = QScrollArea()
        self._cat_scroll.setWidgetResizable(True)
        self._cat_scroll.setFrameShape(QFrame.NoFrame)
        self._cat_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        category_layout.addWidget(self._cat_scroll)

        cat_container = QWidget()
        self._cat_v = QVBoxLayout(cat_container)
        self._cat_v.setContentsMargins(20, 0, 20, 20)
        self._cat_scroll.setWidget(cat_container)

        self._cat_grid_container = QWidget()
        self._cat_grid_container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self._cat_grid = QGridLayout(self._cat_grid_container)
        self._cat_grid.setSpacing(_CARD_SPACING)
        self._cat_grid.setAlignment(Qt.AlignTop)
        self._cat_v.addWidget(self._cat_grid_container)
        self._cat_v.addStretch()

        self._stack.addWidget(category_page)              # idx 2

        self._stack.setCurrentIndex(self._PAGE_LOADING)

        loader = CatalogLoader(self)
        loader.apps_ready.connect(self._on_catalog_loaded)
        loader.source_done.connect(self._on_module_done)

        # Marcar cada módulo como "cargando" antes de arrancar el hilo
        for src_key in ["flatpak", "xbps"]:
            show_fn = {"flatpak": show_flatpak, "xbps": show_xbps}[src_key]
            if show_fn():
                self._loading_screen.set_module_loading(src_key)

        QTimer.singleShot(50, loader.start)

    @Slot(str, int)
    def _on_module_done(self, src_key: str, count: int):
        """Actualiza el indicador de módulo en la pantalla de carga."""
        try:
            self._loading_screen.set_module_done(src_key, count)
        except Exception:
            pass

    @Slot(list)
    def _on_catalog_loaded(self, apps: list):
        self._loading_screen.stop()
        if apps:
            self._all_packages = apps

            self._apply_responsive_layout()
            self._render_top_apps(apps)
            self._render_grid(self._popular())
        self._stack.setCurrentIndex(self._PAGE_CONTENT)

    def _render_top_apps(self, apps: List[Package]):
        """Muestra hasta 3 apps top_app = true (Vivaldi, VLC, LibreOffice)
        como páginas de un único carrusel de banners."""
        top_names = ["vivaldi", "vlc", "libreoffice"]
        by_name = {p.name.strip().lower(): p for p in apps if getattr(p, "top_app", False)}
        top_pkgs = [by_name[n] for n in top_names if n in by_name]

        if not top_pkgs:
            self._top_apps_carousel.setVisible(False)
            self._top_apps_carousel.set_packages([])
            return

        self._top_apps_carousel.setVisible(True)
        self._top_apps_carousel.set_packages(top_pkgs)

    def _on_tile_clicked(self, category: str) -> None:
        self.category_opened.emit(category)
        self._open_category(category)

    def open_category(self, category: str) -> None:
        """API pública: usada por la sidebar (ver main_window) para saltar
        directo a la página de una categoría."""
        self._open_category(category)

    def show_root(self) -> None:
        """API pública: vuelve a la vista principal de Explorar (top apps /
        populares), salga de donde salga (categoría o ya en la raíz)."""
        self._show_explore()

    def _open_category(self, category: str):
        """Navega a la página de categoría con TODA la biblioteca de esa
        categoría (no un top-N, a diferencia de "Explorar apps")."""
        self._current_category = category
        colors = CATEGORY_META.get(category, ("#6366F1", "#4347B0"))
        self._cat_pill.set_category(tr_category(category), colors)
        pkgs = [p for p in self._all_packages if _pkg_group(p) == category]
        self._render_category_grid(pkgs)
        self._stack.setCurrentIndex(self._PAGE_CATEGORY)
        animate_grid_reflow(self._cat_pill)
        animate_grid_reflow(self._cat_grid_container)

    def _show_explore(self):
        self._stack.setCurrentIndex(self._PAGE_CONTENT)
        self.explore_shown.emit()

    def _popular(self) -> List[Package]:
        """Las apps más populares para "Explorar apps" (antes mostraba la
        biblioteca completa filtrable por categoría; ahora eso vive en las
        páginas de categoría, y aquí solo entran las top N)."""
        ranked = sorted(
            self._all_packages,
            key=lambda p: (getattr(p, "rating", 0.0), p.name.lower()),
            reverse=True,
        )
        return ranked[:_POPULAR_APPS_LIMIT]

    def _compute_cols(self) -> int:
        w = self._content_width()
        if w < 460:  return 1
        if w < 680:  return 2
        if w < 920:  return 3
        return 4

    def _content_width(self) -> int:
        """Ancho del área de contenido (destacadas + grid): 80% del ancho
        de la ventana, para que "Explorar" no se estire de punta a
        punta en pantallas anchas (ver _apply_responsive_layout)."""
        return max(360, int(self.width() * 0.8))

    def _apply_responsive_layout(self):
        """Ajusta márgenes y nº de columnas al ancho actual. Se llama en
        resizeEvent, showEvent y al terminar de cargar el catálogo, ya
        que el primer resizeEvent puede no llegar a tiempo (o no llegar
        nunca) si la ventana ya nace con el tamaño final."""
        w = self.width()
        margin = max(12, (w - self._content_width()) // 2)
        self._v.setContentsMargins(margin, 16, margin, 20)
        self._cat_v.setContentsMargins(margin, 0, margin, 20)
        # El header de categoría ("Volver" + píldora) usa el mismo margen
        # horizontal que las tarjetas de abajo, para que el botón quede
        # siempre alineado con el ancho real del contenido.
        self._cat_header_lay.setContentsMargins(margin, 16, margin, 8)

        new_cols = self._compute_cols()
        if new_cols != self._grid_cols:
            self._grid_cols = new_cols
            if self._all_packages:
                self._render_grid(self._popular())
                animate_grid_reflow(self._grid_container)
                if self._stack.currentIndex() == self._PAGE_CATEGORY:
                    pkgs = [p for p in self._all_packages
                            if _pkg_group(p) == self._current_category]
                    self._render_category_grid(pkgs)
                    animate_grid_reflow(self._cat_grid_container)

    def showEvent(self, event):
        super().showEvent(event)
        self._apply_responsive_layout()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_responsive_layout()

    def _render_grid(self, packages: List[Package]):
        while self._grid.count():
            self._grid.takeAt(0)
        for card in self._cards_cache:
            card.deleteLater()
        self._cards_cache = []

        cols = self._grid_cols
        # Reset all column stretches
        for col in range(_GRID_COLS):
            self._grid.setColumnStretch(col, 0)
        for col in range(cols):
            self._grid.setColumnStretch(col, 1)

        for i, pkg in enumerate(packages):
            card = AppCard(pkg)
            card.clicked.connect(self.app_selected)
            self._grid.addWidget(card, i // cols, i % cols)
            self._cards_cache.append(card)

    def _render_category_grid(self, packages: List[Package]):
        """Igual que _render_grid pero para la página de categoría (toda
        la biblioteca de esa categoría, sin límite de N)."""
        while self._cat_grid.count():
            self._cat_grid.takeAt(0)
        for card in self._category_cards_cache:
            card.deleteLater()
        self._category_cards_cache = []

        cols = self._grid_cols
        for col in range(_GRID_COLS):
            self._cat_grid.setColumnStretch(col, 0)
        for col in range(cols):
            self._cat_grid.setColumnStretch(col, 1)

        for i, pkg in enumerate(packages):
            card = AppCard(pkg)
            card.clicked.connect(self.app_selected)
            self._cat_grid.addWidget(card, i // cols, i % cols)
            self._category_cards_cache.append(card)
