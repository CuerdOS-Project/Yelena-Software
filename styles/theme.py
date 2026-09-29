"""Sistema de temas de yl-soft.

Temas disponibles (clave guardada en settings.ini -> "theme"):

  auto            Estilo nativo de la plataforma (comportamiento histórico:
                   no se fuerza QStyle ni QSS ni paleta).
  csds            CSDS (CuerdOS Software Design Standard) por Yelena: tema
                   oscuro propio sobre Fusion, con el acento de marca fijo.
  yelena_system   Igual que CSDS pero el color de acento se toma del sistema
                   (KDE AccentColor / GNOME accent-color) en vez del fijo de
                   marca; si no se detecta ninguno, usa el verde de CuerdOS.
  breeze          Estilo Breeze de KDE (si está instalado en el sistema).
  fusion          QStyle "Fusion" de Qt sin QSS propio (paleta por defecto
                   de Fusion, ni oscura ni CSDS).

apply_theme(app, key) se llama una vez al arrancar (main.py) y también en
caliente desde Ajustes cuando el tema no requiere reinicio.
"""

from __future__ import annotations

import os
import re
import subprocess
from typing import Optional

from PySide6.QtWidgets import QApplication

THEME_AUTO          = "auto"
THEME_CSDS          = "csds"
THEME_YELENA_SYSTEM = "yelena_system"
THEME_BREEZE        = "breeze"
THEME_FUSION        = "fusion"

# Orden de presentación en el selector de Ajustes
THEME_KEYS = [THEME_AUTO, THEME_CSDS, THEME_YELENA_SYSTEM, THEME_BREEZE, THEME_FUSION]

# Etiquetas i18n asociadas a cada tema (clave de core.i18n.tr)
THEME_LABEL_KEYS = {
    THEME_AUTO:          "theme_auto",
    THEME_CSDS:          "theme_yelena_original",
    THEME_YELENA_SYSTEM: "theme_yelena_system",
    THEME_BREEZE:        "theme_breeze",
    THEME_FUSION:        "theme_fusion",
}

_BRAND_ACCENT   = "#61bd95"   # acento único de Yelena Original (ver CSDS_Preview.py):
                              # el mismo color se usa en TODOS los widgets
                              # (botones, checks, sliders, tabs, nav...),
                              # nunca varía entre secciones de la app.

CSDS_QSS_TEMPLATE = """
* { font-family: cantarell, sans-serif; font-size: 13px; outline: none; }
QMainWindow { background: #383838; }
QWidget { background: #383838; color: #ebebeb; }
QPushButton { border: 1px solid transparent; border-radius: 6px; padding: 6px 16px; color: #ebebeb; }
QPushButton:hover { background: #444444; border-color: #7a7a7a; }
QPushButton:pressed { background: #303030; }
QPushButton:checked { background: {ACCENT}; color: #ffffff; }
QPushButton:disabled { background: #303030; color: #606060; border-color: #404040; }
QPushButton:default { border: 2px solid {ACCENT}; }
QPushButton:flat { border: none; background: transparent; }
QPushButton:flat:hover { background: #444444; }
QLabel { border: none; background: transparent; color: #ebebeb; }
QRadioButton { color: #ebebeb; spacing: 6px; font-size: 13px; background: transparent; border: none; }
QRadioButton::indicator { width: 16px; height: 16px; border-radius: 8px; border: 2px solid #5c5c5c; background: #383838; }
QRadioButton::indicator:checked { background: {ACCENT}; border-color: {ACCENT}; image: none; }
QRadioButton::indicator:hover { border-color: #7a7a7a; }
QRadioButton:hover { color: {ACCENT}; }
QCheckBox { color: #ebebeb; font-size: 13px; spacing: 6px; background: transparent; border: none; }
QCheckBox::indicator { width: 16px; height: 16px; border-radius: 4px; border: 2px solid #5c5c5c; background: #383838; }
QCheckBox::indicator:checked { background: {ACCENT}; border-color: {ACCENT}; }
QCheckBox::indicator:hover { border-color: #7a7a7a; }
QCheckBox::indicator:indeterminate { background: {ACCENT}; border-color: {ACCENT}; }
QComboBox { background: #383838; color: #ebebeb; border: 1px solid #5c5c5c; border-radius: 6px; padding: 4px 10px; min-width: 120px; }
QComboBox:hover { border-color: #7a7a7a; }
QComboBox::drop-down { border: none; width: 20px; }
QComboBox::down-arrow { image: none; border: none; }
QComboBox QAbstractItemView { background: #303030; color: #ebebeb; selection-background-color: {ACCENT}; border: 1px solid #5c5c5c; selection-color: #ffffff; }
QSlider::groove:horizontal { height: 4px; background: #4a4a4a; border-radius: 2px; }
QSlider::handle:horizontal { background: {ACCENT}; width: 16px; height: 16px; margin: -6px 0; border-radius: 8px; border: 2px solid {ACCENT}; }
QSlider::sub-page:horizontal { background: {ACCENT}; border-radius: 2px; }
QSlider::groove:vertical { width: 4px; background: #4a4a4a; border-radius: 2px; }
QSlider::handle:vertical { background: {ACCENT}; width: 16px; height: 16px; margin: 0 -6px; border-radius: 8px; border: 2px solid {ACCENT}; }
QSlider::sub-page:vertical { background: {ACCENT}; border-radius: 2px; }
QSpinBox { background: #383838; color: #ebebeb; border: 1px solid #5c5c5c; border-radius: 6px; padding: 4px 8px; min-width: 72px; }
QSpinBox:focus { border-color: {ACCENT}; }
QSpinBox::up-button, QSpinBox::down-button { width: 18px; background: #4a4a4a; border-radius: 3px; }
QDoubleSpinBox { background: #383838; color: #ebebeb; border: 1px solid #5c5c5c; border-radius: 6px; padding: 4px 8px; min-width: 72px; }
QDoubleSpinBox:focus { border-color: {ACCENT}; }
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button { width: 18px; background: #4a4a4a; border-radius: 3px; }
QLineEdit { background: #383838; color: #ebebeb; border: 1px solid #5c5c5c; border-radius: 6px; padding: 5px 10px; }
QLineEdit:focus { border-color: {ACCENT}; }
QLineEdit:disabled { color: #606060; }
QTextEdit { background: #202020; color: #c0c0c0; border: 1px solid #4a4a4a; border-radius: 6px; font-family: monospace; font-size: 11px; padding: 8px; }
QPlainTextEdit { background: #202020; color: #c0c0c0; border: 1px solid #4a4a4a; border-radius: 6px; font-family: monospace; font-size: 11px; padding: 8px; }
QListWidget { background: #303030; color: #ebebeb; border: 1px solid #4a4a4a; border-radius: 8px; outline: none; }
QListWidget::item { padding: 9px 14px; border-radius: 5px; }
QListWidget::item:selected { background: {ACCENT}; color: #ffffff; }
QListWidget::item:hover { background: #444444; }
QListView { background: #303030; color: #ebebeb; border: 1px solid #4a4a4a; border-radius: 8px; outline: none; }
QListView::item:selected { background: {ACCENT}; color: #ffffff; }
QTreeView { background: #303030; color: #ebebeb; border: 1px solid #4a4a4a; border-radius: 8px; outline: none; alternate-background-color: #282828; }
QTreeView::item:selected { background: {ACCENT}; color: #ffffff; }
QTreeView::item:hover { background: #444444; }
QHeaderView::section { background: #4a4a4a; color: #ebebeb; padding: 4px 8px; border: none; font-weight: bold; }
QTableWidget { background: #303030; color: #ebebeb; border: 1px solid #4a4a4a; border-radius: 8px; gridline-color: #4a4a4a; outline: none; }
QTableWidget::item:selected { background: {ACCENT}; color: #ffffff; }
QTableWidget::item:hover { background: #444444; }
QTabWidget::pane { border: 1px solid #4a4a4a; border-radius: 8px; background: #303030; }
QTabBar::tab { background: transparent; color: #c0c0c0; padding: 8px 16px; border: none; border-bottom: 2px solid transparent; }
QTabBar::tab:selected { color: {ACCENT}; border-bottom: 2px solid {ACCENT}; }
QTabBar::tab:hover { color: #ebebeb; }
QProgressBar { background: #4a4a4a; border: 1px solid #5c5c5c; border-radius: 4px; text-align: center; color: #ffffff; min-height: 18px; }
QProgressBar::chunk { background: {ACCENT}; border-radius: 3px; }
QScrollBar:vertical { background: #383838; width: 8px; border-radius: 4px; margin: 0; }
QScrollBar::handle:vertical { background: #5c5c5c; border-radius: 4px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: #7a7a7a; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }
QScrollBar:horizontal { background: #383838; height: 8px; border-radius: 4px; margin: 0; }
QScrollBar::handle:horizontal { background: #5c5c5c; border-radius: 4px; min-width: 30px; }
QScrollBar::handle:horizontal:hover { background: #7a7a7a; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: none; }
QScrollArea { border: none; background: #383838; }
QScrollArea > QWidget > QWidget { background: #383838; }
QGroupBox { color: #ebebeb; border: 1px solid #4a4a4a; border-radius: 8px; margin-top: 12px; padding-top: 16px; font-weight: bold; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 6px; }
QMenu { background: #303030; color: #ebebeb; border: 1px solid #4a4a4a; border-radius: 6px; padding: 4px; }
QMenu::item { padding: 6px 24px; border-radius: 4px; }
QMenu::item:selected { background: {ACCENT}; color: #ffffff; }
QMenu::separator { height: 1px; background: #4a4a4a; margin: 4px 8px; }
QMenuBar { background: #303030; color: #ebebeb; border-bottom: 1px solid #4a4a4a; }
QMenuBar::item { padding: 6px 12px; border-radius: 4px; }
QMenuBar::item:selected { background: {ACCENT}; color: #ffffff; }
QToolBar { background: #303030; border-bottom: 1px solid #4a4a4a; spacing: 4px; padding: 2px; }
QToolButton { background: transparent; color: #ebebeb; border: none; border-radius: 6px; padding: 6px 10px; }
QToolButton:hover { background: #444444; }
QToolButton:pressed { background: #303030; }
QToolButton:checked { background: {ACCENT}; color: #ffffff; }
QStatusBar { background: #303030; color: #808080; border-top: 1px solid #4a4a4a; }
QDialog { background: #303030; color: #ebebeb; }
QToolTip { background: #282828; color: #ebebeb; border: 1px solid #5c5c5c; border-radius: 4px; padding: 4px; }
QFrame#hsep { background: #4a4a4a; max-height: 1px; border: none; }
QFrame#vsep { background: #4a4a4a; max-width: 1px; border: none; }
QSplitter::handle { background: #4a4a4a; }
QSplitter::handle:hover { background: {ACCENT}; }
QDial { background: #303030; }
QDial::groove { background: #4a4a4a; height: 4px; border-radius: 2px; }
QDial::handle { background: {ACCENT}; width: 16px; height: 16px; border-radius: 8px; }
QScrollBar { border: none; }
"""


def _accent_from_palette(app: QApplication) -> Optional[str]:
    """Toma el color de acento directamente de la paleta activa de Qt
    (QPalette.Highlight). Esto funciona sin importar qué motor de estilo
    esté detrás -- Kvantum, Breeze o Fusion -- porque los tres aplican su
    color de selección a esa misma entrada de paleta antes de que
    apply_theme() la sobreescriba con la nuestra. Es la fuente preferida
    porque refleja el tema *realmente activo*, no solo el de KDE/GNOME."""
    try:
        from PySide6.QtGui import QPalette
        color = app.palette().color(QPalette.Highlight)
        if color.isValid() and color.alpha() > 0:
            # Colores casi negros/blancos no son un "acento" útil, suelen
            # ser el resultado de no tener ningún tema con color propio.
            if 24 < (color.red() + color.green() + color.blue()) < 730:
                return color.name()
    except Exception:
        pass
    return None


def _accent_from_kdeglobals() -> Optional[str]:
    """Lee el color de acento de KDE desde ~/.config/kdeglobals.

    KDE Plasma guarda el acento en la sección [General] como
    "AccentColor=r,g,b" (Plasma 5.24+) o, si el usuario no fijó uno
    explícito, se puede aproximar con [Colors:Selection] Background.
    """
    path = os.path.expanduser("~/.config/kdeglobals")
    if not os.path.isfile(path):
        return None
    try:
        text = open(path, "r", encoding="utf-8").read()
    except OSError:
        return None

    def _rgb_to_hex(raw: str) -> Optional[str]:
        parts = [p.strip() for p in raw.split(",")]
        if len(parts) != 3:
            return None
        try:
            r, g, b = (int(p) for p in parts)
            return f"#{r:02x}{g:02x}{b:02x}"
        except ValueError:
            return None

    m = re.search(r"^\[General\](?:\n[^\[].*)*?^AccentColor=([\d,\s]+)$", text, re.M)
    if m:
        hexcolor = _rgb_to_hex(m.group(1))
        if hexcolor:
            return hexcolor

    m = re.search(r"^\[Colors:Selection\](?:\n[^\[].*)*?^Background=([\d,\s]+)$", text, re.M)
    if m:
        hexcolor = _rgb_to_hex(m.group(1))
        if hexcolor:
            return hexcolor

    return None


def _accent_from_gnome() -> Optional[str]:
    """Lee el acento de GNOME 47+ vía gsettings (org.gnome.desktop.interface
    accent-color), que devuelve un nombre ("blue", "green"...) no un hex."""
    _GNOME_ACCENTS = {
        "blue":   "#3584e4",
        "teal":   "#2190a4",
        "green":  "#3a944a",
        "yellow": "#c88800",
        "orange": "#ed5b00",
        "red":    "#e62d42",
        "pink":   "#d56199",
        "purple": "#9141ac",
        "slate":  "#6f8396",
    }
    try:
        r = subprocess.run(
            ["gsettings", "get", "org.gnome.desktop.interface", "accent-color"],
            capture_output=True, text=True, timeout=2,
        )
    except Exception:
        return None
    if r.returncode != 0:
        return None
    name = r.stdout.strip().strip("'\"").lower()
    return _GNOME_ACCENTS.get(name)


def detect_system_accent_color(app: Optional[QApplication] = None) -> str:
    """Intenta detectar el color de acento del tema realmente activo
    (Kvantum, Breeze o Fusion), en ese orden de preferencia:

      1. Paleta activa de Qt (funciona con Kvantum/Breeze/Fusion tal cual
         estén configurados en el sistema en ese momento).
      2. kdeglobals (respaldo si la paleta no trae nada útil).
      3. gsettings de GNOME (respaldo si tampoco hay KDE).

    Si ninguna de las tres da un color, se usan los nativos de Yelena
    (el mismo acento de "Yelena Original") en vez de inventar uno.
    """
    if app is not None:
        color = _accent_from_palette(app)
        if color:
            return color
    for probe in (_accent_from_kdeglobals, _accent_from_gnome):
        try:
            color = probe()
        except Exception:
            color = None
        if color:
            return color
    return _BRAND_ACCENT


def _apply_csds(app: QApplication, accent: str) -> None:
    app.setStyle("Fusion")
    app.setStyleSheet(CSDS_QSS_TEMPLATE.replace("{ACCENT}", accent))


def _apply_breeze(app: QApplication) -> bool:
    """Intenta activar el QStyle "breeze" (paquete qt6-styleplugins /
    breeze en el sistema). Devuelve False y cae a Fusion si no está
    disponible, para no dejar la app sin estilo."""
    from PySide6.QtWidgets import QStyleFactory
    available = {s.lower(): s for s in QStyleFactory.keys()}
    for candidate in ("breeze", "breeze-dark"):
        if candidate in available:
            app.setStyleSheet("")
            app.setStyle(available[candidate])
            return True
    # No hay plugin de Breeze instalado: usar Fusion neutro como respaldo
    app.setStyleSheet("")
    app.setStyle("Fusion")
    return False


def apply_theme(app: QApplication, theme_key: Optional[str] = None) -> str:
    """Aplica el tema `theme_key` ("auto"/"csds"/"yelena_system"/"breeze"/
    "fusion"). Si no se pasa, lo lee de core.settings. Devuelve la clave
    realmente aplicada (por si "breeze" no estaba disponible y se usó
    Fusion de respaldo)."""
    if theme_key is None:
        import core.settings as app_settings
        theme_key = app_settings.get("theme", THEME_AUTO)

    if theme_key == THEME_CSDS:
        _apply_csds(app, _BRAND_ACCENT)
        return THEME_CSDS
    if theme_key == THEME_YELENA_SYSTEM:
        # Importante: detectar el acento ANTES de tocar setStyle/setStyleSheet,
        # mientras la paleta de `app` todavía refleja el tema activo del
        # sistema (Kvantum/Breeze/Fusion/lo que sea).
        accent = detect_system_accent_color(app)
        _apply_csds(app, accent)
        return THEME_YELENA_SYSTEM
    if theme_key == THEME_BREEZE:
        ok = _apply_breeze(app)
        return THEME_BREEZE if ok else THEME_FUSION
    if theme_key == THEME_FUSION:
        app.setStyleSheet("")
        app.setStyle("Fusion")
        return THEME_FUSION

    # "auto" (o cualquier valor desconocido): estilo nativo de la
    # plataforma, sin QSS ni paleta propios. Comportamiento histórico.
    app.setStyleSheet("")
    return THEME_AUTO


# Compat: nombres usados antes por main.py (no-ops en la práctica ya que
# apply_theme() es ahora el único punto de entrada real).

def setup_style(app: QApplication) -> None:
    apply_theme(app)


def setup_palette(app: QApplication, *_args, **_kwargs) -> None:
    return
