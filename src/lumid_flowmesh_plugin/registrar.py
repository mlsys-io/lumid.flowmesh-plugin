"""LumidResourceRegistrar — populate the ACL on resource lifecycle events.

FlowMesh fires `register(principal, ResourceRef(kind, id), logger)` after
each WORKFLOW / TASK / NODE / WORKER is persisted, and `deregister` after
each hard-delete. We write/remove the corresponding ownership row so the
PermissionChecker can read it.

Kind-level refs (`resource.id is None`) are no-ops with a logged warning —
they shouldn't reach a registrar in practice, but we don't want to crash
on one if the server ever does fire one.
"""

import logging

from lumid_hooks import PrincipalContext, ResourceRef

from .acl import OwnershipStore


class LumidResourceRegistrar:
    name = "lumid_flowmesh_plugin.registrar"

    def __init__(self, store: OwnershipStore) -> None:
        self._store = store

    async def register(
        self,
        principal: PrincipalContext,
        resource: ResourceRef,
        logger: logging.Logger,
    ) -> None:
        if resource.id is None:
            logger.warning(
                "%s: ignoring kind-level register kind=%s actor=%s",
                self.name,
                resource.kind,
                principal.principal_id,
            )
            return
        await self._store.set(resource.kind, resource.id, principal.principal_id)
        logger.debug(
            "%s: register %s/%s -> %s",
            self.name,
            resource.kind,
            resource.id,
            principal.principal_id,
        )

    async def deregister(
        self,
        principal: PrincipalContext,
        resource: ResourceRef,
        logger: logging.Logger,
    ) -> None:
        if resource.id is None:
            return
        removed = await self._store.delete(resource.kind, resource.id)
        logger.debug(
            "%s: deregister %s/%s removed=%s actor=%s",
            self.name,
            resource.kind,
            resource.id,
            removed,
            principal.principal_id,
        )
