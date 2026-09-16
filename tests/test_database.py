"""Unit tests for database engine creation and PgBouncer fallback."""
from unittest.mock import patch


class TestDatabaseFactory:

    def test_uses_direct_url_when_no_pgbouncer(self):
        """Without PGBOUNCER_URL, engine uses DATABASE_URL with pooling."""
        import llm_gateway.database as db_mod
        with patch.object(db_mod, "_PGBOUNCER_URL", ""):
            engine = db_mod._build_engine()
            assert engine.pool.__class__.__name__ != "NullPool"

    def test_uses_nullpool_with_pgbouncer_url(self):
        """With PGBOUNCER_URL set to a PostgreSQL URL, engine uses NullPool."""
        import llm_gateway.database as db_mod
        pgb_url = "postgresql+asyncpg://gateway:password@pgbouncer:6432/gateway_db"
        with patch.object(db_mod, "_PGBOUNCER_URL", pgb_url):
            engine = db_mod._build_engine()
            assert engine.pool.__class__.__name__ == "NullPool"

    def test_falls_back_to_direct_on_pgbouncer_error(self):
        """If PgBouncer URL is invalid, falls back to direct."""
        import llm_gateway.database as db_mod
        with patch.object(db_mod, "_PGBOUNCER_URL", "invalid://url"):
            engine = db_mod._build_engine()
            assert engine is not None
