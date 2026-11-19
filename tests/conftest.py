"""Réglages communs des tests : jamais la vraie liste des clips à refaire (state/redo.json)."""
from __future__ import annotations

import pytest

from clipper import hunt


@pytest.fixture(autouse=True)
def _no_real_redo(tmp_path, monkeypatch):
    monkeypatch.setattr(hunt, "REDO", tmp_path / "redo-test.json")
