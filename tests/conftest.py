"""Test-wide setup. Must set env vars BEFORE any app module is imported."""
import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-not-used")

# Make the project root importable from anywhere.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest


@pytest.fixture
def anyio_backend():
    return "asyncio"
