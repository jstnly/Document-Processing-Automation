"""Shared pytest fixtures."""

from pathlib import Path

import pytest

# Resolve once at import time — stable across all tests
PROJECT_ROOT = Path(__file__).parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def config_dir() -> Path:
    """The project's actual config directory."""
    return CONFIG_DIR


@pytest.fixture
def invoices_dir() -> Path:
    return FIXTURES_DIR / "invoices"
