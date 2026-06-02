from __future__ import annotations

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.models import ActionMode


class Settings(BaseSettings):
    bot_token: SecretStr
    redis_url: str
    verify_timeout_seconds: int = Field(gt=0)
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

    @model_validator(mode="after")
    def require_admin_target_for_notifications(self) -> Settings:
        if self.action_mode == ActionMode.NOTIFY_ADMIN and not (
            self.admin_username or self.admin_id is not None
        ):
            raise ValueError(
                "ADMIN_USERNAME or ADMIN_ID is required when ACTION_MODE=notify_admin"
            )
        return self

    def redacted_dump(self) -> dict[str, object]:
        data = self.model_dump()
        data["bot_token"] = "**********"
        data["llm_api_key"] = "**********"
        return data
