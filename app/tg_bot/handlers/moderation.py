from __future__ import annotations

from typing import Any

from aiogram import F, Router
from aiogram.types import Message

from app.config import Settings
from app.cache.redis import BlacklistRepository, RuntimeSettingsRepository
from app.core.models import ActionMode, SpamDetectionResult, StopWordCheckResult
from app.core.services.moderation import ModerationService
from app.core.services.spam_detector import SpamDetectorService

router = Router(name="moderation")
GROUP_CHAT_TYPES = {"group", "supergroup"}


def _chat_type(message: Any) -> str:
    chat = getattr(message, "chat", None)
    chat_type = getattr(chat, "type", "")
    return str(getattr(chat_type, "value", chat_type)).lower()


def _is_bot_message(message: Any) -> bool:
    from_user = getattr(message, "from_user", None)
    return bool(getattr(from_user, "is_bot", False))


async def handle_text_message(
    *,
    message: Message,
    spam_detector_service: SpamDetectorService,
    blacklist_repository: BlacklistRepository,
    settings: Settings | None = None,
    moderation_service: ModerationService | None = None,
    runtime_settings_repository: RuntimeSettingsRepository | None = None,
    bot: Any | None = None,
) -> SpamDetectionResult | None:
    text = getattr(message, "text", None)
    if (
        not text
        or _chat_type(message) not in GROUP_CHAT_TYPES
        or _is_bot_message(message)
    ):
        return None

    from_user = getattr(message, "from_user", None)
    chat = getattr(message, "chat", None)
    user_id = getattr(from_user, "id", None)
    chat_id = getattr(chat, "id", None)
    if user_id is None or chat_id is None:
        return None

    if await blacklist_repository.contains(chat_id=int(chat_id), user_id=int(user_id)):
        result = SpamDetectionResult(
            is_spam=True,
            reason="blacklisted_user",
            stop_word=StopWordCheckResult(matched=False),
        )
        return await _apply_moderation_action(
            message=message,
            result=result,
            blacklist_repository=blacklist_repository,
            settings=settings,
            moderation_service=moderation_service,
            runtime_settings_repository=runtime_settings_repository,
            bot=bot,
        )

    result = await spam_detector_service.detect(text)
    return await _apply_moderation_action(
        message=message,
        result=result,
        blacklist_repository=blacklist_repository,
        settings=settings,
        moderation_service=moderation_service,
        runtime_settings_repository=runtime_settings_repository,
        bot=bot,
    )


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
            default=settings.action_mode
        )

    if action_mode == ActionMode.DELETE:
        return await moderation_service.delete_spam_message(
            bot=bot,
            message=message,
            spam_result=result,
            blacklist_repository=blacklist_repository,
        )

    if action_mode == ActionMode.NOTIFY_ADMIN:
        return await moderation_service.notify_admin_about_spam(
            bot=bot,
            message=message,
            spam_result=result,
            settings=settings,
        )

    return result


@router.message(F.text)
async def on_text_message(
    message: Message,
    bot: Any,
    settings: Settings,
    spam_detector_service: SpamDetectorService,
    blacklist_repository: BlacklistRepository,
    runtime_settings_repository: RuntimeSettingsRepository,
    moderation_service: ModerationService,
) -> None:
    await handle_text_message(
        message=message,
        spam_detector_service=spam_detector_service,
        blacklist_repository=blacklist_repository,
        settings=settings,
        moderation_service=moderation_service,
        runtime_settings_repository=runtime_settings_repository,
        bot=bot,
    )
