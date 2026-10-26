"""API pública de internacionalización. Traducciones en translations/<lang>.json."""

from __future__ import annotations

import locale
import os

from core.translations import get as _get_translation

# Códigos de idioma
LANG_ES = "es"
LANG_EN = "en"
LANG_CA = "ca"
LANG_PT = "pt"
LANG_JA = "ja"
LANG_KO = "ko"
LANG_TR = "tr"
LANG_FR = "fr"
LANG_IT = "it"
LANG_PL = "pl"
LANG_RU = "ru"
LANG_GL = "gl"

SUPPORTED = [LANG_ES, LANG_EN, LANG_CA, LANG_PT, LANG_JA, LANG_KO, LANG_TR, LANG_FR, LANG_IT, LANG_PL, LANG_RU, LANG_GL]

# Estado interno
_current_lang: str = LANG_EN


# API pública

def detect_system_language() -> str:
    """
    Detecta el idioma del sistema y devuelve un código soportado.
    Comprueba LANGUAGE, LANG, LC_ALL, LC_MESSAGES en orden.
    Cae a locale.getlocale(). Devuelve LANG_EN si nada encaja.
    """
    for env_var in ("LANGUAGE", "LANG", "LC_ALL", "LC_MESSAGES"):
        val = os.environ.get(env_var, "")
        if val:
            code = val.split(".")[0].split("_")[0].lower()
            if code in SUPPORTED:
                return code

    try:
        loc = locale.getlocale()[0]
        if loc:
            code = loc.split("_")[0].lower()
            if code in SUPPORTED:
                return code
    except Exception:
        pass

    return LANG_EN


def set_language(lang: str) -> None:
    """Cambia el idioma activo. Usa '' o 'auto' para auto-detectar."""
    global _current_lang
    if lang in ("", "auto"):
        _current_lang = detect_system_language()
    elif lang in SUPPORTED:
        _current_lang = lang
    else:
        _current_lang = LANG_EN


def get_current_language() -> str:
    return _current_lang


def tr(key: str, **kwargs) -> str:
    """
    Traduce una clave al idioma activo.
    Soporta sustitución de {placeholder} via kwargs.
    Cae a inglés si no existe en el idioma activo, luego devuelve la clave.
    """
    text = _get_translation(_current_lang, key, fallback_lang=LANG_EN)
    if text is None:
        text = key
    if kwargs:
        try:
            text = text.format(**kwargs)
        except (KeyError, IndexError):
            pass
    return text


def tr_category(cat: str) -> str:
    """Traduce un nombre de categoría (p.ej. 'Graphics' → 'Gráficos')."""
    key = "cat_" + cat.lower().replace(" ", "_")
    result = tr(key)
    return result if result != key else cat


# Idioma inicial: inglés. El proceso principal llama set_language() al arrancar.
_current_lang = LANG_EN
