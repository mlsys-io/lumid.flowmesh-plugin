"""Lumilake plugin: a single IdentityProvider that introspects lum.id bearers."""

from lumilake_hook import BaseBindings

from ._core import (
    IntrospectedToken,
    LumidIdentityProvider,
    build_email_cache,
    load_core_settings,
)


def install() -> BaseBindings:
    core = load_core_settings()
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
