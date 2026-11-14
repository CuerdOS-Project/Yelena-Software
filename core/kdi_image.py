"""Formato KDI ofuscado usado por el easter egg de Yelena.

No es cifrado criptográfico: evita que la imagen secreta quede como PNG
directamente visible en los recursos del paquete.
"""

from __future__ import annotations

import zlib

KDI_MAGIC = b"YELENA-KDI\x01"
_KEY = b"yelena-software:kdi-image:v1"


def decode_kdi(data: bytes) -> bytes:
    """Devuelve los bytes originales de imagen de un contenedor KDI."""
    if not data.startswith(KDI_MAGIC):
        raise ValueError("Invalid KDI image header")
    encoded = data[len(KDI_MAGIC):]
    compressed = bytes(value ^ _KEY[i % len(_KEY)] for i, value in enumerate(encoded))
    try:
        return zlib.decompress(compressed)
    except zlib.error as exc:
        raise ValueError("Corrupt KDI image data") from exc


def encode_kdi(image_data: bytes) -> bytes:
    """Codifica imagen a KDI (compresión y ofuscación reversible)."""
    compressed = zlib.compress(image_data, level=9)
    encoded = bytes(value ^ _KEY[i % len(_KEY)] for i, value in enumerate(compressed))
    return KDI_MAGIC + encoded
