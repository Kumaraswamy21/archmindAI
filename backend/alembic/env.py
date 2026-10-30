"""Explicit cloud PostgreSQL migrations; never run at API startup."""

from alembic import context

from app.db.config import DatabaseSettings
from app.db.models import Base
from app.db.session import create_database_engine

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    settings = DatabaseSettings()
    context.configure(
        url=settings.sqlalchemy_url().render_as_string(hide_password=False),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_database_engine(DatabaseSettings())
    try:
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=target_metadata)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
