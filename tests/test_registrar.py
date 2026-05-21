"""Tests for LumidResourceRegistrar."""

import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from lumid_hooks import PrincipalContext, ResourceRef
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from lumid_flowmesh_plugin.acl import GrantStore, _Grant, open_store
from lumid_flowmesh_plugin.registrar import LumidResourceRegistrar


@pytest.fixture
async def store_engine(
    tmp_path: Path,
) -> AsyncIterator[tuple[GrantStore, AsyncEngine]]:
    async with open_store(tmp_path / "acl.sqlite") as (engine, s):
        yield s, engine


@pytest.fixture
async def store(
    store_engine: tuple[GrantStore, AsyncEngine],
) -> GrantStore:
    return store_engine[0]


def _principal(pid: str) -> PrincipalContext:
    return PrincipalContext(
        principal_id=pid,
        org_id="lumid",
        external_id=pid,
        principal_type="user",
        scopes=[],
    )


def _registrar(store: GrantStore) -> LumidResourceRegistrar:
    return LumidResourceRegistrar(store, datetime.now(UTC))


async def test_register_writes_grant(
    store: GrantStore, logger: logging.Logger
) -> None:
    reg = _registrar(store)
    await reg.register(
        _principal("alice"), ResourceRef(kind="workflow", id="wf-1"), logger
    )
    assert await store.has_grant("workflow", "wf-1", "alice") is True


async def test_deregister_removes_all_grants(
    store: GrantStore, logger: logging.Logger
) -> None:
    reg = _registrar(store)
    await reg.register(
        _principal("alice"), ResourceRef(kind="task", id="t-1"), logger
    )
    await store.grant("task", "t-1", "bob")  # second grantee added out-of-band
    await reg.deregister(
        _principal("alice"), ResourceRef(kind="task", id="t-1"), logger
    )
    assert await store.has_grant("task", "t-1", "alice") is False
    assert await store.has_grant("task", "t-1", "bob") is False


async def test_kind_level_register_is_noop(
    store: GrantStore, logger: logging.Logger, caplog: pytest.LogCaptureFixture
) -> None:
    reg = _registrar(store)
    with caplog.at_level(logging.WARNING):
        await reg.register(
            _principal("alice"), ResourceRef(kind="workflow"), logger
        )
    assert "kind-level register" in caplog.text
    assert await store.list_ids_for_principal("alice", "workflow") == frozenset()


async def test_kind_level_deregister_is_noop(
    store: GrantStore, logger: logging.Logger
) -> None:
    reg = _registrar(store)
    await reg.deregister(
        _principal("alice"), ResourceRef(kind="workflow"), logger
    )


async def test_re_register_keeps_principal_grant(
    store: GrantStore, logger: logging.Logger
) -> None:
    reg = _registrar(store)
    await reg.register(
        _principal("alice"), ResourceRef(kind="worker", id="w-1"), logger
    )
    await reg.register(
        _principal("alice"), ResourceRef(kind="worker", id="w-1"), logger
    )
    assert await store.has_grant("worker", "w-1", "alice") is True
    assert await store.list_ids_for_principal("alice", "worker") == frozenset({"w-1"})


async def test_refresh_keeps_long_running_grants_alive(
    store_engine: tuple[GrantStore, AsyncEngine], logger: logging.Logger
) -> None:
    store, engine = store_engine
    session_start = datetime.now(UTC)
    reg = LumidResourceRegistrar(store, session_start)

    # Two principals share a worker registered long ago.
    await store.grant("worker", "w-1", "alice")
    await store.grant("worker", "w-1", "bob")
    await _backdate_all(engine, "worker", "w-1", days=120)

    await reg.refresh([ResourceRef(kind="worker", id="w-1")], logger)
    await reg.purge_stale(logger)

    assert await store.has_grant("worker", "w-1", "alice") is True
    assert await store.has_grant("worker", "w-1", "bob") is True


async def test_purge_stale_drops_unrefreshed_grants(
    store_engine: tuple[GrantStore, AsyncEngine], logger: logging.Logger
) -> None:
    store, engine = store_engine
    session_start = datetime.now(UTC)
    reg = LumidResourceRegistrar(store, session_start)

    await store.grant("workflow", "live", "alice")
    await store.grant("workflow", "forgotten", "alice")
    await _backdate_all(engine, "workflow", "live", days=120)
    await _backdate_all(engine, "workflow", "forgotten", days=120)

    await reg.refresh([ResourceRef(kind="workflow", id="live")], logger)
    await reg.purge_stale(logger)

    assert await store.has_grant("workflow", "live", "alice") is True
    assert await store.has_grant("workflow", "forgotten", "alice") is False


async def test_refresh_ignores_kind_level_refs(
    store: GrantStore, logger: logging.Logger
) -> None:
    reg = _registrar(store)
    await reg.refresh([ResourceRef(kind="workflow")], logger)


async def test_purge_stale_preserves_grants_written_after_session_start(
    store: GrantStore, logger: logging.Logger
) -> None:
    session_start = datetime.now(UTC) - timedelta(seconds=1)
    reg = LumidResourceRegistrar(store, session_start)
    await store.grant("worker", "new", "alice")
    await reg.purge_stale(logger)
    assert await store.has_grant("worker", "new", "alice") is True


async def test_sweep_on_empty_store_is_noop(
    store: GrantStore, logger: logging.Logger
) -> None:
    reg = _registrar(store)
    await reg.refresh([], logger)
    await reg.purge_stale(logger)


async def test_double_sweep_is_idempotent(
    store_engine: tuple[GrantStore, AsyncEngine], logger: logging.Logger
) -> None:
    """A second reconcile in the same boot must not drop grants the first
    sweep just touched."""
    store, engine = store_engine
    session_start = datetime.now(UTC)
    reg = LumidResourceRegistrar(store, session_start)

    await store.grant("worker", "w-1", "alice")
    await _backdate_all(engine, "worker", "w-1", days=120)

    refs = [ResourceRef(kind="worker", id="w-1")]
    await reg.refresh(refs, logger)
    await reg.purge_stale(logger)
    await reg.refresh(refs, logger)
    await reg.purge_stale(logger)

    assert await store.has_grant("worker", "w-1", "alice") is True


async def _backdate_all(
    engine: AsyncEngine, kind: str, resource_id: str, *, days: int
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
