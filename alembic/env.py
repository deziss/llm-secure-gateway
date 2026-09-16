"""Alembic environment — supports both async (online) and offline modes.

Reads DATABASE_URL from the environment (default: asyncpg PostgreSQL).
Automatically converts asyncpg → psycopg2 for the sync Alembic runtime.
"""

import asyncio
import os
import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from sqlalchemy.ext.asyncio import AsyncEngine

from alembic import context

from sqlmodel import SQLModel

# Ensure project src is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from src.llm_gateway.models import *  # noqa: F401,F403
from src.llm_gateway.auth.models import *  # noqa: F401,F403

config = context.config

# Build database URL from env — convert async driver to sync for Alembic
database_url = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://gateway:password@postgres:5432/gateway_db",
)
# Alembic runs synchronously; swap the driver
sync_url = database_url.replace("+asyncpg", "+psycopg2")
config.set_main_option("sqlalchemy.url", sync_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = SQLModel.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (generates SQL without connecting)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode (connects to the database)."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        future=True,
    )

    with connectable.connect() as connection:
        do_run_migrations(connection)

    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
