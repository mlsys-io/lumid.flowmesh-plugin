"""RunmeshUsageSink — mirror lumid-tenant usage rows to Runmesh billing."""

import logging
from collections.abc import Sequence

import httpx
from flowmesh_hook import UsageRow

from ._cache import TTLCache


class RunmeshUsageSink:
    """UsageSink[UsageRow] that POSTs rows to Runmesh's billing receiver.

    Hydrates `userEmail` from the IdentityProvider's principal_id→email cache.
    Rows without a cached email are emitted with `userEmail=None` and the
    receiver must hydrate from `userSub` if needed.

    Failures are logged and dropped; the host's `on_conflict_do_nothing`-style
    dedup elsewhere is the safety net against duplicates.
    """

    name = "lumid_flowmesh_plugin.usage"

    def __init__(
        self,
        *,
        base_url: str,
        secret: str,
        org_id: str,
        email_cache: TTLCache[str],
        timeout_sec: float = 10.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._secret = secret
        self._org_id = org_id
        self._email_cache = email_cache
        self._timeout_sec = timeout_sec

    async def emit(self, rows: Sequence[UsageRow], logger: logging.Logger) -> None:
        if not self._base_url or not self._secret or not rows:
            return

        lumid_rows = [r for r in rows if r.get("org_id") == self._org_id]
        if not lumid_rows:
            return

        async with httpx.AsyncClient(timeout=self._timeout_sec) as client:
            for row in lumid_rows:
                await self._post_row(client, row, logger)

    async def _post_row(
        self,
        client: httpx.AsyncClient,
        row: UsageRow,
        logger: logging.Logger,
    ) -> None:
        principal_id = row["principal_id"]
        email = self._email_cache.get(principal_id)
        body = {
            "userEmail": email,
            "userSub": principal_id,
            "taskId": row["task_id"],
            "workflowId": None,
            "cost": str(row["cost"]),
            "durationSec": int(row["runtime_sec"]),
            "costPerHour": row["cost_per_hour"],
            "taskStatus": row["task_status"],
        }
        try:
            resp = await client.post(
                f"{self._base_url}/billing/flowmesh-entry",
                json=body,
                headers={"X-Bridge-Secret": self._secret},
            )
        except httpx.HTTPError as exc:
            logger.warning(
                "%s: POST failed for task %s: %s",
                self.name,
                row["task_id"],
                exc,
            )
            return

        if resp.status_code != 200:
            logger.warning(
                "%s: Runmesh returned %d for task %s: %s",
                self.name,
                resp.status_code,
                row["task_id"],
                resp.text[:200],
            )
