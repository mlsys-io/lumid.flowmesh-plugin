"""Tests for LumidResourceRegistrar."""

import logging
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from lumid_hooks import PrincipalContext, ResourceRef

from lumid_flowmesh_plugin.acl import OwnershipStore, open_store
from lumid_flowmesh_plugin.registrar import LumidResourceRegistrar


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[OwnershipStore]:
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


async def test_register_writes_ownership_row(
    store: OwnershipStore, logger: logging.Logger
) -> None:
    reg = LumidResourceRegistrar(store)
    await reg.register(
        _principal("alice"), ResourceRef(kind="workflow", id="wf-1"), logger
    )
    assert await store.get("workflow", "wf-1") == "alice"


async def test_deregister_removes_ownership_row(
    store: OwnershipStore, logger: logging.Logger
) -> None:
    reg = LumidResourceRegistrar(store)
    await reg.register(
        _principal("alice"), ResourceRef(kind="task", id="t-1"), logger
    )
    await reg.deregister(
        _principal("alice"), ResourceRef(kind="task", id="t-1"), logger
    )
    assert await store.get("task", "t-1") is None


async def test_kind_level_register_is_noop(
    store: OwnershipStore, logger: logging.Logger, caplog: pytest.LogCaptureFixture
) -> None:
    reg = LumidResourceRegistrar(store)
    with caplog.at_level(logging.WARNING):
        await reg.register(
            _principal("alice"), ResourceRef(kind="workflow"), logger
        )
    assert "kind-level register" in caplog.text
    # No row written.
    assert await store.list_ids_for_principal("alice", "workflow") == frozenset()


async def test_kind_level_deregister_is_noop(
    store: OwnershipStore, logger: logging.Logger
) -> None:
    reg = LumidResourceRegistrar(store)
    # Should not raise even if the (kind, id=None) call slips through.
    await reg.deregister(
        _principal("alice"), ResourceRef(kind="workflow"), logger
    )


async def test_re_register_updates_owner(
    store: OwnershipStore, logger: logging.Logger
) -> None:
    reg = LumidResourceRegistrar(store)
    await reg.register(
        _principal("alice"), ResourceRef(kind="worker", id="w-1"), logger
    )
    await reg.register(
        _principal("bob"), ResourceRef(kind="worker", id="w-1"), logger
    )
    assert await store.get("worker", "w-1") == "bob"
