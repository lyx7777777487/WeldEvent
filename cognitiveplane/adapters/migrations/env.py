"""Alembic environment — Phase 1 scaffold.

Wired to the async engine stub so that `alembic upgrade head` can run
without a live database once Phase 2 replaces the dataclass models with
real SQLAlchemy `DeclarativeBase` ORM classes.

Until then this file is a structural placeholder.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = None


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (SQL dump, no DB connection)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode (connected to the database)."""
    connectable = config.attributes.get("connection")
    if connectable is None:
        raise RuntimeError(
            "Online migration requires a database connection. "
            "Phase 2 wires this to AsyncDatabaseEngine."
        )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
