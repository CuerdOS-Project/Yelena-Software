"""Carga y cachea traducciones desde translations/<lang>.json."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Optional

# Directorio donde viven los JSON de traducción
_TRANSLATIONS_DIR = Path(__file__).resolve().parent.parent / "translations"

# Cache en memoria: { lang_code: { key: value } }
_cache: Dict[str, Dict[str, str]] = {}


def _load(lang: str) -> Dict[str, str]:
    """Carga y cachea el JSON de un idioma. Nunca lanza excepciones."""
    if lang in _cache:
        return _cache[lang]

    path = _TRANSLATIONS_DIR / f"{lang}.json"
    try:
        with open(path, encoding="utf-8") as f:
            data: Dict[str, str] = json.load(f)
        _cache[lang] = data
        return data
    except FileNotFoundError:
        print(f"[translations] Archivo no encontrado: {path}")
        _cache[lang] = {}
        return {}
    except json.JSONDecodeError as exc:
        print(f"[translations] JSON inválido en {path}: {exc}")
        _cache[lang] = {}
        return {}


def get(lang: str, key: str, fallback_lang: str = "en") -> Optional[str]:
    """
    Devuelve la traducción de una clave en el idioma indicado.
    Si no existe, intenta el idioma de fallback (inglés).
    Si tampoco existe, devuelve None.
    """
    table = _load(lang)
    value = table.get(key)
    if value is not None:
        return value
    if lang != fallback_lang:
        return _load(fallback_lang).get(key)
    return None


def available_languages() -> list[str]:
    """Lista los idiomas disponibles como archivos JSON en el directorio."""
    try:
        return sorted(
            p.stem for p in _TRANSLATIONS_DIR.glob("*.json")
        )
    except Exception:
        return []


def reload(lang: str) -> None:
    """Fuerza la recarga del JSON de un idioma (útil en desarrollo)."""
    _cache.pop(lang, None)
    _load(lang)


def reload_all() -> None:
    """Recarga todos los idiomas cacheados."""
    langs = list(_cache.keys())
    _cache.clear()
    for lang in langs:
        _load(lang)
