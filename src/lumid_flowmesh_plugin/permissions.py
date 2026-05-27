"""LumidPermissionChecker — admin bypass + scope vocabulary + grants.

Authorization policy:

* **Admin** (`*`, `flowmesh:*`, or `flowmesh:admin` in `principal.scopes`)
  bypasses every check.
* **Kind-level checks** (`resource.id is None`) require the matching scope:

  | (kind, action)        | scope                          |
  |-----------------------|--------------------------------|
  | WORKFLOW, READ        | `flowmesh:workflows:read`      |
  | WORKFLOW, WRITE       | `flowmesh:workflows:write`     |
  | TASK, READ            | `flowmesh:tasks:read`          |
  | RESULT, READ          | `flowmesh:results:read`        |
  | RESULT, WRITE         | `flowmesh:results:write`       |
  | NODE, READ            | `flowmesh:nodes:read`          |
  | NODE, WRITE           | `flowmesh:nodes:write`         |
  | WORKER, READ          | `flowmesh:workers:read`        |
  | WORKER, WRITE         | `flowmesh:workers:write`       |
  | SYSTEM, READ          | `flowmesh:system:read`         |

  A valid `(kind, action)` absent from the table is admin-only; an unrecognised
  kind or action is unsupported. Both deny.
* **Concrete-id checks** require a grant whose level covers the action: READ
  needs `GrantLevel.READ`, mutating actions (WRITE, CANCEL) need
  `GrantLevel.WRITE`. The `admin` action is never grant-satisfiable. RESULT has
  no grants of its own — ownership is inferred from the owning task, so a
  concrete RESULT check resolves against the TASK grant of the same id.
* **accessible_ids** returns the ids the principal can act on at the requested
  action's level, or `None` for admins.
"""

import logging

from fastapi import HTTPException, status
from flowmesh_hook import ResourceAction, ResourceKind
from lumid_hooks import PrincipalContext, ResourceRef

from .acl import GrantLevel, GrantStore

_ADMIN_SCOPES: frozenset[str] = frozenset({"*", "flowmesh:*", "flowmesh:admin"})

# Anything not in this map is admin-only at kind level.
_KIND_LEVEL_SCOPES: dict[tuple[str, str], str] = {
    (ResourceKind.WORKFLOW.value, ResourceAction.READ.value): "flowmesh:workflows:read",
    (ResourceKind.WORKFLOW.value, ResourceAction.WRITE.value): "flowmesh:workflows:write",
    (ResourceKind.TASK.value, ResourceAction.READ.value): "flowmesh:tasks:read",
    (ResourceKind.RESULT.value, ResourceAction.READ.value): "flowmesh:results:read",
    (ResourceKind.RESULT.value, ResourceAction.WRITE.value): "flowmesh:results:write",
    (ResourceKind.NODE.value, ResourceAction.READ.value): "flowmesh:nodes:read",
    (ResourceKind.NODE.value, ResourceAction.WRITE.value): "flowmesh:nodes:write",
    (ResourceKind.WORKER.value, ResourceAction.READ.value): "flowmesh:workers:read",
    (ResourceKind.WORKER.value, ResourceAction.WRITE.value): "flowmesh:workers:write",
    (ResourceKind.SYSTEM.value, ResourceAction.READ.value): "flowmesh:system:read",
}

# Grant level a concrete-id action needs. Actions absent here (e.g. `admin`)
# are never satisfiable by a grant — only the admin-scope bypass clears them.
_REQUIRED_LEVEL: dict[str, GrantLevel] = {
    ResourceAction.READ.value: GrantLevel.READ,
    ResourceAction.WRITE.value: GrantLevel.WRITE,
    ResourceAction.CANCEL.value: GrantLevel.WRITE,
}

# Kinds whose ownership lives under a different kind's grants.
_OWNERSHIP_KIND: dict[str, str] = {
    ResourceKind.RESULT.value: ResourceKind.TASK.value,
}

_VALID_KINDS: frozenset[str] = frozenset(k.value for k in ResourceKind)
_VALID_ACTIONS: frozenset[str] = frozenset(a.value for a in ResourceAction)


def _is_admin(principal: PrincipalContext) -> bool:
    return any(scope in _ADMIN_SCOPES for scope in principal.scopes)


def _recognised(kind: str, action: str) -> bool:
    return kind in _VALID_KINDS and action in _VALID_ACTIONS


class LumidPermissionChecker:
    name = "lumid_flowmesh_plugin.permissions"

    def __init__(self, store: GrantStore) -> None:
        self._store = store

    async def require(
        self,
        principal: PrincipalContext,
        resource: ResourceRef,
        action: str,
        logger: logging.Logger,
    ) -> None:
        if _is_admin(principal):
            return

        if resource.id is None:
            required_scope = _KIND_LEVEL_SCOPES.get((resource.kind, action))
            if required_scope is not None and required_scope in principal.scopes:
                return
            if required_scope is not None:
                detail = (
                    f"kind-level {action} on {resource.kind} requires {required_scope!r}"
                )
            elif _recognised(resource.kind, action):
                detail = f"kind-level {action} on {resource.kind} is admin-only"
            else:
                detail = f"unsupported kind-level {action} on {resource.kind}"
            raise self._deny(logger, detail)

        if not _recognised(resource.kind, action):
            raise self._deny(
                logger, f"unsupported {action} on {resource.kind}/{resource.id}"
            )

        required_level = _REQUIRED_LEVEL.get(action)
        if required_level is None:
            raise self._deny(
                logger, f"{action} on {resource.kind}/{resource.id} is admin-only"
            )

        owner_kind = _OWNERSHIP_KIND.get(resource.kind, resource.kind)
        level = await self._store.get_level(
            owner_kind, resource.id, principal.principal_id
        )
        if level is not None and level >= required_level:
            return
        raise self._deny(
            logger,
            f"{action} on {resource.kind}/{resource.id} denied for "
            f"principal {principal.principal_id}",
        )

    async def accessible_ids(
        self,
        principal: PrincipalContext,
        kind: str,
        action: str,
        logger: logging.Logger,
    ) -> frozenset[str] | None:
        if _is_admin(principal):
            return None
        required_level = _REQUIRED_LEVEL.get(action)
        if required_level is None:
            return frozenset()
        owner_kind = _OWNERSHIP_KIND.get(kind, kind)
        return await self._store.list_ids_for_principal(
            principal.principal_id, owner_kind, required_level
        )

    def _deny(self, logger: logging.Logger, detail: str) -> HTTPException:
        logger.warning("%s: %s", self.name, detail)
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)
