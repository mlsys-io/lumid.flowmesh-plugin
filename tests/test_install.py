"""Tests for the `install()` entry point."""

import pytest
from flowmesh_hook import BaseBindings
from lumid_hooks import HookBindings as SharedHookBindings

from lumid_flowmesh_plugin import install


def test_install_returns_basebindings_with_identity_and_supplier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RUNMESH_BILLING_BASE_URL", raising=False)
    monkeypatch.delenv("FLOWMESH_BRIDGE_SECRET", raising=False)
    monkeypatch.delenv("LUMID_BALANCE_GUARD", raising=False)
    bindings = install()
    assert isinstance(bindings, BaseBindings)
    assert isinstance(bindings, SharedHookBindings)
    assert len(bindings.identity_providers) == 1
    assert len(bindings.supplier_resolvers) == 1
    assert len(bindings.usage_sinks) == 0
    assert len(bindings.submission_guards) == 0


def test_install_registers_usage_sink_when_billing_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RUNMESH_BILLING_BASE_URL", "https://kv.run:8000/Runmesh")
    monkeypatch.setenv("FLOWMESH_BRIDGE_SECRET", "shh")
    monkeypatch.delenv("LUMID_BALANCE_GUARD", raising=False)
    bindings = install()
    assert len(bindings.usage_sinks) == 1
    assert len(bindings.submission_guards) == 0


def test_install_registers_balance_guard_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RUNMESH_BILLING_BASE_URL", "https://kv.run:8000/Runmesh")
    monkeypatch.setenv("FLOWMESH_BRIDGE_SECRET", "shh")
    monkeypatch.setenv("LUMID_BALANCE_GUARD", "on")
    bindings = install()
    assert len(bindings.usage_sinks) == 1
    assert len(bindings.submission_guards) == 1


def test_install_skips_billing_when_secret_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RUNMESH_BILLING_BASE_URL", "https://kv.run:8000/Runmesh")
    monkeypatch.delenv("FLOWMESH_BRIDGE_SECRET", raising=False)
    monkeypatch.setenv("LUMID_BALANCE_GUARD", "on")
    bindings = install()
    assert len(bindings.usage_sinks) == 0
    assert len(bindings.submission_guards) == 0
