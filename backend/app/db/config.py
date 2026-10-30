"""Validated, secret-safe PostgreSQL connection settings."""

from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL, make_url


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    database_url: SecretStr
    database_ssl_mode: Literal["require", "verify-ca", "verify-full"] = "require"

    def sqlalchemy_url(self) -> URL:
        """Validate without placing secret input in a Pydantic error message."""
        try:
            url = make_url(self.database_url.get_secret_value())
        except Exception:
            raise ValueError("DATABASE_URL must be a valid PostgreSQL URL.") from None
        if url.drivername not in {"postgresql", "postgresql+psycopg"}:
            raise ValueError("DATABASE_URL must use PostgreSQL and psycopg.")
        if not url.host or url.host.lower().rstrip(".") in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("DATABASE_URL must identify a remote PostgreSQL host.")
        if url.query:
            raise ValueError("DATABASE_URL query parameters are not supported; set SSL mode separately.")
        return url.set(drivername="postgresql+psycopg")
