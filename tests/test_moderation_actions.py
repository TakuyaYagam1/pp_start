from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

from pydantic import SecretStr

from app.config import Settings
from app.core.models import (
    ActionMode,
    LLMDecision,
    ModerationAction,
    SpamDetectionResult,
    StopWordCheckResult,
)
from app.logging import close_logger_handlers, configure_logging
from app.core.services.moderation import ModerationService
from app.tg_bot.handlers.moderation import handle_text_message


@dataclass
class FakeBot:
    sent_messages: list[dict[str, object]] = field(default_factory=list)
    deleted_messages: list[dict[str, int]] = field(default_factory=list)
    bans: list[dict[str, int]] = field(default_factory=list)
    unbans: list[dict[str, int]] = field(default_factory=list)

    async def send_message(self, **kwargs: object) -> None:
        self.sent_messages.append(kwargs)

    async def delete_message(self, **kwargs: int) -> None:
        self.deleted_messages.append(kwargs)

    async def ban_chat_member(self, **kwargs: int) -> None:
        self.bans.append(kwargs)

    async def unban_chat_member(self, **kwargs: int) -> None:
        self.unbans.append(kwargs)


@dataclass
class FakeBlacklistRepository:
    added: list[tuple[int, int]] = field(default_factory=list)

    async def add(self, *, chat_id: int, user_id: int) -> None:
        self.added.append((chat_id, user_id))

    async def contains(self, *, chat_id: int, user_id: int) -> bool:
        return False


@dataclass
class FakeRuntimeSettingsRepository:
    action_mode: ActionMode

    async def get_action_mode(self, *, default: ActionMode) -> ActionMode:
        return self.action_mode


class FakeSpamDetectorService:
    async def detect(self, message_text: str) -> SpamDetectionResult:
        return _spam_result()


def _settings(*, action_mode: str, log_file: Path) -> Settings:
    return Settings(
        bot_token=SecretStr("123456:test-token"),
        redis_url="redis://redis:6379/0",
        verify_timeout_seconds=180,
        action_mode=action_mode,
        admin_username="admin_user",
        llm_api_key=SecretStr("llm-secret"),
        llm_base_url="https://llm.example/v1",
        llm_model="test-model",
        llm_timeout_seconds=5,
        log_level="INFO",
        log_file=str(log_file),
    )


def _message(*, message_thread_id: int | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        text="казино прямо сейчас",
        message_id=55,
        message_thread_id=message_thread_id,
        chat=SimpleNamespace(id=-100123, type="supergroup", username="public_group"),
        from_user=SimpleNamespace(id=42, is_bot=False, username="spammer"),
    )


def _spam_result() -> SpamDetectionResult:
    return SpamDetectionResult(
        is_spam=True,
        reason="llm_spam",
        stop_word=StopWordCheckResult(matched=True, matched_term="казино"),
        llm_decision=LLMDecision.SPAM,
        matched_term="казино",
    )


def _read_log(logger_name_log_file: Path) -> str:
    return logger_name_log_file.read_text(encoding="utf-8")


def test_delete_action_deletes_ban_unbans_blacklists_and_logs(tmp_path: Path) -> None:
    async def run() -> None:
        log_file = tmp_path / "spam.log"
        logger = configure_logging(_settings(action_mode="delete", log_file=log_file))
        bot = FakeBot()
        blacklist_repository = FakeBlacklistRepository()

        try:
            result = await ModerationService().delete_spam_message(
                bot=bot,
                message=_message(message_thread_id=777),
                spam_result=_spam_result(),
                blacklist_repository=blacklist_repository,
                logger=logger,
            )
        finally:
            for handler in logger.handlers:
                handler.flush()
            close_logger_handlers(logger)

        assert result.moderation_action == ModerationAction.DELETE_MESSAGE
        assert bot.deleted_messages == [{"chat_id": -100123, "message_id": 55}]
        assert bot.bans == [{"chat_id": -100123, "user_id": 42}]
        assert bot.unbans == [{"chat_id": -100123, "user_id": 42}]
        assert bot.sent_messages == []
        assert blacklist_repository.added == [(-100123, 42)]

        log_text = _read_log(log_file)
        assert "spam_detected" in log_text
        assert "action=delete_message" in log_text
        assert "chat_id=-100123" in log_text
        assert "user_id=42" in log_text
        assert "казино прямо сейчас" in log_text

    asyncio.run(run())


def test_notify_admin_action_sends_topic_message_and_logs(tmp_path: Path) -> None:
    async def run() -> None:
        log_file = tmp_path / "spam.log"
        logger = configure_logging(
            _settings(action_mode="notify_admin", log_file=log_file)
        )
        bot = FakeBot()

        try:
            result = await ModerationService().notify_admin_about_spam(
                bot=bot,
                message=_message(message_thread_id=777),
                spam_result=_spam_result(),
                settings=_settings(action_mode="notify_admin", log_file=log_file),
                logger=logger,
            )
        finally:
            for handler in logger.handlers:
                handler.flush()
            close_logger_handlers(logger)

        assert result.moderation_action == ModerationAction.NOTIFY_ADMIN
        assert bot.deleted_messages == []
        assert bot.bans == []
        assert bot.unbans == []
        assert len(bot.sent_messages) == 1

        sent = bot.sent_messages[0]
        assert sent["chat_id"] == -100123
        assert sent["message_thread_id"] == 777
        assert "@admin_user" in str(sent["text"])
        assert "@spammer" in str(sent["text"])
        assert "https://t.me/public_group/55" in str(sent["text"])
        assert "казино прямо сейчас" in str(sent["text"])

        log_text = _read_log(log_file)
        assert "spam_detected" in log_text
        assert "action=notify_admin" in log_text
        assert "chat_id=-100123" in log_text
        assert "user_id=42" in log_text
        assert "казино прямо сейчас" in log_text

    asyncio.run(run())


def test_notify_admin_action_without_topic_omits_message_thread_id(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        log_file = tmp_path / "spam.log"
        logger = configure_logging(
            _settings(action_mode="notify_admin", log_file=log_file)
        )
        bot = FakeBot()

        try:
            await ModerationService().notify_admin_about_spam(
                bot=bot,
                message=_message(message_thread_id=None),
                spam_result=_spam_result(),
                settings=_settings(action_mode="notify_admin", log_file=log_file),
                logger=logger,
            )
        finally:
            close_logger_handlers(logger)

        assert len(bot.sent_messages) == 1
        assert "message_thread_id" not in bot.sent_messages[0]

    asyncio.run(run())


def test_text_moderation_uses_runtime_action_mode_over_env_default(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        log_file = tmp_path / "spam.log"
        settings = _settings(action_mode="notify_admin", log_file=log_file)
        logger = configure_logging(settings)
        bot = FakeBot()
        blacklist_repository = FakeBlacklistRepository()

        try:
            result = await handle_text_message(
                message=_message(message_thread_id=777),
                spam_detector_service=FakeSpamDetectorService(),
                blacklist_repository=blacklist_repository,
                settings=settings,
                moderation_service=ModerationService(),
                runtime_settings_repository=FakeRuntimeSettingsRepository(
                    action_mode=ActionMode.DELETE
                ),
                bot=bot,
            )
        finally:
            close_logger_handlers(logger)

        assert result is not None
        assert result.moderation_action == ModerationAction.DELETE_MESSAGE
        assert bot.deleted_messages == [{"chat_id": -100123, "message_id": 55}]
        assert bot.sent_messages == []
        assert blacklist_repository.added == [(-100123, 42)]

    asyncio.run(run())
