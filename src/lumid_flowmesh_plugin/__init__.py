"""lumid_flowmesh_plugin — FlowMesh plugin bridging lum.id auth + Runmesh billing.

Entry point is `install()`, called by FlowMesh's plugin loader after the
process reads `FLOWMESH_PLUGINS=lumid_flowmesh_plugin`. Yields a
`flowmesh_hook.BaseBindings` carrying every hook this package registers,
and closes the ACL SQLite connection on shutdown.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from flowmesh_hook import BaseBindings

from ._core import (
    IntrospectedToken,
    LumidIdentityProvider,
    build_email_cache,
)
from .acl import open_store
from .config import load_settings
from .permissions import LumidPermissionChecker
from .registrar import LumidResourceRegistrar
from .submission import RunmeshBalanceGuard
from .supplier import NamespaceSupplierResolver
from .usage import RunmeshUsageSink


@asynccontextmanager
async def install() -> AsyncIterator[BaseBindings]:
    settings = load_settings()
    email_cache = build_email_cache()

    async with open_store(settings.lumid_acl_db_path) as store:
        session_start = datetime.now(UTC)

        identity = LumidIdentityProvider(
            base_url=settings.lum_id_base_url,
            org_id=settings.lumid_org_id,
            email_cache=email_cache,
            name="lumid_flowmesh_plugin.identity",
        )
        supplier = NamespaceSupplierResolver()
        permission_checker = LumidPermissionChecker(store)
        registrar = LumidResourceRegistrar(store, session_start)

        submission_guards: tuple[RunmeshBalanceGuard, ...] = ()
        usage_sinks: tuple[RunmeshUsageSink, ...] = ()

        if settings.runmesh_billing_base_url and settings.flowmesh_bridge_secret:
            usage_sinks = (
                RunmeshUsageSink(
                    base_url=settings.runmesh_billing_base_url,
                    secret=settings.flowmesh_bridge_secret,
                    email_cache=email_cache,
                ),
            )
            if settings.lumid_balance_guard_enabled:
                submission_guards = (
                    RunmeshBalanceGuard(
                        base_url=settings.runmesh_billing_base_url,
                        secret=settings.flowmesh_bridge_secret,
                        org_id=settings.lumid_org_id,
                        email_cache=email_cache,
                    ),
                )

        yield BaseBindings(
            identity_providers=(identity,),
            submission_guards=submission_guards,
            usage_sinks=usage_sinks,
            supplier_resolvers=(supplier,),
            permission_checkers=(permission_checker,),
            resource_registrars=(registrar,),
        )


__all__ = [
    "BaseBindings",
    "IntrospectedToken",
    "LumidIdentityProvider",
    "LumidPermissionChecker",
    "LumidResourceRegistrar",
    "NamespaceSupplierResolver",
    "RunmeshBalanceGuard",
    "RunmeshUsageSink",
    "install",
]
