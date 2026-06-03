from __future__ import annotations

from typing import Any

from aiogram import Router
from aiogram.types import Message

from app.config import Settings
from app.cache.redis import (
    BlacklistRepository,
    DuplicateMessageRepository,
    RuntimeSettingsRepository,
)
from app.core.models import (
    ActionMode,
    LLMDecision,
    SpamDetectionResult,
    StopWordCheckResult,
)
from app.core.services.moderation import ModerationService
from app.core.services.spam_detector import SpamDetectorService

router = Router(name="moderation")
GROUP_CHAT_TYPES = {"group", "supergroup"}
FILE_CONTENT_FIELDS = (
    "sticker",
    "animation",
    "video",
    "document",
    "audio",
    "voice",
    "video_note",
)
FILE_METADATA_FIELDS = ("file_name", "mime_type", "emoji", "set_name")


def _chat_type(message: Any) -> str:
    chat = getattr(message, "chat", None)
    chat_type = getattr(chat, "type", "")
    return str(getattr(chat_type, "value", chat_type)).lower()


def _is_bot_message(message: Any) -> bool:
    from_user = getattr(message, "from_user", None)
    return bool(getattr(from_user, "is_bot", False))


def _message_text(message: Any) -> str | None:
    text = getattr(message, "text", None)
    if text:
        return str(text)

    caption = getattr(message, "caption", None)
    if caption:
        return str(caption)

    return None


def _file_unique_content_key(message: Any) -> str | None:
    for field in FILE_CONTENT_FIELDS:
        value = getattr(message, field, None)
        file_unique_id = getattr(value, "file_unique_id", None)
        if file_unique_id:
            return f"{field}:{file_unique_id}"

    photos = getattr(message, "photo", None)
    if photos:
        file_unique_id = getattr(photos[-1], "file_unique_id", None)
        if file_unique_id:
            return f"photo:{file_unique_id}"

    return None


def _duplicate_content_key(message: Any) -> str | None:
    file_content_key = _file_unique_content_key(message)
    if file_content_key:
        return file_content_key

    text = _message_text(message)
    if text:
        return f"text:{DuplicateMessageRepository.normalize_content_key(text)}"

    return None


def _file_metadata_text(message: Any) -> str | None:
    parts: list[str] = []
    for field in FILE_CONTENT_FIELDS:
        value = getattr(message, field, None)
        if value is None:
            continue

        for metadata_field in FILE_METADATA_FIELDS:
            metadata_value = getattr(value, metadata_field, None)
            if metadata_value:
                parts.append(str(metadata_value))

    if not parts:
        return None

    return " ".join(parts)


def _message_spam_text(message: Any) -> str | None:
    parts = [
        part
        for part in (
            _message_text(message),
            _file_metadata_text(message),
        )
        if part
    ]
    if not parts:
        return None
    return " ".join(parts)


def _is_command_message(message: Any) -> bool:
    text = getattr(message, "text", None)
    return isinstance(text, str) and text.startswith("/")


async def handle_text_message(
    *,
    message: Message,
    spam_detector_service: SpamDetectorService,
    blacklist_repository: BlacklistRepository,
    settings: Settings | None = None,
    moderation_service: ModerationService | None = None,
    runtime_settings_repository: RuntimeSettingsRepository | None = None,
    duplicate_message_repository: DuplicateMessageRepository | None = None,
    bot: Any | None = None,
) -> SpamDetectionResult | None:
    if (
        _chat_type(message) not in GROUP_CHAT_TYPES
        or _is_bot_message(message)
        or _is_command_message(message)
    ):
        return None

    from_user = getattr(message, "from_user", None)
    chat = getattr(message, "chat", None)
    user_id = getattr(from_user, "id", None)
    chat_id = getattr(chat, "id", None)
    if user_id is None or chat_id is None:
        return None

    content_key = _duplicate_content_key(message)
    flood_result = await _handle_duplicate_flood(
        message=message,
        duplicate_message_repository=duplicate_message_repository,
        settings=settings,
        moderation_service=moderation_service,
        bot=bot,
        content_key=content_key,
    )
    if flood_result is not None:
        return flood_result

    spam_text = _message_spam_text(message)
    if not spam_text:
        return None

    result = await spam_detector_service.detect(spam_text)
    return await _apply_moderation_action(
        message=message,
        result=result,
        blacklist_repository=blacklist_repository,
        settings=settings,
        moderation_service=moderation_service,
        runtime_settings_repository=runtime_settings_repository,
        bot=bot,
    )


async def _handle_duplicate_flood(
    *,
    message: Message,
    duplicate_message_repository: DuplicateMessageRepository | None,
    settings: Settings | None,
    moderation_service: ModerationService | None,
    bot: Any | None,
    content_key: str | None,
) -> SpamDetectionResult | None:
    if (
        duplicate_message_repository is None
        or settings is None
        or moderation_service is None
        or bot is None
        or content_key is None
    ):
        return None

    message_id = getattr(message, "message_id", None)
    chat_id = getattr(getattr(message, "chat", None), "id", None)
    user_id = getattr(getattr(message, "from_user", None), "id", None)
    if message_id is None or chat_id is None or user_id is None:
        return None

    state = await duplicate_message_repository.record_message(
        chat_id=int(chat_id),
        user_id=int(user_id),
        message_id=int(message_id),
        content_key=content_key,
    )
    warned_digest = await duplicate_message_repository.get_warning_digest(
        chat_id=int(chat_id),
        user_id=int(user_id),
    )
    if warned_digest == state.digest or (
        warned_digest is not None
        and len(state.message_ids) >= settings.duplicate_message_warn_threshold
    ):
        result = SpamDetectionResult(
            is_spam=True,
            reason="duplicate_flood_repeated_after_warning",
            stop_word=StopWordCheckResult(matched=False),
            llm_decision=LLMDecision.SPAM,
            matched_term="duplicate_content",
        )
        moderation_result = await moderation_service.kick_duplicate_flood(
            bot=bot,
            message=message,
            spam_result=result,
            duplicate_message_ids=state.message_ids,
        )
        await duplicate_message_repository.clear(
            chat_id=int(chat_id), user_id=int(user_id)
        )
        await duplicate_message_repository.clear_warning(
            chat_id=int(chat_id),
            user_id=int(user_id),
        )
        return moderation_result

    if len(state.message_ids) < settings.duplicate_message_warn_threshold:
        return None

    marked_warning = await duplicate_message_repository.mark_warned_once(
        chat_id=int(chat_id),
        user_id=int(user_id),
        digest=state.digest,
    )
    if not marked_warning:
        warned_digest = await duplicate_message_repository.get_warning_digest(
            chat_id=int(chat_id),
            user_id=int(user_id),
        )
        if warned_digest is not None:
            result = SpamDetectionResult(
                is_spam=True,
                reason="duplicate_flood_repeated_after_warning",
                stop_word=StopWordCheckResult(matched=False),
                llm_decision=LLMDecision.SPAM,
                matched_term="duplicate_content",
            )
            moderation_result = await moderation_service.kick_duplicate_flood(
                bot=bot,
                message=message,
                spam_result=result,
                duplicate_message_ids=state.message_ids,
            )
            await duplicate_message_repository.clear(
                chat_id=int(chat_id), user_id=int(user_id)
            )
            await duplicate_message_repository.clear_warning(
                chat_id=int(chat_id),
                user_id=int(user_id),
            )
            return moderation_result
        return None

    result = SpamDetectionResult(
        is_spam=True,
        reason="duplicate_flood",
        stop_word=StopWordCheckResult(matched=False),
        llm_decision=LLMDecision.UNKNOWN,
        matched_term="duplicate_content",
    )
    moderation_result = await moderation_service.warn_duplicate_flood(
        bot=bot,
        message=message,
        spam_result=result,
        duplicate_message_ids=state.message_ids,
        warning_message_ttl_seconds=settings.duplicate_warning_message_ttl_seconds,
    )
    await duplicate_message_repository.clear(chat_id=int(chat_id), user_id=int(user_id))
    return moderation_result


async def _apply_moderation_action(
    *,
    message: Message,
    result: SpamDetectionResult,
    blacklist_repository: BlacklistRepository,
    settings: Settings | None,
    moderation_service: ModerationService | None,
    runtime_settings_repository: RuntimeSettingsRepository | None,
    bot: Any | None,
) -> SpamDetectionResult:
    if (
        not result.is_spam
        or settings is None
        or moderation_service is None
        or bot is None
    ):
        return result

    action_mode = settings.action_mode
    if runtime_settings_repository is not None:
        action_mode = await runtime_settings_repository.get_action_mode(
            default=settings.action_mode,
            chat_id=int(message.chat.id),
        )

    if action_mode == ActionMode.DELETE:
        return await moderation_service.delete_spam_message(
            bot=bot,
            message=message,
            spam_result=result,
            blacklist_repository=blacklist_repository,
        )

    if action_mode == ActionMode.NOTIFY_ADMIN:
        notification_target = None
        if runtime_settings_repository is not None:
            notification_target = (
                await runtime_settings_repository.get_notification_target(
                    chat_id=int(message.chat.id)
                )
            )
        return await moderation_service.notify_admin_about_spam(
            bot=bot,
            message=message,
            spam_result=result,
            settings=settings,
            notification_target=notification_target,
        )

    return result


@router.message()
async def on_chat_message(
    message: Message,
    bot: Any,
    settings: Settings,
    spam_detector_service: SpamDetectorService,
    blacklist_repository: BlacklistRepository,
    runtime_settings_repository: RuntimeSettingsRepository,
    duplicate_message_repository: DuplicateMessageRepository,
    moderation_service: ModerationService,
) -> None:
    await handle_text_message(
        message=message,
        spam_detector_service=spam_detector_service,
        blacklist_repository=blacklist_repository,
        settings=settings,
        moderation_service=moderation_service,
        runtime_settings_repository=runtime_settings_repository,
        duplicate_message_repository=duplicate_message_repository,
        bot=bot,
    )
