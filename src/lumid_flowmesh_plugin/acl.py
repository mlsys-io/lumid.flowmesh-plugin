"""SQLite-backed grant store for PermissionChecker / ResourceRegistrar.

One table, ``acl_grants``, keyed by ``(kind, id, principal_id)``. Each row is
one grant: principal P holds ``level`` access on (kind, id). ``level`` is an
ordinal — ``READ`` (1) permits read, ``WRITE`` (2) permits read and mutation.
Rows are written by ``LumidResourceRegistrar.register`` at resource creation
time (owner gets ``WRITE``) and read by ``LumidPermissionChecker`` on every
authz decision. Stale rows are cleared by the host's startup reconcile sweep,
which touches every live resource and then drops anything left untouched.

Built on the stdlib ``sqlite3`` module. A single ``Connection`` opened in
autocommit is shared across all ops; an ``asyncio.Lock`` serialises access
and each query runs in ``asyncio.to_thread`` so the event loop never blocks.
"""

import asyncio
import sqlite3
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from enum import IntEnum
from pathlib import Path
from typing import Any


class GrantLevel(IntEnum):
    READ = 1
    WRITE = 2


_SCHEMA = """
CREATE TABLE IF NOT EXISTS acl_grants (
    kind TEXT NOT NULL,
    id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    level INTEGER NOT NULL,
    granted_at TEXT NOT NULL,
    PRIMARY KEY (kind, id, principal_id)
);
CREATE INDEX IF NOT EXISTS ix_acl_grants_principal_kind
    ON acl_grants (principal_id, kind);
"""


def _connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(
        str(db_path),
        check_same_thread=False,
        isolation_level=None,
    )
    conn.executescript(_SCHEMA)
    return conn


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


# Max ``(kind, id)`` pairs per reconcile UPDATE. Each pair binds 2 variables and
# adds one VALUES row, so 400 stays under SQLite's default variable (999) and
# compound-select (500) limits even on the oldest supported builds.
_RECONCILE_CHUNK = 400


class GrantStore:
    """Async CRUD wrapper around the ``acl_grants`` table.

    All access goes through ``asyncio.to_thread`` and is serialised by an
    ``asyncio.Lock`` since a single ``sqlite3.Connection`` is not thread-safe.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._lock = asyncio.Lock()

    async def grant(
        self,
        kind: str,
        resource_id: str,
        principal_id: str,
        level: GrantLevel = GrantLevel.WRITE,
    ) -> None:
        """Upsert a grant for ``(kind, resource_id, principal_id)`` with ``now``.

        Re-granting refreshes ``granted_at`` and overwrites ``level``.
        """
        params = (kind, resource_id, principal_id, int(level), _now_iso())
        async with self._lock:
            await asyncio.to_thread(
                self._conn.execute,
                "INSERT INTO acl_grants(kind, id, principal_id, level, granted_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(kind, id, principal_id) "
                "DO UPDATE SET granted_at = excluded.granted_at, level = excluded.level",
                params,
            )

    async def revoke(self, kind: str, resource_id: str, principal_id: str) -> bool:
        """Remove a single grant. Returns True if a row was removed."""
        async with self._lock:
            cur = await asyncio.to_thread(
                self._conn.execute,
                "DELETE FROM acl_grants WHERE kind=? AND id=? AND principal_id=?",
                (kind, resource_id, principal_id),
            )
            return cur.rowcount > 0

    async def delete_resource(self, kind: str, resource_id: str) -> int:
        """Remove every grant for ``(kind, resource_id)``. Returns rows removed."""
        async with self._lock:
            cur = await asyncio.to_thread(
                self._conn.execute,
                "DELETE FROM acl_grants WHERE kind=? AND id=?",
                (kind, resource_id),
            )
            return cur.rowcount

    async def get_level(
        self, kind: str, resource_id: str, principal_id: str
    ) -> GrantLevel | None:
        """Return ``principal_id``'s grant level on ``(kind, resource_id)``, or None."""
        async with self._lock:
            row = await asyncio.to_thread(
                self._fetchone,
                "SELECT level FROM acl_grants "
                "WHERE kind=? AND id=? AND principal_id=? LIMIT 1",
                (kind, resource_id, principal_id),
            )
        return GrantLevel(row[0]) if row is not None else None

    async def has_grant(
        self, kind: str, resource_id: str, principal_id: str
    ) -> bool:
        """Return True if ``principal_id`` has any grant on ``(kind, resource_id)``."""
        return await self.get_level(kind, resource_id, principal_id) is not None

    async def list_ids_for_principal(
        self, principal_id: str, kind: str, min_level: GrantLevel = GrantLevel.READ
    ) -> frozenset[str]:
        """Return ids of ``kind`` that ``principal_id`` holds ``>= min_level`` on."""
        async with self._lock:
            rows = await asyncio.to_thread(
                self._fetchall,
                "SELECT id FROM acl_grants "
                "WHERE principal_id=? AND kind=? AND level>=?",
                (principal_id, kind, int(min_level)),
            )
        return frozenset(r[0] for r in rows)

    async def reconcile(
        self,
        pairs: Iterable[tuple[str, str]],
        session_start: datetime,
    ) -> tuple[int, int]:
        """Replace the store's live set with the listed ``(kind, id)`` pairs.

        Single atomic transaction: bumps ``granted_at`` to ``now`` for every
        grant matching a pair, then deletes every grant older than
        ``session_start``. Returns ``(touched, deleted)``.

        ``session_start`` is the cutoff used to recognise stale rows. Callers
        capture it at plugin-load time so grants written between load and the
        host's reconcile call survive the sweep.
        """
        materialized = list(pairs)
        async with self._lock:
            return await asyncio.to_thread(
                self._reconcile_sync, materialized, session_start
            )

    def _reconcile_sync(
        self,
        pairs: list[tuple[str, str]],
        session_start: datetime,
    ) -> tuple[int, int]:
        conn = self._conn
        cutoff = session_start.isoformat()
        now = _now_iso()
        conn.execute("BEGIN")
        try:
            touched = 0
            for start in range(0, len(pairs), _RECONCILE_CHUNK):
                chunk = pairs[start : start + _RECONCILE_CHUNK]
                values_clause = ",".join("(?, ?)" for _ in chunk)
                flat: list[str] = [now]
                for kind, rid in chunk:
                    flat.extend((kind, rid))
                cur = conn.execute(
                    f"UPDATE acl_grants SET granted_at = ? "
                    f"WHERE (kind, id) IN (VALUES {values_clause})",
                    flat,
                )
                touched += cur.rowcount
            cur = conn.execute(
                "DELETE FROM acl_grants WHERE granted_at < ?", (cutoff,)
            )
            deleted = cur.rowcount
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return touched, deleted

    def _fetchone(
        self, sql: str, params: tuple[object, ...]
    ) -> tuple[Any, ...] | None:
        row: tuple[Any, ...] | None = self._conn.execute(sql, params).fetchone()
        return row

    def _fetchall(
        self, sql: str, params: tuple[object, ...]
    ) -> list[tuple[Any, ...]]:
        rows: list[tuple[Any, ...]] = self._conn.execute(sql, params).fetchall()
        return rows


@asynccontextmanager
async def open_store(db_path: str | Path) -> AsyncIterator[GrantStore]:
    """Open a connection, bootstrap the schema, yield a ``GrantStore``; close on exit."""
    conn = await asyncio.to_thread(_connect, db_path)
    try:
        yield GrantStore(conn)
    finally:
        await asyncio.to_thread(conn.close)


__all__ = [
    "GrantLevel",
    "GrantStore",
    "open_store",
]
