"""lumid_flowmesh_plugin — FlowMesh plugin bridging lum.id auth + Runmesh billing.

Entry point is `install()`, called by FlowMesh's plugin loader after the
process reads `FLOWMESH_PLUGINS=lumid_flowmesh_plugin`. Returns a
`flowmesh_hook.BaseBindings` carrying every hook this package registers.
"""

from flowmesh_hook import BaseBindings

from ._cache import TTLCache
from .config import load_settings
from .identity import (
    _EMAIL_CAPACITY,
    _EMAIL_TTL_SEC,
    IntrospectedToken,
    LumidIdentityProvider,
)
from .submission import RunmeshBalanceGuard
from .supplier import NamespaceSupplierResolver
from .usage import RunmeshUsageSink


def install() -> BaseBindings:
    settings = load_settings()
    email_cache: TTLCache[str] = TTLCache(ttl_sec=_EMAIL_TTL_SEC, capacity=_EMAIL_CAPACITY)

    identity = LumidIdentityProvider(
        base_url=settings.lum_id_base_url,
        org_id=settings.lumid_org_id,
        email_cache=email_cache,
    )
    supplier = NamespaceSupplierResolver()

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

    return BaseBindings(
        identity_providers=(identity,),
        submission_guards=submission_guards,
        usage_sinks=usage_sinks,
        supplier_resolvers=(supplier,),
    )


__all__ = [
    "BaseBindings",
    "IntrospectedToken",
    "LumidIdentityProvider",
    "NamespaceSupplierResolver",
    "RunmeshBalanceGuard",
    "RunmeshUsageSink",
    "install",
]
