"""LumidIdentityProvider — resolve lum.id JWT / PAT tokens via /oauth/introspect."""

import hashlib
import logging
from typing import Any

import httpx
from fastapi import HTTPException, status
from lumid_hooks import PrincipalContext
from pydantic import BaseModel, ConfigDict, Field

from ._cache import TTLCache

_INTROSPECT_TTL_SEC = 60
_INTROSPECT_CAPACITY = 10_000
_EMAIL_TTL_SEC = 24 * 3600
_EMAIL_CAPACITY = 10_000


def build_email_cache() -> TTLCache[str]:
    """Construct the `principal_id -> email` cache shared by identity and usage."""
    return TTLCache(ttl_sec=_EMAIL_TTL_SEC, capacity=_EMAIL_CAPACITY)


class IntrospectedToken(BaseModel):
    model_config = ConfigDict(extra="ignore")

    active: bool = False
    sub: str | None = None
    email: str | None = None
    scopes: list[str] = Field(default_factory=list)
    source: str | None = None
    reason: str | None = None


class LumidIdentityProvider:
    """IdentityProvider that delegates token validation to lum.id."""

    name = "lumid_flowmesh_plugin.identity"

    def __init__(
        self,
        *,
        base_url: str,
        org_id: str,
        email_cache: TTLCache[str],
        introspect_cache: TTLCache[IntrospectedToken] | None = None,
        timeout_sec: float = 3.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._org_id = org_id
        self._email_cache = email_cache
        self._introspect_cache = introspect_cache or TTLCache[IntrospectedToken](
            ttl_sec=_INTROSPECT_TTL_SEC, capacity=_INTROSPECT_CAPACITY
        )
        self._timeout_sec = timeout_sec

    async def resolve(
        self, raw_token: str, logger: logging.Logger
    ) -> PrincipalContext | None:
        introspected = await self._introspect(raw_token, logger)
        if introspected is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Identity provider unavailable",
            )
        if not introspected.active:
            return None
        if introspected.sub is None:
            logger.warning("%s: active token missing sub claim", self.name)
            return None

        if introspected.email:
            self._email_cache.set(introspected.sub, introspected.email)

        return PrincipalContext(
            principal_id=introspected.sub,
            org_id=self._org_id,
            external_id=introspected.sub,
            principal_type="user",
            scopes=introspected.scopes,
        )

    async def _introspect(
        self, raw_token: str, logger: logging.Logger
    ) -> IntrospectedToken | None:
        digest = hashlib.sha256(raw_token.encode()).hexdigest()
        cached = self._introspect_cache.get(digest)
        if cached is not None:
            return cached

        try:
            async with httpx.AsyncClient(timeout=self._timeout_sec) as client:
                resp = await client.post(
                    f"{self._base_url}/oauth/introspect",
                    data={"token": raw_token},
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
        except httpx.HTTPError as exc:
            logger.warning("%s: introspect network error: %s", self.name, exc)
            return None

        if resp.status_code != 200:
            logger.warning(
                "%s: introspect status=%d body=%s",
                self.name,
                resp.status_code,
                resp.text[:200],
            )
            return None
        try:
            body: dict[str, Any] = resp.json()
        except ValueError:
            logger.warning("%s: introspect non-JSON body: %s", self.name, resp.text[:200])
            return None

        token = IntrospectedToken.model_validate(body)
        if token.active and token.sub is not None:
            self._introspect_cache.set(digest, token)
        return token
