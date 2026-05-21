"""Tests for the SQLite-backed GrantStore."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from lumid_flowmesh_plugin.acl import (
    GrantStore,
    _Grant,
    bootstrap_schema,
    make_engine,
    open_store,
)


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[GrantStore]:
    async with open_store(tmp_path / "acl.sqlite") as (_engine, s):
        yield s


async def test_grant_and_has_grant_roundtrip(store: GrantStore) -> None:
    await store.grant("workflow", "wf-1", "alice")
    assert await store.has_grant("workflow", "wf-1", "alice") is True
    assert await store.has_grant("workflow", "wf-1", "bob") is False
    assert await store.has_grant("workflow", "missing", "alice") is False


async def test_grant_is_idempotent_per_principal(store: GrantStore) -> None:
    await store.grant("task", "t-1", "alice")
    await store.grant("task", "t-1", "alice")
    assert await store.list_ids_for_principal("alice", "task") == frozenset({"t-1"})


async def test_multiple_principals_share_a_resource(store: GrantStore) -> None:
    await store.grant("workflow", "wf-1", "alice")
    await store.grant("workflow", "wf-1", "bob")
    assert await store.has_grant("workflow", "wf-1", "alice") is True
    assert await store.has_grant("workflow", "wf-1", "bob") is True


async def test_revoke_removes_single_grant(store: GrantStore) -> None:
    await store.grant("workflow", "wf-1", "alice")
    await store.grant("workflow", "wf-1", "bob")
    assert await store.revoke("workflow", "wf-1", "alice") is True
    assert await store.has_grant("workflow", "wf-1", "alice") is False
    assert await store.has_grant("workflow", "wf-1", "bob") is True
    assert await store.revoke("workflow", "wf-1", "alice") is False


async def test_delete_resource_removes_all_grants(store: GrantStore) -> None:
    await store.grant("worker", "w-1", "alice")
    await store.grant("worker", "w-1", "bob")
    await store.grant("worker", "w-2", "alice")
    assert await store.delete_resource("worker", "w-1") == 2
    assert await store.has_grant("worker", "w-1", "alice") is False
    assert await store.has_grant("worker", "w-1", "bob") is False
    assert await store.has_grant("worker", "w-2", "alice") is True
    assert await store.delete_resource("worker", "w-1") == 0


async def test_list_ids_for_principal_filters_by_kind_and_principal(
    store: GrantStore,
) -> None:
    await store.grant("workflow", "wf-1", "alice")
    await store.grant("workflow", "wf-2", "alice")
    await store.grant("workflow", "wf-3", "bob")
    await store.grant("task", "t-1", "alice")

    assert await store.list_ids_for_principal("alice", "workflow") == frozenset(
        {"wf-1", "wf-2"}
    )
    assert await store.list_ids_for_principal("alice", "task") == frozenset({"t-1"})
    assert await store.list_ids_for_principal("alice", "worker") == frozenset()


async def test_list_ids_includes_resources_shared_with_principal(
    store: GrantStore,
) -> None:
    await store.grant("workflow", "wf-1", "alice")
    await store.grant("workflow", "wf-1", "bob")
    await store.grant("workflow", "wf-2", "bob")
    assert await store.list_ids_for_principal("bob", "workflow") == frozenset(
        {"wf-1", "wf-2"}
    )


async def test_touch_resources_bumps_all_grantees(tmp_path: Path) -> None:
    async with open_store(tmp_path / "acl.sqlite") as (engine, store):
        await store.grant("workflow", "wf-1", "alice")
        await store.grant("workflow", "wf-1", "bob")
        await _backdate(engine, "workflow", "wf-1", None, days=120)

        touched = await store.touch_resources([("workflow", "wf-1")])
        assert touched == 2


async def test_touch_resources_only_matches_listed_refs(tmp_path: Path) -> None:
    async with open_store(tmp_path / "acl.sqlite") as (engine, store):
        await store.grant("workflow", "wf-1", "alice")
        await store.grant("workflow", "wf-2", "alice")
        await _backdate(engine, "workflow", "wf-1", "alice", days=120)
        await _backdate(engine, "workflow", "wf-2", "alice", days=120)

        touched = await store.touch_resources([("workflow", "wf-1")])
        assert touched == 1
        # wf-2 should still be stale
        sweep_start = datetime.now(UTC) - timedelta(days=1)
        deleted = await store.delete_unrefreshed(sweep_start)
        assert deleted == 1
        assert await store.has_grant("workflow", "wf-1", "alice") is True
        assert await store.has_grant("workflow", "wf-2", "alice") is False


async def test_touch_resources_empty_input(store: GrantStore) -> None:
    assert await store.touch_resources([]) == 0


async def test_delete_unrefreshed_clears_pre_session_rows(tmp_path: Path) -> None:
    async with open_store(tmp_path / "acl.sqlite") as (engine, store):
        await store.grant("workflow", "old", "alice")
        await _backdate(engine, "workflow", "old", "alice", days=120)
        session_start = datetime.now(UTC) - timedelta(seconds=1)
        await store.grant("workflow", "fresh", "alice")

        deleted = await store.delete_unrefreshed(session_start)
        assert deleted == 1
        assert await store.has_grant("workflow", "old", "alice") is False
        assert await store.has_grant("workflow", "fresh", "alice") is True


async def test_bootstrap_is_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "acl.sqlite"
    engine = make_engine(db)
    try:
        await bootstrap_schema(engine)
        await bootstrap_schema(engine)  # second call must not raise
    finally:
        await engine.dispose()


async def _backdate(
    engine: AsyncEngine,
    kind: str,
    resource_id: str,
    principal_id: str | None,
    *,
    days: int,
) -> None:
    """Backdate either all grants on a resource (principal_id=None) or one grant."""
    sm = async_sessionmaker(engine, expire_on_commit=False)
    backdated = datetime.now(UTC) - timedelta(days=days)
    async with sm() as session:
        stmt = update(_Grant).where(
            _Grant.kind == kind,
            _Grant.id == resource_id,
        )
        if principal_id is not None:
            stmt = stmt.where(_Grant.principal_id == principal_id)
        await session.execute(stmt.values(granted_at=backdated))
        await session.commit()
