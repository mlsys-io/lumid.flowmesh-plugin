"""Tests for the SQLite-backed OwnershipStore."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from lumid_flowmesh_plugin.acl import (
    OwnershipStore,
    _Ownership,
    bootstrap_schema,
    make_engine,
    open_store,
)


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[OwnershipStore]:
    async with open_store(tmp_path / "acl.sqlite") as (_engine, s):
        yield s


async def test_set_get_roundtrip(store: OwnershipStore) -> None:
    await store.set("workflow", "wf-1", "alice")
    assert await store.get("workflow", "wf-1") == "alice"
    assert await store.get("workflow", "missing") is None


async def test_set_is_upsert_and_updates_owner(store: OwnershipStore) -> None:
    await store.set("task", "t-1", "alice")
    await store.set("task", "t-1", "bob")
    assert await store.get("task", "t-1") == "bob"


async def test_delete_removes_row(store: OwnershipStore) -> None:
    await store.set("worker", "w-1", "alice")
    assert await store.delete("worker", "w-1") is True
    assert await store.get("worker", "w-1") is None
    assert await store.delete("worker", "w-1") is False


async def test_list_ids_for_principal_filters_by_kind_and_owner(
    store: OwnershipStore,
) -> None:
    await store.set("workflow", "wf-1", "alice")
    await store.set("workflow", "wf-2", "alice")
    await store.set("workflow", "wf-3", "bob")
    await store.set("task", "t-1", "alice")

    assert await store.list_ids_for_principal("alice", "workflow") == frozenset(
        {"wf-1", "wf-2"}
    )
    assert await store.list_ids_for_principal("alice", "task") == frozenset({"t-1"})
    assert await store.list_ids_for_principal("alice", "worker") == frozenset()


async def test_prune_older_than_honors_cutoff(
    tmp_path: Path,
) -> None:
    async with open_store(tmp_path / "acl.sqlite") as (engine, store):
        await store.set("workflow", "fresh", "alice")
        await store.set("workflow", "stale", "alice")
        # Backdate the stale row.
        await _backdate(engine, "workflow", "stale", days=120)

        pruned = await store.prune_older_than(ttl_days=90)
        assert pruned == 1
        assert await store.get("workflow", "stale") is None
        assert await store.get("workflow", "fresh") == "alice"


async def test_prune_disabled_when_ttl_zero(store: OwnershipStore) -> None:
    await store.set("workflow", "wf-1", "alice")
    assert await store.prune_older_than(ttl_days=0) == 0
    assert await store.get("workflow", "wf-1") == "alice"


async def test_bootstrap_is_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "acl.sqlite"
    engine = make_engine(db)
    try:
        await bootstrap_schema(engine)
        await bootstrap_schema(engine)  # second call must not raise
    finally:
        await engine.dispose()


async def _backdate(engine: AsyncEngine, kind: str, resource_id: str, *, days: int) -> None:
    sm = async_sessionmaker(engine, expire_on_commit=False)
    backdated = datetime.now(UTC) - timedelta(days=days)
    async with sm() as session:
        await session.execute(
            update(_Ownership)
            .where(_Ownership.kind == kind, _Ownership.id == resource_id)
            .values(registered_at=backdated)
        )
        await session.commit()
