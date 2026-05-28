"""Tests for LumidResourceRegistrar."""

import logging
import sqlite3
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from lumid_hooks import PrincipalContext, ResourceRef

from lumid_flowmesh_plugin.acl import GrantLevel, GrantStore, open_store
from lumid_flowmesh_plugin.registrar import LumidResourceRegistrar


@pytest.fixture
async def store_db(
    tmp_path: Path,
) -> AsyncIterator[tuple[GrantStore, Path]]:
    db = tmp_path / "acl.sqlite"
    async with open_store(db) as s:
        yield s, db


@pytest.fixture
async def store(store_db: tuple[GrantStore, Path]) -> GrantStore:
    return store_db[0]


def _principal(pid: str) -> PrincipalContext:
    return PrincipalContext(
        principal_id=pid,
        org_id="lumid",
        external_id=pid,
        principal_type="user",
        scopes=[],
    )


async def test_register_writes_grant(
    store: GrantStore, logger: logging.Logger
) -> None:
    reg = LumidResourceRegistrar(store, datetime.now(UTC))
    await reg.register(
        _principal("alice"), ResourceRef(kind="workflow", id="wf-1"), logger
    )
    assert await store.has_grant("workflow", "wf-1", "alice") is True


async def test_deregister_removes_all_grants(
    store: GrantStore, logger: logging.Logger
) -> None:
    reg = LumidResourceRegistrar(store, datetime.now(UTC))
    await reg.register(
        _principal("alice"), ResourceRef(kind="task", id="t-1"), logger
    )
    await store.grant("task", "t-1", "bob", GrantLevel.WRITE)
    await reg.deregister(
        _principal("alice"), ResourceRef(kind="task", id="t-1"), logger
    )
    assert await store.has_grant("task", "t-1", "alice") is False
    assert await store.has_grant("task", "t-1", "bob") is False


async def test_kind_level_register_is_noop(
    store: GrantStore, logger: logging.Logger, caplog: pytest.LogCaptureFixture
) -> None:
    reg = LumidResourceRegistrar(store, datetime.now(UTC))
    with caplog.at_level(logging.WARNING):
        await reg.register(
            _principal("alice"), ResourceRef(kind="workflow"), logger
        )
    assert "kind-level register" in caplog.text
    assert await store.list_ids_for_principal("alice", "workflow", GrantLevel.READ) == frozenset()


async def test_kind_level_deregister_is_noop(
    store: GrantStore, logger: logging.Logger
) -> None:
    reg = LumidResourceRegistrar(store, datetime.now(UTC))
    await reg.deregister(
        _principal("alice"), ResourceRef(kind="workflow"), logger
    )


async def test_re_register_keeps_principal_grant(
    store: GrantStore, logger: logging.Logger
) -> None:
    reg = LumidResourceRegistrar(store, datetime.now(UTC))
    await reg.register(
        _principal("alice"), ResourceRef(kind="worker", id="w-1"), logger
    )
    await reg.register(
        _principal("alice"), ResourceRef(kind="worker", id="w-1"), logger
    )
    assert await store.has_grant("worker", "w-1", "alice") is True
    assert await store.list_ids_for_principal(
        "alice", "worker", GrantLevel.READ
    ) == frozenset({"w-1"})


async def test_reconcile_keeps_long_running_grants_alive(
    store_db: tuple[GrantStore, Path], logger: logging.Logger
) -> None:
    store, db = store_db
    reg = LumidResourceRegistrar(store, datetime.now(UTC))

    await store.grant("worker", "w-1", "alice", GrantLevel.WRITE)
    await store.grant("worker", "w-1", "bob", GrantLevel.WRITE)
    _backdate_all(db, "worker", "w-1", days=120)

    await reg.reconcile([ResourceRef(kind="worker", id="w-1")], logger)

    assert await store.has_grant("worker", "w-1", "alice") is True
    assert await store.has_grant("worker", "w-1", "bob") is True


async def test_reconcile_drops_resources_not_in_batch(
    store_db: tuple[GrantStore, Path], logger: logging.Logger
) -> None:
    store, db = store_db
    reg = LumidResourceRegistrar(store, datetime.now(UTC))

    await store.grant("workflow", "live", "alice", GrantLevel.WRITE)
    await store.grant("workflow", "forgotten", "alice", GrantLevel.WRITE)
    _backdate_all(db, "workflow", "live", days=120)
    _backdate_all(db, "workflow", "forgotten", days=120)

    await reg.reconcile([ResourceRef(kind="workflow", id="live")], logger)

    assert await store.has_grant("workflow", "live", "alice") is True
    assert await store.has_grant("workflow", "forgotten", "alice") is False


async def test_reconcile_ignores_kind_level_refs(
    store: GrantStore, logger: logging.Logger
) -> None:
    reg = LumidResourceRegistrar(store, datetime.now(UTC))
    await reg.reconcile(
        [ResourceRef(kind="workflow"), ResourceRef(kind="task")], logger
    )


async def test_reconcile_empty_batch_on_empty_store_is_noop(
    store: GrantStore, logger: logging.Logger
) -> None:
    reg = LumidResourceRegistrar(store, datetime.now(UTC))
    await reg.reconcile([], logger)


async def test_double_reconcile_is_idempotent(
    store_db: tuple[GrantStore, Path], logger: logging.Logger
) -> None:
    store, db = store_db
    reg = LumidResourceRegistrar(store, datetime.now(UTC))

    await store.grant("worker", "w-1", "alice", GrantLevel.WRITE)
    _backdate_all(db, "worker", "w-1", days=120)

    refs = [ResourceRef(kind="worker", id="w-1")]
    await reg.reconcile(refs, logger)
    await reg.reconcile(refs, logger)

    assert await store.has_grant("worker", "w-1", "alice") is True


def _backdate_all(db_path: Path, kind: str, resource_id: str, *, days: int) -> None:
    backdated = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE acl_grants SET last_seen_at = ? WHERE kind = ? AND id = ?",
            (backdated, kind, resource_id),
        )
