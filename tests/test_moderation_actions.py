from __future__ import annotations

import asyncio
from pathlib import Path

from app.domain import (
    ModerationAction,
)
from app.observability.logging import close_logger_handlers, configure_logging
from app.usecase.moderation import ModerationService
from app.usecase.moderation.message import (
    SPAM_NOTIFICATION_MAX_LENGTH,
    build_spam_notification_text,
)
from tests.support.moderation import (
    FakeBot,
)
from tests.support.moderation import (
    make_message as _message,
)
from tests.support.moderation import (
    make_settings as _settings,
)
from tests.support.moderation import (
    read_log as _read_log,
)
from tests.support.moderation import (
    spam_result as _spam_result,
)


def test_delete_action_deletes_ban_unbans_and_logs(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        log_file = tmp_path / "spam.log"
        logger = configure_logging(_settings(action_mode="delete", log_file=log_file))
        bot = FakeBot()

        try:
            result = await ModerationService().delete_spam_message(
                bot=bot,
                message=_message(message_thread_id=777),
                spam_result=_spam_result(),
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


def test_spam_notification_text_is_truncated_to_telegram_safe_length() -> None:
    notification = build_spam_notification_text(
        admin_target_text="@admin",
        spammer="@spammer (42)",
        user_id=42,
        reason="llm_spam",
        message_reference="chat_id=-100123; message_id=55",
        message_text="x" * 5000,
    )

    assert len(notification) == SPAM_NOTIFICATION_MAX_LENGTH
    assert notification.endswith("[message truncated]")


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
