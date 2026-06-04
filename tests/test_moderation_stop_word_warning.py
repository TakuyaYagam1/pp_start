from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.bot.controller.v1.moderation import handle_text_message
from app.domain import (
    ActionMode,
    ModerationAction,
)
from app.usecase.moderation import AutoDeleteTaskRegistry, ModerationService
from tests.support.moderation import (
    AutoDeleteFakeBot,
    FailingAutoDeleteMessageRepository,
    FailingSendMessageBot,
    FakeBot,
    FakeRuntimeSettingsRepository,
    FakeSpamDetectorService,
    FakeStopWordWarningRepository,
)
from tests.support.moderation import (
    make_message as _message,
)
from tests.support.moderation import (
    make_settings as _settings,
)
from tests.support.moderation import (
    spam_result as _spam_result,
)


def test_first_stop_word_spam_warns_without_kick(tmp_path: Path) -> None:
    async def run() -> None:
        log_file = tmp_path / "spam.log"
        settings = _settings(action_mode="delete", log_file=log_file)
        bot = FakeBot()
        warning_repository = FakeStopWordWarningRepository(mark_result=True)

        result = await handle_text_message(
            message=_message(message_thread_id=777),
            spam_detector_service=FakeSpamDetectorService(),
            settings=settings,
            moderation_service=ModerationService(),
            runtime_settings_repository=FakeRuntimeSettingsRepository(
                action_mode=ActionMode.DELETE
            ),
            duplicate_message_repository=None,
            stop_word_warning_repository=warning_repository,
            bot=bot,
        )

        assert result is not None
        assert result.moderation_action == ModerationAction.WARN_USER
        assert warning_repository.marked_term == "казино"
        assert bot.deleted_messages == [{"chat_id": -100123, "message_id": 55}]
        assert bot.bans == []
        assert bot.unbans == []
        assert len(bot.sent_messages) == 1
        sent = bot.sent_messages[0]
        assert sent["chat_id"] == -100123
        assert sent["message_thread_id"] == 777
        assert "казино" in str(sent["text"])
        assert "В следующий раз" in str(sent["text"])

    asyncio.run(run())


def test_repeated_stop_word_spam_after_warning_uses_delete_action(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        log_file = tmp_path / "spam.log"
        settings = _settings(action_mode="delete", log_file=log_file)
        bot = FakeBot()

        result = await handle_text_message(
            message=_message(message_thread_id=777),
            spam_detector_service=FakeSpamDetectorService(),
            settings=settings,
            moderation_service=ModerationService(),
            runtime_settings_repository=FakeRuntimeSettingsRepository(
                action_mode=ActionMode.DELETE
            ),
            stop_word_warning_repository=FakeStopWordWarningRepository(
                mark_result=False,
                marked_term="казино",
            ),
            bot=bot,
        )

        assert result is not None
        assert result.moderation_action == ModerationAction.DELETE_MESSAGE
        assert bot.deleted_messages == [{"chat_id": -100123, "message_id": 55}]
        assert bot.bans == [{"chat_id": -100123, "user_id": 42}]
        assert bot.unbans == [{"chat_id": -100123, "user_id": 42}]
        assert bot.sent_messages == []

    asyncio.run(run())


def test_notify_admin_mode_does_not_use_stop_word_warning(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        log_file = tmp_path / "spam.log"
        settings = _settings(action_mode="delete", log_file=log_file)
        bot = FakeBot()
        warning_repository = FakeStopWordWarningRepository(mark_result=True)

        result = await handle_text_message(
            message=_message(message_thread_id=777),
            spam_detector_service=FakeSpamDetectorService(),
            settings=settings,
            moderation_service=ModerationService(),
            runtime_settings_repository=FakeRuntimeSettingsRepository(
                action_mode=ActionMode.NOTIFY_ADMIN
            ),
            stop_word_warning_repository=warning_repository,
            bot=bot,
        )

        assert result is not None
        assert result.moderation_action == ModerationAction.NOTIFY_ADMIN
        assert warning_repository.marked_term is None
        assert bot.deleted_messages == []
        assert bot.bans == []
        assert bot.unbans == []
        assert len(bot.sent_messages) == 1
        assert "@admin_user" in str(bot.sent_messages[0]["text"])

    asyncio.run(run())


def test_stop_word_warning_message_is_deleted_after_ttl() -> None:
    async def run() -> None:
        bot = AutoDeleteFakeBot()

        result = await ModerationService().warn_stop_word_spam(
            bot=bot,
            message=_message(message_thread_id=777),
            spam_result=_spam_result(),
            warning_message_ttl_seconds=0,
        )
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        assert result.moderation_action == ModerationAction.WARN_USER
        assert bot.sent_messages == [
            {
                "chat_id": -100123,
                "text": (
                    "⚠️ Слово или фраза «казино» запрещены в чате. "
                    "В следующий раз вы будете исключены из группы."
                ),
                "message_thread_id": 777,
            }
        ]
        assert bot.deleted_messages == [
            {"chat_id": -100123, "message_id": 55},
            {"chat_id": -100123, "message_id": 999},
        ]

    asyncio.run(run())


def test_stop_word_warning_auto_delete_falls_back_when_redis_schedule_fails() -> None:
    async def run() -> None:
        bot = AutoDeleteFakeBot()

        result = await ModerationService().warn_stop_word_spam(
            bot=bot,
            message=_message(message_thread_id=777),
            spam_result=_spam_result(),
            warning_message_ttl_seconds=0,
            auto_delete_message_repository=FailingAutoDeleteMessageRepository(),
            auto_delete_task_registry=AutoDeleteTaskRegistry(),
        )
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        assert result.moderation_action == ModerationAction.WARN_USER
        assert bot.deleted_messages == [
            {"chat_id": -100123, "message_id": 55},
            {"chat_id": -100123, "message_id": 999},
        ]

    asyncio.run(run())


def test_stop_word_warning_state_rolls_back_when_warning_send_fails(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        log_file = tmp_path / "spam.log"
        settings = _settings(action_mode="delete", log_file=log_file)
        warning_repository = FakeStopWordWarningRepository(mark_result=True)

        with pytest.raises(RuntimeError, match="send failed"):
            await handle_text_message(
                message=_message(message_thread_id=777),
                spam_detector_service=FakeSpamDetectorService(),
                settings=settings,
                moderation_service=ModerationService(),
                runtime_settings_repository=FakeRuntimeSettingsRepository(
                    action_mode=ActionMode.DELETE
                ),
                stop_word_warning_repository=warning_repository,
                bot=FailingSendMessageBot(),
            )

        assert warning_repository.marked_term is None

    asyncio.run(run())
