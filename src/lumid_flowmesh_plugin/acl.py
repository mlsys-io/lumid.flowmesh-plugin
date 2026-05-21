"""SQLite-backed grant store for PermissionChecker / ResourceRegistrar.

One table, ``acl_grants``, keyed by ``(kind, id, principal_id)``. Each row is
one grant: principal P is permitted to act on (kind, id). Rows are written by
``LumidResourceRegistrar.register`` at resource creation time and read by
``LumidPermissionChecker`` on every authz decision. Stale rows are cleared
by the host's startup reconcile sweep, which touches every live resource
and then drops anything left untouched.
"""

from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Index, delete, exists, select, tuple_, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class _Base(DeclarativeBase):
    pass


class _Grant(_Base):
    __tablename__ = "acl_grants"

    kind: Mapped[str] = mapped_column(primary_key=True)
    id: Mapped[str] = mapped_column(primary_key=True)
    principal_id: Mapped[str] = mapped_column(primary_key=True)
    granted_at: Mapped[datetime] = mapped_column()

    __table_args__ = (
        Index("ix_acl_grants_principal_kind", "principal_id", "kind"),
    )


def make_engine(db_path: str | Path) -> AsyncEngine:
    """Create an async SQLAlchemy engine for a SQLite file at ``db_path``.

    The parent directory must exist.
    """
    return create_async_engine(f"sqlite+aiosqlite:///{db_path}", future=True)


async def bootstrap_schema(engine: AsyncEngine) -> None:
    """Idempotently create the ``acl_grants`` table and its indexes."""
    async with engine.begin() as conn:
        await conn.run_sync(_Base.metadata.create_all)


class GrantStore:
    """Async CRUD wrapper around the ``acl_grants`` table."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sm = sessionmaker

    async def grant(self, kind: str, resource_id: str, principal_id: str) -> None:
        """Upsert a grant for ``(kind, resource_id, principal_id)`` with ``now``.

        Re-granting refreshes ``granted_at``.
        """
        now = datetime.now(UTC)
        stmt = sqlite_insert(_Grant).values(
            kind=kind,
            id=resource_id,
            principal_id=principal_id,
            granted_at=now,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[_Grant.kind, _Grant.id, _Grant.principal_id],
            set_={"granted_at": stmt.excluded.granted_at},
        )
        async with self._sm() as session:
            await session.execute(stmt)
            await session.commit()

    async def revoke(self, kind: str, resource_id: str, principal_id: str) -> bool:
        """Remove a single grant. Returns True if a row was removed."""
        stmt = delete(_Grant).where(
            _Grant.kind == kind,
            _Grant.id == resource_id,
            _Grant.principal_id == principal_id,
        )
        async with self._sm() as session:
            result = await session.execute(stmt)
            await session.commit()
            rowcount: int = result.rowcount  # type: ignore[attr-defined]
            return rowcount > 0

    async def delete_resource(self, kind: str, resource_id: str) -> int:
        """Remove every grant for ``(kind, resource_id)``. Returns rows removed."""
        stmt = delete(_Grant).where(
            _Grant.kind == kind, _Grant.id == resource_id
        )
        async with self._sm() as session:
            result = await session.execute(stmt)
            await session.commit()
            rowcount: int = result.rowcount  # type: ignore[attr-defined]
            return rowcount

    async def has_grant(
        self, kind: str, resource_id: str, principal_id: str
    ) -> bool:
        """Return True if ``principal_id`` has a grant on ``(kind, resource_id)``."""
        stmt = select(
            exists().where(
                _Grant.kind == kind,
                _Grant.id == resource_id,
                _Grant.principal_id == principal_id,
            )
        )
        async with self._sm() as session:
            return bool((await session.execute(stmt)).scalar())

    async def list_ids_for_principal(
        self, principal_id: str, kind: str
    ) -> frozenset[str]:
        """Return all ids of ``kind`` that ``principal_id`` has a grant on."""
        stmt = select(_Grant.id).where(
            _Grant.principal_id == principal_id, _Grant.kind == kind
        )
        async with self._sm() as session:
            rows = (await session.execute(stmt)).scalars().all()
        return frozenset(rows)

    async def reconcile(
        self,
        pairs: Iterable[tuple[str, str]],
        session_start: datetime,
    ) -> tuple[int, int]:
        """Replace the store's live set with the listed ``(kind, id)`` pairs.

        Single transaction: bump ``granted_at`` to ``now`` for every grant
        matching a pair, then delete every grant whose ``granted_at`` is
        still older than ``session_start``. On failure the transaction
        rolls back and the store is unchanged. Returns ``(touched, deleted)``.

        ``session_start`` is the cutoff used to recognise stale rows.
        Callers typically capture it at plugin-load time so grants written
        between load and the host's reconcile call (e.g. by other startup
        registrations) survive the sweep.
        """
        materialized = list(pairs)
        now = datetime.now(UTC)
        async with self._sm() as session, session.begin():
            touched = 0
            if materialized:
                touch_stmt = (
                    update(_Grant)
                    .where(tuple_(_Grant.kind, _Grant.id).in_(materialized))
                    .values(granted_at=now)
                )
                touch_result = await session.execute(touch_stmt)
                touched = touch_result.rowcount  # type: ignore[attr-defined]
            delete_stmt = delete(_Grant).where(_Grant.granted_at < session_start)
            delete_result = await session.execute(delete_stmt)
            deleted: int = delete_result.rowcount  # type: ignore[attr-defined]
        return touched, deleted


@asynccontextmanager
async def open_store(db_path: str | Path) -> AsyncIterator[tuple[AsyncEngine, GrantStore]]:
    """Open an engine, bootstrap the schema, yield ``(engine, store)``; dispose on exit."""
    engine = make_engine(db_path)
    try:
        await bootstrap_schema(engine)
        sm = async_sessionmaker(engine, expire_on_commit=False)
        yield engine, GrantStore(sm)
    finally:
        await engine.dispose()


__all__ = [
    "GrantStore",
    "bootstrap_schema",
    "make_engine",
    "open_store",
]
