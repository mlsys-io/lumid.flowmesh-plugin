"""Tests for RunmeshBalanceGuard."""

import logging

import httpx
import pytest
import respx
from fastapi import HTTPException
from lumid_hooks import PrincipalContext

from lumid_flowmesh_plugin._cache import TTLCache
from lumid_flowmesh_plugin.submission import RunmeshBalanceGuard

RUNMESH_BASE = "https://kv.run:8000/Runmesh"
CHECK_URL = f"{RUNMESH_BASE}/billing/check-balance"


def _make_guard(email_cache: TTLCache[str]) -> RunmeshBalanceGuard:
    return RunmeshBalanceGuard(
        base_url=RUNMESH_BASE,
        secret="shh",
        org_id="lumid",
        email_cache=email_cache,
    )


def _principal(org_id: str = "lumid", principal_id: str = "user-1") -> PrincipalContext:
    return PrincipalContext(
        principal_id=principal_id,
        org_id=org_id,
        external_id=principal_id,
        principal_type="user",
        scopes=["*"],
    )


@respx.mock
async def test_skipped_for_non_lumid_org(
    email_cache: TTLCache[str], logger: logging.Logger
) -> None:
    route = respx.post(CHECK_URL)
    guard = _make_guard(email_cache)
    await guard.check(_principal(org_id="other"), logger)
    assert route.call_count == 0


@respx.mock
async def test_skipped_when_email_not_cached(
    email_cache: TTLCache[str], logger: logging.Logger
) -> None:
    route = respx.post(CHECK_URL)
    guard = _make_guard(email_cache)
    await guard.check(_principal(), logger)
    assert route.call_count == 0


@respx.mock
async def test_sufficient_balance_allows(
    email_cache: TTLCache[str], logger: logging.Logger
) -> None:
    email_cache.set("user-1", "alice@example.com")
    respx.post(CHECK_URL).mock(
        return_value=httpx.Response(200, json={"data": {"sufficient": True, "balance": "5.00"}})
    )
    guard = _make_guard(email_cache)
    await guard.check(_principal(), logger)


@respx.mock
async def test_insufficient_balance_raises_402(
    email_cache: TTLCache[str], logger: logging.Logger
) -> None:
    email_cache.set("user-1", "alice@example.com")
    respx.post(CHECK_URL).mock(
        return_value=httpx.Response(200, json={"data": {"sufficient": False, "balance": "0.00"}})
    )
    guard = _make_guard(email_cache)
    with pytest.raises(HTTPException) as exc_info:
        await guard.check(_principal(), logger)
    assert exc_info.value.status_code == 402


@respx.mock
async def test_runmesh_unreachable_fails_open(
    email_cache: TTLCache[str], logger: logging.Logger
) -> None:
    email_cache.set("user-1", "alice@example.com")
    respx.post(CHECK_URL).mock(side_effect=httpx.ConnectError("boom"))
    guard = _make_guard(email_cache)
    await guard.check(_principal(), logger)


@respx.mock
async def test_runmesh_non_200_fails_open(
    email_cache: TTLCache[str], logger: logging.Logger
) -> None:
    email_cache.set("user-1", "alice@example.com")
    respx.post(CHECK_URL).mock(return_value=httpx.Response(500, text="oops"))
    guard = _make_guard(email_cache)
    await guard.check(_principal(), logger)
