"""Database engine with optional PgBouncer support.

Set PGBOUNCER_URL to route through PgBouncer (transaction-mode pooling).
If PgBouncer is unreachable at startup, falls back to direct DATABASE_URL.
The app always starts — PgBouncer is optional infrastructure.

Schema migrations are handled by the ``migrate`` docker-compose service
(runs ``scripts/migrate.py`` once before the gateway starts).
For SQLite tests, ``init_db()`` falls back to ``create_all``.
"""

import logging
import os

from sqlmodel import SQLModel
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

logger = logging.getLogger(__name__)

# Import User model to register with SQLModel
from .auth.models import User  # noqa: F401
from .models import LLMBackend, LLMBot, LLMBotMessage, LLMBotResponse, FallbackChain, ModelAlias, SpendRecord, CachedResponse  # noqa: F401


_DIRECT_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://gateway:password@postgres:5432/gateway_db")
_PGBOUNCER_URL = os.getenv("PGBOUNCER_URL", "")
_SQL_ECHO = os.getenv("SQL_ECHO", "false").lower() == "true"


def _build_engine():
    """Try PgBouncer first; fall back to direct connection."""
    # PgBouncer in transaction mode requires NullPool (PgBouncer owns the pool)
    if _PGBOUNCER_URL:
        try:
            from sqlalchemy.pool import NullPool

            eng = create_async_engine(
                _PGBOUNCER_URL,
                echo=_SQL_ECHO,
                future=True,
                poolclass=NullPool,
            )
            logger.info("Using PgBouncer at %s", _PGBOUNCER_URL.split("@")[-1])
            return eng
        except Exception as exc:
            logger.warning("PgBouncer init failed (%s), falling back to direct DB", exc)

    logger.info("Using direct database connection")

    # SQLite (used in tests) doesn't support pool_size/max_overflow
    extra = {}
    if "sqlite" not in _DIRECT_URL:
        extra = dict(
            pool_size=10,
            max_overflow=20,
            pool_timeout=5,
            pool_pre_ping=True,
        )

    return create_async_engine(
        _DIRECT_URL,
        echo=_SQL_ECHO,
        future=True,
        **extra,
    )


engine = _build_engine()

# Single sessionmaker instance — never create one inside a per-request function
_async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db() -> None:
    """Initialize the database.

    For PostgreSQL: schema is already set up by the ``migrate`` service
    (``scripts/migrate.py``).  This call only ensures tables exist as a
    safety net (``create_all`` is idempotent — it won't drop/recreate
    existing tables).

    For SQLite (tests): creates all tables from SQLModel metadata.
    """
    if "sqlite" in _DIRECT_URL:
        # Tests use in-memory SQLite — always create tables
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
        return

    # For PostgreSQL, create_all is a lightweight safety net.
    # It only creates tables that don't already exist (idempotent).
    # The real migration work is done by scripts/migrate.py.
    try:
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
    except Exception as exc:
        logger.warning("create_all safety check failed (expected if migrate service ran): %s", exc)


async def get_session() -> AsyncSession:
    async with _async_session() as session:
        yield session


def get_session_context():
    """Return an async session context manager."""
    return _async_session()
