from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from app.bot.controller.v1.moderation import handle_text_message
from app.domain import (
    ActionMode,
    DuplicateMessageState,
    ModerationAction,
)
from app.observability.logging import close_logger_handlers, configure_logging
from app.usecase.moderation import ModerationService
from tests.support.moderation import (
    FakeBot,
    FakeDuplicateMessageRepository,
    FakeFloodSpamDetectorService,
    FakeRuntimeSettingsRepository,
    FakeSpamDetectorService,
)
from tests.support.moderation import (
    make_message as _message,
)
from tests.support.moderation import (
    make_settings as _settings,
)


def test_text_moderation_uses_runtime_action_mode_over_env_default(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        log_file = tmp_path / "spam.log"
        settings = _settings(action_mode="notify_admin", log_file=log_file)
        logger = configure_logging(settings)
        bot = FakeBot()

        try:
            result = await handle_text_message(
                message=_message(message_thread_id=777),
                spam_detector_service=FakeSpamDetectorService(),
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

    asyncio.run(run())


def test_unknown_slash_command_is_still_checked_by_moderation(tmp_path: Path) -> None:
    async def run() -> None:
        log_file = tmp_path / "spam.log"
        settings = _settings(action_mode="delete", log_file=log_file)
        bot = FakeBot()

        result = await handle_text_message(
            message=_message(text="/casino бесплатно"),
            spam_detector_service=FakeSpamDetectorService(),
            settings=settings,
            moderation_service=ModerationService(),
            runtime_settings_repository=FakeRuntimeSettingsRepository(
                action_mode=ActionMode.DELETE
            ),
            bot=bot,
        )

        assert result is not None
        assert result.moderation_action == ModerationAction.DELETE_MESSAGE
        assert bot.deleted_messages == [{"chat_id": -100123, "message_id": 55}]
        assert bot.bans == [{"chat_id": -100123, "user_id": 42}]

    asyncio.run(run())


def test_known_bot_control_command_is_not_moderated(tmp_path: Path) -> None:
    async def run() -> None:
        log_file = tmp_path / "spam.log"
        settings = _settings(action_mode="delete", log_file=log_file)
        bot = FakeBot()
        spam_detector = FakeFloodSpamDetectorService()

        result = await handle_text_message(
            message=_message(text="/mode delete казино"),
            spam_detector_service=spam_detector,
            settings=settings,
            moderation_service=ModerationService(),
            runtime_settings_repository=FakeRuntimeSettingsRepository(
                action_mode=ActionMode.DELETE
            ),
            bot=bot,
        )

        assert result is None
        assert spam_detector.detect_calls == []
        assert bot.deleted_messages == []
        assert bot.bans == []

    asyncio.run(run())


def test_real_chat_admin_message_is_not_moderated(tmp_path: Path) -> None:
    async def run() -> None:
        log_file = tmp_path / "spam.log"
        settings = _settings(action_mode="delete", log_file=log_file)
        bot = FakeBot(admin_user_ids={42})
        spam_detector = FakeFloodSpamDetectorService()
        duplicate_repository = FakeDuplicateMessageRepository(
            state=DuplicateMessageState(
                chat_id=-100123,
                user_id=42,
                digest="casino-digest",
                content_key="text:/casino бесплатно",
                message_ids=(55,),
            )
        )

        result = await handle_text_message(
            message=_message(text="/casino бесплатно"),
            spam_detector_service=spam_detector,
            settings=settings,
            moderation_service=ModerationService(),
            runtime_settings_repository=FakeRuntimeSettingsRepository(
                action_mode=ActionMode.DELETE
            ),
            duplicate_message_repository=duplicate_repository,
            bot=bot,
        )

        assert result is None
        assert spam_detector.detect_calls == []
        assert bot.deleted_messages == []
        assert bot.bans == []
        assert bot.unbans == []
        assert bot.sent_messages == []
        assert bot.chat_member_calls == [{"chat_id": -100123, "user_id": 42}]
        assert duplicate_repository.record_calls == []

    asyncio.run(run())


def test_neutral_message_does_not_call_admin_lookup(tmp_path: Path) -> None:
    async def run() -> None:
        log_file = tmp_path / "spam.log"
        settings = _settings(action_mode="delete", log_file=log_file)
        bot = FakeBot(admin_user_ids={42})
        spam_detector = FakeFloodSpamDetectorService()

        result = await handle_text_message(
            message=_message(text="regular team update"),
            spam_detector_service=spam_detector,
            settings=settings,
            moderation_service=ModerationService(),
            runtime_settings_repository=FakeRuntimeSettingsRepository(
                action_mode=ActionMode.DELETE
            ),
            bot=bot,
        )

        assert result is not None
        assert result.is_spam is False
        assert spam_detector.detect_calls == ["regular team update"]
        assert bot.chat_member_calls == []
        assert bot.deleted_messages == []
        assert bot.bans == []

    asyncio.run(run())


def test_admin_duplicate_flood_is_ignored_only_at_action_threshold(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        log_file = tmp_path / "spam.log"
        settings = _settings(action_mode="notify_admin", log_file=log_file)
        bot = FakeBot(admin_user_ids={42})
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

        assert result is None
        assert bot.chat_member_calls == [{"chat_id": -100123, "user_id": 42}]
        assert bot.deleted_messages == []
        assert bot.bans == []
        assert bot.sent_messages == []
        assert spam_detector.detect_calls == []
        assert duplicate_repository.cleared is True
        assert duplicate_repository.warning_cleared is True

    asyncio.run(run())


def test_hidden_text_link_url_is_checked_by_spam_detector(tmp_path: Path) -> None:
    async def run() -> None:
        log_file = tmp_path / "spam.log"
        settings = _settings(action_mode="notify_admin", log_file=log_file)
        bot = FakeBot()
        spam_detector = FakeFloodSpamDetectorService()

        result = await handle_text_message(
            message=_message(
                text="переходи",
                entities=[SimpleNamespace(url="https://casino.example/promo")],
            ),
            spam_detector_service=spam_detector,
            settings=settings,
            moderation_service=ModerationService(),
            runtime_settings_repository=FakeRuntimeSettingsRepository(
                action_mode=ActionMode.NOTIFY_ADMIN
            ),
            bot=bot,
        )

        assert result is not None
        assert result.reason == "no_stop_word"
        assert spam_detector.detect_calls == ["переходи https://casino.example/promo"]

    asyncio.run(run())


def test_media_file_metadata_is_checked_by_spam_detector(tmp_path: Path) -> None:
    async def run() -> None:
        log_file = tmp_path / "spam.log"
        settings = _settings(action_mode="notify_admin", log_file=log_file)
        bot = FakeBot()
        duplicate_repository = FakeDuplicateMessageRepository(
            state=DuplicateMessageState(
                chat_id=-100123,
                user_id=42,
                digest="document-digest",
                content_key="document:document-id",
                message_ids=(55,),
            )
        )
        spam_detector = FakeFloodSpamDetectorService()

        result = await handle_text_message(
            message=_message(
                text=None,
                document_unique_id="document-id",
                document_file_name="казино_bonus.pdf",
                document_mime_type="application/pdf",
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
        assert result.reason == "no_stop_word"
        assert spam_detector.detect_calls == ["казино_bonus.pdf application/pdf"]
        assert bot.deleted_messages == []
        assert bot.sent_messages == []

    asyncio.run(run())
