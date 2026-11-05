"""Deployment-mode validation without reading or logging secret values."""

import json
import logging
import os
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.db.config import DatabaseSettings


class RuntimeSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    archmind_env: Literal["development", "test", "production"] = "development"
    archmind_json_body_limit_bytes: int = Field(
        default=1_048_576, ge=1024, le=10_485_760
    )
    database_url: SecretStr | None = None
    gemini_api_key: SecretStr | None = None

    def validate_startup(self, origins: list[str]) -> None:
        if self.archmind_env != "production":
            return
        if (
            self.database_url is None
            or not self.database_url.get_secret_value().strip()
        ):
            raise ValueError("Production requires DATABASE_URL.")
        DatabaseSettings().sqlalchemy_url()
        if (
            self.gemini_api_key is None
            or not self.gemini_api_key.get_secret_value().strip()
        ):
            raise ValueError("Production requires GEMINI_API_KEY.")
        if not origins or any(urlsplit(origin).scheme != "https" for origin in origins):
            raise ValueError("Production requires explicit HTTPS frontend origins.")
        upload = os.environ.get("ARCHMIND_UPLOAD_ROOT", "")
        if (
            not upload
            or not Path(upload).is_absolute()
            or not Path(upload).is_dir()
            or not os.access(upload, os.W_OK)
        ):
            raise ValueError(
                "Production requires an existing absolute ARCHMIND_UPLOAD_ROOT."
            )
        if os.environ.get("LANGFUSE_ENABLED", "false").lower() in {"true", "1", "yes"}:
            if not os.environ.get("LANGFUSE_PUBLIC_KEY") or not os.environ.get(
                "LANGFUSE_SECRET_KEY"
            ):
                raise ValueError("Enabled Langfuse requires both credentials.")
        # Phase 7.3 was deferred. Never expose source or AI routes on a public host.
        raise RuntimeError(
            "Production deployment is blocked until authentication and authorization are implemented."
        )


def configured_origins() -> list[str]:
    try:
        origins = json.loads(os.environ.get("ARCHMIND_FRONTEND_ORIGINS", "[]"))
    except json.JSONDecodeError as exc:
        raise ValueError(
            "ARCHMIND_FRONTEND_ORIGINS must be a JSON list of origins."
        ) from exc
    if not isinstance(origins, list) or any(
        not isinstance(origin, str) for origin in origins
    ):
        raise ValueError("ARCHMIND_FRONTEND_ORIGINS must be a JSON list of origins.")
    for origin in origins:
        parsed = urlsplit(origin)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username
            or parsed.password
            or parsed.path
            or parsed.query
            or parsed.fragment
            or "*" in origin
        ):
            raise ValueError("ARCHMIND_FRONTEND_ORIGINS contains an invalid origin.")
    return origins


def request_logger() -> logging.Logger:
    logger = logging.getLogger("archmind.requests")
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger
