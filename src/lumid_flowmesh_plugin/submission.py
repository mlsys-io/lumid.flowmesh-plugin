"""RunmeshBalanceGuard — optional GPU-rental balance preflight.

Blocks workflow submission for lumid-tenant principals whose Runmesh balance
falls below `_MIN_BALANCE_FOR_SUBMIT`. Fails open if Runmesh is unreachable
so a billing-bridge outage doesn't take compute offline with it.
"""

import logging

import httpx
from fastapi import HTTPException, status
from lumid_hooks import PrincipalContext

from ._cache import TTLCache

_MIN_BALANCE_FOR_SUBMIT = "0.01"


class RunmeshBalanceGuard:
    name = "lumid_flowmesh_plugin.balance"

    def __init__(
        self,
        *,
        base_url: str,
        secret: str,
        org_id: str,
        email_cache: TTLCache[str],
        timeout_sec: float = 5.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._secret = secret
        self._org_id = org_id
        self._email_cache = email_cache
        self._timeout_sec = timeout_sec

    async def check(self, principal: PrincipalContext, logger: logging.Logger) -> None:
        if principal.org_id != self._org_id:
            return
        if not self._base_url or not self._secret:
            return
        email = self._email_cache.get(principal.principal_id)
        if not email:
            logger.debug(
                "%s: skipped (no email for principal %s)",
                self.name,
                principal.principal_id,
            )
            return

        try:
            async with httpx.AsyncClient(timeout=self._timeout_sec) as client:
                resp = await client.post(
                    f"{self._base_url}/billing/check-balance",
                    json={
                        "userEmail": email,
                        "requiredAmount": _MIN_BALANCE_FOR_SUBMIT,
                    },
                    headers={"X-Bridge-Secret": self._secret},
                )
        except httpx.HTTPError as exc:
            logger.warning("%s: Runmesh unreachable: %s", self.name, exc)
            return

        if resp.status_code != 200:
            logger.warning(
                "%s: Runmesh status %d: %s",
                self.name,
                resp.status_code,
                resp.text[:200],
            )
            return

        try:
            body = resp.json()
        except ValueError:
            return
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, dict):
            return
        if not data.get("sufficient"):
            balance = data.get("balance", "0")
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail=(
                    f"Insufficient balance (${balance}). "
                    "Top up at https://lum.id/app/billing before submitting workflows."
                ),
            )
