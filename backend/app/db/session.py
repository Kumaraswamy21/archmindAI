"""Bounded SQLAlchemy connections for a remote PostgreSQL service."""

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.config import DatabaseSettings


def create_database_engine(
    settings: DatabaseSettings, *, register_vectors: bool = False
) -> Engine:
    engine = create_engine(
        settings.sqlalchemy_url(),
        connect_args={
            "sslmode": settings.database_ssl_mode,
            "connect_timeout": 5,
        },
        pool_size=5,
        max_overflow=2,
        pool_timeout=5,
        pool_recycle=1800,
        pool_pre_ping=True,
        echo=False,
    )
    if register_vectors:
        from pgvector.psycopg import register_vector

        @event.listens_for(engine, "connect")
        def _register_vector(dbapi_connection, _connection_record) -> None:
            register_vector(dbapi_connection)
    return engine


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)
