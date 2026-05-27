"""Tests for LumidPermissionChecker."""

import logging
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from fastapi import HTTPException
from flowmesh_hook import ResourceAction, ResourceKind
from lumid_hooks import PrincipalContext, ResourceRef

from lumid_flowmesh_plugin.acl import GrantLevel, GrantStore, open_store
from lumid_flowmesh_plugin.permissions import LumidPermissionChecker


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[GrantStore]:
    async with open_store(tmp_path / "acl.sqlite") as s:
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
ADMIN = ResourceAction.ADMIN.value


@pytest.mark.parametrize("admin_scope", ["*", "flowmesh:*", "flowmesh:admin"])
async def test_admin_bypass_all_actions(
    store: GrantStore, logger: logging.Logger, admin_scope: str
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
    store: GrantStore, logger: logging.Logger, kind: str, action: str, scope: str
) -> None:
    checker = LumidPermissionChecker(store)
    await checker.require(_principal("alice", scope), ResourceRef(kind=kind), action, logger)


async def test_kind_level_denies_without_scope(
    store: GrantStore, logger: logging.Logger
) -> None:
    checker = LumidPermissionChecker(store)
    with pytest.raises(HTTPException) as exc:
        await checker.require(_principal("alice"), ResourceRef(kind=WF), WRITE, logger)
    assert exc.value.status_code == 403


async def test_kind_level_denies_with_wrong_scope(
    store: GrantStore, logger: logging.Logger
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


async def test_concrete_id_grantee_allowed(
    store: GrantStore, logger: logging.Logger
) -> None:
    checker = LumidPermissionChecker(store)
    await store.grant(WF, "wf-1", "alice")
    for action in (READ, WRITE, CANCEL):
        await checker.require(
            _principal("alice"), ResourceRef(kind=WF, id="wf-1"), action, logger
        )


async def test_concrete_id_second_grantee_allowed(
    store: GrantStore, logger: logging.Logger
) -> None:
    checker = LumidPermissionChecker(store)
    await store.grant(WF, "wf-1", "alice")
    await store.grant(WF, "wf-1", "bob")
    await checker.require(
        _principal("bob"), ResourceRef(kind=WF, id="wf-1"), READ, logger
    )


async def test_concrete_id_non_grantee_denied(
    store: GrantStore, logger: logging.Logger
) -> None:
    checker = LumidPermissionChecker(store)
    await store.grant(WF, "wf-1", "alice")
    with pytest.raises(HTTPException) as exc:
        await checker.require(_principal("bob"), ResourceRef(kind=WF, id="wf-1"), READ, logger)
    assert exc.value.status_code == 403


async def test_concrete_id_unknown_resource_denied(
    store: GrantStore, logger: logging.Logger
) -> None:
    checker = LumidPermissionChecker(store)
    with pytest.raises(HTTPException) as exc:
        await checker.require(
            _principal("alice"), ResourceRef(kind=WF, id="never-registered"), READ, logger
        )
    assert exc.value.status_code == 403


async def test_concrete_id_non_grantee_with_read_scope_denied(
    store: GrantStore, logger: logging.Logger
) -> None:
    checker = LumidPermissionChecker(store)
    await store.grant(WF, "wf-1", "alice")
    with pytest.raises(HTTPException) as exc:
        await checker.require(
            _principal("ops", "flowmesh:workflows:read"),
            ResourceRef(kind=WF, id="wf-1"),
            READ,
            logger,
        )
    assert exc.value.status_code == 403


async def test_concrete_id_read_grantee_cannot_mutate(
    store: GrantStore, logger: logging.Logger
) -> None:
    checker = LumidPermissionChecker(store)
    await store.grant(WF, "wf-1", "alice", GrantLevel.READ)
    await checker.require(
        _principal("alice"), ResourceRef(kind=WF, id="wf-1"), READ, logger
    )
    for action in (WRITE, CANCEL):
        with pytest.raises(HTTPException) as exc:
            await checker.require(
                _principal("alice"), ResourceRef(kind=WF, id="wf-1"), action, logger
            )
        assert exc.value.status_code == 403


async def test_concrete_id_admin_action_denied_for_grantee(
    store: GrantStore, logger: logging.Logger
) -> None:
    checker = LumidPermissionChecker(store)
    await store.grant(WF, "wf-1", "alice")  # WRITE-level owner
    with pytest.raises(HTTPException) as exc:
        await checker.require(
            _principal("alice"), ResourceRef(kind=WF, id="wf-1"), ADMIN, logger
        )
    assert exc.value.status_code == 403
    assert "admin-only" in exc.value.detail


async def test_result_ownership_resolves_against_task_grant(
    store: GrantStore, logger: logging.Logger
) -> None:
    # RESULT has no grants of its own; ownership is the owning task's grant
    # (result id == task id). FlowMesh never registers RESULT directly.
    checker = LumidPermissionChecker(store)
    await store.grant(TASK, "t-1", "alice")
    await checker.require(
        _principal("alice"), ResourceRef(kind=RESULT, id="t-1"), READ, logger
    )
    with pytest.raises(HTTPException):
        await checker.require(
            _principal("bob"), ResourceRef(kind=RESULT, id="t-1"), READ, logger
        )


async def test_kind_level_unsupported_pair_message(
    store: GrantStore, logger: logging.Logger
) -> None:
    checker = LumidPermissionChecker(store)
    with pytest.raises(HTTPException) as exc:
        await checker.require(
            _principal("alice"), ResourceRef(kind="banana"), READ, logger
        )
    assert exc.value.status_code == 403
    assert "unsupported" in exc.value.detail


async def test_kind_level_admin_only_pair_message(
    store: GrantStore, logger: logging.Logger
) -> None:
    # SYSTEM/WRITE is a recognised pair with no kind-level scope -> admin-only.
    checker = LumidPermissionChecker(store)
    with pytest.raises(HTTPException) as exc:
        await checker.require(
            _principal("alice"), ResourceRef(kind=SYSTEM), WRITE, logger
        )
    assert exc.value.status_code == 403
    assert "admin-only" in exc.value.detail


async def test_accessible_ids_returns_granted_set(
    store: GrantStore, logger: logging.Logger
) -> None:
    checker = LumidPermissionChecker(store)
    await store.grant(WF, "wf-1", "alice")
    await store.grant(WF, "wf-2", "alice")
    await store.grant(WF, "wf-3", "bob")
    assert await checker.accessible_ids(_principal("alice"), WF, READ, logger) == frozenset(
        {"wf-1", "wf-2"}
    )
    assert await checker.accessible_ids(_principal("bob"), WF, READ, logger) == frozenset(
        {"wf-3"}
    )


async def test_accessible_ids_includes_shared_resources(
    store: GrantStore, logger: logging.Logger
) -> None:
    checker = LumidPermissionChecker(store)
    await store.grant(WF, "wf-1", "alice")
    await store.grant(WF, "wf-1", "bob")
    await store.grant(WF, "wf-2", "bob")
    assert await checker.accessible_ids(_principal("bob"), WF, READ, logger) == frozenset(
        {"wf-1", "wf-2"}
    )


async def test_accessible_ids_admin_returns_none(
    store: GrantStore, logger: logging.Logger
) -> None:
    checker = LumidPermissionChecker(store)
    assert (
        await checker.accessible_ids(_principal("alice", "*"), WF, READ, logger) is None
    )


async def test_accessible_ids_with_read_scope_returns_granted(
    store: GrantStore, logger: logging.Logger
) -> None:
    checker = LumidPermissionChecker(store)
    await store.grant(WF, "wf-1", "alice")
    await store.grant(WF, "wf-2", "bob")
    result = await checker.accessible_ids(
        _principal("alice", "flowmesh:workflows:read"), WF, READ, logger
    )
    assert result == frozenset({"wf-1"})


async def test_accessible_ids_write_action_excludes_read_grants(
    store: GrantStore, logger: logging.Logger
) -> None:
    checker = LumidPermissionChecker(store)
    await store.grant(WF, "wf-write", "alice", GrantLevel.WRITE)
    await store.grant(WF, "wf-read", "alice", GrantLevel.READ)
    assert await checker.accessible_ids(_principal("alice"), WF, READ, logger) == frozenset(
        {"wf-write", "wf-read"}
    )
    assert await checker.accessible_ids(_principal("alice"), WF, WRITE, logger) == frozenset(
        {"wf-write"}
    )


async def test_accessible_ids_admin_action_returns_empty(
    store: GrantStore, logger: logging.Logger
) -> None:
    checker = LumidPermissionChecker(store)
    await store.grant(WF, "wf-1", "alice")
    assert await checker.accessible_ids(_principal("alice"), WF, ADMIN, logger) == frozenset()
