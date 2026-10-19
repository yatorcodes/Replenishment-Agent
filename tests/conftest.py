"""Shared pytest fixtures."""

from __future__ import annotations

import pytest

from app.db import set_db_path
from seed import seed


@pytest.fixture()
def db_path(tmp_path, monkeypatch):
    path = str(tmp_path / "test_store.db")
    set_db_path(path)
    seed(path)
    yield path
    set_db_path("data/store.db")
