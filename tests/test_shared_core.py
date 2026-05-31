"""Tests for the shared `_core` package at `src/_shared_core/`."""

import ast
from pathlib import Path

import pytest

from lumid_flowmesh_plugin._core import (
    CoreSettings,
    LumidIdentityProvider,
    TTLCache,
    build_email_cache,
)


def test_core_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LUM_ID_BASE_URL", raising=False)
    monkeypatch.delenv("LUMID_ORG_ID", raising=False)
    settings = CoreSettings.from_env()
    assert isinstance(settings, CoreSettings)
    assert settings.lum_id_base_url == "https://lum.id"
    assert settings.lumid_org_id == "lumid"


def test_core_settings_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LUM_ID_BASE_URL", "https://lum.id.dev/")
    monkeypatch.setenv("LUMID_ORG_ID", "tenant-42")
    settings = CoreSettings.from_env()
    assert settings.lum_id_base_url == "https://lum.id.dev"
    assert settings.lumid_org_id == "tenant-42"


def test_identity_provider_default_name_is_core_scoped() -> None:
    identity = LumidIdentityProvider(
        base_url="https://lum.id",
        org_id="lumid",
        email_cache=build_email_cache(),
    )
    assert identity.name == "lumid_plugin._core.identity"


def test_shared_core_does_not_import_host_modules() -> None:
    # AST walk, not string-search, so docstring text doesn't trigger.
    forbidden = {
        "flowmesh_hook",
        "lumilake_hook",
        "lumid_flowmesh_plugin",
        "lumid_lumilake_plugin",
    }
    shared_root = Path(__file__).resolve().parent.parent / "src" / "_shared_core"
    assert shared_root.is_dir(), f"missing shared-core dir at {shared_root}"

    leaks: dict[str, set[str]] = {}
    for path in sorted(shared_root.glob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imported.add(node.module.split(".")[0])
        leaked = imported & forbidden
        if leaked:
            leaks[path.name] = leaked

    assert not leaks, f"shared-core files imported host modules: {leaks}"


def test_each_plugin_exposes_core_via_symlink() -> None:
    # Catches Windows checkouts that materialize symlinks as text files.
    # Iterates over plugin packages whose source dir actually exists so the
    # test works whether or not the lumilake adapter has landed yet.
    src_root = Path(__file__).resolve().parent.parent / "src"
    shared = (src_root / "_shared_core").resolve()
    plugin_dirs = sorted(
        d for d in src_root.iterdir() if d.is_dir() and d.name.startswith("lumid_")
    )
    assert plugin_dirs, "expected at least one lumid_*_plugin source dir"
    for plugin_dir in plugin_dirs:
        core_link = plugin_dir / "_core"
        assert core_link.is_symlink(), f"{plugin_dir.name}/_core must be a symlink"
        assert core_link.resolve() == shared


def test_ttl_cache_round_trip() -> None:
    cache: TTLCache[str] = TTLCache(ttl_sec=60.0, capacity=10)
    cache.set("k", "v", now=1000.0)
    assert cache.get("k", now=1000.0) == "v"
