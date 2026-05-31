"""Tests for `lumid_lumilake_plugin.install()`."""

import pytest
from lumid_hooks import HookBindings as SharedHookBindings
from lumilake_hook import BaseBindings as LumilakeBaseBindings

from lumid_lumilake_plugin import install

# Same physical sources but distinct sys.modules entries vs the FlowMesh
# `_core`, so isinstance only matches against this import path.
from lumid_lumilake_plugin._core import LumidIdentityProvider


def test_install_returns_lumilake_basebindings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LUM_ID_BASE_URL", "https://lum.id")
    bindings = install()
    assert isinstance(bindings, LumilakeBaseBindings)
    assert isinstance(bindings, SharedHookBindings)


def test_install_exposes_only_identity_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LUM_ID_BASE_URL", "https://lum.id")
    bindings = install()
    assert len(bindings.identity_providers) == 1
    assert isinstance(bindings.identity_providers[0], LumidIdentityProvider)
    assert len(bindings.submission_guards) == 0
    assert len(bindings.usage_sinks) == 0
    assert len(bindings.permission_checkers) == 0
    assert len(bindings.resource_registrars) == 0


def test_identity_name_is_lumilake_scoped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LUM_ID_BASE_URL", "https://lum.id")
    identity = install().identity_providers[0]
    assert identity.name == "lumid_lumilake_plugin.identity"


def test_install_reads_org_id_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LUM_ID_BASE_URL", "https://lum.id")
    monkeypatch.setenv("LUMID_ORG_ID", "lumid-prod")
    identity = install().identity_providers[0]
    assert identity._org_id == "lumid-prod"
