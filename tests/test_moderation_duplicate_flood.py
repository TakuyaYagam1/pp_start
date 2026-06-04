from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.bot.controller.v1.moderation import handle_text_message
from app.domain import (
    ActionMode,
    DuplicateMessageState,
    LLMDecision,
    ModerationAction,
    SpamDetectionResult,
    StopWordCheckResult,
)
from app.usecase.moderation import ModerationService
from tests.support.moderation import (
    AutoDeleteFakeBot,
    FailingSendMessageBot,
    FakeBot,
    FakeDuplicateMessageRepository,
    FakeFloodSpamDetectorService,
    FakeRuntimeSettingsRepository,
)
from tests.support.moderation import (
    make_message as _message,
)
from tests.support.moderation import (
    make_settings as _settings,
)


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
                content_key="sticker:same-sticker",
                message_ids=(11, 12, 13),
            )
        )
        spam_detector = FakeFloodSpamDetectorService()

        result = await handle_text_message(
            message=_message(
                message_thread_id=777,
                message_id=13,
                text=None,
                sticker_unique_id="same-sticker",
            ),
            spam_detector_service=spam_detector,
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
        assert spam_detector.detect_calls == []

    asyncio.run(run())


def test_duplicate_flood_warning_message_is_deleted_after_ttl() -> None:
    async def run() -> None:
        bot = AutoDeleteFakeBot()

        result = await ModerationService().warn_duplicate_flood(
            bot=bot,
            message=_message(message_thread_id=777),
            spam_result=SpamDetectionResult(
                is_spam=True,
                reason="duplicate_flood",
                stop_word=StopWordCheckResult(matched=False),
                llm_decision=LLMDecision.UNKNOWN,
                matched_term="duplicate_content",
            ),
            duplicate_message_ids=(11, 12, 13),
            warning_message_ttl_seconds=0,
        )
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        assert result.moderation_action == ModerationAction.WARN_USER
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
        assert bot.deleted_messages[-1] == {"chat_id": -100123, "message_id": 999}

    asyncio.run(run())


def test_duplicate_flood_warning_state_rolls_back_when_warning_send_fails(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        log_file = tmp_path / "spam.log"
        settings = _settings(action_mode="notify_admin", log_file=log_file)
        duplicate_repository = FakeDuplicateMessageRepository(
            state=DuplicateMessageState(
                chat_id=-100123,
                user_id=42,
                digest="same-digest",
                content_key="sticker:same-sticker",
                message_ids=(11, 12, 13),
            )
        )

        with pytest.raises(RuntimeError, match="send failed"):
            await handle_text_message(
                message=_message(
                    message_thread_id=777,
                    message_id=13,
                    text=None,
                    sticker_unique_id="same-sticker",
                ),
                spam_detector_service=FakeFloodSpamDetectorService(),
                settings=settings,
                moderation_service=ModerationService(),
                runtime_settings_repository=FakeRuntimeSettingsRepository(
                    action_mode=ActionMode.NOTIFY_ADMIN
                ),
                duplicate_message_repository=duplicate_repository,
                bot=FailingSendMessageBot(),
            )

        assert duplicate_repository.warning_cleared is True
        assert duplicate_repository.marked_warning_digest is None

    asyncio.run(run())


def test_duplicate_flood_after_any_active_warning_kicks_without_new_warning(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        log_file = tmp_path / "spam.log"
        settings = _settings(action_mode="notify_admin", log_file=log_file)
        bot = FakeBot()
        duplicate_repository = FakeDuplicateMessageRepository(
            state=DuplicateMessageState(
                chat_id=-100123,
                user_id=42,
                digest="new-digest",
                content_key="sticker:new-sticker",
                message_ids=(21, 22, 23),
            ),
            warning_digest="old-digest",
        )

        result = await handle_text_message(
            message=_message(
                message_id=23,
                text=None,
                sticker_unique_id="new-sticker",
            ),
            spam_detector_service=FakeFloodSpamDetectorService(),
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
        assert bot.sent_messages == []
        assert bot.deleted_messages == [
            {"chat_id": -100123, "message_id": 21},
            {"chat_id": -100123, "message_id": 22},
            {"chat_id": -100123, "message_id": 23},
        ]
        assert bot.bans == [{"chat_id": -100123, "user_id": 42}]
        assert bot.unbans == [{"chat_id": -100123, "user_id": 42}]
        assert duplicate_repository.warning_cleared is True

    asyncio.run(run())


def test_duplicate_flood_during_warning_grace_deletes_without_kick(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        log_file = tmp_path / "spam.log"
        settings = _settings(action_mode="notify_admin", log_file=log_file)
        bot = FakeBot()
        duplicate_repository = FakeDuplicateMessageRepository(
            state=DuplicateMessageState(
                chat_id=-100123,
                user_id=42,
                digest="same-digest",
                content_key="sticker:same-sticker",
                message_ids=(31, 32, 33),
            ),
            warning_digest="same-digest",
            warning_grace_active=True,
        )

        result = await handle_text_message(
            message=_message(
                message_id=33,
                text=None,
                sticker_unique_id="same-sticker",
            ),
            spam_detector_service=FakeFloodSpamDetectorService(),
            settings=settings,
            moderation_service=ModerationService(),
            runtime_settings_repository=FakeRuntimeSettingsRepository(
                action_mode=ActionMode.NOTIFY_ADMIN
            ),
            duplicate_message_repository=duplicate_repository,
            bot=bot,
        )

        assert result is not None
        assert result.moderation_action == ModerationAction.DELETE_MESSAGE
        assert result.reason == "duplicate_flood_warning_grace"
        assert bot.deleted_messages == [
            {"chat_id": -100123, "message_id": 31},
            {"chat_id": -100123, "message_id": 32},
            {"chat_id": -100123, "message_id": 33},
        ]
        assert bot.bans == []
        assert bot.unbans == []
        assert bot.sent_messages == []
        assert duplicate_repository.cleared is True
        assert duplicate_repository.warning_cleared is False

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
                content_key="sticker:same-sticker",
                message_ids=(12, 13, 14),
            ),
            warning_digest="same-digest",
        )
        spam_detector = FakeFloodSpamDetectorService()

        result = await handle_text_message(
            message=_message(
                message_id=14, text=None, sticker_unique_id="same-sticker"
            ),
            spam_detector_service=spam_detector,
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
        assert bot.deleted_messages == [
            {"chat_id": -100123, "message_id": 12},
            {"chat_id": -100123, "message_id": 13},
            {"chat_id": -100123, "message_id": 14},
        ]
        assert bot.bans == [{"chat_id": -100123, "user_id": 42}]
        assert bot.unbans == [{"chat_id": -100123, "user_id": 42}]
        assert bot.sent_messages == []
        assert duplicate_repository.cleared is True
        assert duplicate_repository.warning_cleared is True
        assert spam_detector.detect_calls == []

    asyncio.run(run())
