from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

from pydantic import SecretStr

from app.config import Settings
from app.core.models import (
    ActionMode,
    DuplicateMessageState,
    LLMDecision,
    ModerationAction,
    SpamDetectionResult,
    StopWordCheckResult,
)
from app.observability.logging import close_logger_handlers, configure_logging
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
    notification_target: str | None = None

    async def get_action_mode(self, *, default: ActionMode) -> ActionMode:
        return self.action_mode

    async def get_notification_target(self, *, chat_id: int) -> str | None:
        return self.notification_target


class FakeSpamDetectorService:
    async def detect(self, message_text: str) -> SpamDetectionResult:
        return _spam_result()


@dataclass
class FakeFloodSpamDetectorService:
    duplicate_result: SpamDetectionResult
    duplicate_calls: list[tuple[str, int]] = field(default_factory=list)
    detect_calls: list[str] = field(default_factory=list)

    async def detect(self, message_text: str) -> SpamDetectionResult:
        self.detect_calls.append(message_text)
        return SpamDetectionResult(is_spam=False, reason="no_stop_word")

    async def detect_duplicate_flood(
        self,
        message_text: str,
        *,
        duplicate_count: int,
    ) -> SpamDetectionResult:
        self.duplicate_calls.append((message_text, duplicate_count))
        return self.duplicate_result


@dataclass
class FakeDuplicateMessageRepository:
    state: DuplicateMessageState
    warning_digest: str | None = None
    marked_warning_digest: str | None = None
    cleared: bool = False
    warning_cleared: bool = False

    async def record_message(
        self,
        *,
        chat_id: int,
        user_id: int,
        message_id: int,
        message_text: str,
    ) -> DuplicateMessageState:
        return self.state

    async def get_warning_digest(self, *, chat_id: int, user_id: int) -> str | None:
        return self.warning_digest

    async def mark_warned(
        self,
        *,
        chat_id: int,
        user_id: int,
        digest: str,
    ) -> None:
        self.marked_warning_digest = digest

    async def clear(self, *, chat_id: int, user_id: int) -> None:
        self.cleared = True

    async def clear_warning(self, *, chat_id: int, user_id: int) -> None:
        self.warning_cleared = True


def _settings(*, action_mode: str, log_file: Path) -> Settings:
    return Settings(
        bot_token=SecretStr("123456:test-token"),
        redis_url="redis://redis:6379/0",
        verify_timeout_seconds=180,
        action_mode=action_mode,
        admin_username="admin_user",
        admin_id=None,
        llm_api_key=SecretStr("llm-secret"),
        llm_base_url="https://llm.example/v1",
        llm_model="test-model",
        llm_timeout_seconds=5,
        log_level="INFO",
        log_file=str(log_file),
    )


def _message(
    *,
    message_thread_id: int | None = None,
    message_id: int = 55,
    text: str = "казино прямо сейчас",
) -> SimpleNamespace:
    return SimpleNamespace(
        text=text,
        message_id=message_id,
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


def _duplicate_spam_result() -> SpamDetectionResult:
    return SpamDetectionResult(
        is_spam=True,
        reason="llm_duplicate_flood_spam",
        stop_word=StopWordCheckResult(matched=False),
        llm_decision=LLMDecision.SPAM,
        matched_term="duplicate_message",
    )


def _read_log(logger_name_log_file: Path) -> str:
    return logger_name_log_file.read_text(encoding="utf-8")


def test_delete_action_deletes_ban_unbans_without_blacklist_and_logs(
    tmp_path: Path,
) -> None:
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
        assert blacklist_repository.added == []

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


def test_notify_admin_action_sends_private_message_for_numeric_target(
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
                message=_message(message_thread_id=777),
                spam_result=_spam_result(),
                settings=_settings(action_mode="notify_admin", log_file=log_file),
                notification_target="1242888754",
                logger=logger,
            )
        finally:
            close_logger_handlers(logger)

        assert len(bot.sent_messages) == 1
        sent = bot.sent_messages[0]
        assert sent["chat_id"] == 1242888754
        assert "message_thread_id" not in sent
        assert "admin_id:1242888754" in str(sent["text"])

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
        assert blacklist_repository.added == []

    asyncio.run(run())


def test_duplicate_flood_deletes_duplicates_and_warns_user(tmp_path: Path) -> None:
    async def run() -> None:
        log_file = tmp_path / "spam.log"
        settings = _settings(action_mode="notify_admin", log_file=log_file)
        bot = FakeBot()
        duplicate_repository = FakeDuplicateMessageRepository(
            state=DuplicateMessageState(
                chat_id=-100123,
                user_id=42,
                digest="same-digest",
                normalized_text="hello",
                message_ids=(11, 12, 13),
            )
        )
        spam_detector = FakeFloodSpamDetectorService(
            duplicate_result=_duplicate_spam_result()
        )

        result = await handle_text_message(
            message=_message(
                message_thread_id=777,
                message_id=13,
                text="hello",
            ),
            spam_detector_service=spam_detector,
            blacklist_repository=FakeBlacklistRepository(),
            settings=settings,
            moderation_service=ModerationService(),
            runtime_settings_repository=FakeRuntimeSettingsRepository(
                action_mode=ActionMode.NOTIFY_ADMIN
            ),
            duplicate_message_repository=duplicate_repository,
            bot=bot,
        )

        assert result is not None
        assert result.moderation_action == ModerationAction.WARN_USER
        assert bot.deleted_messages == [
            {"chat_id": -100123, "message_id": 11},
            {"chat_id": -100123, "message_id": 12},
            {"chat_id": -100123, "message_id": 13},
        ]
        assert bot.sent_messages == [
            {
                "chat_id": -100123,
                "text": (
                    "⚠️ Обнаружены одинаковые сообщения подряд. "
                    "Повторный flood приведет к исключению из группы."
                ),
                "message_thread_id": 777,
            }
        ]
        assert bot.bans == []
        assert duplicate_repository.marked_warning_digest == "same-digest"
        assert duplicate_repository.cleared is True
        assert spam_detector.duplicate_calls == [("hello", 3)]
        assert spam_detector.detect_calls == []

    asyncio.run(run())


def test_duplicate_flood_repeated_after_warning_kicks_user(tmp_path: Path) -> None:
    async def run() -> None:
        log_file = tmp_path / "spam.log"
        settings = _settings(action_mode="notify_admin", log_file=log_file)
        bot = FakeBot()
        duplicate_repository = FakeDuplicateMessageRepository(
            state=DuplicateMessageState(
                chat_id=-100123,
                user_id=42,
                digest="same-digest",
                normalized_text="hello",
                message_ids=(14,),
            ),
            warning_digest="same-digest",
        )
        spam_detector = FakeFloodSpamDetectorService(
            duplicate_result=_duplicate_spam_result()
        )

        result = await handle_text_message(
            message=_message(message_id=14, text="hello"),
            spam_detector_service=spam_detector,
            blacklist_repository=FakeBlacklistRepository(),
            settings=settings,
            moderation_service=ModerationService(),
            runtime_settings_repository=FakeRuntimeSettingsRepository(
                action_mode=ActionMode.NOTIFY_ADMIN
            ),
            duplicate_message_repository=duplicate_repository,
            bot=bot,
        )

        assert result is not None
        assert result.moderation_action == ModerationAction.BAN_UNBAN
        assert result.reason == "duplicate_flood_repeated_after_warning"
        assert bot.deleted_messages == [{"chat_id": -100123, "message_id": 14}]
        assert bot.bans == [{"chat_id": -100123, "user_id": 42}]
        assert bot.unbans == [{"chat_id": -100123, "user_id": 42}]
        assert bot.sent_messages == []
        assert duplicate_repository.cleared is True
        assert duplicate_repository.warning_cleared is True
        assert spam_detector.duplicate_calls == []
        assert spam_detector.detect_calls == []

    asyncio.run(run())
