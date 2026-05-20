"""LumidPermissionChecker — admin bypass + scope vocabulary + ownership.

Authorization policy:

* **Admin** (`*`, `flowmesh:*`, or `flowmesh:admin` in `principal.scopes`)
  bypasses every check.
* **Kind-level checks** (`resource.id is None`) require the matching scope:

  | (kind, action)        | scope                          |
  |-----------------------|--------------------------------|
  | WORKFLOW, WRITE       | `flowmesh:workflows:write`     |
  | NODE, WRITE           | `flowmesh:nodes:write`         |
  | WORKER, WRITE         | `flowmesh:workers:write`       |
  | SYSTEM, READ          | `flowmesh:system:read`         |

  No scope is defined for TASK or RESULT kind-level — tasks are created
  via workflow submission, results are inferred from tasks.

* **Concrete-id checks** allow if the principal owns the row in the
  ACL store. SYSTEM is the exception: a principal with
  `flowmesh:system:read` may read any SYSTEM resource by id.

* **accessible_ids** returns the set of ids the principal owns, or
  `None` for admins (no filter applied).
"""

import logging

from fastapi import HTTPException, status
from flowmesh_hook import ResourceAction, ResourceKind
from lumid_hooks import PrincipalContext, ResourceRef

from .acl import OwnershipStore

_ADMIN_SCOPES: frozenset[str] = frozenset({"*", "flowmesh:*", "flowmesh:admin"})

# Maps (kind, action) to the scope that authorizes the kind-level operation.
# Anything not in this map is implicitly admin-only at kind level.
_KIND_LEVEL_SCOPES: dict[tuple[str, str], str] = {
    (ResourceKind.WORKFLOW.value, ResourceAction.WRITE.value): "flowmesh:workflows:write",
    (ResourceKind.NODE.value, ResourceAction.WRITE.value): "flowmesh:nodes:write",
    (ResourceKind.WORKER.value, ResourceAction.WRITE.value): "flowmesh:workers:write",
    (ResourceKind.SYSTEM.value, ResourceAction.READ.value): "flowmesh:system:read",
}

_SYSTEM_READ_SCOPE = "flowmesh:system:read"


def _is_admin(principal: PrincipalContext) -> bool:
    return any(scope in _ADMIN_SCOPES for scope in principal.scopes)


class LumidPermissionChecker:
    name = "lumid_flowmesh_plugin.permissions"

    def __init__(self, store: OwnershipStore) -> None:
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

        owner = await self._store.get(resource.kind, resource.id)
        if owner == principal.principal_id:
            return
        if (
            resource.kind == ResourceKind.SYSTEM.value
            and action == ResourceAction.READ.value
            and _SYSTEM_READ_SCOPE in principal.scopes
        ):
            return
        raise self._deny(
            logger,
            f"{action} on {resource.kind}/{resource.id} denied for "
            f"principal {principal.principal_id} (owner={owner})",
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
