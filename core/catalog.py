# yl-soft — Catalog loader + search index

from __future__ import annotations

import os
import unicodedata
from typing import List, Optional, Dict

from backend.models import Package, PackageSource, PackageStatus

# Path to catalog relative to this file (project root)
_DATA_DIR     = os.path.dirname(os.path.dirname(__file__))
_CATALOG_XBPS_PATH = os.path.join(_DATA_DIR, "data", "catalog_xbps.kn")


# Parser

def _parse_kn(path: str) -> List[dict]:
    """
    Parse the KN catalog file.
    Returns a list of dicts, one per [app] block.
    """
    apps: List[dict] = []
    current: Optional[dict] = None

    with open(path, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line == "[app]":
                current = {}
                continue
            if line == "---":
                if current:
                    apps.append(current)
                current = None
                continue
            if current is not None and "=" in line:
                key, _, value = line.partition("=")
                current[key.strip()] = value.strip()

    return apps


# Apps de XBPS que también existen en Flathub: se reutiliza el mismo CDN
# de iconos (dl.flathub.org) como respaldo para cuando el paquete
# XBPS todavía NO está instalado — en ese caso QIcon.fromTheme() falla
# siempre, porque el icono de la app (logo de marca, no un icono
# genérico del sistema) recién se copia al tema local al instalarla.
# Solo se listan apps conocidas con id de Flathub estable; si la
# descarga falla igual, AppIconWidget cae al icono genérico de siempre.
_XBPS_KNOWN_FLATHUB_IDS: Dict[str, str] = {
    "vivaldi": "com.vivaldi.Vivaldi",
    "vlc": "org.videolan.VLC",
    "vscodium": "com.vscodium.codium",
    "wine": "org.winehq.Wine",
    "xonotic": "org.xonotic.Xonotic",
    "warzone2100": "net.wz2100.wz2100",
    "transmission": "com.transmissionbt.Transmission",
    "thunderbird": "org.mozilla.Thunderbird",
    "chromium": "org.chromium.Chromium",
    "firefox": "org.mozilla.firefox",
    "telegram": "org.telegram.desktop",
    "signal-desktop": "org.signal.Signal",
    "discord": "com.discordapp.Discord",
    "spotify-client": "com.spotify.Client",
    "steam": "com.valvesoftware.Steam",
    "gimp": "org.gimp.GIMP",
    "inkscape": "org.inkscape.Inkscape",
    "krita": "org.kde.krita",
    "blender": "org.blender.Blender",
    "audacity": "org.audacityteam.Audacity",
    "keepassxc": "org.keepassxc.KeePassXC",
    "qbittorrent": "org.qbittorrent.qBittorrent",
    "deluge": "org.deluge_torrent.deluge",
    "filezilla": "org.filezillaproject.Filezilla",
    "hexchat": "io.github.Hexchat",
    "flameshot": "org.flameshot.Flameshot",
    "mpv": "io.mpv.Mpv",
    "darktable": "org.darktable.Darktable",
    "rawtherapee": "com.rawtherapee.RawTherapee",
    "gcompris": "org.kde.gcompris",
    "anki": "net.ankiweb.Anki",
    "kalzium": "org.kde.kalzium",
    "alacritty": "org.alacritty.Alacritty",
    "bleachbit": "org.bleachbit.BleachBit",
    "qutebrowser": "org.qutebrowser.qutebrowser",
    "falkon": "org.kde.falkon",
    "pidgin": "im.pidgin.Pidgin",
    "kodi": "tv.kodi.Kodi",
    "scribus": "net.scribus.Scribus",
    "digikam": "org.kde.digikam",
    "pinta": "com.github.PintaProject.Pinta",
    "synfigstudio": "org.synfig.SynfigStudio",
    "openttd": "org.openttd.OpenTTD",
    "qgis": "org.qgis.qgis",
    "stellarium": "org.stellarium.Stellarium",
    "0ad": "com.play0ad.zeroad",
    "calibre-gui": "com.calibre_ebook.calibre",
    "zathura": "org.pwmt.zathura",
}


def _make_package(d: dict, lang: str = "es") -> Package:
    """Build a Package from a parsed KN dict."""
    raw_src = d.get("source", "xbps").lower()
    if raw_src == "flatpak":
        source = PackageSource.FLATPAK
    else:
        source = PackageSource.XBPS

    # Pick summary by language preference
    if lang.startswith("es") and "summary" in d:
        summary = d["summary"]
    elif "summary_en" in d:
        summary = d["summary_en"]
    else:
        summary = d.get("summary", "")

    appstream_id = d.get("appstream_id", "")

    # For Flatpak packages, build icon URL from Flathub CDN
    icon_field = d.get("icon", "")
    icon_url = d.get("icon_url", "")
    if raw_src == "flatpak" and not icon_url and appstream_id:
        icon_url = (
            f"https://dl.flathub.org/repo/appstream/x86_64/icons/128x128/{appstream_id}.png"
        )
    elif raw_src == "xbps" and not icon_url:
        # El icono de una app XBPS no instalada no existe todavía en el
        # tema local (ver _XBPS_KNOWN_FLATHUB_IDS arriba). Primero se
        # prueba si el propio catálogo ya usa un id de Flathub como
        # nombre de icono (p.ej. "org.gnome.Evince"); si no, la tabla
        # curada de apps conocidas.
        flathub_id = icon_field if "." in icon_field else _XBPS_KNOWN_FLATHUB_IDS.get(icon_field.lower(), "")
        if flathub_id:
            icon_url = f"https://dl.flathub.org/repo/appstream/x86_64/icons/128x128/{flathub_id}.png"

    try:
        size_bytes = int(d.get("size_bytes", "0"))
    except ValueError:
        size_bytes = 0

    app_id = d.get("id", f"{raw_src}:{d.get('name', '?')}")

    # (p.ej. "emacs", "0ad"). Se usa, en orden de prioridad: un "pkgname ="
    pkgname = d.get("pkgname", "")
    if not pkgname and ":" in app_id:
        pkgname = app_id.split(":", 1)[1]

    return Package(
        id=app_id,
        name=d.get("name", "Unknown"),
        summary=summary,
        description=d.get("description", ""),
        version=d.get("version", ""),
        source=source,
        status=PackageStatus.NOT_INSTALLED,
        icon_name=d.get("icon", "application-x-executable"),
        icon_url=icon_url,
        category=d.get("category", "Other"),
        size_bytes=size_bytes,
        appstream_id=appstream_id,
        developer=d.get("developer", ""),
        website=d.get("website", ""),
        license=d.get("license", ""),
        rating=float(d.get("rating", "0")),
        tags=d.get("tags", "").split(",") if d.get("tags") else [],
        featured=d.get("featured", "false").lower() == "true",
        top_app=d.get("top_app", "false").lower() == "true",
        pkgname=pkgname,
    )


# Normalisation helper

def _norm(text: str) -> str:
    """Lower-case, strip accents, collapse whitespace."""
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_text = "".join(c for c in nfkd if not unicodedata.combining(c))
    return " ".join(ascii_text.lower().split())


# CatalogIndex — the fast reference

class CatalogIndex:
    """
    In-memory reference index built once from catalog_xbps.kn.

    Lookups:
      - by_id(id)          → Package | None   (O(1) dict lookup)
      - search(query)      → List[Package]    (token-prefix matching, O(terms))
      - by_category(cat)   → List[Package]    (O(1) bucket lookup)
      - featured()         → List[Package]    (pre-filtered at build time)
    """

    def __init__(self, lang: str = "es"):
        self._lang = lang
        self._all: List[Package] = []
        self._by_id: Dict[str, Package] = {}
        # Inverted index: normalised token prefix → list of pkg ids
        self._token_index: Dict[str, List[str]] = {}
        self._by_category: Dict[str, List[Package]] = {}
        self._featured: List[Package] = []
        self._top_apps: List[Package] = []
        self._loaded = False

    def _ensure_loaded(self):
        if self._loaded:
            return

        raw_all: List[dict] = []

        if os.path.exists(_CATALOG_XBPS_PATH):
            try:
                raw_all.extend(_parse_kn(_CATALOG_XBPS_PATH))
            except Exception as exc:
                print(f"[catalog] Warning: could not load catalog_xbps.kn: {exc}")

        if not raw_all:
            self._loaded = True
            return

        # Los sets aceleran la construcción de índices; al final se convierten
        # a listas para conservar la API pública.
        token_index_sets: Dict[str, set] = {}
        by_category_sets: Dict[str, list] = {}

        # Solo se indexan prefijos de longitud >= 2: los prefijos de 1
        # letra generan listas enormes (todas las apps que empiezan por
        # "a", "e"...) que no aportan nada a la búsqueda y son la parte
        # más cara de construir.
        _MIN_PREFIX = 2

        seen_ids: set = set()
        for d in raw_all:
            pkg_id = d.get("id", "")
            if pkg_id in seen_ids:
                continue
            seen_ids.add(pkg_id)

            pkg = _make_package(d, self._lang)
            self._all.append(pkg)
            self._by_id[pkg.id] = pkg

            cat = pkg.category or "Other"
            by_category_sets.setdefault(cat, []).append(pkg)

            if d.get("featured", "false").lower() == "true":
                self._featured.append(pkg)

            if d.get("top_app", "false").lower() == "true":
                self._top_apps.append(pkg)

            searchable = " ".join([
                pkg.name,
                pkg.summary,
                pkg.category,
                pkg.developer,
                " ".join(pkg.tags),
                d.get("summary_en", ""),
            ])
            for token in set(_norm(searchable).split()):
                tok_len = len(token)
                for prefix_len in range(_MIN_PREFIX, tok_len + 1):
                    prefix = token[:prefix_len]
                    token_index_sets.setdefault(prefix, set()).add(pkg.id)
                if tok_len < _MIN_PREFIX:
                    token_index_sets.setdefault(token, set()).add(pkg.id)

        self._by_category = by_category_sets
        self._token_index = {k: list(v) for k, v in token_index_sets.items()}

        self._loaded = True

    # Public API

    def by_id(self, pkg_id: str) -> Optional[Package]:
        """O(1) lookup by package id (e.g. 'flatpak:org.mozilla.firefox')."""
        self._ensure_loaded()
        return self._by_id.get(pkg_id)

    def all_packages(self) -> List[Package]:
        """All packages from the catalog."""
        self._ensure_loaded()
        return list(self._all)

    def featured(self) -> List[Package]:
        """Only packages marked as featured = true."""
        self._ensure_loaded()
        return list(self._featured)

    def top_apps(self) -> List[Package]:
        """Only packages marked as top_app = true (destacadas en portada)."""
        self._ensure_loaded()
        return list(self._top_apps)

    def by_category(self, category: str) -> List[Package]:
        """All packages in a given category."""
        self._ensure_loaded()
        return list(self._by_category.get(category, []))

    def categories(self) -> List[str]:
        """Sorted list of all known categories."""
        self._ensure_loaded()
        return sorted(self._by_category.keys())

    def search(self, query: str, limit: int = 5000) -> List[Package]:
        """
        Fast token-prefix search against the catalog index.
        Each word in *query* must match a token prefix in the index.
        Returns packages ranked by how many query terms matched.
        """
        self._ensure_loaded()
        if not query.strip():
            return list(self._all[:limit])

        terms = _norm(query).split()
        if not terms:
            return []

        # Intersect candidate ids across all terms
        candidates: Optional[set] = None
        for term in terms:
            ids = set(self._token_index.get(term, []))
            candidates = ids if candidates is None else candidates & ids

        if not candidates:
            return []

        # Sort: name-starts-with first, then shorter names
        results = [self._by_id[pid] for pid in candidates if pid in self._by_id]
        results.sort(key=lambda p: (
            0 if _norm(p.name).startswith(_norm(terms[0])) else 1,
            len(p.name),
        ))
        return results[:limit]


# Module-level singleton

_INDEX: Optional[CatalogIndex] = None


def get_index(lang: str = "es") -> CatalogIndex:
    """Return the shared CatalogIndex singleton (built lazily on first call)."""
    global _INDEX
    if _INDEX is None or _INDEX._lang != lang:
        _INDEX = CatalogIndex(lang)
    return _INDEX


# Convenience wrappers (backward-compatible)

def load_catalog(lang: str = "es") -> List[Package]:
    """
    Load packages from catalog_xbps.kn filtered by the active package managers.
    Flatpak is shown when available on the system; XBPS is always the
    primary source.
    Returns an empty list if the file is missing or malformed.
    """
    try:
        from backend.system_detect import show_xbps, show_flatpak
        _show_xbps    = show_xbps()
        _show_flatpak = show_flatpak()
    except Exception:
        _show_xbps = _show_flatpak = True  # falla segura: mostrar todo

    pkgs = get_index(lang).all_packages()
    return [
        p for p in pkgs
        if (p.source.value == "xbps"    and _show_xbps)
        or (p.source.value == "flatpak" and _show_flatpak)
    ]


def load_featured(lang: str = "es") -> List[Package]:
    """Return only apps marked as featured = true, filtered by active sources."""
    try:
        from backend.system_detect import show_xbps, show_flatpak
        _show_xbps    = show_xbps()
        _show_flatpak = show_flatpak()
    except Exception:
        _show_xbps = _show_flatpak = True

    pkgs = get_index(lang).featured()
    return [
        p for p in pkgs
        if (p.source.value == "xbps"    and _show_xbps)
        or (p.source.value == "flatpak" and _show_flatpak)
    ]


def search_catalog(query: str, lang: str = "es", limit: int = 5000) -> List[Package]:
    """Fast catalog search filtered by active package manager sources."""
    try:
        from backend.system_detect import show_xbps, show_flatpak
        _show_xbps    = show_xbps()
        _show_flatpak = show_flatpak()
    except Exception:
        _show_xbps = _show_flatpak = True

    pkgs = get_index(lang).search(query, limit=limit * 3)  # busca más para compensar el filtro
    filtered = [
        p for p in pkgs
        if (p.source.value == "xbps"    and _show_xbps)
        or (p.source.value == "flatpak" and _show_flatpak)
    ]
    return filtered[:limit]

# Installed-status enrichment

def enrich_with_installed_status(packages: List[Package]) -> List[Package]:
    """
    Cross-reference a list of catalog packages with the installed-package
    lists from Flatpak and XBPS.  Updates each Package.status in-place.

    Each package is matched only against its own source so that, for example,
    having Firefox installed via Flatpak does NOT mark the XBPS Firefox as
    installed (and vice-versa).
    """
    # Per-source id sets  (id strings like "xbps:firefox" / "flatpak:org.mozilla.firefox")
    flatpak_ids: set = set()
    xbps_ids:    set = set()

    # Per-source name sets  (lower-cased display names)
    flatpak_names: set = set()
    xbps_names:    set = set()

    # Ambas fuentes hacen su propio subprocess (flatpak list / xbps-query)
    # de forma independiente entre sí; antes se llamaban en serie, sumando
    # sus tiempos. Al lanzarlas en paralelo, el tiempo total baja al del
    # más lento de los dos en vez de la suma de ambos.
    from concurrent.futures import ThreadPoolExecutor

    def _load_flatpak():
        from backend import flatpak_backend
        return flatpak_backend.list_installed()

    def _load_xbps():
        from backend.package_engine import get_inventory_backend
        return get_inventory_backend().list_installed()

    with ThreadPoolExecutor(max_workers=2) as pool:
        fut_flatpak = pool.submit(_load_flatpak)
        fut_xbps = pool.submit(_load_xbps)

        try:
            for p in fut_flatpak.result():
                flatpak_ids.add(p.id)
                flatpak_names.add(p.name.lower())
                if p.appstream_id:
                    flatpak_ids.add(f"flatpak:{p.appstream_id}")
        except Exception:
            pass

        try:
            for p in fut_xbps.result():
                xbps_ids.add(p.id)
                xbps_names.add(p.name.lower())
        except Exception:
            pass

    from backend.models import PackageSource, PackageStatus

    for pkg in packages:
        if pkg.source == PackageSource.FLATPAK:
            installed = pkg.id in flatpak_ids or pkg.name.lower() in flatpak_names
        elif pkg.source == PackageSource.XBPS:
            real_name = (pkg.pkgname or pkg.name).lower()
            installed = pkg.id in xbps_ids or real_name in xbps_names
        else:
            installed = False

        if installed:
            pkg.status = PackageStatus.INSTALLED

    return packages
