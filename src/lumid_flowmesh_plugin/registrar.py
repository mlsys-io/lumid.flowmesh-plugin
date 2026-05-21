"""LumidResourceRegistrar — populate the ACL on resource lifecycle events.

FlowMesh fires ``register(principal, ResourceRef(kind, id), logger)`` after
each WORKFLOW / TASK / NODE / WORKER is persisted, and ``deregister`` after
each hard-delete. We write a grant on register and wipe every grant on the
resource on deregister, so the PermissionChecker can read the current set.

At startup FlowMesh also runs a reconcile sweep — ``refresh`` is called
once with every live resource, then ``purge_stale`` once to drop any
grant the sweep didn't touch. ``session_start`` (set when the plugin
loads) marks the boundary.

Kind-level refs (``resource.id is None``) on register/deregister are
no-ops with a logged warning — they shouldn't reach a registrar in
practice, but we don't want to crash on one if the server ever does
fire one.
"""

import logging
from collections.abc import Iterable
from datetime import datetime

from lumid_hooks import PrincipalContext, ResourceRef

from .acl import GrantStore


class LumidResourceRegistrar:
    name = "lumid_flowmesh_plugin.registrar"

    def __init__(self, store: GrantStore, session_start: datetime) -> None:
        self._store = store
        self._session_start = session_start

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

    async def refresh(
        self,
        resources: Iterable[ResourceRef],
        logger: logging.Logger,
    ) -> None:
        pairs = [(r.kind, r.id) for r in resources if r.id is not None]
        touched = await self._store.touch_resources(pairs)
        logger.debug(
            "%s: refresh requested=%d touched=%d",
            self.name,
            len(pairs),
            touched,
        )

    async def purge_stale(self, logger: logging.Logger) -> None:
        removed = await self._store.delete_unrefreshed(self._session_start)
        logger.info(
            "%s: purge_stale removed=%d session_start=%s",
            self.name,
            removed,
            self._session_start.isoformat(),
        )
