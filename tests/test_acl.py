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


async def test_reconcile_marks_live_and_drops_stale(tmp_path: Path) -> None:
    async with open_store(tmp_path / "acl.sqlite") as (engine, store):
        await store.grant("worker", "live", "alice")
        await store.grant("worker", "live", "bob")
        await store.grant("worker", "stale", "alice")
        await _backdate_all(engine, "worker", "live", days=120)
        await _backdate_all(engine, "worker", "stale", days=120)

        session_start = datetime.now(UTC)
        touched, deleted = await store.reconcile(
            [("worker", "live")], session_start
        )
        assert touched == 2  # both alice and bob refreshed
        assert deleted == 1  # stale dropped

        assert await store.has_grant("worker", "live", "alice") is True
        assert await store.has_grant("worker", "live", "bob") is True
        assert await store.has_grant("worker", "stale", "alice") is False


async def test_reconcile_empty_batch_wipes_pre_session_rows(
    tmp_path: Path,
) -> None:
    async with open_store(tmp_path / "acl.sqlite") as (engine, store):
        await store.grant("workflow", "wf-1", "alice")
        await _backdate_all(engine, "workflow", "wf-1", days=1)

        session_start = datetime.now(UTC)
        touched, deleted = await store.reconcile([], session_start)

        assert touched == 0
        assert deleted == 1
        assert await store.has_grant("workflow", "wf-1", "alice") is False


async def test_reconcile_preserves_grants_written_after_session_start(
    store: GrantStore,
) -> None:
    """A grant written after `session_start` survives even with an empty
    batch — its `granted_at` is past the cutoff. This protects grants from
    `register` calls that land between plugin install and the host's
    reconcile sweep.
    """
    session_start = datetime.now(UTC) - timedelta(seconds=1)
    await store.grant("worker", "fresh", "alice")
    touched, deleted = await store.reconcile([], session_start)
    assert (touched, deleted) == (0, 0)
    assert await store.has_grant("worker", "fresh", "alice") is True


async def test_reconcile_on_empty_store_is_noop(store: GrantStore) -> None:
    session_start = datetime.now(UTC)
    touched, deleted = await store.reconcile(
        [("worker", "w-1")], session_start
    )
    assert (touched, deleted) == (0, 0)


async def test_reconcile_is_idempotent(tmp_path: Path) -> None:
    async with open_store(tmp_path / "acl.sqlite") as (engine, store):
        await store.grant("worker", "w-1", "alice")
        await _backdate_all(engine, "worker", "w-1", days=120)

        session_start = datetime.now(UTC)
        first = await store.reconcile([("worker", "w-1")], session_start)
        second = await store.reconcile([("worker", "w-1")], session_start)

        assert first == (1, 0)
        assert second == (1, 0)
        assert await store.has_grant("worker", "w-1", "alice") is True


async def test_reconcile_rolls_back_on_error(tmp_path: Path) -> None:
    """If the transaction raises mid-flight, neither the update nor the
    delete commits."""
    async with open_store(tmp_path / "acl.sqlite") as (engine, store):
        await store.grant("worker", "w-1", "alice")
        await _backdate_all(engine, "worker", "w-1", days=120)

        # Wrap the session so the second statement (delete) raises after
        # the first (update) ran but before commit.
        real_sm = store._sm  # type: ignore[attr-defined]

        class _ExplodingSession:
            def __init__(self, inner: object) -> None:
                self._inner = inner
                self._executions = 0

            async def __aenter__(self) -> "_ExplodingSession":
                await self._inner.__aenter__()  # type: ignore[attr-defined]
                return self

            async def __aexit__(self, *args: object) -> None:
                await self._inner.__aexit__(*args)  # type: ignore[attr-defined]

            def begin(self) -> object:
                return self._inner.begin()  # type: ignore[attr-defined]

            async def execute(self, stmt: object) -> object:
                self._executions += 1
                if self._executions == 2:
                    raise RuntimeError("simulated mid-transaction failure")
                return await self._inner.execute(stmt)  # type: ignore[attr-defined]

            async def commit(self) -> None:
                await self._inner.commit()  # type: ignore[attr-defined]

        def _exploding_sm() -> _ExplodingSession:
            return _ExplodingSession(real_sm())

        store._sm = _exploding_sm  # type: ignore[assignment]
        try:
            session_start = datetime.now(UTC)
            with pytest.raises(RuntimeError, match="simulated"):
                await store.reconcile([("worker", "w-1")], session_start)
        finally:
            store._sm = real_sm  # type: ignore[assignment]

        # Update was attempted but rollback restored the original state.
        assert await store.has_grant("worker", "w-1", "alice") is True


async def test_bootstrap_is_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "acl.sqlite"
    engine = make_engine(db)
    try:
        await bootstrap_schema(engine)
        await bootstrap_schema(engine)  # second call must not raise
    finally:
        await engine.dispose()


async def _backdate_all(
    engine: AsyncEngine,
    kind: str,
    resource_id: str,
    *,
    days: int,
) -> None:
    sm = async_sessionmaker(engine, expire_on_commit=False)
    backdated = datetime.now(UTC) - timedelta(days=days)
    async with sm() as session:
        await session.execute(
            update(_Grant)
            .where(_Grant.kind == kind, _Grant.id == resource_id)
            .values(granted_at=backdated)
        )
        await session.commit()
