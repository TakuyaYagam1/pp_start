"""Aiogram router for version 1 Telegram moderation controller"""

from __future__ import annotations

from typing import Any

from aiogram import Router
from aiogram.types import Message

from app.bot.controller.v1.moderation.action import apply_moderation_action
from app.bot.controller.v1.moderation.flood import handle_duplicate_flood
from app.bot.controller.v1.moderation.message import (
    duplicate_content_key,
    is_chat_admin_sender,
    message_spam_text,
    should_ignore_message,
)
from app.config import Settings
from app.domain import SpamDetectionResult
from app.domain.stopword import check_stop_words
from app.infrastructure.redis import (
    AutoDeleteMessageRepository,
    DuplicateMessageRepository,
    RuntimeSettingsRepository,
    StopWordWarningRepository,
)
from app.usecase.contract import (
    AutoDeleteMessageStore,
    DuplicateMessageStore,
    RuntimeSettingsStore,
    StopWordWarningStore,
)
from app.usecase.moderation import ModerationService
from app.usecase.moderation.auto_delete import AutoDeleteTaskRegistry
from app.usecase.moderation.spam_detector import SpamDetectorService

router = Router(name="moderation")


async def handle_text_message(
    *,
    message: Message,
    spam_detector_service: SpamDetectorService,
    settings: Settings | None = None,
    moderation_service: ModerationService | None = None,
    runtime_settings_repository: RuntimeSettingsStore | None = None,
    duplicate_message_repository: DuplicateMessageStore | None = None,
    stop_word_warning_repository: StopWordWarningStore | None = None,
    auto_delete_message_repository: AutoDeleteMessageStore | None = None,
    auto_delete_task_registry: AutoDeleteTaskRegistry | None = None,
    bot: Any | None = None,
) -> SpamDetectionResult | None:
    if should_ignore_message(message):
        return None

    from_user = getattr(message, "from_user", None)
    chat = getattr(message, "chat", None)
    user_id = getattr(from_user, "id", None)
    chat_id = getattr(chat, "id", None)
    if user_id is None or chat_id is None:
        return None

    exempt_sender: bool | None = None

    async def is_exempt_sender() -> bool:
        nonlocal exempt_sender
        if exempt_sender is None:
            exempt_sender = await is_chat_admin_sender(message=message, bot=bot)
        return exempt_sender

    spam_text = message_spam_text(message)
    if spam_text and check_stop_words(spam_text).matched and await is_exempt_sender():
        return None

    content_key = duplicate_content_key(message)
    flood_result = await handle_duplicate_flood(
        message=message,
        duplicate_message_repository=duplicate_message_repository,
        settings=settings,
        moderation_service=moderation_service,
        bot=bot,
        content_key=content_key,
        auto_delete_message_repository=auto_delete_message_repository,
        auto_delete_task_registry=auto_delete_task_registry,
        is_exempt_sender=is_exempt_sender,
    )
    if flood_result is not None:
        return flood_result

    if not spam_text:
        return None

    result = await spam_detector_service.detect(spam_text)
    return await apply_moderation_action(
        message=message,
        result=result,
        settings=settings,
        moderation_service=moderation_service,
        runtime_settings_repository=runtime_settings_repository,
        stop_word_warning_repository=stop_word_warning_repository,
        auto_delete_message_repository=auto_delete_message_repository,
        auto_delete_task_registry=auto_delete_task_registry,
        is_exempt_sender=is_exempt_sender,
        bot=bot,
    )


@router.message()
async def on_chat_message(
    message: Message,
    bot: Any,
    settings: Settings,
    spam_detector_service: SpamDetectorService,
    runtime_settings_repository: RuntimeSettingsRepository,
    duplicate_message_repository: DuplicateMessageRepository,
    stop_word_warning_repository: StopWordWarningRepository,
    auto_delete_message_repository: AutoDeleteMessageRepository,
    auto_delete_task_registry: AutoDeleteTaskRegistry,
    moderation_service: ModerationService,
) -> None:
    await handle_text_message(
        message=message,
        spam_detector_service=spam_detector_service,
        settings=settings,
        moderation_service=moderation_service,
        runtime_settings_repository=runtime_settings_repository,
        duplicate_message_repository=duplicate_message_repository,
        stop_word_warning_repository=stop_word_warning_repository,
        auto_delete_message_repository=auto_delete_message_repository,
        auto_delete_task_registry=auto_delete_task_registry,
        bot=bot,
    )
