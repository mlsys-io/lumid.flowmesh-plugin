"""Tests for the TTLCache primitive."""

import pytest

from lumid_flowmesh_plugin._cache import TTLCache


def test_set_and_get_roundtrip() -> None:
    cache: TTLCache[str] = TTLCache(ttl_sec=60.0, capacity=10)
    cache.set("k", "v", now=1000.0)
    assert cache.get("k", now=1010.0) == "v"


def test_get_missing_returns_none() -> None:
    cache: TTLCache[str] = TTLCache(ttl_sec=60.0, capacity=10)
    assert cache.get("absent") is None


def test_expired_entry_returns_none_and_evicts() -> None:
    cache: TTLCache[str] = TTLCache(ttl_sec=60.0, capacity=10)
    cache.set("k", "v", now=1000.0)
    assert cache.get("k", now=2000.0) is None
    assert len(cache) == 0


def test_capacity_prune_drops_head() -> None:
    cache: TTLCache[str] = TTLCache(ttl_sec=60.0, capacity=2)
    cache.set("a", "1", now=1000.0)
    cache.set("b", "2", now=1001.0)
    cache.set("c", "3", now=1002.0)
    assert len(cache) == 2
    assert cache.get("a", now=1003.0) is None
    assert cache.get("c", now=1003.0) == "3"


def test_clear_empties_the_store() -> None:
    cache: TTLCache[str] = TTLCache(ttl_sec=60.0, capacity=10)
    cache.set("k", "v")
    cache.clear()
    assert len(cache) == 0
    assert cache.get("k") is None


@pytest.mark.parametrize("ttl", [0.001, 1.0, 60.0])
def test_ttl_field_honored(ttl: float) -> None:
    cache: TTLCache[str] = TTLCache(ttl_sec=ttl, capacity=10)
    cache.set("k", "v", now=0.0)
    assert cache.get("k", now=ttl / 2.0) == "v"
    assert cache.get("k", now=ttl + 1.0) is None
