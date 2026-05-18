"""Test DATABASE_URL normalization for Postgres compat."""
from __future__ import annotations


def test_postgres_short_form():
    """Railway gives postgres://user:pass@host/db — must become postgresql+asyncpg://"""
    import os
    os.environ["DATABASE_URL"] = "postgres://u:p@host:5432/db"

    # Force a fresh load of Settings.
    from importlib import reload
    import config
    reload(config)

    assert config.settings.database_url == "postgresql+asyncpg://u:p@host:5432/db"


def test_postgres_full_form():
    """postgresql:// (without driver) must also gain +asyncpg."""
    import os
    os.environ["DATABASE_URL"] = "postgresql://u:p@host:5432/db"

    from importlib import reload
    import config
    reload(config)

    assert config.settings.database_url == "postgresql+asyncpg://u:p@host:5432/db"


def test_postgres_with_driver_unchanged():
    """If driver already specified, leave as-is."""
    import os
    os.environ["DATABASE_URL"] = "postgresql+asyncpg://u:p@host:5432/db"

    from importlib import reload
    import config
    reload(config)

    assert config.settings.database_url == "postgresql+asyncpg://u:p@host:5432/db"


def test_sqlite_unchanged():
    """SQLite URL must pass through."""
    import os
    os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"

    from importlib import reload
    import config
    reload(config)

    assert config.settings.database_url == "sqlite+aiosqlite:///:memory:"


def test_effective_port_railway():
    """Railway sets PORT — must take precedence."""
    import os
    os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
    os.environ["PORT"] = "12345"
    os.environ["WEB_PORT"] = "8000"

    from importlib import reload
    import config
    reload(config)

    assert config.settings.effective_port == 12345

    # Cleanup so other tests aren't affected
    del os.environ["PORT"]


def test_effective_port_fallback():
    """Without PORT, fall back to WEB_PORT."""
    import os
    os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
    os.environ.pop("PORT", None)
    os.environ["WEB_PORT"] = "9999"

    from importlib import reload
    import config
    reload(config)

    assert config.settings.effective_port == 9999
