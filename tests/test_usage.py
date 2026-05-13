"""Tests for RunmeshUsageSink."""

import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import respx
from flowmesh_hook import UsageRow

from lumid_flowmesh_plugin._cache import TTLCache
from lumid_flowmesh_plugin.usage import RunmeshUsageSink

RUNMESH_BASE = "https://kv.run:8000/Runmesh"
EMIT_URL = f"{RUNMESH_BASE}/billing/flowmesh-entry"


def _row(
    *,
    principal_id: str = "user-1",
    org_id: str = "lumid",
    task_id: str = "tsk-1",
    cost: Decimal = Decimal("0.42"),
    runtime_sec: float = 12.5,
    cost_per_hour: float = 1.5,
    task_status: str = "DONE",
) -> UsageRow:
    return UsageRow(  # type: ignore[typeddict-item]
        org_id=org_id,
        principal_id=principal_id,
        supplier_id="flowmesh",
        occurred_at=datetime.now(UTC),
        cost=cost,
        task_id=task_id,
        runtime_sec=runtime_sec,
        cost_per_hour=cost_per_hour,
        task_status=task_status,
    )


def _make_sink(email_cache: TTLCache[str]) -> RunmeshUsageSink:
    return RunmeshUsageSink(
        base_url=RUNMESH_BASE,
        secret="shh",
        org_id="lumid",
        email_cache=email_cache,
    )


@respx.mock
async def test_emit_posts_one_per_lumid_row_with_email_lookup(
    email_cache: TTLCache[str], logger: logging.Logger
) -> None:
    email_cache.set("user-1", "alice@example.com")
    route = respx.post(EMIT_URL).mock(return_value=httpx.Response(200, json={"ok": True}))

    sink = _make_sink(email_cache)
    rows: Sequence[UsageRow] = [
        _row(principal_id="user-1", task_id="tsk-1"),
        _row(principal_id="user-2", task_id="tsk-2"),  # no email in cache
        _row(principal_id="user-3", task_id="tsk-3", org_id="other"),  # filtered
    ]
    await sink.emit(rows, logger)

    assert route.call_count == 2
    bodies = [call.request.read() for call in route.calls]
    assert b'"userEmail":"alice@example.com"' in bodies[0]
    assert b'"userEmail":null' in bodies[1]
    assert b'"userSub":"user-1"' in bodies[0]


@respx.mock
async def test_emit_skips_when_base_url_or_secret_empty(
    email_cache: TTLCache[str], logger: logging.Logger
) -> None:
    route = respx.post(EMIT_URL)
    sink = RunmeshUsageSink(
        base_url="",
        secret="shh",
        org_id="lumid",
        email_cache=email_cache,
    )
    await sink.emit([_row()], logger)
    assert route.call_count == 0


@respx.mock
async def test_emit_isolates_per_row_failures(
    email_cache: TTLCache[str], logger: logging.Logger
) -> None:
    respx.post(EMIT_URL).mock(
        side_effect=[
            httpx.ConnectError("boom"),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    sink = _make_sink(email_cache)
    await sink.emit([_row(task_id="tsk-bad"), _row(task_id="tsk-good")], logger)


@respx.mock
async def test_emit_logs_non_200_and_continues(
    email_cache: TTLCache[str], logger: logging.Logger
) -> None:
    respx.post(EMIT_URL).mock(return_value=httpx.Response(500, text="nope"))
    sink = _make_sink(email_cache)
    await sink.emit([_row()], logger)


@respx.mock
async def test_empty_input_is_a_noop(
    email_cache: TTLCache[str], logger: logging.Logger
) -> None:
    route = respx.post(EMIT_URL)
    sink = _make_sink(email_cache)
    await sink.emit([], logger)
    assert route.call_count == 0
