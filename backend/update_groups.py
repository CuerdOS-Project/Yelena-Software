"""Stable package-family groups used by the filtered system-updates view."""
from __future__ import annotations

GROUP_ORDER = (
    "cuerdos_base",
    "desktop",
    "python3",
    "qemu",
    "xorg",
    "xf86",
    "other",
)

GROUP_TRANSLATION_KEYS = {
    "cuerdos_base": "updates_group_cuerdos_base",
    "desktop": "updates_group_desktop",
    "python3": "updates_group_python3",
    "qemu": "updates_group_qemu",
    "xorg": "updates_group_xorg",
    "xf86": "updates_group_xf86",
    "other": "updates_group_other",
}


def update_package_group(name: str) -> str:
    """Return the stable group id for a system package name."""
    low = (name or "").strip().lower()

    if low.startswith(("linux", "gnu", "glibc", "musl", "cuerdos", "yel-soft", "ramjet", "skycatcher")):
        return "cuerdos_base"
    if low == "python3" or low.startswith(("python3-", "python3.")):
        return "python3"
    if low == "qemu" or low.startswith("qemu-"):
        return "qemu"
    if low == "xorg" or low.startswith(("xorg-", "xorgproto")):
        return "xorg"
    if low == "xf86" or low.startswith("xf86-"):
        return "xf86"
    if any(token in low for token in ("xfce", "gnome", "kde", "plasma", "mate", "cinnamon")):
        return "desktop"
    return "other"
