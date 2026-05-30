"""Pytest fixtures shared by every test module."""

import logging

import pytest

from lumid_flowmesh_plugin._core import TTLCache


@pytest.fixture
def logger() -> logging.Logger:
    return logging.getLogger("lumid_flowmesh_plugin.tests")


@pytest.fixture
def email_cache() -> TTLCache[str]:
    return TTLCache[str](ttl_sec=3600.0, capacity=100)
