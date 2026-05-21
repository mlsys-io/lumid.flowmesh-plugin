"""LumidResourceRegistrar — populate the ACL on resource lifecycle events.

FlowMesh fires ``register(principal, ResourceRef(kind, id), logger)`` after
each WORKFLOW / TASK / NODE / WORKER is persisted, and ``deregister`` after
each hard-delete. We write a grant on register and wipe every grant on the
resource on deregister, so the PermissionChecker can read the current set.

Kind-level refs (``resource.id is None``) are no-ops with a logged warning —
they shouldn't reach a registrar in practice, but we don't want to crash
on one if the server ever does fire one.
"""

import logging

from lumid_hooks import PrincipalContext, ResourceRef

from .acl import GrantStore


class LumidResourceRegistrar:
    name = "lumid_flowmesh_plugin.registrar"

    def __init__(self, store: GrantStore) -> None:
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
        await self._store.grant(resource.kind, resource.id, principal.principal_id)
        logger.debug(
            "%s: grant %s/%s -> %s",
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
        removed = await self._store.delete_resource(resource.kind, resource.id)
        logger.debug(
            "%s: deregister %s/%s removed=%d actor=%s",
            self.name,
            resource.kind,
            resource.id,
            removed,
            principal.principal_id,
        )
