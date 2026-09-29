# yl-soft — Extracción automática del ícono embebido de un AppImage.
#
# La mayoría de los AppImage traen su .desktop y su ícono empaquetados
# adentro (imagen SquashFS). El propio binario sabe descomprimirse con
# el flag estándar "--appimage-extract", así que no dependemos de
# herramientas externas (unsquashfs, etc.) para esto.

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

_CACHE_DIR = Path.home() / ".cache" / "yelena-software" / "appimage-icons"

_ICON_LINE_RE = re.compile(r"^Icon=(.+)$", re.MULTILINE)


def _find_desktop_icon_name(root: Path) -> Optional[str]:
    """Busca la clave Icon= dentro del primer .desktop encontrado."""
    for desktop in root.rglob("*.desktop"):
        try:
            text = desktop.read_text(errors="ignore")
        except OSError:
            continue
        m = _ICON_LINE_RE.search(text)
        if m:
            return m.group(1).strip()
    return None


def _find_icon_file(root: Path, icon_name: Optional[str]) -> Optional[Path]:
    """Busca el archivo de ícono real, probando primero por el nombre
    declarado en el .desktop, luego por ".DirIcon" (symlink estándar de
    AppImage al ícono principal) y, en último caso, cualquier imagen
    suelta cerca de la raíz."""
    candidates: list[Path] = []

    diricon = root / ".DirIcon"
    if diricon.exists():
        candidates.append(diricon)

    if icon_name:
        for ext in (".png", ".svg", ".xpm"):
            candidates += sorted(root.rglob(f"{icon_name}{ext}"))

    if not candidates:
        for ext in ("png", "svg"):
            candidates += sorted(root.glob(f"*.{ext}"))

    for c in candidates:
        try:
            if c.exists() and c.stat().st_size > 0:
                return c
        except OSError:
            continue
    return None


def extract_icon(appimage_path: str, timeout: int = 20) -> Optional[str]:
    """Extrae (best-effort) el ícono embebido de un AppImage y lo deja
    cacheado en disco. Devuelve la ruta al ícono cacheado o None si no
    se pudo detectar/extraer nada (nunca lanza excepciones)."""
    if not appimage_path or not os.path.isfile(appimage_path):
        return None

    try:
        st = os.stat(appimage_path)
        os.chmod(appimage_path, st.st_mode | 0o111)
    except OSError:
        pass

    try:
        with tempfile.TemporaryDirectory(prefix="yl-appimage-icon-") as tmp:
            try:
                subprocess.run(
                    [appimage_path, "--appimage-extract"],
                    cwd=tmp, capture_output=True, timeout=timeout, check=False,
                )
            except Exception:
                return None

            root = Path(tmp) / "squashfs-root"
            if not root.is_dir():
                return None

            icon_name = _find_desktop_icon_name(root)
            icon_file = _find_icon_file(root, icon_name)
            if not icon_file:
                return None

            try:
                real = icon_file.resolve()
            except OSError:
                real = icon_file

            _CACHE_DIR.mkdir(parents=True, exist_ok=True)
            dest = _CACHE_DIR / f"{Path(appimage_path).stem}{real.suffix or '.png'}"
            shutil.copyfile(real, dest)
            return str(dest)
    except Exception:
        return None
