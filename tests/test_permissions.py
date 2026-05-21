"""Tests for LumidPermissionChecker."""

import logging
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from fastapi import HTTPException
from flowmesh_hook import ResourceAction, ResourceKind
from lumid_hooks import PrincipalContext, ResourceRef

from lumid_flowmesh_plugin.acl import OwnershipStore, open_store
from lumid_flowmesh_plugin.permissions import LumidPermissionChecker


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[OwnershipStore]:
    async with open_store(tmp_path / "acl.sqlite") as (_engine, s):
        yield s


def _principal(pid: str, *scopes: str) -> PrincipalContext:
    return PrincipalContext(
        principal_id=pid,
        org_id="lumid",
        external_id=pid,
        principal_type="user",
        scopes=list(scopes),
    )


WF = ResourceKind.WORKFLOW.value
TASK = ResourceKind.TASK.value
NODE = ResourceKind.NODE.value
WORKER = ResourceKind.WORKER.value
SYSTEM = ResourceKind.SYSTEM.value
RESULT = ResourceKind.RESULT.value
WRITE = ResourceAction.WRITE.value
READ = ResourceAction.READ.value
CANCEL = ResourceAction.CANCEL.value


@pytest.mark.parametrize("admin_scope", ["*", "flowmesh:*", "flowmesh:admin"])
async def test_admin_bypass_all_actions(
    store: OwnershipStore, logger: logging.Logger, admin_scope: str
) -> None:
    checker = LumidPermissionChecker(store)
    # Kind-level and concrete-id, both pass with no ACL row at all.
    await checker.require(_principal("alice", admin_scope), ResourceRef(kind=WF), WRITE, logger)
    await checker.require(
        _principal("alice", admin_scope),
        ResourceRef(kind=WF, id="wf-owned-by-someone-else"),
        READ,
        logger,
    )
    assert await checker.accessible_ids(_principal("alice", admin_scope), WF, READ, logger) is None


@pytest.mark.parametrize(
    "kind,action,scope",
    [
        (WF, READ, "flowmesh:workflows:read"),
        (WF, WRITE, "flowmesh:workflows:write"),
        (TASK, READ, "flowmesh:tasks:read"),
        (RESULT, READ, "flowmesh:results:read"),
        (NODE, READ, "flowmesh:nodes:read"),
        (NODE, WRITE, "flowmesh:nodes:write"),
        (WORKER, READ, "flowmesh:workers:read"),
        (WORKER, WRITE, "flowmesh:workers:write"),
        (SYSTEM, READ, "flowmesh:system:read"),
    ],
)
async def test_kind_level_scope_grants_access(
    store: OwnershipStore, logger: logging.Logger, kind: str, action: str, scope: str
) -> None:
    checker = LumidPermissionChecker(store)
    await checker.require(_principal("alice", scope), ResourceRef(kind=kind), action, logger)


async def test_kind_level_denies_without_scope(
    store: OwnershipStore, logger: logging.Logger
) -> None:
    checker = LumidPermissionChecker(store)
    with pytest.raises(HTTPException) as exc:
        await checker.require(_principal("alice"), ResourceRef(kind=WF), WRITE, logger)
    assert exc.value.status_code == 403


async def test_kind_level_denies_with_wrong_scope(
    store: OwnershipStore, logger: logging.Logger
) -> None:
    checker = LumidPermissionChecker(store)
    # Has nodes:write but tries to create a workflow.
    with pytest.raises(HTTPException) as exc:
        await checker.require(
            _principal("alice", "flowmesh:nodes:write"),
            ResourceRef(kind=WF),
            WRITE,
            logger,
        )
    assert exc.value.status_code == 403


async def test_concrete_id_owner_allowed(
    store: OwnershipStore, logger: logging.Logger
) -> None:
    checker = LumidPermissionChecker(store)
    await store.set(WF, "wf-1", "alice")
    for action in (READ, WRITE, CANCEL):
        await checker.require(
            _principal("alice"), ResourceRef(kind=WF, id="wf-1"), action, logger
        )


async def test_concrete_id_non_owner_denied(
    store: OwnershipStore, logger: logging.Logger
) -> None:
    checker = LumidPermissionChecker(store)
    await store.set(WF, "wf-1", "alice")
    with pytest.raises(HTTPException) as exc:
        await checker.require(_principal("bob"), ResourceRef(kind=WF, id="wf-1"), READ, logger)
    assert exc.value.status_code == 403


async def test_concrete_id_unknown_resource_denied(
    store: OwnershipStore, logger: logging.Logger
) -> None:
    checker = LumidPermissionChecker(store)
    with pytest.raises(HTTPException) as exc:
        await checker.require(
            _principal("alice"), ResourceRef(kind=WF, id="never-registered"), READ, logger
        )
    assert exc.value.status_code == 403


async def test_concrete_id_non_owner_with_read_scope_denied(
    store: OwnershipStore, logger: logging.Logger
) -> None:
    checker = LumidPermissionChecker(store)
    await store.set(WF, "wf-1", "alice")
    with pytest.raises(HTTPException) as exc:
        await checker.require(
            _principal("ops", "flowmesh:workflows:read"),
            ResourceRef(kind=WF, id="wf-1"),
            READ,
            logger,
        )
    assert exc.value.status_code == 403


async def test_result_kind_owner_only(
    store: OwnershipStore, logger: logging.Logger
) -> None:
    checker = LumidPermissionChecker(store)
    await store.set(RESULT, "r-1", "alice")
    await checker.require(_principal("alice"), ResourceRef(kind=RESULT, id="r-1"), READ, logger)
    with pytest.raises(HTTPException):
        await checker.require(_principal("bob"), ResourceRef(kind=RESULT, id="r-1"), READ, logger)


async def test_accessible_ids_returns_owned_set(
    store: OwnershipStore, logger: logging.Logger
) -> None:
    checker = LumidPermissionChecker(store)
    await store.set(WF, "wf-1", "alice")
    await store.set(WF, "wf-2", "alice")
    await store.set(WF, "wf-3", "bob")
    assert await checker.accessible_ids(_principal("alice"), WF, READ, logger) == frozenset(
        {"wf-1", "wf-2"}
    )
    assert await checker.accessible_ids(_principal("bob"), WF, READ, logger) == frozenset(
        {"wf-3"}
    )


async def test_accessible_ids_admin_returns_none(
    store: OwnershipStore, logger: logging.Logger
) -> None:
    checker = LumidPermissionChecker(store)
    assert (
        await checker.accessible_ids(_principal("alice", "*"), WF, READ, logger) is None
    )


async def test_accessible_ids_with_read_scope_returns_owned(
    store: OwnershipStore, logger: logging.Logger
) -> None:
    checker = LumidPermissionChecker(store)
    await store.set(WF, "wf-1", "alice")
    await store.set(WF, "wf-2", "bob")
    result = await checker.accessible_ids(
        _principal("alice", "flowmesh:workflows:read"), WF, READ, logger
    )
    assert result == frozenset({"wf-1"})
