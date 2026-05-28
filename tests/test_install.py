"""Tests for the `install()` async ctx-manager entry point."""

import sqlite3
from pathlib import Path

import pytest
from flowmesh_hook import BaseBindings
from lumid_hooks import HookBindings as SharedHookBindings

from lumid_flowmesh_plugin import (
    LumidPermissionChecker,
    LumidResourceRegistrar,
    install,
)


@pytest.fixture(autouse=True)
def _acl_db_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Point every test at a throwaway SQLite file."""
    monkeypatch.setenv("LUMID_ACL_DB_PATH", str(tmp_path / "acl.sqlite"))


async def test_install_yields_basebindings_with_all_hooks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RUNMESH_BILLING_BASE_URL", raising=False)
    monkeypatch.delenv("FLOWMESH_BRIDGE_SECRET", raising=False)
    monkeypatch.delenv("LUMID_BALANCE_GUARD", raising=False)
    async with install() as bindings:
        assert isinstance(bindings, BaseBindings)
        assert isinstance(bindings, SharedHookBindings)
        assert len(bindings.identity_providers) == 1
        assert len(bindings.supplier_resolvers) == 1
        assert len(bindings.permission_checkers) == 1
        assert len(bindings.resource_registrars) == 1
        assert isinstance(bindings.permission_checkers[0], LumidPermissionChecker)
        assert isinstance(bindings.resource_registrars[0], LumidResourceRegistrar)
        assert len(bindings.usage_sinks) == 0
        assert len(bindings.submission_guards) == 0


async def test_install_registers_usage_sink_when_billing_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RUNMESH_BILLING_BASE_URL", "https://kv.run:8000/Runmesh")
    monkeypatch.setenv("FLOWMESH_BRIDGE_SECRET", "shh")
    monkeypatch.delenv("LUMID_BALANCE_GUARD", raising=False)
    async with install() as bindings:
        assert len(bindings.usage_sinks) == 1
        assert len(bindings.submission_guards) == 0


async def test_install_registers_balance_guard_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RUNMESH_BILLING_BASE_URL", "https://kv.run:8000/Runmesh")
    monkeypatch.setenv("FLOWMESH_BRIDGE_SECRET", "shh")
    monkeypatch.setenv("LUMID_BALANCE_GUARD", "on")
    async with install() as bindings:
        assert len(bindings.usage_sinks) == 1
        assert len(bindings.submission_guards) == 1


async def test_install_skips_billing_when_secret_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RUNMESH_BILLING_BASE_URL", "https://kv.run:8000/Runmesh")
    monkeypatch.delenv("FLOWMESH_BRIDGE_SECRET", raising=False)
    monkeypatch.setenv("LUMID_BALANCE_GUARD", "on")
    async with install() as bindings:
        assert len(bindings.usage_sinks) == 0
        assert len(bindings.submission_guards) == 0


async def test_install_bootstraps_acl_schema(tmp_path: Path) -> None:
    db_path = tmp_path / "acl.sqlite"
    async with install() as _bindings:
        assert db_path.exists()

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    assert ("acl_grants",) in rows
