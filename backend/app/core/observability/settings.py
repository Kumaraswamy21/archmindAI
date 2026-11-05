"""Environment configuration for optional Langfuse export."""

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class ObservabilitySettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    langfuse_enabled: bool = False
    langfuse_public_key: SecretStr | None = None
    langfuse_secret_key: SecretStr | None = None
    langfuse_host: str = Field(default="https://cloud.langfuse.com", min_length=8, max_length=200)
    langfuse_timeout_seconds: float = Field(default=2.0, gt=0, le=10)

    def export_enabled(self) -> bool:
        return bool(
            self.langfuse_enabled
            and self.langfuse_public_key is not None
            and self.langfuse_secret_key is not None
            and self.langfuse_public_key.get_secret_value().strip()
            and self.langfuse_secret_key.get_secret_value().strip()
        )
