"""Tests for LumidResourceRegistrar."""

import logging
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from lumid_hooks import PrincipalContext, ResourceRef

from lumid_flowmesh_plugin.acl import GrantStore, open_store
from lumid_flowmesh_plugin.registrar import LumidResourceRegistrar


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[GrantStore]:
    async with open_store(tmp_path / "acl.sqlite") as (_engine, s):
        yield s


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
    reg = LumidResourceRegistrar(store)
    await reg.register(
        _principal("alice"), ResourceRef(kind="workflow", id="wf-1"), logger
    )
    assert await store.has_grant("workflow", "wf-1", "alice") is True


async def test_deregister_removes_all_grants(
    store: GrantStore, logger: logging.Logger
) -> None:
    reg = LumidResourceRegistrar(store)
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
    reg = LumidResourceRegistrar(store)
    with caplog.at_level(logging.WARNING):
        await reg.register(
            _principal("alice"), ResourceRef(kind="workflow"), logger
        )
    assert "kind-level register" in caplog.text
    assert await store.list_ids_for_principal("alice", "workflow") == frozenset()


async def test_kind_level_deregister_is_noop(
    store: GrantStore, logger: logging.Logger
) -> None:
    reg = LumidResourceRegistrar(store)
    await reg.deregister(
        _principal("alice"), ResourceRef(kind="workflow"), logger
    )


async def test_re_register_keeps_principal_grant(
    store: GrantStore, logger: logging.Logger
) -> None:
    reg = LumidResourceRegistrar(store)
    await reg.register(
        _principal("alice"), ResourceRef(kind="worker", id="w-1"), logger
    )
    await reg.register(
        _principal("alice"), ResourceRef(kind="worker", id="w-1"), logger
    )
    assert await store.has_grant("worker", "w-1", "alice") is True
    assert await store.list_ids_for_principal("alice", "worker") == frozenset({"w-1"})
