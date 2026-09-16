#!/usr/bin/env python3
"""Smart database migration helper for llm-secure-gateway.

Handles all upgrade paths automatically:
  • Fresh database       → creates complete v0.5.1 schema
  • v0.3.x SQL dump      → upgrades in-place (adds columns, renames, new tables)
  • Old Alembic (001-004) → stamps to new baseline, then upgrades
  • Already current      → no-op

Usage:
    python scripts/migrate.py              # auto-detect + upgrade
    python scripts/migrate.py --status     # show current migration state
    python scripts/migrate.py --stamp      # stamp head (after manual create_all)
    python scripts/migrate.py --downgrade  # rollback one revision (DANGER)
"""

import argparse
import os
import sys

# Ensure project root is on path
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)

from alembic.config import Config
from alembic import command
from sqlalchemy import create_engine, inspect, text

_BASELINE_REV = "001_baseline_v051"
_OLD_REVISIONS = frozenset({"001_v01_to_v03", "002", "003", "004"})


def _get_alembic_config() -> Config:
    """Build Alembic Config pointing at alembic.ini in the project root."""
    ini_path = os.path.join(_PROJECT_ROOT, "alembic.ini")
    if not os.path.exists(ini_path):
        raise FileNotFoundError(f"alembic.ini not found at {ini_path}")

    # Build sync database URL from env
    db_url = os.getenv(
        "DATABASE_URL",
        "postgresql+asyncpg://gateway:password@postgres:5432/gateway_db",
    )
    sync_url = db_url.replace("+asyncpg", "+psycopg2")

    cfg = Config(ini_path)
    cfg.set_main_option("sqlalchemy.url", sync_url)
    return cfg


def _get_sync_engine():
    db_url = os.getenv(
        "DATABASE_URL",
        "postgresql+asyncpg://gateway:password@postgres:5432/gateway_db",
    )
    sync_url = db_url.replace("+asyncpg", "+psycopg2")
    return create_engine(sync_url)


def _get_current_revision(engine) -> str | None:
    """Return the current alembic_version revision, or None."""
    with engine.connect() as conn:
        insp = inspect(conn)
        if "alembic_version" not in insp.get_table_names():
            return None
        result = conn.execute(text("SELECT version_num FROM alembic_version"))
        row = result.fetchone()
        return row[0] if row else None


def _detect_state(engine) -> str:
    """Detect the database state:
    - 'empty'   : no application tables exist
    - 'legacy'  : tables exist but no alembic_version (v0.3.x dump)
    - 'old_alembic' : alembic_version contains an old revision (001-004)
    - 'current' : alembic_version matches baseline
    - 'ahead'   : alembic_version is newer (future migrations)
    """
    with engine.connect() as conn:
        insp = inspect(conn)
        tables = insp.get_table_names()

        has_app_tables = any(t in tables for t in ["llmbackend", "apikey", "owner", "user"])
        has_alembic = "alembic_version" in tables

        if not has_app_tables and not has_alembic:
            return "empty"

        if has_app_tables and not has_alembic:
            return "legacy"

        revision = _get_current_revision(engine)
        if revision in _OLD_REVISIONS:
            return "old_alembic"
        if revision == _BASELINE_REV:
            return "current"
        return "ahead"


def cmd_status():
    """Show the current database migration state."""
    engine = _get_sync_engine()
    state = _detect_state(engine)
    revision = _get_current_revision(engine)

    print(f"Database state : {state}")
    print(f"Alembic revision: {revision or '(none)'}")
    print(f"Target baseline : {_BASELINE_REV}")

    if state == "empty":
        print("\n→ Run 'python scripts/migrate.py' to create the schema from scratch.")
    elif state == "legacy":
        print("\n→ Run 'python scripts/migrate.py' to upgrade the legacy v0.3.x schema.")
    elif state == "old_alembic":
        print(f"\n→ Run 'python scripts/migrate.py' to upgrade from old revision '{revision}'.")
    elif state == "current":
        print("\n✓ Database is up to date.")
    else:
        print(f"\n✓ Database is ahead of baseline (revision: {revision}).")

    engine.dispose()


def cmd_upgrade():
    """Auto-detect state and upgrade to head."""
    engine = _get_sync_engine()
    state = _detect_state(engine)
    revision = _get_current_revision(engine)

    print(f"Detected state: {state} (revision: {revision or 'none'})")

    cfg = _get_alembic_config()

    if state == "old_alembic":
        # Stamp to baseline so Alembic doesn't look for deleted revisions
        print(f"Stamping old revision '{revision}' → '{_BASELINE_REV}'")
        with engine.connect() as conn:
            conn.execute(
                text("UPDATE alembic_version SET version_num = :rev"),
                {"rev": _BASELINE_REV},
            )
            conn.commit()
        print("✓ Stamped. Running upgrade to head...")

    elif state == "current":
        print("✓ Already at baseline. Checking for newer migrations...")

    # Run alembic upgrade head (idempotent for all states)
    command.upgrade(cfg, "head")
    print("✓ Database schema is up to date.")

    engine.dispose()


def cmd_stamp():
    """Stamp the current database as 'head' (use after manual create_all)."""
    cfg = _get_alembic_config()
    command.stamp(cfg, "head")
    print(f"✓ Stamped alembic_version to head.")


def cmd_downgrade():
    """Downgrade one revision (DANGER — drops tables)."""
    response = input("⚠ This will rollback the last migration. Continue? [y/N] ")
    if response.lower() != "y":
        print("Aborted.")
        return
    cfg = _get_alembic_config()
    command.downgrade(cfg, "-1")
    print("✓ Downgraded one revision.")


def main():
    parser = argparse.ArgumentParser(description="LLM Gateway database migration tool")
    parser.add_argument("--status", action="store_true", help="Show current migration state")
    parser.add_argument("--stamp", action="store_true", help="Stamp head (after manual create_all)")
    parser.add_argument("--downgrade", action="store_true", help="Rollback one revision (DANGER)")
    args = parser.parse_args()

    if args.status:
        cmd_status()
    elif args.stamp:
        cmd_stamp()
    elif args.downgrade:
        cmd_downgrade()
    else:
        cmd_upgrade()


if __name__ == "__main__":
    main()
