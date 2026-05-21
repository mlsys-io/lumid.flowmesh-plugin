"""lumid_flowmesh_plugin — FlowMesh plugin bridging lum.id auth + Runmesh billing.

Entry point is `install()`, called by FlowMesh's plugin loader after the
process reads `FLOWMESH_PLUGINS=lumid_flowmesh_plugin`. Yields a
`flowmesh_hook.BaseBindings` carrying every hook this package registers,
and disposes the ACL SQLite engine on shutdown.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from flowmesh_hook import BaseBindings
from sqlalchemy.ext.asyncio import async_sessionmaker

from ._cache import TTLCache
from .acl import GrantStore, bootstrap_schema, make_engine
from .config import load_settings
from .identity import (
    _EMAIL_CAPACITY,
    _EMAIL_TTL_SEC,
    IntrospectedToken,
    LumidIdentityProvider,
)
from .permissions import LumidPermissionChecker
from .registrar import LumidResourceRegistrar
from .submission import RunmeshBalanceGuard
from .supplier import NamespaceSupplierResolver
from .usage import RunmeshUsageSink


@asynccontextmanager
async def install() -> AsyncIterator[BaseBindings]:
    settings = load_settings()
    email_cache: TTLCache[str] = TTLCache(ttl_sec=_EMAIL_TTL_SEC, capacity=_EMAIL_CAPACITY)

    engine = make_engine(settings.lumid_acl_db_path)
    try:
        await bootstrap_schema(engine)
        sm = async_sessionmaker(engine, expire_on_commit=False)
        store = GrantStore(sm)
        session_start = datetime.now(UTC)

        identity = LumidIdentityProvider(
            base_url=settings.lum_id_base_url,
            org_id=settings.lumid_org_id,
            email_cache=email_cache,
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
    finally:
        await engine.dispose()


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
