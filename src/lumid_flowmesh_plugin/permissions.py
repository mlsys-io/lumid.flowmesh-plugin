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
  | NODE, READ            | `flowmesh:nodes:read`          |
  | NODE, WRITE           | `flowmesh:nodes:write`         |
  | WORKER, READ          | `flowmesh:workers:read`        |
  | WORKER, WRITE         | `flowmesh:workers:write`       |
  | SYSTEM, READ          | `flowmesh:system:read`         |

* **Concrete-id checks** allow any principal with a grant on the resource.
* **accessible_ids** returns the principal's granted ids, or `None` for admins.
"""

import logging

from fastapi import HTTPException, status
from flowmesh_hook import ResourceAction, ResourceKind
from lumid_hooks import PrincipalContext, ResourceRef

from .acl import GrantStore

_ADMIN_SCOPES: frozenset[str] = frozenset({"*", "flowmesh:*", "flowmesh:admin"})

# Anything not in this map is admin-only at kind level.
_KIND_LEVEL_SCOPES: dict[tuple[str, str], str] = {
    (ResourceKind.WORKFLOW.value, ResourceAction.READ.value): "flowmesh:workflows:read",
    (ResourceKind.WORKFLOW.value, ResourceAction.WRITE.value): "flowmesh:workflows:write",
    (ResourceKind.TASK.value, ResourceAction.READ.value): "flowmesh:tasks:read",
    (ResourceKind.RESULT.value, ResourceAction.READ.value): "flowmesh:results:read",
    (ResourceKind.NODE.value, ResourceAction.READ.value): "flowmesh:nodes:read",
    (ResourceKind.NODE.value, ResourceAction.WRITE.value): "flowmesh:nodes:write",
    (ResourceKind.WORKER.value, ResourceAction.READ.value): "flowmesh:workers:read",
    (ResourceKind.WORKER.value, ResourceAction.WRITE.value): "flowmesh:workers:write",
    (ResourceKind.SYSTEM.value, ResourceAction.READ.value): "flowmesh:system:read",
}


def _is_admin(principal: PrincipalContext) -> bool:
    return any(scope in _ADMIN_SCOPES for scope in principal.scopes)


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
            required = _KIND_LEVEL_SCOPES.get((resource.kind, action))
            if required is not None and required in principal.scopes:
                return
            raise self._deny(
                logger,
                f"kind-level {action} on {resource.kind} requires "
                f"{required!r}" if required else
                f"kind-level {action} on {resource.kind} is admin-only",
            )

        if await self._store.has_grant(resource.kind, resource.id, principal.principal_id):
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
        return await self._store.list_ids_for_principal(principal.principal_id, kind)

    def _deny(self, logger: logging.Logger, detail: str) -> HTTPException:
        logger.warning("%s: %s", self.name, detail)
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)
