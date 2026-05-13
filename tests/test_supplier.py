"""Tests for NamespaceSupplierResolver."""

from dataclasses import dataclass, field
from typing import Any

from lumid_flowmesh_plugin.supplier import NamespaceSupplierResolver


@dataclass(frozen=True)
class FakeWorker:
    id: str = "w1"
    node_id: str = "n1"
    namespace: str = "flowmesh"
    cluster: str = "default"
    tags: list[str] = field(default_factory=list)
    env: dict[str, Any] = field(default_factory=dict)


def test_returns_namespace_when_set() -> None:
    resolver = NamespaceSupplierResolver()
    assert resolver.resolve(FakeWorker(namespace="vendor-a")) == "vendor-a"


def test_returns_none_when_namespace_empty() -> None:
    resolver = NamespaceSupplierResolver()
    assert resolver.resolve(FakeWorker(namespace="")) is None
