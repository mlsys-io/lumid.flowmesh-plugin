"""SQLite-backed ownership store for PermissionChecker / ResourceRegistrar.

One table, ``acl_ownership``, keyed by ``(kind, id)``. Rows are written by
``LumidResourceRegistrar.register`` at resource creation time and read by
``LumidPermissionChecker`` on every authz decision. Stale rows are trimmed
by ``prune_older_than`` at startup; ``deregister`` is the steady-state
cleanup.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, select
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


class _Ownership(_Base):
    __tablename__ = "acl_ownership"

    kind: Mapped[str] = mapped_column(primary_key=True)
    id: Mapped[str] = mapped_column(primary_key=True)
    principal_id: Mapped[str] = mapped_column(index=True)
    registered_at: Mapped[datetime] = mapped_column()


def make_engine(db_path: str | Path) -> AsyncEngine:
    """Create an async SQLAlchemy engine for a SQLite file at ``db_path``.

    The parent directory must exist; FlowMesh's stack-side ``ensure_dir`` on
    ``FLOWMESH_PLUGIN_DATA_DIR`` covers the default path.
    """
    return create_async_engine(f"sqlite+aiosqlite:///{db_path}", future=True)


async def bootstrap_schema(engine: AsyncEngine) -> None:
    """Idempotently create the ``acl_ownership`` table + its principal index."""
    async with engine.begin() as conn:
        await conn.run_sync(_Base.metadata.create_all)


class OwnershipStore:
    """Async CRUD wrapper around the ``acl_ownership`` table."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sm = sessionmaker

    async def set(self, kind: str, resource_id: str, principal_id: str) -> None:
        """Upsert ``(kind, resource_id) -> principal_id`` with ``now`` timestamp.

        Re-registering an existing resource updates the owner and the
        registered_at timestamp. SQLite's ``INSERT OR REPLACE`` semantics are
        expressed via ``ON CONFLICT DO UPDATE``.
        """
        now = datetime.now(UTC)
        stmt = sqlite_insert(_Ownership).values(
            kind=kind,
            id=resource_id,
            principal_id=principal_id,
            registered_at=now,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[_Ownership.kind, _Ownership.id],
            set_={
                "principal_id": stmt.excluded.principal_id,
                "registered_at": stmt.excluded.registered_at,
            },
        )
        async with self._sm() as session:
            await session.execute(stmt)
            await session.commit()

    async def get(self, kind: str, resource_id: str) -> str | None:
        """Return the owning principal_id for ``(kind, resource_id)``, or None."""
        stmt = select(_Ownership.principal_id).where(
            _Ownership.kind == kind, _Ownership.id == resource_id
        )
        async with self._sm() as session:
            return (await session.execute(stmt)).scalar_one_or_none()

    async def delete(self, kind: str, resource_id: str) -> bool:
        """Remove the ``(kind, resource_id)`` row. Returns True if a row was removed."""
        stmt = delete(_Ownership).where(
            _Ownership.kind == kind, _Ownership.id == resource_id
        )
        async with self._sm() as session:
            result = await session.execute(stmt)
            await session.commit()
            rowcount: int = result.rowcount  # type: ignore[attr-defined]
            return rowcount > 0

    async def list_ids_for_principal(
        self, principal_id: str, kind: str
    ) -> frozenset[str]:
        """Return all ids of ``kind`` owned by ``principal_id``."""
        stmt = select(_Ownership.id).where(
            _Ownership.principal_id == principal_id, _Ownership.kind == kind
        )
        async with self._sm() as session:
            rows = (await session.execute(stmt)).scalars().all()
        return frozenset(rows)

    async def prune_older_than(self, ttl_days: int) -> int:
        """Delete rows whose ``registered_at`` is older than ``ttl_days``.

        Returns the number of rows pruned. ``ttl_days <= 0`` is a no-op.
        """
        if ttl_days <= 0:
            return 0
        cutoff = datetime.now(UTC) - timedelta(days=ttl_days)
        stmt = delete(_Ownership).where(_Ownership.registered_at < cutoff)
        async with self._sm() as session:
            result = await session.execute(stmt)
            await session.commit()
            rowcount: int = result.rowcount  # type: ignore[attr-defined]
            return rowcount


@asynccontextmanager
async def open_store(db_path: str | Path) -> AsyncIterator[tuple[AsyncEngine, OwnershipStore]]:
    """Open an engine, bootstrap the schema, yield ``(engine, store)``; dispose on exit."""
    engine = make_engine(db_path)
    try:
        await bootstrap_schema(engine)
        sm = async_sessionmaker(engine, expire_on_commit=False)
        yield engine, OwnershipStore(sm)
    finally:
        await engine.dispose()


__all__ = [
    "OwnershipStore",
    "bootstrap_schema",
    "make_engine",
    "open_store",
]
