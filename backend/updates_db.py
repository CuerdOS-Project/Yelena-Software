#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Applied updates are removed automatically when a new check succeeds

from __future__ import annotations

import sqlite3
import threading
import logging
from datetime import datetime, UTC
from pathlib import Path
from typing import List, Dict, Tuple

logger = logging.getLogger(__name__)

CONFIG_DIR = Path.home() / '.config' / 'cuerdtoken'
DB_PATH    = CONFIG_DIR / 'updates.db'

# Schema version – bump if you change the schema
_SCHEMA_VERSION = 2

# 1 = gestor de paquetes / herramientas base (xbps, libxbps...)
TIER_CRITICAL = 0
TIER_PKG_MGR  = 1
TIER_NORMAL   = 2

# Estados posibles de una actualización pendiente
STATUS_PENDING    = "pending"
STATUS_INSTALLING = "installing"
STATUS_DONE       = "done"
STATUS_FAILED     = "failed"

_DDL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_info (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pending_updates (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT    NOT NULL,
    cur_version  TEXT,
    new_version  TEXT,
    arch         TEXT    DEFAULT '',
    manager      TEXT    NOT NULL,   -- 'xbps', 'flatpak'
    tier         INTEGER NOT NULL DEFAULT 2,   -- orden de instalación (ver TIER_*)
    status       TEXT    NOT NULL DEFAULT 'pending',  -- ver STATUS_*
    error        TEXT    DEFAULT '',
    first_seen   TEXT    NOT NULL,   -- ISO-8601 UTC
    last_seen    TEXT    NOT NULL    -- ISO-8601 UTC
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_pending ON pending_updates (name, manager);
"""


def _compute_tier(name: str, manager: str) -> int:
    """Calcula el tier de instalación de un paquete: crítico del sistema
    primero, luego el propio gestor de paquetes / herramientas base,
    luego el resto. Reutiliza la misma lógica de detección que el resto
    de la app para que el orden mostrado en la UI y el orden real de
    instalación coincidan siempre."""
    try:
        from .update_status import is_critical_update
        if is_critical_update(name):
            return TIER_CRITICAL
    except Exception:
        pass
    if manager == "xbps":
        try:
            from .provides import is_priority_xbps
            if is_priority_xbps(name):
                return TIER_PKG_MGR
        except Exception:
            pass
    return TIER_NORMAL


class UpdatesDB:
    """Thread-safe SQLite manager for pending package updates."""

    def __init__(self, db_path: Path = DB_PATH) -> None:
        self._db_path = db_path
        self._lock    = threading.Lock()
        self._init_db()

    # Internal helpers

    def _connect(self) -> sqlite3.Connection:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._db_path), timeout=10,
                               check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _migrate_db(self, conn: sqlite3.Connection) -> None:
        """Aplica migraciones de schema sobre tablas ya existentes."""
        cols = {
            row[1]
            for row in conn.execute(
                "PRAGMA table_info(pending_updates)"
            ).fetchall()
        }
        if "arch" not in cols:
            conn.execute(
                "ALTER TABLE pending_updates ADD COLUMN arch TEXT DEFAULT ''"
            )
            logger.info("UpdatesDB: migrated — added 'arch' column")
        if "tier" not in cols:
            conn.execute(
                "ALTER TABLE pending_updates ADD COLUMN tier INTEGER NOT NULL DEFAULT 2"
            )
            logger.info("UpdatesDB: migrated — added 'tier' column")
        if "status" not in cols:
            conn.execute(
                "ALTER TABLE pending_updates ADD COLUMN status TEXT NOT NULL DEFAULT 'pending'"
            )
            logger.info("UpdatesDB: migrated — added 'status' column")
        if "error" not in cols:
            conn.execute(
                "ALTER TABLE pending_updates ADD COLUMN error TEXT DEFAULT ''"
            )
            logger.info("UpdatesDB: migrated — added 'error' column")

    def _init_db(self) -> None:
        try:
            with self._lock:
                conn = self._connect()
                with conn:
                    conn.executescript(_DDL)
                    self._migrate_db(conn)
                    row = conn.execute(
                        "SELECT value FROM schema_info WHERE key='version'"
                    ).fetchone()
                    if row is None:
                        conn.execute(
                            "INSERT INTO schema_info VALUES ('version', ?)",
                            (str(_SCHEMA_VERSION),),
                        )
                conn.close()
                logger.debug("UpdatesDB initialised at %s", self._db_path)
        except Exception as exc:
            logger.error("UpdatesDB._init_db: %s", exc)

    # Public API

    def save_pending(self, updates: List[Dict],
                     replace_managers: tuple[str, ...] = ()) -> bool:
        """
        Replace the pending list for every manager present in *updates*.
        ``replace_managers`` additionally marks a complete snapshot: those
        managers are replaced even when they returned zero pending updates.

        Algorithm:
        1. Collect the set of managers in *updates*.
        2. For each manager, DELETE rows whose name is NOT in the new list
           (they were applied or disappeared).
        3. UPSERT every entry (insert new ones, update last_seen for existing).

        Managers not present in *updates* or *replace_managers* are untouched.
        """
        if not updates and not replace_managers:
            return True
        now = datetime.now(UTC).isoformat()
        managers: Dict[str, List[Dict]] = {}
        for u in updates:
            mgr = u.get('manager') or u.get('type') or 'unknown'
            managers.setdefault(mgr, []).append(u)
        for mgr in replace_managers:
            managers.setdefault(mgr, [])

        try:
            with self._lock:
                conn = self._connect()
                with conn:
                    for mgr, pkgs in managers.items():
                        names = [p['name'] for p in pkgs if p.get('name')]
                        if names:
                            placeholders = ','.join('?' * len(names))
                            # Remove entries that are no longer pending for this manager
                            conn.execute(
                                f"DELETE FROM pending_updates "
                                f"WHERE manager=? AND name NOT IN ({placeholders})",
                                [mgr] + names,
                            )
                        else:
                            conn.execute(
                                "DELETE FROM pending_updates WHERE manager=?", (mgr,)
                            )
                        # Upsert pending entries. El tier se recalcula siempre
                        for p in pkgs:
                            name = p.get('name', '')
                            conn.execute(
                                """
                                INSERT INTO pending_updates
                                    (name, cur_version, new_version, arch, manager, tier, status, error, first_seen, last_seen)
                                VALUES (?, ?, ?, ?, ?, ?, 'pending', '', ?, ?)
                                ON CONFLICT(name, manager) DO UPDATE SET
                                    cur_version = excluded.cur_version,
                                    new_version = excluded.new_version,
                                    arch        = excluded.arch,
                                    tier        = excluded.tier,
                                    status      = 'pending',
                                    error       = '',
                                    last_seen   = excluded.last_seen
                                """,
                                (
                                    name,
                                    p.get('current_version') or p.get('cur_version'),
                                    p.get('new_version') or p.get('version'),
                                    p.get('arch', ''),
                                    mgr,
                                    _compute_tier(name, mgr),
                                    now,
                                    now,
                                ),
                            )
                conn.close()
                logger.info("UpdatesDB: saved %d pending updates", len(updates))
                return True
        except Exception as exc:
            logger.error("UpdatesDB.save_pending: %s", exc)
            return False

    def load_pending(self) -> Tuple[List[Dict], int]:
        """Return (list_of_dicts, count) of all pending updates, ordered
        por tier de instalación (crítico → gestor de paquetes → normal) y
        luego por nombre. Este es el mismo orden que se usa para instalar,
        así la UI siempre muestra la lista en el orden real de ejecución."""
        try:
            with self._lock:
                conn = self._connect()
                rows = conn.execute(
                    "SELECT name, cur_version, new_version, arch, manager, "
                    "tier, status, error, first_seen "
                    "FROM pending_updates ORDER BY tier ASC, manager, name"
                ).fetchall()
                conn.close()
                result = [
                    {
                        'name':            r['name'],
                        'current_version': r['cur_version'],
                        'new_version':     r['new_version'],
                        'arch':            r['arch'] or '',
                        'type':            r['manager'],
                        'manager':         r['manager'],
                        'tier':            r['tier'],
                        'status':          r['status'],
                        'error':           r['error'] or '',
                    }
                    for r in rows
                ]
                return result, len(result)
        except Exception as exc:
            logger.error("UpdatesDB.load_pending: %s", exc)
            return [], 0

    def set_status(self, name: str, manager: str, status: str,
                    error: str = "") -> bool:
        """Actualiza el estado (pending/installing/done/failed) de un
        paquete pendiente concreto. Se llama en vivo durante la instalación
        para que, si la app se cierra o se cae a mitad de una tanda, al
        reabrirla se sepa exactamente qué paquetes ya se instalaron, cuál
        estaba a medias y cuáles siguen pendientes."""
        try:
            with self._lock:
                conn = self._connect()
                with conn:
                    conn.execute(
                        "UPDATE pending_updates SET status=?, error=? "
                        "WHERE name=? AND manager=?",
                        (status, error, name, manager),
                    )
                conn.close()
                return True
        except Exception as exc:
            logger.error("UpdatesDB.set_status: %s", exc)
            return False

    def set_status_bulk(self, names: List[str], manager: str, status: str,
                         error: str = "") -> bool:
        """Igual que set_status pero para varios paquetes a la vez (evita
        una conexión/transacción por paquete cuando se marca una tanda
        entera como 'installing' o 'failed')."""
        if not names:
            return True
        try:
            with self._lock:
                conn = self._connect()
                placeholders = ','.join('?' * len(names))
                with conn:
                    conn.execute(
                        f"UPDATE pending_updates SET status=?, error=? "
                        f"WHERE manager=? AND name IN ({placeholders})",
                        [status, error, manager] + names,
                    )
                conn.close()
                return True
        except Exception as exc:
            logger.error("UpdatesDB.set_status_bulk: %s", exc)
            return False

    def load_incomplete(self) -> List[Dict]:
        """Devuelve los paquetes que quedaron a medias (status
        'installing') en una ejecución anterior — señal de que el proceso
        se interrumpió o la app se cerró a mitad de una instalación."""
        try:
            with self._lock:
                conn = self._connect()
                rows = conn.execute(
                    "SELECT name, manager, tier FROM pending_updates "
                    "WHERE status='installing' ORDER BY tier ASC, name"
                ).fetchall()
                conn.close()
                return [
                    {'name': r['name'], 'manager': r['manager'], 'tier': r['tier']}
                    for r in rows
                ]
        except Exception as exc:
            logger.error("UpdatesDB.load_incomplete: %s", exc)
            return []

    def remove_applied(self, names: List[str], manager: str) -> bool:
        """
        Remove packages from the pending list after they have been applied.
        Call this immediately after a successful upgrade for *manager*.
        """
        if not names:
            return True
        try:
            with self._lock:
                conn = self._connect()
                placeholders = ','.join('?' * len(names))
                with conn:
                    conn.execute(
                        f"DELETE FROM pending_updates "
                        f"WHERE manager=? AND name IN ({placeholders})",
                        [manager] + names,
                    )
                conn.close()
                logger.info("UpdatesDB: removed %d applied %s packages",
                            len(names), manager)
                return True
        except Exception as exc:
            logger.error("UpdatesDB.remove_applied: %s", exc)
            return False

    def clear_manager(self, manager: str) -> bool:
        """Remove ALL pending updates for a specific manager (e.g. after full upgrade)."""
        try:
            with self._lock:
                conn = self._connect()
                with conn:
                    conn.execute(
                        "DELETE FROM pending_updates WHERE manager=?", (manager,)
                    )
                conn.close()
                logger.info("UpdatesDB: cleared all %s pending updates", manager)
                return True
        except Exception as exc:
            logger.error("UpdatesDB.clear_manager: %s", exc)
            return False

    def clear_all(self) -> bool:
        """Remove all pending updates from the database."""
        try:
            with self._lock:
                conn = self._connect()
                with conn:
                    conn.execute("DELETE FROM pending_updates")
                conn.close()
                logger.info("UpdatesDB: cleared all pending updates")
                return True
        except Exception as exc:
            logger.error("UpdatesDB.clear_all: %s", exc)
            return False

    def count(self) -> int:
        """Return the total number of pending updates."""
        try:
            with self._lock:
                conn = self._connect()
                n = conn.execute(
                    "SELECT COUNT(*) FROM pending_updates"
                ).fetchone()[0]
                conn.close()
                return n
        except Exception as exc:
            logger.error("UpdatesDB.count: %s", exc)
            return 0


# Module-level singleton
updates_db = UpdatesDB()
