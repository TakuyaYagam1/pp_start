from __future__ import annotations

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.models import ActionMode


class Settings(BaseSettings):
    bot_token: SecretStr
    telegram_proxy_url: str | None = None
    redis_url: str
    verify_timeout_seconds: int = Field(gt=0)
    duplicate_message_window_seconds: int = Field(default=60, gt=0)
    duplicate_message_warn_threshold: int = Field(default=3, gt=1)
    duplicate_message_warning_ttl_seconds: int = Field(default=300, gt=0)
    action_mode: ActionMode
    admin_username: str | None = None
    admin_id: int | None = None
    llm_api_key: SecretStr
    llm_base_url: str
    llm_model: str
    llm_timeout_seconds: int = Field(gt=0)
    log_level: str = "INFO"
    log_file: str

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
    )

    def redacted_dump(self) -> dict[str, object]:
        data = self.model_dump()
        data["bot_token"] = "**********"
        data["llm_api_key"] = "**********"
        return data
