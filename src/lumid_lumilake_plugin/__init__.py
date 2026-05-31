"""Lumilake plugin: a single IdentityProvider that introspects lum.id bearers."""

from lumilake_hook import BaseBindings

from ._core import (
    CoreSettings,
    IntrospectedToken,
    LumidIdentityProvider,
    build_email_cache,
)


def install() -> BaseBindings:
    core = CoreSettings.from_env()
    email_cache = build_email_cache()

    identity = LumidIdentityProvider(
        base_url=core.lum_id_base_url,
        org_id=core.lumid_org_id,
        email_cache=email_cache,
        name="lumid_lumilake_plugin.identity",
    )

    return BaseBindings(identity_providers=(identity,))


__all__ = [
    "BaseBindings",
    "IntrospectedToken",
    "LumidIdentityProvider",
    "install",
]
