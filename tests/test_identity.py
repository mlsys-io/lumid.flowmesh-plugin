"""Tests for LumidIdentityProvider."""

import logging

import httpx
import pytest
import respx
from fastapi import HTTPException

from lumid_flowmesh_plugin._cache import TTLCache
from lumid_flowmesh_plugin.identity import LumidIdentityProvider

LUM_ID_BASE = "https://lum.id"
INTROSPECT_URL = f"{LUM_ID_BASE}/oauth/introspect"


def _make_provider(email_cache: TTLCache[str]) -> LumidIdentityProvider:
    return LumidIdentityProvider(
        base_url=LUM_ID_BASE,
        org_id="lumid",
        email_cache=email_cache,
    )


@respx.mock
async def test_resolve_active_token_returns_principal_and_caches_email(
    email_cache: TTLCache[str], logger: logging.Logger
) -> None:
    respx.post(INTROSPECT_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "active": True,
                "sub": "user-123",
                "email": "alice@example.com",
                "scopes": ["flowmesh:workers:register", "lumilake:*"],
                "source": "pat",
            },
        )
    )

    provider = _make_provider(email_cache)
    principal = await provider.resolve("lm_pat_live_xxx", logger)

    assert principal is not None
    assert principal.principal_id == "user-123"
    assert principal.org_id == "lumid"
    assert principal.external_id == "user-123"
    assert principal.principal_type == "user"
    assert principal.scopes == ["flowmesh:workers:register", "lumilake:*"]
    assert email_cache.get("user-123") == "alice@example.com"


@respx.mock
async def test_resolve_inactive_token_returns_none(
    email_cache: TTLCache[str], logger: logging.Logger
) -> None:
    respx.post(INTROSPECT_URL).mock(
        return_value=httpx.Response(
            200,
            json={"active": False, "reason": "revoked"},
        )
    )
    provider = _make_provider(email_cache)
    assert await provider.resolve("expired", logger) is None
    assert email_cache.get("user-123") is None


@respx.mock
async def test_resolve_network_error_raises_503(
    email_cache: TTLCache[str], logger: logging.Logger
) -> None:
    respx.post(INTROSPECT_URL).mock(side_effect=httpx.ConnectError("boom"))
    provider = _make_provider(email_cache)
    with pytest.raises(HTTPException) as exc_info:
        await provider.resolve("anything", logger)
    assert exc_info.value.status_code == 503


@respx.mock
async def test_resolve_non_200_returns_503(
    email_cache: TTLCache[str], logger: logging.Logger
) -> None:
    respx.post(INTROSPECT_URL).mock(return_value=httpx.Response(500, text="oops"))
    provider = _make_provider(email_cache)
    with pytest.raises(HTTPException) as exc_info:
        await provider.resolve("anything", logger)
    assert exc_info.value.status_code == 503


@respx.mock
async def test_resolve_caches_active_introspect(
    email_cache: TTLCache[str], logger: logging.Logger
) -> None:
    route = respx.post(INTROSPECT_URL).mock(
        return_value=httpx.Response(
            200,
            json={"active": True, "sub": "user-7", "scopes": ["*"]},
        )
    )
    provider = _make_provider(email_cache)
    await provider.resolve("same-token", logger)
    await provider.resolve("same-token", logger)
    assert route.call_count == 1


@respx.mock
async def test_active_token_without_sub_returns_none(
    email_cache: TTLCache[str], logger: logging.Logger
) -> None:
    respx.post(INTROSPECT_URL).mock(
        return_value=httpx.Response(
            200,
            json={"active": True, "sub": None, "scopes": ["*"]},
        )
    )
    provider = _make_provider(email_cache)
    assert await provider.resolve("malformed", logger) is None
