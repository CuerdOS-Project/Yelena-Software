"""Cross-process marker for system package update activity."""
from __future__ import annotations

import os
from pathlib import Path

UPDATE_ACTIVITY_FLAG = Path.home() / ".config" / "yl-soft" / "applet_updating.flag"


def is_system_update_active() -> bool:
    """Return whether the recorded update process is still alive.

    Empty or unreadable markers are treated as active to avoid launching a
    competing transaction while another process is creating the marker.
    Stale PID markers are removed automatically after a crashed process.
    """
    try:
        raw_pid = UPDATE_ACTIVITY_FLAG.read_text(encoding="ascii").strip()
    except FileNotFoundError:
        return False
    except OSError:
        return UPDATE_ACTIVITY_FLAG.exists()

    if not raw_pid:
        return True
    try:
        pid = int(raw_pid)
    except ValueError:
        return True
    if pid <= 0:
        return True
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        try:
            UPDATE_ACTIVITY_FLAG.unlink(missing_ok=True)
        except OSError:
            pass
        return False
    except PermissionError:
        return True
    except OSError:
        return UPDATE_ACTIVITY_FLAG.exists()


def begin_system_update() -> int | None:
    """Create the marker exclusively and return its owner PID.

    Returns ``None`` if another live update already owns the marker or if the
    marker cannot safely be created.
    """
    try:
        UPDATE_ACTIVITY_FLAG.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None

    for attempt in range(2):
        try:
            fd = os.open(
                UPDATE_ACTIVITY_FLAG,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            with os.fdopen(fd, "w", encoding="ascii") as stream:
                stream.write(str(os.getpid()))
                stream.flush()
                os.fsync(stream.fileno())
            return os.getpid()
        except FileExistsError:
            if is_system_update_active():
                return None
            if attempt == 0:
                continue
            return None
        except OSError:
            return None
    return None


def end_system_update(owner_pid: int | None = None) -> bool:
    """Remove the marker only when this process owns it."""
    owner_pid = os.getpid() if owner_pid is None else owner_pid
    try:
        current = UPDATE_ACTIVITY_FLAG.read_text(encoding="ascii").strip()
        if current != str(owner_pid):
            return False
        UPDATE_ACTIVITY_FLAG.unlink(missing_ok=True)
        return True
    except OSError:
        return False
